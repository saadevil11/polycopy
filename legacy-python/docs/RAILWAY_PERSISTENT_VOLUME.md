# Railway Persistent Volume Setup

## 🗄️ Problem
Railway containers are **ephemeral** - they lose all data when redeployed. Your trade database gets deleted on every deploy!

## ✅ Solution: Persistent Volumes

### Step 1: Create Volume in Railway Dashboard

1. Open your Railway project
2. Click on your bot service
3. Go to **"Data"** or **"Volumes"** tab
4. Click **"+ New Volume"**
5. Configure:
   ```
   Mount Path: /app/data
   Size: 1 GB
   ```
6. Click **"Add"**

### Step 2: Set Environment Variable

In Railway **Variables** tab, add:

```bash
DB_PATH=/app/data
```

This tells the bot to store the database in the persistent volume.

### Step 3: Redeploy

Railway will automatically redeploy with the volume attached.

---

## 📊 What Gets Persisted

With the volume mounted at `/app/data`, these files persist across deploys:

```
/app/data/
  └── trades.db          # All trade history
```

---

## 🔍 Verify It's Working

After deploying, check the logs for:

```
✅ Database initialized: /app/data/trades.db
```

---

## 🧹 Local Development

Locally, the bot uses `./data/trades.db` (not persistent volume).

Your `.gitignore` excludes `data/` so the database won't be committed.

---

## 🚨 Important Notes

1. **Volumes persist forever** - even if you delete/redeploy the service
2. **One volume per service** - can't share between services
3. **Backups**: Railway doesn't auto-backup volumes, consider periodic exports
4. **Size**: 1 GB is more than enough for years of trades

---

## 📈 Database Size Reference

```
~1,000 trades   = ~1 MB
~10,000 trades  = ~10 MB
~100,000 trades = ~100 MB
```

You have PLENTY of space! 🎉

