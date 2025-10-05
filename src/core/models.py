"""
Data models for the Polymarket copy trading bot
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
import json

class TradeStatus(Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    FAILED = "failed"
    SKIPPED = "skipped"

class TradeSide(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"

@dataclass
class MarketInfo:
    """Information about a Polymarket market"""
    market_id: str
    token_id: str
    title: str
    description: str
    outcome: str  # YES or NO
    current_price: float
    liquidity_usd: float
    volume_24h_usd: float
    is_active: bool
    end_date: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market_id': self.market_id,
            'token_id': self.token_id,
            'title': self.title,
            'description': self.description,
            'outcome': self.outcome,
            'current_price': self.current_price,
            'liquidity_usd': self.liquidity_usd,
            'volume_24h_usd': self.volume_24h_usd,
            'is_active': self.is_active,
            'end_date': self.end_date.isoformat() if self.end_date else None
        }

@dataclass
class TraderTrade:
    """Represents a trade made by the target trader"""
    trade_id: str
    trader_address: str
    market_id: str
    token_id: str
    side: TradeSide
    price: float
    size: float
    amount_usd: float
    timestamp: datetime
    transaction_hash: str
    market_info: Optional[MarketInfo] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'trade_id': self.trade_id,
            'trader_address': self.trader_address,
            'market_id': self.market_id,
            'token_id': self.token_id,
            'side': self.side.value,
            'price': self.price,
            'size': self.size,
            'amount_usd': self.amount_usd,
            'timestamp': self.timestamp.isoformat(),
            'transaction_hash': self.transaction_hash,
            'market_info': self.market_info.to_dict() if self.market_info else None
        }

@dataclass
class CopyTrade:
    """Represents a copy trade to be executed"""
    original_trade: TraderTrade
    copy_size: float
    copy_amount_usd: float
    order_type: OrderType
    limit_price: Optional[float] = None
    status: TradeStatus = TradeStatus.PENDING
    execution_timestamp: Optional[datetime] = None
    order_id: Optional[str] = None
    execution_price: Optional[float] = None
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'original_trade': self.original_trade.to_dict(),
            'copy_size': self.copy_size,
            'copy_amount_usd': self.copy_amount_usd,
            'order_type': self.order_type.value,
            'limit_price': self.limit_price,
            'status': self.status.value,
            'execution_timestamp': self.execution_timestamp.isoformat() if self.execution_timestamp else None,
            'order_id': self.order_id,
            'execution_price': self.execution_price,
            'error_message': self.error_message
        }

@dataclass
class Position:
    """Represents a current position"""
    market_id: str
    token_id: str
    side: TradeSide
    size: float
    average_price: float
    current_price: float
    unrealized_pnl: float
    market_info: Optional[MarketInfo] = None
    
    @property
    def current_value_usd(self) -> float:
        return self.size * self.current_price
        
    @property
    def cost_basis_usd(self) -> float:
        return self.size * self.average_price
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market_id': self.market_id,
            'token_id': self.token_id,
            'side': self.side.value,
            'size': self.size,
            'average_price': self.average_price,
            'current_price': self.current_price,
            'unrealized_pnl': self.unrealized_pnl,
            'current_value_usd': self.current_value_usd,
            'cost_basis_usd': self.cost_basis_usd,
            'market_info': self.market_info.to_dict() if self.market_info else None
        }

@dataclass
class RiskMetrics:
    """Risk metrics for monitoring"""
    total_positions: int
    total_position_value_usd: float
    daily_pnl_usd: float
    total_unrealized_pnl_usd: float
    max_position_size_usd: float
    positions_at_risk: int  # Positions with significant unrealized losses
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_positions': self.total_positions,
            'total_position_value_usd': self.total_position_value_usd,
            'daily_pnl_usd': self.daily_pnl_usd,
            'total_unrealized_pnl_usd': self.total_unrealized_pnl_usd,
            'max_position_size_usd': self.max_position_size_usd,
            'positions_at_risk': self.positions_at_risk
        }

@dataclass
class BotStatus:
    """Current status of the bot"""
    is_running: bool
    last_check_timestamp: datetime
    trades_copied_today: int
    successful_trades: int
    failed_trades: int
    current_balance_usd: float
    risk_metrics: RiskMetrics
    errors: list = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'is_running': self.is_running,
            'last_check_timestamp': self.last_check_timestamp.isoformat(),
            'trades_copied_today': self.trades_copied_today,
            'successful_trades': self.successful_trades,
            'failed_trades': self.failed_trades,
            'current_balance_usd': self.current_balance_usd,
            'risk_metrics': self.risk_metrics.to_dict(),
            'errors': self.errors
        }
