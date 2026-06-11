"""
Trade replication system for the copy trading bot
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List
from loguru import logger

from src.core.polymarket_client import PolymarketClient
from src.core.models import TraderTrade, CopyTrade, OrderType, TradeStatus, TradeSide, AccountRestrictedException
from src.core.config import trading_config, bot_config
from src.core.market_filters import MarketFilter


class TradeReplicator:
    """Handles replication of target trader's trades"""
    
    def __init__(self, polymarket_client: PolymarketClient, risk_manager):
        self.client = polymarket_client
        self.risk_manager = risk_manager
        self.config = trading_config
        self.bot_config = bot_config
        self.market_filter = MarketFilter()
        
    async def replicate_trade(self, original_trade: TraderTrade) -> Optional[CopyTrade]:
        """Replicate a trade from the target trader"""
        try:
            logger.info(f"Attempting to replicate trade {original_trade.trade_id}")
            
            # Pre-trade risk checks
            if not await self._pre_trade_checks(original_trade):
                return self._create_skipped_trade(original_trade, "Failed pre-trade checks")
            
            # Calculate position size
            copy_size, copy_amount = self._calculate_position_size(original_trade)
            if copy_size <= 0:
                return self._create_skipped_trade(original_trade, "Position size too small")
            
            # Create copy trade object
            copy_trade = CopyTrade(
                original_trade=original_trade,
                copy_size=copy_size,
                copy_amount_usd=copy_amount,
                order_type=OrderType.MARKET,  # Default to market orders for speed
                status=TradeStatus.PENDING
            )
            
            # Execute the trade
            if self.bot_config.dry_run:
                return self._simulate_trade(copy_trade)
            else:
                return await self._execute_trade(copy_trade)
                
        except Exception as e:
            logger.error(f"Failed to replicate trade {original_trade.trade_id}: {e}")
            return self._create_failed_trade(original_trade, str(e))
    
    async def _pre_trade_checks(self, trade: TraderTrade) -> bool:
        """Perform pre-trade risk and validation checks"""
        try:
            # Check if market is in excluded list
            if trade.market_id in self.config.excluded_markets:
                logger.info(f"Market {trade.market_id} is excluded")
                return False
            
            # Check minimum target trade value
            if trade.amount_usd < self.config.min_target_trade_value_usd:
                logger.info(f"Target trade too small: ${trade.amount_usd:.2f} < ${self.config.min_target_trade_value_usd:.2f} - skipping")
                return False
                
            # Market filter already checked by WebSocket monitor - skip duplicate check
            
            # Check market liquidity if market info is available.
            # NOTE: WebSocket trades carry liquidity_usd=0 because the live feed
            # doesn't include liquidity data, so a 0 means "unknown", not "no
            # liquidity". Only enforce the minimum when we have a real (>0)
            # figure - otherwise every WS-sourced trade would be rejected.
            if trade.market_info:
                if (trade.market_info.liquidity_usd > 0
                        and trade.market_info.liquidity_usd < self.config.min_market_liquidity_usd):
                    logger.warning(f"Market liquidity too low: ${trade.market_info.liquidity_usd}")
                    return False
                
                if not trade.market_info.is_active:
                    logger.warning(f"Market is not active: {trade.market_id}")
                    return False
            
            # Check risk manager approval
            if not await self.risk_manager.can_open_position(trade):
                logger.warning("Risk manager rejected trade")
                return False
            
            # Skip balance check for speed optimization
            # Balance is verified at bot startup, so we trust it here
            if not self.bot_config.dry_run:
                logger.debug("Balance check skipped for speed - verified at startup")
            else:
                logger.debug("[DRY_RUN] Balance check skipped")
            
            return True
            
        except Exception as e:
            logger.error(f"Error in pre-trade checks: {e}")
            return False
    
    def _calculate_position_size(self, trade: TraderTrade) -> tuple[float, float]:
        """Calculate the position size to copy"""
        try:
            # For SELL orders, we need to sell the same PERCENTAGE of shares
            # that the target trader sold, not recalculate based on USD
            if trade.side == TradeSide.SELL:
                # Calculate what percentage of their position they're selling
                # If they're selling everything, we sell everything
                # If they're selling 50%, we sell 50%
                
                # For now, use the same share ratio approach
                # This means if target sells 1.8 shares, we sell (1.8 * copy_percentage) shares
                copy_size = trade.size * self.config.copy_percentage

                # Cap the SELL at what we ACTUALLY hold - you can't sell shares
                # you don't own. If our proportional size exceeds our holdings,
                # sell the maximum we have (provided it still meets the minimum).
                held = self.client.get_token_holdings(trade.token_id)
                if held > 0 and copy_size > held:
                    logger.info(f"[SELL] Capping {copy_size:.2f} -> {held:.2f} shares (we only hold {held:.2f})")
                    copy_size = held

                copy_amount = copy_size * trade.price if trade.price > 0 else copy_size

                # Polymarket orders have a 5-share minimum, and a SELL can't be
                # bumped up. If what we can sell is below 5 shares, it's
                # un-placeable -> skip cleanly rather than failing.
                if (self.config.gtc_enforce_min_shares or self.config.use_weather_mode) and copy_size < 5.0:
                    logger.info(f"[SELL] Sellable size {copy_size:.2f} shares < 5-share order minimum - skipping")
                    return 0.0, 0.0

                logger.info(f"[SELL] Calculated position size: {copy_size} shares, ${copy_amount:.2f}")
                return copy_size, copy_amount
            
            # For BUY orders, use USD-based calculation
            # Base amount from original trade
            base_amount = trade.amount_usd * self.config.copy_percentage
            
            # Apply position size limits
            copy_amount = min(base_amount, self.config.max_position_size_usd)
            
            # Apply minimum size check - but skip for GTC-only and weather modes,
            # since both place GTC orders that enforce their own 5-share minimum.
            if not self.config.use_gtc_only and not self.config.use_weather_mode:
                copy_amount = max(copy_amount, self.config.min_position_size_usd)

                # If the amount is below minimum, don't trade
                if copy_amount < self.config.min_position_size_usd:
                    return 0.0, 0.0
            else:
                # GTC-only / weather mode: let the 5-share minimum in the order
                # placement logic handle minimums. Don't enforce USD minimum here.
                pass
            
            # Calculate size based on the original trade's price
            if trade.price > 0:
                copy_size = copy_amount / trade.price
            else:
                # If price is 0 or invalid, use size ratio
                copy_size = trade.size * self.config.copy_percentage
            
            logger.info(f"[BUY] Calculated position size: {copy_size} shares, ${copy_amount:.2f}")
            return copy_size, copy_amount
            
        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return 0.0, 0.0
    
    async def _execute_trade(self, copy_trade: CopyTrade) -> CopyTrade:
        """Execute the actual trade with enhanced retry logic"""
        try:
            # Calculate copy percentage
            copy_pct = (copy_trade.copy_size / copy_trade.original_trade.size * 100) if copy_trade.original_trade.size > 0 else 0
            logger.info(f"   You ({copy_pct:.1f}%): {copy_trade.original_trade.side.value} {copy_trade.copy_size:.2f} shares = ${copy_trade.copy_amount_usd:.2f}")
            
            # Determine amount to use (shares for SELL, USD for BUY)
            if copy_trade.original_trade.side == TradeSide.SELL:
                amount = copy_trade.copy_size  # For SELL: use share count
            else:
                amount = copy_trade.copy_amount_usd  # For BUY: use USD amount
            
            # Use enhanced order execution with retry logic
            order_id, result, details = await self.client.place_order_with_retry(
                token_id=copy_trade.original_trade.token_id,
                side=copy_trade.original_trade.side,
                amount_usd=amount,
                original_price=copy_trade.original_trade.price
            )
            
            # Process result
            if order_id:
                copy_trade.order_id = order_id
                copy_trade.execution_timestamp = datetime.now()
                
                # Set execution price from details
                if details.get('avg_price', 0) > 0:
                    copy_trade.execution_price = details['avg_price']
                elif details.get('filled_amount', 0) > 0:
                    # Try to get from order status
                    order_status = self.client.get_order_status(order_id)
                    if order_status and 'price' in order_status:
                        copy_trade.execution_price = float(order_status['price'])
                
                # Determine status based on result
                from src.core.polymarket_client import OrderExecutionResult
                
                if result == OrderExecutionResult.SUCCESS:
                    copy_trade.status = TradeStatus.EXECUTED
                    logger.success(f"✅ Trade executed successfully: {order_id}")
                    logger.info(f"   Strategy: {details['strategy_used']}")
                    logger.info(f"   Attempts: {details['attempts']}")
                    logger.info(f"   Filled: {details['filled_amount']:.2f} shares")
                    
                elif result == OrderExecutionResult.PARTIAL_FILL:
                    copy_trade.status = TradeStatus.EXECUTED  # Still count as executed
                    fill_pct = (details['filled_amount'] / copy_trade.copy_size * 100) if copy_trade.copy_size > 0 else 0
                    copy_trade.error_message = f"Partial fill: {fill_pct:.1f}% filled"
                    logger.warning(f"⚠️  Trade partially filled: {order_id} ({fill_pct:.1f}%)")
                    logger.info(f"   Filled: {details['filled_amount']:.2f} of {copy_trade.copy_size:.2f} shares")
                    
                else:
                    # Shouldn't happen if order_id is returned, but handle it
                    copy_trade.status = TradeStatus.FAILED
                    copy_trade.error_message = f"Unexpected result: {result.value}"
                    logger.error(f"❌ Unexpected execution result: {result.value}")
                
                # Update risk manager
                await self.risk_manager.on_trade_executed(copy_trade)
                
            else:
                # Order failed completely
                copy_trade.status = TradeStatus.FAILED
                copy_trade.error_message = f"Order failed: {result.value}. Reasons: {', '.join(details['failure_reasons'])}"
                
                logger.error(f"❌ Trade execution failed after {details['attempts']} attempts")
                logger.error(f"   Result: {result.value}")
                logger.error(f"   Reasons: {', '.join(details['failure_reasons'])}")
                
                # Log specific failure type for analysis
                from src.core.polymarket_client import OrderExecutionResult
                if result == OrderExecutionResult.INSUFFICIENT_LIQUIDITY:
                    logger.warning("💧 Market has insufficient liquidity for this order size")
                elif result == OrderExecutionResult.PRICE_MOVED:
                    logger.warning("📈 Price moved significantly during execution")
                elif result == OrderExecutionResult.TIMEOUT:
                    logger.warning("⏰ Order execution timed out")
            
            return copy_trade
            
        except AccountRestrictedException as e:
            # Re-raise account restriction errors - these are critical and should stop the bot
            logger.critical(f"⛔ Account restriction detected: {e.message}")
            raise
            
        except Exception as e:
            logger.error(f"❌ Unexpected error executing trade: {e}")
            logger.exception(e)  # Log full traceback
            copy_trade.status = TradeStatus.FAILED
            copy_trade.error_message = f"Unexpected error: {str(e)}"
            return copy_trade
    
    def _simulate_trade(self, copy_trade: CopyTrade) -> CopyTrade:
        """Simulate trade execution for dry run mode"""
        logger.info(f"[DRY RUN] Would execute trade:")
        logger.info(f"  Token ID: {copy_trade.original_trade.token_id}")
        logger.info(f"  Side: {copy_trade.original_trade.side.value}")
        logger.info(f"  Size: {copy_trade.copy_size}")
        logger.info(f"  Amount: ${copy_trade.copy_amount_usd:.2f}")
        
        copy_trade.status = TradeStatus.EXECUTED
        copy_trade.execution_timestamp = datetime.now()
        copy_trade.execution_price = copy_trade.original_trade.price
        copy_trade.order_id = f"dry_run_{int(datetime.now().timestamp())}"
        
        return copy_trade
    
    def _create_skipped_trade(self, original_trade: TraderTrade, reason: str) -> CopyTrade:
        """Create a skipped trade record"""
        logger.warning(f"Skipping trade {original_trade.trade_id}: {reason}")
        
        return CopyTrade(
            original_trade=original_trade,
            copy_size=0.0,
            copy_amount_usd=0.0,
            order_type=OrderType.MARKET,
            status=TradeStatus.SKIPPED,
            error_message=reason
        )
    
    def _create_failed_trade(self, original_trade: TraderTrade, error: str) -> CopyTrade:
        """Create a failed trade record"""
        logger.error(f"Failed to process trade {original_trade.trade_id}: {error}")
        
        return CopyTrade(
            original_trade=original_trade,
            copy_size=0.0,
            copy_amount_usd=0.0,
            order_type=OrderType.MARKET,
            status=TradeStatus.FAILED,
            error_message=error
        )
