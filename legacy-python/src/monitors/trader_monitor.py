"""
Trader monitoring system for the copy trading bot
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Set, Optional, Callable
from loguru import logger

from src.core.polymarket_client import PolymarketClient
from src.core.models import TraderTrade, TradeSide
from src.core.config import trading_config


class TraderMonitor:
    """Monitors target trader for new trades"""
    
    def __init__(self, polymarket_client: PolymarketClient):
        self.client = polymarket_client
        self.config = trading_config
        self.target_address = self.config.target_trader_address
        self.monitoring = False
        self.last_check_time = datetime.now()
        self.seen_trade_ids: Set[str] = set()
        self.new_trade_callbacks: List[Callable[[TraderTrade], None]] = []
        
    def add_new_trade_callback(self, callback: Callable[[TraderTrade], None]):
        """Add callback to be called when new trade is detected"""
        self.new_trade_callbacks.append(callback)
    
    def start_monitoring(self):
        """Start monitoring the target trader"""
        if not self.target_address:
            raise ValueError("Target trader address not configured")
            
        logger.info(f"Starting to monitor trader: {self.target_address}")
        self.monitoring = True
        
        # Initialize with recent trades to avoid copying old trades
        self._initialize_seen_trades()
        
        # Start monitoring loop
        asyncio.create_task(self._monitoring_loop())
    
    def stop_monitoring(self):
        """Stop monitoring the target trader"""
        logger.info("Stopping trader monitoring")
        self.monitoring = False
    
    def _initialize_seen_trades(self):
        """Initialize the set of seen trades to avoid copying old trades"""
        try:
            logger.info("Initializing seen trades...")
            recent_trades = self.client.get_trader_recent_trades(
                self.target_address, 
                limit=50  # Look at last 50 trades to initialize
            )
            
            for trade in recent_trades:
                self.seen_trade_ids.add(trade.trade_id)
            
            logger.info(f"Initialized with {len(self.seen_trade_ids)} existing trades")
            
        except Exception as e:
            logger.error(f"Failed to initialize seen trades: {e}")
    
    async def _monitoring_loop(self):
        """Main monitoring loop"""
        logger.info("Starting monitoring loop")
        
        while self.monitoring:
            try:
                await self._check_for_new_trades()
                await asyncio.sleep(self.config.monitoring_interval_seconds)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(5)  # Wait before retrying
    
    async def _check_for_new_trades(self):
        """Check for new trades from the target trader"""
        try:
            current_time = datetime.now()
            logger.debug(f"Checking for new trades at {current_time}")
            
            # Get recent trades
            recent_trades = self.client.get_trader_recent_trades(
                self.target_address,
                limit=20  # Check last 20 trades for new ones
            )
            
            new_trades = []
            for trade in recent_trades:
                # Check if this is a new trade
                if trade.trade_id not in self.seen_trade_ids:
                    # Additional check: only consider trades from the last hour to avoid old trades
                    if trade.timestamp > (current_time - timedelta(hours=1)):
                        new_trades.append(trade)
                        self.seen_trade_ids.add(trade.trade_id)
            
            if new_trades:
                logger.info(f"Found {len(new_trades)} new trades")
                
                # Process new trades (newest first)
                for trade in sorted(new_trades, key=lambda x: x.timestamp, reverse=True):
                    await self._process_new_trade(trade)
            
            self.last_check_time = current_time
            
        except Exception as e:
            logger.error(f"Failed to check for new trades: {e}")
    
    async def _process_new_trade(self, trade: TraderTrade):
        """Process a new trade from the target trader"""
        try:
            logger.info(f"Processing new trade: {trade.trade_id}")
            logger.info(f"  Market: {trade.market_id}")
            logger.info(f"  Side: {trade.side.value}")
            logger.info(f"  Size: {trade.size}")
            logger.info(f"  Price: {trade.price}")
            logger.info(f"  Amount: ${trade.amount_usd:.2f}")
            
            # Get market information
            market_info = self.client.get_market_info(trade.token_id)
            if market_info:
                trade.market_info = market_info
                logger.info(f"  Market Title: {market_info.title}")
                logger.info(f"  Outcome: {market_info.outcome}")
            
            # Apply delay before copying (to avoid front-running)
            if self.config.trade_delay_seconds > 0:
                logger.info(f"Waiting {self.config.trade_delay_seconds} seconds before copying...")
                await asyncio.sleep(self.config.trade_delay_seconds)
            
            # Notify callbacks about new trade
            for callback in self.new_trade_callbacks:
                try:
                    callback(trade)
                except Exception as e:
                    logger.error(f"Error in trade callback: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to process new trade {trade.trade_id}: {e}")
    
    def get_monitoring_status(self) -> dict:
        """Get current monitoring status"""
        return {
            'monitoring': self.monitoring,
            'target_address': self.target_address,
            'last_check_time': self.last_check_time.isoformat(),
            'seen_trades_count': len(self.seen_trade_ids),
            'monitoring_interval': self.config.monitoring_interval_seconds
        }
