"""
Polymarket API client wrapper for copy trading bot
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum
import requests
from loguru import logger

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds, OrderArgs, MarketOrderArgs, OrderType,
    TradeParams, OpenOrderParams, BookParams, OrderPayload
)
from py_clob_client_v2.order_builder.constants import BUY, SELL

from src.core.config import polymarket_config, trading_config, bot_config
from src.core.models import TraderTrade, MarketInfo, TradeSide, Position, AccountRestrictedException


class OrderExecutionResult(Enum):
    """Result of order execution attempt"""
    SUCCESS = "success"
    PARTIAL_FILL = "partial_fill"
    FAILED = "failed"
    INSUFFICIENT_LIQUIDITY = "insufficient_liquidity"
    PRICE_MOVED = "price_moved"
    TIMEOUT = "timeout"


class PolymarketClient:
    """Wrapper for Polymarket API interactions"""
    
    def __init__(self):
        self.config = polymarket_config
        self.trading_config = trading_config
        self.bot_config = bot_config
        self._client: Optional[ClobClient] = None
        
        # HTTP session with connection pooling for speed
        self._session = requests.Session()
        from requests.adapters import HTTPAdapter
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0
        )
        self._session.mount('https://', adapter)
        self._session.mount('http://', adapter)
        
        # Balance cache for speed optimization
        self._balance_cache = None
        self._balance_cache_time = None
        self._balance_cache_ttl = timedelta(seconds=60)  # Cache for 60 seconds
        
        # Market info cache for speed optimization (saves 50-100ms per repeated market)
        self._market_cache: Dict[str, Dict] = {}
        self._market_cache_ttl = timedelta(hours=1)  # Cache for 1 hour

        # Positions cache (Data API) - short TTL so risk checks stay current
        self._positions_cache: Optional[List[Position]] = None
        self._positions_cache_time: Optional[datetime] = None
        self._positions_cache_ttl = timedelta(seconds=15)

        # Per-token async locks so concurrent (non-blocking) order execution on
        # the SAME token serializes - prevents two chases fighting/duplicating
        # orders on one market. Different tokens still run in parallel.
        self._token_locks: Dict[str, asyncio.Lock] = {}

        # Last error string from place_limit_order (so callers can detect
        # non-retryable failures like insufficient balance/allowance).
        self._last_place_error: Optional[str] = None
        
    def initialize(self) -> bool:
        """Initialize the Polymarket client"""
        try:
            self._client = ClobClient(
                host=self.config.clob_api_url,
                chain_id=self.config.chain_id,
                key=self.config.private_key,
                signature_type=self.config.signature_type,
                funder=self.config.funder_address
            )

            # Create or derive API credentials
            if not all([self.config.api_key, self.config.api_secret, self.config.api_passphrase]):
                logger.info("Creating API credentials...")
                creds = self._client.create_or_derive_api_key()
                self._client.set_api_creds(creds)
                self.config.api_key = creds.api_key
                self.config.api_secret = creds.api_secret
                self.config.api_passphrase = creds.api_passphrase
            else:
                creds = ApiCreds(
                    api_key=self.config.api_key,
                    api_secret=self.config.api_secret,
                    api_passphrase=self.config.api_passphrase
                )
                self._client.set_api_creds(creds)
            
            # Test connection
            ok = self._client.get_ok()
            if not ok:
                raise Exception("Failed to connect to Polymarket API")
                
            logger.success("Polymarket client initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Polymarket client: {e}")
            return False
    
    @property
    def client(self) -> ClobClient:
        """Get the CLOB client"""
        if not self._client:
            raise RuntimeError("Client not initialized. Call initialize() first.")
        return self._client

    def check_region_allowed(self, timeout: float = 10.0) -> bool:
        """Preflight check: will Polymarket accept orders from this IP/region?

        Polymarket enforces a geo-block at the API edge that rejects order
        placement with HTTP 403 "Trading restricted in your region" - and it's
        applied *before* authentication, so we can detect it with an
        unauthenticated probe (no real order, no credentials needed).

        - Blocked region -> 403 with a geo-block message  -> returns False
        - Allowed region  -> 401/400/etc (auth/validation) -> returns True

        On any network error we return True (don't block startup on a hiccup);
        real order placement still has its own geo-block handling as a backstop.
        """
        import urllib.request
        import urllib.error

        url = f"{self.config.clob_api_url}/order"
        headers = {
            "Content-Type": "application/json",
            # Browser-like headers so Cloudflare lets the probe reach the geo
            # check instead of returning its own bot block (error 1010).
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Origin": "https://polymarket.com",
            "Referer": "https://polymarket.com/",
        }
        req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            status, body = resp.status, resp.read()[:300].decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()[:300].decode("utf-8", "replace")
        except Exception as e:
            logger.warning(f"Region preflight could not complete ({e}); proceeding anyway.")
            return True

        blocked = status == 403 and (
            "geoblock" in body.lower() or "restricted in your region" in body.lower()
        )
        if blocked:
            logger.critical("⛔ GEO-BLOCKED: Polymarket refuses trading from this IP/region.")
            logger.critical("   Preflight order endpoint returned 403 'restricted in your region'.")
            logger.critical("   Order placement WILL fail here. Run the bot from a supported region")
            logger.critical("   (e.g. a VPN/proxy or host in an allowed jurisdiction).")
            return False

        logger.success(
            f"✅ Region preflight OK - trading not geo-blocked (probe returned {status})."
        )
        return True

    def get_trader_recent_trades(self, trader_address: str, limit: int = 100) -> List[TraderTrade]:
        """Get recent trades for a specific trader"""
        try:
            trades_response = self.client.get_trades(
                TradeParams(maker_address=trader_address)
            )
            
            trades = []
            for trade_data in trades_response[:limit]:
                try:
                    trade = TraderTrade(
                        trade_id=trade_data.get('id', ''),
                        trader_address=trader_address,
                        market_id=trade_data.get('market', ''),
                        token_id=trade_data.get('asset_id', ''),
                        side=TradeSide(trade_data.get('side', 'BUY')),
                        price=float(trade_data.get('price', 0)),
                        size=float(trade_data.get('size', 0)),
                        amount_usd=float(trade_data.get('price', 0)) * float(trade_data.get('size', 0)),
                        timestamp=datetime.fromtimestamp(int(trade_data.get('match_time', 0)) / 1000),
                        transaction_hash=trade_data.get('transaction_hash', '')
                    )
                    trades.append(trade)
                except Exception as e:
                    logger.warning(f"Failed to parse trade data: {e}")
                    continue
                    
            logger.info(f"Retrieved {len(trades)} recent trades for trader {trader_address}")
            return trades
            
        except Exception as e:
            logger.error(f"Failed to get trader trades: {e}")
            return []
    
    def get_market_info(self, token_id: str) -> Optional[MarketInfo]:
        """Get market information for a token ID (with caching for speed)"""
        try:
            # Check cache first (saves 50-100ms on repeated markets)
            if token_id in self._market_cache:
                cached_data = self._market_cache[token_id]
                cache_time = cached_data.get('cached_at')
                
                # Check if cache is still valid
                if cache_time and datetime.now() - cache_time < self._market_cache_ttl:
                    logger.debug(f"Market info cache hit for {token_id}")
                    return cached_data['market_info']
                else:
                    # Cache expired, remove it
                    del self._market_cache[token_id]
            
            # Cache miss - fetch from API
            logger.debug(f"Market info cache miss for {token_id}, fetching...")
            
            # Get market data from Gamma API
            response = self._session.get(
                f"{self.config.gamma_api_url}/markets",
                params={"token_id": token_id}
            )
            
            if response.status_code != 200:
                logger.warning(f"Failed to get market info for token {token_id}")
                return None
                
            markets_data = response.json()
            if not markets_data or len(markets_data) == 0:
                return None
                
            market_data = markets_data[0]  # Take first match
            
            # Get current price and orderbook
            try:
                price = self.client.get_midpoint(token_id)
                book = self.client.get_order_book(token_id)
            except:
                price = 0.0
                book = None
            
            market_info = MarketInfo(
                market_id=market_data.get('condition_id', ''),
                token_id=token_id,
                title=market_data.get('question', ''),
                description=market_data.get('description', ''),
                outcome=market_data.get('outcome', ''),
                current_price=price,
                liquidity_usd=float(market_data.get('liquidity', 0)),
                volume_24h_usd=float(market_data.get('volume24hr', 0)),
                is_active=market_data.get('active', True),
                end_date=datetime.fromisoformat(market_data.get('end_date_iso', '').replace('Z', '+00:00')) if market_data.get('end_date_iso') else None
            )
            
            # Cache the result
            self._market_cache[token_id] = {
                'market_info': market_info,
                'cached_at': datetime.now()
            }
            
            return market_info
            
        except Exception as e:
            logger.error(f"Failed to get market info for token {token_id}: {e}")
            return None
    
    def get_current_positions(self, use_cache: bool = True) -> List[Position]:
        """Get current open positions for the bot account.

        Live positions are read from Polymarket's Data API
        (https://data-api.polymarket.com/positions?user=<funder>), which reports
        every outcome token the wallet currently holds. This is what powers the
        MAX_POSITIONS limit and the duplicate-market guard in the risk manager.
        """
        try:
            # In dry run mode, there are no real positions to report
            if self.bot_config.dry_run:
                logger.debug("[DRY RUN] Position tracking skipped")
                return []

            # Serve from short-lived cache when possible
            if use_cache and self._positions_cache is not None:
                now = datetime.now()
                if (self._positions_cache_time and
                        now - self._positions_cache_time < self._positions_cache_ttl):
                    logger.debug(f"Using cached positions ({len(self._positions_cache)} positions)")
                    return self._positions_cache

            funder_address = self.config.funder_address
            if not funder_address:
                logger.error("FUNDER_ADDRESS not configured - cannot fetch positions")
                return []

            response = self._session.get(
                f"{self.config.data_api_url}/positions",
                params={"user": funder_address, "sizeThreshold": 0.1},
                timeout=10
            )

            if response.status_code != 200:
                logger.warning(f"Failed to get positions (HTTP {response.status_code})")
                return self._positions_cache or []

            raw_positions = response.json()
            if not isinstance(raw_positions, list):
                logger.warning(f"Unexpected positions response: {type(raw_positions)}")
                return self._positions_cache or []

            positions: List[Position] = []
            for item in raw_positions:
                try:
                    size = float(item.get('size', 0) or 0)
                    if size <= 0:
                        continue  # Closed/empty position

                    # Skip resolved positions awaiting redemption. They are no
                    # longer open exposure (the auto-redeemer claims them), and
                    # counting them inflates both the position count and value.
                    if item.get('redeemable') is True:
                        continue

                    avg_price = float(item.get('avgPrice', 0) or 0)
                    # curPrice can legitimately be 0.0 (resolved/worthless).
                    # Only fall back to avg_price when the field is truly absent
                    # - NOT on a real 0, which would wildly overvalue dead
                    # positions (size x avg_price instead of size x 0).
                    raw_cur = item.get('curPrice')
                    if raw_cur is None or raw_cur == '':
                        cur_price = avg_price
                    else:
                        cur_price = float(raw_cur)

                    # Data API reports cash P&L directly; fall back to a manual calc
                    unrealized_pnl = item.get('cashPnl')
                    if unrealized_pnl is None:
                        unrealized_pnl = (cur_price - avg_price) * size
                    else:
                        unrealized_pnl = float(unrealized_pnl)

                    market_id = item.get('conditionId', '') or ''
                    token_id = item.get('asset', '') or ''
                    title = item.get('title', '') or ''
                    outcome = item.get('outcome', '') or ''

                    market_info = MarketInfo(
                        market_id=market_id,
                        token_id=token_id,
                        title=title,
                        description='',
                        outcome=outcome,
                        current_price=cur_price,
                        liquidity_usd=0.0,
                        volume_24h_usd=0.0,
                        is_active=True
                    )

                    # Holding an outcome token is always a long (BUY) position
                    positions.append(Position(
                        market_id=market_id,
                        token_id=token_id,
                        side=TradeSide.BUY,
                        size=size,
                        average_price=avg_price,
                        current_price=cur_price,
                        unrealized_pnl=unrealized_pnl,
                        market_info=market_info
                    ))
                except Exception as e:
                    logger.warning(f"Failed to parse position entry: {e}")
                    continue

            # Update cache
            self._positions_cache = positions
            self._positions_cache_time = datetime.now()

            logger.debug(f"Retrieved {len(positions)} open positions from Data API")
            return positions

        except Exception as e:
            logger.error(f"Failed to get current positions: {e}")
            return self._positions_cache or []

    def get_token_holdings(self, token_id: str, use_cache: bool = True) -> float:
        """Return how many shares of a specific outcome token the wallet holds.

        Used to cap SELL orders at what we actually own (you can't sell shares
        you don't have). Returns 0.0 if we hold none / can't determine.
        """
        try:
            for p in self.get_current_positions(use_cache=use_cache):
                if p.token_id == token_id:
                    return float(p.size)
        except Exception as e:
            logger.debug(f"Could not get holdings for {token_id}: {e}")
        return 0.0

    def get_open_orders(self, market_id: Optional[str] = None,
                        token_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get the account's resting (open) orders via the V2 CLOB API.

        Optionally filter by market (condition id) or token id (asset id).
        """
        try:
            if self.bot_config.dry_run:
                return []

            params = None
            if market_id or token_id:
                params = OpenOrderParams(market=market_id, asset_id=token_id)

            orders = self.client.get_open_orders(params) if params else self.client.get_open_orders()
            if not orders:
                return []
            return list(orders)

        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []
    
    def get_account_balance(self, use_cache: bool = True) -> float:
        """Get current USDC balance (cached for speed)"""
        try:
            # In dry run mode, return simulated balance
            if self.bot_config.dry_run:
                simulated_balance = 50000.0  # $50,000 USDC for testing
                logger.info(f"[DRY RUN] Simulated balance: ${simulated_balance}")
                return simulated_balance
            
            # Check cache if requested
            if use_cache and self._balance_cache is not None:
                now = datetime.now()
                if self._balance_cache_time and now - self._balance_cache_time < self._balance_cache_ttl:
                    age = (now - self._balance_cache_time).total_seconds()
                    logger.debug(f"Using cached balance: ${self._balance_cache:.2f} (age: {age:.1f}s)")
                    return self._balance_cache
            
            # Get real balance from the client
            logger.info("Getting account USDC balance...")

            # Use Web3 to get USDC balance directly from blockchain
            import os
            from web3 import Web3

            # Polygon RPC endpoints. A custom endpoint can be supplied via
            # POLYGON_RPC_URL (recommended for cloud hosts where the public RPC
            # is rate-limited/blocked). We then fall back through several public
            # endpoints so a single dead RPC doesn't break balance reads.
            rpc_candidates = []
            custom_rpc = os.getenv("POLYGON_RPC_URL", "").strip()
            if custom_rpc:
                rpc_candidates.append(custom_rpc)
            rpc_candidates.extend([
                "https://polygon-rpc.com",
                "https://polygon-bor-rpc.publicnode.com",
                "https://rpc.ankr.com/polygon",
                "https://1rpc.io/matic",
                "https://polygon.llamarpc.com",
            ])

            w3 = None
            for rpc_url in rpc_candidates:
                try:
                    candidate = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
                    if candidate.is_connected():
                        w3 = candidate
                        logger.debug(f"Connected to Polygon RPC: {rpc_url}")
                        break
                    else:
                        logger.warning(f"Polygon RPC not responding: {rpc_url}")
                except Exception as rpc_err:
                    logger.warning(f"Polygon RPC failed ({rpc_url}): {rpc_err}")

            if w3 is None:
                logger.error(
                    "Could not connect to any Polygon RPC. Set POLYGON_RPC_URL to a "
                    "reliable endpoint (e.g. an Alchemy/Infura/QuickNode URL). "
                    "Returning cached balance if available."
                )
                # Prefer a stale cached balance over 0.0 so trading isn't blocked
                # by a transient RPC outage.
                if self._balance_cache is not None:
                    return self._balance_cache
                return 0.0

            # Get funder address
            funder_address = self.config.funder_address
            if not funder_address:
                logger.error("FUNDER_ADDRESS not configured")
                return 0.0
            
            # Convert to checksum address
            funder_address = w3.to_checksum_address(funder_address)
            
            # pUSD collateral token on Polygon (CLOB V2 replaced USDC.e with pUSD)
            usdc_address = w3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
            
            # ERC20 ABI for balance check
            erc20_abi = [
                {
                    "constant": True,
                    "inputs": [{"name": "_owner", "type": "address"}],
                    "name": "balanceOf",
                    "outputs": [{"name": "balance", "type": "uint256"}],
                    "type": "function"
                },
                {
                    "constant": True,
                    "inputs": [],
                    "name": "decimals",
                    "outputs": [{"name": "", "type": "uint8"}],
                    "type": "function"
                }
            ]
            
            # Create contract instance and get balance
            usdc_contract = w3.eth.contract(address=usdc_address, abi=erc20_abi)
            balance_wei = usdc_contract.functions.balanceOf(funder_address).call()
            decimals = usdc_contract.functions.decimals().call()
            usdc_balance = balance_wei / (10 ** decimals)
            
            logger.info(f"Current USDC balance: ${usdc_balance:.2f}")
            logger.debug("Polymarket handles gas fees automatically - no MATIC needed!")
            
            # Update cache
            self._balance_cache = usdc_balance
            self._balance_cache_time = datetime.now()
            
            return usdc_balance
            
        except Exception as e:
            logger.error(f"Failed to get account balance: {e}")
            # Fall back to last known balance on transient errors rather than
            # reporting $0 (which would otherwise halt trading).
            if self._balance_cache is not None:
                logger.warning(f"Returning last known balance: ${self._balance_cache:.2f}")
                return self._balance_cache
            return 0.0

    def place_market_order(self, token_id: str, side: TradeSide, amount_usd: float) -> Optional[str]:
        """Place a market order"""
        try:
            order_side = BUY if side == TradeSide.BUY else SELL
            
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount_usd,
                side=order_side,
                order_type=OrderType.FAK
            )

            signed_order = self.client.create_market_order(order_args)
            response = self.client.post_order(signed_order, OrderType.FAK)
            
            if response.get('success'):
                order_id = response.get('orderID')
                logger.success(f"Market order placed successfully: {order_id}")
                return order_id
            else:
                logger.error(f"Failed to place market order: {response}")
                return None
                
        except Exception as e:
            error_str = str(e)
            
            # Check for "closed only mode" restriction
            if "closed only mode" in error_str.lower():
                logger.critical("⛔ ACCOUNT RESTRICTED: Your account is in CLOSED-ONLY MODE")
                logger.critical("You can only close existing positions, not open new ones")
                logger.critical("This is a critical error - the bot will stop trading")
                raise AccountRestrictedException(
                    f"Account is in closed-only mode: {error_str}",
                    restriction_type="closed_only"
                )
            
            logger.error(f"Failed to place market order: {e}")
            return None
    
    def place_limit_order(self, token_id: str, side: TradeSide, size: float, price: float) -> Optional[str]:
        """Place a limit order"""
        try:
            order_side = BUY if side == TradeSide.BUY else SELL
            
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side
            )
            
            self._last_place_error = None
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)

            if response.get('success'):
                order_id = response.get('orderID')
                logger.success(f"Limit order placed successfully: {order_id}")
                return order_id
            else:
                logger.error(f"Failed to place limit order: {response}")
                self._last_place_error = str(response)
                return None

        except Exception as e:
            error_str = str(e)
            self._last_place_error = error_str

            # Check for "closed only mode" restriction
            if "closed only mode" in error_str.lower():
                logger.critical("⛔ ACCOUNT RESTRICTED: Your account is in CLOSED-ONLY MODE")
                logger.critical("You can only close existing positions, not open new ones")
                logger.critical("This is a critical error - the bot will stop trading")
                raise AccountRestrictedException(
                    f"Account is in closed-only mode: {error_str}",
                    restriction_type="closed_only"
                )

            # Check for regional geo-block (403). This is persistent and applies
            # to every order, so there's no point retrying - stop cleanly with a
            # single clear message instead of hammering the API.
            if "geoblock" in error_str.lower() or "restricted in your region" in error_str.lower():
                logger.critical("⛔ GEO-BLOCKED: Polymarket is refusing orders from this IP/region")
                logger.critical("The CLOB API returned 403 'Trading restricted in your region'")
                logger.critical("Run the bot from a supported region (e.g. via a VPN/proxy in an")
                logger.critical("allowed jurisdiction). Balance reads work; only order placement is blocked.")
                raise AccountRestrictedException(
                    f"Trading geo-blocked in this region: {error_str}",
                    restriction_type="geoblock"
                )

            logger.error(f"Failed to place limit order: {e}")
            return None
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            response = self.client.cancel_order(OrderPayload(orderID=order_id))

            # The V2 API returns {'canceled': [ids], 'not_canceled': {id: reason}}
            # rather than {'success': True}. Treat the order as cancelled if it's
            # in the 'canceled' list (or the legacy success flag is set).
            if isinstance(response, dict):
                canceled = response.get('canceled') or []
                not_canceled = response.get('not_canceled') or {}
                if order_id in canceled or response.get('success'):
                    logger.info(f"Order cancelled successfully: {order_id}")
                    return True
                # If it's neither canceled nor explicitly rejected, it's likely
                # already gone (filled/expired) - not an error worth flagging.
                if order_id not in not_canceled and not not_canceled:
                    logger.info(f"Order already gone (nothing to cancel): {order_id}")
                    return True
                logger.warning(f"Order not cancelled: {order_id} -> {not_canceled.get(order_id, response)}")
                return False

            logger.warning(f"Unexpected cancel response: {response}")
            return False

        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False
    
    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an order"""
        try:
            order = self.client.get_order(order_id)
            return order
            
        except Exception as e:
            logger.error(f"Failed to get order status for {order_id}: {e}")
            return None
    
    async def _place_gtc_order_directly(
        self, 
        token_id: str, 
        side: TradeSide, 
        amount_usd: float,
        original_price: Optional[float],
        details: dict
    ) -> Tuple[Optional[str], OrderExecutionResult, Dict[str, Any]]:
        """Place GTC order directly without trying FAK first (for GTC-only mode)"""
        config = self.trading_config
        
        try:
            # Get current market price (or use original price as fallback)
            current_price = None
            if not config.skip_price_fetch_for_speed:
                try:
                    midpoint_data = self.client.get_midpoint(token_id)
                    if isinstance(midpoint_data, dict):
                        current_price = float(midpoint_data.get('mid', 0))
                    elif isinstance(midpoint_data, (int, float)):
                        current_price = float(midpoint_data)
                    
                    if current_price and current_price > 0:
                        logger.debug(f"Current market price: ${current_price:.4f}")
                    else:
                        current_price = None
                except Exception as e:
                    logger.debug(f"Could not get current price (will use target's price): {e}")
            
            base_price = current_price if current_price and current_price > 0 else original_price
            
            if base_price and base_price > 0:
                # Determine limit price based on configuration
                if config.gtc_use_exact_target_price:
                    limit_price = base_price
                    slippage_info = "exact target price (no slippage)"
                else:
                    # Calculate acceptable price with slippage
                    if side == TradeSide.BUY:
                        limit_price = base_price * (1 + config.price_slippage_tolerance)
                    else:
                        limit_price = base_price * (1 - config.price_slippage_tolerance)
                    slippage_info = f"{config.price_slippage_tolerance*100:.1f}% slippage"
                
                # Calculate size from amount
                if side == TradeSide.SELL:
                    size = amount_usd  # For SELL: amount_usd is actually share count
                    
                    # Ensure SELL orders meet 5-share minimum
                    if config.gtc_enforce_min_shares and size < 5.0:
                        logger.warning(f"⚠️  GTC SELL order too small ({size:.2f} shares < 5 minimum)")
                        logger.info(f"📈 Increasing to 5 shares minimum for GTC order")
                        size = 5.0
                else:
                    size = amount_usd / limit_price  # Convert USD to shares
                    
                    # Ensure BUY orders meet 5-share minimum
                    if config.gtc_enforce_min_shares and size < 5.0:
                        logger.warning(f"⚠️  GTC BUY order too small ({size:.2f} shares < 5 minimum)")
                        logger.info(f"📈 Increasing to 5 shares minimum for GTC order")
                        size = 5.0
                        adjusted_amount = size * limit_price
                        logger.info(f"💰 Adjusted GTC amount: ${adjusted_amount:.2f} (was ${amount_usd:.2f})")
                
                price_source = "current market price" if (current_price and current_price > 0) else "target's price (fallback)"
                
                logger.info(f"📊 Placing GTC-only order: {size:.2f} shares @ ${limit_price:.4f}")
                logger.info(f"   Base price: ${base_price:.4f} ({price_source})")
                logger.info(f"   Strategy: {slippage_info}")
                
                gtc_order_id = self.place_limit_order(token_id, side, size, limit_price)
                
                if gtc_order_id:
                    logger.success(f"✅ GTC-only order placed: {gtc_order_id}")
                    logger.info(f"⏰ Order will remain active until filled or cancelled")
                    
                    # Quick check if it fills immediately
                    await asyncio.sleep(0.5)
                    gtc_order_status = self.get_order_status(gtc_order_id)
                    
                    if gtc_order_status:
                        gtc_filled_size = float(gtc_order_status.get('size_matched', 0))
                        if gtc_filled_size > 0:
                            details['filled_amount'] = gtc_filled_size
                            details['avg_price'] = float(gtc_order_status.get('price', 0))
                            logger.success(f"✅ GTC-only order partially/fully filled: {gtc_filled_size} shares")
                            return gtc_order_id, OrderExecutionResult.SUCCESS, details
                    
                    # GTC order placed but not filled yet - still return as success
                    logger.info(f"📊 GTC-only order placed, waiting for fill...")
                    return gtc_order_id, OrderExecutionResult.SUCCESS, details
                else:
                    logger.error("❌ GTC-only order placement failed")
                    details['failure_reasons'].append("GTC-only order failed")
                    return None, OrderExecutionResult.FAILED, details
            else:
                logger.error("❌ Cannot place GTC-only order: no price data available")
                details['failure_reasons'].append("GTC-only: no price data")
                return None, OrderExecutionResult.FAILED, details
                
        except Exception as e:
            logger.error(f"❌ GTC-only order error: {e}")
            details['failure_reasons'].append(f"GTC-only error: {str(e)}")
            return None, OrderExecutionResult.FAILED, details
    
    def _get_tick_size(self, token_id: str) -> float:
        """Get the market tick size (defaults to 0.01 / 1 cent on failure)."""
        try:
            tick = self.client.get_tick_size(token_id)
            return float(tick)
        except Exception as e:
            logger.debug(f"Could not fetch tick size, defaulting to 0.01: {e}")
            return 0.01

    @staticmethod
    def _round_to_tick(price: float, tick: float) -> float:
        """Snap a price onto the market's tick grid."""
        if tick <= 0:
            return price
        # Determine decimal places from the tick (0.01 -> 2, 0.001 -> 3, ...)
        decimals = max(0, len(f"{tick:.10f}".rstrip('0').split('.')[-1]))
        steps = round(price / tick)
        return round(steps * tick, decimals)

    def _get_best_price(self, token_id: str, side: TradeSide) -> Optional[float]:
        """Get the best executable price for a side (ask for BUY, bid for SELL)."""
        try:
            # For a BUY we care about the best ask; for a SELL the best bid.
            quote_side = BUY if side == TradeSide.BUY else SELL
            data = self.client.get_price(token_id, quote_side)
            if isinstance(data, dict):
                raw = data.get('price', 0)
            else:
                raw = data
            price = float(raw)
            return price if price > 0 else None
        except Exception as e:
            logger.debug(f"Could not fetch best price: {e}")
            return None

    def _get_token_lock(self, token_id: str) -> asyncio.Lock:
        """Return (creating if needed) the per-token execution lock."""
        lock = self._token_locks.get(token_id)
        if lock is None:
            lock = asyncio.Lock()
            self._token_locks[token_id] = lock
        return lock

    def _find_open_order(self, order_id: str, token_id: str) -> Optional[Dict[str, Any]]:
        """Return the open-order record if order_id is still resting, else None."""
        try:
            for o in self.get_open_orders(token_id=token_id):
                oid = o.get('id') or o.get('orderID') or o.get('order_id')
                if oid == order_id:
                    return o
        except Exception as e:
            logger.debug(f"_find_open_order error: {e}")
        return None

    def _resolve_order_state(self, order_id: str, token_id: str) -> Tuple[str, float, float]:
        """Resolve the true state of an order using two independent sources.

        Returns (state, filled_size, price) where state is one of:
          - 'FILLED' : size_matched > 0 (from get_order or the open-order record)
          - 'OPEN'   : still present in the open-orders book, no fill
          - 'CLOSED' : not open and no fill reported (cancelled / gone)

        A filled GTC order is always reflected by get_order (size_matched persists),
        so 'CLOSED' reliably means "not resting and never filled". This is the basis
        for the no-duplicate guarantee in the weather chaser.
        """
        filled = 0.0
        price = 0.0

        status = self.get_order_status(order_id)  # get_order
        if status:
            try:
                filled = float(status.get('size_matched', 0) or 0)
            except (TypeError, ValueError):
                filled = 0.0
            try:
                price = float(status.get('price', 0) or 0)
            except (TypeError, ValueError):
                price = 0.0

        open_rec = self._find_open_order(order_id, token_id)
        if open_rec is not None:
            try:
                of = float(open_rec.get('size_matched', 0) or 0)
            except (TypeError, ValueError):
                of = 0.0
            filled = max(filled, of)
            if price == 0:
                try:
                    price = float(open_rec.get('price', 0) or 0)
                except (TypeError, ValueError):
                    price = 0.0
            if filled > 0:
                return ('FILLED', filled, price)
            return ('OPEN', 0.0, price)

        # Not in the open-orders book
        if filled > 0:
            return ('FILLED', filled, price)
        return ('CLOSED', 0.0, price)

    def _cancel_and_confirm(self, order_id: str, token_id: str) -> Tuple[str, float, float]:
        """Cancel an order and CONFIRM the outcome before the caller re-places.

        Returns (result, filled_size, price) where result is one of:
          - 'FILLED'    : the order filled (do NOT place a replacement)
          - 'CANCELLED' : confirmed gone & unfilled (safe to place the next order)
          - 'HOLD'      : could not confirm cancellation (still resting / ambiguous)
                          -> caller MUST NOT place a replacement, to avoid duplicates
        """
        # It may have filled before we even try to cancel
        state, filled, price = self._resolve_order_state(order_id, token_id)
        if filled > 0:
            return ('FILLED', filled, price)

        self.cancel_order(order_id)

        state, filled, price = self._resolve_order_state(order_id, token_id)
        if filled > 0:
            return ('FILLED', filled, price)
        if state == 'CLOSED':
            return ('CANCELLED', 0.0, price)

        # Still shows OPEN -> one more cancel attempt
        self.cancel_order(order_id)
        state, filled, price = self._resolve_order_state(order_id, token_id)
        if filled > 0:
            return ('FILLED', filled, price)
        if state == 'CLOSED':
            return ('CANCELLED', 0.0, price)

        # Still cannot confirm it's gone -> hold (never duplicate)
        return ('HOLD', 0.0, price)

    def _cancel_and_capture(self, order_id: str, token_id: str) -> Tuple[bool, float, float]:
        """Cancel an order and report how much it filled, confirming it's gone.

        Unlike _cancel_and_confirm (which short-circuits to 'FILLED' on any
        partial and never cancels the remainder), this ALWAYS cancels the
        unfilled remainder and returns the order's final matched size, so the
        weather chaser can chase the still-unfilled shares.

        Returns (confirmed_gone, filled_size, price):
          - confirmed_gone=True  : order is gone from the book AND we have a
            reliable get_order read of its matched size (safe to place a
            replacement for the unfilled remainder)
          - confirmed_gone=False : could NOT confirm both of those (HOLD) ->
            caller MUST NOT place a replacement, to avoid duplicates

        CRITICAL: a replacement is only ever placed when the prior order is
        positively gone *and* its fill amount is known. If get_order can't be
        read (network hiccup) we return HOLD rather than assume "unfilled" -
        that wrong assumption is exactly what causes duplicate orders.
        """
        def _read() -> Optional[Tuple[float, float]]:
            """Return (matched, price) from get_order, or None if unreadable."""
            status = self.get_order_status(order_id)
            if not status:
                return None
            try:
                f = float(status.get('size_matched', 0) or 0)
            except (TypeError, ValueError):
                f = 0.0
            try:
                p = float(status.get('price', 0) or 0)
            except (TypeError, ValueError):
                p = 0.0
            return (f, p)

        # If it's already gone, we still need a reliable fill read to proceed.
        if self._find_open_order(order_id, token_id) is None:
            r = _read()
            return (True, r[0], r[1]) if r is not None else (False, 0.0, 0.0)

        # Resting -> cancel, then require (gone AND readable) to confirm.
        for _ in range(2):
            self.cancel_order(order_id)
            if self._find_open_order(order_id, token_id) is None:
                r = _read()
                return (True, r[0], r[1]) if r is not None else (False, 0.0, 0.0)

        # Still resting / cannot confirm -> HOLD (never duplicate)
        r = _read()
        return (False, r[0] if r else 0.0, r[1] if r else 0.0)

    async def _place_weather_chase_order(
        self,
        token_id: str,
        side: TradeSide,
        amount_usd: float,
        original_price: Optional[float],
        details: dict
    ) -> Tuple[Optional[str], OrderExecutionResult, Dict[str, Any]]:
        """Weather mode: aggressively chase the price 1 tick ahead until filled.

        Strategy (BUY example): the target filled at e.g. 0.73, so we place a GTC
        limit buy 1 tick ahead at 0.74. A GTC buy priced at/above the ask fills
        immediately, so resting unfilled means the ask has moved up. We then
        CONFIRM-cancel the stale order and re-place 1 tick ahead of the new ask.
        SELL mirrors this 1 tick *below* the best bid (hits the bid -> fills),
        floored at 1 tick. Capped at 1 - tick (e.g. 0.99) for BUY.

        DUPLICATE-PROOF INVARIANT: a replacement order is only ever placed after
        `_cancel_and_confirm` returns 'CANCELLED' (positively gone & unfilled). If
        cancellation cannot be confirmed ('HOLD') or the order filled, we stop and
        never place another order. A per-token lock also prevents two concurrent
        chases on the same market.
        """
        config = self.trading_config

        # Serialize all execution for this token so concurrent trades on the same
        # market cannot place competing/duplicate orders.
        async with self._get_token_lock(token_id):
            try:
                tick = self._get_tick_size(token_id)
                max_price = round(1.0 - tick, 10)   # e.g. 0.99 for 0.01 tick
                min_price = tick                     # e.g. 0.01 for 0.01 tick

                # Seed the reference from the target's fill price, else live market
                reference = original_price if (original_price and original_price > 0) else None
                if reference is None:
                    reference = self._get_best_price(token_id, side)
                if reference is None or reference <= 0:
                    logger.error("❌ Weather: no price data available to chase")
                    details['failure_reasons'].append("weather: no price data")
                    return None, OrderExecutionResult.FAILED, details

                active_order_id: Optional[str] = None
                last_order_id: Optional[str] = None
                last_limit_price: Optional[float] = None
                total_filled = 0.0       # cumulative shares filled across all chases
                last_fill_price = 0.0

                # Target quantity in SHARES (copy intent). For SELL the amount is
                # already a share count; for BUY convert the USD budget at the
                # seed price. We chase until total_filled reaches this.
                if side == TradeSide.SELL:
                    target_shares = amount_usd
                else:
                    target_shares = (amount_usd / reference) if reference > 0 else 0.0
                if target_shares <= 0:
                    details['failure_reasons'].append("weather: invalid target size")
                    return None, OrderExecutionResult.FAILED, details

                EPS = 0.01            # shares: treat a remainder <= this as complete
                MIN_SHARES = 5.0      # Polymarket order minimum; can't replace below this

                def _finish(order_id, label):
                    """Record fill stats and pick a result code."""
                    details['filled_amount'] = total_filled
                    details['avg_price'] = last_fill_price or last_limit_price or 0.0
                    if total_filled >= target_shares - EPS:
                        logger.success(f"✅ Weather: filled {total_filled:.2f}/{target_shares:.2f} shares "
                                       f"@ ${details['avg_price']:.4f} ({label})")
                        return order_id or last_order_id, OrderExecutionResult.SUCCESS, details
                    if total_filled > EPS:
                        logger.warning(f"🌦️  Weather: partial {total_filled:.2f}/{target_shares:.2f} shares ({label})")
                        return order_id or last_order_id, OrderExecutionResult.PARTIAL_FILL, details
                    details['failure_reasons'].append(f"weather: no fill ({label})")
                    return None, OrderExecutionResult.FAILED, details

                for chase in range(config.weather_max_chases):
                    remaining = target_shares - total_filled
                    if remaining <= EPS:
                        break
                    # A remainder below the 5-share order minimum can't be
                    # placed - accept what we've filled so far. (First order is
                    # exempt: it gets bumped up to the minimum below.)
                    if total_filled > EPS and remaining < MIN_SHARES:
                        logger.info(f"🌦️  Weather: remainder {remaining:.2f} sh < {MIN_SHARES:.0f}-share "
                                    f"minimum - accepting {total_filled:.2f}/{target_shares:.2f} as filled")
                        break

                    # --- 1. Reclaim any prior resting order (capture its fill) ---
                    if active_order_id is not None:
                        confirmed, filled, price = self._cancel_and_capture(active_order_id, token_id)
                        total_filled += filled
                        if price:
                            last_fill_price = price
                        if not confirmed:
                            # Couldn't confirm cancellation -> stop, do NOT duplicate.
                            logger.warning(f"🌦️  Weather: could not confirm cancel of {active_order_id}; "
                                           f"leaving it resting and stopping (no duplicate)")
                            return _finish(active_order_id, "cancel-unconfirmed")
                        active_order_id = None
                        remaining = target_shares - total_filled
                        if remaining <= EPS or remaining < MIN_SHARES:
                            break

                    # --- 2. Compute the chase price (1 tick ahead of reference) ---
                    if side == TradeSide.BUY:
                        limit_price = round(min(self._round_to_tick(reference, tick) + tick, max_price), 10)
                    else:
                        limit_price = round(max(self._round_to_tick(reference, tick) - tick, min_price), 10)

                    # --- 3. Size = remaining shares. The 5-share minimum is only
                    #        bumped for the FIRST *BUY* (we have USD to overbuy).
                    #        A SELL is NEVER bumped: you can't sell shares you don't
                    #        own, so a sub-5 sell is skipped upstream and a >=5 sell
                    #        is placed as-is. ---
                    size = remaining
                    if (side == TradeSide.BUY and config.gtc_enforce_min_shares
                            and total_filled <= EPS and size < 5.0):
                        logger.info(f"📈 Weather: bumping BUY size to 5-share minimum (was {size:.2f})")
                        size = 5.0

                    logger.info(
                        f"🌦️  Weather chase {chase + 1}/{config.weather_max_chases}: "
                        f"{side.value} {size:.2f} shares @ ${limit_price:.4f} "
                        f"(1 tick ahead of ${reference:.4f}, tick={tick}; "
                        f"filled {total_filled:.2f}/{target_shares:.2f})"
                    )

                    # --- 4. Place the order ---
                    order_id = self.place_limit_order(token_id, side, size, limit_price)
                    if not order_id:
                        err = (self._last_place_error or "").lower()
                        # Insufficient balance/allowance is permanent for this
                        # order (e.g. SELL more shares than held) - do NOT retry.
                        if "balance" in err or "allowance" in err:
                            logger.error("❌ Weather: insufficient balance/allowance - not retryable, stopping")
                            details['failure_reasons'].append("weather: insufficient balance/allowance")
                            break
                        logger.error("❌ Weather: order placement failed")
                        details['failure_reasons'].append(f"weather chase {chase + 1}: placement failed")
                        new_ref = self._get_best_price(token_id, side)
                        if new_ref:
                            reference = new_ref
                            continue
                        break

                    active_order_id = order_id
                    last_order_id = order_id
                    last_limit_price = limit_price

                    # --- 5. Wait, then check how much THIS order filled ---
                    await asyncio.sleep(config.weather_fill_wait_seconds)
                    status = self.get_order_status(order_id)
                    filled_now = 0.0
                    if status:
                        try:
                            filled_now = float(status.get('size_matched', 0) or 0)
                        except (TypeError, ValueError):
                            filled_now = 0.0
                        try:
                            sp = float(status.get('price', 0) or 0)
                            if sp:
                                last_fill_price = sp
                        except (TypeError, ValueError):
                            pass

                    if filled_now >= size - EPS:
                        # This order fully filled -> overall target reached.
                        total_filled += filled_now
                        active_order_id = None
                        return _finish(order_id, "filled")

                    # --- 6. Partial/none fill. Decide: chase the remainder, or
                    #        accept it if it's below the 5-share order minimum. ---
                    remaining_after = target_shares - (total_filled + filled_now)
                    if remaining_after < MIN_SHARES:
                        # Can't place a sub-5-share replacement. Accept the
                        # partial as final and leave THIS order resting (its
                        # remainder may still fill; same order = no duplicate).
                        total_filled += filled_now
                        active_order_id = None  # leave it resting; don't reclaim
                        logger.info(f"🌦️  Weather: remainder {remaining_after:.2f} sh < {MIN_SHARES:.0f}-share "
                                    f"minimum - accepting {total_filled:.2f}/{target_shares:.2f} as filled, "
                                    f"leaving order {order_id} resting")
                        return _finish(order_id, "remainder-below-min")

                    # Remainder >= 5 -> chase: refresh the reference and loop. The
                    # top of the loop reclaims the remainder (confirmed gone) and
                    # re-prices 1 tick ahead of the new market.
                    new_ref = self._get_best_price(token_id, side)
                    if new_ref is not None:
                        reference = new_ref

                # --- Exhausted chases (or remainder too small). Leave the last
                #     order resting to catch late liquidity; report what filled. ---
                if active_order_id is not None:
                    status = self.get_order_status(active_order_id)
                    if status:
                        try:
                            total_filled += float(status.get('size_matched', 0) or 0)
                        except (TypeError, ValueError):
                            pass
                        try:
                            sp = float(status.get('price', 0) or 0)
                            if sp:
                                last_fill_price = sp
                        except (TypeError, ValueError):
                            pass
                    if total_filled < target_shares - EPS:
                        logger.warning(f"🌦️  Weather: exhausted {config.weather_max_chases} chases; "
                                       f"{total_filled:.2f}/{target_shares:.2f} filled, remainder left "
                                       f"resting as order {active_order_id}")
                    return _finish(active_order_id, "exhausted/resting")

                return _finish(last_order_id, "exhausted")

            except AccountRestrictedException:
                raise
            except Exception as e:
                logger.error(f"❌ Weather chase error: {e}")
                details['failure_reasons'].append(f"weather error: {str(e)}")
                return None, OrderExecutionResult.FAILED, details

    async def place_order_with_retry(
        self,
        token_id: str,
        side: TradeSide,
        amount_usd: float,
        original_price: Optional[float] = None
    ) -> Tuple[Optional[str], OrderExecutionResult, Dict[str, Any]]:
        """
        Enhanced order placement with retry logic and fallback strategies
        
        Returns:
            - order_id: The order ID if successful, None otherwise
            - result: OrderExecutionResult enum indicating outcome
            - details: Dictionary with execution details (filled_amount, price, etc.)
        """
        config = self.trading_config
        details = {
            'attempts': 0,
            'filled_amount': 0.0,
            'avg_price': 0.0,
            'strategy_used': 'FAK',
            'failure_reasons': []
        }
        
        logger.debug(f"🎯 Order execution: {side.value} ${amount_usd:.2f}")

        # Weather mode takes priority: aggressively chase the price 1 tick ahead.
        if config.use_weather_mode:
            logger.info("🌦️  Using WEATHER mode (chase price 1 tick ahead until filled)")
            details['strategy_used'] = 'WEATHER'
            return await self._place_weather_chase_order(token_id, side, amount_usd, original_price, details)

        # Check if we should use GTC-only mode (skip FAK entirely)
        if config.use_gtc_only:
            logger.info("🎯 Using GTC-only mode (skipping FAK for more accurate % copying)")
            details['strategy_used'] = 'GTC_ONLY'
            
            # Jump directly to GTC placement logic
            return await self._place_gtc_order_directly(token_id, side, amount_usd, original_price, details)
        
        # Get current market price for FAK fill percentage calculation
        # Can be skipped for maximum speed (uses target's price instead)
        current_price = None
        if not config.skip_price_fetch_for_speed:
            try:
                midpoint_data = self.client.get_midpoint(token_id)
                # get_midpoint returns a dict with 'mid' key
                if isinstance(midpoint_data, dict):
                    current_price = float(midpoint_data.get('mid', 0))
                elif isinstance(midpoint_data, (int, float)):
                    current_price = float(midpoint_data)
                
                if current_price and current_price > 0:
                    logger.debug(f"Current market price: ${current_price:.4f}")
                else:
                    current_price = None
            except Exception as e:
                logger.debug(f"Could not get current price (will use target's price): {e}")
        else:
            logger.debug("Skipping price fetch for maximum speed (using target's price)")
        
        # Strategy 1: Try FAK orders with retries
        fak_order_id = None
        fak_filled_size = 0.0
        fak_fill_percentage = 0.0
        
        for attempt in range(config.max_order_retries):
            details['attempts'] = attempt + 1
            logger.debug(f"📤 FAK attempt {attempt + 1}/{config.max_order_retries}")
            
            try:
                order_id = self.place_market_order(token_id, side, amount_usd)
                
                if order_id:
                    fak_order_id = order_id
                    # Check order status with optimized timing and retry
                    await asyncio.sleep(0.05)  # Fast first check (50ms)
                    order_status = self.get_order_status(order_id)
                    
                    # Retry once if not found (handles slow registration)
                    if not order_status:
                        logger.debug(f"Order status not ready, retrying...")
                        await asyncio.sleep(0.05)  # Wait another 50ms
                        order_status = self.get_order_status(order_id)
                    
                    if order_status:
                        # Check fill status
                        filled_size = float(order_status.get('size_matched', 0))
                        
                        # Calculate requested size
                        # For SELL: amount_usd is actually share count (not USD)
                        # For BUY: amount_usd is USD, need to convert to shares
                        if side == TradeSide.SELL:
                            # For SELL, amount_usd is already the share count
                            requested_size = amount_usd
                        else:
                            # For BUY, convert USD to shares using current price
                            if current_price and current_price > 0:
                                requested_size = amount_usd / current_price
                            elif original_price and original_price > 0:
                                # Fallback to target's price if current price unavailable
                                requested_size = amount_usd / original_price
                            else:
                                # Last resort: use size from order status
                                requested_size = float(order_status.get('original_size', filled_size))
                        
                        fill_percentage = (filled_size / requested_size) if requested_size > 0 else 0
                        
                        fak_filled_size = filled_size
                        fak_fill_percentage = fill_percentage
                        
                        details['filled_amount'] = filled_size
                        details['avg_price'] = float(order_status.get('price', 0))
                        
                        # Full fill (≥90%) - success, no need for GTC!
                        if fill_percentage >= 0.90:  # 90% or more is considered full
                            logger.success(f"✅ Order fully filled: {order_id} ({fill_percentage*100:.1f}%)")
                            return order_id, OrderExecutionResult.SUCCESS, details
                        
                        # Any partial fill (<90%) - accept what we got, no GTC!
                        elif fill_percentage > 0:
                            logger.warning(f"⚠️  Partial fill: {order_id} ({fill_percentage*100:.1f}%)")
                            logger.info(f"📊 Accepting partial fill (no GTC for partial fills)")
                            break  # Exit FAK loop to handle partial fill
                        
                        else:
                            # 0% fill - completely failed
                            logger.warning(f"❌ No fill: {order_id} (0% filled)")
                            details['failure_reasons'].append(f"Attempt {attempt+1}: 0% filled")
                    else:
                        logger.warning(f"⚠️  Could not verify order status for {order_id}")
                        details['failure_reasons'].append(f"Attempt {attempt+1}: Status check failed")
                else:
                    logger.warning(f"❌ Order placement failed on attempt {attempt + 1}")
                    details['failure_reasons'].append(f"Attempt {attempt+1}: Order placement failed")
                
                # Wait before retry
                if attempt < config.max_order_retries - 1:
                    logger.info(f"⏳ Waiting {config.retry_delay_seconds}s before retry...")
                    await asyncio.sleep(config.retry_delay_seconds)
                    
            except AccountRestrictedException:
                # Re-raise account restrictions immediately
                raise
            except Exception as e:
                logger.error(f"❌ Error on attempt {attempt + 1}: {e}")
                details['failure_reasons'].append(f"Attempt {attempt+1}: {str(e)}")
                
                if attempt < config.max_order_retries - 1:
                    await asyncio.sleep(config.retry_delay_seconds)
        
        # Strategy 2: Fallback to GTC order if enabled
        # Use GTC for both BUY and SELL when FAK completely fails (0% fill)
        # Do NOT use GTC for partial fills - accept what we got
        if config.use_gtc_fallback and fak_filled_size == 0:
            # FAK completely failed (0% fill) - place GTC for full amount
            logger.warning("🔄 FAK orders failed completely, trying GTC fallback...")
            details['strategy_used'] = 'GTC_FALLBACK'
            gtc_amount = amount_usd
            
            try:
                # Use current market price for GTC orders (same as FAK)
                # Market may have moved since target's execution
                base_price = current_price if current_price and current_price > 0 else original_price
                
                if base_price and base_price > 0:
                    # Determine limit price based on configuration
                    if config.gtc_use_exact_target_price:
                        # Use exact current price (no slippage)
                        limit_price = base_price
                        slippage_info = "exact current price (no slippage)"
                    else:
                        # Calculate acceptable price with slippage FROM CURRENT MARKET PRICE
                        if side == TradeSide.BUY:
                            # For buys, willing to pay slightly more than current market
                            limit_price = base_price * (1 + config.price_slippage_tolerance)
                        else:
                            # For sells, willing to accept slightly less than current market
                            limit_price = base_price * (1 - config.price_slippage_tolerance)
                        slippage_info = f"{config.price_slippage_tolerance*100:.1f}% slippage"
                    
                    # Calculate size from GTC amount (only for 0% FAK fills)
                    # For SELL: gtc_amount is already share count, use directly
                    # For BUY: gtc_amount is USD, convert to shares
                    if side == TradeSide.SELL:
                        size = gtc_amount  # Already in shares
                        
                        # Ensure SELL orders meet 5-share minimum for GTC/limit orders
                        if config.gtc_enforce_min_shares and size < 5.0:
                            logger.warning(f"⚠️  GTC SELL order too small ({size:.2f} shares < 5 minimum)")
                            logger.info(f"📈 Increasing to 5 shares minimum for GTC order")
                            size = 5.0
                            # Update gtc_amount to reflect the increased size
                            gtc_amount = size
                    else:
                        size = gtc_amount / limit_price  # Convert USD to shares
                        
                        # Ensure BUY orders meet 5-share minimum for GTC/limit orders
                        if config.gtc_enforce_min_shares and size < 5.0:
                            logger.warning(f"⚠️  GTC BUY order too small ({size:.2f} shares < 5 minimum)")
                            logger.info(f"📈 Increasing to 5 shares minimum for GTC order")
                            size = 5.0
                            # Update gtc_amount to reflect the increased USD amount needed
                            gtc_amount = size * limit_price
                            logger.info(f"💰 Adjusted GTC amount: ${gtc_amount:.2f} (was ${amount_usd:.2f})")
                    
                    price_source = "current market price" if (current_price and current_price > 0) else "target's price (fallback)"
                    
                    # Since we only reach here if fak_filled_size == 0
                    logger.info(f"📊 Placing GTC order: {size:.2f} shares @ ${limit_price:.4f}")
                    logger.info(f"   Base price: ${base_price:.4f} ({price_source})")
                    logger.info(f"   Strategy: {slippage_info}")
                    
                    gtc_order_id = self.place_limit_order(token_id, side, size, limit_price)
                    
                    if gtc_order_id:
                        logger.success(f"✅ GTC order placed: {gtc_order_id}")
                        logger.info(f"⏰ Order will remain active until filled or cancelled")
                        
                        # Quick check if it fills immediately
                        await asyncio.sleep(0.5)
                        gtc_order_status = self.get_order_status(gtc_order_id)
                        
                        if gtc_order_status:
                            gtc_filled_size = float(gtc_order_status.get('size_matched', 0))
                            if gtc_filled_size > 0:
                                details['filled_amount'] = gtc_filled_size
                                details['avg_price'] = float(gtc_order_status.get('price', 0))
                                logger.success(f"✅ GTC order partially/fully filled: {gtc_filled_size} shares")
                                return gtc_order_id, OrderExecutionResult.SUCCESS, details
                        
                        # GTC order placed but not filled yet - still return as success
                        logger.info(f"📊 GTC order placed, waiting for fill...")
                        return gtc_order_id, OrderExecutionResult.SUCCESS, details
                    else:
                        logger.error("❌ GTC order placement failed")
                        details['failure_reasons'].append("GTC fallback failed")
                else:
                    logger.error("❌ Cannot place GTC order: no price data available")
                    details['failure_reasons'].append("GTC fallback: no price data")
                    
            except AccountRestrictedException:
                raise
            except Exception as e:
                logger.error(f"❌ GTC fallback error: {e}")
                details['failure_reasons'].append(f"GTC fallback: {str(e)}")
        elif fak_filled_size > 0:
            # Partial fill (for both BUY and SELL) - accept what we got
            logger.warning(f"⚠️  Order partially filled: {fak_filled_size:.2f} shares ({fak_fill_percentage*100:.1f}%)")
            logger.info(f"📊 Accepting partial fill (no GTC for partial fills)")
            details['filled_amount'] = fak_filled_size
            return fak_order_id, OrderExecutionResult.PARTIAL_FILL, details
        
        # All strategies failed
        logger.error(f"❌ All order execution strategies failed after {details['attempts']} attempts")
        logger.error(f"Failure reasons: {', '.join(details['failure_reasons'])}")
        
        return None, OrderExecutionResult.FAILED, details
