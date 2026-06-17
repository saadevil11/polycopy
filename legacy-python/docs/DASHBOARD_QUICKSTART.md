# Multi-Bot Dashboard - Quick Start 🚀

Monitor both of your Polymarket copy trading bots in one beautiful dashboard!

## 📸 What You'll See

- **Combined Portfolio**: Total P&L, positions, and volume across all bots
- **Individual Bot Stats**: Side-by-side comparison of each bot's performance
- **Real-Time Positions**: All open positions with current prices and P&L
- **Recent Activity**: Live feed of trades from both bots
- **Auto-Refresh**: Updates every 30 seconds automatically

## ⚡ Quick Start (3 Steps)

### 1. Update Configuration

Edit `multi_bot_config.json` and set your Railway service names:

```json
{
  "bots": [
    {
      "name": "Bot 1 - Primary Wallet",
      "railway_service": "ticket",        ← Change this to your first service name
      ...
    },
    {
      "name": "Bot 2 - Secondary Wallet", 
      "railway_service": "ticket-1",      ← Change this to your second service name
      ...
    }
  ]
}
```

**How to find your service names:**
1. Go to your Railway project
2. Look at the service names in the left sidebar
3. They're usually like: `ticket`, `ticket-1`, `ticket-production`, etc.

### 2. Install Railway CLI (if not installed)

```bash
# Install
npm i -g @railway/cli

# Login
railway login

# Link to your project
railway link
```

### 3. Start Dashboard

```bash
./start_dashboard.sh
```

Or manually:
```bash
python tools/multi_bot_dashboard_railway.py
```

Then open: **http://localhost:8080**

## 🎨 Customize Your Dashboard

### Change Bot Names

In `multi_bot_config.json`:
```json
{
  "name": "🐋 Whale Trader Bot",     ← Change this
  "description": "Following big bets", ← And this
  "color": "#007bff"
}
```

### Change Colors

```json
"color": "#28a745"    ← Choose any hex color
```

Popular colors:
- Blue: `#007bff`
- Green: `#28a745`
- Purple: `#6f42c1`
- Red: `#dc3545`
- Orange: `#fd7e14`
- Teal: `#20c997`

### Change Port

```json
"dashboard": {
  "port": 3000,    ← Change from 8080 to any port
}
```

### Change Refresh Speed

```json
"dashboard": {
  "auto_refresh_seconds": 60    ← Refresh every 60 seconds instead of 30
}
```

## 📊 Dashboard Sections Explained

### Combined Portfolio Summary (Top)
Shows your **total** performance across both bots:
- Total positions count
- Total value in USD
- Combined unrealized P&L
- Total volume traded
- Combined trade count
- Average success rate

### Individual Bot Cards (Middle)
Each bot shows:
- **Today's Trades**: How many trades today
- **Successful**: Total successful trades
- **Success Rate**: Win percentage
- **Open Positions**: Current number of positions
- **Unrealized P&L**: Current profit/loss
- **Total Volume**: Lifetime trading volume
- **Top 5 Positions**: Biggest current positions

### Recent Activity Feed (Bottom)
Combined live feed showing:
- Which bot made the trade (color-coded)
- Market name
- Buy/Sell side
- Size and price
- Total amount
- Timestamp

## 🔧 Troubleshooting

### "Railway CLI not found"
```bash
npm i -g @railway/cli
railway login
```

### "Not logged in to Railway"
```bash
railway login
```

### "Service not found"
Check your service names in Railway dashboard and update `multi_bot_config.json`

### Bot shows "Disconnected"
- Check that the Railway service name is correct
- Verify the bot is actually running on Railway
- Run `railway status` to see all services

### Dashboard not loading
1. Check port 8080 isn't in use: `lsof -i :8080`
2. Try a different port in config
3. Check Python is installed: `python --version`

## 💡 Pro Tips

1. **Keep it open**: Leave the dashboard open in a browser tab for real-time monitoring
2. **Multiple tabs**: Open the same dashboard on phone and computer
3. **Compare strategies**: Use different colors to easily distinguish bot strategies
4. **Check daily**: Review the combined P&L every day
5. **Export data**: Use the API endpoint `/api/status` to export data

## 🔐 Security

The dashboard runs on `localhost` by default - only you can see it on your computer.

**To access from another device:**
```bash
# On your server/computer running the dashboard
ssh -L 8080:localhost:8080 user@your-server

# Then open http://localhost:8080 on your local machine
```

## 📱 Access from Phone

If you want to check your bots from your phone:

1. **Option A**: Use SSH tunneling (secure)
   - Install an SSH client on your phone (e.g., Termius)
   - SSH with port forwarding
   - Open localhost:8080 in phone browser

2. **Option B**: Deploy dashboard to Railway (advanced)
   - Create a new Railway service for the dashboard
   - Add the dashboard code
   - Get a public URL from Railway
   - ⚠️ Add authentication if you do this!

## 📈 Understanding the Metrics

### Success Rate
- **70%+**: Excellent! Most trades are profitable
- **50-70%**: Good, decent win rate
- **Below 50%**: Review your strategy

### Unrealized P&L
- **Green (positive)**: Your positions are currently profitable
- **Red (negative)**: Your positions are currently at a loss
- Note: This can change as market prices move

### Position Value
Total amount currently in open positions (at current market prices)

### Volume Traded
Lifetime total of all trades executed (buy + sell)

## 🎯 Next Steps

1. ✅ Set up the dashboard
2. ✅ Verify both bots show up
3. ✅ Check that positions and trades are displaying
4. 📊 Monitor daily performance
5. 📈 Compare bot strategies
6. 🔄 Adjust bot parameters based on performance

## 📚 More Info

- Full documentation: `docs/MULTI_BOT_DASHBOARD_GUIDE.md`
- Bot setup: `docs/RAILWAY_DEPLOYMENT_GUIDE.md`
- Railway docs: https://docs.railway.app

## 🆘 Need Help?

Common issues:
1. **Railway CLI issues**: https://docs.railway.app/develop/cli
2. **Service names**: Check Railway dashboard → Your project → Service names in sidebar
3. **Database access**: Ensure volumes are mounted at `/app/data` on Railway
4. **Python errors**: Make sure Flask is installed: `pip install flask`

---

**Ready to monitor your bots?** 🎉

Run: `./start_dashboard.sh`

Then open: http://localhost:8080

