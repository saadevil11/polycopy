# Multi-Bot Dashboard Guide

This guide shows you how to monitor multiple copy trading bots simultaneously with a unified dashboard.

## 🎯 Features

- **Real-time Monitoring**: Track all your bots in one place
- **Combined P&L**: See total unrealized profit/loss across all wallets
- **Side-by-Side Comparison**: Compare performance of each bot
- **Position Tracking**: View open positions for each wallet
- **Recent Activity Feed**: Combined activity stream from all bots
- **Auto-Refresh**: Dashboard updates automatically every 30 seconds

## 📋 Setup Instructions

### Option 1: Local Monitoring (Both Bots Local)

If you're running both bots locally:

1. **Update the configuration file** (`multi_bot_config.json`):
```json
{
  "bots": [
    {
      "name": "Bot 1 - Primary Wallet",
      "db_path": "data/trades.db",
      "description": "First trader copy bot",
      "color": "#007bff",
      "type": "local"
    },
    {
      "name": "Bot 2 - Secondary Wallet",
      "db_path": "data/trades_bot2.db",
      "description": "Second trader copy bot",
      "color": "#28a745",
      "type": "local"
    }
  ],
  "dashboard": {
    "port": 8080,
    "auto_refresh_seconds": 30
  }
}
```

2. **Run the dashboard**:
```bash
python tools/multi_bot_dashboard.py
```

3. **Open in browser**: http://localhost:8080

### Option 2: Railway Monitoring (Bots on Railway)

If your bots are running on Railway:

#### Prerequisites
1. Install Railway CLI: https://docs.railway.app/develop/cli
2. Login to Railway: `railway login`
3. Link to your project: `railway link`

#### Configuration

Update `multi_bot_config.json`:

```json
{
  "bots": [
    {
      "name": "Bot 1 - Primary Wallet",
      "db_path": "/app/data/trades.db",
      "description": "First trader (Railway)",
      "color": "#007bff",
      "type": "railway",
      "railway_service": "ticket"
    },
    {
      "name": "Bot 2 - Secondary Wallet",
      "db_path": "/app/data/trades.db",
      "description": "Second trader (Railway)",
      "color": "#28a745",
      "type": "railway",
      "railway_service": "ticket-1"
    }
  ],
  "dashboard": {
    "port": 8080,
    "auto_refresh_seconds": 30
  }
}
```

**Note**: Replace `"ticket"` and `"ticket-1"` with your actual Railway service names.

#### Run the Dashboard

```bash
python tools/multi_bot_dashboard_railway.py
```

The dashboard will automatically download the databases from Railway and display them.

### Option 3: Mixed (One Local, One Railway)

You can mix local and Railway bots:

```json
{
  "bots": [
    {
      "name": "Bot 1 - Local",
      "db_path": "data/trades.db",
      "description": "Local development bot",
      "color": "#007bff",
      "type": "local"
    },
    {
      "name": "Bot 2 - Production",
      "db_path": "/app/data/trades.db",
      "description": "Railway production bot",
      "color": "#28a745",
      "type": "railway",
      "railway_service": "ticket"
    }
  ]
}
```

## 🎨 Customization

### Change Colors

Update the `"color"` field for each bot in the config. Use any hex color:
- Blue: `#007bff`
- Green: `#28a745`
- Purple: `#6f42c1`
- Red: `#dc3545`
- Orange: `#fd7e14`

### Change Port

Update the `"port"` in dashboard settings:
```json
"dashboard": {
  "port": 3000,
  "auto_refresh_seconds": 30
}
```

### Change Refresh Interval

Update `"auto_refresh_seconds"`:
```json
"dashboard": {
  "port": 8080,
  "auto_refresh_seconds": 60
}
```

## 📊 Dashboard Sections

### Combined Portfolio Summary
- **Total Open Positions**: Number of active positions across all bots
- **Total Position Value**: Combined value of all positions
- **Total Unrealized P&L**: Combined profit/loss
- **Total Volume Traded**: Lifetime trading volume
- **Total Trades**: Number of trades executed
- **Average Success Rate**: Average success rate across all bots

### Individual Bot Cards
Each bot shows:
- **Today's Trades**: Trades executed today
- **Successful Trades**: Total successful trades
- **Success Rate**: Percentage of successful trades
- **Open Positions**: Number of current positions
- **Unrealized P&L**: Current profit/loss
- **Total Volume**: Lifetime trading volume
- **Top 5 Positions**: Current largest positions

### Recent Activity
Combined feed of recent trades from all bots, color-coded by bot.

## 🔧 Troubleshooting

### Bot Shows "Disconnected"

**Local Bot:**
- Check that the database file exists at the specified path
- Verify the path in `multi_bot_config.json`

**Railway Bot:**
- Ensure Railway CLI is installed and authenticated
- Check that the service name matches exactly
- Run `railway status` to see available services

### Dashboard Not Loading

1. Check that port 8080 is not in use:
   ```bash
   lsof -i :8080
   ```

2. Try a different port in the config file

3. Check Python dependencies:
   ```bash
   pip install flask
   ```

### No Positions Showing

- Verify the bot has actually executed trades
- Check the database file is not corrupted
- Ensure tables exist: `sqlite3 data/trades.db ".tables"`

## 📱 API Endpoints

The dashboard also provides REST API endpoints:

### Get Combined Status
```
GET http://localhost:8080/api/status
```

Returns JSON with all bot statistics.

### Response Example
```json
{
  "status": "ok",
  "timestamp": "2025-10-09T12:00:00",
  "bots": [
    {
      "name": "Bot 1",
      "status": "connected",
      "stats": {...},
      "open_positions": 5,
      "daily_pnl": 123.45
    }
  ]
}
```

## 🚀 Running as a Service

To keep the dashboard running in the background:

### Using screen (Linux/Mac)
```bash
screen -S dashboard
python tools/multi_bot_dashboard_railway.py
# Press Ctrl+A, then D to detach
```

To reattach:
```bash
screen -r dashboard
```

### Using nohup
```bash
nohup python tools/multi_bot_dashboard_railway.py > dashboard.log 2>&1 &
```

### Using systemd (Linux)
Create `/etc/systemd/system/polymarket-dashboard.service`:
```ini
[Unit]
Description=Polymarket Multi-Bot Dashboard
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/ticket
ExecStart=/usr/bin/python3 tools/multi_bot_dashboard_railway.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable polymarket-dashboard
sudo systemctl start polymarket-dashboard
```

## 🔐 Security Notes

- The dashboard runs on localhost by default (only accessible from your computer)
- To access remotely, use SSH tunneling:
  ```bash
  ssh -L 8080:localhost:8080 user@remote-server
  ```
- Don't expose the dashboard to the public internet without authentication

## 💡 Tips

1. **Use descriptive names**: Give your bots meaningful names in the config
2. **Color code by strategy**: Use different colors for different trading strategies
3. **Monitor regularly**: Check the dashboard daily to track performance
4. **Compare performance**: Use the side-by-side view to see which bot performs better
5. **Export data**: Use the API endpoints to export data for further analysis

## 📧 Support

If you encounter issues:
1. Check the terminal output for error messages
2. Verify your configuration in `multi_bot_config.json`
3. Ensure all dependencies are installed: `pip install -r requirements.txt`
4. Check Railway logs if using Railway bots: `railway logs`

