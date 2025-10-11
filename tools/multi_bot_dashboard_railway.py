"""
Multi-Bot Unified Dashboard with Railway Support
Monitors multiple Polymarket copy trading bots (local and Railway-hosted)
Run with: python tools/multi_bot_dashboard_railway.py
"""
import json
import sqlite3
import sys
import os
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify, request
from typing import Dict, List, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

app = Flask(__name__)

# Load configuration
def load_config():
    """Load bot configuration from file"""
    config_path = Path(__file__).parent.parent / "multi_bot_config.json"
    
    if config_path.exists():
        with open(config_path, 'r') as f:
            return json.load(f)
    
    # Default configuration
    return {
        "bots": [
            {
                "name": "Bot 1 - Primary",
                "db_path": "data/trades.db",
                "description": "Local bot",
                "color": "#007bff",
                "type": "local"
            }
        ],
        "dashboard": {
            "port": 8080,
            "auto_refresh_seconds": 30
        }
    }

CONFIG = load_config()

class BotMonitor:
    """Monitor for a single bot instance"""
    
    def __init__(self, name: str, db_path: str, description: str, color: str, 
                 bot_type: str = "local", railway_service: Optional[str] = None):
        self.name = name
        self.db_path = db_path
        self.description = description
        self.color = color
        self.bot_type = bot_type
        self.railway_service = railway_service
        self._local_db_cache = None
        
    def get_connection(self):
        """Get database connection (handles both local and Railway)"""
        if self.bot_type == "railway" and self.railway_service:
            # Download database from Railway
            return self._get_railway_connection()
        else:
            # Local database
            if not os.path.exists(self.db_path):
                return None
            return sqlite3.connect(self.db_path)
    
    def _get_railway_connection(self):
        """Connect to Railway database via HTTP API"""
        try:
            # Create temporary file for database
            if not self._local_db_cache:
                self._local_db_cache = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
                self._local_db_cache.close()
            
            # Use Railway CLI to download database from the service
            # This works when both services are in the same Railway project
            cmd = ["railway", "run", f"--service={self.railway_service}", "cat", self.db_path]
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0:
                with open(self._local_db_cache.name, 'wb') as f:
                    f.write(result.stdout)
                return sqlite3.connect(self._local_db_cache.name)
            else:
                print(f"Error downloading Railway DB for {self.name}: {result.stderr}")
                return None
                
        except Exception as e:
            print(f"Error connecting to Railway database for {self.name}: {e}")
            return None
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get trading statistics"""
        conn = self.get_connection()
        if not conn:
            return {
                'total_trades': 0,
                'successful_trades': 0,
                'failed_trades': 0,
                'success_rate': 0.0,
                'total_volume_usd': 0.0,
                'today_trades': 0,
                'status': 'disconnected'
            }
        
        try:
            cursor = conn.cursor()
            
            # Total trades
            cursor.execute("SELECT COUNT(*) FROM copy_trades")
            total_trades = cursor.fetchone()[0]
            
            # Successful trades
            cursor.execute("SELECT COUNT(*) FROM copy_trades WHERE status = 'executed'")
            successful_trades = cursor.fetchone()[0]
            
            # Failed trades
            cursor.execute("SELECT COUNT(*) FROM copy_trades WHERE status = 'failed'")
            failed_trades = cursor.fetchone()[0]
            
            # Success rate
            success_rate = (successful_trades / total_trades * 100) if total_trades > 0 else 0.0
            
            # Total volume
            cursor.execute("SELECT SUM(copy_amount_usd) FROM copy_trades WHERE status = 'executed'")
            total_volume = cursor.fetchone()[0] or 0.0
            
            # Today's trades
            cursor.execute("""
                SELECT COUNT(*) FROM copy_trades 
                WHERE DATE(execution_timestamp) = DATE('now', 'localtime')
            """)
            today_trades = cursor.fetchone()[0]
            
            conn.close()
            
            return {
                'total_trades': total_trades,
                'successful_trades': successful_trades,
                'failed_trades': failed_trades,
                'success_rate': success_rate,
                'total_volume_usd': total_volume,
                'today_trades': today_trades,
                'status': 'connected'
            }
            
        except Exception as e:
            print(f"Error getting statistics for {self.name}: {e}")
            if conn:
                conn.close()
            return {
                'total_trades': 0,
                'successful_trades': 0,
                'failed_trades': 0,
                'success_rate': 0.0,
                'total_volume_usd': 0.0,
                'today_trades': 0,
                'status': 'error'
            }
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current open positions"""
        conn = self.get_connection()
        if not conn:
            return []
        
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                WITH position_summary AS (
                    SELECT 
                        ct.market_id,
                        tt.token_id,
                        tt.side,
                        SUM(CASE WHEN tt.side = 'BUY' THEN ct.copy_size ELSE -ct.copy_size END) as net_size,
                        AVG(tt.price) as avg_price,
                        MAX(ct.execution_timestamp) as last_trade,
                        COUNT(*) as num_trades,
                        SUM(ct.copy_amount_usd) as total_volume
                    FROM copy_trades ct
                    JOIN target_trades tt ON ct.original_trade_id = tt.trade_id
                    WHERE ct.status = 'executed'
                    GROUP BY ct.market_id
                    HAVING ABS(net_size) > 0.0001
                )
                SELECT 
                    ps.*,
                    m.title as market_title,
                    m.current_price,
                    m.end_date
                FROM position_summary ps
                LEFT JOIN markets m ON ps.market_id = m.market_id
                ORDER BY ps.last_trade DESC
                LIMIT 50
            """)
            
            positions = []
            for row in cursor.fetchall():
                (market_id, token_id, side, size, price, timestamp, 
                 num_trades, total_volume, market_title, current_price, end_date) = row
                
                current = float(current_price) if current_price else float(price)
                abs_size = abs(float(size))
                entry_price = float(price)
                current_value = abs_size * current
                
                # Calculate P&L
                if float(size) > 0:  # Long position
                    unrealized_pnl = (current - entry_price) * abs_size
                    side_display = "BUY"
                    position = "YES" if entry_price > 0.5 else "NO"
                else:  # Short position
                    unrealized_pnl = (entry_price - current) * abs_size
                    side_display = "SELL"
                    position = "NO" if entry_price > 0.5 else "YES"
                
                positions.append({
                    'market_id': market_id,
                    'market_title': market_title or market_id[:30] + '...',
                    'side': side_display,
                    'position': position,
                    'entry_price': entry_price,
                    'current_price': current,
                    'size': abs_size,
                    'current_value': current_value,
                    'unrealized_pnl': unrealized_pnl,
                    'num_trades': num_trades,
                    'total_volume': float(total_volume),
                    'end_date': end_date
                })
            
            conn.close()
            return positions
            
        except Exception as e:
            print(f"Error getting positions for {self.name}: {e}")
            if conn:
                conn.close()
            return []
    
    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent trades"""
        conn = self.get_connection()
        if not conn:
            return []
        
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    ct.execution_timestamp,
                    ct.market_id,
                    tt.side,
                    tt.price,
                    ct.copy_size,
                    ct.copy_amount_usd,
                    ct.status,
                    m.title as market_title
                FROM copy_trades ct
                JOIN target_trades tt ON ct.original_trade_id = tt.trade_id
                LEFT JOIN markets m ON ct.market_id = m.market_id
                WHERE ct.status = 'executed'
                ORDER BY ct.execution_timestamp DESC
                LIMIT ?
            """, (limit,))
            
            trades = []
            for row in cursor.fetchall():
                timestamp, market_id, side, price, size, amount, status, market_title = row
                
                trades.append({
                    'timestamp': timestamp,
                    'market_title': market_title or market_id[:30] + '...',
                    'side': side,
                    'price': float(price),
                    'size': float(size),
                    'amount': float(amount),
                    'status': status
                })
            
            conn.close()
            return trades
            
        except Exception as e:
            print(f"Error getting trades for {self.name}: {e}")
            if conn:
                conn.close()
            return []
    
    def get_daily_pnl(self) -> float:
        """Calculate daily P&L (approximate)"""
        positions = self.get_positions()
        total_pnl = sum(pos['unrealized_pnl'] for pos in positions)
        return total_pnl

# Initialize bot monitors from config
bot_monitors = []
for bot_config in CONFIG['bots']:
    monitor = BotMonitor(
        name=bot_config['name'],
        db_path=bot_config['db_path'],
        description=bot_config['description'],
        color=bot_config['color'],
        bot_type=bot_config.get('type', 'local'),
        railway_service=bot_config.get('railway_service')
    )
    bot_monitors.append(monitor)

# HTML Dashboard Template (same as before)
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Multi-Bot Dashboard - Polymarket Copy Trading</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { 
            max-width: 1800px; 
            margin: 0 auto;
        }
        .header {
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }
        .header h1 {
            font-size: 36px;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
        .header .update-time {
            font-size: 14px;
            opacity: 0.9;
        }
        .refresh-btn {
            background: white;
            color: #667eea;
            border: none;
            padding: 12px 24px;
            border-radius: 25px;
            cursor: pointer;
            font-weight: 600;
            margin-top: 15px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.2);
            transition: all 0.3s;
        }
        .refresh-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.3);
        }
        
        /* Bot Grid */
        .bot-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(600px, 1fr));
            gap: 25px;
            margin-bottom: 30px;
        }
        
        /* Bot Card */
        .bot-card {
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            border-left: 5px solid;
        }
        
        .bot-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #f0f0f0;
        }
        .bot-name {
            font-size: 24px;
            font-weight: 700;
        }
        .bot-status {
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }
        .status-connected {
            background: #d4edda;
            color: #155724;
        }
        .status-disconnected {
            background: #f8d7da;
            color: #721c24;
        }
        
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
            margin-bottom: 20px;
        }
        .stat-box {
            text-align: center;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 10px;
        }
        .stat-value {
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 5px;
        }
        .stat-label {
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .success { color: #28a745; }
        .danger { color: #dc3545; }
        .warning { color: #ffc107; }
        .primary { color: #007bff; }
        
        /* Positions Table */
        .positions-section {
            margin-top: 20px;
        }
        .section-title {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 15px;
            color: #333;
        }
        .table-container {
            overflow-x: auto;
            border-radius: 8px;
            background: #f8f9fa;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th, td {
            padding: 12px;
            text-align: left;
        }
        th {
            background: #e9ecef;
            font-weight: 600;
            color: #495057;
            border-bottom: 2px solid #dee2e6;
        }
        tr:hover {
            background: #f1f3f5;
        }
        
        /* Combined Summary */
        .combined-summary {
            background: white;
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            margin-bottom: 30px;
        }
        .combined-summary h2 {
            margin-bottom: 20px;
            color: #333;
        }
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }
        .summary-box {
            text-align: center;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        .summary-value {
            font-size: 32px;
            font-weight: 700;
            margin-bottom: 8px;
        }
        .summary-label {
            font-size: 14px;
            opacity: 0.95;
        }
        
        /* Recent Activity */
        .activity-section {
            background: white;
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        
        .trade-item {
            padding: 15px;
            border-left: 4px solid;
            margin-bottom: 10px;
            background: #f8f9fa;
            border-radius: 5px;
        }
        .trade-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
        }
        .trade-market {
            font-weight: 600;
            color: #333;
        }
        .trade-time {
            color: #666;
            font-size: 12px;
        }
        .trade-details {
            display: flex;
            gap: 15px;
            font-size: 13px;
            color: #666;
        }
        
        @media (max-width: 768px) {
            .bot-grid {
                grid-template-columns: 1fr;
            }
            .stats-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }
    </style>
    <script>
        function refreshData() {
            location.reload();
        }
        
        // Auto-refresh every {{ refresh_seconds }} seconds
        setInterval(refreshData, {{ refresh_seconds }} * 1000);
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Multi-Bot Dashboard</h1>
            <p class="update-time">Last updated: {{ current_time }}</p>
            <button class="refresh-btn" onclick="refreshData()">🔄 Refresh Now</button>
        </div>
        
        <!-- Combined Summary -->
        <div class="combined-summary">
            <h2>📊 Combined Portfolio Summary</h2>
            <div class="summary-grid">
                <div class="summary-box">
                    <div class="summary-value">{{ combined_stats.total_positions }}</div>
                    <div class="summary-label">Total Open Positions</div>
                </div>
                <div class="summary-box">
                    <div class="summary-value">${{ "%.2f"|format(combined_stats.total_value) }}</div>
                    <div class="summary-label">Total Position Value</div>
                </div>
                <div class="summary-box">
                    <div class="summary-value" style="color: {{ '#4ade80' if combined_stats.total_pnl >= 0 else '#f87171' }}">
                        ${{ "%.2f"|format(combined_stats.total_pnl) }}
                    </div>
                    <div class="summary-label">Total Unrealized P&L</div>
                </div>
                <div class="summary-box">
                    <div class="summary-value">${{ "%.2f"|format(combined_stats.total_volume) }}</div>
                    <div class="summary-label">Total Volume Traded</div>
                </div>
                <div class="summary-box">
                    <div class="summary-value">{{ combined_stats.total_trades }}</div>
                    <div class="summary-label">Total Trades</div>
                </div>
                <div class="summary-box">
                    <div class="summary-value">{{ "%.1f"|format(combined_stats.avg_success_rate) }}%</div>
                    <div class="summary-label">Average Success Rate</div>
                </div>
            </div>
        </div>
        
        <!-- Individual Bot Cards -->
        <div class="bot-grid">
            {% for bot in bots %}
            <div class="bot-card" style="border-left-color: {{ bot.color }};">
                <div class="bot-header">
                    <div>
                        <div class="bot-name" style="color: {{ bot.color }};">{{ bot.name }}</div>
                        <div style="font-size: 13px; color: #666; margin-top: 5px;">{{ bot.description }}</div>
                    </div>
                    <div class="bot-status status-{{ bot.stats.status }}">
                        {{ '🟢 Connected' if bot.stats.status == 'connected' else '🔴 Disconnected' }}
                    </div>
                </div>
                
                <div class="stats-grid">
                    <div class="stat-box">
                        <div class="stat-value primary">{{ bot.stats.today_trades }}</div>
                        <div class="stat-label">Today's Trades</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value success">{{ bot.stats.successful_trades }}</div>
                        <div class="stat-label">Successful</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value {{ 'success' if bot.stats.success_rate > 70 else 'warning' if bot.stats.success_rate > 50 else 'danger' }}">
                            {{ "%.1f"|format(bot.stats.success_rate) }}%
                        </div>
                        <div class="stat-label">Success Rate</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{{ bot.positions|length }}</div>
                        <div class="stat-label">Open Positions</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value {{ 'success' if bot.daily_pnl >= 0 else 'danger' }}">
                            ${{ "%.2f"|format(bot.daily_pnl) }}
                        </div>
                        <div class="stat-label">Unrealized P&L</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">${{ "%.2f"|format(bot.stats.total_volume_usd) }}</div>
                        <div class="stat-label">Total Volume</div>
                    </div>
                </div>
                
                <div class="positions-section">
                    <div class="section-title">📈 Top Positions</div>
                    <div class="table-container">
                        <table>
                            <thead>
                                <tr>
                                    <th>Market</th>
                                    <th>Side</th>
                                    <th>Size</th>
                                    <th>Value</th>
                                    <th>P&L</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for pos in bot.positions[:5] %}
                                <tr>
                                    <td>{{ pos.market_title[:40] }}...</td>
                                    <td>{{ '🟢' if pos.side == 'BUY' else '🔴' }} {{ pos.side }}</td>
                                    <td>{{ "%.2f"|format(pos.size) }}</td>
                                    <td>${{ "%.2f"|format(pos.current_value) }}</td>
                                    <td class="{{ 'success' if pos.unrealized_pnl >= 0 else 'danger' }}">
                                        ${{ "%.2f"|format(pos.unrealized_pnl) }}
                                    </td>
                                </tr>
                                {% endfor %}
                                {% if bot.positions|length == 0 %}
                                <tr>
                                    <td colspan="5" style="text-align: center; color: #999;">No open positions</td>
                                </tr>
                                {% endif %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
        
        <!-- Recent Activity -->
        <div class="activity-section">
            <h2 class="section-title">📝 Recent Activity (All Bots)</h2>
            {% for activity in recent_activity %}
            <div class="trade-item" style="border-left-color: {{ activity.color }};">
                <div class="trade-header">
                    <div>
                        <span style="background: {{ activity.color }}; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-right: 8px;">
                            {{ activity.bot_name }}
                        </span>
                        <span class="trade-market">{{ activity.market_title }}</span>
                    </div>
                    <div class="trade-time">{{ activity.timestamp }}</div>
                </div>
                <div class="trade-details">
                    <span>{{ '🟢 BUY' if activity.side == 'BUY' else '🔴 SELL' }}</span>
                    <span>Size: {{ "%.2f"|format(activity.size) }}</span>
                    <span>Price: ${{ "%.3f"|format(activity.price) }}</span>
                    <span>Amount: ${{ "%.2f"|format(activity.amount) }}</span>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def dashboard():
    """Main dashboard page"""
    try:
        # Collect data from all bots
        bots_data = []
        all_positions = []
        all_trades = []
        
        for monitor in bot_monitors:
            stats = monitor.get_statistics()
            positions = monitor.get_positions()
            trades = monitor.get_recent_trades(10)
            daily_pnl = monitor.get_daily_pnl()
            
            bots_data.append({
                'name': monitor.name,
                'description': monitor.description,
                'color': monitor.color,
                'stats': stats,
                'positions': positions,
                'trades': trades,
                'daily_pnl': daily_pnl
            })
            
            all_positions.extend(positions)
            for trade in trades:
                trade['bot_name'] = monitor.name
                trade['color'] = monitor.color
                all_trades.append(trade)
        
        # Calculate combined statistics
        total_positions = len(all_positions)
        total_value = sum(pos['current_value'] for pos in all_positions)
        total_pnl = sum(pos['unrealized_pnl'] for pos in all_positions)
        total_volume = sum(bot['stats']['total_volume_usd'] for bot in bots_data)
        total_trades = sum(bot['stats']['total_trades'] for bot in bots_data)
        avg_success_rate = sum(bot['stats']['success_rate'] for bot in bots_data) / len(bots_data) if bots_data else 0
        
        combined_stats = {
            'total_positions': total_positions,
            'total_value': total_value,
            'total_pnl': total_pnl,
            'total_volume': total_volume,
            'total_trades': total_trades,
            'avg_success_rate': avg_success_rate
        }
        
        # Sort recent activity by timestamp
        all_trades.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return render_template_string(
            DASHBOARD_HTML,
            current_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            bots=bots_data,
            combined_stats=combined_stats,
            recent_activity=all_trades[:20],
            refresh_seconds=CONFIG['dashboard'].get('auto_refresh_seconds', 30)
        )
        
    except Exception as e:
        return f"Error loading dashboard: {e}", 500

@app.route('/api/status')
def api_status():
    """API endpoint for combined status"""
    try:
        bots_status = []
        
        for monitor in bot_monitors:
            stats = monitor.get_statistics()
            positions = monitor.get_positions()
            
            bots_status.append({
                'name': monitor.name,
                'status': stats['status'],
                'stats': stats,
                'open_positions': len(positions),
                'daily_pnl': monitor.get_daily_pnl()
            })
        
        return jsonify({
            'status': 'ok',
            'timestamp': datetime.now().isoformat(),
            'bots': bots_status
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = CONFIG['dashboard'].get('port', 8080)
    
    print("🚀 Starting Multi-Bot Dashboard")
    print("=" * 60)
    print(f"📊 Monitoring {len(bot_monitors)} bots:")
    for i, monitor in enumerate(bot_monitors):
        print(f"   {i+1}. {monitor.name} ({monitor.bot_type})")
        print(f"      {monitor.description}")
        if monitor.bot_type == "local":
            print(f"      Database: {monitor.db_path}")
        else:
            print(f"      Railway Service: {monitor.railway_service}")
    print()
    print(f"🌐 Dashboard available at: http://localhost:{port}")
    print(f"🔄 Auto-refreshes every {CONFIG['dashboard'].get('auto_refresh_seconds', 30)} seconds")
    print("📡 API endpoint: /api/status")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=port, debug=False)

