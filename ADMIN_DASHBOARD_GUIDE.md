# 🔒 Admin Dashboard Guide

The Admin Dashboard is for **YOU** (the platform owner) to monitor all users, bots, and track revenue.

---

## 🎯 What It Does

### **Monitor Everything:**
- ✅ All users and their bots
- ✅ Total platform revenue (0.5% fees)
- ✅ Active bots count
- ✅ Total trades across all users
- ✅ Platform-wide P&L
- ✅ Per-user statistics

### **Key Features:**
- 🔐 Password protected (admin only)
- 📊 Real-time stats
- 💰 Revenue tracking
- 👥 User grouping
- 🤖 Bot health monitoring
- 🔄 Auto-refresh every 30 seconds

---

## 🚀 Quick Start

### **1. Run Locally:**

```bash
# Install dependencies (if needed)
pip install flask requests loguru

# Run the admin dashboard
python tools/admin_dashboard.py
```

**Access:** http://localhost:8090/admin/login

**Default Credentials:**
- Username: `admin`
- Password: `changeme123`

⚠️ **IMPORTANT:** Change these in production!

---

### **2. Configure Bots:**

**Option A: Environment Variables** (Recommended for Railway)

```bash
# Admin credentials
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=your-secure-password
export ADMIN_SECRET_KEY=your-secret-key

# Bot configuration
export BOT_1_NAME=btc1
export BOT_1_URL=https://btc1.up.railway.app
export BOT_1_USER=john@example.com

export BOT_2_NAME=btc2
export BOT_2_URL=https://btc2.up.railway.app
export BOT_2_USER=sarah@example.com
```

**Option B: JSON Config File**

Edit `multi_bot_config_api.json`:

```json
{
  "bots": [
    {
      "name": "btc1",
      "api_url": "https://btc1.up.railway.app",
      "user_email": "john@example.com",
      "description": "Main BTC bot",
      "color": "#007bff"
    },
    {
      "name": "btc2",
      "api_url": "https://btc2.up.railway.app",
      "user_email": "sarah@example.com",
      "description": "Secondary bot",
      "color": "#28a745"
    }
  ]
}
```

---

## 🏗️ Deploy to Railway

### **Step 1: Create New Service**

1. Go to Railway dashboard
2. Click "New Project" → "Deploy from GitHub"
3. Select your repository
4. Click "Add Service"

### **Step 2: Configure Service**

**Service Name:** `admin-dashboard`

**Branch:** `main` (or create `admin-dashboard` branch)

**Start Command:**
```bash
python tools/admin_dashboard.py
```

**Environment Variables:**
```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password-here
ADMIN_SECRET_KEY=your-random-secret-key-here
ADMIN_DASHBOARD_PORT=8090

# Bot URLs (use Railway internal URLs for speed)
BOT_1_NAME=btc1
BOT_1_URL=http://btc1.railway.internal:8081
BOT_1_USER=john@example.com

BOT_2_NAME=btc2
BOT_2_URL=http://btc2.railway.internal:8081
BOT_2_USER=sarah@example.com
```

### **Step 3: Deploy**

Railway will automatically deploy. Access your admin dashboard at:
```
https://admin-dashboard-production-xxxx.up.railway.app/admin/login
```

---

## 📊 Dashboard Features

### **Platform Overview:**

```
┌────────────────────────────────────────┐
│  💰 Total Revenue:    $18,750         │
│  👥 Total Users:      50               │
│  🤖 Active Bots:      127/150          │
│  📊 Total Trades:     2,500            │
│  💵 Total Balance:    $125,000         │
│  📈 Daily P&L:        +$1,250          │
└────────────────────────────────────────┘
```

### **User View:**

```
👤 john@example.com
   Balance: $2,140  |  Fees Paid: $45.50  |  Trades: 38  |  Bots: 2
   
   🤖 BTC Bot 1        ● Online
      Balance: $1,250  |  Daily P&L: +$45.30
      Trades: 23       |  Positions: 5
   
   🤖 ETH Bot 1        ● Online
      Balance: $890    |  Daily P&L: -$12.10
      Trades: 15       |  Positions: 3
```

---

## 🔐 Security

### **Change Default Password:**

```bash
# Set strong password via environment variable
export ADMIN_PASSWORD="your-very-secure-password-123!"
```

### **Generate Secret Key:**

```python
import secrets
print(secrets.token_hex(32))
# Use this as ADMIN_SECRET_KEY
```

### **Production Checklist:**
- [ ] Change default admin password
- [ ] Set strong secret key
- [ ] Use HTTPS only
- [ ] Restrict access by IP (optional)
- [ ] Enable 2FA (future feature)

---

## 🎨 Customization

### **Change Port:**

```bash
export ADMIN_DASHBOARD_PORT=9000
```

### **Change Refresh Interval:**

```bash
export DASHBOARD_REFRESH=60  # Refresh every 60 seconds
```

### **Add More Bots:**

Just add more environment variables:

```bash
export BOT_3_NAME=eth1
export BOT_3_URL=https://eth1.up.railway.app
export BOT_3_USER=mike@example.com
```

---

## 📈 Revenue Calculation

**Platform Fee:** 0.5% per trade

**Example:**
```
User trades $100 → Platform collects $0.50
User trades $1,000 → Platform collects $5.00

50 users × 50 trades/day × $50 avg × 0.5% = $625/day
= $18,750/month 💰
```

The dashboard automatically calculates:
- Total fees collected
- Fees per user
- Daily/monthly revenue projections

---

## 🐛 Troubleshooting

### **Can't Login:**
- Check `ADMIN_USERNAME` and `ADMIN_PASSWORD` env vars
- Default is `admin` / `changeme123`

### **Bots Show Offline:**
- Check bot API URLs are correct
- Ensure bots are running
- Check Railway internal networking

### **No Data Showing:**
- Verify bot APIs are accessible
- Check bot API endpoints: `/api/status`, `/api/metrics`, `/api/trades`
- Check Railway logs for errors

---

## 🔄 Difference from User Dashboard

| Feature | Admin Dashboard | User Dashboard |
|---------|----------------|----------------|
| **Purpose** | Monitor platform | Manage personal bots |
| **Access** | You only | All users |
| **Sees** | All users & bots | Only their bots |
| **Can Create Bots** | No | Yes |
| **Revenue Tracking** | Yes | No |
| **User Management** | Yes (future) | No |

---

## 🚀 Next Steps

1. ✅ **Admin Dashboard** (DONE!)
2. 🔨 **User Dashboard** (Next.js - in progress)
3. 🔨 **Management API** (FastAPI - coming soon)
4. 🔨 **Bot Manager** (Multi-user support - coming soon)

---

## 📞 Support

If you need help:
1. Check Railway logs
2. Verify environment variables
3. Test bot APIs directly
4. Check this guide

---

**Your admin dashboard is ready!** 🎉

Run it locally or deploy to Railway to start monitoring your platform!

