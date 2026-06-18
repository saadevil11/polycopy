//! CLOB **V2** order signing — a faithful Rust port of py-clob-client-v2's
//! `ExchangeOrderBuilderV2` (sig type 2 / Gnosis-Safe path) and `OrderBuilder`
//! amount math. Verified field-by-field against the installed package source:
//!   order_utils/model/ctf_exchange_v2_typed_data.py, exchange_order_builder_v2.py,
//!   order_builder/builder.py + helpers.py, model/order_data_v2.py, config.py.
//!
//! Nothing here touches the network — it is pure and unit-testable, which is how
//! we pass the signing-validation gate (see `selftest`) before any live order.
use ethers_core::abi::{encode, Token};
use ethers_core::types::{Address, Signature, H256, U256};
use ethers_core::utils::keccak256;
use ethers_signers::{LocalWallet, Signer};

// ── verified constants (py-clob-client-v2) ──────────────────────────────────
pub const DOMAIN_NAME: &str = "Polymarket CTF Exchange";
pub const DOMAIN_VERSION: &str = "2";
pub const ORDER_TYPE_STRING: &str = "Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)";
const DOMAIN_TYPE_STRING: &str =
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)";

// ClobAuth (L1) — different domain, NO verifyingContract.
const CLOB_AUTH_DOMAIN_NAME: &str = "ClobAuthDomain";
const CLOB_AUTH_VERSION: &str = "1";
const CLOB_AUTH_MSG: &str = "This message attests that I control the given wallet";
const CLOB_AUTH_DOMAIN_TYPE: &str = "EIP712Domain(string name,string version,uint256 chainId)";
const CLOB_AUTH_STRUCT_TYPE: &str =
    "ClobAuth(address address,string timestamp,uint256 nonce,string message)";

pub const SIDE_BUY: u8 = 0;
pub const SIDE_SELL: u8 = 1;
pub const SIG_TYPE_GNOSIS_SAFE: u8 = 2;
pub const SIG_TYPE_POLY_1271: u8 = 3;

// Solady ERC-7739 "TypedDataSign" wrapper — POLY_1271 / deposit wallets (sig type
// 3). The full prefix is concatenated with ORDER_TYPE_STRING (no separator).
const SOLADY_TYPE_PREFIX: &str = "TypedDataSign(Order contents,string name,string version,uint256 chainId,address verifyingContract,bytes32 salt)";
const DEPOSIT_WALLET_NAME: &str = "DepositWallet";
const DEPOSIT_WALLET_VERSION: &str = "1";

/// (price_decimals, size_decimals, amount_decimals) keyed by tick size string.
fn round_config(tick: f64) -> (i32, i32, i32) {
    if (tick - 0.1).abs() < 1e-9 {
        (1, 2, 3)
    } else if (tick - 0.001).abs() < 1e-9 {
        (3, 2, 5)
    } else if (tick - 0.0001).abs() < 1e-9 {
        (4, 2, 6)
    } else {
        (2, 2, 4) // 0.01 default
    }
}

// ── rounding helpers (mirror order_builder/helpers.py) ───────────────────────
fn round_down(x: f64, d: i32) -> f64 {
    let m = 10f64.powi(d);
    (x * m).floor() / m
}
fn round_up(x: f64, d: i32) -> f64 {
    let m = 10f64.powi(d);
    (x * m).ceil() / m
}
fn round_normal(x: f64, d: i32) -> f64 {
    let m = 10f64.powi(d);
    (x * m).round_ties_even() / m
}
fn decimal_places(x: f64) -> i32 {
    let s = format!("{x}");
    match s.split_once('.') {
        Some((_, frac)) => frac.trim_end_matches('0').len() as i32,
        None => 0,
    }
}
/// `to_token_decimals`: scale to 6dp integer (pUSD / CTF share units).
fn to_token_decimals(x: f64) -> u128 {
    let mut f = 1e6 * x;
    if decimal_places(f) > 0 {
        f = f.round_ties_even();
    }
    f.trunc() as u128
}

/// True iff `price` is exactly representable at `tick` (a whole multiple of it).
/// 0.999 is representable at tick 0.001/0.0001 but NOT at 0.01 — so we only place
/// when the live tick can hit a target exactly (we never settle for a worse fill).
/// Kept as a validated helper for strategies that require exact-price placement.
#[allow(dead_code)]
pub fn price_representable(price: f64, tick: f64) -> bool {
    if tick <= 0.0 {
        return false;
    }
    let q = price / tick;
    (q - q.round()).abs() < 1e-6
}

/// Snap a desired price to the market's tick (never rounds *up* past the target,
/// so a 0.999 request on a 0.01-tick market becomes 0.99, not 1.00).
pub fn snap_price(price: f64, tick: f64) -> f64 {
    let n = (price / tick + 1e-9).floor();
    let p = n * tick;
    // re-round to the tick's decimals to kill fp dust
    let (pd, _, _) = round_config(tick);
    round_normal(p, pd)
}

/// Limit-BUY (maker_amount = pUSD cost, taker_amount = shares), both 6dp ints.
/// Faithful to `OrderBuilder.get_order_amounts(side=BUY)`.
pub fn buy_amounts(price: f64, size: f64, tick: f64) -> (u128, u128) {
    let (pd, sd, ad) = round_config(tick);
    let raw_price = round_normal(price, pd);
    let raw_taker = round_down(size, sd);
    let mut raw_maker = raw_taker * raw_price;
    if decimal_places(raw_maker) > ad {
        raw_maker = round_up(raw_maker, ad + 4);
        if decimal_places(raw_maker) > ad {
            raw_maker = round_down(raw_maker, ad);
        }
    }
    (to_token_decimals(raw_maker), to_token_decimals(raw_taker))
}

/// Limit-SELL (maker_amount = shares, taker_amount = pUSD), both 6dp ints.
/// Faithful to `OrderBuilder.get_order_amounts(side=SELL)`: maker is the share
/// count, taker is the pUSD proceeds.
pub fn sell_amounts(price: f64, size: f64, tick: f64) -> (u128, u128) {
    let (pd, sd, ad) = round_config(tick);
    let raw_price = round_normal(price, pd);
    let raw_maker = round_down(size, sd);
    let mut raw_taker = raw_maker * raw_price;
    if decimal_places(raw_taker) > ad {
        raw_taker = round_up(raw_taker, ad + 4);
        if decimal_places(raw_taker) > ad {
            raw_taker = round_down(raw_taker, ad);
        }
    }
    (to_token_decimals(raw_maker), to_token_decimals(raw_taker))
}

// ── EIP-712 hashing ──────────────────────────────────────────────────────────
fn ks(s: &str) -> [u8; 32] {
    keccak256(s.as_bytes())
}

fn order_domain_separator(verifying: Address, chain_id: u64) -> [u8; 32] {
    keccak256(encode(&[
        Token::FixedBytes(ks(DOMAIN_TYPE_STRING).to_vec()),
        Token::FixedBytes(ks(DOMAIN_NAME).to_vec()),
        Token::FixedBytes(ks(DOMAIN_VERSION).to_vec()),
        Token::Uint(U256::from(chain_id)),
        Token::Address(verifying),
    ]))
}

/// The 11-field V2 Order struct hash.
#[allow(clippy::too_many_arguments)]
fn order_struct_hash(
    salt: u128,
    maker: Address,
    signer: Address,
    token_id: U256,
    maker_amount: u128,
    taker_amount: u128,
    side: u8,
    sig_type: u8,
    timestamp: u128,
) -> [u8; 32] {
    keccak256(encode(&[
        Token::FixedBytes(ks(ORDER_TYPE_STRING).to_vec()),
        Token::Uint(U256::from(salt)),
        Token::Address(maker),
        Token::Address(signer),
        Token::Uint(token_id),
        Token::Uint(U256::from(maker_amount)),
        Token::Uint(U256::from(taker_amount)),
        Token::Uint(U256::from(side)),
        Token::Uint(U256::from(sig_type)),
        Token::Uint(U256::from(timestamp)),
        Token::FixedBytes(vec![0u8; 32]), // metadata
        Token::FixedBytes(vec![0u8; 32]), // builder
    ]))
}

fn eip712_digest(domain_sep: [u8; 32], struct_hash: [u8; 32]) -> [u8; 32] {
    let mut buf = Vec::with_capacity(66);
    buf.extend_from_slice(&[0x19, 0x01]);
    buf.extend_from_slice(&domain_sep);
    buf.extend_from_slice(&struct_hash);
    keccak256(buf)
}

/// All fields needed to build + sign one order.
pub struct OrderInput {
    pub salt: u128,
    pub maker: Address,      // funder / Safe
    pub signer: Address,     // signing EOA (sig type 2)
    pub token_id: U256,
    pub maker_amount: u128,  // 6dp pUSD
    pub taker_amount: u128,  // 6dp shares
    pub side: u8,            // BUY=0
    pub sig_type: u8,        // 2
    pub timestamp_ms: u128,
}

/// Compute the EIP-712 order hash (0x… 32-byte) for the given exchange.
pub fn order_hash(o: &OrderInput, verifying: Address, chain_id: u64) -> [u8; 32] {
    let ds = order_domain_separator(verifying, chain_id);
    let sh = order_struct_hash(
        o.salt, o.maker, o.signer, o.token_id, o.maker_amount, o.taker_amount, o.side,
        o.sig_type, o.timestamp_ms,
    );
    eip712_digest(ds, sh)
}

/// Sign the order. sig type 0/1/2 sign the plain EIP-712 order digest (the owner
/// EOA path); sig type 3 (POLY_1271 deposit wallet) uses the Solady ERC-7739
/// nested-signature scheme. Returns the 0x-prefixed signature exactly as
/// py-clob-client-v2 would produce.
pub fn sign_order(
    wallet: &LocalWallet,
    o: &OrderInput,
    verifying: Address,
    chain_id: u64,
) -> anyhow::Result<String> {
    if o.sig_type == SIG_TYPE_POLY_1271 {
        return sign_order_1271(wallet, o, verifying, chain_id);
    }
    let digest = order_hash(o, verifying, chain_id);
    let sig: Signature = wallet.sign_hash(H256::from(digest))?;
    Ok(format!("0x{}", hex::encode(sig.to_vec())))
}

/// POLY_1271 (sig type 3 / deposit wallet) signature — a Solady ERC-7739
/// "TypedDataSign" nested EIP-712 signature. The order's `maker == signer ==
/// funder` (the deposit wallet); the controlling EOA `wallet` produces the inner
/// ECDSA sig over the wrapped digest, then the Solady suffix is appended. Faithful
/// to py-clob-client-v2 `ExchangeOrderBuilderV2._build_poly_1271_order_signature`.
pub fn sign_order_1271(
    wallet: &LocalWallet,
    o: &OrderInput,
    verifying: Address,
    chain_id: u64,
) -> anyhow::Result<String> {
    // 1. contents hash = the standard 11-field Order struct hash.
    let contents_hash = order_struct_hash(
        o.salt, o.maker, o.signer, o.token_id, o.maker_amount, o.taker_amount, o.side,
        o.sig_type, o.timestamp_ms,
    );
    // 2. App (CTF Exchange) domain separator — what the final digest is sealed under.
    let app_sep = order_domain_separator(verifying, chain_id);
    // 3. TypedDataSign struct hash, carrying the *DepositWallet* domain fields.
    let solady_type_hash = {
        let mut s = String::from(SOLADY_TYPE_PREFIX);
        s.push_str(ORDER_TYPE_STRING);
        keccak256(s.as_bytes())
    };
    let tds_hash = keccak256(encode(&[
        Token::FixedBytes(solady_type_hash.to_vec()),
        Token::FixedBytes(contents_hash.to_vec()),
        Token::FixedBytes(ks(DEPOSIT_WALLET_NAME).to_vec()),
        Token::FixedBytes(ks(DEPOSIT_WALLET_VERSION).to_vec()),
        Token::Uint(U256::from(chain_id)),
        Token::Address(o.signer), // deposit wallet = the 1271 verifying contract
        Token::FixedBytes(vec![0u8; 32]), // DepositWallet domain salt = bytes32(0)
    ]));
    // 4. Final digest: keccak(0x1901 || app_domain_separator || tds_hash).
    let digest = eip712_digest(app_sep, tds_hash);
    // 5. Raw-hash ECDSA sign with the controlling EOA (v in {27,28}).
    let sig: Signature = wallet.sign_hash(H256::from(digest))?;
    // 6. Append the Solady ERC-7739 suffix.
    let type_str = ORDER_TYPE_STRING.as_bytes();
    let mut out = Vec::with_capacity(65 + 32 + 32 + type_str.len() + 2);
    out.extend_from_slice(&sig.to_vec()); // 65: r || s || v
    out.extend_from_slice(&app_sep); // 32: app domain separator
    out.extend_from_slice(&contents_hash); // 32: order contents hash
    out.extend_from_slice(type_str); // N: ORDER_TYPE_STRING (ASCII)
    out.extend_from_slice(&(type_str.len() as u16).to_be_bytes()); // 2: uint16 big-endian
    Ok(format!("0x{}", hex::encode(out)))
}

// ── L1 ClobAuth signing (derive/create API key) ─────────────────────────────
fn clob_auth_domain_separator(chain_id: u64) -> [u8; 32] {
    keccak256(encode(&[
        Token::FixedBytes(ks(CLOB_AUTH_DOMAIN_TYPE).to_vec()),
        Token::FixedBytes(ks(CLOB_AUTH_DOMAIN_NAME).to_vec()),
        Token::FixedBytes(ks(CLOB_AUTH_VERSION).to_vec()),
        Token::Uint(U256::from(chain_id)),
    ]))
}

/// Sign the ClobAuth message → 0x-prefixed signature for the L1 POLY_SIGNATURE.
pub fn sign_clob_auth(
    wallet: &LocalWallet,
    address: Address,
    timestamp_secs: u64,
    nonce: u64,
    chain_id: u64,
) -> anyhow::Result<String> {
    let struct_hash = keccak256(encode(&[
        Token::FixedBytes(ks(CLOB_AUTH_STRUCT_TYPE).to_vec()),
        Token::Address(address),
        Token::FixedBytes(ks(&timestamp_secs.to_string()).to_vec()),
        Token::Uint(U256::from(nonce)),
        Token::FixedBytes(ks(CLOB_AUTH_MSG).to_vec()),
    ]));
    let digest = eip712_digest(clob_auth_domain_separator(chain_id), struct_hash);
    let sig: Signature = wallet.sign_hash(H256::from(digest))?;
    Ok(format!("0x{}", hex::encode(sig.to_vec())))
}

/// Generate a salt the same way as py-clob-client-v2: floor(rand[0,1) * now_ms).
pub fn generate_salt(now_ms: u128) -> u128 {
    (rand::random::<f64>() * now_ms as f64) as u128
}

/// Deterministic signing self-test. Prints the order hash + signature for a
/// FIXED input so the exact same inputs can be run through py-clob-client-v2 and
/// compared byte-for-byte. Pass the gate before flipping LIVE_SIGNING_VALIDATED.
pub fn selftest(private_key: &str, funder: &str) -> anyhow::Result<()> {
    let wallet: LocalWallet = private_key.trim_start_matches("0x").parse()?;
    let signer = wallet.address();
    let maker: Address = funder.parse().unwrap_or(signer);

    // Fixed reference vector (no randomness, no clock):
    let token_id = U256::from_dec_str(
        "48331043336612883890938759509493159234755048973500640148014422747788308965732",
    )?;
    let (maker_amount, taker_amount) = buy_amounts(0.999, 5.0, 0.001);
    let o = OrderInput {
        salt: 479249096354,
        maker,
        signer,
        token_id,
        maker_amount,
        taker_amount,
        side: SIDE_BUY,
        sig_type: SIG_TYPE_GNOSIS_SAFE,
        timestamp_ms: 1700000000000,
    };

    for (label, addr) in [
        ("exchange_v2", "0xE111180000d2663C0091e4f400237545B87B996B"),
        ("neg_risk_exchange_v2", "0xe2222d279d744050d28e00520010520000310F59"),
    ] {
        let verifying: Address = addr.parse()?;
        let h = order_hash(&o, verifying, 137);
        let sig = sign_order(&wallet, &o, verifying, 137)?;
        println!("[{label}] verifyingContract={addr}");
        println!("  order_hash = 0x{}", hex::encode(h));
        println!("  signature  = {sig}");
    }
    println!("signer (EOA)      = {signer:?}");
    println!("maker  (funder)   = {maker:?}");
    println!("makerAmount={maker_amount}  takerAmount={taker_amount}  (BUY 5 @ 0.999, tick 0.001)");
    println!("salt=479249096354 timestamp_ms=1700000000000 side=0 sigType=2");

    // ── signatureType 3 (POLY_1271 deposit wallet): maker == signer == funder ──
    let o3 = OrderInput {
        salt: 479249096354,
        maker,
        signer: maker, // deposit wallet (maker == signer)
        token_id,
        maker_amount,
        taker_amount,
        side: SIDE_BUY,
        sig_type: SIG_TYPE_POLY_1271,
        timestamp_ms: 1700000000000,
    };
    println!("\n--- signatureType 3 (POLY_1271 deposit wallet; maker=signer=funder) ---");
    for (label, addr) in [
        ("exchange_v2", "0xE111180000d2663C0091e4f400237545B87B996B"),
        ("neg_risk_exchange_v2", "0xe2222d279d744050d28e00520010520000310F59"),
    ] {
        let verifying: Address = addr.parse()?;
        let sig = sign_order_1271(&wallet, &o3, verifying, 137)?;
        println!("[{label}] sigType3 signature = {sig}");
    }
    println!("\nRun the SAME fixed inputs through py-clob-client-v2 ExchangeOrderBuilderV2");
    println!("(build_order_hash / build_order_signature, types 2 AND 3) and confirm all match.");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn amounts_999() {
        // BUY 5 shares @ 0.999, tick 0.001 -> maker 4995000, taker 5000000
        assert_eq!(buy_amounts(0.999, 5.0, 0.001), (4_995_000, 5_000_000));
        // BUY 38 @ 0.999 -> 37962000 / 38000000
        assert_eq!(buy_amounts(0.999, 38.0, 0.001), (37_962_000, 38_000_000));
    }

    #[test]
    fn sell_amounts_match_py_clob_client_v2() {
        // Reference values produced by py-clob-client-v2 OrderBuilder
        // get_order_amounts(Side.SELL, ...). maker=shares, taker=pUSD (6dp).
        assert_eq!(sell_amounts(0.51, 50.0, 0.01), (50_000_000, 25_500_000));
        assert_eq!(sell_amounts(0.999, 38.0, 0.001), (38_000_000, 37_962_000));
        assert_eq!(sell_amounts(0.33, 100.0, 0.01), (100_000_000, 33_000_000));
        assert_eq!(sell_amounts(0.985, 211.3, 0.001), (211_300_000, 208_130_500));
        assert_eq!(sell_amounts(0.999, 5.0, 0.001), (5_000_000, 4_995_000));
    }

    #[test]
    fn representable() {
        assert!(price_representable(0.999, 0.001));
        assert!(price_representable(0.999, 0.0001));
        assert!(!price_representable(0.999, 0.01)); // <- the key case: skip these
        assert!(price_representable(0.99, 0.01));
        assert!(price_representable(1.0, 0.01));
    }

    #[test]
    fn snap_to_tick() {
        assert!((snap_price(0.999, 0.001) - 0.999).abs() < 1e-9);
        assert!((snap_price(0.999, 0.01) - 0.99).abs() < 1e-9); // 0.999 invalid on 0.01 tick
        assert!((snap_price(1.0, 0.01) - 1.0).abs() < 1e-9);
    }

    #[test]
    fn poly_1271_signature_matches_py_clob_client_v2() {
        // Type-3 (POLY_1271 deposit wallet) signature, validated byte-for-byte
        // against py-clob-client-v2 ExchangeOrderBuilderV2._build_poly_1271_order_signature
        // for this fixed vector (Hardhat key #0; maker==signer==funder).
        let wallet: LocalWallet =
            "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80".parse().unwrap();
        let funder: Address = "0xCa6b93ad9ec941ec69FF9fC7A97859A9a442ff01".parse().unwrap();
        let o = OrderInput {
            salt: 479249096354,
            maker: funder,
            signer: funder, // maker == signer == funder for type 3
            token_id: U256::from_dec_str(
                "48331043336612883890938759509493159234755048973500640148014422747788308965732",
            )
            .unwrap(),
            maker_amount: 4_995_000,
            taker_amount: 5_000_000,
            side: SIDE_BUY,
            sig_type: SIG_TYPE_POLY_1271,
            timestamp_ms: 1700000000000,
        };
        let v: Address = "0xE111180000d2663C0091e4f400237545B87B996B".parse().unwrap();
        let sig = sign_order_1271(&wallet, &o, v, 137).unwrap();
        assert_eq!(sig, "0x84883bb17d94e62bda68b7ea90484edcb38f352efa1c74c2493216883ea1049f41629e36fc476c723ea9be9b191c109c0e52666059cb71a45985c38ba25f10261b3264e159346253e26a64e00b69032db0e7d32f94628de3e6eecb50304d7af3d26fbc2d714b3f258bc77896fe86ef46081972aea73e842b9d54734bb84e7462014f726465722875696e743235362073616c742c61646472657373206d616b65722c61646472657373207369676e65722c75696e7432353620746f6b656e49642c75696e74323536206d616b6572416d6f756e742c75696e743235362074616b6572416d6f756e742c75696e743820736964652c75696e7438207369676e6174757265547970652c75696e743235362074696d657374616d702c62797465733332206d657461646174612c62797465733332206275696c6465722900ba");
    }

    #[test]
    fn hash_is_stable() {
        // Same input => same hash (sanity; not the cross-impl gate).
        let maker: Address = "0x1111111111111111111111111111111111111111".parse().unwrap();
        let o = OrderInput {
            salt: 1,
            maker,
            signer: maker,
            token_id: U256::from(123u64),
            maker_amount: 4_995_000,
            taker_amount: 5_000_000,
            side: SIDE_BUY,
            sig_type: SIG_TYPE_GNOSIS_SAFE,
            timestamp_ms: 1700000000000,
        };
        let v: Address = "0xE111180000d2663C0091e4f400237545B87B996B".parse().unwrap();
        assert_eq!(order_hash(&o, v, 137), order_hash(&o, v, 137));
    }
}
