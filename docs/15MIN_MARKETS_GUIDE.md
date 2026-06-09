# 15-Minute Markets Configuration Guide

## Overview
By default, the bot **blocks 15-minute interval markets** because they are:
- ⚡ Extremely fast-moving (high volatility)
- 💸 High slippage risk
- 🎲 More gambling than strategic trading

## Enable 15-Minute Markets

### Option 1: Railway Variables
Add this environment variable:
```
ALLOW_15MIN_MARKETS=true
```

### Option 2: Local .env File
Add to your `.env`:
```bash
ALLOW_15MIN_MARKETS=true
```

## Default Behavior (Recommended)
```bash
ALLOW_15MIN_MARKETS=false  # (default)
```

## What Markets Are Blocked?

### ❌ Blocked by Default (15-min intervals):
- "Bitcoin Up or Down - 12:30AM-12:45AM"
- "BTC Price 1:45PM-2:00PM"
- "Will Bitcoin go up 5:15AM-5:30AM"

### ✅ Always Allowed:
- "Bitcoin Up or Down - October 19, 5PM ET" (hourly)
- "BTC Price on October 20" (daily)
- "Will Bitcoin reach $100k in 2024" (long-term)

## Detection Pattern
The bot identifies 15-minute markets by looking for time ranges like:
- `12:30AM-12:45AM`
- `1:45PM-2:00PM`
- `5:15AM-5:30AM`

## ⚠️ Warning
**If you enable 15-minute markets, expect:**
1. **Higher slippage** (±$0.05 or more)
2. **More failed orders** (market moves too fast)
3. **Lower success rate** (price changes between detection and execution)

## Recommendation
**Keep this OFF** unless:
- Your target trader is very successful on 15-min markets
- You're okay with higher risk/slippage
- You're testing strategies

---

**Default: OFF (safer)**
**To enable: `ALLOW_15MIN_MARKETS=true`**

