"""
Lightweight Flask API for bot monitoring
Provides read-only endpoints for dashboard access
"""
from flask import Flask, jsonify, request
from typing import Optional
from datetime import datetime, timedelta
from loguru import logger
import threading
import os

class BotAPI:
    """Lightweight API server for bot monitoring"""
    
    def __init__(self, bot_instance, database, risk_manager, port: int = 8081):
        """
        Initialize API server
        
        Args:
            bot_instance: Reference to the main bot instance
            database: Database instance
            risk_manager: Risk manager instance
            port: Port to run API on (default: 8081)
        """
        self.bot = bot_instance
        self.database = database
        self.risk_manager = risk_manager
        self.port = port
        self.app = Flask(__name__)
        self._setup_routes()
        self._server_thread = None
        
        # Cache for balance (refresh every 5 minutes)
        self._balance_cache = None
        self._balance_cache_time = None
        self._balance_cache_ttl = 300  # 5 minutes in seconds
        
    def _setup_routes(self):
        """Setup API endpoints"""
        
        @self.app.route('/')
        def dashboard():
            """Serve the monitoring dashboard (single-page app)."""
            from flask import Response
            from src.core.dashboard_page import DASHBOARD_HTML
            return Response(DASHBOARD_HTML, mimetype='text/html')

        @self.app.route('/health')
        def health():
            """Health check endpoint"""
            return jsonify({
                "status": "ok",
                "timestamp": datetime.now().isoformat()
            })
        
        @self.app.route('/api/status')
        def get_status():
            """Get bot status"""
            try:
                status = self.bot.get_status_dict()
                return jsonify({
                    "success": True,
                    "data": status,
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                logger.error(f"API error in /api/status: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @self.app.route('/api/trades')
        def get_trades():
            """Get recent trades"""
            try:
                limit = request.args.get('limit', 50, type=int)
                limit = min(limit, 200)  # Cap at 200

                # Enriched rows (joined with target_trades for market/side/price)
                rows = self.database.get_recent_copy_trades(limit)

                trades_data = []
                for r in rows:
                    trades_data.append({
                        "time": r.get('created_at'),
                        "original_trade_id": r.get('original_trade_id'),
                        "market_id": r.get('market_id'),
                        "token_id": r.get('token_id'),
                        "market_title": r.get('market_title') or 'Unknown',
                        "side": r.get('side'),
                        "copy_size": float(r.get('copy_size') or 0),
                        "copy_amount_usd": float(r.get('copy_amount_usd') or 0),
                        "execution_price": float(r.get('execution_price') or 0),
                        "target_price": float(r.get('target_price') or 0),
                        "status": r.get('status'),
                        "order_id": r.get('order_id'),
                        "error": r.get('error_message'),
                    })

                return jsonify({
                    "success": True,
                    "data": trades_data,
                    "count": len(trades_data),
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                logger.error(f"API error in /api/trades: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @self.app.route('/api/positions')
        def get_positions():
            """Get current open positions"""
            try:
                # Read live positions straight from the client (full Position
                # objects with market info). Cached ~15s inside the client.
                positions = []
                if self.bot and hasattr(self.bot, 'polymarket_client'):
                    positions = self.bot.polymarket_client.get_current_positions()

                positions_data = []
                for pos in positions:
                    mi = getattr(pos, 'market_info', None)
                    positions_data.append({
                        "market_id": pos.market_id,
                        "token_id": pos.token_id,
                        "market_title": (mi.title if mi and mi.title else "Unknown"),
                        "outcome": (mi.outcome if mi else ""),
                        "side": pos.side.value if pos.side else "BUY",
                        "size": float(pos.size),
                        "avg_price": float(pos.average_price),
                        "current_price": float(pos.current_price),
                        "cost_basis": float(pos.cost_basis_usd),
                        "current_value": float(pos.current_value_usd),
                        "unrealized_pnl": float(pos.unrealized_pnl),
                    })

                # Largest value first
                positions_data.sort(key=lambda p: p["current_value"], reverse=True)

                return jsonify({
                    "success": True,
                    "data": positions_data,
                    "count": len(positions_data),
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                logger.error(f"API error in /api/positions: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @self.app.route('/api/metrics')
        def get_metrics():
            """Get bot metrics (P&L, stats, etc.)"""
            try:
                metrics = self.risk_manager.get_risk_metrics()
                
                # Get cash balance with caching (5 min TTL)
                cash_balance = 0
                position_value = 0
                try:
                    import time
                    current_time = time.time()
                    
                    # Check if cache is valid
                    if (self._balance_cache is not None and 
                        self._balance_cache_time is not None and 
                        (current_time - self._balance_cache_time) < self._balance_cache_ttl):
                        # Use cached balance
                        cash_balance = self._balance_cache
                        logger.debug(f"API using cached balance: ${cash_balance:.2f}")
                    else:
                        # Fetch fresh balance
                        if self.bot and hasattr(self.bot, 'polymarket_client'):
                            balance_data = self.bot.polymarket_client.get_account_balance()
                            cash_balance = float(balance_data) if balance_data else 0
                            # Update cache
                            self._balance_cache = cash_balance
                            self._balance_cache_time = current_time
                            logger.info(f"API fetched fresh balance: ${cash_balance:.2f} (cached for 5 min)")
                        else:
                            logger.warning("Bot or polymarket_client not available in API")
                except Exception as e:
                    logger.error(f"Failed to fetch balance in API: {e}")
                    # If fetch fails but we have cache, use it anyway
                    if self._balance_cache is not None:
                        cash_balance = self._balance_cache
                        logger.warning(f"Using stale cached balance: ${cash_balance:.2f}")
                    pass
                
                # Get position value from metrics
                position_value = float(metrics.total_position_value_usd) if hasattr(metrics, 'total_position_value_usd') and metrics.total_position_value_usd else 0
                
                # Get claimable winnings (resolved but not redeemed)
                claimable_winnings = 0
                try:
                    # Try to get claimable amount from bot's auto_redeemer
                    if self.bot and hasattr(self.bot, 'auto_redeemer') and self.bot.auto_redeemer:
                        # For now, we'll estimate this from resolved positions
                        # A proper implementation would check on-chain claimable balance
                        # This is a placeholder - claimable winnings would need blockchain query
                        logger.debug("Claimable winnings check not yet implemented")
                except Exception as e:
                    logger.debug(f"Could not fetch claimable winnings: {e}")
                
                # Total portfolio = cash + open positions + claimable winnings
                total_portfolio = cash_balance + position_value + claimable_winnings
                
                # Only log portfolio breakdown when balance is freshly fetched
                if self._balance_cache_time and (current_time - self._balance_cache_time) < 5:
                    logger.info(f"💰 Portfolio: Cash=${cash_balance:.2f}, Positions=${position_value:.2f}, Claimable=${claimable_winnings:.2f}, Total=${total_portfolio:.2f}")
                else:
                    logger.debug(f"Portfolio (cached): Total=${total_portfolio:.2f}")
                
                # Calculate P&L from database (persists across restarts!)
                daily_pnl = self.database.calculate_daily_pnl()
                all_time_pnl = self.database.calculate_all_time_pnl()
                
                # Get trade statistics from database (persists across restarts!)
                trade_stats = self.database.get_trade_statistics()
                
                metrics_data = {
                    "daily_pnl": daily_pnl,  # From database - persists across restarts
                    "total_pnl": all_time_pnl,  # NEW - all-time P&L from database
                    "total_positions": metrics.total_positions if hasattr(metrics, 'total_positions') else 0,
                    "total_position_value": position_value,
                    "balance": total_portfolio,  # Total portfolio value (cash + positions)
                    "cash_balance": cash_balance,  # Available cash only
                    "trades_today": trade_stats.get('today_trades', 0),  # From database
                    "max_daily_loss": float(metrics.max_daily_loss_usd) if hasattr(metrics, 'max_daily_loss_usd') and metrics.max_daily_loss_usd else 0,
                    "max_positions": metrics.max_positions if hasattr(metrics, 'max_positions') else 0,
                    "risk_level": "normal",  # Can add logic to determine risk level
                    # Success rate from database (persists across restarts!)
                    "success_rate": trade_stats.get('success_rate', 0),
                    "total_trades": trade_stats.get('total_trades', 0),
                    "successful_trades": trade_stats.get('successful_trades', 0),
                    "failed_trades": trade_stats.get('failed_trades', 0)
                }
                
                return jsonify({
                    "success": True,
                    "data": metrics_data,
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                logger.error(f"API error in /api/metrics: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @self.app.route('/api/config')
        def get_config():
            """Get bot configuration (non-sensitive)"""
            try:
                from src.core.config import trading_config
                
                # copy_percentage is stored as decimal (0.1 = 10%), multiply by 100 for display
                copy_pct = trading_config.copy_percentage
                # If it's already >= 1, it's likely already in percentage form (wrong config)
                if copy_pct >= 1:
                    copy_pct_display = copy_pct  # Already a percentage
                else:
                    copy_pct_display = copy_pct * 100  # Convert decimal to percentage
                
                config_data = {
                    "target_trader": trading_config.target_trader_address[:10] + "..." if trading_config.target_trader_address else "N/A",
                    "copy_percentage": copy_pct_display,
                    "max_position_size": float(trading_config.max_position_size_usd),
                    "min_position_size": float(trading_config.min_position_size_usd),
                    "max_daily_loss": float(trading_config.max_daily_loss_usd),
                    "max_positions": trading_config.max_positions,
                    "min_market_liquidity": float(trading_config.min_market_liquidity_usd),
                    "min_target_trade_value": float(trading_config.min_target_trade_value_usd)
                }
                
                logger.debug(f"Config API: copy_percentage raw={copy_pct}, display={copy_pct_display}")
                
                return jsonify({
                    "success": True,
                    "data": config_data,
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                logger.error(f"API error in /api/config: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
    
    def start(self):
        """Start API server in background thread"""
        if self._server_thread and self._server_thread.is_alive():
            logger.warning("API server already running")
            return
        
        def run_server():
            try:
                logger.info(f"🌐 Starting API server on port {self.port}")
                # Disable Flask's default logging (too verbose)
                import logging
                log = logging.getLogger('werkzeug')
                log.setLevel(logging.ERROR)
                
                self.app.run(
                    host='0.0.0.0',  # Listen on all interfaces (Railway requirement)
                    port=self.port,
                    debug=False,
                    use_reloader=False,
                    threaded=True
                )
            except Exception as e:
                logger.error(f"API server error: {e}")
        
        self._server_thread = threading.Thread(target=run_server, daemon=True, name="BotAPI")
        self._server_thread.start()
        logger.success(f"✅ API server started on port {self.port}")
    
    def stop(self):
        """Stop API server (daemon thread will exit with main program)"""
        logger.info("API server will stop with main program (daemon thread)")

