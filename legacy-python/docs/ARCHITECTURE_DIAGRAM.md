# Multi-Bot Architecture

## 🏗️ System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        RAILWAY PROJECT                           │
│                                                                   │
│  ┌──────────────────────────┐  ┌──────────────────────────┐    │
│  │   Service 1: "ticket"     │  │  Service 2: "ticket-1"    │    │
│  │                           │  │                            │    │
│  │  🤖 Copy Trading Bot      │  │  🤖 Copy Trading Bot       │    │
│  │                           │  │                            │    │
│  │  Wallet: Primary          │  │  Wallet: Secondary         │    │
│  │  Target: Trader A         │  │  Target: Trader B          │    │
│  │                           │  │                            │    │
│  │  ┌─────────────────┐     │  │  ┌─────────────────┐       │    │
│  │  │  Volume: /app/data│    │  │  │  Volume: /app/data│      │    │
│  │  │  trades.db        │    │  │  │  trades.db        │      │    │
│  │  └─────────────────┘     │  │  └─────────────────┘       │    │
│  └──────────────────────────┘  └──────────────────────────┘    │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Railway CLI
                              │ (downloads databases)
                              │
                              ▼
        ┌──────────────────────────────────────────┐
        │      YOUR LOCAL COMPUTER                  │
        │                                            │
        │  ┌────────────────────────────────────┐  │
        │  │   Multi-Bot Dashboard              │  │
        │  │   (Python Flask App)               │  │
        │  │                                    │  │
        │  │   📊 Combined Portfolio View       │  │
        │  │   📈 Individual Bot Stats          │  │
        │  │   💰 Real-time P&L                 │  │
        │  │   📝 Activity Feed                 │  │
        │  │                                    │  │
        │  │   http://localhost:8080            │  │
        │  └────────────────────────────────────┘  │
        │                                            │
        └──────────────────────────────────────────┘
                              │
                              │ Browser
                              │
                              ▼
                    ┌─────────────────┐
                    │  Your Browser    │
                    │  (Chrome/Firefox)│
                    │                  │
                    │  Dashboard UI    │
                    └─────────────────┘
```

## 🔄 Data Flow

```
1. Bot 1 on Railway → Executes trades → Saves to trades.db (Volume 1)
2. Bot 2 on Railway → Executes trades → Saves to trades.db (Volume 2)
                              ↓
3. Dashboard uses Railway CLI to download both databases
                              ↓
4. Dashboard reads data from both databases
                              ↓
5. Dashboard displays combined view in browser
                              ↓
6. Auto-refreshes every 30 seconds
```

## 📦 Configuration Flow

```
multi_bot_config.json
        │
        ├─→ Bot 1 Config
        │   ├─ Service Name: "ticket"
        │   ├─ Color: Blue (#007bff)
        │   └─ Description: "Following Trader A"
        │
        └─→ Bot 2 Config
            ├─ Service Name: "ticket-1"
            ├─ Color: Green (#28a745)
            └─ Description: "Following Trader B"
```

## 🎨 Dashboard Layout

```
┌────────────────────────────────────────────────────────────┐
│                    🤖 Multi-Bot Dashboard                   │
│                   Last updated: 2025-10-09 12:00:00         │
│                        [🔄 Refresh Now]                     │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│              📊 Combined Portfolio Summary                  │
│                                                              │
│  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐           │
│  │   12   │  │ $5,432 │  │ +$234  │  │ $50,000│           │
│  │Positions│  │ Value  │  │  P&L   │  │ Volume │           │
│  └────────┘  └────────┘  └────────┘  └────────┘           │
└────────────────────────────────────────────────────────────┘

┌─────────────────────────┐  ┌─────────────────────────┐
│ Bot 1 - Primary Wallet  │  │ Bot 2 - Secondary Wallet│
│ 🟢 Connected            │  │ 🟢 Connected            │
├─────────────────────────┤  ├─────────────────────────┤
│ Today: 5 trades         │  │ Today: 3 trades         │
│ Success: 45 (90%)       │  │ Success: 28 (85%)       │
│ Positions: 7            │  │ Positions: 5            │
│ P&L: +$125.50           │  │ P&L: +$108.75           │
│                         │  │                         │
│ Top 5 Positions:        │  │ Top 5 Positions:        │
│ ├─ Bitcoin Up...        │  │ ├─ Ethereum Up...       │
│ ├─ Solana Down...       │  │ ├─ Trump vs...          │
│ └─ ...                  │  │ └─ ...                  │
└─────────────────────────┘  └─────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│           📝 Recent Activity (All Bots)                     │
│                                                              │
│ [Bot 1] Bitcoin Up or Down...    🟢 BUY  $50.00  12:34 PM │
│ [Bot 2] Ethereum Up or Down...   🔴 SELL $30.00  12:33 PM │
│ [Bot 1] Solana Up or Down...     🟢 BUY  $25.00  12:30 PM │
└────────────────────────────────────────────────────────────┘
```

## 🔐 Security Model

```
┌─────────────────────────────────────────────────────────┐
│                     SECURITY LAYERS                      │
│                                                           │
│  1. Railway (Cloud)                                       │
│     ├─ Private keys stored as env variables              │
│     ├─ Each service isolated                             │
│     └─ Volumes are service-specific                      │
│                                                           │
│  2. Local Dashboard                                       │
│     ├─ Runs on localhost only (127.0.0.1)               │
│     ├─ No external access by default                     │
│     └─ Read-only access to databases                     │
│                                                           │
│  3. Railway CLI                                           │
│     ├─ Authenticated with your Railway account           │
│     ├─ Downloads data securely                           │
│     └─ Temporary local database copies                   │
└─────────────────────────────────────────────────────────┘
```

## 📊 Component Responsibilities

```
┌─────────────────────┐
│  Railway Service 1   │
├─────────────────────┤
│ ✓ Execute trades    │
│ ✓ Monitor Trader A  │
│ ✓ Save to database  │
│ ✓ Auto-redeem       │
│ ✓ Merge positions   │
└─────────────────────┘

┌─────────────────────┐
│  Railway Service 2   │
├─────────────────────┤
│ ✓ Execute trades    │
│ ✓ Monitor Trader B  │
│ ✓ Save to database  │
│ ✓ Auto-redeem       │
│ ✓ Merge positions   │
└─────────────────────┘

┌─────────────────────┐
│   Local Dashboard    │
├─────────────────────┤
│ ✓ Fetch databases   │
│ ✓ Combine data      │
│ ✓ Calculate P&L     │
│ ✓ Display UI        │
│ ✓ Auto-refresh      │
│ ✗ No trading        │
└─────────────────────┘
```

## 🎯 Deployment Topology

```
                     INTERNET
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
   Polymarket    Railway Cloud    Your Computer
   (Markets)     (2 Bot Services) (Dashboard)
        │               │               │
        │               │               │
        └───────────────┴───────────────┘
              Trading Ecosystem
```

## 🔄 Update Cycle

```
Time: 0s
├─ Bot 1: Sees new trade from Trader A
├─ Bot 1: Executes copy trade
├─ Bot 1: Saves to database
│
Time: 5s  
├─ Bot 2: Sees new trade from Trader B
├─ Bot 2: Executes copy trade
├─ Bot 2: Saves to database
│
Time: 30s (Dashboard refresh)
├─ Dashboard: Downloads Bot 1 database
├─ Dashboard: Downloads Bot 2 database
├─ Dashboard: Combines data
├─ Dashboard: Updates browser UI
│
Time: 60s (Next refresh)
└─ Cycle repeats...
```

## 📈 Scaling Options

```
Current Setup:
2 Bots → 1 Dashboard

Easy Scaling:
┌─────────────────────────────────┐
│  Add more bots by:               │
│  1. Deploy new Railway service   │
│  2. Add to multi_bot_config.json│
│  3. Dashboard auto-detects       │
└─────────────────────────────────┘

Future: 3, 4, 5+ bots supported!
```

## 🎨 Color Coding System

```
Bot 1: Blue (#007bff)
├─ Easy to spot in activity feed
└─ Distinct from other bots

Bot 2: Green (#28a745)
├─ Different strategy or trader
└─ Visually distinct

Bot 3+: Purple, Orange, Red...
└─ Each bot gets unique color
```

## 💡 Tips for Organization

```
Name by Strategy:
├─ "🐋 Whale Follower"
├─ "📈 Trend Trader"
└─ "⚡ Quick Scalper"

Name by Trader:
├─ "Trader ABC123"
├─ "Trader XYZ789"
└─ "Top Performer #1"

Name by Wallet:
├─ "Primary Wallet ($5k)"
├─ "Secondary Wallet ($2k)"
└─ "Test Wallet ($500)"
```

---

**This architecture allows you to:**
- ✅ Run multiple bots independently on Railway
- ✅ Monitor all bots from one dashboard
- ✅ Keep each bot's data isolated
- ✅ Scale to any number of bots
- ✅ Maintain security and privacy

