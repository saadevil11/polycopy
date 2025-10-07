"""
Trade replication system for the copy trading bot
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List
from loguru import logger

from src.core.polymarket_client import PolymarketClient
from src.core.models import TraderTrade, CopyTrade, OrderType, TradeStatus, TradeSide
from src.core.config import trading_config, bot_config
from src.core.market_filters import MarketFilter


class TradeReplicator:
    """Handles replication of target trader's trades"""
    
    def __init__(self, polymarket_client: PolymarketClient, risk_manager):
        self.client = polymarket_client
        self.risk_manager = risk_manager
        self.config = trading_config
        self.bot_config = bot_config
        self.market_filter = MarketFilter()
        
    async def replicate_trade(self, original_trade: TraderTrade) -> Optional[CopyTrade]:
        """Replicate a trade from the target trader"""
        try:
            logger.info(f"Attempting to replicate trade {original_trade.trade_id}")
            
            # Pre-trade risk checks
            if not await self._pre_trade_checks(original_trade):
                return self._create_skipped_trade(original_trade, "Failed pre-trade checks")
            
            # Calculate position size
            copy_size, copy_amount = self._calculate_position_size(original_trade)
            if copy_size <= 0:
                return self._create_skipped_trade(original_trade, "Position size too small")
            
            # Create copy trade object
            copy_trade = CopyTrade(
                original_trade=original_trade,
                copy_size=copy_size,
                copy_amount_usd=copy_amount,
                order_type=OrderType.MARKET,  # Default to market orders for speed
                status=TradeStatus.PENDING
            )
            
            # Execute the trade
            if self.bot_config.dry_run:
                return self._simulate_trade(copy_trade)
            else:
                return await self._execute_trade(copy_trade)
                
        except Exception as e:
            logger.error(f"Failed to replicate trade {original_trade.trade_id}: {e}")
            return self._create_failed_trade(original_trade, str(e))
    
    async def _pre_trade_checks(self, trade: TraderTrade) -> bool:
        """Perform pre-trade risk and validation checks"""
        try:
            # Check if market is in excluded list
            if trade.market_id in self.config.excluded_markets:
                logger.info(f"Market {trade.market_id} is excluded")
                return False
            
            # Check minimum target trade value
            if trade.amount_usd < self.config.min_target_trade_value_usd:
                logger.info(f"Target trade too small: ${trade.amount_usd:.2f} < ${self.config.min_target_trade_value_usd:.2f} - skipping")
                return False
                
            # Market filter already checked by WebSocket monitor - skip duplicate check
            
            # Check market liquidity if market info is available
            if trade.market_info:
                if trade.market_info.liquidity_usd < self.config.min_market_liquidity_usd:
                    logger.warning(f"Market liquidity too low: ${trade.market_info.liquidity_usd}")
                    return False
                
                if not trade.market_info.is_active:
                    logger.warning(f"Market is not active: {trade.market_id}")
                    return False
            
            # Check risk manager approval
            if not await self.risk_manager.can_open_position(trade):
                logger.warning("Risk manager rejected trade")
                return False
            
            # Skip balance check for speed optimization
            # Balance is verified at bot startup, so we trust it here
            if not self.bot_config.dry_run:
                logger.debug("Balance check skipped for speed - verified at startup")
            else:
                logger.debug("[DRY_RUN] Balance check skipped")
            
            return True
            
        except Exception as e:
            logger.error(f"Error in pre-trade checks: {e}")
            return False
    
    def _calculate_position_size(self, trade: TraderTrade) -> tuple[float, float]:
        """Calculate the position size to copy"""
        try:
            # For SELL orders, we need to sell the same PERCENTAGE of shares
            # that the target trader sold, not recalculate based on USD
            if trade.side == TradeSide.SELL:
                # Calculate what percentage of their position they're selling
                # If they're selling everything, we sell everything
                # If they're selling 50%, we sell 50%
                
                # For now, use the same share ratio approach
                # This means if target sells 1.8 shares, we sell (1.8 * copy_percentage) shares
                copy_size = trade.size * self.config.copy_percentage
                copy_amount = copy_size * trade.price if trade.price > 0 else copy_size
                
                logger.info(f"[SELL] Calculated position size: {copy_size} shares (same % as target), ${copy_amount:.2f}")
                return copy_size, copy_amount
            
            # For BUY orders, use USD-based calculation
            # Base amount from original trade
            base_amount = trade.amount_usd * self.config.copy_percentage
            
            # Apply position size limits
            copy_amount = min(base_amount, self.config.max_position_size_usd)
            copy_amount = max(copy_amount, self.config.min_position_size_usd)
            
            # If the amount is below minimum, don't trade
            if copy_amount < self.config.min_position_size_usd:
                return 0.0, 0.0
            
            # Calculate size based on the original trade's price
            if trade.price > 0:
                copy_size = copy_amount / trade.price
            else:
                # If price is 0 or invalid, use size ratio
                copy_size = trade.size * self.config.copy_percentage
            
            logger.info(f"[BUY] Calculated position size: {copy_size} shares, ${copy_amount:.2f}")
            return copy_size, copy_amount
            
        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return 0.0, 0.0
    
    async def _execute_trade(self, copy_trade: CopyTrade) -> CopyTrade:
        """Execute the actual trade"""
        try:
            logger.info(f"Executing copy trade for {copy_trade.original_trade.trade_id}")
            
            # Place market order for speed
            # For SELL orders, pass the number of shares; for BUY, pass USD amount
            if copy_trade.original_trade.side == TradeSide.SELL:
                order_id = self.client.place_market_order(
                    token_id=copy_trade.original_trade.token_id,
                    side=copy_trade.original_trade.side,
                    amount_usd=copy_trade.copy_size  # For SELL: use share count
                )
            else:
                order_id = self.client.place_market_order(
                    token_id=copy_trade.original_trade.token_id,
                    side=copy_trade.original_trade.side,
                    amount_usd=copy_trade.copy_amount_usd  # For BUY: use USD amount
                )
            
            if order_id:
                copy_trade.order_id = order_id
                copy_trade.execution_timestamp = datetime.now()
                copy_trade.status = TradeStatus.EXECUTED
                
                # Try to get execution price (might need to wait a moment)
                await asyncio.sleep(1)
                order_status = self.client.get_order_status(order_id)
                if order_status and 'price' in order_status:
                    copy_trade.execution_price = float(order_status['price'])
                
                logger.success(f"Trade executed successfully: {order_id}")
                
                # Update risk manager
                await self.risk_manager.on_trade_executed(copy_trade)
                
            else:
                copy_trade.status = TradeStatus.FAILED
                copy_trade.error_message = "Failed to place order"
                logger.error("Failed to place market order")
            
            return copy_trade
            
        except Exception as e:
            logger.error(f"Error executing trade: {e}")
            copy_trade.status = TradeStatus.FAILED
            copy_trade.error_message = str(e)
            return copy_trade
    
    def _simulate_trade(self, copy_trade: CopyTrade) -> CopyTrade:
        """Simulate trade execution for dry run mode"""
        logger.info(f"[DRY RUN] Would execute trade:")
        logger.info(f"  Token ID: {copy_trade.original_trade.token_id}")
        logger.info(f"  Side: {copy_trade.original_trade.side.value}")
        logger.info(f"  Size: {copy_trade.copy_size}")
        logger.info(f"  Amount: ${copy_trade.copy_amount_usd:.2f}")
        
        copy_trade.status = TradeStatus.EXECUTED
        copy_trade.execution_timestamp = datetime.now()
        copy_trade.execution_price = copy_trade.original_trade.price
        copy_trade.order_id = f"dry_run_{int(datetime.now().timestamp())}"
        
        return copy_trade
    
    def _create_skipped_trade(self, original_trade: TraderTrade, reason: str) -> CopyTrade:
        """Create a skipped trade record"""
        logger.warning(f"Skipping trade {original_trade.trade_id}: {reason}")
        
        return CopyTrade(
            original_trade=original_trade,
            copy_size=0.0,
            copy_amount_usd=0.0,
            order_type=OrderType.MARKET,
            status=TradeStatus.SKIPPED,
            error_message=reason
        )
    
    def _create_failed_trade(self, original_trade: TraderTrade, error: str) -> CopyTrade:
        """Create a failed trade record"""
        logger.error(f"Failed to process trade {original_trade.trade_id}: {error}")
        
        return CopyTrade(
            original_trade=original_trade,
            copy_size=0.0,
            copy_amount_usd=0.0,
            order_type=OrderType.MARKET,
            status=TradeStatus.FAILED,
            error_message=error
        )
