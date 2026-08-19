# polybotshadow

**A free, open-source Polymarket copytrading bot — written in Rust.**

Follow any Polymarket trader and mirror their fills automatically, in real time.
No subscription, no hosted middleman, no custody of your keys: you run the binary,
your key stays on your machine.

![Rust](https://img.shields.io/badge/rust-2021-orange.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

> ⚠️ **Trading involves real financial risk.** Start with `DRY_RUN=true`, read the
> [Disclaimer](#-disclaimer), and never trade money you cannot afford to lose.

---

## Why this exists

Copytrading bots for Polymarket are typically sold as monthly subscriptions, run on
someone else's server, and expect you to hand over credentials. This one is MIT
licensed and runs entirely on your own machine.

What makes it different under the hood:

| | |
|---|---|
| 🔐 **Signing validated against the reference client** | The EIP-712 CLOB v2 order signing is checked against Polymarket's own `py-clob-client-v2` — identical order hash *and* signature for identical inputs. There's a `selftest` command plus unit tests that assert it. |
| 🆕 **Supports the new deposit wallets** | Handles signature type 2 (Gnosis-Safe proxy) **and** type 3 (`POLY_1271` deposit wallets used by post-v2 accounts) with the full ERC-7739 nested-signature wrap. |
| ⚡ **No laggy position snapshots** | The Data-API `/positions` endpoint can lag by minutes. This bot detects the target from the activity WebSocket (with a REST safety-net poll) and tracks *your* position from actual order fills — so it never acts on stale data. |
| 🔁 **Restart-safe by design** | State is reconstructed from on-chain holdings and open orders. Restarting mid-session never double-buys and never strands a position. |
| 🧠 **Smart retries** | Transient failures (network, 5xx, rate limits, unfilled fill-and-kill) retry with backoff; genuine ones (insufficient balance, closed market) fail fast. Retries check whether the previous attempt actually landed, so they never duplicate an order. |
| 🚦 **Non-blocking** | Every copied trade runs on its own bounded worker, so one slow market never blocks the others. |
| 🦀 **Single binary** | Rust + Tokio. No Python runtime, no `node_modules`, no Docker required (though a Dockerfile is included). |

---

## ✨ Strategies

**The copy replicator is the default and the mode this project is built around.**
It runs out of the box — no flag required. The other strategies are advanced /
experimental extras: they're off by default, each one *replaces* the replicator when
enabled, and they're included because they're useful to study and to run deliberately.

### The default — proportional copy replicator
Mirrors the target's buys and sells at a configurable fraction of their size
(`COPY_PERCENTAGE`). Sells are always capped at your actual holdings, so it can
never over-sell. Supports a per-market cap, a minimum trade size, and a
"only copy markets dated on/after X" filter so you don't join a run mid-stream.

Order type is selectable:

- `gtc` — a limit order at the target's exact price (purest mirror; rests if the book has moved).
- `fak` — fill-and-kill at the live quote: crosses and fills *now*, remainder cancelled.

---

### Advanced / experimental modes *(off by default)*

Enable at most one, and only on purpose. If several are set, precedence is
`99c-scanner > 99c > sell-with-target > brownfox > replicator`.

#### Fixed-size entry with managed exit
One fixed-share buy per market at the target's price, a resting sell at
`buy + markup`, and a forced exit if the target sells below it.

#### Buy-and-hold
One fixed-share buy per market, held to resolution — target sells are ignored.
Built for traders who buy heavy favourites near $0.99 and hold.

#### Exit-with-target
Fill-and-kill market entry; when the target first sells, the exit adapts to your
real average cost (read from your own fills): chase the bid when you are in profit,
cross the book when you are not.

#### Self-driven order-book scanner — no target trader
Instead of copying anyone, it watches the order books of short-duration up/down
markets over the CLOB market WebSocket and acts when the best ask hits a
configured trigger. Includes optional technical entry filters computed live from
Binance candles:

- **Liquidity levels** — swing-pivot support/resistance pooled from 5m/15m/30m.
- **Fair value gaps** — un-mitigated three-candle imbalances (5m/15m, configurable).
- **Doji detection** — skips entries on indecision candles.
- **Minimum-move gate** — requires the favourite to have actually moved before entering.

Each filter is directional: a resistance level blocks *Up* entries and support blocks
*Down*, so a signal on one side doesn't needlessly cost you the other.

---

## 🚀 Quick start

### Prerequisites

- **Rust** (stable). On Windows use the GNU toolchain with MinGW-w64 (`dlltool` on PATH);
  on Linux the standard toolchain works as-is.
- A **Polymarket account** funded with USDC, with allowances set for the exchange contracts.
- The **address of the trader** you want to copy.

### 1. Build

```bash
git clone https://github.com/saadevil11/polycopy.git
cd polycopy
cargo build --release
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` — at minimum:

```bash
PRIVATE_KEY=0x...                # your signer key - stays local, never committed
FUNDER_ADDRESS=0x...             # your Polymarket wallet address
SIGNATURE_TYPE=2                 # 2 = Safe proxy | 3 = new deposit wallet
TARGET_TRADER_ADDRESS=0x...      # the trader you want to copy
DRY_RUN=true                     # start here: logs orders, places nothing
```

Leave every `USE_*_MODE` flag at `false` (the shipped default) and the bot runs the
copy replicator.


### 3. Dry run — do this first

```bash
DRY_RUN=true ./target/release/polybotshadow
```

It detects the target's trades live and logs exactly what it *would* place, without
sending anything. The dashboard is at **http://localhost:8090**.

### 4. Verify signing

```bash
cargo run --release -- selftest
```

Prints the order hash and signature for a fixed reference vector, so you can confirm
they match the reference client before risking anything.

### 5. Go live

Live placement is **double-gated** and refuses to start otherwise:

```bash
DRY_RUN=false LIVE_SIGNING_VALIDATED=true ./target/release/polybotshadow
```

Start with a tiny size and confirm one real fill before scaling up.

---

## ⚙️ Configuration

Everything is environment-driven — [`.env.example`](.env.example) documents every
option inline. The most useful knobs:

```bash
DATA_SOURCE=rest                 # rest = polling (works on cloud IPs) | ws = push (needs allowed IP)
COPY_PERCENTAGE=0.1              # copy 10% of the target's size
COPY_ORDER_TYPE=gtc              # gtc = mirror the price | fak = fill now at the live quote
MIN_TARGET_TRADE_VALUE_USD=4     # ignore dust trades
COPY_MAX_CONCURRENT=8            # parallel copied trades
DASHBOARD_PORT=8090
```

---

## 📊 Dashboard

A built-in web dashboard (axum) on `http://localhost:8090`:

- current positions and portfolio value
- recent copied trades
- realised / unrealised PnL

`GET /api/state` returns the same data as JSON if you want to build on it.

---

## 📁 Project structure

```
src/
├── main.rs               # config load, auth, task spawning
├── config.rs             # env config + live-mode safety gate
├── clob/                 # the money layer (Polymarket CLOB v2)
│   ├── signing.rs        #   EIP-712 order hashing + signing (types 2 & 3), amount math
│   ├── auth.rs           #   L1 API-key derivation, L2 HMAC request signing
│   └── executor.rs       #   build -> sign -> POST orders; book/holdings/fill reads
└── copytrader/
    ├── monitor.rs        # target feed: activity WebSocket + REST safety net
    ├── replicator.rs     # proportional copy
    ├── brownfox.rs       # fixed-size entry, managed exit
    ├── ninetynine.rs     # buy-and-hold
    ├── sellwithtarget.rs # exit alongside the target
    ├── scanner.rs        # self-driven order-book strategy
    ├── liquidity.rs      # swing-level filter
    ├── fvg.rs            # fair-value-gap filter
    ├── doji.rs           # doji filter
    ├── store.rs          # JSON persistence (dedup, state, trade log)
    └── dashboard.rs      # web UI + JSON API
```

---

## ☁️ Deployment

[`deploy/AWS_DEPLOY.md`](deploy/AWS_DEPLOY.md) covers EC2 + systemd and Docker.

> **Region matters.** Polymarket geoblocks order submission from US datacenter IPs.
> Reads work anywhere, but to place orders you must run from a permitted region.

---

## 🔒 Security

- Your private key lives in `.env` (gitignored) and is used **only** locally to sign
  orders — it is never transmitted anywhere.
- Live trading requires **two** independent flags (`DRY_RUN=false` *and*
  `LIVE_SIGNING_VALIDATED=true`).
- Sells are hard-capped at your actual holdings.
- No telemetry, no phone-home, no third-party server in the trade path.

**Never commit your `.env`.** It is gitignored by default — keep it that way.

---

## 🤝 Contributing

Issues and pull requests are welcome. When reporting a bug, please include your
(redacted) config, the strategy mode, and the relevant log lines.

---

## ⚠️ Disclaimer

This software is provided for **educational and research purposes**. It is not
financial advice. Prediction markets are risky and you can lose your entire stake.
Automated trading can amplify losses through bugs, network failures, or unexpected
market behaviour. You are solely responsible for anything you run and for any
losses you incur, and for checking that using this software is legal in your
jurisdiction. The authors accept no liability. Provided "as is", without warranty
of any kind.

---

## 📝 License

MIT — see [LICENSE](LICENSE). Free to use, modify, and distribute.

## 🙏 Acknowledgments

- [Polymarket](https://polymarket.com/) — the prediction market platform
- [py-clob-client](https://github.com/Polymarket/py-clob-client) — the reference client this implementation's signing is validated against
