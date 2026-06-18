//! Lightweight persistence for the copytrader: the seen-trade-id dedup set and
//! the brownfox per-market state. Pure-Rust JSON files (no SQLite / no C dep);
//! the data volume is tiny (a few hundred ids + a handful of markets).
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tracing::warn;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BrownfoxRecord {
    pub token_id: String,
    pub buy_price: f64,
    pub sell_price: f64,
    pub status: String, // ACTIVE / EXITING / DONE / ABORTED / STUCK
    #[serde(default)]
    pub title: String, // human-readable market name (default keeps old files loadable)
    #[serde(default)]
    pub placed_at_ms: u64, // wall-clock ms our buy was placed (99c stale-cancel across restarts; 0 = untracked)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TradeLog {
    pub time: String,
    pub market: String,
    pub title: String,
    pub side: String,   // BUY / SELL
    pub size: f64,
    pub price: f64,
    pub status: String, // FILLED / RESTING / SKIPPED / FAILED
}

pub struct Store {
    dir: PathBuf,
    seen: Mutex<HashSet<String>>,
    brownfox: Mutex<HashMap<String, BrownfoxRecord>>,
    /// 99c mode per-market records (same shape as brownfox; sell_price unused).
    ninetynine: Mutex<HashMap<String, BrownfoxRecord>>,
    /// sell-with-target mode per-market records (separate file; sell_price unused).
    sellwithtarget: Mutex<HashMap<String, BrownfoxRecord>>,
    /// market_id -> token_id for markets the general replicator has copied
    /// (drives MAX_POSITIONS counting + the auto-sell token set).
    copied: Mutex<HashMap<String, String>>,
    /// recent copy trades for the dashboard (newest first, bounded).
    trades: Mutex<Vec<TradeLog>>,
}

impl Store {
    pub fn open(dir: &str) -> Self {
        let dir = PathBuf::from(dir);
        let _ = std::fs::create_dir_all(&dir);
        let seen: HashSet<String> = read_json(&dir.join("seen.json")).unwrap_or_default();
        let brownfox: HashMap<String, BrownfoxRecord> =
            read_json(&dir.join("brownfox.json")).unwrap_or_default();
        let ninetynine: HashMap<String, BrownfoxRecord> =
            read_json(&dir.join("ninetynine.json")).unwrap_or_default();
        let sellwithtarget: HashMap<String, BrownfoxRecord> =
            read_json(&dir.join("sellwithtarget.json")).unwrap_or_default();
        let copied: HashMap<String, String> =
            read_json(&dir.join("copied.json")).unwrap_or_default();
        let trades: Vec<TradeLog> = read_json(&dir.join("trades.json")).unwrap_or_default();
        Store {
            dir,
            seen: Mutex::new(seen),
            brownfox: Mutex::new(brownfox),
            ninetynine: Mutex::new(ninetynine),
            sellwithtarget: Mutex::new(sellwithtarget),
            copied: Mutex::new(copied),
            trades: Mutex::new(trades),
        }
    }

    // ── recent copy trades (for the dashboard) ────────────────────────────────
    pub fn record_trade(&self, t: TradeLog) {
        let mut v = self.trades.lock().unwrap();
        v.insert(0, t);
        v.truncate(200);
        write_json(&self.dir.join("trades.json"), &*v);
    }
    pub fn recent_trades(&self) -> Vec<TradeLog> {
        self.trades.lock().unwrap().clone()
    }

    // ── copied markets (general replicator) ───────────────────────────────────
    pub fn mark_copied(&self, market: &str, token: &str) {
        let mut c = self.copied.lock().unwrap();
        c.insert(market.to_string(), token.to_string());
        write_json(&self.dir.join("copied.json"), &*c);
    }
    pub fn has_copied_market(&self, market: &str) -> bool {
        self.copied.lock().unwrap().contains_key(market)
    }
    pub fn copied_count(&self) -> usize {
        self.copied.lock().unwrap().len()
    }
    pub fn copied_tokens(&self) -> HashSet<String> {
        self.copied.lock().unwrap().values().cloned().collect()
    }

    // ── dedup ───────────────────────────────────────────────────────────────
    pub fn seen(&self, id: &str) -> bool {
        self.seen.lock().unwrap().contains(id)
    }
    pub fn mark_seen(&self, id: &str) {
        let mut s = self.seen.lock().unwrap();
        if s.insert(id.to_string()) {
            // Keep the file bounded (newest ~5000 ids is plenty for dedup).
            if s.len() > 5000 {
                let drop: Vec<String> = s.iter().take(s.len() - 5000).cloned().collect();
                for d in drop {
                    s.remove(&d);
                }
            }
            write_json(&self.dir.join("seen.json"), &*s);
        }
    }

    // ── brownfox state ────────────────────────────────────────────────────────
    pub fn brownfox_markets(&self) -> HashSet<String> {
        self.brownfox.lock().unwrap().keys().cloned().collect()
    }
    pub fn record_brownfox(&self, market: &str, rec: BrownfoxRecord) {
        let mut m = self.brownfox.lock().unwrap();
        m.entry(market.to_string()).or_insert(rec);
        write_json(&self.dir.join("brownfox.json"), &*m);
    }
    pub fn update_brownfox_status(&self, market: &str, status: &str) {
        let mut m = self.brownfox.lock().unwrap();
        if let Some(r) = m.get_mut(market) {
            r.status = status.to_string();
            write_json(&self.dir.join("brownfox.json"), &*m);
        }
    }
    pub fn delete_brownfox(&self, market: &str) {
        let mut m = self.brownfox.lock().unwrap();
        if m.remove(market).is_some() {
            write_json(&self.dir.join("brownfox.json"), &*m);
        }
    }
    /// Markets not yet terminal (for restart resume).
    pub fn active_brownfox(&self) -> Vec<(String, BrownfoxRecord)> {
        self.brownfox
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, r)| r.status != "DONE" && r.status != "ABORTED")
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect()
    }

    // ── 99c mode state (same shape; separate file so modes never collide) ──────
    pub fn ninetynine_markets(&self) -> HashSet<String> {
        self.ninetynine.lock().unwrap().keys().cloned().collect()
    }
    pub fn record_99c(&self, market: &str, rec: BrownfoxRecord) {
        let mut m = self.ninetynine.lock().unwrap();
        m.entry(market.to_string()).or_insert(rec);
        write_json(&self.dir.join("ninetynine.json"), &*m);
    }
    pub fn update_99c_status(&self, market: &str, status: &str) {
        let mut m = self.ninetynine.lock().unwrap();
        if let Some(r) = m.get_mut(market) {
            r.status = status.to_string();
            write_json(&self.dir.join("ninetynine.json"), &*m);
        }
    }
    pub fn delete_99c(&self, market: &str) {
        let mut m = self.ninetynine.lock().unwrap();
        if m.remove(market).is_some() {
            write_json(&self.dir.join("ninetynine.json"), &*m);
        }
    }
    /// 99c markets still being managed (PENDING). FILLED/CANCELLED are terminal
    /// but kept in the file so the market is never re-bought.
    pub fn active_99c(&self) -> Vec<(String, BrownfoxRecord)> {
        self.ninetynine
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, r)| r.status != "FILLED" && r.status != "CANCELLED")
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect()
    }

    // ── sell-with-target mode state (separate file) ───────────────────────────
    pub fn swt_markets(&self) -> HashSet<String> {
        self.sellwithtarget.lock().unwrap().keys().cloned().collect()
    }
    pub fn record_swt(&self, market: &str, rec: BrownfoxRecord) {
        let mut m = self.sellwithtarget.lock().unwrap();
        m.entry(market.to_string()).or_insert(rec);
        write_json(&self.dir.join("sellwithtarget.json"), &*m);
    }
    pub fn update_swt_status(&self, market: &str, status: &str) {
        let mut m = self.sellwithtarget.lock().unwrap();
        if let Some(r) = m.get_mut(market) {
            r.status = status.to_string();
            write_json(&self.dir.join("sellwithtarget.json"), &*m);
        }
    }
    pub fn delete_swt(&self, market: &str) {
        let mut m = self.sellwithtarget.lock().unwrap();
        if m.remove(market).is_some() {
            write_json(&self.dir.join("sellwithtarget.json"), &*m);
        }
    }
    /// Resume set = ACTIVE or EXITING only. DONE/ABORTED/STUCK are TERMINAL (kept
    /// in the file so the market is never re-bought, but never reloaded as active
    /// — STUCK is NOT auto-resumed, to avoid a duplicate racing exit worker).
    pub fn active_swt(&self) -> Vec<(String, BrownfoxRecord)> {
        self.sellwithtarget
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, r)| r.status == "ACTIVE" || r.status == "EXITING")
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect()
    }
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &PathBuf) -> Option<T> {
    let bytes = std::fs::read(path).ok()?;
    serde_json::from_slice(&bytes).ok()
}

fn write_json<T: Serialize>(path: &PathBuf, val: &T) {
    match serde_json::to_vec_pretty(val) {
        Ok(bytes) => {
            if let Err(e) = std::fs::write(path, bytes) {
                warn!("store: write {} failed: {e}", path.display());
            }
        }
        Err(e) => warn!("store: serialize failed: {e}"),
    }
}
