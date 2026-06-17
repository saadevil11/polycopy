# Copy Trading Bot Architecture

## System Overview

The Polymarket Copy Trading Bot is a real-time trading system that automatically replicates trades from a target trader using WebSocket connections and smart order execution strategies.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         COPY TRADING BOT SYSTEM                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                           1. INITIALIZATION                             │
└─────────────────────────────────────────────────────────────────────────┘

    start_bot.py
        ↓
    Load Environment Variables (.env / Railway)
        ↓
    Validate Config (PRIVATE_KEY, FUNDER_ADDRESS, TARGET_TRADER_ADDRESS)
        ↓
    Initialize Bot Components:
        ├─ Database (SQLite)
        ├─ PolymarketClient (Polygon blockchain)
        ├─ RiskManager
        ├─ TradeReplicator
        ├─ PositionMerger
        ├─ WebSocketTraderMonitor
        └─ Optional: API Server, Auto-Redeemer


┌─────────────────────────────────────────────────────────────────────────┐
│                      2. REAL-TIME MONITORING LAYER                      │
└─────────────────────────────────────────────────────────────────────────┘

    WebSocket Connection (wss://ws-live-data.polymarket.com)
        │
        ├─ Subscribe to: "activity/trades"
        ├─ Health Check: Ping/Pong every 20s
        └─ Auto-Reconnect on disconnect
        
    ↓ [Real-time Trade Stream]
    
    Filter Incoming Messages:
        ├─ Is from TARGET_TRADER_ADDRESS?
        ├─ Market Title Passes Filter? (15-min/hourly/etc.)
        └─ Not a Duplicate Trade? (check trade_id cache)
    
    ↓ [New Trade Detected]
    
    Parse Trade Data:
        ├─ Trade ID (transaction hash)
        ├─ Side (BUY/SELL)
        ├─ Token ID
        ├─ Price & Size
        ├─ Market Info (title, outcome)
        └─ Timestamp
    
    ↓ [Trigger Callbacks]


┌─────────────────────────────────────────────────────────────────────────┐
│                       3. TRADE PROCESSING FLOW                          │
└─────────────────────────────────────────────────────────────────────────┘

    _on_new_trade() Callback
        │
        ├─ Check Account Restriction Status
        ├─ Check Duplicate (in-memory cache + DB)
        └─ Save Target Trade to Database
        
    ↓ [Trade Replicator]
    
    PRE-TRADE CHECKS:
        ├─ Market Not in Excluded List?
        ├─ Target Trade Value ≥ MIN_TARGET_TRADE_VALUE?
        ├─ Market Liquidity ≥ MIN_MARKET_LIQUIDITY?
        ├─ Market Still Active?
        ├─ Risk Manager Approval?
        │   ├─ Position Count < MAX_POSITIONS?
        │   ├─ Daily P&L > -MAX_DAILY_LOSS?
        │   ├─ No Existing Position (same side)?
        │   └─ Market Not Ending Soon?
        └─ Account Has Sufficient Balance?
    
    ↓ [Calculate Position Size]
    
    Position Sizing:
        │
        FOR BUY:
            ├─ Base Amount = Target Amount × COPY_PERCENTAGE
            ├─ Apply MAX_POSITION_SIZE cap
            ├─ Apply MIN_POSITION_SIZE floor
            └─ Calculate Shares = Amount / Price
        
        FOR SELL:
            ├─ Shares = Target Shares × COPY_PERCENTAGE
            └─ Amount = Shares × Price
    
    ↓ [Execute Trade]


┌─────────────────────────────────────────────────────────────────────────┐
│                    4. ORDER EXECUTION STRATEGIES                        │
└─────────────────────────────────────────────────────────────────────────┘

    place_order_with_retry() [PolymarketClient]
        │
        ├─ Strategy Selection Based on USE_GTC_ONLY flag
        │
        ├─ STRATEGY 1: FAK → GTC Fallback (Default)
        │   │
        │   ├─ Try FAK (Fill-And-Kill) Order
        │   │   ├─ Fast execution (market order)
        │   │   ├─ Check fill status after 2 seconds
        │   │   └─ If filled ≥ 90% → SUCCESS
        │   │
        │   └─ Fallback to GTC if FAK fails
        │       ├─ Calculate limit price with slippage
        │       ├─ Place GTC order
        │       ├─ Poll for fill status (up to 10s)
        │       └─ If filled ≥ 50% → PARTIAL_FILL
        │
        └─ STRATEGY 2: GTC Only Mode
            │
            ├─ Fetch Current Market Price
            ├─ Calculate Limit Price:
            │   ├─ BUY: price × (1 + SLIPPAGE_TOLERANCE)
            │   └─ SELL: price × (1 - SLIPPAGE_TOLERANCE)
            ├─ Enforce 5-Share Minimum (if enabled)
            ├─ Place GTC Order
            └─ Wait for Fill (poll every 0.5s, max 10s)

    Result Types:
        ├─ SUCCESS: Order fully filled (≥90%)
        ├─ PARTIAL_FILL: Order partially filled (50-90%)
        ├─ INSUFFICIENT_LIQUIDITY: Not enough liquidity
        ├─ PRICE_MOVED: Price changed too much
        ├─ TIMEOUT: Order took too long
        ├─ ACCOUNT_RESTRICTED: Account in closed-only mode
        └─ FAILED: General failure

    ↓ [Update Trade Record]


┌─────────────────────────────────────────────────────────────────────────┐
│                         5. RISK MANAGEMENT                              │
└─────────────────────────────────────────────────────────────────────────┘

    Risk Manager (runs continuously)
        │
        ├─ Monitor Current Positions
        │   ├─ Cache positions (30s TTL)
        │   ├─ Track P&L per position
        │   └─ Calculate total exposure
        │
        ├─ Daily Counters
        │   ├─ Daily P&L
        │   ├─ Trades executed today
        │   └─ Reset at midnight
        │
        └─ Risk Limits
            ├─ MAX_POSITIONS: Stop if limit reached
            ├─ MAX_DAILY_LOSS: Stop if exceeded
            ├─ POSITIONS_AT_RISK: Warning if >70%
            └─ ACCOUNT_RESTRICTED: Stop all trading

    Emergency Actions:
        └─ emergency_close_all()
            ├─ Close all open positions
            └─ Place opposite side orders


┌─────────────────────────────────────────────────────────────────────────┐
│                      6. POSITION MANAGEMENT                             │
└─────────────────────────────────────────────────────────────────────────┘

    Position Merger (Optional)
        │
        ├─ Triggered when target trader merges positions
        │   └─ WebSocket detects "merge" action
        │
        ├─ Find Matching Positions (YES + NO)
        ├─ Calculate USDC Recovery
        ├─ Execute Merge Transaction
        └─ Save Result to Database

    Auto-Redeemer (Optional)
        │
        ├─ Runs every AUTO_REDEEM_INTERVAL_MINUTES
        ├─ Check for Resolved Markets
        ├─ Identify Winning Positions
        ├─ Redeem Winning Tokens for USDC
        └─ Track Claimed Markets


┌─────────────────────────────────────────────────────────────────────────┐
│                     7. DATA PERSISTENCE LAYER                           │
└─────────────────────────────────────────────────────────────────────────┘

    Database (SQLite)
        │
        ├─ target_trades: All trades from target trader
        ├─ copy_trades: All trades executed by bot
        ├─ bot_status: Current bot status & metrics
        └─ claimed_markets: Markets already redeemed

    Trade Statistics:
        ├─ Total trades copied
        ├─ Success rate
        ├─ Average execution time
        └─ Total P&L


┌─────────────────────────────────────────────────────────────────────────┐
│                      8. MONITORING & REPORTING                          │
└─────────────────────────────────────────────────────────────────────────┘

    Status Reporting Loop (every 5 minutes)
        │
        ├─ Log Current Status:
        │   ├─ Uptime
        │   ├─ WebSocket connection status
        │   ├─ Trades copied today
        │   ├─ Success rate
        │   ├─ Total positions
        │   ├─ Daily P&L
        │   └─ Recent errors
        │
        └─ Save Status to Database

    Optional API Server (if API_PORT set)
        │
        ├─ GET /health: Bot health check
        ├─ GET /status: Full bot status
        ├─ GET /trades: Recent trades
        └─ GET /positions: Current positions


┌─────────────────────────────────────────────────────────────────────────┐
│                     9. ERROR HANDLING & RECOVERY                        │
└─────────────────────────────────────────────────────────────────────────┘

    Error Types:
        │
        ├─ WebSocket Disconnection
        │   └─ Auto-reconnect with exponential backoff
        │
        ├─ Order Execution Failure
        │   └─ Retry with different strategy
        │
        ├─ Account Restriction (CRITICAL)
        │   ├─ Log critical error
        │   ├─ Set account_restricted flag
        │   └─ Stop all trading immediately
        │
        ├─ Risk Limit Exceeded
        │   └─ Skip new trades until reset
        │
        └─ General Errors
            ├─ Log to file
            ├─ Add to errors list
            └─ Continue monitoring


┌─────────────────────────────────────────────────────────────────────────┐
│                          10. KEY FEATURES                               │
└─────────────────────────────────────────────────────────────────────────┘

    ✅ Real-time Trade Detection (WebSocket)
    ✅ Smart Order Execution (FAK + GTC strategies)
    ✅ Comprehensive Risk Management
    ✅ Position Sizing with Limits
    ✅ Market Filtering (15-min/hourly markets)
    ✅ Duplicate Trade Prevention
    ✅ Account Restriction Detection
    ✅ Auto-Reconnect on Disconnect
    ✅ Position Merging
    ✅ Auto-Claiming Winnings
    ✅ Performance Optimizations:
        ├─ In-memory trade cache
        ├─ Position caching (30s TTL)
        ├─ Skip redundant price fetches
        └─ Parallel order execution
    ✅ Dry-Run Mode for Testing
    ✅ Detailed Logging & Statistics


┌─────────────────────────────────────────────────────────────────────────┐
│                       COMPONENT RELATIONSHIPS                           │
└─────────────────────────────────────────────────────────────────────────┘

    PolymarketCopyTradingBot (main orchestrator)
        │
        ├─ WebSocketTraderMonitor
        │   └─ MarketFilter
        │
        ├─ TradeReplicator
        │   ├─ PolymarketClient
        │   ├─ RiskManager
        │   └─ MarketFilter
        │
        ├─ RiskManager
        │   └─ PolymarketClient
        │
        ├─ PositionMerger
        │   └─ PolymarketClient
        │
        ├─ PolymarketRedeemer (optional)
        │   └─ PolymarketClient
        │
        ├─ Database
        │
        └─ BotAPI (optional)


┌─────────────────────────────────────────────────────────────────────────┐
│                     CONFIGURATION PARAMETERS                            │
└─────────────────────────────────────────────────────────────────────────┘

    Environment Variables:
        ├─ PRIVATE_KEY: Your wallet private key
        ├─ FUNDER_ADDRESS: Your wallet address
        ├─ TARGET_TRADER_ADDRESS: Trader to copy
        ├─ COPY_PERCENTAGE: % of target position size (default: 0.1)
        ├─ MAX_POSITION_SIZE_USD: Max per position (default: 1000)
        ├─ MIN_POSITION_SIZE_USD: Min per position (default: 1)
        ├─ MAX_DAILY_LOSS_USD: Daily loss limit (default: 1000)
        ├─ MAX_POSITIONS: Max concurrent positions (default: 10000)
        ├─ USE_GTC_ONLY: Use GTC-only strategy (default: false)
        ├─ PRICE_SLIPPAGE_TOLERANCE: Slippage tolerance (default: 0.14)
        ├─ DRY_RUN: Test mode without real trades (default: false)
        ├─ ALLOW_15MIN_MARKETS: Allow 15-min markets (default: false)
        ├─ ALLOW_HOURLY_MARKETS: Allow hourly markets (default: true)
        └─ AUTO_REDEEM_ENABLED: Auto-claim winnings (default: false)


┌─────────────────────────────────────────────────────────────────────────┐
│                         EXECUTION FLOW SUMMARY                          │
└─────────────────────────────────────────────────────────────────────────┘

    1. Bot starts → Initialize components
    2. WebSocket connects → Subscribe to trades
    3. Target trader makes trade → Detected in <1 second
    4. Market filter applied → Check if market is eligible
    5. Pre-trade checks → Risk validation
    6. Position sizing → Calculate copy amount
    7. Order execution → FAK or GTC strategy
    8. Result handling → Update DB & stats
    9. Risk monitoring → Continuous safety checks
    10. Status reporting → Log every 5 minutes

    [Continuous Loop: Steps 3-10]
```

## Technical Stack

- **Language**: Python 3.9+
- **Async Framework**: asyncio
- **WebSocket**: websockets library
- **Blockchain**: py-clob-client (Polymarket SDK)
- **Database**: SQLite
- **Logging**: loguru
- **Network**: Polygon (Layer 2 Ethereum)

## Key Algorithms

### Trade Detection
- Uses WebSocket for real-time monitoring (sub-second latency)
- Deduplication via transaction hash caching
- Market filtering before processing

### Position Sizing
```
BUY:  copy_amount = min(target_amount × copy_percentage, max_position_size)
      copy_shares = copy_amount / price

SELL: copy_shares = target_shares × copy_percentage
      copy_amount = copy_shares × price
```

### Order Execution Priority
1. **FAK (Fill-And-Kill)**: Fast market order
2. **GTC Fallback**: Limit order with price tolerance
3. **Retry Logic**: Up to N attempts with delay

### Risk Scoring
```
Risk Score = (positions_at_risk / total_positions) × 100%
Stop Trading if:
  - Daily P&L ≤ -MAX_DAILY_LOSS
  - Risk Score > 70%
  - Account Restricted
```

## Performance Characteristics

- **Trade Detection Latency**: <1 second (WebSocket)
- **Order Execution Time**: 2-10 seconds
- **Database Operations**: <10ms (SQLite)
- **Position Cache TTL**: 30 seconds
- **Status Reporting**: Every 5 minutes
- **Risk Checks**: Every 1 minute

## Security Features

- Private keys stored in environment variables
- Account restriction detection
- Daily loss limits
- Position size limits
- Market liquidity checks
- Duplicate trade prevention

## Scalability

- In-memory caching for speed
- Async I/O for concurrent operations
- Connection pooling for API calls
- Auto-reconnect for reliability
- Health monitoring for WebSocket

