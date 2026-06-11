"""
Main Polymarket Copy Trading Bot
"""
import asyncio
import signal
import sys
from datetime import datetime, timedelta
from typing import Optional, Set
from loguru import logger

from src.core.config import bot_config, trading_config, polymarket_config
from src.core.models import BotStatus, RiskMetrics, TraderTrade, CopyTrade, AccountRestrictedException
from src.core.polymarket_client import PolymarketClient
from src.monitors.trader_monitor import TraderMonitor
from src.monitors.alternative_trader_monitor import AlternativeTraderMonitor
from src.monitors.websocket_trader_monitor import WebSocketTraderMonitor
from src.monitors.data_api_trader_monitor import DataAPITraderMonitor
from src.core.trade_replicator import TradeReplicator
from src.core.risk_manager import RiskManager
from src.core.database import Database
from src.core.merge_positions import PositionMerger
from src.core.polymarket_redeem import PolymarketRedeemer
from src.core.api import BotAPI


class PolymarketCopyTradingBot:
    """Main copy trading bot class"""
    
    def __init__(self):
        self.config = bot_config
        self.trading_config = trading_config
        self.running = False
        self.account_restricted = False  # Flag to track if account is restricted
        
        self.start_time = datetime.now()
        
        # Initialize components
        self.database = Database()
        self.polymarket_client = PolymarketClient()
        self.risk_manager = RiskManager(self.polymarket_client)
        self.trade_replicator = TradeReplicator(self.polymarket_client, self.risk_manager)
        self.position_merger = PositionMerger(self.polymarket_client)
        self.auto_redeemer = None  # Initialize later after client is ready
        
        # Trade detection: real-time WebSocket by default, or Data-API polling
        # when USE_POLLING_MONITOR=true (needed on hosts whose IP the WS edge
        # rate-limits with HTTP 429, e.g. many cloud/datacenter IPs).
        if self.trading_config.use_polling_monitor:
            self.trader_monitor = DataAPITraderMonitor()
            logger.info("🛰️  Using Data-API POLLING monitor (USE_POLLING_MONITOR=true)")
        else:
            self.trader_monitor = WebSocketTraderMonitor()
        
        # API server for monitoring (optional)
        self.api_server = None
        
        # Statistics
        self.trades_copied_today = 0
        self.successful_trades = 0
        self.failed_trades = 0
        self.errors = []
        
        # In-memory cache for executed trades (speed optimization)
        self.executed_trades_cache: Set[str] = set()

        # Background replication tasks (non-blocking dispatch). Held so they are
        # not garbage-collected mid-flight; discarded on completion.
        self._replication_tasks: Set[asyncio.Task] = set()
        
    async def initialize(self) -> bool:
        """Initialize the bot"""
        try:
            logger.info("Initializing Polymarket Copy Trading Bot...")
            
            # Validate configuration
            if not self._validate_config():
                return False
            
            # Initialize Polymarket client
            if not self.polymarket_client.initialize():
                logger.error("Failed to initialize Polymarket client")
                return False

            # Preflight region check - fail fast if Polymarket geo-blocks this
            # IP/region, instead of waiting for the first copied trade to 403.
            # (Skipped in dry-run, where no real orders are placed.)
            if not self.config.dry_run:
                if not self.polymarket_client.check_region_allowed():
                    logger.error("Region preflight failed - trading is geo-blocked from here. "
                                 "Aborting startup.")
                    return False

            # Initialize Polymarket redeemer if enabled
            if self.config.auto_redeem_enabled:
                try:
                    self.auto_redeemer = PolymarketRedeemer(self.polymarket_client)
                    logger.info("✅ Polymarket auto-claiming enabled (checks every {} minutes)".format(
                        self.config.auto_redeem_interval_minutes
                    ))
                except Exception as e:
                    logger.warning(f"Failed to initialize Polymarket redeemer: {e}")
                    logger.warning("Auto-claiming will be disabled")
                    self.auto_redeemer = None
            else:
                logger.info("Auto-claiming disabled")
            
            # Initialize API server if API_PORT is set
            import os
            # Railway sets PORT for web services; use it if API_PORT isn't set
            # so the monitoring dashboard is exposed automatically.
            api_port = os.getenv("API_PORT") or os.getenv("PORT")
            if api_port:
                try:
                    self.api_server = BotAPI(
                        bot_instance=self,
                        database=self.database,
                        risk_manager=self.risk_manager,
                        port=int(api_port)
                    )
                    self.api_server.start()
                    logger.success(f"✅ API server enabled on port {api_port}")
                except Exception as e:
                    logger.warning(f"Failed to start API server: {e}")
                    self.api_server = None
            else:
                logger.debug("API server disabled (API_PORT not set)")
            
            # Set up trader monitor callbacks
            self.trader_monitor.add_new_trade_callback(self._on_new_trade)
            
            logger.success("Bot initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
            return False
    
    def _validate_config(self) -> bool:
        """Validate bot configuration"""
        if not self.trading_config.target_trader_address:
            logger.error("Target trader address not configured")
            return False
        
        if not polymarket_config.private_key:
            logger.error("Private key not configured")
            return False
        
        if not polymarket_config.funder_address:
            logger.error("Funder address not configured")
            return False
        
        logger.info(f"Configuration validated:")
        logger.info(f"  Target trader: {self.trading_config.target_trader_address}")
        logger.info(f"  Copy percentage: {self.trading_config.copy_percentage * 100}%")
        logger.info(f"  Max position size: ${self.trading_config.max_position_size_usd}")
        logger.info(f"  Max daily loss: ${self.trading_config.max_daily_loss_usd}")
        logger.info(f"  Dry run mode: {self.config.dry_run}")
        
        return True
    
    async def start(self):
        """Start the bot"""
        if not await self.initialize():
            logger.error("Failed to initialize bot")
            return
        
        logger.info("Starting copy trading bot...")
        self.running = True
        
        # Start monitoring
        self.trader_monitor.start_monitoring()
        
        # Start status reporting task
        asyncio.create_task(self._status_reporting_loop())
        
        # Start risk monitoring task
        asyncio.create_task(self._risk_monitoring_loop())
        
        # Start auto-claiming task if enabled
        if self.auto_redeemer:
            asyncio.create_task(self.auto_redeemer.start_auto_claim_loop(
                check_interval_minutes=self.config.auto_redeem_interval_minutes
            ))
            logger.info(f"🎁 Auto-claiming task started (checks every {self.config.auto_redeem_interval_minutes} min)")
        
        # Note: Position merging is now only triggered by target trader actions
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.success("Bot started successfully")
        
        # Keep running until stopped
        try:
            while self.running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop the bot"""
        logger.info("Stopping copy trading bot...")
        self.running = False
        
        # Stop monitoring
        self.trader_monitor.stop_monitoring()
        
        # Save final status
        await self._save_status()
        
        logger.info("Bot stopped")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}")
        self.running = False
    
    async def _on_new_trade(self, trade_or_action):
        """Callback for when a new trade or merge action is detected"""
        try:
            # Check if account is already restricted
            if self.account_restricted:
                logger.warning("Account is restricted - skipping all trades")
                return
            
            # Check if this is a special action
            if isinstance(trade_or_action, dict):
                action_type = trade_or_action.get('action')
                if action_type == 'merge':
                    await self._handle_merge_action(trade_or_action)
                    return
                elif action_type == 'redeem':
                    await self._handle_redeem_action(trade_or_action)
                    return
            
            # Handle regular trade
            trade = trade_or_action
            logger.info(f"New trade detected from {trade.trader_address}")
            
            # Check if we've already executed this trade (in-memory cache first for speed)
            if trade.trade_id in self.executed_trades_cache:
                logger.debug(f"Trade {trade.trade_id} in cache, skipping")
                return
            
            # Double-check database (in case bot restarted)
            if self.database.has_executed_trade(trade.trade_id):
                logger.info(f"Trade {trade.trade_id} already executed, skipping")
                self.executed_trades_cache.add(trade.trade_id)  # Add to cache
                return
            
            # Mark as being processed
            self.executed_trades_cache.add(trade.trade_id)
            
            # Save the target trade to database (non-blocking)
            asyncio.create_task(asyncio.to_thread(self.database.save_target_trade, trade))
            
            # Check if we should stop trading due to risk limits
            if self.risk_manager.should_stop_trading():
                logger.warning("Risk limits exceeded, skipping trade")
                return

            # Replicate the trade as a NON-BLOCKING background task so that a slow
            # order (e.g. weather-mode chasing, which can take several seconds while
            # cancelling/replacing) does not stall ingestion of the target's other
            # trades. Dedup (executed_trades_cache) already ran synchronously above,
            # so a duplicate WS message cannot spawn a second task for the same
            # trade. Per-token serialization happens inside the client.
            task = asyncio.create_task(self._replicate_and_record(trade))
            self._replication_tasks.add(task)
            task.add_done_callback(self._replication_tasks.discard)

        except Exception as e:
            logger.error(f"Error dispatching new trade: {e}")
            self.errors.append(f"Error dispatching trade: {str(e)}")

    async def _replicate_and_record(self, trade: TraderTrade):
        """Replicate a single trade and record the outcome.

        Runs as its own task (see _on_new_trade) so multiple markets can be
        copied concurrently. Each task owns its own error handling because
        exceptions raised here do NOT propagate back to _on_new_trade.
        """
        try:
            copy_trade = await self.trade_replicator.replicate_trade(trade)

            if copy_trade:
                # Save copy trade to database
                self.database.save_copy_trade(copy_trade)

                # Update statistics
                if copy_trade.status.value == "executed":
                    self.successful_trades += 1
                    self.trades_copied_today += 1
                    logger.success(f"Successfully copied trade: {copy_trade.order_id}")
                elif copy_trade.status.value == "failed":
                    self.failed_trades += 1
                    self.errors.append(f"Trade failed: {copy_trade.error_message}")
                    logger.error(f"Failed to copy trade: {copy_trade.error_message}")
                elif copy_trade.status.value == "skipped":
                    logger.info(f"Trade skipped: {copy_trade.error_message}")

        except AccountRestrictedException as e:
            # Critical error - account is restricted
            logger.critical("=" * 80)
            logger.critical("🚨 CRITICAL: ACCOUNT RESTRICTION DETECTED 🚨")
            logger.critical("=" * 80)
            logger.critical(f"Restriction Type: {e.restriction_type}")
            logger.critical(f"Error Message: {e.message}")
            logger.critical("")
            logger.critical("Your Polymarket account is in CLOSED-ONLY MODE")
            logger.critical("This means you can only CLOSE existing positions, not open new ones")
            logger.critical("")
            logger.critical("THE BOT WILL NOW STOP TRADING")
            logger.critical("")
            logger.critical("To resolve this issue:")
            logger.critical("1. Contact Polymarket support to understand why your account was restricted")
            logger.critical("2. Check if you're accessing from a restricted region")
            logger.critical("3. Verify your account is fully compliant with Polymarket's terms")
            logger.critical("4. Consider using a different wallet address that isn't restricted")
            logger.critical("=" * 80)

            # Set the restriction flag
            self.account_restricted = True

            # Add to errors
            self.errors.append(f"CRITICAL: Account restricted - {e.restriction_type}: {e.message}")
            self.failed_trades += 1

            # Stop the bot
            logger.critical("Stopping the bot due to account restriction...")
            self.running = False

        except Exception as e:
            logger.error(f"Error processing new trade: {e}")
            self.errors.append(f"Error processing trade: {str(e)}")
            self.failed_trades += 1

    async def _handle_merge_action(self, merge_action: dict):
        """Handle merge action from target trader"""
        try:
            if not self.config.copy_merge_actions:
                logger.info("Merge copying disabled, skipping merge action")
                return
                
            market_id = merge_action.get('market_id')
            amount = merge_action.get('amount', 0)
            
            logger.info(f"Target trader merged {amount} positions in market {market_id}")
            logger.info("Copying merge action...")
            
            # Copy the merge action
            result = self.position_merger.copy_merge_for_market(
                market_id=market_id,
                dry_run=self.config.dry_run
            )
            
            if result.get('merged', 0) > 0:
                logger.success(f"Successfully copied merge: recovered ${result.get('usdc_recovered', 0):.2f} USDC")
                self.successful_trades += 1
            else:
                reason = result.get('reason', 'unknown')
                logger.info(f"Merge copy skipped: {reason}")
                
        except Exception as e:
            logger.error(f"Error handling merge action: {e}")
            self.failed_trades += 1
    
    async def _handle_redeem_action(self, redeem_action: dict):
        """Handle redeem action from target trader"""
        try:
            if not self.config.copy_redeem_actions:
                logger.info("Redeem copying disabled, skipping redeem action")
                return
                
            market_id = redeem_action.get('market_id')
            amount = redeem_action.get('amount', 0)
            
            logger.info(f"Target trader redeemed {amount} winning tokens in market {market_id}")
            logger.info("Copying redeem action...")
            
            # Copy the redeem action
            result = self.position_merger.copy_redeem_for_market(
                market_id=market_id,
                dry_run=self.config.dry_run
            )
            
            if result.get('redeemed', 0) > 0:
                logger.success(f"Successfully copied redeem: recovered ${result.get('usdc_recovered', 0):.2f} USDC")
                self.successful_trades += 1
            else:
                reason = result.get('reason', 'unknown')
                logger.info(f"Redeem copy skipped: {reason}")
                
        except Exception as e:
            logger.error(f"Error handling redeem action: {e}")
            self.failed_trades += 1
    
    async def _status_reporting_loop(self):
        """Periodic status reporting"""
        while self.running:
            try:
                await self._save_status()
                await self._log_status()
                await asyncio.sleep(300)  # Report every 5 minutes
                
            except Exception as e:
                logger.error(f"Error in status reporting: {e}")
                await asyncio.sleep(60)
    
    async def _risk_monitoring_loop(self):
        """Periodic risk monitoring"""
        while self.running:
            try:
                # Check if risk limits are exceeded
                if self.risk_manager.should_stop_trading():
                    logger.critical("Risk limits exceeded - considering emergency stop")
                    
                    # Could implement emergency stop here
                    # await self.risk_manager.emergency_close_all()
                
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Error in risk monitoring: {e}")
                await asyncio.sleep(60)
    
    
    async def _save_status(self):
        """Save current bot status"""
        try:
            risk_metrics = self.risk_manager.get_risk_metrics()
            balance = self.polymarket_client.get_account_balance()
            
            status = BotStatus(
                is_running=self.running,
                last_check_timestamp=datetime.now(),
                trades_copied_today=self.trades_copied_today,
                successful_trades=self.successful_trades,
                failed_trades=self.failed_trades,
                current_balance_usd=balance,
                risk_metrics=risk_metrics,
                errors=self.errors[-10:]  # Keep last 10 errors
            )
            
            self.database.save_bot_status(status)
            
        except Exception as e:
            logger.error(f"Error saving status: {e}")
    
    async def _log_status(self):
        """Log current status"""
        try:
            uptime = datetime.now() - self.start_time
            risk_metrics = self.risk_manager.get_risk_metrics()
            trade_stats = self.database.get_trade_statistics()
            monitor_status = self.trader_monitor.get_monitoring_status()
            
            logger.info("=== Bot Status ===")
            logger.info(f"Uptime: {uptime}")
            logger.info(f"Monitoring: {monitor_status['monitoring']}")
            logger.info(f"WebSocket Connected: {monitor_status.get('connected', False)}")
            logger.info(f"Target Trader: {monitor_status['target_address']}")
            logger.info(f"Trades Seen: {monitor_status['seen_trades_count']}")
            logger.info(f"Trades Today: {self.trades_copied_today}")
            logger.info(f"Success Rate: {trade_stats.get('success_rate', 0):.1f}%")
            logger.info(f"Total Positions: {risk_metrics.total_positions}")
            logger.info(f"Daily P&L: ${risk_metrics.daily_pnl_usd:.2f}")
            logger.info(f"Total Position Value: ${risk_metrics.total_position_value_usd:.2f}")
            
            if self.account_restricted:
                logger.critical("⛔ ACCOUNT RESTRICTED - Bot will not place new trades")
            
            if self.errors:
                logger.warning(f"Recent errors: {len(self.errors)}")
                for error in self.errors[-3:]:  # Show last 3 errors
                    logger.warning(f"  - {error}")
            
        except Exception as e:
            logger.error(f"Error logging status: {e}", exc_info=True)
    
    def get_status_dict(self) -> dict:
        """Get current status as dictionary"""
        try:
            risk_metrics = self.risk_manager.get_risk_metrics()
            trade_stats = self.database.get_trade_statistics()
            uptime = datetime.now() - self.start_time
            
            return {
                'running': self.running,
                'account_restricted': self.account_restricted,
                'uptime_seconds': uptime.total_seconds(),
                'monitoring_status': self.trader_monitor.get_monitoring_status(),
                'trades_today': self.trades_copied_today,
                'successful_trades': self.successful_trades,
                'failed_trades': self.failed_trades,
                'trade_statistics': trade_stats,
                'risk_metrics': risk_metrics.to_dict(),
                'recent_errors': self.errors[-5:],
                'configuration': {
                    'target_trader': self.trading_config.target_trader_address,
                    'copy_percentage': self.trading_config.copy_percentage,
                    'max_position_size': self.trading_config.max_position_size_usd,
                    'dry_run': self.config.dry_run
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return {'error': str(e)}


async def main():
    """Main entry point"""
    # Configure logging
    logger.remove()  # Remove default handler
    logger.add(
        sys.stdout,
        level=bot_config.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.add(
        bot_config.log_file,
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="1 day",
        retention="30 days"
    )
    
    logger.info("Starting Polymarket Copy Trading Bot")
    
    # Create and start bot
    bot = PolymarketCopyTradingBot()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
