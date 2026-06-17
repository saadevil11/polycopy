//! Shared, lock-free bot state. One instance is cloned into every task
//! (discovery, data feed, strategy, dashboard). DashMap gives concurrent
//! per-key access without a global lock.
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use chrono::{DateTime, Utc};
use dashmap::mapref::entry::Entry;
use dashmap::DashMap;

use crate::models::{BookTop, OrderStatus, OutcomeToken, Position, RestingOrder};

#[derive(Clone)]
pub struct BotState {
    inner: Arc<Inner>,
}

pub struct Inner {
    /// token_id -> the tradable token we monitor
    pub tokens: DashMap<String, OutcomeToken>,
    /// token_id -> latest top-of-book
    pub books: DashMap<String, BookTop>,
    /// token_id -> latest LIVE tick size (from CLOB book / tick_size_change).
    /// Polymarket tightens the tick toward 0.001 as a side nears 1.00, so this
    /// can differ from Gamma's static `orderPriceMinTickSize`.
    pub live_ticks: DashMap<String, f64>,
    /// order_id -> resting/placed order (incl. dry-run logged orders)
    pub orders: DashMap<String, RestingOrder>,
    /// token_id -> true once we've placed (dedup so we fire once per token)
    pub triggered: DashMap<String, bool>,
    /// token_id -> open position (held to resolution)
    pub positions: DashMap<String, Position>,

    pub orders_placed: AtomicU64,
    pub markets_seen: AtomicU64,
    pub started_at: DateTime<Utc>,
}

impl BotState {
    pub fn new(started_at: DateTime<Utc>) -> Self {
        BotState {
            inner: Arc::new(Inner {
                tokens: DashMap::new(),
                books: DashMap::new(),
                live_ticks: DashMap::new(),
                orders: DashMap::new(),
                triggered: DashMap::new(),
                positions: DashMap::new(),
                orders_placed: AtomicU64::new(0),
                markets_seen: AtomicU64::new(0),
                started_at,
            }),
        }
    }

    /// Insert/update a monitored token. New token_ids bump `markets_seen`.
    pub fn upsert_token(&self, t: OutcomeToken) {
        match self.tokens.entry(t.token_id.clone()) {
            Entry::Occupied(mut o) => {
                o.insert(t);
            }
            Entry::Vacant(v) => {
                self.markets_seen.fetch_add(1, Ordering::Relaxed);
                v.insert(t);
            }
        }
    }

    pub fn set_book(&self, token_id: &str, top: BookTop) {
        self.books.insert(token_id.to_string(), top);
    }

    pub fn set_tick(&self, token_id: &str, tick: f64) {
        if tick > 0.0 {
            self.live_ticks.insert(token_id.to_string(), tick);
        }
    }

    /// Best-known tick for a token: the live CLOB tick if seen, else the token's
    /// Gamma `tick_size`, else 0.01.
    pub fn effective_tick(&self, token_id: &str) -> f64 {
        if let Some(t) = self.live_ticks.get(token_id) {
            return *t;
        }
        self.tokens
            .get(token_id)
            .map(|t| t.tick_size)
            .filter(|t| *t > 0.0)
            .unwrap_or(0.01)
    }

    /// Atomically claim a token for a one-shot order. Returns true the first
    /// time only, so the strategy never double-fires on the same token.
    pub fn mark_triggered(&self, token_id: &str) -> bool {
        match self.triggered.entry(token_id.to_string()) {
            Entry::Occupied(_) => false,
            Entry::Vacant(v) => {
                v.insert(true);
                true
            }
        }
    }

    /// Undo a trigger claim (used when an order placement fails, so we retry).
    pub fn clear_triggered(&self, token_id: &str) {
        self.triggered.remove(token_id);
    }

    pub fn record_order(&self, o: RestingOrder) {
        self.orders_placed.fetch_add(1, Ordering::Relaxed);
        self.orders.insert(o.order_id.clone(), o);
    }

    pub fn mark_order_cancelled(&self, order_id: &str) {
        if let Some(mut o) = self.orders.get_mut(order_id) {
            o.status = OrderStatus::Cancelled;
        }
    }

    pub fn upsert_position(&self, p: Position) {
        self.positions.insert(p.token_id.clone(), p);
    }

    /// Drop tokens whose market has already ended (with a small grace window),
    /// plus their book/trigger state, so the maps don't grow without bound.
    pub fn prune_expired(&self, now: DateTime<Utc>) {
        let grace = chrono::Duration::seconds(90);
        let dead: Vec<String> = self
            .tokens
            .iter()
            .filter(|e| e.value().end_time.map(|t| t + grace < now).unwrap_or(false))
            .map(|e| e.key().clone())
            .collect();
        for tid in dead {
            self.tokens.remove(&tid);
            self.books.remove(&tid);
            self.live_ticks.remove(&tid);
            // keep `triggered` and any resulting position/order for the record
        }
    }
}

impl std::ops::Deref for BotState {
    type Target = Inner;
    fn deref(&self) -> &Inner {
        &self.inner
    }
}
