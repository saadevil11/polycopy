"""
Brownfox mode.

A distinct trading style for a specific target:

- ONE fixed-USD buy per market (conditionId). We copy only the target's FIRST
  buy in a market and ignore all their scale-ins. The guard is persisted BEFORE
  the order is placed, so a crash can never lose it (no restart double-buy).
- We buy at the target's EXACT price (no slippage), for a constant USD size
  (e.g. $50). The buy RESTS until filled.
- We keep resting SELL orders at buy + markup (+1 cent) that, in total, cover all
  the shares our buy has acquired - sized off the buy order's matched amount
  (monotonic, never rotates) and hard-capped by fresh on-chain holdings, so we
  can never place sells for more than we hold.
- Once selling starts (price reaches our +1c, or our holdings drop), we cancel
  the rest of the unfilled buy (only when the cancel is CONFIRMED).
- If the target exits the market BEFORE our buy fills at all, we cancel the buy
  (re-checking holdings after the cancel in case it filled during the race).
- If the target sells BELOW our +1c, we cancel our sells + buy and MARKET-SELL
  everything we hold (guaranteed, retried, never over-selling). At/above +1c, the
  resting sell handles the exit.

State is rebuilt on restart from the persisted row + live exchange state (open
orders + holdings), so an in-flight position resumes rather than orphaning.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from loguru import logger

from src.core.models import TraderTrade, TradeSide


@dataclass
class BrownfoxPos:
    market_id: str
    token_id: str
    buy_price: float
    sell_price: float
    status: str = "ACTIVE"              # ACTIVE, EXITING, DONE, ABORTED, STUCK
    buy_order_id: Optional[str] = None
    buy_size: float = 0.0               # total shares the buy was placed for
    buy_done: bool = False              # buy filled or confirmed-cancelled
    bought: float = 0.0                 # shares acquired (buy matched, monotonic)
    peak_holdings: float = 0.0
    exit_attempts: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrownfoxManager:
    EPS = 0.01
    MIN_SHARES = 5.0

    def __init__(self, client, database, config):
        self.client = client
        self.db = database
        self.config = config
        self.positions: Dict[str, BrownfoxPos] = {}
        self.entered: set = set()
        self._entered_lock = asyncio.Lock()
        # Cache of the target's currently-held tokens (to detect their exit even
        # if we miss the sell event). Refreshed at most every ~15s.
        self._target_tokens: Optional[set] = None
        self._target_tokens_at: float = 0.0

    # ---- startup -----------------------------------------------------------

    def load(self):
        """Restore the one-buy guard AND rebuild in-flight positions so the
        reconcile loop can resume them (re-discovering live orders/holdings)."""
        try:
            self.entered = self.db.get_brownfox_markets()
        except Exception as e:
            logger.warning(f"Brownfox: could not load entered markets: {e}")
            self.entered = set()
        rows = []
        try:
            rows = self.db.get_active_brownfox_positions()
        except Exception as e:
            logger.warning(f"Brownfox: could not read active positions: {e}")
        for row in rows:
            try:
                m = row.get('market_id')
                if not m:
                    continue
                pos = BrownfoxPos(
                    market_id=m, token_id=row.get('token_id') or '',
                    buy_price=float(row.get('buy_price') or 0),
                    sell_price=float(row.get('sell_price') or 0),
                    status=row.get('status') or 'ACTIVE',
                )
                self._rehydrate(pos)
                # Always register the position so the reconcile loop manages it,
                # even if _rehydrate hit an error (it self-heals from live state).
                self.positions[m] = pos
            except Exception as e:
                logger.warning(f"Brownfox: could not rebuild position {row.get('market_id')}: {e}")
        logger.info(f"🦊 Brownfox: loaded {len(self.entered)} entered markets, "
                    f"resumed {len(self.positions)} active positions")

    def _rehydrate(self, pos: BrownfoxPos):
        """Re-discover a position's live state from the exchange on restart."""
        # The original buy size is the fixed share count (independent of price),
        # so 'buy fully filled' can still be inferred after a restart.
        pos.buy_size = max(self.MIN_SHARES, round(float(self.config.brownfox_trade_size_shares), 2))
        try:
            buy_oid, buy_matched = self._find_order(pos.token_id, TradeSide.BUY, pos.buy_price)
            resting_sells = self._resting_sell_size(pos.token_id, pos.sell_price)
            holdings = self._holdings(pos.token_id)
            if buy_oid:
                pos.buy_order_id = buy_oid
                pos.bought = max(buy_matched, holdings + resting_sells)
            else:
                pos.buy_done = True
                pos.bought = holdings + resting_sells  # everything we still have / have up for sale
            # peak_holdings is ONLY the owned balance - resting sells are not a
            # drop from peak. Counting them would falsely trigger 'selling
            # started' on the first reconcile and cancel a still-needed buy.
            pos.peak_holdings = holdings
            # A STUCK position from before a restart gets a fresh exit attempt.
            if pos.status == "STUCK":
                pos.status = "EXITING"
                pos.exit_attempts = 0
        except Exception as e:
            logger.debug(f"Brownfox rehydrate error for {pos.market_id[:10]}: {e}")

    # ---- event entry point -------------------------------------------------

    async def on_event(self, trade: TraderTrade):
        try:
            if trade.side == TradeSide.BUY:
                await self.on_target_buy(trade)
            else:
                await self.on_target_sell(trade)
        except Exception as e:
            logger.error(f"🦊 Brownfox on_event error: {e}")

    # ---- buy ---------------------------------------------------------------

    async def on_target_buy(self, trade: TraderTrade):
        market, token = trade.market_id, trade.token_id
        if not market or not token:
            return
        async with self._entered_lock:
            if market in self.entered or market in self.positions:
                logger.info(f"🦊 Brownfox: already took our one buy in market {market[:10]} - ignoring")
                return
            self.entered.add(market)

        tick = self.client._get_tick_size(token)
        buy_price = self.client._round_to_tick(trade.price, tick)
        max_price = round(1.0 - tick, 10)
        if buy_price <= 0 or buy_price >= 1.0:
            async with self._entered_lock:
                self.entered.discard(market)
            return

        # Sell price must be STRICTLY above the buy price (at least 1 tick) so the
        # +markup actually makes a profit. A markup smaller than the tick, or a
        # buy price near the cap, can otherwise snap the sell back to the buy
        # price (no markup, and the 'selling started' trigger would fire at once).
        markup = max(self.config.brownfox_sell_markup, tick)
        sell_price = round(self.client._round_to_tick(buy_price + markup, tick), 10)
        if sell_price <= buy_price + 1e-9:
            sell_price = round(buy_price + tick, 10)
        if sell_price > max_price + 1e-9:
            logger.info(f"🦊 Brownfox: buy @ ${buy_price:.4f} too high to place a +markup sell within "
                        f"valid range (cap ${max_price:.4f}) - skipping market {market[:10]}")
            async with self._entered_lock:
                self.entered.discard(market)
            return

        # Fixed SHARE count per market (not a USD amount).
        size = round(float(self.config.brownfox_trade_size_shares), 2)
        if size < self.MIN_SHARES:
            size = self.MIN_SHARES

        # Persist the reservation + state BEFORE placing the order, so a crash
        # between place and persist can never lose the guard (no double-buy).
        self.db.record_brownfox_market(market, token, buy_price, sell_price)
        pos = BrownfoxPos(market_id=market, token_id=token, buy_price=buy_price,
                          sell_price=sell_price, buy_size=size)
        self.positions[market] = pos

        logger.info(f"🦊 Brownfox: target bought {market[:10]} @ ${trade.price:.4f} -> "
                    f"buy {size:.2f} shares @ exact ${buy_price:.4f} (sell @ ${sell_price:.4f})")
        order_id = self.client.place_limit_order(token, TradeSide.BUY, size, buy_price)
        if order_id:
            pos.buy_order_id = order_id
            logger.success(f"🦊 Brownfox: buy resting {order_id} @ ${buy_price:.4f}")
            return

        # Placement returned None: it may be a true rejection OR a lost response
        # with the order actually live. Look for our resting buy before giving up.
        found_oid, _ = self._find_order(token, TradeSide.BUY, buy_price)
        if found_oid:
            pos.buy_order_id = found_oid
            logger.warning(f"🦊 Brownfox: placement returned None but found resting buy {found_oid} - adopting")
            return
        # No order placed and none resting -> the buy genuinely failed. Release
        # the reservation (drop the guard row + in-memory entry) so a later
        # target buy in this market can retry, rather than permanently blocking it.
        logger.error("🦊 Brownfox: buy placement failed and no resting order found - releasing market")
        self.positions.pop(market, None)
        self.db.delete_brownfox_market(market)
        async with self._entered_lock:
            self.entered.discard(market)

    # ---- target sell -------------------------------------------------------

    async def on_target_sell(self, trade: TraderTrade):
        pos = self.positions.get(trade.market_id)
        if not pos:
            return
        async with pos.lock:
            if pos.status in ("DONE", "ABORTED", "STUCK", "EXITING"):
                return
            token = pos.token_id
            self._refresh_bought(pos)
            holdings = self._holdings(token)

            if trade.price >= pos.sell_price - 1e-9:
                # Target sold at/above our +1c -> the resting sell handles it.
                logger.info(f"🦊 Brownfox: target sold @ ${trade.price:.4f} >= our sell ${pos.sell_price:.4f} "
                            f"- resting sell handles it")
                return

            # Target sold BELOW our +1c.
            if holdings < self.EPS and pos.bought < self.EPS:
                # Nothing acquired yet -> cancel the resting buy (target exited
                # before our fill). Re-check holdings after, in case it filled
                # during the cancel race.
                if self._confirm_cancel(pos.buy_order_id, token):
                    pos.buy_done = True
                    if self._holdings(token) < self.EPS:
                        self._set_status(pos, "ABORTED")
                        logger.info(f"🦊 Brownfox: target exited before our buy filled - cancelled buy")
                        return
                # else: couldn't confirm cancel, or shares appeared -> force exit
            logger.info(f"🦊 Brownfox: target sold @ ${trade.price:.4f} BELOW our +mark "
                        f"${pos.sell_price:.4f} - forcing market exit")
            self._set_status(pos, "EXITING")
            await self._forced_exit(pos)

    # ---- reconcile ---------------------------------------------------------

    async def reconcile_once(self):
        for market in list(self.positions.keys()):
            pos = self.positions.get(market)
            if not pos:
                continue
            async with pos.lock:
                if pos.status in ("DONE", "ABORTED", "STUCK"):
                    continue
                try:
                    if pos.status == "EXITING":
                        await self._forced_exit(pos)
                        continue
                    await self._manage(pos)
                except Exception as e:
                    logger.error(f"🦊 Brownfox reconcile error for {market[:10]}: {e}")

    async def _manage(self, pos: BrownfoxPos):
        token = pos.token_id
        holdings = self._holdings(token)
        pos.peak_holdings = max(pos.peak_holdings, holdings)
        self._refresh_bought(pos)

        # The buy is finished once it has fully filled (matched >= size). Don't
        # infer 'done' from a single _find_open_order miss (could be a transient
        # API error) - a confirmed cancel below is the only other way it's done.
        if pos.buy_size and pos.bought >= pos.buy_size - self.EPS:
            pos.buy_done = True

        # Target-exit detection independent of receiving their SELL event (the
        # event can be missed/deduped/lost on restart). If the target no longer
        # holds this token, we never want a live buy here - cancel it first, then
        # resolve our position by how much we hold.
        if not self._target_holds(token):
            if not pos.buy_done:
                if self._confirm_cancel(pos.buy_order_id, token):
                    pos.buy_done = True
            if pos.buy_done:
                bid = self.client._get_best_price(token, TradeSide.SELL)
                if holdings < self.EPS:
                    self._set_status(pos, "ABORTED")
                    logger.info(f"🦊 Brownfox: target left {pos.market_id[:10]} before our buy filled - cancelled")
                    return
                if holdings < self.MIN_SHARES:
                    self._cancel_all_sells(pos)
                    self._set_status(pos, "DONE")
                    logger.info(f"🦊 Brownfox: target left {pos.market_id[:10]}, {holdings:.2f} sh dust - closing")
                    return
                if bid is None or bid < pos.sell_price - 1e-9:
                    logger.info(f"🦊 Brownfox: target left {pos.market_id[:10]} and market below our +mark "
                                f"- forcing exit")
                    self._set_status(pos, "EXITING")
                    await self._forced_exit(pos)
                    return
                # else market is at/above our +1c -> let the resting sell ride
                # (fall through to normal sell management below)

        # Cancel the rest of the buy once selling has started (price reached our
        # sell level, or our holdings dropped from peak). Only on CONFIRMED cancel.
        if pos.buy_order_id and not pos.buy_done:
            bid = self.client._get_best_price(token, TradeSide.SELL)
            selling_started = (pos.peak_holdings - holdings > self.EPS) or \
                              (bid is not None and bid >= pos.sell_price - 1e-9)
            if selling_started:
                if self._confirm_cancel(pos.buy_order_id, token):
                    pos.buy_done = True
                    logger.info(f"🦊 Brownfox: selling started - cancelled remaining buy for {pos.market_id[:10]}")

        # Keep resting sells covering our shares at +mark, but NEVER for more
        # than we actually hold. The sell size is verified against our live
        # position (holdings, read fresh from the Data API) minus what's already
        # resting, so total sells can never exceed holdings - we only ever sell
        # the max we hold (same holdings-cap rule as the copytrader's SELLs).
        resting = self._resting_sell_size(token, pos.sell_price)
        uncovered = round(holdings - resting, 2)             # uncovered held shares
        place_size = round(min(uncovered, holdings), 2)      # hard cap at holdings
        if place_size >= self.MIN_SHARES:
            oid = self.client.place_limit_order(token, TradeSide.SELL, place_size, pos.sell_price)
            if oid:
                logger.info(f"🦊 Brownfox: resting sell {place_size:.2f} sh @ ${pos.sell_price:.4f} "
                            f"for {pos.market_id[:10]} (held {holdings:.2f}, already resting {resting:.2f})")

        # Done when the buy is finished and we hold ~nothing with no resting sell.
        if pos.buy_done and holdings < self.EPS and resting < self.EPS:
            self._set_status(pos, "DONE")
            logger.success(f"🦊 Brownfox: market {pos.market_id[:10]} fully closed")
        elif pos.buy_done and holdings < self.MIN_SHARES and resting < self.EPS and pos.bought > self.EPS:
            # Sub-5 residual dust that can't be sold -> close out (don't spin).
            self._set_status(pos, "DONE")
            logger.info(f"🦊 Brownfox: {holdings:.2f} sh residual dust in {pos.market_id[:10]} - closing")

    # ---- forced exit -------------------------------------------------------

    async def _forced_exit(self, pos: BrownfoxPos):
        token = pos.token_id
        pos.exit_attempts += 1

        # Cancel every resting sell + the buy, and CONFIRM they're gone before we
        # market-sell - otherwise a still-live resting sell plus the market sell
        # could together exceed our balance. If we can't confirm, retry next cycle
        # rather than stacking another sell.
        sells_gone = self._cancel_all_sells(pos)
        if not pos.buy_done and self._confirm_cancel(pos.buy_order_id, token):
            pos.buy_done = True
        if not sells_gone or not pos.buy_done:
            logger.warning(f"🦊 Brownfox: forced exit for {pos.market_id[:10]} - orders not all confirmed "
                           f"cancelled yet, retrying next cycle")
            if pos.exit_attempts >= 12:
                self._set_status(pos, "STUCK")
                logger.critical(f"⛔ Brownfox: cannot confirm order cancels in {pos.market_id[:10]} - manual action")
            return

        holdings = self._holdings(token)
        if holdings < self.EPS:
            self._set_status(pos, "DONE")
            return
        if holdings < self.MIN_SHARES:
            logger.warning(f"🦊 Brownfox: {holdings:.2f} sh dust in {pos.market_id[:10]} - unsellable, closing")
            self._set_status(pos, "DONE")
            return

        sold, left = await asyncio.to_thread(
            self.client.market_sell_all, token, holdings, self.config.brownfox_market_sell_retries
        )
        if left < self.MIN_SHARES:
            self._set_status(pos, "DONE")
            logger.success(f"🦊 Brownfox: forced exit complete for {pos.market_id[:10]} ({sold:.2f} sold)")
        elif pos.exit_attempts >= 12:
            self._set_status(pos, "STUCK")
            logger.critical(f"⛔ Brownfox: could NOT exit {left:.2f} sh in {pos.market_id[:10]} after "
                            f"{pos.exit_attempts} attempts - manual action needed")
        else:
            logger.warning(f"🦊 Brownfox: forced exit left {left:.2f} sh in {pos.market_id[:10]} - will retry")

    # ---- helpers -----------------------------------------------------------

    def _holdings(self, token: str) -> float:
        try:
            return float(self.client.get_token_holdings(token, use_cache=False) or 0.0)
        except Exception:
            return 0.0

    def _target_holds(self, token: str) -> bool:
        """Does the target still hold this token? Returns True when UNKNOWN
        (fetch failed / not yet loaded) so we never act on a bad read."""
        import time as _time
        addr = getattr(self.config, 'target_trader_address', '') or ''
        if not addr:
            return True
        now = _time.time()
        if self._target_tokens is None or (now - self._target_tokens_at) > 15.0:
            fetched = self.client.get_trader_held_tokens(addr)
            if fetched is not None:
                self._target_tokens = fetched
                self._target_tokens_at = now
        if self._target_tokens is None:
            return True  # unknown -> assume still in (don't act on a bad read)
        return token in self._target_tokens

    def _refresh_bought(self, pos: BrownfoxPos):
        if pos.buy_order_id:
            m = self._matched(pos.buy_order_id)
            if m is not None and m > pos.bought:
                pos.bought = m

    def _matched(self, order_id: str) -> Optional[float]:
        try:
            status = self.client.get_order_status(order_id)
            if not status:
                return None
            return float(status.get('size_matched', 0) or 0)
        except Exception:
            return None

    def _find_order(self, token: str, side: TradeSide, price: float) -> Tuple[Optional[str], float]:
        """Return (order_id, matched) of a resting order for this token at price."""
        try:
            for o in self.client.get_open_orders(token_id=token):
                if str(o.get('side', '')).upper() != side.value:
                    continue
                try:
                    op = float(o.get('price', 0) or 0)
                except (TypeError, ValueError):
                    op = 0.0
                if abs(op - price) > 1e-9:
                    continue
                oid = o.get('id') or o.get('orderID') or o.get('order_id')
                try:
                    matched = float(o.get('size_matched', 0) or 0)
                except (TypeError, ValueError):
                    matched = 0.0
                return (oid, matched)
        except Exception as e:
            logger.debug(f"Brownfox _find_order error: {e}")
        return (None, 0.0)

    def _resting_sell_size(self, token: str, price: float) -> float:
        """Total unfilled SELL size resting for this token at price."""
        total = 0.0
        try:
            for o in self.client.get_open_orders(token_id=token):
                if str(o.get('side', '')).upper() != 'SELL':
                    continue
                try:
                    op = float(o.get('price', 0) or 0)
                except (TypeError, ValueError):
                    op = 0.0
                if abs(op - price) > 1e-9:
                    continue
                try:
                    orig = float(o.get('original_size', o.get('size', 0)) or 0)
                    matched = float(o.get('size_matched', 0) or 0)
                except (TypeError, ValueError):
                    orig, matched = 0.0, 0.0
                total += max(orig - matched, 0.0)
        except Exception as e:
            logger.debug(f"Brownfox _resting_sell_size error: {e}")
        return total

    def _cancel_all_sells(self, pos: BrownfoxPos) -> bool:
        """Cancel every resting sell for this token and CONFIRM each is gone.
        Returns True only if all were confirmed cancelled (so a forced market-sell
        won't run while sells are still live and double-sell)."""
        all_gone = True
        try:
            for o in self.client.get_open_orders(token_id=pos.token_id):
                if str(o.get('side', '')).upper() != 'SELL':
                    continue
                oid = o.get('id') or o.get('orderID') or o.get('order_id')
                if oid and not self._confirm_cancel(oid, pos.token_id):
                    all_gone = False
        except Exception as e:
            logger.debug(f"Brownfox _cancel_all_sells error: {e}")
            all_gone = False
        return all_gone

    def _confirm_cancel(self, order_id: Optional[str], token_id: str) -> bool:
        """Cancel and CONFIRM the order is gone. Returns True only when confirmed
        not resting (so the caller never assumes a cancel that didn't happen)."""
        if not order_id:
            return True
        try:
            if self.client._find_open_order(order_id, token_id) is None:
                return True
            self.client.cancel_order(order_id)
            if self.client._find_open_order(order_id, token_id) is None:
                return True
            self.client.cancel_order(order_id)
            return self.client._find_open_order(order_id, token_id) is None
        except Exception as e:
            logger.debug(f"Brownfox confirm-cancel error for {order_id}: {e}")
            return False

    def _set_status(self, pos: BrownfoxPos, status: str):
        pos.status = status
        try:
            self.db.update_brownfox_status(pos.market_id, status)
        except Exception as e:
            logger.debug(f"Brownfox status persist error: {e}")
