# ARCHITECTURE.md — `latebutfast` (Rust Polymarket near-resolution sniper)

## 1. What this bot is

`latebutfast` is a **Rust** Polymarket trading bot that watches many fast-resolving
markets at once and, **the instant a market's book shows a best ask of 0.999 or
1.00**, rests a **GTC buy limit at 0.999** on that outcome token. The edge: those
outcomes are near-certain and **resolve very soon**, so capital is never stuck
long, and a fill that settles at $1.00 is a small, fast, high-probability gain.
Name = enter **late** (only once price is at 0.999/1.00) but act **fast** (Rust,
one event-driven instance, lowest monitoring latency).

It is **NOT** a copy-trader. It reuses the *integration* of the sibling Python
copy-trader (`../polybotshadow-1`, `py-clob-client-v2`) — order signing/auth,
position tracking, CLOB **V2** — but **none** of the copy-trading logic.

**Strategy (exact):**
- For every monitored outcome token, track the **best ask** + the **live tick**.
- Fire when ALL hold: `best_ask ≥ 0.999` (book shows 0.999/1.00 on that side);
  the **live tick can place 0.999 EXACTLY** (never 0.99 — see §3); the market is
  **eligible** (see kickoff gate); and we have not already fired / don't already
  hold/rest on that token.
- **Kickoff gate (sports):** crypto 5m is always eligible; any non-crypto market
  (World Cup games, other sports) is eligible **only once the game has STARTED**
  (live or ended) — `now ≥ gameStartTime`. **Never pre-kickoff.** Unknown start
  time ⇒ not eligible. Gate is applied BEFORE the one-shot claim so a pre-game
  token becomes eligible the moment it kicks off. (`OutcomeToken::started`.)
- Then rest a **GTC BUY @ `BUY_PRICE`** (0.999), size per market kind:
  `CRYPTO_ORDER_SIZE_SHARES` for crypto 5m, `WORLDCUP_ORDER_SIZE_SHARES` for World
  Cup (both default to `ORDER_SIZE_SHARES`, ≥ 5).
- **Dedup (like the copytrader):** (1) in-memory one-shot claim per token;
  (2) skip if we already hold a position on that token; (3) skip if the exchange
  already shows a resting order on it (`GET /data/orders?asset_id=`). (2)+(3)
  survive restarts → never double-place.
- **Stale-cancel (crypto only):** a background sweep cancels **crypto** resting
  orders older than `CRYPTO_ORDER_MAX_AGE_MINS` (default 45; 0 = off) — an
  unfilled 0.999 buy on a long-resolved 5m market is dead capital. Sports/World
  Cup orders are **exempt** (their games run long). `DELETE /order` live; status
  → Cancelled in dry-run.
- Never sell — the outcome resolves to $1.00; just redeem. One **instance
  monitors ALL markets concurrently** — no per-market process.

## 2. Markets monitored (single instance, all at once)

1. **Crypto 5-minute up/down** for **BTC, ETH, SOL, XRP, DOGE, HYPE, BNB**.
   - slug: `<asset>-updown-5m-<slot>`, `slot` = floor(now/300)*300 (300 s/market).
   - Gamma `GET /markets?slug=…`; outcomes `["Up","Down"]`, `clobTokenIds[0]=Up`,
     `[1]=Down`, tick 0.01, `negRisk=false`. Discovery pre-registers the current
     slot + next 2 per asset (= 7 assets × 2 sides × 3 slots = 42 tokens).
2. **FIFA World Cup** game markets — **Moneyline, Spreads, Totals, Both-Teams-To-Score**.
   - Live under series **`soccer-fifwc`** (NOT `tag=world-cup`, which is only
     futures). Gamma `GET /events?series_slug=soccer-fifwc&closed=false&limit=100&offset=N`
     (paginated). https://polymarket.com/sports/world-cup/games
   - Each game splits across events: base slug = **moneyline** (3-way negRisk
     group of YES/NO legs), `{base}-more-markets` = **spreads/totals/btts**.
   - Type = `sportsMarketType` + `marketMetadata.opticOddsMarketId`:
     `moneyline`/`moneyline_3-way`, `spreads`/`asian_handicap`,
     `totals`/`total_goals`, `both_teams_to_score`. Half-time / team-total /
     exact-score variants are excluded. moneyline = `negRisk=true`; the rest
     `negRisk=false`.

Chosen because **all resolve soon** (5 min, or by end of a match) → capital isn't
locked for long.

## 3. Verified Polymarket integration (CLOB **V2**)

All values confirmed **against the installed `py-clob-client-v2` source** and live
probes (2026-06-17). The Rust signing is **byte-for-byte validated** against it
(see §7).

| Thing | Value |
|---|---|
| CLOB REST | `https://clob.polymarket.com` |
| Gamma (discovery) | `https://gamma-api.polymarket.com` |
| Data API (positions) | `https://data-api.polymarket.com` |
| CLOB **market** WebSocket (best bid/ask) | `wss://ws-subscriptions-clob.polymarket.com/ws/market` |
| Chain | Polygon, `chain_id = 137` |
| Signature type | `2` (Gnosis-Safe proxy) — `PRIVATE_KEY` signs, `FUNDER_ADDRESS` = maker |
| Collateral (V2) | **pUSD** `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` (6 dp) |
| **CTF Exchange V2** (negRisk=false) | `0xE111180000d2663C0091e4f400237545B87B996B` |
| **Neg-Risk Exchange V2** (negRisk=true) | `0xe2222d279d744050d28e00520010520000310F59` |
| Order type | **GTC** limit, **buy-only**, 5-share min |
| Positions | `GET data-api/positions?user=<funder>&sizeThreshold=0.1` |

> ⚠️ The old **V1** exchanges (`0x4bFb41…`, `0xC5d563…`, domain version `"1"`) are
> NOT used for V2 orders. V2 uses the addresses above + domain version `"2"`.

**EIP-712 order signing (V2 — `clob/signing.rs`):**
- Domain: `name="Polymarket CTF Exchange"`, `version="2"`, `chainId=137`,
  `verifyingContract` = exchange (neg-risk one for `negRisk=true` markets).
- **Order struct (11 fields, in this exact order/type):**
  `Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)`.
  V2 **dropped** `taker/expiration/nonce/feeRateBps` from the hash and **added**
  `timestamp` (ms), `metadata`, `builder` (both `bytes32(0)`).
- `maker` = funder/Safe; `signer` = signing EOA; `side` BUY=0; `signatureType` 2;
  `timestamp` = ms (uniqueness, also in the body); `salt = floor(rand()*now_ms)`.
- Sign the plain EIP-712 digest with the owner EOA (`wallet.sign_hash`) → 65-byte
  `0x…` sig. (The Solady 1271 wrapper is only for `signatureType=3` — not us.)
- **Amounts (limit BUY, `buy_amounts`):** pick `ROUNDING_CONFIG` by tick
  (`0.01`→(2,2,4), `0.001`→(3,2,5)…); `raw_price=round(p,price_dp)`,
  `takerAmount=to_token_decimals(round_down(size,size_dp))` (shares, 6 dp),
  `makerAmount=to_token_decimals(round_down(size)*raw_price)` (pUSD, 6 dp).
- **`expiration`** ("0" = GTC) goes in the POST body but **NOT** the signed hash.

**`0.999` vs tick (important — we ONLY place at exactly 0.999):** 0.999 is only
representable on **0.001-tick** (or finer) markets. Polymarket uses a **dynamic
tick** that tightens from 0.01 → 0.001 as a side approaches 1.00 (the WS
`tick_size_change` event), so a market that's 0.01-tick at mid-price becomes
0.001-tick near resolution — exactly when our ask hits 0.999. The bot tracks the
**live tick** per token (`state.live_ticks`, from CLOB `book` / `tick_size_change`
/ `POST /books`) and `price_representable(0.999, tick)` gates every placement at
three levels (scan, strategy, executor). If the live tick can only reach 0.99 we
**skip entirely** — we never place at 0.99. (Live check: 24 of 426 monitored
tokens were already 0.001-tick; the rest 0.01.)

**Auth (`clob/auth.rs`):**
- **L1** (derive API key): EIP-712 `ClobAuth(address address,string timestamp,uint256 nonce,string message)`,
  domain `ClobAuthDomain`/`"1"`/137 (no verifyingContract), `message="This message
  attests that I control the given wallet"`, `timestamp`=seconds. Headers
  `POLY_ADDRESS/SIGNATURE/TIMESTAMP/NONCE` → `POST /auth/api-key` (fallback `GET
  /auth/derive-api-key`) → `{apiKey,secret,passphrase}`.
- **L2** (per request): `sig = base64url(HMAC_SHA256(base64url_decode(secret),
  timestamp_secs + METHOD + path + body))`. Headers `POLY_ADDRESS/SIGNATURE/
  TIMESTAMP/API_KEY/PASSPHRASE`. The HMAC body is the **exact compact JSON string
  we POST**.
- `POST /order` body: `{order:{salt(int),maker,signer,tokenId,makerAmount,
  takerAmount,side:"BUY",expiration:"0",signatureType:2,timestamp(ms),metadata,
  builder,signature}, owner:<api_key>, orderType:"GTC", deferExec:false,
  postOnly:false}`.

**Data feed (`clob/ws.rs`):** subscribe `{assets_ids:[…], type:"market",
custom_feature_enabled:true}`; read `best_bid_ask` / `price_change` (both carry
best bid/ask) / `book` (depth, arrays ascending → best = last). `"PING"` every 7 s;
reconnect when the watch set changes. REST resync (`POST /books`) as a safety net.

## 4. Architecture (single async `tokio` process) — all built

```
src/
  main.rs            load config; (selftest |) build executor (live); spawn
                     discovery + feed(s) + positions + strategy + dashboard; run
  config.rs          env config + verified hosts/addresses; live-mode safety gate
  models.rs          MarketKind, OutcomeToken, BookTop, RestingOrder, Position
  state.rs           BotState: lock-free DashMaps (tokens/books/orders/triggered/positions)
  strategy.rs        the ask≥0.999 → GTC-buy-@0.999 rule; dedup; dry-run vs live
  dashboard.rs       axum server: GET / (live HTML) + GET /api/state (JSON)
  markets/discovery.rs   crypto 5m slugs (7 assets) + World Cup (series soccer-fifwc)
  clob/
    signing.rs       EIP-712 V2 order hash+sign, amount/tick math, `selftest`
    auth.rs          L1 derive-api-key (ClobAuth) + L2 HMAC headers
    executor.rs      build→sign→POST GTC buy (L2 auth); live only
    ws.rs            CLOB market socket feed (best bid/ask)
    rest.rs          POST /books batch top-of-book (feed / resync)
    positions.rs     Data API positions poll (funder wallet) for the dashboard
```

**Concurrency:** discovery refreshes the token set; the feed (WS + slow REST
resync, or pure REST when `DATA_SOURCE=rest`) writes best bid/ask into shared
state; the strategy scans books every 120 ms and fires once per token; the
dashboard serves a live view. One instance, all markets — no per-market process.

## 5. Config (env) — see `.env.example`

`PRIVATE_KEY`, `FUNDER_ADDRESS`, `SIGNATURE_TYPE=2`, `DRY_RUN=true|false`,
`LIVE_SIGNING_VALIDATED`, `DATA_SOURCE=ws|rest`,
`ASSETS=btc,eth,sol,xrp,doge,hype,bnb`, `ENABLE_WORLDCUP=true`,
`WORLDCUP_MARKET_TYPES=moneyline,spreads,totals,btts`, `BUY_PRICE=0.999`,
`TRIGGER_ASKS=0.999,1.0`, `ORDER_SIZE_SHARES=5` (global default),
**`CRYPTO_ORDER_SIZE_SHARES`** (crypto 5m size), **`WORLDCUP_ORDER_SIZE_SHARES`**
(World Cup size) — both default to `ORDER_SIZE_SHARES`,
**`CRYPTO_ORDER_MAX_AGE_MINS=45`** (cancel crypto resting orders older than this;
0 = off; sports exempt), `MAX_OPEN_ORDERS`, `DASHBOARD_PORT=8090`, `REST_POLL_MS`,
`LOG_LEVEL`.

## 6. Build / run

Toolchain: Rust **GNU** (`stable-x86_64-pc-windows-gnu`) + MinGW-w64 (WinLibs) on
PATH — no Visual Studio. Deps are pure-Rust (native-tls/SChannel, k256) so GNU
links self-contained.

```powershell
cd F:\others\githubrepo\latebutfast
# (PATH already has ~/.cargo/bin + the WinLibs mingw64\bin)
cargo build
# safe dry-run: discovers markets, streams books, LOGS would-be orders, dashboard live
$env:DRY_RUN="true"; cargo run
# dashboard: http://localhost:8090
# signing self-test vector (no network) — for the cross-check gate:
$env:PRIVATE_KEY="0x…"; cargo run -- selftest
# after the gate + funding/allowances are set:
$env:DRY_RUN="false"; $env:LIVE_SIGNING_VALIDATED="true"; cargo run
```

## 7. Status — COMPLETE (dry-run validated 2026-06-17)

- ✅ Discovery, WS feed, REST resync, strategy, dashboard, positions — **built and
  verified in dry-run** on the live book: 426 tokens (42 crypto-5m + 384 WC), WS
  streaming real bids/asks, trigger → dry-run order → dashboard confirmed.
- ✅ **Signing gate PASSED.** `cargo run -- selftest` produces an order hash +
  signature **byte-identical** to `py-clob-client-v2` `ExchangeOrderBuilderV2` for
  the same fixed inputs, on **both** exchanges (verified — see below). The hardest
  risk (silent invalid signatures) is eliminated.
- Live order placement stays gated behind `DRY_RUN=false` **and**
  `LIVE_SIGNING_VALIDATED=true` (config refuses to start live otherwise).

**Reproduce the signing gate** (Hardhat key `0xac0974…f2ff80`):
`cargo run -- selftest` ⇒ exchange_v2 hash `0x4cdf33ec…f6db3e`, sig `0x7a000fcb…64ee2f1c`;
neg_risk hash `0x67de69a9…3a6e58`, sig `0x58545f32…f16bce1c` — matches
py-clob-client-v2 exactly.

### Remaining before live (operational, not code)
1. Fund the proxy wallet with **pUSD** and set **ERC-20 allowance** to the V2
   exchange(s) + CTF token approvals (orders can't fill without it).
2. First **live** `POST /order` round-trip: confirm L1/L2 auth works with your real
   key and the response shape (`success`/`orderID`/`status`). Start with one
   market / tiny size.
3. (Behaviour already locked: on 0.01-tick markets we **skip** — never place at
   0.99; we only fire when the live tick can hit 0.999 exactly.)

## 8. Hard rules

1. **Never** place an order while `DRY_RUN=true` — only log it.
2. Live placement requires `LIVE_SIGNING_VALIDATED=true` (signing gate passed).
3. Buy-only at **exactly 0.999** — if the live tick can only reach 0.99, **skip**
   (never place at 0.99). Never sell. 5-share minimum.
4. **No double-placing:** one-shot per token + position + open-order reconciliation.
5. Per-kind size: `CRYPTO_ORDER_SIZE_SHARES` vs `WORLDCUP_ORDER_SIZE_SHARES`.
6. **Sports markets only trade once the game has started** (live or ended) — never
   pre-kickoff. Crypto 5m is exempt.
7. One instance monitors all markets; never require per-market deployment.
8. Use the **V2** exchanges + domain version `"2"` (§3). Never the V1 addresses.
9. The L2 HMAC body must be the **exact bytes POSTed** — build the compact JSON
   once, sign and send the same string.
