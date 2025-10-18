"""
Risk management system for the copy trading bot
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger

from src.core.polymarket_client import PolymarketClient
from src.core.models import TraderTrade, CopyTrade, Position, RiskMetrics, TradeSide, AccountRestrictedException
from src.core.config import trading_config


class RiskManager:
    """Manages risk and position limits for the bot"""
    
    def __init__(self, polymarket_client: PolymarketClient):
        self.client = polymarket_client
        self.config = trading_config
        self.daily_trades: List[CopyTrade] = []
        self.current_positions: Dict[str, Position] = {}
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now().date()
        self.account_restricted = False  # Track if account is restricted
        
        # Position cache for speed optimization
        self.position_cache_time = None
        self.position_cache_ttl = timedelta(seconds=30)  # Cache positions for 30 seconds
        
    async def can_open_position(self, trade: TraderTrade) -> bool:
        """Check if we can open a new position based on risk limits"""
        try:
            # Update current state
            await self._update_positions()
            self._check_daily_reset()
            
            # Add DRY RUN indicator to logs but keep all checks
            mode_prefix = "[DRY RUN] " if self.client.bot_config.dry_run else ""
            
            # Check position count limit
            if len(self.current_positions) >= self.config.max_positions:
                logger.warning(f"{mode_prefix}Maximum positions reached: {len(self.current_positions)}")
                return False
            
            # Check daily loss limit
            current_daily_pnl = self._calculate_daily_pnl()
            if current_daily_pnl <= -self.config.max_daily_loss_usd:
                logger.warning(f"{mode_prefix}Daily loss limit reached: ${current_daily_pnl:.2f}")
                return False
            
            # Check if we already have a position in this market
            if trade.market_id in self.current_positions:
                existing_position = self.current_positions[trade.market_id]
                
                # Allow closing positions (opposite side)
                if existing_position.side != trade.side:
                    logger.info(f"{mode_prefix}Allowing position close/reduce in market {trade.market_id}")
                    return True
                
                # For same side, check if we want to increase position
                # For now, let's be conservative and not increase positions
                logger.warning(f"{mode_prefix}Already have position in market {trade.market_id}")
                return False
            
            # Check market-specific limits
            if trade.market_info:
                # Don't trade in markets ending soon (less than 1 day)
                if trade.market_info.end_date:
                    time_to_end = trade.market_info.end_date - datetime.now()
                    if time_to_end < timedelta(days=1):
                        logger.warning(f"{mode_prefix}Market ending too soon: {time_to_end}")
                        return False
                
                # Don't trade in markets with extreme prices (likely to be resolved)
                # Note: Relaxed from 0.95/0.05 to 0.99/0.01 to allow more trades
                if trade.market_info.current_price > 1 or trade.market_info.current_price < 0.001:
                    logger.warning(f"{mode_prefix}Extreme market price: {trade.market_info.current_price}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error in risk check: {e}")
            return False
    
    async def on_trade_executed(self, copy_trade: CopyTrade):
        """Called when a trade is executed"""
        try:
            self.daily_trades.append(copy_trade)
            
            # Update positions
            await self._update_positions()
            
            # Log risk metrics
            metrics = self.get_risk_metrics()
            logger.info(f"Updated risk metrics: {metrics.total_positions} positions, "
                       f"${metrics.total_position_value_usd:.2f} total value, "
                       f"${metrics.daily_pnl_usd:.2f} daily P&L")
            
        except Exception as e:
            logger.error(f"Error updating risk metrics: {e}")
    
    async def _update_positions(self):
        """Update current positions from the API (with caching for speed)"""
        try:
            # Check if cache is still valid
            now = datetime.now()
            if (self.position_cache_time and 
                now - self.position_cache_time < self.position_cache_ttl):
                logger.debug(f"Using cached positions (age: {(now - self.position_cache_time).total_seconds():.1f}s)")
                return
            
            # Update cache
            positions = self.client.get_current_positions()
            self.current_positions = {pos.market_id: pos for pos in positions}
            self.position_cache_time = now
            logger.debug(f"Position cache updated ({len(self.current_positions)} positions)")
            
        except Exception as e:
            logger.error(f"Error updating positions: {e}")
    
    def _check_daily_reset(self):
        """Check if we need to reset daily counters"""
        current_date = datetime.now().date()
        if current_date > self.last_reset_date:
            logger.info("Resetting daily counters")
            self.daily_trades = []
            self.daily_pnl = 0.0
            self.last_reset_date = current_date
    
    def _calculate_daily_pnl(self) -> float:
        """Calculate daily P&L from executed trades"""
        daily_pnl = 0.0
        
        for trade in self.daily_trades:
            if trade.status.value == "executed" and trade.execution_price:
                # Simple P&L calculation (this is a simplified version)
                # In reality, you'd need to track entry/exit prices properly
                if trade.original_trade.side == TradeSide.BUY:
                    # For buys, P&L is current_price - entry_price
                    current_price = 0.5  # Would need to get current market price
                    pnl = (current_price - trade.execution_price) * trade.copy_size
                else:
                    # For sells, P&L is entry_price - current_price
                    current_price = 0.5  # Would need to get current market price
                    pnl = (trade.execution_price - current_price) * trade.copy_size
                
                daily_pnl += pnl
        
        return daily_pnl
    
    def get_risk_metrics(self) -> RiskMetrics:
        """Get current risk metrics"""
        total_position_value = sum(pos.current_value_usd for pos in self.current_positions.values())
        total_unrealized_pnl = sum(pos.unrealized_pnl for pos in self.current_positions.values())
        max_position_size = max((pos.current_value_usd for pos in self.current_positions.values()), default=0)
        positions_at_risk = sum(1 for pos in self.current_positions.values() if pos.unrealized_pnl < -50)
        
        return RiskMetrics(
            total_positions=len(self.current_positions),
            total_position_value_usd=total_position_value,
            daily_pnl_usd=self._calculate_daily_pnl(),
            total_unrealized_pnl_usd=total_unrealized_pnl,
            max_position_size_usd=max_position_size,
            positions_at_risk=positions_at_risk
        )
    
    async def emergency_close_all(self):
        """Emergency function to close all positions"""
        logger.warning("EMERGENCY: Closing all positions")
        
        try:
            for position in self.current_positions.values():
                # Place opposite order to close position
                opposite_side = TradeSide.SELL if position.side == TradeSide.BUY else TradeSide.BUY
                
                try:
                    order_id = self.client.place_market_order(
                        token_id=position.token_id,
                        side=opposite_side,
                        amount_usd=position.current_value_usd
                    )
                    
                    if order_id:
                        logger.info(f"Emergency close order placed: {order_id}")
                    else:
                        logger.error(f"Failed to place emergency close order for {position.market_id}")
                        
                except AccountRestrictedException as e:
                    logger.critical(f"Cannot close position - account is restricted: {e.message}")
                    self.account_restricted = True
                    raise
                    
        except AccountRestrictedException:
            # Re-raise account restrictions
            raise
        except Exception as e:
            logger.error(f"Error in emergency close: {e}")
    
    def should_stop_trading(self) -> bool:
        """Check if trading should be stopped due to risk limits"""
        # Stop if account is restricted
        if self.account_restricted:
            logger.critical("Account is restricted - stopping all trading")
            return True
        
        metrics = self.get_risk_metrics()
        
        # Stop if daily loss limit exceeded
        if metrics.daily_pnl_usd <= -self.config.max_daily_loss_usd:
            logger.warning(f"Daily loss limit exceeded: ${metrics.daily_pnl_usd:.2f}")
            return True
        
        # Stop if too many positions at risk
        if metrics.positions_at_risk > metrics.total_positions * 0.7:  # 70% of positions at risk
            logger.warning(f"Too many positions at risk: {metrics.positions_at_risk}/{metrics.total_positions}")
            return True
        
        return False
