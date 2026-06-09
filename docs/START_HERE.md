# 🚀 START HERE - Multi-Bot Dashboard Setup

**Everything you need to monitor both your Polymarket bots in one place!**

---

## 🎯 What This Does

You have (or will have) **2 bots on Railway**, each with:
- Different wallet
- Different trader to copy
- Own database

The **unified dashboard** lets you:
- ✅ Monitor both bots simultaneously
- ✅ See combined P&L across all wallets
- ✅ Compare performance side-by-side
- ✅ Track all positions in one view
- ✅ View recent activity from both

---

## 🏁 Quick Start (5 Minutes)

### Step 1: Deploy Second Bot on Railway

Since you mentioned you have a second wallet:

1. **Go to Railway** → Your project
2. Click **"New"** → **"GitHub Repo"**
3. Select the same repository
4. Railway creates a **second service** (e.g., `ticket-1`)

5. **Set environment variables** for the second bot:
   ```
   PRIVATE_KEY=<your_second_wallet_private_key>
   FUNDER_ADDRESS=<your_second_wallet_address>
   TARGET_TRADER_ADDRESS=<different_trader_to_copy>
   
   COPY_PERCENTAGE=0.1
   MAX_POSITION_SIZE_USD=1000
   MIN_POSITION_SIZE_USD=0.1
   DRY_RUN=false
   ```

6. **Add Volume**:
   - Variables tab → Add: `DB_PATH=/app/data`
   - Settings → Volumes → New Volume at `/app/data`

7. **Deploy!** ✅

### Step 2: Install Railway CLI (One-time)

```bash
# Install
npm i -g @railway/cli

# Login
railway login

# Link to your project
cd /Users/saadshafqat/Desktop/ticket
railway link
```

### Step 3: Update Dashboard Config

Edit `multi_bot_config.json`:

```json
{
  "bots": [
    {
      "name": "Bot 1 - Primary Wallet",
      "db_path": "/app/data/trades.db",
      "description": "Copying Trader A",
      "color": "#007bff",
      "type": "railway",
      "railway_service": "ticket"          ← Your first service name
    },
    {
      "name": "Bot 2 - Secondary Wallet",
      "db_path": "/app/data/trades.db",
      "description": "Copying Trader B",
      "color": "#28a745",
      "type": "railway",
      "railway_service": "ticket-1"        ← Your second service name
    }
  ]
}
```

**Find your service names:**
- Open Railway dashboard
- Look in left sidebar for service names
- Usually: `ticket`, `ticket-1`, `ticket-production`, etc.

### Step 4: Start Dashboard

```bash
./start_dashboard.sh
```

Or manually:
```bash
python tools/multi_bot_dashboard_railway.py
```

### Step 5: Open in Browser

Navigate to: **http://localhost:8080**

🎉 **You're done!**

---

## 📊 What You'll See

### Top Section: Combined Summary
```
📊 Combined Portfolio Summary
┌─────────────────────────────────────────────┐
│  Total Positions: 12                         │
│  Total Value: $5,432.00                      │
│  Total P&L: +$234.50  ✅                     │
│  Total Volume: $50,000.00                    │
│  Total Trades: 127                           │
│  Avg Success Rate: 87.5%                     │
└─────────────────────────────────────────────┘
```

### Middle Section: Individual Bots
```
┌──────────────────────────┐  ┌──────────────────────────┐
│ Bot 1 - Primary Wallet   │  │ Bot 2 - Secondary Wallet │
│ 🟢 Connected             │  │ 🟢 Connected             │
├──────────────────────────┤  ├──────────────────────────┤
│ Today: 5 trades          │  │ Today: 3 trades          │
│ Success: 45 (90%)        │  │ Success: 28 (85%)        │
│ Open Positions: 7        │  │ Open Positions: 5        │
│ P&L: +$125.50            │  │ P&L: +$108.75            │
│ Volume: $25,000          │  │ Volume: $25,000          │
│                          │  │                          │
│ Top 5 Positions:         │  │ Top 5 Positions:         │
│ • Bitcoin Up or Down...  │  │ • Ethereum Up or Down... │
│ • Solana Down...         │  │ • Trump vs Biden...      │
│ • ...                    │  │ • ...                    │
└──────────────────────────┘  └──────────────────────────┘
```

### Bottom Section: Activity Feed
```
📝 Recent Activity (All Bots)
┌─────────────────────────────────────────────────────────┐
│ [Bot 1] Bitcoin Up or Down on Oct 10    🟢 BUY  $50.00 │
│ [Bot 2] Ethereum Up or Down on Oct 10   🔴 SELL $30.00 │
│ [Bot 1] Solana Up or Down on Oct 10     🟢 BUY  $25.00 │
└─────────────────────────────────────────────────────────┘
```

---

## 🎨 Customization

### Change Bot Names

Make them meaningful:
```json
"name": "🐋 Whale Follower Bot"
"name": "📈 Trend Trader Bot"
"name": "⚡ Quick Scalper Bot"
```

### Change Colors

```json
"color": "#007bff"  // Blue
"color": "#28a745"  // Green
"color": "#6f42c1"  // Purple
"color": "#dc3545"  // Red
"color": "#fd7e14"  // Orange
```

### Change Port

```json
"dashboard": {
  "port": 3000    // Instead of 8080
}
```

### Change Refresh Rate

```json
"dashboard": {
  "auto_refresh_seconds": 60    // Instead of 30
}
```

---

## 🔧 Troubleshooting

### Bot Shows "Disconnected"

**Check:**
1. Railway service name in config matches exactly
2. Bot is actually running on Railway
3. Volume is mounted at `/app/data`

**Fix:**
```bash
# Check service names
railway status

# Check logs
railway logs --service=ticket
railway logs --service=ticket-1
```

### "Railway CLI not found"

```bash
npm i -g @railway/cli
```

### "Not logged in to Railway"

```bash
railway login
```

### Dashboard Won't Start

```bash
# Check if port 8080 is in use
lsof -i :8080

# Use different port in config if needed
```

### No Data Showing

1. Wait 30 seconds for first refresh
2. Check that bots have executed trades
3. Verify databases exist: `railway run --service=ticket ls /app/data`

---

## 📚 Documentation

**Quick Start:**
- `DASHBOARD_QUICKSTART.md` - Basic setup guide
- `SETUP_SUMMARY.md` - What was created

**Detailed Guides:**
- `docs/MULTI_BOT_DASHBOARD_GUIDE.md` - Complete documentation
- `docs/RAILWAY_DEPLOYMENT_GUIDE.md` - Deploy bots to Railway
- `ARCHITECTURE_DIAGRAM.md` - System architecture

**Configuration:**
- `multi_bot_config.json` - Your bot configuration
- `multi_bot_config.local.example.json` - Example for local testing

---

## 💡 Pro Tips

1. **Descriptive Names**: Use meaningful names for bots
2. **Color Code**: Different colors for different strategies
3. **Keep Open**: Leave dashboard open in browser tab
4. **Daily Check**: Review combined P&L every day
5. **Compare**: Use side-by-side view to see which bot performs better

---

## 🎯 Workflow

### Daily Monitoring
```
1. Open dashboard: http://localhost:8080
2. Check combined P&L
3. Review each bot's performance
4. Check recent activity
5. Adjust strategies if needed
```

### Weekly Analysis
```
1. Export data via API: http://localhost:8080/api/status
2. Compare bot performance
3. Analyze success rates
4. Review position history
5. Optimize bot parameters
```

---

## 🔐 Security Notes

- ✅ Dashboard runs on **localhost** (only you can access)
- ✅ Private keys stay on Railway (never downloaded)
- ✅ Only **database data** is downloaded (no keys)
- ✅ Each bot isolated in separate Railway service

**For remote access:**
```bash
# Use SSH tunnel (secure)
ssh -L 8080:localhost:8080 user@your-server
```

---

## 📱 Mobile Access

Want to check from your phone?

**Option 1: SSH Tunnel** (Recommended)
1. Install SSH app on phone (e.g., Termius)
2. Create tunnel: `ssh -L 8080:localhost:8080 user@server`
3. Open `http://localhost:8080` on phone browser

**Option 2: Deploy Dashboard to Railway** (Advanced)
1. Create new Railway service
2. Deploy dashboard code
3. Get public URL
4. ⚠️ **Add authentication!**

---

## 🆘 Common Issues

### "Service not found"
→ Check Railway dashboard for exact service names

### "Database file not found"
→ Ensure bot has executed at least one trade

### "Permission denied"
→ Run `railway login` again

### Dashboard is slow
→ Reduce refresh rate in config (60 seconds instead of 30)

---

## 🎉 You're All Set!

**To start monitoring:**

```bash
./start_dashboard.sh
```

**Then open:** http://localhost:8080

---

## 📞 Next Steps

1. ✅ Deploy second bot to Railway
2. ✅ Install Railway CLI
3. ✅ Update `multi_bot_config.json`
4. ✅ Run `./start_dashboard.sh`
5. ✅ Open http://localhost:8080
6. 📊 Start monitoring!

---

## 🌟 Features at a Glance

| Feature | Status |
|---------|--------|
| Monitor multiple bots | ✅ |
| Combined P&L | ✅ |
| Side-by-side comparison | ✅ |
| Real-time positions | ✅ |
| Activity feed | ✅ |
| Auto-refresh | ✅ |
| API endpoints | ✅ |
| Customizable colors | ✅ |
| Railway support | ✅ |
| Local bot support | ✅ |

---

**Questions?** Check the documentation in the `docs/` folder!

**Ready to monitor?** Run: `./start_dashboard.sh` 🚀

