# ARCHITECTURE.md — `polybotshadow` (Rust Polymarket copytrader)

## 1. What this bot is

`polybotshadow` is a **Rust** Polymarket **copytrader**: it follows a target
trader and replicates their fills through the validated Polymarket **CLOB V2**
signing path. It is the Rust port of the original Python copytrader — same
behaviour, plus the requested **brownfox** strategy, now entirely in Rust.

The **entire repo is Rust** — there is no Python and no sniper in the tree. The
original Python copytrader exists only in git history (commit `823b1d4` and
earlier) if it is ever needed for reference; nothing here executes Python.

Two strategies, selected by env:

- **brownfox** (`USE_BROWNFOX_MODE=true`, the requested mode) — for each market
  the target enters, place **one fixed-SHARE buy** at their exact price, mark a
  **resting sell** at `buy + BROWNFOX_SELL_MARKUP` (+1 cent), and **force an exit**
  (retried market-sell) if the target sells below it. Holdings-driven and
  restart-safe: state is reconstructed from on-chain holdings + open orders, so a
  restart never double-buys or strands a position.
- **99c** (`USE_99C_MODE=true`, buy-and-hold) — like brownfox's *entry* but with
  **no sell/exit**: for each market the target enters, place **one fixed-SHARE
  buy** at the target's price and **hold to resolution**. For targets that buy
  near 1.0 (e.g. BTC 5-minute at 0.99) and hold. One buy per market, restart-safe;
  target sells are ignored; an unfilled buy is cancelled after
  `NINETYNINE_BUY_MAX_AGE_SECS` so it doesn't strand capital. Takes priority over
  brownfox when both are enabled.
- **99c-scanner** (`USE_99C_SCANNER_MODE=true`) — **self-driven, NO target trader**.
  Runs *instead of* the copytrader: discovers all open `<asset>-updown-5m` markets
  (Gamma, `NINETYNINE_ASSETS`), subscribes to their order books on the **CLOB market
  WebSocket** (`wss://ws-subscriptions-clob.polymarket.com/ws/market`, public), and
  when a token's **best ask reaches `SCANNER_TRIGGER_ASK` (0.99) and `< 1.0`** places
  **one GTC buy of `SCANNER_TRADE_SIZE_SHARES` (≥5) at `SCANNER_BUY_PRICE` (0.99)** and
  holds to resolution. Atomic per-token claim (`claim_scan`) = no double-buy; detached
  semaphore-bounded placement; fills via `order_matched` (never `/positions`); stale-
  cancel via `NINETYNINE_BUY_MAX_AGE_SECS`; restart-safe; terminal records pruned. Buys
  the same markets as 99c but order-book-driven so it doesn't miss the trader's entries.
  Two optional **DIRECTIONAL avoidance filters** gate each 0.99 buy by the side it would
  buy (Up vs Down), keyed off the coin's **current 5-minute candle** (the market window).
  (1) **liquidity levels** (`SCANNER_LIQUIDITY_FILTER`, `liquidity.rs`, "Liquidity Levels -
  Sonarlab" port): swing pivots (15L/5R, wick) pooled from **5m+15m+30m** — touching a
  **high** level (resistance) blocks **Up**, a **low** level (support) blocks **Down**.
  (2) **Fair Value Gaps** (`SCANNER_FVG_FILTER`, `fvg.rs`, "Fair Value Gap [LuxAlgo]" port):
  un-mitigated 3-candle gaps on **5m+15m** — inside a **bearish** FVG blocks **Up**, a
  **bullish** FVG blocks **Down**. Each filter returns a `FilterSignal{block_up, block_down,
  evaluable}`. **Entry:** the side being bought must be unblocked on **both** filters (and
  both must be evaluable — no Binance pair / no data ⇒ skip). **STOP:** a held position is
  dumped with a GTC sell at `SCANNER_EXIT_PRICE` (0.10) if **either** filter blocks the side
  we hold (held Up + high level/bearish FVG, or held Down + low level/bullish FVG). The live
  price (forming-candle wick) streams from the **Binance kline WebSocket**; the slower
  levels/zones refresh from REST every `SCANNER_LIQ_POLL_SECS`. **FVG hit** = the 5m candle
  opened INSIDE the gap OR opened outside and wicked IN; detected on CLOSED candles only
  (confirmed 3rd candle; the forming candle is excluded). FVG significance: fixed
  `SCANNER_FVG_THRESHOLD_PCT` (default 0 = every gap; raise to ignore tiny ones). Optional
  `SCANNER_FVG_AUTO` (OFF by default) instead scales the threshold to `SCANNER_FVG_AUTO_FACTOR`
  × the coin's avg candle range. A `SCANNER_FILTER_BLANKET` toggle makes any level/FVG hit block
  BOTH sides. A third **doji filter** (`SCANNER_DOJI_FILTER`, `doji.rs`, "Doji signals" port) blocks BOTH
  sides at ENTRY if the current 5m candle is a doji (`|open-close| ≤
  (high-low)·SCANNER_DOJI_PRECISION`, default 0.15, live on the forming candle); its STOP is
  **DIRECTIONAL** — dumps a held position only if the doji leans AGAINST it (held Up + bearish
  doji, held Down + bullish doji), else holds (don't dump a near-tie that may still pay 1.0).
  Coins with no Binance USDT pair (HYPE)
  aren't traded while a filter is on.
- **sell-with-target** (`USE_SELL_WITH_TARGET_MODE=true`) — **FAK market-buy** entry
  (one fixed-SHARE fill-and-kill buy per market — `orderType:"FAK"`, a limit
  `SELL_WITH_TARGET_BUY_AHEAD` above the best ask so it crosses + fills now and cancels
  any unfilled remainder; we hold exactly what filled, read from the order. A plain
  limit at the target's stale price rarely fills because the book has moved up). **No +1c resting sell**; on the target's
  **first sell** the exit style depends on their sell price vs **our average fill cost**
  (read lag-free from our `/data/trades`): (a) **sold AT/ABOVE our cost (profit) →
  chase the bid** — a **FAK** (fill-and-kill) SELL at ~1 tick under the best bid each
  round, capturing the good top-of-book prices (0.52, 0.51, …) instead of dumping deep,
  retried against a fresh bid, then a floor sell mops up any leftover; (b) **sold BELOW our cost (loss) → dump** —
  one GTC SELL at `SELL_WITH_TARGET_EXIT_PRICE` (default 0.10), a marketable limit that
  crosses the whole bid side (rejection-proof on a fast/crashing book) and rests any
  remainder at the floor. Runs on a **detached, semaphore-bounded worker** (never
  blocks other markets); size + fills come from CLOB order data (`order_matched` /
  `/data/trades`), **never the laggy /positions**; placements retried; restart-safe
  (EXITING resumes via the floor dump; DONE/ABORTED/STUCK terminal). A dropped WS sell
  is caught by the `/activity` safety-net poll (§2). State in `sellwithtarget.json`.
- **general copy replicator** (`USE_BROWNFOX_MODE=false`) — copy the target's
  buys/sells **proportionally** (`COPY_PERCENTAGE`): buys via optional weather
  price-chasing (or a GTC at their price), sells **capped at our holdings**
  (never over-sell), honouring `MAX_POSITIONS` (new-market cap) and a minimum
  target-trade value. Optional **auto-sell** resting limit and **stale-order
  sweep** loops run alongside it.

## 2. How it runs

```
target trader ──(monitor: WS or REST)──▶ TargetTrade channel ──▶ strategy ──▶ Executor (CLOB V2)
                                                                     │
                                                          store (JSON) + dashboard
```

- **monitor** (`copytrader/monitor.rs`) — `DATA_SOURCE=rest` polls the Data API
  `/activity` for the target; `DATA_SOURCE=ws` streams the activity WebSocket
  (`wss://ws-live-data.polymarket.com`) **and** runs the `/activity` poll alongside
  it as a fast safety net (the WS can drop a frame on a reconnect; `/activity` is the
  same trade feed, fresh in ~1-2s — NOT the laggy `/positions` snapshot — and catches
  it within a poll). Both dedup via the store and emit `TargetTrade`s.
- **strategy** — `brownfox.rs` (state machine) or `replicator.rs` (+ `weather.rs`
  chase, `autosell.rs` resting-limit maintainer, `stale.rs` BUY sweep).
- **store** (`copytrader/store.rs`) — JSON persistence: dedup set, brownfox
  per-market records, copied-market set, recent trade log. Survives restarts.
- **executor** (`clob/executor.rs`) — builds, signs (EIP-712 V2) and POSTs GTC
  orders with L2 HMAC auth; all writes are no-ops in `DRY_RUN=true`.
- **dashboard** (`copytrader/dashboard.rs`) — axum `GET /` (live HTML) +
  `GET /api/state` (positions, recent copy trades, portfolio value/PnL).

## 3. Verified Polymarket integration (CLOB **V2**) — the validated money layer

All values confirmed **against the installed `py-clob-client-v2` source** and live
probes. The Rust signing is **byte-for-byte validated** against it (see §6).

| Thing | Value |
|---|---|
| CLOB REST | `https://clob.polymarket.com` |
| Data API (positions / activity) | `https://data-api.polymarket.com` |
| Activity WebSocket (target's fills) | `wss://ws-live-data.polymarket.com` |
| Chain | Polygon, `chain_id = 137` |
| Signature type | `2` Gnosis-Safe (old wallets) · `3` POLY_1271 deposit wallet (NEW post-v2 accounts) — `PRIVATE_KEY` always signs, `FUNDER_ADDRESS` = maker |
| **CTF Exchange V2** (negRisk=false) | `0xE111180000d2663C0091e4f400237545B87B996B` |
| **Neg-Risk Exchange V2** (negRisk=true) | `0xe2222d279d744050d28e00520010520000310F59` |
| Order type | **GTC** limit, BUY=0 / SELL=1, 5-share min |
| Positions | `GET data-api/positions?user=<funder>&sizeThreshold=0.1` |

> ⚠️ The old **V1** exchanges (domain version `"1"`) are NOT used for V2 orders.
> V2 uses the addresses above + domain version `"2"`.

**EIP-712 order signing (V2 — `clob/signing.rs`):**
- Domain: `name="Polymarket CTF Exchange"`, `version="2"`, `chainId=137`,
  `verifyingContract` = exchange (neg-risk one for `negRisk=true` markets).
- **Order struct (11 fields, exact order/type):**
  `Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)`.
  V2 dropped `taker/expiration/nonce/feeRateBps` from the hash and added
  `timestamp` (ms), `metadata`, `builder` (both `bytes32(0)`).
- **Type 2 (Gnosis-Safe, old wallets):** `maker` = funder/Safe, `signer` = signing
  EOA; sign the plain EIP-712 order digest with the owner EOA → 65-byte sig.
- **Type 3 (POLY_1271 deposit wallet, NEW post-v2 accounts):** `maker = signer =
  funder` (the deposit wallet). The signature is a **Solady ERC-7739
  "TypedDataSign" nested** sig: hashStruct(Order) → wrap in TypedDataSign carrying
  the `DepositWallet` domain → seal under the CTF-Exchange domain separator →
  raw-ECDSA sign with the EOA → append suffix `appDomainSeparator‖contentsHash‖
  ORDER_TYPE_STRING‖uint16(len)`. Implemented in `sign_order_1271`; **validated
  byte-for-byte vs py-clob-client-v2** (`cargo test poly_1271…`). L1/L2 auth is
  unchanged (POLY_ADDRESS = EOA).
  > ⚠️ The 1271 wrap is for the **order signature ONLY**. **Do NOT** apply it (or
  > set POLY_ADDRESS = funder) to L1/L2 auth: the CLOB `/auth` endpoint does plain
  > ECDSA recovery — there is **no** ERC-1271 path for auth (py-clob-client-v2#76).
  > For **every** sig type the API key derives + binds to the **EOA** (plain
  > `ClobAuth`, POLY_ADDRESS = EOA); the deposit wallet appears only in the order
  > (maker = signer = funder). The CLOB does **not** require `order.signer ==
  > api-key address` — an EOA-bound key trading a sig-type-3 order is the supported
  > path (verified against the official `polymarket-client` / `py-sdk`). Optionally
  > skip derivation with pre-created `CLOB_API_*` creds (`auth::provided_creds`).
- BUY=0 / SELL=1; `timestamp` = ms; `salt = floor(rand()*now_ms)`.
- **Amounts:** `buy_amounts`/`sell_amounts` pick `ROUNDING_CONFIG` by tick;
  6-dp token decimals. `expiration` ("0" = GTC) goes in the POST body but NOT the
  signed hash. Both buy and sell paths are validated against py-clob-client-v2.

**Auth (`clob/auth.rs`):**
- **L1** (derive API key): EIP-712 `ClobAuth(...)`, domain `ClobAuthDomain`/`"1"`/
  137 → `POST /auth/api-key` (fallback `GET /auth/derive-api-key`).
- **L2** (per request): `sig = base64url(HMAC_SHA256(base64url_decode(secret),
  ts + METHOD + path + body))`. The HMAC body is the **exact compact JSON string
  POSTed** — build it once, sign and send the same bytes.

## 4. Architecture (single async `tokio` process)

```
src/
  main.rs                 load config; (selftest |) auth executor; spawn monitor
                          + strategy (brownfox | replicator + autosell/stale) + dashboard
  config.rs               env config + verified hosts/addresses; live-mode safety gate
  clob/
    signing.rs            EIP-712 V2 order hash+sign, amount/tick math, `selftest`
    auth.rs               L1 derive-api-key (ClobAuth) + L2 HMAC headers
    executor.rs           build→sign→POST GTC (BUY/SELL) + reads (book/tick/holdings/positions)
  copytrader/
    mod.rs                Side, TargetTrade
    monitor.rs            target feed: REST poll (run) or activity WS (run_ws)
    brownfox.rs           one-fixed-share-buy-per-market state machine (restart-safe)
    ninetynine.rs         99c buy-and-hold: one fixed-share buy/market, hold to resolution
    sellwithtarget.rs     brownfox entry; on target's sell, market-sell all (non-blocking worker)
    scanner.rs            99c order-book scanner: no target; CLOB market WS, buy 0.99 when ask hits it
    liquidity.rs          Binance 5m/15m/30m swing-level filter (Sonarlab port); skip 0.99 if price at a level
    fvg.rs                Binance 5m/15m fair-value-gap filter (LuxAlgo port); skip 0.99 if price in an FVG
    doji.rs               Binance 5m doji filter (non-directional); skip/stop if the 5m candle is a doji
    replicator.rs         proportional buy/sell copy
    weather.rs            price-chase buy (1c ahead) / sell (1 tick down)
    autosell.rs           resting standard-price sell maintainer
    stale.rs              cancel resting BUYs once a market runs near 1.0
    store.rs              JSON persistence (dedup, brownfox, copied, trade log)
    dashboard.rs          axum: GET / (HTML) + GET /api/state (positions/trades/PnL)
```

## 5. Config (env) — see [`.env.example`](.env.example)

Wallet/auth: `PRIVATE_KEY`, `FUNDER_ADDRESS`, `SIGNATURE_TYPE=2`. Safety:
`DRY_RUN`, `LIVE_SIGNING_VALIDATED`. Target/data: `TARGET_TRADER_ADDRESS`,
`DATA_SOURCE=rest|ws`, `COPY_POLL_MS`. brownfox: `USE_BROWNFOX_MODE`,
`BROWNFOX_TRADE_SIZE_SHARES`, `BROWNFOX_SELL_MARKUP`, `BROWNFOX_RECONCILE_MS`,
`BROWNFOX_MARKET_SELL_RETRIES`. Replicator: `COPY_PERCENTAGE`, `MAX_POSITIONS`,
`MIN_TARGET_TRADE_VALUE_USD`; weather `USE_WEATHER_MODE`, `WEATHER_*`; auto-sell
`AUTO_SELL_*`; stale `STALE_ORDER_*`. 99c: `USE_99C_MODE`,
`NINETYNINE_TRADE_SIZE_SHARES`, `NINETYNINE_RECONCILE_MS`,
`NINETYNINE_BUY_MAX_AGE_SECS`, `NINETYNINE_ASSETS` (only copy
`<asset>-updown-<dur>-*` markets; empty = all), `NINETYNINE_DURATIONS` (default
`5m,15m`; set `5m` to disable 15-minute). 99c-scanner: `USE_99C_SCANNER_MODE`,
`SCANNER_TRADE_SIZE_SHARES` (≥5), `SCANNER_BUY_PRICE` (0.99), `SCANNER_TRIGGER_ASK`
(0.99), `SCANNER_EXIT_PRICE` (0.10, liquidity-stop floor), `SCANNER_DISCOVERY_SECS`
(reuses `NINETYNINE_ASSETS`/`_BUY_MAX_AGE_SECS`/`_RECONCILE_MS`/`_MAX_CONCURRENT_BUYS`);
liquidity filter `SCANNER_LIQUIDITY_FILTER`, `SCANNER_LIQ_POLL_SECS` (REST levels/FVG cadence),
`BINANCE_REST_URL`, `BINANCE_WS_URL` (live price); FVG filter `SCANNER_FVG_FILTER`,
`SCANNER_FVG_AUTO`, `SCANNER_FVG_THRESHOLD_PCT`; blanket `SCANNER_FILTER_BLANKET`; doji
`SCANNER_DOJI_FILTER`, `SCANNER_DOJI_PRECISION`. Infra: `COPY_DATA_DIR`, `DASHBOARD_PORT`,
`LOG_LEVEL`.

## 6. Build / run

Toolchain: Rust **GNU** (`stable-x86_64-pc-windows-gnu`) + MinGW-w64 (`dlltool`)
on PATH — no Visual Studio. Deps are pure-Rust (native-tls, k256) so GNU links
self-contained. On Linux (AWS) the standard GNU toolchain works with no MinGW.

```powershell
cd F:\others\githubrepo\polybotshadow-1
# Windows build needs the mingw64 bin on PATH for dlltool:
$env:PATH = "C:\msys64\mingw64\bin;$env:PATH"
cargo build --release
# safe dry-run: detects the target, LOGS would-be orders, dashboard live at :8090
$env:DRY_RUN="true"; cargo run --release
# signing self-test vector (no network) — for the cross-check gate:
$env:PRIVATE_KEY="0x…"; cargo run --release -- selftest
# after the gate + funding/allowances are set:
$env:DRY_RUN="false"; $env:LIVE_SIGNING_VALIDATED="true"; cargo run --release
```

AWS deployment (EC2+systemd or Docker, incl. the region/geoblock caveat):
see [`deploy/AWS_DEPLOY.md`](deploy/AWS_DEPLOY.md) and
[`deploy/polybotshadow.service`](deploy/polybotshadow.service).

## 7. Status

- ✅ Full Rust copytrader: monitor (WS/REST), brownfox, replicator, weather,
  auto-sell, stale sweep, store, dashboard — compiles, tests pass.
- ✅ **Signing gate PASSED.** `cargo run -- selftest` produces an order hash +
  signature byte-identical to `py-clob-client-v2` `ExchangeOrderBuilderV2` for the
  same fixed inputs, on both exchanges. Sell amounts match too (`cargo test`).
- Live order placement stays gated behind `DRY_RUN=false` **and**
  `LIVE_SIGNING_VALIDATED=true` (config refuses to start live otherwise).
- The entire project is Rust. The original Python copytrader is **not** in the
  repo; it lives only in git history (commit `823b1d4` and earlier) if ever needed.

### Remaining before live (operational, not code)
1. Fund the proxy wallet with **pUSD** and set **ERC-20 allowance** to the V2
   exchange(s) + CTF token approvals (orders can't fill without it).
2. First live `POST /order` round-trip with your real key (tiny size).
3. Deploy in a **Polymarket-allowed region** — US datacenter IPs are geoblocked
   for order POSTs (reads work everywhere). See `deploy/AWS_DEPLOY.md`.

## 8. Hard rules

1. **Never** place an order while `DRY_RUN=true` — only log it. Live placement
   requires `LIVE_SIGNING_VALIDATED=true`.
2. **Everything is Rust.** There is no Python in the repo — the whole project is
   Rust; the original lives only in git history.
3. Sells are **capped at our actual holdings** — never over-sell.
4. **No double-buying:** brownfox reconstructs state from holdings + open orders
   and the store dedups; a restart must never re-enter a held market.
5. Use the **V2** exchanges + domain version `"2"` (§3). Never the V1 addresses.
6. The L2 HMAC body must be the **exact bytes POSTed** — build the compact JSON
   once, sign and send the same string.
7. Secrets (`PRIVATE_KEY`) never get committed — `.env` is gitignored.
8. **The Data-API `/positions` snapshot lags minutes — NEVER gate trading on it.**
   Detect the target's trades from the **WebSocket** (+ the `/activity` safety-net
   poll); track **our** position from the buy/sell **orders' matched fills**
   (`order_matched` / `open_orders` — CLOB order data, lag-free). `/positions`
   (`token_holdings*`) is allowed only for the dashboard/PnL and one-time startup
   recovery, never in the live detect/execute path. (active in `sellwithtarget` +
   `ninetynine`; `brownfox`/`replicator`/`autosell` still read `/positions` and
   should be converted before they're used live.)
