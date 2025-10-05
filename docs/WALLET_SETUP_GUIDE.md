# 🚨 CRITICAL WALLET SETUP GUIDE

## The #1 Cause of "Invalid Signature" Errors

**FUNDER_ADDRESS must be your POLYMARKET wallet address, NOT your MetaMask address!**

### How to Find Your Polymarket Wallet Address:

1. **Go to Polymarket.com**
2. **Log in to your account**
3. **Look at the top right corner** - click on your profile
4. **Copy the wallet address shown there**

This address is usually **different** from your MetaMask address!

### Example:
```
❌ WRONG: Using your MetaMask address
FUNDER_ADDRESS=0x1234567890abcdef1234567890abcdef12345678

✅ CORRECT: Using your Polymarket wallet address (from profile)
FUNDER_ADDRESS=0xabcdef1234567890abcdef1234567890abcdef12
```

### Why This Happens:
- Polymarket uses **proxy wallets** for most users
- Your MetaMask signs transactions, but Polymarket creates a separate wallet address
- The API expects the Polymarket wallet address, not your MetaMask address
- This is the most common cause of authentication failures

### Quick Fix:
1. Go to Polymarket.com
2. Click your profile (top right)
3. Copy the wallet address shown
4. Update your `.env` file:
   ```
   FUNDER_ADDRESS=your_polymarket_wallet_address_here
   ```

### Other Common Issues:

#### Private Key Format:
- **MetaMask users**: Export private key from MetaMask (without 0x prefix)
- **Magic/Gmail users**: Use the private key from Magic wallet

#### Signature Type:
```bash
SIGNATURE_TYPE=0  # For MetaMask/EOA wallets (direct)
SIGNATURE_TYPE=1  # For Magic/Gmail wallets
SIGNATURE_TYPE=2  # For Polymarket Proxy wallets (MOST COMMON)
```

**Most Polymarket users should use `SIGNATURE_TYPE=2`** since Polymarket creates proxy wallets!

### Still Getting Errors?
1. Double-check you're using the Polymarket wallet address (not MetaMask)
2. Verify your private key is correct
3. Make sure SIGNATURE_TYPE matches your wallet type
4. Try the manual trade test: `python manual_trade_test.py`

---

**This single fix resolves 90% of authentication issues!**
