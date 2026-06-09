# Deploy Dashboard to Railway

## Why Deploy Dashboard to Railway?

Instead of running locally and downloading databases, deploy the dashboard as a Railway service that can:
- ✅ **Directly access** bot databases via shared volumes
- ✅ **No downloads** - instant data access
- ✅ **24/7 availability** - access from anywhere
- ✅ **Auto-updates** - redeploys with bot changes

## Setup Steps

### Option 1: Separate Dashboard Service (Recommended)

1. **In Railway**, create a **new service** for the dashboard:
   - Click "New" → "Empty Service"
   - Name it "dashboard"

2. **Connect to same GitHub repo**:
   - Service Settings → Connect to GitHub
   - Choose your `polybotshadow` repo
   - Set Root Directory: `/` (same as bots)

3. **Set Environment Variables**:
   ```
   PORT=8080
   ```

4. **Configure Volumes** (Mount bot databases):
   
   **Important**: You need to mount volumes from BOTH bot services:
   
   - Go to Service Settings → Volumes
   - Click "Mount Volume from Another Service"
   
   **For BTC bot database:**
   - Source Service: `btc`
   - Source Path: `/app/data`
   - Destination Path: `/mnt/btc-data`
   
   **For XRP bot database:**
   - Source Service: `xrp`
   - Source Path: `/app/data`
   - Destination Path: `/mnt/xrp-data`

5. **Update `multi_bot_config.json` for Railway**:
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

6. **Deploy**:
   - Railway will auto-deploy
   - Generate a domain: Settings → Generate Domain
   - Access your dashboard at: `https://your-dashboard.railway.app`

### Option 2: One Service with Multiple Processes (Not Recommended)

Run bot + dashboard in same service - but this makes it harder to manage.

## Architecture After Deployment

```
Railway Project
├── btc (service)
│   └── Volume: /app/data/trades.db
├── xrp (service)
│   └── Volume: /app/data/trades.db
└── dashboard (service)
    ├── Mounts: /mnt/btc-data → btc volume
    ├── Mounts: /mnt/xrp-data → xrp volume
    └── Reads both databases directly ✅
```

## Benefits

1. **No Railway CLI needed** - direct volume access
2. **Real-time data** - no download delay
3. **Persistent** - always accessible
4. **Secure** - Railway handles auth
5. **Fast** - local disk I/O instead of network

## After Setup

Access your dashboard at:
```
https://your-dashboard.railway.app
```

See both bots' data in real-time! 🚀

## Troubleshooting

### Dashboard shows "disconnected"

- Check that volumes are mounted correctly
- Verify paths: `/mnt/btc-data/trades.db` and `/mnt/xrp-data/trades.db`
- Check Railway logs: `railway logs --service=dashboard`

### Can't mount volumes

- Make sure source services (btc, xrp) have volumes created
- Volumes must exist before mounting
- Check volume paths in source services

## Security

- Dashboard is on Railway's network
- Add Railway authentication if needed
- Or use Railway's built-in service authentication

