"""
Admin Dashboard for Polymarket Copy Trading Platform
Monitors ALL user bots, tracks revenue, and provides platform analytics

This is for YOU (the platform owner) to monitor everything.

Run with: python tools/admin_dashboard.py
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, session, redirect, url_for
import requests
from typing import Dict, List, Any
from loguru import logger
from functools import wraps

sys.path.insert(0, str(Path(__file__).parent.parent))

app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_SECRET_KEY", "change-this-in-production-please")

# Admin credentials (set via environment variables)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme123")

# Authentication decorator
def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# Load configuration
def load_config():
    """
    Load bot configuration from environment variables OR file
    
    Priority:
    1. Environment variables (BOT_1_NAME, BOT_1_URL, BOT_1_USER, etc.)
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
        
        # Optional: user email, description, color
        bot_user = os.getenv(f"BOT_{bot_index}_USER", f"user{bot_index}@example.com")
        bot_desc = os.getenv(f"BOT_{bot_index}_DESC", f"Bot {bot_index}")
        bot_color = os.getenv(f"BOT_{bot_index}_COLOR", _get_color_for_index(bot_index))
        
        bots.append({
            "name": bot_name,
            "api_url": bot_url,
            "user_email": bot_user,
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
                # Add default user_email if not present
                for bot in bots:
                    if 'user_email' not in bot:
                        bot['user_email'] = 'unknown@example.com'
    
    # 3. If still no bots, use default
    if not bots:
        bots = [{
            "name": "Bot 1",
            "api_url": os.getenv("BOT1_API_URL", "http://localhost:8081"),
            "user_email": "demo@example.com",
            "description": "Primary bot",
            "color": "#007bff"
        }]
    
    logger.info(f"📊 Loaded {len(bots)} bot(s) for admin dashboard")
    for bot in bots:
        logger.info(f"  - {bot['name']} ({bot['user_email']}): {bot['api_url']}")
    
    return {
        "bots": bots,
        "dashboard": {
            "port": int(os.getenv("ADMIN_DASHBOARD_PORT", "8090")),
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
    bot_data = {
        "name": bot_config["name"],
        "user_email": bot_config["user_email"],
        "config": bot_config,
        "connected": False,
        "error": None,
        "status": {},
        "metrics": {},
        "trades": [],
        "positions": []
    }
    
    try:
        # Fetch bot status
        status_response = requests.get(
            f"{bot_config['api_url']}/api/status",
            timeout=5
        )
        if status_response.status_code == 200:
            bot_data["status"] = status_response.json().get("data", {})
            bot_data["connected"] = True
        
        # Fetch metrics
        metrics_response = requests.get(
            f"{bot_config['api_url']}/api/metrics",
            timeout=5
        )
        if metrics_response.status_code == 200:
            bot_data["metrics"] = metrics_response.json().get("data", {})
        
        # Fetch trades
        trades_response = requests.get(
            f"{bot_config['api_url']}/api/trades",
            timeout=5
        )
        if trades_response.status_code == 200:
            bot_data["trades"] = trades_response.json().get("data", [])
        
        # Fetch positions
        positions_response = requests.get(
            f"{bot_config['api_url']}/api/positions",
            timeout=5
        )
        if positions_response.status_code == 200:
            bot_data["positions"] = positions_response.json().get("data", [])
            
    except requests.exceptions.RequestException as e:
        bot_data["error"] = str(e)
        bot_data["connected"] = False
        logger.error(f"Failed to fetch data from {bot_config['name']}: {e}")
    
    return bot_data

def calculate_platform_stats(bots_data: List[Dict]) -> Dict[str, Any]:
    """Calculate platform-wide statistics"""
    total_balance = 0
    total_daily_pnl = 0
    total_all_time_pnl = 0
    total_trades = 0
    total_fees = 0
    active_bots = 0
    total_users = len(set(bot['user_email'] for bot in bots_data))
    
    for bot in bots_data:
        if bot['connected']:
            metrics = bot.get('metrics', {})
            total_balance += metrics.get('balance', 0)
            total_daily_pnl += metrics.get('daily_pnl', 0)
            total_all_time_pnl += metrics.get('total_pnl', 0)
            total_trades += len(bot.get('trades', []))
            
            # Calculate fees (0.5% of trade amounts)
            for trade in bot.get('trades', []):
                trade_amount = trade.get('copy_amount_usd', 0)
                total_fees += trade_amount * 0.005  # 0.5% fee
            
            if bot.get('status', {}).get('running'):
                active_bots += 1
    
    return {
        "total_balance": total_balance,
        "total_daily_pnl": total_daily_pnl,
        "total_all_time_pnl": total_all_time_pnl,
        "total_trades": total_trades,
        "total_fees": total_fees,
        "active_bots": active_bots,
        "total_bots": len(bots_data),
        "total_users": total_users
    }

def group_bots_by_user(bots_data: List[Dict]) -> Dict[str, List[Dict]]:
    """Group bots by user email"""
    users = {}
    for bot in bots_data:
        email = bot['user_email']
        if email not in users:
            users[email] = {
                "email": email,
                "bots": [],
                "total_balance": 0,
                "total_fees": 0,
                "total_trades": 0
            }
        
        users[email]["bots"].append(bot)
        users[email]["total_balance"] += bot.get('metrics', {}).get('balance', 0)
        users[email]["total_trades"] += len(bot.get('trades', []))
        
        # Calculate user's fees
        for trade in bot.get('trades', []):
            trade_amount = trade.get('copy_amount_usd', 0)
            users[email]["total_fees"] += trade_amount * 0.005
    
    return users

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['is_admin'] = True
            session['admin_username'] = username
            logger.info(f"✅ Admin logged in: {username}")
            return redirect(url_for('admin_dashboard'))
        else:
            logger.warning(f"❌ Failed login attempt: {username}")
            return render_template_string(LOGIN_HTML, error="Invalid credentials")
    
    return render_template_string(LOGIN_HTML, error=None)

@app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/')
@app.route('/admin')
@app.route('/admin/dashboard')
@require_admin
def admin_dashboard():
    """Main admin dashboard"""
    # Fetch data from all bots
    bots_data = []
    for bot_config in CONFIG["bots"]:
        bot_data = fetch_bot_data(bot_config)
        bots_data.append(bot_data)
    
    # Calculate platform stats
    platform_stats = calculate_platform_stats(bots_data)
    
    # Group bots by user
    users = group_bots_by_user(bots_data)
    
    return render_template_string(
        ADMIN_DASHBOARD_HTML,
        platform_stats=platform_stats,
        users=users,
        bots=bots_data,
        config=CONFIG["dashboard"],
        now=datetime.now(),
        admin_username=session.get('admin_username', 'Admin')
    )

@app.route('/api/refresh')
@require_admin
def api_refresh():
    """API endpoint for dashboard refresh"""
    bots_data = []
    for bot_config in CONFIG["bots"]:
        bot_data = fetch_bot_data(bot_config)
        bots_data.append(bot_data)
    
    platform_stats = calculate_platform_stats(bots_data)
    users = group_bots_by_user(bots_data)
    
    return jsonify({
        "success": True,
        "platform_stats": platform_stats,
        "users": {email: {
            "email": user["email"],
            "total_balance": user["total_balance"],
            "total_fees": user["total_fees"],
            "total_trades": user["total_trades"],
            "bot_count": len(user["bots"])
        } for email, user in users.items()},
        "timestamp": datetime.now().isoformat()
    })

# HTML Templates
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login - Polymarket Copy Trading</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .login-container {
            background: white;
            padding: 3rem;
            border-radius: 1rem;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            width: 100%;
            max-width: 400px;
        }
        
        .logo {
            text-align: center;
            margin-bottom: 2rem;
        }
        
        .logo h1 {
            font-size: 1.75rem;
            color: #667eea;
            margin-bottom: 0.5rem;
        }
        
        .logo p {
            color: #666;
            font-size: 0.875rem;
        }
        
        .form-group {
            margin-bottom: 1.5rem;
        }
        
        label {
            display: block;
            margin-bottom: 0.5rem;
            color: #333;
            font-weight: 500;
        }
        
        input {
            width: 100%;
            padding: 0.75rem;
            border: 2px solid #e0e0e0;
            border-radius: 0.5rem;
            font-size: 1rem;
            transition: border-color 0.2s;
        }
        
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        
        button {
            width: 100%;
            padding: 0.875rem;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 0.5rem;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s;
        }
        
        button:hover {
            transform: translateY(-2px);
        }
        
        .error {
            background: #fee;
            color: #c33;
            padding: 0.75rem;
            border-radius: 0.5rem;
            margin-bottom: 1rem;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">
            <h1>🔒 Admin Panel</h1>
            <p>Polymarket Copy Trading Platform</p>
        </div>
        
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        
        <form method="POST">
            <div class="form-group">
                <label>Username</label>
                <input type="text" name="username" required autofocus>
            </div>
            
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required>
            </div>
            
            <button type="submit">Login</button>
        </form>
    </div>
</body>
</html>
"""

ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Dashboard - Polymarket Copy Trading</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #667eea;
            --secondary: #764ba2;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --dark: #1a1a2e;
            --light: #f8f9fa;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Inter', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 2rem;
        }
        
        .container-fluid {
            max-width: 1600px;
        }
        
        .navbar {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            padding: 1rem 2rem;
            border-radius: 1rem;
            margin-bottom: 2rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
        }
        
        .navbar-brand {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--primary);
        }
        
        .platform-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        
        .stat-card {
            background: white;
            padding: 1.5rem;
            border-radius: 1rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
        }
        
        .stat-label {
            font-size: 0.875rem;
            color: #666;
            margin-bottom: 0.5rem;
        }
        
        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            color: var(--dark);
        }
        
        .stat-value.positive {
            color: var(--success);
        }
        
        .stat-value.negative {
            color: var(--danger);
        }
        
        .user-section {
            background: white;
            padding: 2rem;
            border-radius: 1rem;
            margin-bottom: 2rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
        }
        
        .user-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            padding-bottom: 1rem;
            border-bottom: 2px solid #f0f0f0;
        }
        
        .user-email {
            font-size: 1.25rem;
            font-weight: 600;
            color: var(--dark);
        }
        
        .user-stats {
            display: flex;
            gap: 2rem;
            font-size: 0.875rem;
            color: #666;
        }
        
        .bot-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 1rem;
        }
        
        .bot-card {
            background: #f8f9fa;
            padding: 1.5rem;
            border-radius: 0.75rem;
            border-left: 4px solid var(--primary);
        }
        
        .bot-card.offline {
            opacity: 0.6;
            border-left-color: #ccc;
        }
        
        .bot-name {
            font-weight: 600;
            margin-bottom: 0.5rem;
        }
        
        .bot-status {
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 1rem;
            font-size: 0.75rem;
            font-weight: 600;
            margin-bottom: 1rem;
        }
        
        .bot-status.online {
            background: #d1fae5;
            color: #065f46;
        }
        
        .bot-status.offline {
            background: #fee2e2;
            color: #991b1b;
        }
        
        .bot-metrics {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.75rem;
            font-size: 0.875rem;
        }
        
        .metric {
            display: flex;
            justify-content: space-between;
        }
        
        .metric-label {
            color: #666;
        }
        
        .metric-value {
            font-weight: 600;
        }
        
        .logout-btn {
            background: var(--danger);
            color: white;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 0.5rem;
            cursor: pointer;
            font-weight: 600;
        }
        
        .logout-btn:hover {
            background: #dc2626;
        }
    </style>
</head>
<body>
    <div class="container-fluid">
        <!-- Navbar -->
        <nav class="navbar">
            <div class="d-flex justify-content-between align-items-center w-100">
                <div class="navbar-brand">
                    <i class="fas fa-shield-alt"></i> Admin Dashboard
                </div>
                <div class="d-flex align-items-center gap-3">
                    <span>👋 {{ admin_username }}</span>
                    <a href="/admin/logout" class="logout-btn">
                        <i class="fas fa-sign-out-alt"></i> Logout
                    </a>
                </div>
            </div>
        </nav>
        
        <!-- Platform Stats -->
        <div class="platform-stats">
            <div class="stat-card">
                <div class="stat-label">💰 Total Revenue</div>
                <div class="stat-value positive">${{ "%.2f"|format(platform_stats.total_fees) }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">👥 Total Users</div>
                <div class="stat-value">{{ platform_stats.total_users }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">🤖 Active Bots</div>
                <div class="stat-value">{{ platform_stats.active_bots }}/{{ platform_stats.total_bots }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">📊 Total Trades</div>
                <div class="stat-value">{{ platform_stats.total_trades }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">💵 Total Balance</div>
                <div class="stat-value">${{ "%.2f"|format(platform_stats.total_balance) }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">📈 Daily P&L</div>
                <div class="stat-value {% if platform_stats.total_daily_pnl >= 0 %}positive{% else %}negative{% endif %}">
                    ${{ "%.2f"|format(platform_stats.total_daily_pnl) }}
                </div>
            </div>
        </div>
        
        <!-- Users & Their Bots -->
        {% for email, user in users.items() %}
        <div class="user-section">
            <div class="user-header">
                <div>
                    <div class="user-email">
                        <i class="fas fa-user"></i> {{ user.email }}
                    </div>
                </div>
                <div class="user-stats">
                    <div><strong>Balance:</strong> ${{ "%.2f"|format(user.total_balance) }}</div>
                    <div><strong>Fees Paid:</strong> ${{ "%.2f"|format(user.total_fees) }}</div>
                    <div><strong>Trades:</strong> {{ user.total_trades }}</div>
                    <div><strong>Bots:</strong> {{ user.bots|length }}</div>
                </div>
            </div>
            
            <div class="bot-grid">
                {% for bot in user.bots %}
                <div class="bot-card {% if not bot.connected %}offline{% endif %}">
                    <div class="bot-name">🤖 {{ bot.name }}</div>
                    <span class="bot-status {% if bot.connected %}online{% else %}offline{% endif %}">
                        {% if bot.connected %}● Online{% else %}○ Offline{% endif %}
                    </span>
                    
                    {% if bot.connected %}
                    <div class="bot-metrics">
                        <div class="metric">
                            <span class="metric-label">Balance:</span>
                            <span class="metric-value">${{ "%.2f"|format(bot.metrics.get('balance', 0)) }}</span>
                        </div>
                        <div class="metric">
                            <span class="metric-label">Daily P&L:</span>
                            <span class="metric-value">${{ "%.2f"|format(bot.metrics.get('daily_pnl', 0)) }}</span>
                        </div>
                        <div class="metric">
                            <span class="metric-label">Trades:</span>
                            <span class="metric-value">{{ bot.trades|length }}</span>
                        </div>
                        <div class="metric">
                            <span class="metric-label">Positions:</span>
                            <span class="metric-value">{{ bot.positions|length }}</span>
                        </div>
                    </div>
                    {% else %}
                    <div style="color: #999; font-size: 0.875rem;">
                        {{ bot.error or 'Bot offline' }}
                    </div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </div>
    
    <script>
        // Auto-refresh every {{ config.auto_refresh_seconds }} seconds
        setInterval(function() {
            fetch('/api/refresh')
                .then(response => response.json())
                .then(data => {
                    console.log('Dashboard refreshed', data);
                    // Optionally update stats without full reload
                })
                .catch(error => console.error('Refresh failed:', error));
        }, {{ config.auto_refresh_seconds * 1000 }});
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = CONFIG["dashboard"]["port"]
    logger.info(f"🚀 Starting Admin Dashboard on port {port}")
    logger.info(f"📊 Monitoring {len(CONFIG['bots'])} bot(s)")
    logger.info(f"🔐 Admin login: http://localhost:{port}/admin/login")
    logger.info(f"   Username: {ADMIN_USERNAME}")
    logger.info(f"   Password: {ADMIN_PASSWORD}")
    
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )

