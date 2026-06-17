# 🤖 polybotshadow — Polymarket Copytrader (Rust)

A Rust Polymarket **copytrader**: it follows a target trader and replicates their
fills in real time through the validated Polymarket **CLOB V2** signing path.

This is the Rust port of the original Python copytrader. **The entire project is
Rust** — there is no Python in the repo; the original lives only in git history
(commit `823b1d4` and earlier) if ever needed for reference.

![Rust](https://img.shields.io/badge/rust-2021-orange.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## ✨ What it does

- 🎯 **Real-time trade copying** — detect a target's fills via the activity
  WebSocket **or** Data-API REST polling (your choice: `DATA_SOURCE=ws|rest`).
- 🦊 **brownfox mode** (`USE_BROWNFOX_MODE=true`) — for each market the target
  enters, place **one fixed-SHARE buy** at their exact price, mark a **resting
  sell** at `buy + markup` (+1 cent), and **force an exit** if they sell below it.
  Holdings-driven + restart-safe (state reconstructed from on-chain holdings +
  open orders, so a restart never double-buys).
- 💰 **General copy replicator** (`USE_BROWNFOX_MODE=false`) — copy buys/sells
  **proportionally** (`COPY_PERCENTAGE`), sells **capped at our holdings** (never
  over-sell), with `MAX_POSITIONS` and a minimum target-trade value. Optional
  **weather** price-chasing, **auto-sell** resting limit, and **stale-order sweep**.
- 📊 **Dashboard** — live positions, recent copy trades, and portfolio value/PnL
  at `http://localhost:8090`.
- 🧪 **Dry-run mode** — `DRY_RUN=true` detects trades and logs would-be orders
  while placing **nothing**.

## 🚀 Quick start

### Prerequisites
- Rust (stable). On **Windows**, the GNU toolchain + MinGW-w64 (`dlltool` on PATH).
  On **Linux** (incl. AWS), the standard GNU toolchain — no extra C toolchain.
- A Polymarket account (proxy wallet) with pUSD, and the target trader's address.

### Build & run (dry-run)
```powershell
cd F:\others\githubrepo\polybotshadow-1
# Windows only: put the mingw64 bin on PATH for dlltool
$env:PATH = "C:\msys64\mingw64\bin;$env:PATH"
cargo build --release

# copy the example env and edit it (target address, mode, sizes…)
copy .env.example .env

# safe dry-run: detects the target, LOGS would-be orders, dashboard at :8090
$env:DRY_RUN="true"; cargo run --release
```
On Linux:
```bash
cargo build --release
cp .env.example .env   # edit it
DRY_RUN=true ./target/release/polybotshadow
```

### Go live (gated)
Live order placement is refused unless **both** flags are set, and only after the
signing self-test matches `py-clob-client-v2`:
```powershell
# 1) signing self-test vector (no network)
$env:PRIVATE_KEY="0x…"; cargo run --release -- selftest
# 2) after the gate + funding/allowances are set:
$env:DRY_RUN="false"; $env:LIVE_SIGNING_VALIDATED="true"; cargo run --release
```

## ⚙️ Configuration

All configuration is via environment variables — see [`.env.example`](.env.example)
for the full annotated list. The essentials:

```bash
PRIVATE_KEY=0x…                  # signer EOA (NEVER committed)
FUNDER_ADDRESS=0x…               # Polymarket proxy wallet (the maker)
SIGNATURE_TYPE=2                 # Gnosis-Safe proxy (most users)
TARGET_TRADER_ADDRESS=0x…        # the trader to copy
DATA_SOURCE=rest                 # rest (AWS-safe) | ws (faster, needs allowed IP)
DRY_RUN=true                     # true = place nothing, just log
USE_BROWNFOX_MODE=true           # the requested strategy (else general copy)
BROWNFOX_TRADE_SIZE_SHARES=50    # fixed shares per market
```

## 📁 Project structure

```
polybotshadow-1/
├── src/
│   ├── main.rs              # load config; spawn monitor + strategy + dashboard
│   ├── config.rs           # env config + verified hosts/addresses; live-mode gate
│   ├── clob/               # the validated money layer (CLOB V2)
│   │   ├── signing.rs      # EIP-712 V2 order hash+sign, amount/tick math, selftest
│   │   ├── auth.rs         # L1 derive-api-key + L2 HMAC headers
│   │   └── executor.rs     # build→sign→POST GTC (BUY/SELL) + reads
│   └── copytrader/
│       ├── monitor.rs      # target feed: REST poll | activity WebSocket
│       ├── brownfox.rs     # one-fixed-share-buy-per-market state machine
│       ├── replicator.rs   # proportional buy/sell copy
│       ├── weather.rs      # price-chase buy/sell
│       ├── autosell.rs     # resting standard-price sell maintainer
│       ├── stale.rs        # cancel resting BUYs near 1.0
│       ├── store.rs        # JSON persistence (dedup, brownfox, copied, trades)
│       └── dashboard.rs    # axum: GET / (HTML) + GET /api/state (JSON)
├── deploy/                 # AWS_DEPLOY.md + polybotshadow.service (systemd)
├── Dockerfile              # Linux build + slim runtime
└── ARCHITECTURE.md               # full design + verified CLOB V2 integration notes
```

## ☁️ Deployment

See [`deploy/AWS_DEPLOY.md`](deploy/AWS_DEPLOY.md) for EC2 + systemd and Docker
(ECS/Fargate) instructions, including the **region geoblock caveat**: Polymarket
blocks order POSTs from US datacenter IPs — reads work everywhere, but you must
deploy in a Polymarket-allowed region (or proxy) for live orders to fill.

## 🔒 Security

- ✅ Private keys live in `.env` (gitignored), never committed.
- ✅ Live trading is double-gated: `DRY_RUN=false` **and**
  `LIVE_SIGNING_VALIDATED=true`, after the signing self-test matches the reference.
- ✅ Signature type 2 for Polymarket proxy wallets; all signing is local.
- ✅ Sells are capped at actual holdings — the bot never over-sells.

## ⚠️ Disclaimer

For educational purposes. Trading involves risk; only trade with funds you can
afford to lose. The authors are not responsible for any financial losses.

## 📝 License

MIT — see [LICENSE](LICENSE).

## 🙏 Acknowledgments

- [Polymarket](https://polymarket.com/) — prediction market platform
- [py-clob-client](https://github.com/Polymarket/py-clob-client) — reference client the Rust signing is validated against
