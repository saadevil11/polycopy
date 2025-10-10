"""
Polymarket API client wrapper for copy trading bot
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import requests
from loguru import logger

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, MarketOrderArgs, OrderType, 
    TradeParams, OpenOrderParams, BookParams
)
from py_clob_client.order_builder.constants import BUY, SELL

from src.core.config import polymarket_config, trading_config, bot_config
from src.core.models import TraderTrade, MarketInfo, TradeSide, Position


class PolymarketClient:
    """Wrapper for Polymarket API interactions"""
    
    def __init__(self):
        self.config = polymarket_config
        self.trading_config = trading_config
        self.bot_config = bot_config
        self._client: Optional[ClobClient] = None
        self._session = requests.Session()
        
        # Balance cache for speed optimization
        self._balance_cache = None
        self._balance_cache_time = None
        self._balance_cache_ttl = timedelta(seconds=60)  # Cache for 60 seconds
        
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
        """Get market information for a token ID"""
        try:
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
