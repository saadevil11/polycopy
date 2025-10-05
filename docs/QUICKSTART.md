# 🚀 Quick Start Guide

Get your Polymarket copy trading bot running in 5 minutes!

## 📋 Prerequisites

- Python 3.9 or higher
- A Polymarket account with funds
- MetaMask or hardware wallet (for EOA users)

## 🛠️ Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy the example configuration
cp env.example .env

# Edit the configuration
nano .env
```

**Required settings:**
```bash
PRIVATE_KEY=your_private_key_without_0x_prefix
FUNDER_ADDRESS=0xYourWalletAddress
TARGET_TRADER_ADDRESS=0xTraderYouWantToCopy
```

### 3. Set Up Token Allowances (MetaMask/Hardware Wallet Users Only)

If you're using MetaMask or a hardware wallet, run this once:

```bash
python setup_allowances.py
```

*Skip this step if you're using an email/Magic wallet.*

### 4. Test in Dry Run Mode

```bash
# Test without real trades
DRY_RUN=true python start_bot.py
```

### 5. Start Live Trading

```bash
# Start live trading
python start_bot.py
```

## 📊 Monitor Your Bot

### Web Dashboard
```bash
# Start the dashboard (in another terminal)
python dashboard.py
```
Then open: http://localhost:8080

### Logs
```bash
# Watch live logs
tail -f polymarket_bot.log
```

## ⚙️ Configuration Options

Edit your `.env` file to customize:

```bash
# Position sizing
COPY_PERCENTAGE=0.5              # Copy 50% of target's position size
MAX_POSITION_SIZE_USD=500        # Max $500 per position
MIN_POSITION_SIZE_USD=20         # Min $20 per position

# Risk management
MAX_DAILY_LOSS_USD=200          # Stop if daily loss exceeds $200
MAX_POSITIONS=5                 # Maximum 5 open positions

# Timing
TRADE_DELAY_SECONDS=10          # 10 second delay before copying
MONITORING_INTERVAL_SECONDS=15  # Check for new trades every 15 seconds
```

## 🆘 Troubleshooting

**Bot not copying trades?**
- Check if target trader has recent activity
- Verify target trader address is correct
- Ensure you have sufficient balance

**Authentication errors?**
- Verify private key is correct (no 0x prefix)
- Check funder address matches your wallet
- For MetaMask users: run `python setup_allowances.py`

**Trades failing?**
- Check account balance
- Verify market is active and liquid
- Review risk management settings

## 🛡️ Safety Tips

- **Start with small amounts** in dry run mode
- **Set conservative risk limits** initially
- **Monitor the dashboard** regularly
- **Keep private keys secure** - never share them
- **Test thoroughly** before large deployments

## 📞 Support

- Check the logs: `tail -f polymarket_bot.log`
- Review the full README.md for detailed documentation
- Enable debug logging: `LOG_LEVEL=DEBUG`

## 🎯 Example Configuration

Here's a conservative setup for beginners:

```bash
# Conservative settings for beginners
COPY_PERCENTAGE=0.25            # Copy 25% of target's trades
MAX_POSITION_SIZE_USD=100       # Max $100 per trade
MIN_POSITION_SIZE_USD=10        # Min $10 per trade
MAX_DAILY_LOSS_USD=50          # Stop at $50 daily loss
MAX_POSITIONS=3                # Max 3 positions
TRADE_DELAY_SECONDS=30         # 30 second delay
DRY_RUN=true                   # Start in simulation mode
```

Happy trading! 🎉
