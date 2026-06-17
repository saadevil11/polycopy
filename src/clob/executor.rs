//! Live order executor: builds, signs (EIP-712 V2), and POSTs a GTC BUY with L2
//! HMAC auth. Mirrors py-clob-client-v2 create_and_post_order for sig type 2.
//! Only constructed in LIVE mode (DRY_RUN=false + LIVE_SIGNING_VALIDATED=true).
use std::time::Duration;

use ethers_core::types::{Address, U256};
use ethers_core::utils::to_checksum;
use ethers_signers::{LocalWallet, Signer};
use serde_json::{json, Value};
use tracing::{debug, warn};

use crate::clob::auth::{self, ApiCreds};
use crate::clob::signing::{self, OrderInput, SIDE_BUY};
use crate::config::Config;
use crate::models::OutcomeToken;

const BYTES32_ZERO: &str = "0x0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Clone, serde::Serialize)]
pub struct PositionInfo {
    pub token_id: String,
    pub title: String,
    pub outcome: String,
    pub size: f64,
    pub avg_price: f64,
    pub cur_price: f64,
    pub value: f64,
    pub pnl: f64,
}

pub struct Executor {
    client: reqwest::Client,
    wallet: LocalWallet,
    creds: ApiCreds,
    clob_url: String,
    data_api_url: String,
    chain_id: u64,
    sig_type: u8,
    /// funder / Safe address, verbatim as the user supplied it (the `maker`).
    funder: String,
    funder_addr: Address,
    signer_checksum: String,
    exchange_v2: Address,
    neg_risk_exchange_v2: Address,
    dry_run: bool,
}

impl Executor {
    /// Build the wallet and derive API credentials. Fails loudly if auth fails.
    pub async fn new(cfg: &Config) -> anyhow::Result<Self> {
        let wallet: LocalWallet = cfg
            .private_key
            .trim_start_matches("0x")
            .parse()
            .map_err(|e| anyhow::anyhow!("invalid PRIVATE_KEY: {e}"))?;
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .user_agent("latebutfast/0.1")
            .build()?;
        let creds = auth::create_or_derive(cfg, &client, &wallet).await?;

        let funder_addr: Address = cfg
            .funder_address
            .parse()
            .map_err(|e| anyhow::anyhow!("invalid FUNDER_ADDRESS: {e}"))?;

        Ok(Executor {
            signer_checksum: to_checksum(&wallet.address(), None),
            client,
            wallet,
            creds,
            clob_url: cfg.clob_url.clone(),
            data_api_url: cfg.data_api_url.clone(),
            chain_id: cfg.chain_id,
            sig_type: cfg.signature_type,
            funder: cfg.funder_address.clone(),
            funder_addr,
            exchange_v2: cfg.exchange_v2.parse()?,
            neg_risk_exchange_v2: cfg.neg_risk_exchange_v2.parse()?,
            dry_run: cfg.dry_run,
        })
    }

    /// Place a GTC BUY of `size` shares at EXACTLY `price`, using the live `tick`.
    /// Refuses if `price` is not representable at `tick` (we never settle for a
    /// worse price like 0.99). Returns the CLOB order id on success.
    pub async fn place_gtc_buy(
        &self,
        token: &OutcomeToken,
        price: f64,
        size: f64,
        tick: f64,
    ) -> anyhow::Result<String> {
        let tick = if tick > 0.0 { tick } else { 0.01 };
        if !signing::price_representable(price, tick) {
            anyhow::bail!("price {price} not representable at tick {tick} — refusing (only place exact {price})");
        }
        let snapped = signing::snap_price(price, tick);
        let (maker_amount, taker_amount) = signing::buy_amounts(snapped, size, tick);
        if taker_amount == 0 || maker_amount == 0 {
            anyhow::bail!("computed zero amount (price={snapped}, size={size}, tick={tick})");
        }

        let token_id = U256::from_dec_str(&token.token_id)
            .map_err(|e| anyhow::anyhow!("bad token_id: {e}"))?;
        let now_ms = auth::now_millis();
        let salt = signing::generate_salt(now_ms);

        let verifying = if token.neg_risk {
            self.neg_risk_exchange_v2
        } else {
            self.exchange_v2
        };

        let order = OrderInput {
            salt,
            maker: self.funder_addr,
            signer: self.wallet.address(),
            token_id,
            maker_amount,
            taker_amount,
            side: SIDE_BUY,
            sig_type: self.sig_type,
            timestamp_ms: now_ms,
        };
        let signature = signing::sign_order(&self.wallet, &order, verifying, self.chain_id)?;

        // Build the body ONCE as a compact string; HMAC and POST the same bytes.
        let body = json!({
            "order": {
                "salt": salt as u64,
                "maker": self.funder,
                "signer": self.signer_checksum,
                "tokenId": token.token_id,
                "makerAmount": maker_amount.to_string(),
                "takerAmount": taker_amount.to_string(),
                "side": "BUY",
                "expiration": "0",
                "signatureType": self.sig_type,
                "timestamp": now_ms.to_string(),
                "metadata": BYTES32_ZERO,
                "builder": BYTES32_ZERO,
                "signature": signature,
            },
            "owner": self.creds.api_key,
            "orderType": "GTC",
            "deferExec": false,
            "postOnly": false,
        });
        let body_str = serde_json::to_string(&body)?;

        let ts = auth::now_secs();
        let headers = auth::l2_headers(
            &self.creds,
            &self.signer_checksum,
            ts,
            "POST",
            "/order",
            Some(&body_str),
        )?;

        let mut rb = self
            .client
            .post(format!("{}/order", self.clob_url))
            .header("Content-Type", "application/json")
            .body(body_str);
        for (k, v) in headers {
            rb = rb.header(k, v);
        }

        let resp = rb.send().await?;
        let status = resp.status();
        let v: Value = resp.json().await.unwrap_or(Value::Null);
        debug!("POST /order -> {status}: {v}");

        let ok = v.get("success").and_then(|x| x.as_bool()).unwrap_or(false);
        let order_id = v
            .get("orderID")
            .or_else(|| v.get("orderId"))
            .or_else(|| v.get("id"))
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();

        if status.is_success() && ok && !order_id.is_empty() {
            Ok(order_id)
        } else {
            let err = v
                .get("errorMsg")
                .or_else(|| v.get("error"))
                .and_then(|x| x.as_str())
                .unwrap_or("unknown");
            warn!("order rejected ({status}): {err} | full={v}");
            anyhow::bail!("order rejected ({status}): {err}")
        }
    }

    /// Cancel a resting order by id. `DELETE /order` body `{"orderID":"…"}`,
    /// L2-signed over `DELETE`+`/order`+body (py-clob-client-v2 cancel_order).
    pub async fn cancel_order(&self, order_id: &str) -> anyhow::Result<()> {
        if self.dry_run || order_id.starts_with("dryrun-") {
            return Ok(());
        }
        let body_str = serde_json::to_string(&json!({ "orderID": order_id }))?;
        let ts = auth::now_secs();
        let headers =
            auth::l2_headers(&self.creds, &self.signer_checksum, ts, "DELETE", "/order", Some(&body_str))?;
        let mut rb = self
            .client
            .delete(format!("{}/order", self.clob_url))
            .header("Content-Type", "application/json")
            .body(body_str);
        for (k, v) in headers {
            rb = rb.header(k, v);
        }
        let resp = rb.send().await?;
        let status = resp.status();
        let v: Value = resp.json().await.unwrap_or(Value::Null);
        if status.is_success() {
            debug!("cancel {} -> {v}", &order_id[..order_id.len().min(10)]);
            Ok(())
        } else {
            anyhow::bail!("cancel rejected ({status}): {v}")
        }
    }

    /// True if we already have a resting (un-filled) order on this token.
    /// Mirrors the copytrader's open-orders reconciliation so a restart never
    /// double-places. `GET /data/orders?asset_id=…`, L2-signed over `GET`+path.
    pub async fn has_existing_order(&self, token_id: &str) -> bool {
        let ts = auth::now_secs();
        let headers =
            match auth::l2_headers(&self.creds, &self.signer_checksum, ts, "GET", "/data/orders", None) {
                Ok(h) => h,
                Err(_) => return false,
            };
        let mut rb = self.client.get(format!(
            "{}/data/orders?asset_id={}&next_cursor=MA==",
            self.clob_url, token_id
        ));
        for (k, v) in headers {
            rb = rb.header(k, v);
        }
        let v: Value = match rb.send().await {
            Ok(r) => r.json().await.unwrap_or(Value::Null),
            Err(e) => {
                debug!("open-orders check failed for {}: {e}", &token_id[..token_id.len().min(8)]);
                return false; // don't block on a transient error; one-shot still guards the session
            }
        };
        let data = v.get("data").and_then(|d| d.as_array()).cloned().unwrap_or_default();
        data.iter().any(|o| {
            let orig = num(o, "original_size");
            let matched = num(o, "size_matched");
            orig - matched > 0.0
        })
    }

    // ── copytrader execution API (token-id based; reuses validated signing) ──

    /// Place a GTC limit order (BUY=0 / SELL=1) at EXACTLY `price` (snapped to
    /// tick) for `size` shares on `token_id`. Returns the CLOB order id.
    pub async fn place_gtc(
        &self,
        token_id: &str,
        neg_risk: bool,
        side: u8,
        price: f64,
        size: f64,
        tick: f64,
    ) -> anyhow::Result<String> {
        let tick = if tick > 0.0 { tick } else { 0.01 };
        let snapped = signing::snap_price(price, tick);
        let side_str = if side == signing::SIDE_BUY { "BUY" } else { "SELL" };
        if self.dry_run {
            debug!("DRY: would place {side_str} {size} @ {snapped} on {}", &token_id[..token_id.len().min(10)]);
            return Ok(format!("dryrun-{}", auth::now_millis()));
        }
        let (maker_amount, taker_amount) = if side == signing::SIDE_BUY {
            signing::buy_amounts(snapped, size, tick)
        } else {
            signing::sell_amounts(snapped, size, tick)
        };
        if maker_amount == 0 || taker_amount == 0 {
            anyhow::bail!("computed zero amount (price={snapped}, size={size}, tick={tick})");
        }
        let token_u256 =
            U256::from_dec_str(token_id).map_err(|e| anyhow::anyhow!("bad token_id: {e}"))?;
        let now_ms = auth::now_millis();
        let salt = signing::generate_salt(now_ms);
        let verifying = if neg_risk { self.neg_risk_exchange_v2 } else { self.exchange_v2 };

        let order = OrderInput {
            salt,
            maker: self.funder_addr,
            signer: self.wallet.address(),
            token_id: token_u256,
            maker_amount,
            taker_amount,
            side,
            sig_type: self.sig_type,
            timestamp_ms: now_ms,
        };
        let signature = signing::sign_order(&self.wallet, &order, verifying, self.chain_id)?;
        let side_str = if side == signing::SIDE_BUY { "BUY" } else { "SELL" };

        let body = json!({
            "order": {
                "salt": salt as u64,
                "maker": self.funder,
                "signer": self.signer_checksum,
                "tokenId": token_id,
                "makerAmount": maker_amount.to_string(),
                "takerAmount": taker_amount.to_string(),
                "side": side_str,
                "expiration": "0",
                "signatureType": self.sig_type,
                "timestamp": now_ms.to_string(),
                "metadata": BYTES32_ZERO,
                "builder": BYTES32_ZERO,
                "signature": signature,
            },
            "owner": self.creds.api_key,
            "orderType": "GTC",
            "deferExec": false,
            "postOnly": false,
        });
        let body_str = serde_json::to_string(&body)?;
        let ts = auth::now_secs();
        let headers =
            auth::l2_headers(&self.creds, &self.signer_checksum, ts, "POST", "/order", Some(&body_str))?;
        let mut rb = self
            .client
            .post(format!("{}/order", self.clob_url))
            .header("Content-Type", "application/json")
            .body(body_str);
        for (k, v) in headers {
            rb = rb.header(k, v);
        }
        let resp = rb.send().await?;
        let status = resp.status();
        let v: Value = resp.json().await.unwrap_or(Value::Null);
        debug!("POST /order ({side_str}) -> {status}: {v}");
        let ok = v.get("success").and_then(|x| x.as_bool()).unwrap_or(false);
        let order_id = v
            .get("orderID")
            .or_else(|| v.get("orderId"))
            .or_else(|| v.get("id"))
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        if status.is_success() && ok && !order_id.is_empty() {
            Ok(order_id)
        } else {
            let err = v
                .get("errorMsg")
                .or_else(|| v.get("error"))
                .and_then(|x| x.as_str())
                .unwrap_or("unknown")
                .to_string();
            // Surface the raw error so callers can detect balance/min-size/etc.
            anyhow::bail!("order rejected ({status}): {err}")
        }
    }

    /// Marketable sell: price 3c below the best bid so it sweeps the bid side and
    /// fills now. Returns the order id (the unfilled remainder rests and should
    /// be cancelled by the caller's retry loop).
    pub async fn market_sell(
        &self,
        token_id: &str,
        neg_risk: bool,
        size: f64,
        best_bid: f64,
        tick: f64,
    ) -> anyhow::Result<String> {
        let tick = if tick > 0.0 { tick } else { 0.01 };
        let sweep = (signing::snap_price((best_bid - 0.03).max(tick), tick)).max(tick);
        self.place_gtc(token_id, neg_risk, signing::SIDE_SELL, sweep, size, tick).await
    }

    /// Matched (filled) share count for an order id. `GET /data/order/{id}`.
    pub async fn order_matched(&self, order_id: &str) -> Option<f64> {
        let path = format!("/data/order/{order_id}");
        let ts = auth::now_secs();
        let headers = auth::l2_headers(&self.creds, &self.signer_checksum, ts, "GET", &path, None).ok()?;
        let mut rb = self.client.get(format!("{}{}", self.clob_url, path));
        for (k, v) in headers {
            rb = rb.header(k, v);
        }
        let v: Value = rb.send().await.ok()?.json().await.ok()?;
        Some(num(&v, "size_matched"))
    }

    /// All resting orders for a token: (order_id, side, price, original, matched).
    pub async fn open_orders(&self, token_id: &str) -> Vec<(String, String, f64, f64, f64)> {
        let ts = auth::now_secs();
        let headers = match auth::l2_headers(&self.creds, &self.signer_checksum, ts, "GET", "/data/orders", None) {
            Ok(h) => h,
            Err(_) => return Vec::new(),
        };
        let mut rb = self
            .client
            .get(format!("{}/data/orders?asset_id={}&next_cursor=MA==", self.clob_url, token_id));
        for (k, v) in headers {
            rb = rb.header(k, v);
        }
        let v: Value = match rb.send().await {
            Ok(r) => r.json().await.unwrap_or(Value::Null),
            Err(_) => return Vec::new(),
        };
        let data = v.get("data").and_then(|d| d.as_array()).cloned().unwrap_or_default();
        data.iter()
            .map(|o| {
                let id = o.get("id").or_else(|| o.get("orderID")).and_then(|x| x.as_str()).unwrap_or("").to_string();
                let side = o.get("side").and_then(|x| x.as_str()).unwrap_or("").to_uppercase();
                (id, side, num(o, "price"), num(o, "original_size"), num(o, "size_matched"))
            })
            .filter(|(id, ..)| !id.is_empty())
            .collect()
    }

    /// All resting orders across all markets: (order_id, token_id, side, price).
    pub async fn all_open_orders(&self) -> Vec<(String, String, String, f64)> {
        let ts = auth::now_secs();
        let headers = match auth::l2_headers(&self.creds, &self.signer_checksum, ts, "GET", "/data/orders", None) {
            Ok(h) => h,
            Err(_) => return Vec::new(),
        };
        let mut rb = self.client.get(format!("{}/data/orders?next_cursor=MA==", self.clob_url));
        for (k, v) in headers {
            rb = rb.header(k, v);
        }
        let v: Value = match rb.send().await {
            Ok(r) => r.json().await.unwrap_or(Value::Null),
            Err(_) => return Vec::new(),
        };
        let data = v.get("data").and_then(|d| d.as_array()).cloned().unwrap_or_default();
        data.iter()
            .map(|o| {
                let id = o.get("id").or_else(|| o.get("orderID")).and_then(|x| x.as_str()).unwrap_or("").to_string();
                let token = o.get("asset_id").or_else(|| o.get("asset")).and_then(|x| x.as_str()).unwrap_or("").to_string();
                let side = o.get("side").and_then(|x| x.as_str()).unwrap_or("").to_uppercase();
                (id, token, side, num(o, "price"))
            })
            .filter(|(id, ..)| !id.is_empty())
            .collect()
    }

    /// Cancel and confirm a single order is gone from the book. Returns true if
    /// confirmed not resting (cancelled or already gone).
    pub async fn cancel_confirmed(&self, order_id: &str, token_id: &str) -> bool {
        for _ in 0..3 {
            if !self.open_orders(token_id).await.iter().any(|(id, ..)| id == order_id) {
                return true;
            }
            let _ = self.cancel_order(order_id).await;
        }
        !self.open_orders(token_id).await.iter().any(|(id, ..)| id == order_id)
    }

    /// Shares of `token_id` held by the funder, from the Data API positions.
    pub async fn token_holdings(&self, token_id: &str) -> f64 {
        let url = format!("{}/positions?user={}&sizeThreshold=0.1", self.data_api_url, self.funder);
        let v: Value = match self.client.get(&url).send().await {
            Ok(r) => r.json().await.unwrap_or(Value::Null),
            Err(_) => return 0.0,
        };
        v.as_array()
            .map(|arr| {
                arr.iter()
                    .filter(|p| p.get("asset").and_then(|a| a.as_str()) == Some(token_id))
                    .map(|p| num(p, "size"))
                    .sum()
            })
            .unwrap_or(0.0)
    }

    /// Open positions for the funder (for the dashboard), via the Data API.
    /// Skips resolved/redeemable positions.
    pub async fn positions(&self) -> Vec<PositionInfo> {
        let url = format!("{}/positions?user={}&sizeThreshold=0.1", self.data_api_url, self.funder);
        let v: Value = match self.client.get(&url).send().await {
            Ok(r) => r.json().await.unwrap_or(Value::Null),
            Err(_) => return Vec::new(),
        };
        let arr = match v.as_array() {
            Some(a) => a,
            None => return Vec::new(),
        };
        arr.iter()
            .filter(|p| p.get("redeemable").and_then(|x| x.as_bool()) != Some(true))
            .filter(|p| num(p, "size") > 0.0)
            .map(|p| {
                let size = num(p, "size");
                let cur = num(p, "curPrice");
                PositionInfo {
                    token_id: p.get("asset").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    title: p.get("title").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    outcome: p.get("outcome").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    size,
                    avg_price: num(p, "avgPrice"),
                    cur_price: cur,
                    value: size * cur,
                    pnl: num(p, "cashPnl"),
                }
            })
            .collect()
    }

    /// Set of outcome-token ids a given address currently holds (size>0), via the
    /// Data API. None on failure (caller treats as "unknown", not "exited").
    pub async fn token_holdings_set(&self, address: &str) -> Option<std::collections::HashSet<String>> {
        let url = format!("{}/positions?user={}&sizeThreshold=0.1", self.data_api_url, address);
        let v: Value = self.client.get(&url).send().await.ok()?.json().await.ok()?;
        let arr = v.as_array()?;
        let mut set = std::collections::HashSet::new();
        for p in arr {
            if num(p, "size") > 0.0 {
                if let Some(a) = p.get("asset").and_then(|x| x.as_str()) {
                    set.insert(a.to_string());
                }
            }
        }
        Some(set)
    }

    /// Best (bid, ask) for a token via `GET /book`.
    pub async fn book(&self, token_id: &str) -> (f64, f64) {
        let url = format!("{}/book?token_id={}", self.clob_url, token_id);
        let v: Value = match self.client.get(&url).send().await {
            Ok(r) => r.json().await.unwrap_or(Value::Null),
            Err(_) => return (0.0, 0.0),
        };
        let bid = v["bids"].as_array().map(|a| a.iter().filter_map(|l| lvl(l)).fold(0.0_f64, f64::max)).unwrap_or(0.0);
        let ask = v["asks"].as_array().map(|a| a.iter().filter_map(|l| lvl(l)).fold(f64::MAX, f64::min)).filter(|x| *x != f64::MAX).unwrap_or(0.0);
        (bid, ask)
    }

    /// Whether a token's market is neg-risk (selects the signing exchange).
    /// Resolved from the CLOB market record; defaults false on failure.
    pub async fn token_neg_risk(&self, token_id: &str) -> bool {
        let url = format!("{}/markets/0x?clob_token_ids={}", self.clob_url, token_id);
        // Prefer the gamma markets lookup which exposes negRisk reliably.
        let gamma = format!("https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}");
        for u in [gamma, url] {
            if let Ok(r) = self.client.get(&u).send().await {
                if let Ok(v) = r.json::<Value>().await {
                    let item = if v.is_array() { v.get(0).cloned().unwrap_or(Value::Null) } else { v };
                    if let Some(b) = item
                        .get("negRisk")
                        .or_else(|| item.get("neg_risk"))
                        .or_else(|| item.get("negativeRisk"))
                        .and_then(|x| x.as_bool())
                    {
                        return b;
                    }
                }
            }
        }
        false
    }

    /// Tick size for a token via `GET /tick-size` (defaults 0.01).
    pub async fn tick_size(&self, token_id: &str) -> f64 {
        let url = format!("{}/tick-size?token_id={}", self.clob_url, token_id);
        let v: Value = match self.client.get(&url).send().await {
            Ok(r) => r.json().await.unwrap_or(Value::Null),
            Err(_) => return 0.01,
        };
        v.get("minimum_tick_size")
            .and_then(|x| x.as_f64().or_else(|| x.as_str().and_then(|s| s.parse().ok())))
            .unwrap_or(0.01)
    }
}

fn lvl(level: &Value) -> Option<f64> {
    match &level["price"] {
        Value::String(s) => s.parse().ok(),
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

fn num(v: &Value, key: &str) -> f64 {
    match v.get(key) {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::String(s)) => s.parse().unwrap_or(0.0),
        _ => 0.0,
    }
}
