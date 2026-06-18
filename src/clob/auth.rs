//! CLOB auth: L1 (derive/create API key via ClobAuth EIP-712) and L2 (HMAC-SHA256
//! request signing). Faithful to py-clob-client-v2 signing/eip712.py, signing/hmac.py,
//! headers/headers.py.
use std::time::{SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::URL_SAFE;
use base64::Engine;
use ethers_core::utils::to_checksum;
use ethers_signers::{LocalWallet, Signer};
use hmac::{Hmac, Mac};
use serde_json::Value;
use sha2::Sha256;
use tracing::info;

use crate::clob::signing;
use crate::config::Config;

#[derive(Clone, Debug)]
pub struct ApiCreds {
    pub api_key: String,
    pub api_secret: String,
    pub api_passphrase: String,
}

/// Pre-created API credentials from the env (CLOB_API_KEY / CLOB_API_SECRET /
/// CLOB_API_PASSPHRASE). Returned only if all three are set. Required for deposit
/// wallets (sig type 3), which can't be L1-derived here — create them for the
/// deposit wallet on Polymarket. Also usable for any wallet to skip derivation.
pub fn provided_creds(cfg: &Config) -> Option<ApiCreds> {
    if !cfg.clob_api_key.is_empty()
        && !cfg.clob_api_secret.is_empty()
        && !cfg.clob_api_passphrase.is_empty()
    {
        Some(ApiCreds {
            api_key: cfg.clob_api_key.clone(),
            api_secret: cfg.clob_api_secret.clone(),
            api_passphrase: cfg.clob_api_passphrase.clone(),
        })
    } else {
        None
    }
}

pub fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}
pub fn now_millis() -> u128 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis()
}

/// L1: create-or-derive the API credentials for this wallet.
/// Tries POST /auth/api-key, then falls back to GET /auth/derive-api-key.
pub async fn create_or_derive(
    cfg: &Config,
    client: &reqwest::Client,
    wallet: &LocalWallet,
) -> anyhow::Result<ApiCreds> {
    // L1 derivation only works for an EOA-controlled key (types 0/1/2). Deposit
    // wallets (sig type 3) can't be L1-derived here (would need an ERC-1271 ClobAuth
    // signature); those MUST supply pre-created creds — see provided_creds() +
    // Executor::new. POLY_ADDRESS + ClobAuth address = the signing EOA, checksummed.
    let ts = now_secs();
    let nonce = 0u64;
    let addr = wallet.address();
    let sig = signing::sign_clob_auth(wallet, addr, ts, nonce, cfg.chain_id)?;
    let addr_hex = to_checksum(&addr, None);

    let l1 = |rb: reqwest::RequestBuilder| {
        rb.header("POLY_ADDRESS", &addr_hex)
            .header("POLY_SIGNATURE", &sig)
            .header("POLY_TIMESTAMP", ts.to_string())
            .header("POLY_NONCE", nonce.to_string())
    };

    // create
    let create = l1(client.post(format!("{}/auth/api-key", cfg.clob_url)))
        .send()
        .await;
    if let Ok(r) = create {
        if r.status().is_success() {
            if let Ok(v) = r.json::<Value>().await {
                if let Some(c) = parse_creds(&v) {
                    info!("clob auth: created API key {}", mask(&c.api_key));
                    return Ok(c);
                }
            }
        }
    }
    // derive
    let derive = l1(client.get(format!("{}/auth/derive-api-key", cfg.clob_url)))
        .send()
        .await?;
    let status = derive.status();
    let v: Value = derive.json().await.map_err(|e| anyhow::anyhow!("derive-api-key decode ({status}): {e}"))?;
    let c = parse_creds(&v)
        .ok_or_else(|| anyhow::anyhow!("derive-api-key returned no creds ({status}): {v}"))?;
    info!("clob auth: derived API key {}", mask(&c.api_key));
    Ok(c)
}

fn parse_creds(v: &Value) -> Option<ApiCreds> {
    let g = |a: &str, b: &str| {
        v.get(a)
            .or_else(|| v.get(b))
            .and_then(|x| x.as_str())
            .map(String::from)
    };
    Some(ApiCreds {
        api_key: g("apiKey", "api_key")?,
        api_secret: g("secret", "api_secret")?,
        api_passphrase: g("passphrase", "api_passphrase")?,
    })
}

/// L2 HMAC: signature over `timestamp(secs) + METHOD + path [+ body]`.
/// URL-safe base64 on BOTH the secret decode and the digest encode.
pub fn l2_signature(
    secret: &str,
    ts: u64,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> anyhow::Result<String> {
    let key = URL_SAFE.decode(secret.as_bytes())?;
    let mut mac = Hmac::<Sha256>::new_from_slice(&key)
        .map_err(|e| anyhow::anyhow!("hmac key: {e}"))?;
    let mut msg = format!("{ts}{method}{path}");
    if let Some(b) = body {
        msg.push_str(b);
    }
    mac.update(msg.as_bytes());
    Ok(URL_SAFE.encode(mac.finalize().into_bytes()))
}

/// The 5 L2 headers for an authenticated request, as (name, value) pairs.
pub fn l2_headers(
    creds: &ApiCreds,
    address_hex: &str,
    ts: u64,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> anyhow::Result<Vec<(&'static str, String)>> {
    let sig = l2_signature(&creds.api_secret, ts, method, path, body)?;
    Ok(vec![
        ("POLY_ADDRESS", address_hex.to_string()),
        ("POLY_SIGNATURE", sig),
        ("POLY_TIMESTAMP", ts.to_string()),
        ("POLY_API_KEY", creds.api_key.clone()),
        ("POLY_PASSPHRASE", creds.api_passphrase.clone()),
    ])
}

fn mask(s: &str) -> String {
    if s.len() > 8 {
        format!("{}…{}", &s[..4], &s[s.len() - 4..])
    } else {
        "****".into()
    }
}
