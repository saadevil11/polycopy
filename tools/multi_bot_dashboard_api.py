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
    """
    Load bot configuration from environment variables OR file
    
    Priority:
    1. Environment variables (BOT_1_NAME, BOT_1_URL, etc.)
    2. JSON config file
    3. Default config
    """
    bots = []
    
    # 1. Try to load from environment variables
    bot_index = 1
    while True:
        bot_name = os.getenv(f"BOT_{bot_index}_NAME")
        bot_url = os.getenv(f"BOT_{bot_index}_URL")
        
        if not bot_name or not bot_url:
            break  # No more bots in env vars
        
        # Optional: description and color
        bot_desc = os.getenv(f"BOT_{bot_index}_DESC", f"Bot {bot_index}")
        bot_color = os.getenv(f"BOT_{bot_index}_COLOR", _get_color_for_index(bot_index))
        
        bots.append({
            "name": bot_name,
            "api_url": bot_url,
            "description": bot_desc,
            "color": bot_color
        })
        bot_index += 1
    
    # 2. If no env vars found, try JSON config file
    if not bots:
        config_path = Path(__file__).parent.parent / "multi_bot_config_api.json"
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                bots = config.get("bots", [])
    
    # 3. If still no bots, use default
    if not bots:
        bots = [{
            "name": "Bot 1",
            "api_url": os.getenv("BOT1_API_URL", "http://localhost:8081"),
            "description": "Primary bot",
            "color": "#007bff"
        }]
    
    logger.info(f"📊 Loaded {len(bots)} bot(s) for dashboard")
    for bot in bots:
        logger.info(f"  - {bot['name']}: {bot['api_url']}")
    
    return {
        "bots": bots,
        "dashboard": {
            "port": int(os.getenv("DASHBOARD_PORT", "8080")),
            "auto_refresh_seconds": int(os.getenv("DASHBOARD_REFRESH", "30"))
        }
    }

def _get_color_for_index(index: int) -> str:
    """Get a color for a bot based on its index"""
    colors = [
        "#007bff",  # Blue
        "#28a745",  # Green
        "#dc3545",  # Red
        "#ffc107",  # Yellow
        "#17a2b8",  # Cyan
        "#6f42c1",  # Purple
        "#fd7e14",  # Orange
        "#20c997",  # Teal
    ]
    return colors[(index - 1) % len(colors)]

CONFIG = load_config()

def fetch_bot_data(bot_config: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch data from a bot's API"""
    api_url = bot_config['api_url']
    bot_data = {
        "name": bot_config['name'],
        "description": bot_config.get('description', ''),
        "color": bot_config.get('color', '#007bff'),
        "connected": False,
        "error": None,
        "status": {},
        "trades": [],
        "positions": [],
        "metrics": {},
        "config": {}
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
    <title>Polymarket Copy Trading - Enterprise Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0e1a;
            --bg-secondary: #12172a;
            --bg-card: #1a1f35;
            --bg-card-hover: #1f2540;
            --border-color: rgba(99, 102, 241, 0.1);
            --text-primary: #ffffff;
            --text-secondary: #94a3b8;
            --accent-blue: #6366f1;
            --accent-purple: #8b5cf6;
            --accent-cyan: #06b6d4;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --accent-orange: #f97316;
            --glow-blue: rgba(99, 102, 241, 0.3);
            --glow-purple: rgba(139, 92, 246, 0.3);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            background: linear-gradient(135deg, var(--bg-primary) 0%, #0f1420 50%, var(--bg-primary) 100%);
            color: var(--text-primary);
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            min-height: 100vh;
            padding: 0;
            overflow-x: hidden;
        }
        
        /* Animated background gradient */
        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: 
                radial-gradient(circle at 20% 50%, rgba(99, 102, 241, 0.1) 0%, transparent 50%),
                radial-gradient(circle at 80% 80%, rgba(139, 92, 246, 0.1) 0%, transparent 50%),
                radial-gradient(circle at 40% 80%, rgba(6, 182, 212, 0.05) 0%, transparent 50%);
            animation: bgShift 20s ease-in-out infinite;
            z-index: -1;
        }
        
        @keyframes bgShift {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.8; transform: scale(1.05); }
        }
        
        /* Navbar */
        .navbar {
            background: rgba(26, 31, 53, 0.8);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border-color);
            padding: 1rem 0;
        }
        
        .navbar-brand {
            font-size: 1.5rem;
            font-weight: 800;
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .nav-stats {
            display: flex;
            gap: 2rem;
            align-items: center;
        }
        
        .nav-stat {
            font-size: 0.85rem;
            color: var(--text-secondary);
        }
        
        .nav-stat strong {
            color: var(--text-primary);
            font-weight: 600;
            margin-left: 0.25rem;
        }
        
        /* Container */
        .dashboard-container {
            max-width: 1600px;
            margin: 0 auto;
            padding: 2rem 1.5rem;
        }
        
        /* Header */
        .dashboard-header {
            margin-bottom: 2.5rem;
        }
        
        .dashboard-title {
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(135deg, #fff 0%, var(--text-secondary) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 0.5rem;
        }
        
        .dashboard-subtitle {
            color: var(--text-secondary);
            font-size: 1rem;
            font-weight: 400;
        }
        
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2.5rem;
        }
        
        .stat-card {
            background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.75rem;
            position: relative;
            overflow: hidden;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            cursor: pointer;
        }
        
        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-purple));
            transform: scaleX(0);
            transition: transform 0.3s ease;
        }
        
        .stat-card:hover {
            transform: translateY(-4px);
            border-color: var(--accent-blue);
            box-shadow: 0 20px 40px rgba(99, 102, 241, 0.2);
        }
        
        .stat-card:hover::before {
            transform: scaleX(1);
        }
        
        .stat-icon {
            width: 48px;
            height: 48px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 1rem;
            font-size: 1.5rem;
        }
        
        .stat-label {
            font-size: 0.875rem;
            color: var(--text-secondary);
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 0.5rem;
        }
        
        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            line-height: 1;
            margin-bottom: 0.5rem;
        }
        
        .stat-change {
            font-size: 0.875rem;
            font-weight: 500;
        }
        
        .stat-change.positive {
            color: var(--accent-green);
        }
        
        .stat-change.negative {
            color: var(--accent-red);
        }
        
        /* Bot Cards */
        .bots-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(500px, 1fr));
            gap: 2rem;
        }
        
        .bot-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            overflow: hidden;
            transition: all 0.3s ease;
            position: relative;
        }
        
        .bot-card::after {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: radial-gradient(circle at 50% 0%, var(--glow-blue), transparent 70%);
            opacity: 0;
            transition: opacity 0.3s ease;
            pointer-events: none;
        }
        
        .bot-card:hover {
            border-color: var(--accent-blue);
            transform: translateY(-2px);
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }
        
        .bot-card:hover::after {
            opacity: 0.1;
        }
        
        .bot-header {
            padding: 1.75rem;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: relative;
        }
        
        .bot-header::before {
            content: '';
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 4px;
            background: linear-gradient(180deg, var(--accent-blue), var(--accent-purple));
        }
        
        .bot-info {
            flex: 1;
            padding-left: 1rem;
        }
        
        .bot-name {
            font-size: 1.5rem;
            font-weight: 700;
            margin-bottom: 0.25rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        
        .bot-description {
            color: var(--text-secondary);
            font-size: 0.875rem;
        }
        
        .status-badge {
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .status-badge.connected {
            background: linear-gradient(135deg, rgba(16, 185, 129, 0.2), rgba(16, 185, 129, 0.1));
            color: var(--accent-green);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }
        
        .status-badge.disconnected {
            background: linear-gradient(135deg, rgba(239, 68, 68, 0.2), rgba(239, 68, 68, 0.1));
            color: var(--accent-red);
            border: 1px solid rgba(239, 68, 68, 0.3);
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            animation: pulse 2s ease-in-out infinite;
        }
        
        .connected .status-dot {
            background: var(--accent-green);
            box-shadow: 0 0 10px var(--accent-green);
        }
        
        .disconnected .status-dot {
            background: var(--accent-red);
            box-shadow: 0 0 10px var(--accent-red);
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.6; transform: scale(1.2); }
        }
        
        /* Bot Metrics Grid */
        .bot-metrics {
            padding: 1.75rem;
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1.5rem;
        }
        
        .bot-metric {
            text-align: center;
        }
        
        .bot-metric-label {
            font-size: 0.75rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 0.5rem;
        }
        
        .bot-metric-value {
            font-size: 1.5rem;
            font-weight: 700;
        }
        
        /* Collapsible Sections */
        .collapsible-section {
            border-top: 1px solid var(--border-color);
        }
        
        .collapsible-header {
            padding: 1.25rem 1.75rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            transition: background 0.2s ease;
            user-select: none;
        }
        
        .collapsible-header:hover {
            background: rgba(99, 102, 241, 0.05);
        }
        
        .collapsible-title {
            font-size: 1rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        
        .collapsible-icon {
            transition: transform 0.3s ease;
        }
        
        .collapsed .collapsible-icon {
            transform: rotate(-90deg);
        }
        
        .collapsible-content {
            padding: 0 1.75rem 1.75rem;
            max-height: 500px;
            overflow-y: auto;
        }
        
        .collapsible-content::-webkit-scrollbar {
            width: 6px;
        }
        
        .collapsible-content::-webkit-scrollbar-track {
            background: var(--bg-secondary);
            border-radius: 3px;
        }
        
        .collapsible-content::-webkit-scrollbar-thumb {
            background: var(--accent-blue);
            border-radius: 3px;
        }
        
        /* Position & Trade Items */
        .position-item, .trade-item {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1rem;
            margin-bottom: 0.75rem;
            transition: all 0.2s ease;
        }
        
        .position-item:hover, .trade-item:hover {
            border-color: var(--accent-blue);
            background: var(--bg-card-hover);
        }
        
        .item-title {
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: var(--text-primary);
        }
        
        .item-details {
            font-size: 0.875rem;
            color: var(--text-secondary);
            display: flex;
            flex-wrap: wrap;
            gap: 1rem;
        }
        
        .detail-badge {
            padding: 0.25rem 0.75rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        .badge-success {
            background: rgba(16, 185, 129, 0.2);
            color: var(--accent-green);
        }
        
        .badge-warning {
            background: rgba(249, 115, 22, 0.2);
            color: var(--accent-orange);
        }
        
        .badge-danger {
            background: rgba(239, 68, 68, 0.2);
            color: var(--accent-red);
        }
        
        /* Responsive Design */
        @media (max-width: 1200px) {
            .bots-grid {
                grid-template-columns: 1fr;
            }
        }
        
        @media (max-width: 768px) {
            .stats-grid {
                grid-template-columns: repeat(2, 1fr);
            }
            
            .bot-metrics {
                grid-template-columns: repeat(2, 1fr);
            }
            
            .dashboard-title {
                font-size: 2rem;
            }
            
            .nav-stats {
                display: none;
            }
        }
        
        @media (max-width: 480px) {
            .stats-grid {
                grid-template-columns: 1fr;
            }
            
            .bot-metrics {
                grid-template-columns: 1fr;
            }
        }
        
        /* Loading Animation */
        .refresh-indicator {
            display: inline-block;
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        
        /* Empty State */
        .empty-state {
            text-align: center;
            padding: 3rem 1rem;
            color: var(--text-secondary);
        }
        
        .empty-state i {
            font-size: 3rem;
            margin-bottom: 1rem;
            opacity: 0.3;
        }
    </style>
</head>
<body>
        <!-- Navigation Bar -->
        <nav class="navbar navbar-expand-lg fixed-top">
            <div class="container-fluid" style="max-width: 1600px; margin: 0 auto; padding: 0 1.5rem;">
                <div class="navbar-brand">
                    <i class="fas fa-robot"></i> Polymarket Copy Trading
                </div>
                <div class="nav-stats d-none d-lg-flex">
                    <div class="nav-stat">
                        <i class="fas fa-server"></i>
                        <strong>{{ combined.connected_bots }}/{{ combined.total_bots }}</strong> Bots
                    </div>
                    <div class="nav-stat">
                        <i class="fas fa-clock"></i>
                        <strong id="update-time">{{ now.strftime('%H:%M:%S') }}</strong>
                        <span id="refresh-indicator" class="refresh-indicator" style="display:none;">
                            <i class="fas fa-sync-alt"></i>
                        </span>
                    </div>
                </div>
            </div>
        </nav>
    
        <!-- Main Content -->
        <div class="dashboard-container" style="margin-top: 80px;">
            <!-- Header -->
            <div class="dashboard-header">
                <h1 class="dashboard-title">
                    <i class="fas fa-chart-line"></i> Trading Dashboard
                </h1>
                <p class="dashboard-subtitle">Real-time monitoring for your copy trading bots</p>
            </div>
    
            <!-- Stats Grid -->
            <div class="stats-grid">
                <!-- Total Balance -->
                <div class="stat-card">
                    <div class="stat-icon" style="background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));">
                        <i class="fas fa-wallet"></i>
                    </div>
                    <div class="stat-label">Total Balance</div>
                    <div class="stat-value">${{ "%.2f"|format(combined.total_balance) }}</div>
                </div>
    
                <!-- Daily P&L -->
                <div class="stat-card">
                    <div class="stat-icon" style="background: linear-gradient(135deg, {% if combined.total_daily_pnl >= 0 %}var(--accent-green), var(--accent-cyan){% else %}var(--accent-red), var(--accent-orange){% endif %});">
                        <i class="fas fa-{% if combined.total_daily_pnl >= 0 %}arrow-trend-up{% else %}arrow-trend-down{% endif %}"></i>
                    </div>
                    <div class="stat-label">Daily P&L</div>
                    <div class="stat-value {% if combined.total_daily_pnl >= 0 %}stat-change positive{% else %}stat-change negative{% endif %}">
                        ${{ "%.2f"|format(combined.total_daily_pnl) }}
                    </div>
                </div>
    
                <!-- Total Positions -->
                <div class="stat-card">
                    <div class="stat-icon" style="background: linear-gradient(135deg, var(--accent-purple), var(--accent-blue));">
                        <i class="fas fa-layer-group"></i>
                    </div>
                    <div class="stat-label">Open Positions</div>
                    <div class="stat-value">{{ combined.total_positions }}</div>
                </div>
    
                <!-- Position Value -->
                <div class="stat-card">
                    <div class="stat-icon" style="background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));">
                        <i class="fas fa-chart-pie"></i>
                    </div>
                    <div class="stat-label">Position Value</div>
                    <div class="stat-value">${{ "%.2f"|format(combined.total_position_value) }}</div>
                </div>
    
                <!-- Trades Today -->
                <div class="stat-card">
                    <div class="stat-icon" style="background: linear-gradient(135deg, var(--accent-green), var(--accent-cyan));">
                        <i class="fas fa-exchange-alt"></i>
                    </div>
                    <div class="stat-label">Trades Today</div>
                    <div class="stat-value">{{ combined.total_trades_today }}</div>
                </div>
    
                <!-- Success Rate -->
                <div class="stat-card">
                    <div class="stat-icon" style="background: linear-gradient(135deg, var(--accent-orange), var(--accent-red));">
                        <i class="fas fa-bullseye"></i>
                    </div>
                    <div class="stat-label">Avg Success</div>
                    <div class="stat-value">{{ "%.1f"|format(combined.average_success_rate) }}%</div>
                </div>
            </div>
    
            <!-- Bot Cards Grid -->
            <div class="bots-grid">
                {% for bot in bots %}
                <div class="bot-card">
                    <!-- Bot Header -->
                    <div class="bot-header">
                        <div class="bot-info">
                            <div class="bot-name">
                                <i class="fas fa-robot"></i>
                                {{ bot.name }}
                            </div>
                            <div class="bot-description">{{ bot.description }}</div>
                        </div>
                        <div>
                            <span class="status-badge {% if bot.connected %}connected{% else %}disconnected{% endif %}">
                                <span class="status-dot"></span>
                                {{ 'Online' if bot.connected else 'Offline' }}
                            </span>
                        </div>
                    </div>
    
                    {% if bot.error %}
                    <div style="padding: 1rem 1.75rem; background: rgba(239, 68, 68, 0.1); border-left: 3px solid var(--accent-red);">
                        <i class="fas fa-exclamation-triangle"></i> <strong>Error:</strong> {{ bot.error }}
                    </div>
                    {% endif %}
    
                    {% if bot.connected %}
                    <!-- Bot Metrics -->
                    <div class="bot-metrics">
                        <div class="bot-metric">
                            <div class="bot-metric-label">
                                <i class="fas fa-wallet"></i> Balance
                            </div>
                            <div class="bot-metric-value">${{ "%.2f"|format(bot.get('metrics', {}).get('balance', 0)) }}</div>
                        </div>
                        <div class="bot-metric">
                            <div class="bot-metric-label">
                                <i class="fas fa-chart-line"></i> Daily P&L
                            </div>
                            <div class="bot-metric-value {% if bot.get('metrics', {}).get('daily_pnl', 0) >= 0 %}stat-change positive{% else %}stat-change negative{% endif %}">
                                ${{ "%.2f"|format(bot.get('metrics', {}).get('daily_pnl', 0)) }}
                            </div>
                        </div>
                        <div class="bot-metric">
                            <div class="bot-metric-label">
                                <i class="fas fa-layer-group"></i> Positions
                            </div>
                            <div class="bot-metric-value">{{ bot.get('metrics', {}).get('total_positions', 0) }}</div>
                        </div>
                        <div class="bot-metric">
                            <div class="bot-metric-label">
                                <i class="fas fa-percentage"></i> Copy %
                            </div>
                            <div class="bot-metric-value">{{ bot.get('config', {}).get('copy_percentage', 0) | round(1) }}%</div>
                        </div>
                        <div class="bot-metric">
                            <div class="bot-metric-label">
                                <i class="fas fa-exchange-alt"></i> Trades
                            </div>
                            <div class="bot-metric-value">{{ bot.get('metrics', {}).get('trades_today', 0) }}</div>
                        </div>
                        <div class="bot-metric">
                            <div class="bot-metric-label">
                                <i class="fas fa-bullseye"></i> Success
                            </div>
                            <div class="bot-metric-value">{{ "%.1f"|format(bot.get('metrics', {}).get('success_rate', 0)) }}%</div>
                        </div>
                        <div class="bot-metric">
                            <div class="bot-metric-label">
                                <i class="fas fa-clock"></i> Uptime
                            </div>
                            <div class="bot-metric-value">{{ (bot.get('status', {}).get('uptime_seconds', 0) / 3600) | round(1) }}h</div>
                        </div>
                        <div class="bot-metric">
                            <div class="bot-metric-label">
                                <i class="fas fa-shield-alt"></i> Mode
                            </div>
                            <div class="bot-metric-value" style="font-size: 1rem;">
                                {{ 'DRY RUN' if bot.get('status', {}).get('configuration', {}).get('dry_run') else 'LIVE' }}
                            </div>
                        </div>
                    </div>
    
                    <!-- Open Positions (Collapsible) -->
                    {% if bot.get('positions') %}
                    <div class="collapsible-section">
                        <div class="collapsible-header collapsed" onclick="toggleCollapse(this, 'positions-{{ loop.index }}')">
                            <div class="collapsible-title">
                                <i class="fas fa-chart-area"></i>
                                Open Positions ({{ bot.get('positions', []) | length }})
                            </div>
                            <i class="fas fa-chevron-down collapsible-icon"></i>
                        </div>
                        <div class="collapsible-content" id="positions-{{ loop.index }}" style="display: none;">
                            {% for pos in bot.get('positions', [])[:10] %}
                            <div class="position-item">
                                <div class="item-title">{{ pos.get('market_title', 'Unknown')[:60] }}</div>
                                <div class="item-details">
                                    <span><i class="fas fa-tag"></i> {{ pos.get('side', 'N/A') }}</span>
                                    <span><i class="fas fa-coins"></i> {{ "%.2f"|format(pos.get('size', 0)) }} shares</span>
                                    <span><i class="fas fa-dollar-sign"></i> ${{ "%.2f"|format(pos.get('avg_price', 0)) }} → ${{ "%.2f"|format(pos.get('current_price', 0)) }}</span>
                                    <span class="detail-badge {% if pos.get('unrealized_pnl', 0) >= 0 %}badge-success{% else %}badge-danger{% endif %}">
                                        P&L: ${{ "%.2f"|format(pos.get('unrealized_pnl', 0)) }}
                                    </span>
                                </div>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                    {% else %}
                    <div class="collapsible-section">
                        <div class="collapsible-header">
                            <div class="collapsible-title">
                                <i class="fas fa-chart-area"></i>
                                Open Positions (0)
                            </div>
                        </div>
                        <div style="padding: 0 1.75rem 1.75rem;">
                            <div class="empty-state">
                                <i class="fas fa-inbox"></i>
                                <p>No open positions</p>
                            </div>
                        </div>
                    </div>
                    {% endif %}
    
                    <!-- Recent Trades (Collapsible) -->
                    {% if bot.get('trades') %}
                    <div class="collapsible-section">
                        <div class="collapsible-header collapsed" onclick="toggleCollapse(this, 'trades-{{ loop.index }}')">
                            <div class="collapsible-title">
                                <i class="fas fa-list"></i>
                                Recent Trades ({{ bot.get('trades', []) | length }})
                            </div>
                            <i class="fas fa-chevron-down collapsible-icon"></i>
                        </div>
                        <div class="collapsible-content" id="trades-{{ loop.index }}" style="display: none;">
                            {% for trade in bot.get('trades', [])[:15] %}
                            <div class="trade-item">
                                <div class="item-title">{{ trade.get('market_title', 'Unknown')[:60] }}</div>
                                <div class="item-details">
                                    <span><i class="fas fa-tag"></i> {{ trade.get('side', 'N/A') }}</span>
                                    <span><i class="fas fa-coins"></i> {{ "%.2f"|format(trade.get('copy_size', 0)) }} shares</span>
                                    <span><i class="fas fa-dollar-sign"></i> ${{ "%.2f"|format(trade.get('copy_amount_usd', 0)) }}</span>
                                    <span class="detail-badge {% if trade.get('status') == 'EXECUTED' %}badge-success{% elif trade.get('status') == 'PARTIAL_FILL' %}badge-warning{% else %}badge-danger{% endif %}">
                                        {{ trade.get('status', 'UNKNOWN') }}
                                    </span>
                                    <span style="color: var(--text-secondary); font-size: 0.75rem;">
                                        <i class="fas fa-clock"></i> {{ (trade.get('timestamp', 'N/A'))[:19] if trade.get('timestamp') else 'N/A' }}
                                    </span>
                                </div>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                    {% endif %}
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </div>
    
        <!-- Bootstrap JS & Custom Scripts -->
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            // Collapsible toggle function
            function toggleCollapse(header, targetId) {
                const content = document.getElementById(targetId);
                const isCollapsed = content.style.display === 'none';
                
                if (isCollapsed) {
                    content.style.display = 'block';
                    header.classList.remove('collapsed');
                } else {
                    content.style.display = 'none';
                    header.classList.add('collapsed');
                }
            }
    
            // Auto-refresh every {{ config.auto_refresh_seconds }} seconds
            setInterval(function() {
                document.getElementById('refresh-indicator').style.display = 'inline';
                fetch('/api/refresh')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('update-time').textContent = new Date().toLocaleTimeString();
                        document.getElementById('refresh-indicator').style.display = 'none';
                        // Full page reload for simplicity
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

