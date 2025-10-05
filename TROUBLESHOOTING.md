# 🔧 Troubleshooting Guide

## Bot Not Detecting Trades

If your bot is running but not copying trades, follow these steps:

### 1. Verify Target Trader Address

**The most common issue!** Make sure you're using the correct address format.

```bash
# ✅ CORRECT: Use the Polymarket proxy wallet address
TARGET_TRADER_ADDRESS=0x1234...  # From trader's Polymarket profile

# ❌ WRONG: Don't use MetaMask address or ENS name
```

**How to find the correct address:**
1. Go to Polymarket.com
2. Search for the trader you want to copy
3. Click on their profile
4. Copy the address from the URL: `polymarket.com/profile/0x...`
5. Use that FULL address (starts with 0x, 42 characters long)

### 2. Check Market Filters

Your bot only copies trades that match your market filters.

**In Railway Variables:**
```bash
# If this is set, bot ONLY copies these markets
MARKET_FILTERS=Bitcoin Up or Down on,Ethereum Up or Down on

# To copy ALL markets, set it to empty:
MARKET_FILTERS=
```

**Test without filters:**
1. Go to Railway → Variables
2. Set `MARKET_FILTERS` to empty (blank value)
3. Redeploy
4. Check if trades are detected now

### 3. Verify WebSocket Connection

Check Railway logs for:

```bash
# ✅ Good - WebSocket is connected
🔌 Connecting to WebSocket...
✅ WebSocket connected
Subscribed to activity/trades

# ❌ Bad - Connection issues
WebSocket connection error: ...
WebSocket disconnected, reconnecting...
```

### 4. Check Target Trader Activity

**Is the target trader actually trading?**

1. Go to their Polymarket profile
2. Check "Activity" tab
3. Verify they've made trades recently (within last hour)
4. If no recent trades, wait for them to trade

### 5. Verify Environment Variables

In Railway dashboard → Variables, ensure these are set:

```bash
# Required
TARGET_TRADER_ADDRESS=0x...  # Full address, 42 characters
PRIVATE_KEY=0x...            # Your private key
FUNDER_ADDRESS=0x...         # Your Polymarket wallet address
SIGNATURE_TYPE=2

# Trading settings
COPY_PERCENTAGE=0.1
MAX_POSITION_SIZE_USD=1000
MIN_POSITION_SIZE_USD=10

# Filters (empty = copy all markets)
MARKET_FILTERS=

# Mode
DRY_RUN=false
LOG_LEVEL=INFO
```

### 6. Enable Debug Logging

For more detailed logs:

1. Go to Railway → Variables
2. Set `LOG_LEVEL=DEBUG`
3. Redeploy
4. Check logs for detailed WebSocket messages

You should see:
```bash
📊 Status: Connected=True, Trades seen=0
Checking for new trades...
Received WebSocket message: {...}
```

### 7. Test with Known Active Trader

Try copying a very active trader first to confirm the bot works:

**Popular active traders on Polymarket:**
- Check the leaderboard: polymarket.com/leaderboard
- Pick someone with recent trades (last hour)
- Update `TARGET_TRADER_ADDRESS` to their address

### 8. Check Risk Manager Limits

Your bot might be blocking trades due to risk limits:

```bash
# In Railway Variables, temporarily relax limits for testing:
MAX_POSITIONS=50              # Increase from 10
MAX_DAILY_LOSS_USD=10000     # Increase from 500
MAX_POSITION_SIZE_USD=5000   # Increase from 1000
```

### 9. Verify Balance

Ensure you have enough USDC:

**Check logs for:**
```bash
💰 USDC Balance: X.XX USDC
```

If balance is 0 or too low:
1. Deposit USDC to your Polymarket wallet
2. Ensure it's on Polygon network
3. Wait a few minutes for confirmation

### 10. WebSocket Message Format

The bot looks for trades with this structure:
```json
{
  "topic": "activity",
  "type": "trades",
  "payload": {
    "proxyWallet": "0x...",  // Must match TARGET_TRADER_ADDRESS
    "title": "Market name",
    "side": "BUY" or "SELL",
    "size": "100",
    "price": "0.65"
  }
}
```

---

## Common Error Messages

### "❌ Missing required environment variables"

**Fix:** Add all required variables in Railway → Variables tab

### "WebSocket connection error"

**Fix:** 
- Check internet connection
- Verify Railway service is running
- Check Polymarket WebSocket status: https://polymarket.com

### "Market does not match enabled filters"

**Fix:** 
- Update `MARKET_FILTERS` to include the market
- Or set `MARKET_FILTERS=` (empty) to copy all markets

### "Maximum positions reached"

**Fix:** Increase `MAX_POSITIONS` in Railway Variables

### "Insufficient balance"

**Fix:** Deposit more USDC to your Polymarket wallet

### "Invalid signature"

**Fix:**
- Verify `FUNDER_ADDRESS` is your Polymarket wallet (not MetaMask)
- Set `SIGNATURE_TYPE=2`
- Check `PRIVATE_KEY` is correct

---

## Debug Checklist

Run through this checklist:

- [ ] Target trader address is correct (from Polymarket profile URL)
- [ ] Target trader has made trades in the last hour
- [ ] WebSocket shows "connected" in logs
- [ ] Market filters are configured correctly (or empty)
- [ ] All environment variables are set in Railway
- [ ] USDC balance is sufficient
- [ ] `LOG_LEVEL=DEBUG` for detailed logs
- [ ] Risk limits are not too restrictive
- [ ] Bot is in correct mode (`DRY_RUN=false` for live)

---

## Still Not Working?

### Enable Maximum Logging

Set these in Railway Variables:
```bash
LOG_LEVEL=DEBUG
DRY_RUN=true  # Test in dry run first
MARKET_FILTERS=  # Empty = copy all markets
```

### Check Railway Logs

Look for these specific messages:

**Good signs:**
```
✅ WebSocket connected
🎯 Trade from target trader detected!
Processing new WebSocket trade
```

**Bad signs:**
```
WebSocket connection error
Failed to check for new trades
No trades detected from target
```

### Test Locally First

Run the bot locally to see detailed output:

```bash
# On your computer
cd polybotshadow
source venv/bin/activate
python start_bot.py
```

Watch for any errors or warnings.

### Contact Support

If still not working:
1. Check Railway logs (last 100 lines)
2. Verify target trader's recent activity on Polymarket
3. Confirm all environment variables are set
4. Open an issue on GitHub with logs

---

## Pro Tips

1. **Start with DRY_RUN=true** to test without risking money
2. **Use DEBUG logging** initially to see everything
3. **Test with active traders** from the leaderboard
4. **Start with no filters** (MARKET_FILTERS=) to catch all trades
5. **Monitor Railway logs** for first 24 hours
6. **Use small COPY_PERCENTAGE** (0.01 = 1%) for testing

---

## Quick Test

Want to quickly test if your bot can detect trades?

1. Set `LOG_LEVEL=DEBUG`
2. Set `MARKET_FILTERS=` (empty)
3. Set `DRY_RUN=true`
4. Pick a very active trader from leaderboard
5. Update `TARGET_TRADER_ADDRESS`
6. Redeploy and watch logs

You should see trade detection within minutes if the trader is active!
