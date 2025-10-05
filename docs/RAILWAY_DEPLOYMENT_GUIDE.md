# 🚂 Railway Deployment Guide for Polymarket Copy Trading Bot

Deploy your bot to Railway in minutes! Railway is easier than AWS and perfect for this bot.

---

## 💰 Pricing

- **Free Tier**: $5 credit/month (enough for light usage)
- **Hobby Plan**: $5/month for $5 credit + $0.000231/GB-hour
- **Estimated Cost**: ~$5-10/month for 24/7 operation

---

## 🚀 Quick Deploy (5 Minutes)

### Step 1: Create Railway Account

1. Go to [railway.app](https://railway.app)
2. Sign up with GitHub (recommended)
3. Verify your email

### Step 2: Deploy from GitHub

1. **Push to GitHub** (if not done already):
   ```bash
   git add railway.json Procfile nixpacks.toml runtime.txt
   git commit -m "Add Railway deployment config"
   git push origin main
   ```

2. **Create New Project on Railway**:
   - Click "New Project"
   - Select "Deploy from GitHub repo"
   - Choose `shadow-112/polybotshadow`
   - Railway will auto-detect Python and start building

### Step 3: Configure Environment Variables

In Railway dashboard:

1. Click on your project
2. Go to **Variables** tab
3. Add these variables (click "+ New Variable"):

```bash
# Required - Wallet Configuration
PRIVATE_KEY=your_private_key_here
FUNDER_ADDRESS=0xYourPolymarketWalletAddress
TARGET_TRADER_ADDRESS=0xTargetTraderAddress
SIGNATURE_TYPE=2

# Trading Settings
COPY_PERCENTAGE=0.1
MAX_POSITION_SIZE_USD=1000
MIN_POSITION_SIZE_USD=10
MAX_DAILY_LOSS_USD=500
MAX_POSITIONS=10
TRADE_DELAY_SECONDS=0

# Market Filters
MARKET_FILTERS=Bitcoin Up or Down on,Ethereum Up or Down on,Solana Up or Down on

# Bot Settings
LOG_LEVEL=INFO
DRY_RUN=false
COPY_MERGE_ACTIONS=true
COPY_REDEEM_ACTIONS=true

# Database Path (Railway persistent storage)
DATABASE_PATH=/app/data/trades.db
```

### Step 4: Deploy!

1. Click **Deploy** (or it auto-deploys)
2. Watch the build logs
3. Bot will start automatically! 🎉

---

## 📊 Monitor Your Bot

### View Logs (Real-time)

1. Go to Railway dashboard
2. Click your project
3. Click **Deployments** tab
4. Click latest deployment
5. View live logs

### Check Bot Status

Look for these in logs:
```
✅ Configuration validated
🎯 Target trader: 0x...
💰 Funder address: 0x...
🔌 WebSocket connected
✅ Bot started successfully
```

---

## 🔧 Railway CLI (Optional)

### Install Railway CLI

```bash
# macOS
brew install railway

# npm
npm install -g @railway/cli

# Or download from railway.app/cli
```

### Login and Link

```bash
railway login
cd /path/to/polybotshadow
railway link
```

### Useful Commands

```bash
# View logs
railway logs

# Run commands in Railway environment
railway run python tools/check_balance.py

# Open dashboard
railway open

# Deploy manually
railway up

# Check status
railway status

# Set environment variable
railway variables set DRY_RUN=false
```

---

## 💾 Persistent Storage (Database)

Railway provides persistent volumes for your database.

### Add Volume (Recommended)

1. Go to your project in Railway
2. Click **Settings** tab
3. Scroll to **Volumes**
4. Click **+ New Volume**
5. Set:
   - **Mount Path**: `/app/data`
   - **Size**: 1 GB (more than enough)
6. Click **Add**

Your `trades.db` will persist across deployments!

---

## 🔄 Auto-Deploy from GitHub

Railway automatically deploys when you push to GitHub!

```bash
# Make changes locally
nano src/core/config.py

# Commit and push
git add .
git commit -m "Update config"
git push origin main

# Railway auto-deploys! 🚀
```

### Disable Auto-Deploy (Optional)

1. Go to **Settings** tab
2. Find **Deployment Triggers**
3. Toggle off "Deploy on push"

---

## 🐛 Troubleshooting

### Bot Not Starting

**Check logs:**
```bash
railway logs
```

**Common issues:**
- ❌ Missing environment variables → Add in Variables tab
- ❌ Invalid `PRIVATE_KEY` → Check .env format
- ❌ Wrong `FUNDER_ADDRESS` → Use Polymarket wallet address (not MetaMask)

### "Module Not Found" Error

**Fix**: Ensure `requirements.txt` is up to date
```bash
pip freeze > requirements.txt
git add requirements.txt
git commit -m "Update requirements"
git push
```

### Database Not Persisting

**Fix**: Add a persistent volume (see above)

### Out of Memory

**Fix**: Upgrade Railway plan or optimize bot
1. Go to **Settings**
2. Increase memory limit
3. Or reduce `MAX_POSITIONS` in variables

### WebSocket Disconnecting

**Normal behavior** - bot auto-reconnects. Check logs for:
```
🔌 WebSocket disconnected, reconnecting...
✅ WebSocket reconnected
```

---

## 🔐 Security Best Practices

### 1. Never Commit Secrets

✅ Use Railway environment variables
❌ Don't put secrets in code

### 2. Use GitHub Private Repo (Optional)

```bash
# Make repo private on GitHub
# Settings → Danger Zone → Change visibility → Private
```

### 3. Enable 2FA

- Enable 2FA on Railway account
- Enable 2FA on GitHub account

### 4. Rotate Keys Regularly

Update `PRIVATE_KEY` in Railway variables if compromised.

---

## 📈 Scaling & Performance

### Monitor Resource Usage

1. Go to **Metrics** tab
2. Check:
   - CPU usage
   - Memory usage
   - Network traffic

### Optimize for Lower Costs

```bash
# In Railway Variables, set:
LOG_LEVEL=WARNING  # Less verbose logs
MAX_POSITIONS=5    # Fewer concurrent positions
```

### Upgrade Plan (If Needed)

- **Hobby**: $5/month + usage
- **Pro**: $20/month + usage (team features)

---

## 🎯 Railway vs AWS Comparison

| Feature | Railway | AWS EC2 |
|---------|---------|---------|
| Setup Time | 5 minutes | 30+ minutes |
| Ease of Use | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| Cost | ~$5-10/month | ~$8-20/month |
| Auto-Deploy | ✅ Yes | ❌ Manual |
| Logs | ✅ Built-in | ⚠️ CloudWatch |
| SSL/HTTPS | ✅ Free | ⚠️ Setup required |
| Scaling | ✅ One-click | ⚠️ Manual |
| **Best For** | Quick deploy | Full control |

---

## 🚀 Advanced: Multiple Bots

Deploy multiple bots (different traders) on Railway:

### Method 1: Separate Projects

1. Create new Railway project
2. Deploy same repo
3. Set different `TARGET_TRADER_ADDRESS`

### Method 2: Separate Branches

```bash
# Create trader2 branch
git checkout -b trader2
# Update configs
git push origin trader2

# Deploy trader2 branch in new Railway project
```

---

## 📊 Dashboard Access (Optional)

### Deploy Dashboard Separately

1. Create new Railway project
2. Deploy same repo
3. Set start command to: `python tools/dashboard.py`
4. Railway will give you a public URL
5. Access dashboard at: `https://your-app.railway.app`

**Note**: Dashboard is optional - use Railway logs for monitoring.

---

## 🔄 Backup & Recovery

### Backup Database

```bash
# Using Railway CLI
railway run python -c "import shutil; shutil.copy('data/trades.db', 'trades_backup.db')"

# Download backup
railway run cat data/trades.db > local_backup.db
```

### Restore from Backup

```bash
# Upload backup
railway run python -c "import shutil; shutil.copy('trades_backup.db', 'data/trades.db')"
```

---

## 📝 Maintenance Checklist

### Daily
- [ ] Check Railway logs for errors
- [ ] Verify bot is running (check last log timestamp)
- [ ] Check Polymarket balance

### Weekly
- [ ] Review trading performance
- [ ] Check Railway usage/costs
- [ ] Update `MARKET_FILTERS` if needed

### Monthly
- [ ] Backup database
- [ ] Review and optimize settings
- [ ] Check for bot updates (git pull)

---

## 🆘 Support & Help

### Railway Issues
- [Railway Docs](https://docs.railway.app)
- [Railway Discord](https://discord.gg/railway)
- [Railway Status](https://status.railway.app)

### Bot Issues
- Check logs: `railway logs`
- Review [README.md](../README.md)
- Check [GitHub Issues](https://github.com/shadow-112/polybotshadow/issues)

---

## 🎉 Quick Start Commands

```bash
# 1. Login to Railway
railway login

# 2. Link project
railway link

# 3. View logs
railway logs

# 4. Check balance
railway run python tools/check_balance.py

# 5. Update environment variable
railway variables set DRY_RUN=false

# 6. Restart bot
railway up --detach

# 7. Open dashboard
railway open
```

---

## ✅ Deployment Checklist

Before going live:

- [ ] GitHub repo pushed with all files
- [ ] Railway account created and verified
- [ ] Project deployed from GitHub
- [ ] All environment variables set (especially `PRIVATE_KEY`, `FUNDER_ADDRESS`)
- [ ] `SIGNATURE_TYPE=2` set correctly
- [ ] Persistent volume added for database
- [ ] Test in `DRY_RUN=true` mode first
- [ ] Check logs show "Bot started successfully"
- [ ] Verify WebSocket connected
- [ ] Test with small `COPY_PERCENTAGE` first (0.01 = 1%)
- [ ] Monitor first few trades closely
- [ ] Set up balance alerts

---

## 🎊 You're Live!

Your bot is now running 24/7 on Railway! 🚀

**Next Steps:**
1. Monitor logs for first few trades
2. Adjust `COPY_PERCENTAGE` based on performance
3. Update `MARKET_FILTERS` as needed
4. Check Railway costs after first week

**Pro Tip**: Start with `COPY_PERCENTAGE=0.01` (1%) for the first day to test everything works correctly!

---

**Happy Trading! 📈**
