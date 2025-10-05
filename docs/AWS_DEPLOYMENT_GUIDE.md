# AWS Deployment Guide for Polymarket Copy Trading Bot

This guide will help you deploy your Polymarket copy trading bot on AWS EC2.

---

## 📋 Prerequisites

- AWS Account
- SSH key pair for EC2 access
- Your `.env` file configured locally

---

## 🚀 Step 1: Launch EC2 Instance

### 1.1 Create EC2 Instance

1. Go to AWS Console → EC2 → Launch Instance
2. **Name:** `polymarket-copy-bot`
3. **AMI:** Ubuntu Server 22.04 LTS (Free tier eligible)
4. **Instance Type:** `t2.micro` (Free tier) or `t3.small` (recommended for better performance)
5. **Key pair:** Create new or use existing
6. **Network Settings:**
   - Allow SSH (port 22) from your IP
   - Allow HTTP (port 8080) if you want to access the dashboard remotely
7. **Storage:** 8 GB (default is fine)
8. Click **Launch Instance**

### 1.2 Connect to Your Instance

```bash
# Download your key pair (e.g., polymarket-bot-key.pem)
chmod 400 polymarket-bot-key.pem

# Connect via SSH
ssh -i polymarket-bot-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

---

## 🔧 Step 2: Install Dependencies on EC2

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3-pip

# Install git
sudo apt install -y git

# Install system dependencies
sudo apt install -y build-essential libssl-dev libffi-dev python3-dev
```

---

## 📦 Step 3: Upload Bot Files to EC2

### Option A: Using SCP (from your local machine)

```bash
# Create a tarball of your bot
cd /Users/saadshafqat/Desktop
tar -czf ticket.tar.gz ticket/

# Upload to EC2
scp -i polymarket-bot-key.pem ticket.tar.gz ubuntu@YOUR_EC2_PUBLIC_IP:~/

# On EC2, extract files
ssh -i polymarket-bot-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
tar -xzf ticket.tar.gz
cd ticket
```

### Option B: Using Git (if you have a private repo)

```bash
# On EC2
git clone https://github.com/yourusername/polymarket-bot.git
cd polymarket-bot
```

---

## ⚙️ Step 4: Configure the Bot on EC2

```bash
# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file
nano .env
```

**Copy your local `.env` configuration:**
```bash
# Required settings
PRIVATE_KEY=your_private_key_here
FUNDER_ADDRESS=0xYourPolymarketWalletAddress
TARGET_TRADER_ADDRESS=0xTargetTraderAddress
SIGNATURE_TYPE=2

# Trading settings
COPY_PERCENTAGE=0.1
MAX_POSITION_SIZE_USD=1000
MIN_POSITION_SIZE_USD=10
MAX_DAILY_LOSS_USD=500
MAX_POSITIONS=10
TRADE_DELAY_SECONDS=0

# Market filters
MARKET_FILTERS=Ethereum Up or Down on,Bitcoin Up or Down on

# Bot settings
LOG_LEVEL=INFO
DRY_RUN=false
COPY_MERGE_ACTIONS=true
COPY_REDEEM_ACTIONS=true
```

Save with `Ctrl+X`, then `Y`, then `Enter`.

---

## 🏃 Step 5: Run the Bot

### Test Run (Foreground)

```bash
# Activate virtual environment
source venv/bin/activate

# Run bot
python start_bot.py
```

Press `Ctrl+C` to stop.

---

## 🔄 Step 6: Run Bot as Background Service (Recommended)

### 6.1 Create Systemd Service

```bash
sudo nano /etc/systemd/system/polymarket-bot.service
```

**Paste this configuration:**
```ini
[Unit]
Description=Polymarket Copy Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/ticket
Environment="PATH=/home/ubuntu/ticket/venv/bin"
ExecStart=/home/ubuntu/ticket/venv/bin/python /home/ubuntu/ticket/start_bot.py
Restart=always
RestartSec=10

# Logging
StandardOutput=append:/home/ubuntu/ticket/bot.log
StandardError=append:/home/ubuntu/ticket/bot.error.log

[Install]
WantedBy=multi-user.target
```

Save with `Ctrl+X`, then `Y`, then `Enter`.

### 6.2 Enable and Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service (start on boot)
sudo systemctl enable polymarket-bot

# Start service
sudo systemctl start polymarket-bot

# Check status
sudo systemctl status polymarket-bot
```

### 6.3 Manage the Service

```bash
# View logs (live)
tail -f ~/ticket/bot.log

# View error logs
tail -f ~/ticket/bot.error.log

# Stop bot
sudo systemctl stop polymarket-bot

# Restart bot
sudo systemctl restart polymarket-bot

# Check if running
sudo systemctl status polymarket-bot
```

---

## 📊 Step 7: Access Dashboard (Optional)

### 7.1 Run Dashboard

```bash
# In a separate SSH session
cd ~/ticket
source venv/bin/activate
python dashboard.py
```

### 7.2 Access from Browser

1. **Option A - SSH Tunnel (Secure):**
   ```bash
   # On your local machine
   ssh -i polymarket-bot-key.pem -L 8080:localhost:8080 ubuntu@YOUR_EC2_PUBLIC_IP
   ```
   Then open: http://localhost:8080

2. **Option B - Public Access (Less Secure):**
   - Add inbound rule in EC2 Security Group for port 8080
   - Access: http://YOUR_EC2_PUBLIC_IP:8080

---

## 🔐 Step 8: Security Best Practices

### 8.1 Secure Your .env File

```bash
# Set proper permissions
chmod 600 ~/.env
```

### 8.2 Enable AWS CloudWatch Monitoring

1. Go to EC2 → Monitoring
2. Enable detailed monitoring
3. Set up alarms for:
   - High CPU usage
   - Low disk space
   - Instance status checks

### 8.3 Regular Backups

```bash
# Backup trades database
cp ~/ticket/trades.db ~/ticket/trades.db.backup

# Or set up automatic daily backups
crontab -e
# Add this line:
0 0 * * * cp ~/ticket/trades.db ~/ticket/trades.db.$(date +\%Y\%m\%d)
```

---

## 📈 Step 9: Monitor Your Bot

### View Live Logs

```bash
# Bot logs
tail -f ~/ticket/bot.log

# Polymarket bot logs
tail -f ~/ticket/polymarket_bot.log

# Error logs
tail -f ~/ticket/bot.error.log
```

### Check Bot Status

```bash
# Service status
sudo systemctl status polymarket-bot

# Check if process is running
ps aux | grep start_bot.py

# Check resource usage
top
htop  # if installed (sudo apt install htop)
```

### View Database

```bash
# Install sqlite3
sudo apt install sqlite3

# Query trades
sqlite3 ~/ticket/trades.db "SELECT * FROM copy_trades ORDER BY execution_timestamp DESC LIMIT 10;"
```

---

## 🔧 Troubleshooting

### Bot Not Starting

```bash
# Check logs
sudo journalctl -u polymarket-bot -n 50

# Check permissions
ls -la ~/ticket/

# Verify .env file
cat ~/ticket/.env
```

### Connection Issues

```bash
# Test internet connection
ping polymarket.com

# Check DNS
nslookup polymarket.com

# Test WebSocket
curl -I https://ws-live-data.polymarket.com
```

### Out of Memory

```bash
# Check memory usage
free -h

# If needed, upgrade to larger instance (t3.small or t3.medium)
```

---

## 💰 Cost Estimation

### Free Tier (First 12 months)
- **t2.micro:** 750 hours/month FREE
- **Storage:** 30 GB FREE
- **Data Transfer:** 15 GB/month FREE

### After Free Tier
- **t2.micro:** ~$8-10/month
- **t3.small:** ~$15-20/month (recommended)
- **t3.medium:** ~$30-40/month (for heavy trading)

---

## 🚀 Quick Commands Reference

```bash
# Start bot
sudo systemctl start polymarket-bot

# Stop bot
sudo systemctl stop polymarket-bot

# Restart bot
sudo systemctl restart polymarket-bot

# View logs
tail -f ~/ticket/bot.log

# Check status
sudo systemctl status polymarket-bot

# Update bot code
cd ~/ticket
git pull  # if using git
sudo systemctl restart polymarket-bot
```

---

## 📝 Maintenance Checklist

### Daily
- [ ] Check bot is running: `sudo systemctl status polymarket-bot`
- [ ] Review logs for errors: `tail -f ~/ticket/bot.log`
- [ ] Check balance and positions

### Weekly
- [ ] Review trading performance
- [ ] Check disk space: `df -h`
- [ ] Backup database: `cp trades.db trades.db.backup`

### Monthly
- [ ] Update system: `sudo apt update && sudo apt upgrade`
- [ ] Review AWS costs
- [ ] Rotate old log files

---

## 🆘 Support

If you encounter issues:
1. Check logs: `tail -f ~/ticket/bot.log`
2. Check service status: `sudo systemctl status polymarket-bot`
3. Verify .env configuration
4. Check AWS instance is running
5. Verify internet connectivity

---

## 🎉 You're Done!

Your Polymarket copy trading bot is now running 24/7 on AWS! 🚀

Monitor it regularly and adjust your filters and risk settings as needed.
