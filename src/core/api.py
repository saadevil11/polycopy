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
                
                trades = self.database.get_recent_copy_trades(limit=limit)
                
                trades_data = []
                for trade in trades:
                    trades_data.append({
                        "trade_id": trade.trade_id,
                        "original_trade_id": trade.original_trade_id,
                        "timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
                        "side": trade.side.value if trade.side else None,
                        "token_id": trade.token_id,
                        "copy_amount_usd": float(trade.copy_amount_usd) if trade.copy_amount_usd else 0,
                        "copy_size": float(trade.copy_size) if trade.copy_size else 0,
                        "status": trade.status.value if trade.status else None,
                        "market_title": trade.original_trade.market_info.title if trade.original_trade and trade.original_trade.market_info else "Unknown"
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
                positions = self.database.get_open_positions()
                
                positions_data = []
                for pos in positions:
                    positions_data.append({
                        "token_id": pos.token_id,
                        "market_title": pos.market_title,
                        "side": pos.side.value if pos.side else None,
                        "size": float(pos.size) if pos.size else 0,
                        "avg_price": float(pos.avg_price) if pos.avg_price else 0,
                        "current_price": float(pos.current_price) if pos.current_price else 0,
                        "unrealized_pnl": float(pos.unrealized_pnl) if pos.unrealized_pnl else 0,
                        "cost_basis": float(pos.cost_basis) if pos.cost_basis else 0,
                        "current_value": float(pos.current_value) if pos.current_value else 0
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
                
                metrics_data = {
                    "daily_pnl": float(metrics.daily_pnl_usd) if metrics.daily_pnl_usd else 0,
                    "total_positions": metrics.total_positions,
                    "total_position_value": float(metrics.total_position_value_usd) if metrics.total_position_value_usd else 0,
                    "balance": float(metrics.current_balance_usd) if metrics.current_balance_usd else 0,
                    "trades_today": len(metrics.daily_trades) if metrics.daily_trades else 0,
                    "max_daily_loss": float(metrics.max_daily_loss_usd) if metrics.max_daily_loss_usd else 0,
                    "max_positions": metrics.max_positions,
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
                
                config_data = {
                    "target_trader": trading_config.target_trader_address[:10] + "..." if trading_config.target_trader_address else "N/A",
                    "copy_percentage": trading_config.copy_percentage * 100,  # Convert to percentage
                    "max_position_size": float(trading_config.max_position_size_usd),
                    "min_position_size": float(trading_config.min_position_size_usd),
                    "max_daily_loss": float(trading_config.max_daily_loss_usd),
                    "max_positions": trading_config.max_positions,
                    "min_market_liquidity": float(trading_config.min_market_liquidity_usd),
                    "min_target_trade_value": float(trading_config.min_target_trade_value_usd)
                }
                
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

