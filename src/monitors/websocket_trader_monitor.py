"""
WebSocket-based trader monitoring using Polymarket's real-time data
"""
import asyncio
import json
import websockets
import time
from datetime import datetime, timedelta
from typing import List, Set, Optional, Dict, Any
from loguru import logger

from src.core.models import TraderTrade, TradeSide, MarketInfo
from src.core.config import trading_config
from src.core.market_filters import MarketFilter


class WebSocketTraderMonitor:
    """
    Monitor trader using WebSocket real-time data
    """
    
    def __init__(self):
        self.config = trading_config
        self.target_address = self.config.target_trader_address.lower()
        self.monitoring = False
        self.last_check_time = datetime.now()
        self.seen_trade_ids: Set[str] = set()
        self.new_trade_callbacks = []
        self.market_filter = MarketFilter()
        
        # WebSocket connection
        self.websocket = None
        self.ws_url = "wss://ws-live-data.polymarket.com"
        self.last_message_time = datetime.now()
        self.connection_count = 0
        self.is_connected = False
        
    def add_new_trade_callback(self, callback):
        """Add callback for new trades"""
        self.new_trade_callbacks.append(callback)
    
    def start_monitoring(self):
        """Start WebSocket monitoring"""
        logger.info(f"Starting WebSocket monitoring for trader: {self.target_address}")
        self.monitoring = True
        
        # Start WebSocket connection
        asyncio.create_task(self._websocket_loop())
    
    def stop_monitoring(self):
        """Stop monitoring"""
        logger.info("Stopping WebSocket trader monitoring")
        self.monitoring = False
        if self.websocket:
            asyncio.create_task(self.websocket.close())
    
    async def _websocket_loop(self):
        """Main WebSocket loop with health monitoring"""
        while self.monitoring:
            try:
                await self._connect_websocket()
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                self.is_connected = False
                logger.info("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self.is_connected = False
                logger.info("Reconnecting in 10 seconds...")
                await asyncio.sleep(10)
    
    async def _connect_websocket(self):
        """Connect to WebSocket and listen for trades"""
        try:
            self.connection_count += 1
            logger.info(f"Connecting to Polymarket WebSocket... (attempt #{self.connection_count})")
            
            # Add ping_interval and ping_timeout for better connection health
            async with websockets.connect(
                self.ws_url,
                ping_interval=20,  # Send ping every 20 seconds
                ping_timeout=10,   # Wait 10 seconds for pong
                close_timeout=5
            ) as websocket:
                self.websocket = websocket
                self.is_connected = True
                self.last_message_time = datetime.now()
                logger.success(f"✅ WebSocket connected (connection #{self.connection_count})")
                
                # Subscribe to activity/trades
                subscribe_message = {
                    "action": "subscribe",
                    "subscriptions": [
                        {
                            "topic": "activity",
                            "type": "trades"
                        }
                    ]
                }
                
                await websocket.send(json.dumps(subscribe_message))
                logger.info("📡 Subscribed to activity/trades")
                
                # Start health check task
                health_check_task = asyncio.create_task(self._health_check())
                
                try:
                    # Listen for messages
                    async for message in websocket:
                        try:
                            self.last_message_time = datetime.now()
                            await self._handle_websocket_message(message)
                        except Exception as e:
                            logger.error(f"Error handling WebSocket message: {e}")
                finally:
                    health_check_task.cancel()
                        
        except Exception as e:
            self.is_connected = False
            logger.error(f"WebSocket connection error: {e}")
            raise
    
    async def _health_check(self):
        """Monitor WebSocket connection health"""
        while self.is_connected:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                time_since_last_message = (datetime.now() - self.last_message_time).total_seconds()
                
                if time_since_last_message > 60:
                    logger.warning(f"⚠️  No messages received for {time_since_last_message:.0f} seconds")
                    logger.info("WebSocket may be stale, will reconnect...")
                    if self.websocket:
                        await self.websocket.close()
                    break
                else:
                    logger.debug(f"✅ WebSocket healthy (last message {time_since_last_message:.0f}s ago)")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _handle_websocket_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            if message == "ping":
                return
            
            if not message.strip():
                return
                
            data = json.loads(message)
            
            # Check if it's a trade message
            if (data.get('topic') == 'activity' and 
                data.get('type') == 'trades' and 
                'payload' in data):
                
                payload = data['payload']
                
                # Check if this trade is from our target trader
                trader_address = payload.get('proxyWallet', '').lower()
                
                if trader_address == self.target_address:
                    # Check if this is a special action
                    action_type = payload.get('action', payload.get('type', ''))
                    
                    if action_type.lower() == 'merge':
                        logger.info(f"🔄 Merge action from target trader detected!")
                        await self._process_merge_action(payload)
                    elif action_type.lower() == 'redeem':
                        logger.info(f"💰 Redeem action from target trader detected!")
                        await self._process_redeem_action(payload)
                    else:
                        # Get market title for filtering
                        market_title = payload.get('title', '')
                        if not market_title:
                            market_info = payload.get('marketInfo', {})
                            market_title = market_info.get('title', '')
                            
                        # Check market filter ONCE before processing
                        if market_title and not self.market_filter.should_copy_market(market_title):
                            logger.debug(f"⏭️  Skipped (filter): {market_title}")
                            return
                            
                        logger.info(f"🎯 Trade from target trader detected!")
                        
                        # Convert to TraderTrade
                        trade = await self._convert_websocket_trade(payload)
                        if trade:
                            await self._process_new_trade(trade)
                        
        except json.JSONDecodeError:
            logger.debug("Received non-JSON message")
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")
    
    async def _process_merge_action(self, payload: Dict[str, Any]):
        """Process merge action from target trader"""
        try:
            market_id = payload.get('marketId', payload.get('market_id', ''))
            amount = float(payload.get('amount', 0))
            
            logger.info(f"Target trader merged {amount} positions in market {market_id}")
            
            # Create a special merge "trade" object to notify callbacks
            merge_action = {
                'action': 'merge',
                'market_id': market_id,
                'amount': amount,
                'trader_address': self.target_address,
                'timestamp': datetime.now().isoformat()
            }
            
            # Notify callbacks about merge action
            for callback in self.new_trade_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(merge_action)
                    else:
                        callback(merge_action)
                except Exception as e:
                    logger.error(f"Error in merge callback: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing merge action: {e}")
    
    async def _process_redeem_action(self, payload: Dict[str, Any]):
        """Process redeem action from target trader"""
        try:
            market_id = payload.get('marketId', payload.get('market_id', ''))
            amount = float(payload.get('amount', 0))
            
            logger.info(f"Target trader redeemed {amount} winning tokens in market {market_id}")
            
            # Create a special redeem "action" object to notify callbacks
            redeem_action = {
                'action': 'redeem',
                'market_id': market_id,
                'amount': amount,
                'trader_address': self.target_address,
                'timestamp': datetime.now().isoformat()
            }
            
            # Notify callbacks about redeem action
            for callback in self.new_trade_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(redeem_action)
                    else:
                        callback(redeem_action)
                except Exception as e:
                    logger.error(f"Error in redeem callback: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing redeem action: {e}")
    
    async def _convert_websocket_trade(self, payload: Dict[str, Any]) -> Optional[TraderTrade]:
        """Convert WebSocket trade payload to TraderTrade"""
        try:
            # Extract trade information
            # Use only transaction hash for duplicate detection (no timestamp)
            # Timestamp would create different IDs for same transaction
            tx_hash = payload.get('transactionHash', '')
            trade_id = f"ws_{tx_hash}"
            
            # Determine side
            side_str = payload.get('side', 'BUY')
            side = TradeSide.BUY if side_str == 'BUY' else TradeSide.SELL
            
            # Extract other fields
            price = float(payload.get('price', 0))
            size = float(payload.get('size', 0))
            amount_usd = price * size
            
            # Create market info
            market_info = MarketInfo(
                market_id=payload.get('conditionId', ''),
                token_id=payload.get('asset', ''),
                title=payload.get('title', ''),
                description='',
                outcome=payload.get('outcome', ''),
                current_price=price,
                liquidity_usd=0,
                volume_24h_usd=0,
                is_active=True
            )
            
            trade = TraderTrade(
                trade_id=trade_id,
                trader_address=self.target_address,
                market_id=payload.get('conditionId', ''),
                token_id=payload.get('asset', ''),
                side=side,
                price=price,
                size=size,
                amount_usd=amount_usd,
                timestamp=datetime.fromtimestamp(payload.get('timestamp', time.time()) / 1000),
                transaction_hash=payload.get('transactionHash', ''),
                market_info=market_info
            )
            
            return trade
            
        except Exception as e:
            logger.error(f"Failed to convert WebSocket trade: {e}")
            return None
    
    async def _process_new_trade(self, trade: TraderTrade):
        """Process a new trade"""
        try:
            # Check if we've seen this trade before
            if trade.trade_id in self.seen_trade_ids:
                return
            
            self.seen_trade_ids.add(trade.trade_id)
            
            # 3-line format for clarity
            market_title = trade.market_info.title if trade.market_info else "Unknown Market"
            logger.info(f"📊 Market: {market_title}")
            logger.info(f"   Target: {trade.side.value} {trade.size:.2f} shares @ ${trade.price:.2f} = ${trade.amount_usd:.2f}")
            
            # Apply delay
            if self.config.trade_delay_seconds > 0:
                logger.info(f"Waiting {self.config.trade_delay_seconds} seconds...")
                await asyncio.sleep(self.config.trade_delay_seconds)
            
            # Notify callbacks
            for callback in self.new_trade_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(trade)
                    else:
                        callback(trade)
                except Exception as e:
                    logger.error(f"Error in trade callback: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to process new trade: {e}")
    
    def get_monitoring_status(self) -> dict:
        """Get monitoring status"""
        time_since_last_message = (datetime.now() - self.last_message_time).total_seconds()
        
        return {
            'monitoring': self.monitoring,
            'connected': self.is_connected,
            'connection_count': self.connection_count,
            'time_since_last_message': time_since_last_message,
            'target_address': self.target_address,
            'last_check_time': self.last_check_time.isoformat(),
            'seen_trades_count': len(self.seen_trade_ids),
            'monitoring_interval': 'real-time',
            'method': 'websocket',
            'active_filters': self.market_filter.get_enabled_patterns(),
            'filtering_enabled': bool(self.market_filter.get_enabled_patterns())
        }
