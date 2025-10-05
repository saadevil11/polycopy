# 🤖 Polymarket Copy Trading Bot

A sophisticated automated trading bot that copies trades from successful Polymarket traders in real-time.

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## ✨ Features

- 🎯 **Real-time Trade Copying** - Instantly replicate trades from target traders via WebSocket
- 💰 **Smart Position Sizing** - Configurable copy percentage with min/max limits
- 🛡️ **Risk Management** - Built-in position limits, daily loss caps, and price filters
- 🔄 **Merge & Redeem** - Automatically copy merge/redeem actions from target traders
- 🎨 **Beautiful GUI** - Easy-to-use interface for configuration and monitoring
- 📊 **Market Filters** - Focus on specific markets (e.g., Bitcoin, Ethereum, Solana)
- 🧪 **Dry Run Mode** - Test strategies without risking real money
- 📈 **Trade Analytics** - Track performance with detailed logs and database

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Polymarket account with USDC balance
- Target trader address to copy

### Installation

```bash
# Clone the repository
git clone https://github.com/shadow-112/polybotshadow.git
cd polybotshadow

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp docs/env.example .env
nano .env  # Edit with your settings
```

### Configuration

Edit `.env` with your settings:

```bash
# Wallet Configuration
PRIVATE_KEY=your_private_key_here
FUNDER_ADDRESS=0xYourPolymarketWalletAddress  # From Polymarket profile!
TARGET_TRADER_ADDRESS=0xTargetTraderAddress
SIGNATURE_TYPE=2  # Most users need this (Proxy wallet)

# Trading Settings
COPY_PERCENTAGE=0.1  # Copy 10% of target's trade size
MAX_POSITION_SIZE_USD=1000
MIN_POSITION_SIZE_USD=10
MAX_DAILY_LOSS_USD=500
MAX_POSITIONS=10

# Market Filters (comma-separated)
MARKET_FILTERS=Bitcoin Up or Down on,Ethereum Up or Down on,Solana Up or Down on

# Bot Settings
DRY_RUN=false  # Set to true for testing
COPY_MERGE_ACTIONS=true
COPY_REDEEM_ACTIONS=true
```

### Run the Bot

#### Option 1: GUI (Recommended)
```bash
python gui.py
```

#### Option 2: Command Line
```bash
python start_bot.py
```

## 📁 Project Structure

```
polybotshadow/
├── src/
│   ├── core/              # Core bot logic
│   │   ├── config.py      # Configuration management
│   │   ├── models.py      # Data models
│   │   ├── database.py    # SQLite database
│   │   ├── polymarket_client.py  # API wrapper
│   │   ├── copy_trading_bot.py   # Main bot orchestrator
│   │   ├── trade_replicator.py   # Trade execution
│   │   ├── risk_manager.py       # Risk controls
│   │   ├── market_filters.py     # Market filtering
│   │   └── merge_positions.py    # Merge/redeem logic
│   ├── monitors/          # Trade monitoring
│   │   ├── websocket_trader_monitor.py  # Real-time WebSocket
│   │   ├── trader_monitor.py            # API polling
│   │   └── alternative_trader_monitor.py # Backup monitor
│   └── utils/             # Test utilities
├── tools/                 # Utility scripts
│   ├── check_balance.py   # Check wallet balance
│   ├── manual_trade_test.py  # Manual trade testing
│   ├── dashboard.py       # Web dashboard
│   └── setup_allowances.py   # Setup USDC allowances
├── docs/                  # Documentation
│   ├── env.example        # Example configuration
│   ├── QUICKSTART.md      # Quick start guide
│   ├── WALLET_SETUP_GUIDE.md  # Wallet setup
│   └── AWS_DEPLOYMENT_GUIDE.md  # AWS deployment
├── gui.py                 # GUI application
├── start_bot.py           # CLI entry point
└── requirements.txt       # Python dependencies
```

## 🛠️ Tools

### Check Balance
```bash
python tools/check_balance.py
```

### Manual Trade Test
```bash
python tools/manual_trade_test.py
```

### Web Dashboard
```bash
python tools/dashboard.py
# Open http://localhost:5000
```

## 📖 Documentation

- [Quick Start Guide](docs/QUICKSTART.md)
- [Wallet Setup Guide](docs/WALLET_SETUP_GUIDE.md)
- [AWS Deployment Guide](docs/AWS_DEPLOYMENT_GUIDE.md)
- [Project Overview](docs/PROJECT_OVERVIEW.md)

## ⚙️ Key Features Explained

### Market Filters
Focus on specific markets by pattern matching:
```bash
MARKET_FILTERS=Bitcoin Up or Down on,Ethereum Up or Down on
```

### Risk Management
- **Max Positions**: Limit concurrent open positions
- **Daily Loss Cap**: Stop trading after reaching loss limit
- **Position Sizing**: Min/max limits per trade
- **Price Filters**: Avoid extreme markets (>0.99 or <0.01)

### Merge & Redeem
- **Merge**: Combine YES + NO positions → USDC
- **Redeem**: Claim winnings from resolved markets
- Automatically copies when target trader performs these actions

### Dry Run Mode
Test your strategy without risking real money:
```bash
DRY_RUN=true
```

## 🔒 Security

- ✅ Private keys stored in `.env` (never committed)
- ✅ `.gitignore` configured to exclude sensitive files
- ✅ Signature type 2 for Polymarket proxy wallets
- ✅ All transactions signed locally

## 🐛 Troubleshooting

### "Invalid signature" error
- Ensure `FUNDER_ADDRESS` is your **Polymarket wallet address** (from profile), not MetaMask
- Set `SIGNATURE_TYPE=2` for proxy wallets

### Bot not copying trades
- Check market filters are configured correctly
- Verify WebSocket connection in logs
- Ensure target trader is active

### Balance issues
- Run `python tools/check_balance.py`
- Ensure USDC is on Polygon network
- Polymarket handles gas fees automatically

## 🚀 AWS Deployment

Deploy to AWS EC2 for 24/7 operation:

```bash
# See detailed guide
cat docs/AWS_DEPLOYMENT_GUIDE.md
```

**Cost**: ~$8-20/month (after free tier)

## 📊 Performance Monitoring

### GUI
- Real-time trade feed
- Position tracking
- P&L monitoring
- Activity logs

### Logs
```bash
tail -f polymarket_bot.log
```

### Database
```bash
sqlite3 data/trades.db "SELECT * FROM copy_trades ORDER BY execution_timestamp DESC LIMIT 10;"
```

## 🤝 Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## ⚠️ Disclaimer

This bot is for educational purposes. Trading involves risk. Only trade with funds you can afford to lose. The authors are not responsible for any financial losses.

## 📝 License

MIT License - see LICENSE file for details

## 🙏 Acknowledgments

- [Polymarket](https://polymarket.com/) - Prediction market platform
- [py-clob-client](https://github.com/Polymarket/py-clob-client) - Python API client

## 📧 Support

For issues and questions:
- Open an issue on GitHub
- Check documentation in `docs/`

---

**Made with ❤️ for the Polymarket community**
