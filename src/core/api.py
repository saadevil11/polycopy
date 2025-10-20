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
        
    def _setup_routes(self):
        """Setup API endpoints"""
        
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
                limit = request.args.get('limit', 20, type=int)
                limit = min(limit, 100)  # Cap at 100
                
                # Use get_copy_trades_today instead
                trades = self.database.get_copy_trades_today()[:limit]
                
                trades_data = []
                for trade in trades:
                    # trade is already a dictionary from SQL
                    trades_data.append({
                        "trade_id": trade.get('trade_id'),
                        "original_trade_id": trade.get('original_trade_id'),
                        "timestamp": trade.get('created_at'),
                        "side": trade.get('side'),
                        "token_id": trade.get('token_id'),
                        "copy_amount_usd": float(trade.get('copy_amount_usd', 0)),
                        "copy_size": float(trade.get('copy_size', 0)),
                        "status": trade.get('status'),
                        "market_title": trade.get('market_title', 'Unknown')
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
                # Get positions from risk manager's current positions
                positions = self.risk_manager.current_positions if hasattr(self.risk_manager, 'current_positions') else []
                
                positions_data = []
                for pos in positions:
                    positions_data.append({
                        "token_id": pos.token_id if hasattr(pos, 'token_id') else None,
                        "market_title": pos.market_title if hasattr(pos, 'market_title') else "Unknown",
                        "side": pos.side.value if hasattr(pos, 'side') and pos.side else None,
                        "size": float(pos.size) if hasattr(pos, 'size') and pos.size else 0,
                        "avg_price": float(pos.avg_price) if hasattr(pos, 'avg_price') and pos.avg_price else 0,
                        "current_price": float(pos.current_price) if hasattr(pos, 'current_price') and pos.current_price else 0,
                        "unrealized_pnl": float(pos.unrealized_pnl) if hasattr(pos, 'unrealized_pnl') and pos.unrealized_pnl else 0,
                        "cost_basis": float(pos.cost_basis) if hasattr(pos, 'cost_basis') and pos.cost_basis else 0,
                        "current_value": float(pos.current_value) if hasattr(pos, 'current_value') and pos.current_value else 0
                    })
                
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
                
                # Get balance from bot's polymarket_client
                balance = 0
                try:
                    if self.bot and hasattr(self.bot, 'polymarket_client'):
                        balance_data = self.bot.polymarket_client.get_account_balance()
                        balance = float(balance_data) if balance_data else 0
                        logger.info(f"API fetched balance: ${balance}")
                    else:
                        logger.warning("Bot or polymarket_client not available in API")
                except Exception as e:
                    logger.error(f"Failed to fetch balance in API: {e}")
                    pass
                
                metrics_data = {
                    "daily_pnl": float(metrics.daily_pnl_usd) if hasattr(metrics, 'daily_pnl_usd') and metrics.daily_pnl_usd else 0,
                    "total_positions": metrics.total_positions if hasattr(metrics, 'total_positions') else 0,
                    "total_position_value": float(metrics.total_position_value_usd) if hasattr(metrics, 'total_position_value_usd') and metrics.total_position_value_usd else 0,
                    "balance": balance,
                    "trades_today": len(metrics.daily_trades) if hasattr(metrics, 'daily_trades') and metrics.daily_trades else 0,
                    "max_daily_loss": float(metrics.max_daily_loss_usd) if hasattr(metrics, 'max_daily_loss_usd') and metrics.max_daily_loss_usd else 0,
                    "max_positions": metrics.max_positions if hasattr(metrics, 'max_positions') else 0,
                    "risk_level": "normal"  # Can add logic to determine risk level
                }
                
                # Calculate success rate from recent trades
                if self.bot:
                    total_trades = self.bot.successful_trades + self.bot.failed_trades
                    success_rate = (self.bot.successful_trades / total_trades * 100) if total_trades > 0 else 0
                    metrics_data["success_rate"] = round(success_rate, 1)
                    metrics_data["total_trades"] = total_trades
                    metrics_data["successful_trades"] = self.bot.successful_trades
                    metrics_data["failed_trades"] = self.bot.failed_trades
                
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

