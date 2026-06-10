"""
Data-API polling monitor for the target trader.

Drop-in alternative to WebSocketTraderMonitor for environments where the live
WebSocket (wss://ws-live-data.polymarket.com) is rejected with HTTP 429. That
rejection is a per-IP rate-limit Polymarket's edge applies to datacenter IPs
(Railway, AWS, ...) and is *separate* from the regional geo-block - so a region
that can place orders fine can still have its WS throttled.

The Data API (https://data-api.polymarket.com) is reachable from those same
IPs, so we poll the target's recent activity instead of streaming it. Slightly
higher latency than the WS, but it actually works where the WS does not.

Public interface mirrors WebSocketTraderMonitor so the bot can use either:
    add_new_trade_callback, start_monitoring, stop_monitoring, get_monitoring_status
"""
import asyncio
import json
import urllib.request
import urllib.error
from datetime import datetime
from typing import List, Set, Optional, Dict, Any
from loguru import logger

from src.core.models import TraderTrade, TradeSide, MarketInfo
from src.core.config import trading_config, polymarket_config
from src.core.market_filters import MarketFilter


class DataAPITraderMonitor:
    """Monitor a trader by polling the Polymarket Data API activity feed."""

    def __init__(self):
        self.config = trading_config
        self.target_address = self.config.target_trader_address.lower()
        self.monitoring = False
        self.new_trade_callbacks = []
        self.market_filter = MarketFilter()

        self.data_api_url = polymarket_config.data_api_url.rstrip("/")
        # Poll cadence reuses the existing monitoring interval (default 1s),
        # floored so we never hammer the API.
        self.poll_interval = max(1.0, float(self.config.monitoring_interval_seconds))
        self.limit = 30  # how many recent activity items to pull each poll

        self.seen_trade_ids: Set[str] = set()
        self.last_check_time = datetime.now()
        self.last_poll_time = datetime.now()
        self.is_connected = False  # True once the first poll succeeds
        self.poll_count = 0

    def add_new_trade_callback(self, callback):
        """Add callback for new trades"""
        self.new_trade_callbacks.append(callback)

    def start_monitoring(self):
        """Start polling the Data API"""
        logger.info(f"Starting Data-API polling for trader: {self.target_address}")
        logger.info(f"   Poll interval: {self.poll_interval:.0f}s  |  endpoint: {self.data_api_url}")
        self.monitoring = True
        asyncio.create_task(self._poll_loop())

    def stop_monitoring(self):
        """Stop monitoring"""
        logger.info("Stopping Data-API trader monitoring")
        self.monitoring = False

    async def _poll_loop(self):
        """Main polling loop. Seeds 'seen' on first poll so only trades that
        happen AFTER startup are copied (no replay of history)."""
        # Seed: mark everything currently in the feed as already-seen.
        try:
            seed = await self._fetch_activity()
            for item in seed:
                self.seen_trade_ids.add(self._activity_key(item))
            self.is_connected = True
            logger.success(f"✅ Data-API monitor ready (seeded {len(self.seen_trade_ids)} recent items)")
        except Exception as e:
            logger.warning(f"Data-API seed poll failed ({e}); will retry in loop")

        while self.monitoring:
            try:
                await asyncio.sleep(self.poll_interval)
                items = await self._fetch_activity()
                self.is_connected = True
                self.last_poll_time = datetime.now()

                # Oldest-first so we dispatch in chronological order.
                new_items = [it for it in items if self._activity_key(it) not in self.seen_trade_ids]
                for item in reversed(new_items):
                    self.seen_trade_ids.add(self._activity_key(item))
                    await self._process_activity(item)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.is_connected = False
                logger.error(f"Data-API poll error: {e}")
                # Soft backoff on errors; loop continues.
                await asyncio.sleep(min(self.poll_interval * 2, 10))

    async def _fetch_activity(self) -> List[Dict[str, Any]]:
        """Fetch recent activity for the target (runs the blocking HTTP call in
        a thread so the event loop isn't stalled)."""
        return await asyncio.to_thread(self._fetch_activity_blocking)

    def _fetch_activity_blocking(self) -> List[Dict[str, Any]]:
        url = f"{self.data_api_url}/activity?user={self.target_address}&limit={self.limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code} from Data API") from e
        if not isinstance(data, list):
            return []
        return data

    @staticmethod
    def _activity_key(item: Dict[str, Any]) -> str:
        """Unique key per activity item. A single tx can contain multiple
        legs/assets, so key on tx hash + asset + type."""
        return (f"{item.get('transactionHash', '')}:"
                f"{item.get('asset', '')}:"
                f"{item.get('type', '')}")

    async def _process_activity(self, item: Dict[str, Any]):
        """Route one activity item to trade / merge / redeem handling."""
        try:
            act_type = str(item.get("type", "")).upper()

            if act_type == "REDEEM":
                await self._fire_action("redeem", item)
                return
            if act_type in ("MERGE", "SPLIT"):
                await self._fire_action("merge" if act_type == "MERGE" else "split", item)
                return

            # Otherwise treat as a trade (TRADE / BUY / SELL).
            side_str = str(item.get("side", "")).upper()
            if side_str not in ("BUY", "SELL"):
                return  # not a tradeable activity we handle

            market_title = item.get("title", "") or ""
            if market_title and not self.market_filter.should_copy_market(market_title):
                logger.debug(f"⏭️  Skipped (filter): {market_title}")
                return

            trade = self._convert_trade(item, side_str)
            if not trade:
                return

            logger.info("🎯 Trade from target trader detected! (Data-API)")
            logger.info(f"📊 Market: {market_title or 'Unknown Market'}")
            logger.info(f"   Target: {trade.side.value} {trade.size:.2f} shares @ ${trade.price:.2f} = ${trade.amount_usd:.2f}")

            if self.config.trade_delay_seconds > 0:
                await asyncio.sleep(self.config.trade_delay_seconds)

            await self._dispatch(trade)

        except Exception as e:
            logger.error(f"Failed to process Data-API activity item: {e}")

    def _convert_trade(self, item: Dict[str, Any], side_str: str) -> Optional[TraderTrade]:
        try:
            tx_hash = item.get("transactionHash", "")
            # Keep the same id scheme as the WS monitor so DB dedup is shared
            # if the two are ever swapped.
            trade_id = f"ws_{tx_hash}"

            price = float(item.get("price", 0) or 0)
            size = float(item.get("size", 0) or 0)
            amount_usd = float(item.get("usdcSize", 0) or 0) or (price * size)
            ts = int(item.get("timestamp", 0) or 0)

            market_info = MarketInfo(
                market_id=item.get("conditionId", ""),
                token_id=item.get("asset", ""),
                title=item.get("title", ""),
                description="",
                outcome=item.get("outcome", ""),
                current_price=price,
                liquidity_usd=0,
                volume_24h_usd=0,
                is_active=True,
            )

            return TraderTrade(
                trade_id=trade_id,
                trader_address=self.target_address,
                market_id=item.get("conditionId", ""),
                token_id=item.get("asset", ""),
                side=TradeSide.BUY if side_str == "BUY" else TradeSide.SELL,
                price=price,
                size=size,
                amount_usd=amount_usd,
                timestamp=datetime.fromtimestamp(ts) if ts else datetime.now(),
                transaction_hash=tx_hash,
                market_info=market_info,
            )
        except Exception as e:
            logger.error(f"Failed to convert Data-API trade: {e}")
            return None

    async def _dispatch(self, trade: TraderTrade):
        for callback in self.new_trade_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(trade)
                else:
                    callback(trade)
            except Exception as e:
                logger.error(f"Error in trade callback: {e}")

    async def _fire_action(self, action: str, item: Dict[str, Any]):
        payload = {
            "action": action,
            "market_id": item.get("conditionId", item.get("marketId", "")),
            "amount": float(item.get("size", 0) or 0),
            "trader_address": self.target_address,
            "timestamp": datetime.now().isoformat(),
        }
        logger.info(f"{'💰' if action == 'redeem' else '🔄'} Target {action} detected (Data-API)")
        for callback in self.new_trade_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(payload)
                else:
                    callback(payload)
            except Exception as e:
                logger.error(f"Error in {action} callback: {e}")

    def get_monitoring_status(self) -> dict:
        return {
            "monitoring": self.monitoring,
            "connected": self.is_connected,
            "poll_count": self.poll_count,
            "target_address": self.target_address,
            "last_check_time": self.last_poll_time.isoformat(),
            "seen_trades_count": len(self.seen_trade_ids),
            "monitoring_interval": f"{self.poll_interval:.0f}s (polling)",
            "method": "data-api-polling",
            "active_filters": self.market_filter.get_enabled_patterns(),
            "filtering_enabled": bool(self.market_filter.get_enabled_patterns()),
        }
