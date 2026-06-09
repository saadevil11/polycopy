# Quick Railway Dashboard Setup

## 🚀 Deploy in 5 Minutes

### Step 1: Create Dashboard Service

In Railway dashboard:
1. Click **"New"** → **"Empty Service"**
2. Name it: **"dashboard"**

### Step 2: Connect GitHub

1. Service Settings → **"Connect"** → GitHub
2. Select your repo: **polybotshadow**
3. Root Directory: `/` (leave default)

### Step 3: Set Start Command

Service Settings → **Deploy**:
```
python tools/multi_bot_dashboard_railway.py
```

### Step 4: Mount Bot Volumes ⚠️ IMPORTANT

Go to Service Settings → **Volumes** → **"Mount from another service"**

**Mount 1 - BTC Bot:**
- Source Service: `btc`
- Source Path: `/app/data`
- Mount Path: `/mnt/btc-data`
- Click "Add"

**Mount 2 - XRP Bot:**
- Source Service: `xrp`
- Source Path: `/app/data`  
- Mount Path: `/mnt/xrp-data`
- Click "Add"

### Step 5: Add Config File

The dashboard needs to know where to find the mounted databases.

Create a new file in your repo: `multi_bot_config.json`

```json
{
  "bots": [
    {
      "name": "btc",
      "db_path": "/mnt/btc-data/trades.db",
      "description": "BTC trader bot",
      "color": "#007bff",
      "type": "local"
    },
    {
      "name": "xrp",
      "db_path": "/mnt/xrp-data/trades.db",
      "description": "XRP trader bot",
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

Commit and push:
```bash
git add multi_bot_config.json
git commit -m "Add Railway dashboard config"
git push
```

### Step 6: Generate Domain

Service Settings → **Networking** → **"Generate Domain"**

Your dashboard will be at: `https://dashboard-production-xxxx.railway.app`

### Step 7: Open Dashboard! 🎉

Click the generated URL - you'll see:
- ✅ Both bots connected
- 📊 Real-time trade data
- 💰 Combined P&L
- 📈 All positions
- 📝 Recent activity

## Why This Works

```
┌─────────────────────────────────────┐
│         Railway Project              │
│                                      │
│  ┌────────┐  ┌────────┐            │
│  │  btc   │  │  xrp   │            │
│  │ Volume │  │ Volume │            │
│  │ /app/  │  │ /app/  │            │
│  │  data  │  │  data  │            │
│  └───┬────┘  └────┬───┘            │
│      │            │                 │
│      └────┬───────┘                 │
│           │ Mounted to              │
│      ┌────▼──────────┐              │
│      │  Dashboard    │              │
│      │  /mnt/btc-    │              │
│      │  /mnt/xrp-    │              │
│      │  Reads both   │              │
│      │  databases ✅ │              │
│      └───────────────┘              │
│                                      │
└─────────────────────────────────────┘
```

## Benefits

- ✅ **No CLI needed** - runs on Railway
- ✅ **Direct access** - reads databases instantly
- ✅ **Always on** - 24/7 availability
- ✅ **Secure** - Railway handles everything
- ✅ **Fast** - no network downloads

## Troubleshooting

**Dashboard shows "disconnected":**
1. Check volumes are mounted: Service → Volumes tab
2. Verify paths in config match mount points
3. Check logs: `railway logs --service=dashboard`

**Can't see trades:**
1. Make sure bots have executed trades
2. Check database files exist in bot volumes
3. Wait for auto-refresh (30 seconds)

That's it! 🚀

