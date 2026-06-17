"""
Brownfox mode.

A distinct trading style for a specific target:

- ONE fixed-USD buy per market (conditionId). We copy only the target's FIRST
  buy in a market and ignore all their scale-ins. Guard persists across restarts.
- We buy at the target's EXACT price (no slippage), for a constant USD size
  (e.g. $50), and the buy REST until it fills.
- As the buy fills (possibly in pieces), we keep a resting SELL covering all
  shares filled so far, priced at buy + markup (default +1 cent).
- The moment our sell fills any shares, we cancel the rest of the unfilled buy.
- If the target exits the market BEFORE our buy fills at all, we cancel the buy.
- If the target sells BELOW our +1c sell, we cancel our sell and the buy and
  MARKET-SELL all our shares (guaranteed, retried). If they sell at/above +1c,
  our resting sell handles the exit.

Careful fill confirmation and no-duplicate guarantees mirror the weather mode:
a replacement/forced action only proceeds on confirmed state, and a per-market
lock serialises everything for one market.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional, Set
from loguru import logger

from src.core.models import TraderTrade, TradeSide


@dataclass
class BrownfoxPos:
    market_id: str
    token_id: str
    buy_price: float
    sell_price: float
    buy_order_id: Optional[str] = None
    sell_order_id: Optional[str] = None
    bought_shares: float = 0.0     # cumulative filled on our buy
    sold_shares: float = 0.0       # cumulative filled on our sell(s)
    sell_size: float = 0.0         # shares currently covered by the resting sell
    buy_cancelled: bool = False
    status: str = "BUYING"         # BUYING, HOLDING, EXITING, DONE, ABORTED
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrownfoxManager:
    EPS = 0.01

    def __init__(self, client, database, config):
        self.client = client
        self.db = database
        self.config = config
        self.positions: Dict[str, BrownfoxPos] = {}
        self.entered: Set[str] = set()
        self._entered_lock = asyncio.Lock()

    def load(self):
        """Load the persisted one-buy-per-market guard."""
        try:
            self.entered = self.db.get_brownfox_markets()
            logger.info(f"🦊 Brownfox: loaded {len(self.entered)} already-entered markets")
        except Exception as e:
            logger.warning(f"Brownfox: could not load entered markets: {e}")
            self.entered = set()

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
        market = trade.market_id
        token = trade.token_id
        if not market or not token:
            return

        # One buy per market - reserve atomically so concurrent buys can't
        # double-enter. Persisted so a restart won't re-buy.
        async with self._entered_lock:
            if market in self.entered or market in self.positions:
                logger.info(f"🦊 Brownfox: already took our one buy in market {market[:10]} - ignoring")
                return
            self.entered.add(market)

        tick = self.client._get_tick_size(token)
        buy_price = self.client._round_to_tick(trade.price, tick)
        if buy_price <= 0 or buy_price >= 1.0:
            async with self._entered_lock:
                self.entered.discard(market)
            return
        sell_price = round(min(self.client._round_to_tick(buy_price + self.config.brownfox_sell_markup, tick),
                               round(1.0 - tick, 10)), 10)
        size = round(self.config.brownfox_trade_size_usd / buy_price, 2)
        if size < 5.0:
            size = 5.0

        logger.info(f"🦊 Brownfox: target bought market {market[:10]} @ ${trade.price:.4f} -> "
                    f"placing ${self.config.brownfox_trade_size_usd} buy = {size:.2f} sh @ exact ${buy_price:.4f}")
        order_id = self.client.place_limit_order(token, TradeSide.BUY, size, buy_price)
        if not order_id:
            logger.error("🦊 Brownfox: buy placement failed - releasing market reservation")
            async with self._entered_lock:
                self.entered.discard(market)
            return

        self.db.record_brownfox_market(market, token, buy_price)
        self.positions[market] = BrownfoxPos(
            market_id=market, token_id=token, buy_price=buy_price,
            sell_price=sell_price, buy_order_id=order_id, status="BUYING",
        )
        logger.success(f"🦊 Brownfox: buy resting {order_id} @ ${buy_price:.4f}; will sell at +"
                       f"{self.config.brownfox_sell_markup} = ${sell_price:.4f}")

    # ---- sell (target's) ---------------------------------------------------

    async def on_target_sell(self, trade: TraderTrade):
        pos = self.positions.get(trade.market_id)
        if not pos:
            return
        async with pos.lock:
            if pos.status in ("DONE", "ABORTED", "EXITING"):
                return
            await self._refresh_fills(pos)

            if pos.bought_shares < self.EPS:
                # Target exited before our buy filled at all -> cancel the buy.
                logger.info(f"🦊 Brownfox: target exited market {pos.market_id[:10]} before our buy "
                            f"filled - cancelling buy {pos.buy_order_id}")
                self._confirm_cancel(pos.buy_order_id, pos.token_id)
                pos.buy_cancelled = True
                pos.status = "ABORTED"
                return

            if trade.price < pos.sell_price - 1e-9:
                logger.info(f"🦊 Brownfox: target sold @ ${trade.price:.4f} BELOW our +mark sell "
                            f"${pos.sell_price:.4f} - forcing market exit")
                pos.status = "EXITING"
                await self._forced_exit(pos)
            else:
                logger.info(f"🦊 Brownfox: target sold @ ${trade.price:.4f} >= our sell ${pos.sell_price:.4f} "
                            f"- resting +mark sell handles the exit")

    # ---- reconcile (periodic) ---------------------------------------------

    async def reconcile_once(self):
        for market in list(self.positions.keys()):
            pos = self.positions.get(market)
            if not pos:
                continue
            async with pos.lock:
                if pos.status in ("DONE", "ABORTED"):
                    continue
                try:
                    await self._refresh_fills(pos)

                    if pos.status == "EXITING":
                        await self._forced_exit(pos)
                        continue

                    held = round(pos.bought_shares - pos.sold_shares, 6)

                    # Once our sell has taken ANY shares, cancel the rest of the
                    # unfilled buy (price has risen; no more accumulating).
                    if pos.sold_shares > self.EPS and not pos.buy_cancelled:
                        self._confirm_cancel(pos.buy_order_id, pos.token_id)
                        pos.buy_cancelled = True
                        logger.info(f"🦊 Brownfox: sell started filling - cancelled remaining buy "
                                    f"for {pos.market_id[:10]}")

                    # Keep the resting sell sized to cover all shares we hold.
                    if held >= 5.0 and abs(held - pos.sell_size) >= 1.0:
                        await self._ensure_sell(pos, held)

                    # Fully exited?
                    if pos.buy_cancelled and held < self.EPS:
                        pos.status = "DONE"
                        logger.success(f"🦊 Brownfox: market {pos.market_id[:10]} fully closed")
                except Exception as e:
                    logger.error(f"🦊 Brownfox reconcile error for {market[:10]}: {e}")

    # ---- helpers -----------------------------------------------------------

    async def _refresh_fills(self, pos: BrownfoxPos):
        """Update bought/sold shares from order status (best-effort)."""
        if pos.buy_order_id:
            m = self._matched(pos.buy_order_id)
            if m is not None and m > pos.bought_shares:
                pos.bought_shares = m
        if pos.sell_order_id:
            m = self._matched(pos.sell_order_id)
            if m is not None and m > pos.sold_shares:
                pos.sold_shares = m

    def _matched(self, order_id: str) -> Optional[float]:
        try:
            status = self.client.get_order_status(order_id)
            if not status:
                return None
            return float(status.get('size_matched', 0) or 0)
        except Exception:
            return None

    async def _ensure_sell(self, pos: BrownfoxPos, held: float):
        """Make sure a resting sell at +mark covers `held` shares (cancel and
        re-place if the covered size is stale)."""
        # Cancel the existing sell first (confirmed) so we never have two sells.
        if pos.sell_order_id:
            if not self._confirm_cancel(pos.sell_order_id, pos.token_id):
                logger.warning("🦊 Brownfox: could not confirm sell cancel - not re-placing (no dup)")
                return
            # capture any fills that happened on it before cancel
            m = self._matched(pos.sell_order_id)
            if m is not None and m > pos.sold_shares:
                pos.sold_shares = m
            pos.sell_order_id = None
            pos.sell_size = 0.0
            held = round(pos.bought_shares - pos.sold_shares, 6)
            if held < 5.0:
                return
        oid = self.client.place_limit_order(pos.token_id, TradeSide.SELL, held, pos.sell_price)
        if oid:
            pos.sell_order_id = oid
            pos.sell_size = held
            logger.info(f"🦊 Brownfox: resting sell {held:.2f} sh @ ${pos.sell_price:.4f} for {pos.market_id[:10]}")

    async def _forced_exit(self, pos: BrownfoxPos):
        """Cancel our sell + remaining buy and market-sell everything we hold."""
        # Cancel resting sell (capture any fills first).
        if pos.sell_order_id:
            m = self._matched(pos.sell_order_id)
            if m is not None and m > pos.sold_shares:
                pos.sold_shares = m
            self._confirm_cancel(pos.sell_order_id, pos.token_id)
            pos.sell_order_id = None
            pos.sell_size = 0.0
        # Cancel remaining buy.
        if not pos.buy_cancelled:
            self._confirm_cancel(pos.buy_order_id, pos.token_id)
            pos.buy_cancelled = True

        held = round(pos.bought_shares - pos.sold_shares, 6)
        if held < self.EPS:
            pos.status = "DONE"
            return

        sold, left = await asyncio.to_thread(
            self.client.market_sell_all, pos.token_id, held,
            self.config.brownfox_market_sell_retries,
        )
        pos.sold_shares += sold
        if left < self.EPS:
            pos.status = "DONE"
            logger.success(f"🦊 Brownfox: forced exit complete for {pos.market_id[:10]}")
        else:
            # Leave EXITING; the reconcile loop will retry until flat.
            logger.warning(f"🦊 Brownfox: forced exit left {left:.2f} sh in {pos.market_id[:10]} - will retry")

    def _confirm_cancel(self, order_id: Optional[str], token_id: str) -> bool:
        """Cancel an order and confirm it's gone from the book. Returns True if
        the order is confirmed not resting (cancelled or already gone)."""
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
