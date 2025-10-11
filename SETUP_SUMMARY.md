# Setup Summary - Multi-Bot Dashboard

## 🎉 What You Have Now

I've created a **unified dashboard** that monitors multiple Polymarket copy trading bots simultaneously!

## 📁 Files Created

1. **`tools/multi_bot_dashboard.py`** - Basic dashboard for local bots
2. **`tools/multi_bot_dashboard_railway.py`** - Enhanced dashboard with Railway support ⭐
3. **`multi_bot_config.json`** - Configuration file for your bots
4. **`start_dashboard.sh`** - Easy launcher script
5. **`DASHBOARD_QUICKSTART.md`** - Quick start guide
6. **`docs/MULTI_BOT_DASHBOARD_GUIDE.md`** - Comprehensive documentation

## 🚀 Quick Setup (3 Steps)

### Step 1: Update Configuration

Edit `multi_bot_config.json` with your Railway service names:

```json
{
  "bots": [
    {
      "name": "Bot 1 - Primary Wallet",
      "railway_service": "ticket",        ← Your first service name
      ...
    },
    {
      "name": "Bot 2 - Secondary Wallet",
      "railway_service": "ticket-1",      ← Your second service name
      ...
    }
  ]
}
```

**Find your service names:**
- Go to Railway dashboard
- Look at service names in left sidebar
- Update the config file

### Step 2: Install Railway CLI

```bash
# Install
npm i -g @railway/cli

# Login
railway login

# Link to your project
railway link
```

### Step 3: Start Dashboard

```bash
./start_dashboard.sh
```

Then open: **http://localhost:8080**

## 🎨 What You'll See

### Combined Summary (Top)
- Total positions across both wallets
- Combined P&L
- Total volume traded
- Average success rate

### Individual Bot Cards (Middle)
Each bot shows:
- Today's trades
- Success rate
- Open positions
- Current P&L
- Top 5 positions

### Activity Feed (Bottom)
- Recent trades from both bots
- Color-coded by bot
- Real-time updates

## 🎯 For Your Second Bot on Railway

Since you mentioned you have a second funder wallet and want to copy someone else:

### On Railway:

1. **In your Railway project**, click "New" → "GitHub Repo"
2. Select the same repository
3. This creates a **second service** (e.g., `ticket-1`)
4. Set environment variables for the second bot:
   ```
   PRIVATE_KEY=<second_wallet_private_key>
   FUNDER_ADDRESS=<second_wallet_address>
   TARGET_TRADER_ADDRESS=<different_trader_address>
   ```
5. Add volume at `/app/data` path
6. Deploy!

### Update Dashboard Config:

```json
{
  "bots": [
    {
      "name": "Bot 1 - Trader A",
      "railway_service": "ticket",         ← First bot service
      "description": "Following Trader A",
      "color": "#007bff"
    },
    {
      "name": "Bot 2 - Trader B",
      "railway_service": "ticket-1",       ← Second bot service
      "description": "Following Trader B",
      "color": "#28a745"
    }
  ]
}
```

## 📊 Dashboard Features

✅ **Real-time monitoring** of both bots  
✅ **Combined P&L** across all wallets  
✅ **Side-by-side comparison** of performance  
✅ **Position tracking** for each bot  
✅ **Recent activity feed** from both  
✅ **Auto-refresh** every 30 seconds  
✅ **API endpoints** for data export  

## 🎨 Customization

### Change Bot Names
```json
"name": "🐋 Whale Follower Bot"
```

### Change Colors
```json
"color": "#6f42c1"  // Purple
```

Popular colors:
- Blue: `#007bff`
- Green: `#28a745`
- Purple: `#6f42c1`
- Red: `#dc3545`
- Orange: `#fd7e14`

### Change Port
```json
"dashboard": {
  "port": 3000
}
```

### Change Refresh Speed
```json
"dashboard": {
  "auto_refresh_seconds": 60
}
```

## 🔧 Troubleshooting

### Bot shows "Disconnected"
- Check Railway service name is correct
- Verify bot is running on Railway
- Run `railway status` to check

### "Railway CLI not found"
```bash
npm i -g @railway/cli
railway login
```

### Dashboard won't start
```bash
# Check port availability
lsof -i :8080

# Try different port in config
```

## 📱 Access from Phone

The dashboard runs on localhost by default. To access from phone:

**Option 1: SSH Tunnel (Secure)**
```bash
ssh -L 8080:localhost:8080 user@your-server
```

**Option 2: Deploy Dashboard to Railway**
- Create a new Railway service for the dashboard
- Get a public URL
- ⚠️ Add authentication if you do this!

## 💡 Pro Tips

1. **Keep it open** - Leave dashboard open in a browser tab
2. **Compare strategies** - Use different colors for different strategies
3. **Check daily** - Review combined P&L every day
4. **Export data** - Use `/api/status` endpoint to export data
5. **Multiple tabs** - Open on phone and computer simultaneously

## 📚 Documentation

- **Quick Start**: `DASHBOARD_QUICKSTART.md`
- **Full Guide**: `docs/MULTI_BOT_DASHBOARD_GUIDE.md`
- **Railway Setup**: `docs/RAILWAY_DEPLOYMENT_GUIDE.md`

## 🎯 Next Steps

1. ✅ Update `multi_bot_config.json` with your Railway service names
2. ✅ Install Railway CLI if needed
3. ✅ Run `./start_dashboard.sh`
4. ✅ Open http://localhost:8080
5. ✅ Verify both bots show up
6. 📊 Monitor your bots!

## 🆘 Need Help?

Common issues:
- **Railway service names**: Check Railway dashboard → Service names in left sidebar
- **Authentication**: Run `railway login` if needed
- **Database access**: Ensure volumes are at `/app/data` on Railway
- **Python packages**: Run `pip install flask` if needed

## 📊 What's Next?

The dashboard will show:
- **Real-time data** from both bots
- **Combined performance** metrics
- **Individual bot stats** side-by-side
- **All positions** from both wallets
- **Recent trades** from both bots

**Auto-refreshes every 30 seconds!**

---

**Ready to monitor?** 🚀

```bash
./start_dashboard.sh
```

Open: http://localhost:8080

