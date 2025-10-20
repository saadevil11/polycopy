# Railway API-Based Dashboard Setup Guide

## 🎯 Overview

This guide shows you how to deploy a **centralized dashboard** on Railway that monitors multiple copy trading bots via their APIs.

**No volume mounting needed!** Each bot exposes an API, and the dashboard queries them.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────┐
│            Railway Project                   │
│                                              │
│  ┌──────────────┐      ┌──────────────┐    │
│  │  BTC Bot     │      │  XRP Bot     │    │
│  │              │      │              │    │
│  │  Trading ✓   │      │  Trading ✓   │    │
│  │  API :8081   │      │  API :8082   │    │
│  └──────┬───────┘      └──────┬───────┘    │
│         │                     │             │
│         │   HTTP (Internal)   │             │
│         └─────────┬───────────┘             │
│                   │                          │
│          ┌────────▼────────┐                │
│          │   Dashboard     │                │
│          │   Port: 8080    │                │
│          │   (Public URL)  │                │
│          └─────────────────┘                │
│                                              │
└─────────────────────────────────────────────┘
```

---

## 📋 Step-by-Step Setup

### **Part 1: Enable API on Bots**

For **each bot** (BTC, XRP, etc.):

#### 1. Add Environment Variable

In Railway Dashboard → Service → Variables:

```
API_PORT=8081  # Use 8082 for second bot, 8083 for third, etc.
```

#### 2. Redeploy

Railway will automatically redeploy. Check logs:
```
✅ API server enabled on port 8081
```

#### 3. Get Internal URL

Go to Service → Settings → Networking:
- You'll see something like: `btc-bot-production.up.railway.app`
- This is your **internal URL** (bots can talk to each other)

**Repeat for all bots!**

---

### **Part 2: Deploy Dashboard Service**

#### 1. Create New Service

In Railway:
- Click **"New"** → **"Empty Service"**
- Name it: **`dashboard`**

#### 2. Connect GitHub

- Service Settings → **"Connect"** → GitHub
- Select repo: **`polybotshadow`**
- Root Directory: `/` (default)

#### 3. Set Start Command

Service Settings → **Deploy** → Custom Start Command:
```bash
python tools/multi_bot_dashboard_api.py
```

#### 4. Create Config File

Update `multi_bot_config_api.json` with your bot URLs:

```json
{
  "bots": [
    {
      "name": "BTC Bot",
      "api_url": "http://btc-bot-production.railway.internal:8081",
      "description": "BTC copy trading bot",
      "color": "#007bff"
    },
    {
      "name": "XRP Bot",
      "api_url": "http://xrp-bot-production.railway.internal:8082",
      "description": "XRP copy trading bot",
      "color": "#28a745"
    }
  ],
  "dashboard": {
    "port": 8080,
    "auto_refresh_seconds": 30
  }
}
```

**Important:** Use `.railway.internal` for internal network communication (faster + secure).

#### 5. Commit and Push

```bash
git add multi_bot_config_api.json
git commit -m "Configure API dashboard for Railway"
git push
```

#### 6. Generate Public Domain

- Service Settings → **Networking** → **"Generate Domain"**
- Your dashboard: `https://dashboard-production-xxxx.railway.app`

---

## 🎉 Access Your Dashboard

Visit your generated URL:
```
https://dashboard-production-xxxx.railway.app
```

You should see:
- ✅ All bots connected
- 📊 Combined overview (total P&L, positions, balance)
- 📈 Individual bot stats
- 📝 Recent trades
- 💰 Open positions
- 🔄 Auto-refresh every 30 seconds

---

## 🔧 Configuration Options

### Change Refresh Rate

Edit `multi_bot_config_api.json`:
```json
"dashboard": {
  "auto_refresh_seconds": 10  // Faster refresh (10 seconds)
}
```

### Add More Bots

Just add to the `bots` array:
```json
{
  "name": "ETH Bot",
  "api_url": "http://eth-bot-production.railway.internal:8083",
  "description": "ETH copy trading bot",
  "color": "#6f42c1"
}
```

### Custom Colors

Use any hex color:
- Blue: `#007bff`
- Green: `#28a745`
- Purple: `#6f42c1`
- Red: `#dc3545`
- Orange: `#fd7e14`
- Teal: `#20c997`

---

## 🔒 Security

### Internal Network
- Bots communicate via Railway's **private network** (`.railway.internal`)
- Dashboard is the only public-facing service
- Bot APIs are **not exposed** to the internet

### Optional: Add Authentication

If you want to add a password to the dashboard, update `tools/multi_bot_dashboard_api.py`:

```python
from flask_httpauth import HTTPBasicAuth
auth = HTTPBasicAuth()

@auth.verify_password
def verify_password(username, password):
    return username == "admin" and password == os.getenv("DASHBOARD_PASSWORD")

@app.route('/')
@auth.login_required
def dashboard():
    # ... rest of code
```

Then set `DASHBOARD_PASSWORD` in Railway dashboard variables.

---

## 🐛 Troubleshooting

### Dashboard shows "Disconnected"

**Check bot logs:**
```bash
railway logs --service=btc
```

Look for:
```
✅ API server enabled on port 8081
```

If missing, verify `API_PORT` variable is set.

### "Connection refused" error

**Check internal URLs:**
- Use `.railway.internal` domain
- Format: `http://SERVICE-NAME.railway.internal:PORT`
- Example: `http://btc-bot-production.railway.internal:8081`

### Dashboard won't load

**Check dashboard logs:**
```bash
railway logs --service=dashboard
```

Look for errors in config file or missing `multi_bot_config_api.json`.

### Slow refresh

- Increase `auto_refresh_seconds` to reduce API calls
- Check if bots are responding slowly (Railway may be cold-starting)

---

## 📊 API Endpoints (for reference)

Each bot exposes these endpoints:

| Endpoint | Description |
|----------|-------------|
| `/health` | Health check |
| `/api/status` | Bot status (running, uptime, trades) |
| `/api/trades` | Recent trades (default: 20) |
| `/api/positions` | Current open positions |
| `/api/metrics` | P&L, success rate, balance |
| `/api/config` | Bot configuration (non-sensitive) |

**Example:**
```bash
curl http://btc-bot.railway.internal:8081/api/status
```

---

## ✅ Benefits of API-Based Dashboard

1. **No Volume Mounting**: Works on any Railway plan
2. **Fast**: Direct HTTP calls (no DB downloads)
3. **Scalable**: Add unlimited bots
4. **Secure**: Private Railway network
5. **Real-time**: 30-second refresh (or faster)
6. **Independent**: Dashboard doesn't affect bot performance

---

## 🚀 Next Steps

### Local Testing (Optional)

Test locally before deploying:

```bash
# Terminal 1: Start bot with API
API_PORT=8081 python start_bot.py

# Terminal 2: Start dashboard
python tools/multi_bot_dashboard_api.py
```

Visit: http://localhost:8080

### Production Deployment

Once tested locally:
1. Push to GitHub
2. Railway auto-deploys
3. Access via public URL

---

## 📝 Summary

**What you did:**
1. ✅ Added `API_PORT` to bot environment variables
2. ✅ Created dashboard service on Railway
3. ✅ Configured `multi_bot_config_api.json` with bot URLs
4. ✅ Generated public domain for dashboard
5. ✅ Accessed dashboard from anywhere!

**Result:**
- 🎯 Centralized monitoring for all bots
- 📊 Real-time data
- 🔒 Secure (private network)
- 🚀 Fast (no DB downloads)

---

**Questions?** Check the troubleshooting section or review Railway logs!

