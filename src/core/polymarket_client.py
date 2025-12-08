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

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, MarketOrderArgs, OrderType, 
    TradeParams, OpenOrderParams, BookParams
)
from py_clob_client.order_builder.constants import BUY, SELL

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
        
    def initialize(self) -> bool:
        """Initialize the Polymarket client"""
        try:
            self._client = ClobClient(
                host=self.config.clob_api_url,
                key=self.config.private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,
                funder=self.config.funder_address
            )
            
            # Create or derive API credentials
            if not all([self.config.api_key, self.config.api_secret, self.config.api_passphrase]):
                logger.info("Creating API credentials...")
                creds = self._client.create_or_derive_api_creds()
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
    
    def get_current_positions(self) -> List[Position]:
        """Get current positions for the bot account"""
        try:
            # In dry run mode, return empty positions
            if self.bot_config.dry_run:
                logger.debug("[DRY RUN] Position tracking skipped")
                return []
            
            # Position tracking in live mode is not critical for bot operation
            # The risk manager tracks positions internally from executed trades
            logger.debug("Position tracking skipped (not required for operation)")
            return []
            
        except Exception as e:
            logger.error(f"Failed to get current positions: {e}")
            return []
            
    def _get_positions_from_database(self) -> List[Position]:
        """Get positions from database (simplified - not critical for bot operation)"""
        # Position tracking is not essential for the bot to function
        # The risk manager will track positions based on executed trades
        logger.debug("Position tracking skipped (not required for operation)")
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
            from web3 import Web3
            
            # Polygon RPC
            rpc_url = "https://polygon-rpc.com"
            w3 = Web3(Web3.HTTPProvider(rpc_url))
            
            if not w3.is_connected():
                logger.error("Could not connect to Polygon network")
                return 0.0
            
            # Get funder address
            funder_address = self.config.funder_address
            if not funder_address:
                logger.error("FUNDER_ADDRESS not configured")
                return 0.0
            
            # Convert to checksum address
            funder_address = w3.to_checksum_address(funder_address)
            
            # USDC contract on Polygon
            usdc_address = w3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
            
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
            return 0.0
    
    def place_market_order(self, token_id: str, side: TradeSide, amount_usd: float) -> Optional[str]:
        """Place a market order"""
        try:
            order_side = BUY if side == TradeSide.BUY else SELL
            
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount_usd,
                side=order_side
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
            
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)
            
            if response.get('success'):
                order_id = response.get('orderID')
                logger.success(f"Limit order placed successfully: {order_id}")
                return order_id
            else:
                logger.error(f"Failed to place limit order: {response}")
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
            
            logger.error(f"Failed to place limit order: {e}")
            return None
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            response = self.client.cancel(order_id)
            if response.get('success'):
                logger.info(f"Order cancelled successfully: {order_id}")
                return True
            else:
                logger.error(f"Failed to cancel order: {response}")
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
