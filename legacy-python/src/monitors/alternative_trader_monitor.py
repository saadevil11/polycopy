"""
Alternative trader monitoring using public data sources
"""
import asyncio
import requests
import time
from datetime import datetime, timedelta
from typing import List, Set, Optional, Dict, Any
from loguru import logger
import json

from src.core.models import TraderTrade, TradeSide, MarketInfo
from src.core.config import trading_config


class AlternativeTraderMonitor:
    """
    Alternative trader monitor using public APIs and subgraph data
    """
    
    def __init__(self):
        self.config = trading_config
        self.target_address = self.config.target_trader_address.lower()
        self.monitoring = False
        self.last_check_time = datetime.now()
        self.seen_trade_ids: Set[str] = set()
        self.new_trade_callbacks = []
        
        # API endpoints
        self.subgraph_url = "https://api.thegraph.com/subgraphs/name/polymarket/polymarket"
        self.gamma_api_url = "https://gamma-api.polymarket.com"
        
    def add_new_trade_callback(self, callback):
        """Add callback for new trades"""
        self.new_trade_callbacks.append(callback)
    
    def start_monitoring(self):
        """Start monitoring using alternative methods"""
        logger.info(f"Starting alternative monitoring for trader: {self.target_address}")
        self.monitoring = True
        
        # Initialize seen trades
        self._initialize_seen_trades()
        
        # Start monitoring loop
        asyncio.create_task(self._monitoring_loop())
    
    def stop_monitoring(self):
        """Stop monitoring"""
        logger.info("Stopping alternative trader monitoring")
        self.monitoring = False
    
    def _initialize_seen_trades(self):
        """Initialize seen trades using subgraph"""
        try:
            logger.info("Initializing seen trades from subgraph...")
            trades = self._get_trades_from_subgraph(limit=50)
            
            for trade in trades:
                self.seen_trade_ids.add(trade.trade_id)
            
            logger.info(f"Initialized with {len(self.seen_trade_ids)} existing trades")
            
        except Exception as e:
            logger.error(f"Failed to initialize seen trades: {e}")
    
    async def _monitoring_loop(self):
        """Main monitoring loop"""
        logger.info("Starting alternative monitoring loop")
        
        while self.monitoring:
            try:
                await self._check_for_new_trades()
                await asyncio.sleep(self.config.monitoring_interval_seconds)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(30)  # Wait longer on error
    
    async def _check_for_new_trades(self):
        """Check for new trades using multiple data sources"""
        try:
            current_time = datetime.now()
            logger.debug(f"Checking for new trades at {current_time}")
            
            # Method 1: Try subgraph
            new_trades_subgraph = self._get_trades_from_subgraph(limit=10)
            
            # Method 2: Try activity feed (if available)
            new_trades_activity = self._get_trades_from_activity_feed()
            
            # Combine and deduplicate
            all_new_trades = []
            for trade in new_trades_subgraph + new_trades_activity:
                if trade.trade_id not in self.seen_trade_ids:
                    # Only consider recent trades
                    if trade.timestamp > (current_time - timedelta(hours=1)):
                        all_new_trades.append(trade)
                        self.seen_trade_ids.add(trade.trade_id)
            
            if all_new_trades:
                logger.info(f"Found {len(all_new_trades)} new trades")
                
                for trade in sorted(all_new_trades, key=lambda x: x.timestamp, reverse=True):
                    await self._process_new_trade(trade)
            
            self.last_check_time = current_time
            
        except Exception as e:
            logger.error(f"Failed to check for new trades: {e}")
    
    def _get_trades_from_subgraph(self, limit: int = 20) -> List[TraderTrade]:
        """Get trades from The Graph subgraph"""
        try:
            # GraphQL query for user trades
            query = """
            {
                fpmmTrades(
                    first: %d
                    orderBy: creationTimestamp
                    orderDirection: desc
                    where: {
                        creator: "%s"
                    }
                ) {
                    id
                    creator
                    fpmm {
                        id
                        title
                        outcomes
                        condition {
                            id
                        }
                    }
                    outcomeTokensTraded
                    outcomeIndex
                    investmentAmount
                    feeAmount
                    creationTimestamp
                    transactionHash
                }
            }
            """ % (limit, self.target_address)
            
            response = requests.post(
                self.subgraph_url,
                json={'query': query},
                timeout=10
            )
            
            if response.status_code != 200:
                logger.warning(f"Subgraph request failed: {response.status_code}")
                return []
            
            data = response.json()
            if 'errors' in data:
                logger.warning(f"Subgraph query errors: {data['errors']}")
                return []
            
            trades = []
            for trade_data in data.get('data', {}).get('fpmmTrades', []):
                try:
                    # Convert subgraph data to TraderTrade
                    trade = self._convert_subgraph_trade(trade_data)
                    if trade:
                        trades.append(trade)
                except Exception as e:
                    logger.warning(f"Failed to convert subgraph trade: {e}")
                    continue
            
            logger.debug(f"Retrieved {len(trades)} trades from subgraph")
            return trades
            
        except Exception as e:
            logger.error(f"Failed to get trades from subgraph: {e}")
            return []
    
    def _get_trades_from_activity_feed(self) -> List[TraderTrade]:
        """Get trades from activity feed API"""
        try:
            # Try to get recent activity
            url = f"{self.gamma_api_url}/events"
            params = {
                'limit': 20,
                'offset': 0
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            trades = []
            
            # Process activity data (this would need to be adapted based on actual API structure)
            # For now, return empty list as we need to discover the correct API endpoints
            
            return trades
            
        except Exception as e:
            logger.debug(f"Activity feed not available: {e}")
            return []
    
    def _convert_subgraph_trade(self, trade_data: Dict[str, Any]) -> Optional[TraderTrade]:
        """Convert subgraph trade data to TraderTrade"""
        try:
            fpmm = trade_data.get('fpmm', {})
            
            # Determine side based on investment amount vs tokens traded
            investment = float(trade_data.get('investmentAmount', 0))
            tokens_traded = float(trade_data.get('outcomeTokensTraded', 0))
            
            # If investment > 0, it's a buy; if tokens_traded > investment, it's likely a sell
            side = TradeSide.BUY if investment > 0 else TradeSide.SELL
            
            # Calculate price (investment / tokens for buys)
            price = investment / tokens_traded if tokens_traded > 0 else 0
            
            trade = TraderTrade(
                trade_id=trade_data.get('id', ''),
                trader_address=self.target_address,
                market_id=fpmm.get('condition', {}).get('id', ''),
                token_id=fpmm.get('id', ''),
                side=side,
                price=price,
                size=tokens_traded,
                amount_usd=investment,
                timestamp=datetime.fromtimestamp(int(trade_data.get('creationTimestamp', 0))),
                transaction_hash=trade_data.get('transactionHash', ''),
                market_info=MarketInfo(
                    market_id=fpmm.get('condition', {}).get('id', ''),
                    token_id=fpmm.get('id', ''),
                    title=fpmm.get('title', ''),
                    description='',
                    outcome=fpmm.get('outcomes', ['', ''])[int(trade_data.get('outcomeIndex', 0))],
                    current_price=price,
                    liquidity_usd=0,
                    volume_24h_usd=0,
                    is_active=True
                )
            )
            
            return trade
            
        except Exception as e:
            logger.error(f"Failed to convert subgraph trade: {e}")
            return None
    
    async def _process_new_trade(self, trade: TraderTrade):
        """Process a new trade"""
        try:
            logger.info(f"Processing new trade from subgraph: {trade.trade_id}")
            logger.info(f"  Market: {trade.market_info.title if trade.market_info else 'Unknown'}")
            logger.info(f"  Side: {trade.side.value}")
            logger.info(f"  Size: {trade.size}")
            logger.info(f"  Price: {trade.price}")
            logger.info(f"  Amount: ${trade.amount_usd:.2f}")
            
            # Apply delay
            if self.config.trade_delay_seconds > 0:
                logger.info(f"Waiting {self.config.trade_delay_seconds} seconds...")
                await asyncio.sleep(self.config.trade_delay_seconds)
            
            # Notify callbacks
            for callback in self.new_trade_callbacks:
                try:
                    callback(trade)
                except Exception as e:
                    logger.error(f"Error in trade callback: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to process new trade: {e}")
    
    def get_monitoring_status(self) -> dict:
        """Get monitoring status"""
        return {
            'monitoring': self.monitoring,
            'target_address': self.target_address,
            'last_check_time': self.last_check_time.isoformat(),
            'seen_trades_count': len(self.seen_trade_ids),
            'monitoring_interval': self.config.monitoring_interval_seconds,
            'method': 'subgraph + activity_feed'
        }
