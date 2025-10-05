"""
Simple web dashboard for monitoring the copy trading bot
Run with: python dashboard.py
"""
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify, request

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.core.database import Database
from src.core.config import bot_config

app = Flask(__name__)
db = Database()

# HTML template for the dashboard
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Polymarket Copy Trading Bot Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .card { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .header { text-align: center; color: #333; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
        .stat { text-align: center; padding: 15px; background: #f8f9fa; border-radius: 5px; }
        .stat-value { font-size: 24px; font-weight: bold; color: #007bff; }
        .stat-label { color: #666; margin-top: 5px; }
        .success { color: #28a745; }
        .danger { color: #dc3545; }
        .warning { color: #ffc107; }
        .table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        .table th, .table td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        .table th { background: #f8f9fa; font-weight: bold; }
        .status-running { color: #28a745; }
        .status-stopped { color: #dc3545; }
        .refresh-btn { background: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; }
        .refresh-btn:hover { background: #0056b3; }
    </style>
    <script>
        function refreshData() {
            location.reload();
        }
        
        // Auto-refresh every 30 seconds
        setInterval(refreshData, 30000);
    </script>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1 class="header">🤖 Polymarket Copy Trading Bot Dashboard</h1>
            <p class="header">Last updated: {{ current_time }}</p>
            <button class="refresh-btn" onclick="refreshData()">🔄 Refresh</button>
        </div>
        
        <div class="card">
            <h2>📊 Statistics</h2>
            <div class="stats">
                <div class="stat">
                    <div class="stat-value {{ 'success' if stats.success_rate > 70 else 'warning' if stats.success_rate > 50 else 'danger' }}">
                        {{ "%.1f"|format(stats.success_rate) }}%
                    </div>
                    <div class="stat-label">Success Rate</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{{ stats.total_trades }}</div>
                    <div class="stat-label">Total Trades</div>
                </div>
                <div class="stat">
                    <div class="stat-value success">{{ stats.successful_trades }}</div>
                    <div class="stat-label">Successful</div>
                </div>
                <div class="stat">
                    <div class="stat-value danger">{{ stats.failed_trades }}</div>
                    <div class="stat-label">Failed</div>
                </div>
                <div class="stat">
                    <div class="stat-value">${{ "%.2f"|format(stats.total_volume_usd) }}</div>
                    <div class="stat-label">Total Volume</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{{ stats.today_trades }}</div>
                    <div class="stat-label">Today's Trades</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>📈 Recent Copy Trades</h2>
            <table class="table">
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Market</th>
                        <th>Side</th>
                        <th>Amount</th>
                        <th>Status</th>
                        <th>Error</th>
                    </tr>
                </thead>
                <tbody>
                    {% for trade in recent_trades %}
                    <tr>
                        <td>{{ trade.created_at }}</td>
                        <td>{{ trade.original_trade_id[:8] }}...</td>
                        <td>{{ trade.side if trade.side else 'N/A' }}</td>
                        <td>${{ "%.2f"|format(trade.copy_amount_usd) }}</td>
                        <td class="{{ 'success' if trade.status == 'executed' else 'danger' if trade.status == 'failed' else 'warning' }}">
                            {{ trade.status.title() }}
                        </td>
                        <td>{{ trade.error_message[:50] if trade.error_message else '' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>🎯 Target Trader Activity</h2>
            <table class="table">
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Market</th>
                        <th>Side</th>
                        <th>Size</th>
                        <th>Price</th>
                        <th>Amount</th>
                    </tr>
                </thead>
                <tbody>
                    {% for trade in target_trades %}
                    <tr>
                        <td>{{ trade.timestamp }}</td>
                        <td>{{ trade.market_id[:8] }}...</td>
                        <td class="{{ 'success' if trade.side == 'BUY' else 'danger' }}">{{ trade.side }}</td>
                        <td>{{ "%.2f"|format(trade.size) }}</td>
                        <td>${{ "%.3f"|format(trade.price) }}</td>
                        <td>${{ "%.2f"|format(trade.amount_usd) }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def dashboard():
    """Main dashboard page"""
    try:
        # Get statistics
        stats = db.get_trade_statistics()
        
        # Get recent copy trades
        recent_copy_trades = db.get_copy_trades_today()
        
        # Get recent target trades
        recent_target_trades = db.get_recent_target_trades(limit=20)
        
        return render_template_string(
            DASHBOARD_HTML,
            current_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            stats=type('Stats', (), stats)(),
            recent_trades=recent_copy_trades,
            target_trades=recent_target_trades
        )
        
    except Exception as e:
        return f"Error loading dashboard: {e}", 500

@app.route('/api/status')
def api_status():
    """API endpoint for bot status"""
    try:
        stats = db.get_trade_statistics()
        recent_trades = db.get_copy_trades_today()
        
        return jsonify({
            'status': 'ok',
            'timestamp': datetime.now().isoformat(),
            'statistics': stats,
            'recent_trades_count': len(recent_trades)
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/trades')
def api_trades():
    """API endpoint for recent trades"""
    try:
        limit = request.args.get('limit', 50, type=int)
        trades = db.get_copy_trades_today()[:limit]
        
        return jsonify({
            'status': 'ok',
            'trades': trades
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    print("🚀 Starting Polymarket Copy Trading Bot Dashboard")
    print("📊 Dashboard available at: http://localhost:8080")
    print("🔄 Auto-refreshes every 30 seconds")
    print("📡 API endpoints:")
    print("   - /api/status - Bot status")
    print("   - /api/trades - Recent trades")
    
    app.run(host='0.0.0.0', port=8080, debug=False)
