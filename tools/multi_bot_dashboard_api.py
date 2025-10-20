"""
Multi-Bot API-Based Dashboard
Monitors multiple Polymarket copy trading bots via their APIs
Perfect for Railway deployment - no database downloads needed!

Run with: python tools/multi_bot_dashboard_api.py
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template_string, jsonify
import requests
from typing import Dict, List, Any
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

app = Flask(__name__)

# Load configuration
def load_config():
    """Load bot configuration from file"""
    config_path = Path(__file__).parent.parent / "multi_bot_config_api.json"
    
    if config_path.exists():
        with open(config_path, 'r') as f:
            return json.load(f)
    
    # Default configuration
    return {
        "bots": [
            {
                "name": "Bot 1",
                "api_url": os.getenv("BOT1_API_URL", "http://localhost:8081"),
                "description": "Primary bot",
                "color": "#007bff"
            }
        ],
        "dashboard": {
            "port": 8080,
            "auto_refresh_seconds": 30
        }
    }

CONFIG = load_config()

def fetch_bot_data(bot_config: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch data from a bot's API"""
    api_url = bot_config['api_url']
    bot_data = {
        "name": bot_config['name'],
        "description": bot_config.get('description', ''),
        "color": bot_config.get('color', '#007bff'),
        "connected": False,
        "error": None
    }
    
    try:
        # Fetch status
        status_response = requests.get(f"{api_url}/api/status", timeout=5)
        if status_response.status_code == 200:
            bot_data["status"] = status_response.json().get('data', {})
            bot_data["connected"] = True
        
        # Fetch trades
        trades_response = requests.get(f"{api_url}/api/trades?limit=20", timeout=5)
        if trades_response.status_code == 200:
            bot_data["trades"] = trades_response.json().get('data', [])
        
        # Fetch positions
        positions_response = requests.get(f"{api_url}/api/positions", timeout=5)
        if positions_response.status_code == 200:
            bot_data["positions"] = positions_response.json().get('data', [])
        
        # Fetch metrics
        metrics_response = requests.get(f"{api_url}/api/metrics", timeout=5)
        if metrics_response.status_code == 200:
            bot_data["metrics"] = metrics_response.json().get('data', {})
        
        # Fetch config
        config_response = requests.get(f"{api_url}/api/config", timeout=5)
        if config_response.status_code == 200:
            bot_data["config"] = config_response.json().get('data', {})
        
    except requests.RequestException as e:
        bot_data["error"] = str(e)
        bot_data["connected"] = False
        logger.warning(f"Failed to fetch data from {bot_config['name']}: {e}")
    
    return bot_data

@app.route('/')
def dashboard():
    """Main dashboard view"""
    bots_data = []
    
    for bot_config in CONFIG['bots']:
        bot_data = fetch_bot_data(bot_config)
        bots_data.append(bot_data)
    
    # Calculate combined metrics
    combined_metrics = {
        "total_daily_pnl": sum(bot.get('metrics', {}).get('daily_pnl', 0) for bot in bots_data),
        "total_positions": sum(bot.get('metrics', {}).get('total_positions', 0) for bot in bots_data),
        "total_position_value": sum(bot.get('metrics', {}).get('total_position_value', 0) for bot in bots_data),
        "total_balance": sum(bot.get('metrics', {}).get('balance', 0) for bot in bots_data),
        "total_trades_today": sum(bot.get('metrics', {}).get('trades_today', 0) for bot in bots_data),
        "average_success_rate": sum(bot.get('metrics', {}).get('success_rate', 0) for bot in bots_data) / len(bots_data) if bots_data else 0,
        "connected_bots": sum(1 for bot in bots_data if bot['connected']),
        "total_bots": len(bots_data)
    }
    
    return render_template_string(DASHBOARD_HTML, 
                                   bots=bots_data, 
                                   combined=combined_metrics,
                                   config=CONFIG['dashboard'],
                                   now=datetime.now())

@app.route('/api/refresh')
def refresh_data():
    """API endpoint for AJAX refresh"""
    bots_data = []
    
    for bot_config in CONFIG['bots']:
        bot_data = fetch_bot_data(bot_config)
        bots_data.append(bot_data)
    
    # Calculate combined metrics
    combined_metrics = {
        "total_daily_pnl": sum(bot.get('metrics', {}).get('daily_pnl', 0) for bot in bots_data),
        "total_positions": sum(bot.get('metrics', {}).get('total_positions', 0) for bot in bots_data),
        "total_position_value": sum(bot.get('metrics', {}).get('total_position_value', 0) for bot in bots_data),
        "total_balance": sum(bot.get('metrics', {}).get('balance', 0) for bot in bots_data),
        "total_trades_today": sum(bot.get('metrics', {}).get('trades_today', 0) for bot in bots_data),
        "average_success_rate": sum(bot.get('metrics', {}).get('success_rate', 0) for bot in bots_data) / len(bots_data) if bots_data else 0,
        "connected_bots": sum(1 for bot in bots_data if bot['connected']),
        "total_bots": len(bots_data)
    }
    
    return jsonify({
        "bots": bots_data,
        "combined": combined_metrics,
        "timestamp": datetime.now().isoformat()
    })

# Dashboard HTML template
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Multi-Bot Dashboard (API)</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #0a0e27; color: #fff; padding: 20px; }
        .card { background-color: #1a1f3a; border: 1px solid #2a2f4a; margin-bottom: 20px; }
        .card-header { border-bottom: 1px solid #2a2f4a; }
        .metric-card { padding: 15px; border-radius: 8px; margin-bottom: 15px; }
        .positive { color: #28a745; }
        .negative { color: #dc3545; }
        .status-badge { padding: 5px 10px; border-radius: 5px; font-size: 0.85em; }
        .status-connected { background-color: #28a745; }
        .status-disconnected { background-color: #dc3545; }
        .trade-item { padding: 10px; border-bottom: 1px solid #2a2f4a; }
        .trade-item:last-child { border-bottom: none; }
        .position-item { padding: 10px; border-radius: 5px; margin-bottom: 10px; background-color: #252a45; }
        .bot-header { border-left: 4px solid; padding-left: 15px; }
        .timestamp { color: #6c757d; font-size: 0.85em; }
        #last-update { position: fixed; top: 20px; right: 20px; background: #1a1f3a; padding: 10px; border-radius: 5px; }
    </style>
</head>
<body>
    <div id="last-update" class="timestamp">
        Last updated: <span id="update-time">{{ now.strftime('%H:%M:%S') }}</span>
        <span id="refresh-indicator" style="display:none;">🔄</span>
    </div>

    <div class="container-fluid">
        <h1 class="mb-4">🤖 Multi-Bot Dashboard (API-Based)</h1>
        
        <!-- Combined Overview -->
        <div class="row mb-4">
            <div class="col-md-12">
                <div class="card">
                    <div class="card-header">
                        <h5>📊 Combined Overview ({{ combined.connected_bots }}/{{ combined.total_bots }} bots connected)</h5>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-2">
                                <div class="metric-card" style="background-color: #1e3a5f;">
                                    <small>Total Balance</small>
                                    <h3>${{ "%.2f"|format(combined.total_balance) }}</h3>
                                </div>
                            </div>
                            <div class="col-md-2">
                                <div class="metric-card" style="background-color: {% if combined.total_daily_pnl >= 0 %}#1e4d2b{% else %}#4d1e1e{% endif %};">
                                    <small>Daily P&L</small>
                                    <h3 class="{% if combined.total_daily_pnl >= 0 %}positive{% else %}negative{% endif %}">
                                        ${{ "%.2f"|format(combined.total_daily_pnl) }}
                                    </h3>
                                </div>
                            </div>
                            <div class="col-md-2">
                                <div class="metric-card" style="background-color: #2e1e4d;">
                                    <small>Total Positions</small>
                                    <h3>{{ combined.total_positions }}</h3>
                                </div>
                            </div>
                            <div class="col-md-2">
                                <div class="metric-card" style="background-color: #4d2e1e;">
                                    <small>Position Value</small>
                                    <h3>${{ "%.2f"|format(combined.total_position_value) }}</h3>
                                </div>
                            </div>
                            <div class="col-md-2">
                                <div class="metric-card" style="background-color: #1e4d4d;">
                                    <small>Trades Today</small>
                                    <h3>{{ combined.total_trades_today }}</h3>
                                </div>
                            </div>
                            <div class="col-md-2">
                                <div class="metric-card" style="background-color: #1e3a5f;">
                                    <small>Avg Success Rate</small>
                                    <h3>{{ "%.1f"|format(combined.average_success_rate) }}%</h3>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Individual Bot Cards -->
        {% for bot in bots %}
        <div class="card mb-4">
            <div class="card-header bot-header" style="border-left-color: {{ bot.color }};">
                <h5>
                    {{ bot.name }}
                    <span class="status-badge {% if bot.connected %}status-connected{% else %}status-disconnected{% endif %}">
                        {{ 'Connected' if bot.connected else 'Disconnected' }}
                    </span>
                </h5>
                <small class="text-muted">{{ bot.description }}</small>
                {% if bot.error %}
                <div class="alert alert-danger mt-2">Error: {{ bot.error }}</div>
                {% endif %}
            </div>
            
            {% if bot.connected %}
            <div class="card-body">
                <!-- Bot Status & Metrics -->
                <div class="row mb-3">
                    <div class="col-md-3">
                        <strong>Status:</strong> {{ 'Running' if bot.status.running else 'Stopped' }}<br>
                        <strong>Uptime:</strong> {{ (bot.status.uptime_seconds / 3600) | round(1) }}h<br>
                        <strong>Trades Today:</strong> {{ bot.metrics.trades_today or 0 }}
                    </div>
                    <div class="col-md-3">
                        <strong>Balance:</strong> ${{ "%.2f"|format(bot.metrics.balance or 0) }}<br>
                        <strong>Daily P&L:</strong> 
                        <span class="{% if bot.metrics.daily_pnl >= 0 %}positive{% else %}negative{% endif %}">
                            ${{ "%.2f"|format(bot.metrics.daily_pnl or 0) }}
                        </span><br>
                        <strong>Success Rate:</strong> {{ "%.1f"|format(bot.metrics.success_rate or 0) }}%
                    </div>
                    <div class="col-md-3">
                        <strong>Positions:</strong> {{ bot.metrics.total_positions or 0 }}<br>
                        <strong>Position Value:</strong> ${{ "%.2f"|format(bot.metrics.total_position_value or 0) }}<br>
                        <strong>Copy %:</strong> {{ (bot.config.copy_percentage * 100) | round(1) }}%
                    </div>
                    <div class="col-md-3">
                        <strong>Target:</strong> {{ bot.config.target_trader[:10] }}...<br>
                        <strong>Max Loss:</strong> ${{ "%.0f"|format(bot.config.max_daily_loss or 0) }}<br>
                        <strong>Mode:</strong> {{ 'DRY RUN' if bot.status.get('configuration', {}).get('dry_run') else 'LIVE' }}
                    </div>
                </div>

                <!-- Open Positions -->
                {% if bot.positions %}
                <h6>Open Positions ({{ bot.positions | length }})</h6>
                <div class="row">
                    {% for pos in bot.positions[:5] %}
                    <div class="col-md-6">
                        <div class="position-item">
                            <strong>{{ pos.market_title[:50] }}...</strong><br>
                            <small>
                                {{ pos.side }} {{ "%.2f"|format(pos.size) }} @ ${{ "%.2f"|format(pos.avg_price) }}
                                → ${{ "%.2f"|format(pos.current_price) }}
                                <span class="{% if pos.unrealized_pnl >= 0 %}positive{% else %}negative{% endif %}">
                                    (${{ "%.2f"|format(pos.unrealized_pnl) }})
                                </span>
                            </small>
                        </div>
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <p class="text-muted">No open positions</p>
                {% endif %}

                <!-- Recent Trades -->
                {% if bot.trades %}
                <h6 class="mt-3">Recent Trades ({{ bot.trades | length }})</h6>
                <div style="max-height: 300px; overflow-y: auto;">
                    {% for trade in bot.trades[:10] %}
                    <div class="trade-item">
                        <strong>{{ trade.market_title[:60] }}</strong><br>
                        <small>
                            {{ trade.side }} {{ "%.2f"|format(trade.copy_size) }} shares 
                            (${{ "%.2f"|format(trade.copy_amount_usd) }})
                            - <span class="badge bg-{{ 'success' if trade.status == 'EXECUTED' else 'warning' if trade.status == 'PARTIAL_FILL' else 'danger' }}">
                                {{ trade.status }}
                            </span>
                            <span class="timestamp">{{ trade.timestamp[:19] if trade.timestamp else 'N/A' }}</span>
                        </small>
                    </div>
                    {% endfor %}
                </div>
                {% endif %}
            </div>
            {% endif %}
        </div>
        {% endfor %}
    </div>

    <script>
        // Auto-refresh every {{ config.auto_refresh_seconds }} seconds
        setInterval(function() {
            document.getElementById('refresh-indicator').style.display = 'inline';
            fetch('/api/refresh')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('update-time').textContent = new Date().toLocaleTimeString();
                    document.getElementById('refresh-indicator').style.display = 'none';
                    // Full page refresh for simplicity (could do partial updates)
                    location.reload();
                })
                .catch(error => {
                    console.error('Refresh failed:', error);
                    document.getElementById('refresh-indicator').style.display = 'none';
                });
        }, {{ config.auto_refresh_seconds * 1000 }});
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", CONFIG['dashboard'].get('port', 8080)))
    logger.info(f"Starting Multi-Bot API Dashboard on port {port}")
    logger.info(f"Monitoring {len(CONFIG['bots'])} bots")
    logger.info(f"Auto-refresh: {CONFIG['dashboard'].get('auto_refresh_seconds', 30)}s")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False
    )

