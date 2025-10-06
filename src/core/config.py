"""
Configuration settings for the Polymarket copy trading bot
"""
import os
from typing import Optional
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class TradingConfig:
    """Configuration for trading parameters"""
    # Target trader to copy
    target_trader_address: str
    
    # Position sizing
    copy_percentage: float = 0.1  # What percentage of target's position size to copy
    max_position_size_usd: float = 1000.0  # Maximum position size in USD
    min_position_size_usd: float = 1.0   # Minimum position size in USD
    
    # Risk management
    max_daily_loss_usd: float = 1000.0     # Maximum daily loss limit
    max_positions: int = 10000               # Maximum number of open positions
    
    # Timing
    trade_delay_seconds: int = 0          # Delay before copying trades (to avoid front-running)
    monitoring_interval_seconds: int = 1  # How often to check for new trades
    
    # Filters
    min_market_liquidity_usd: float = 1000.0  # Only trade markets with minimum liquidity
    excluded_markets: list = None             # List of market IDs to exclude
    
    def __post_init__(self):
        if self.excluded_markets is None:
            self.excluded_markets = []

@dataclass
class PolymarketConfig:
    """Configuration for Polymarket API"""
    # API endpoints
    clob_api_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    websocket_url: str = "wss://ws-live-data.polymarket.com"
    
    # Network
    chain_id: int = 137  # Polygon mainnet
    
    # Authentication
    private_key: str = os.getenv("PRIVATE_KEY", "")
    funder_address: str = os.getenv("FUNDER_ADDRESS", "")
    signature_type: int = int(os.getenv("SIGNATURE_TYPE", "0"))
    
    # API credentials (will be generated)
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    api_passphrase: Optional[str] = None

@dataclass
class BotConfig:
    """Main bot configuration"""
    # Logging
    log_level: str = "INFO"
    log_file: str = "polymarket_bot.log"
    
    # Monitoring
    enable_telegram_alerts: bool = False
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Database (for tracking trades)
    # Use /app/data for Railway persistent volume, fallback to local for dev
    db_file: str = os.path.join(
        os.getenv("DB_PATH", "./data"),
        "trades.db"
    )
    
    # Safety
    dry_run: bool = False  # If True, only log trades without executing
    
    # Performance
    max_concurrent_requests: int = 5
    
    # Position Management
    copy_merge_actions: bool = True  # Copy merge actions from target trader
    copy_redeem_actions: bool = True  # Copy redeem actions from target trader
    auto_redeem_enabled: bool = True  # Automatically redeem winnings from resolved markets
    auto_redeem_interval_minutes: int = 60  # How often to check for redeemable positions

# Global configuration instances
trading_config = TradingConfig(
    target_trader_address=os.getenv("TARGET_TRADER_ADDRESS", ""),
    copy_percentage=float(os.getenv("COPY_PERCENTAGE", "1.0")),
    max_position_size_usd=float(os.getenv("MAX_POSITION_SIZE_USD", "1000")),
    min_position_size_usd=float(os.getenv("MIN_POSITION_SIZE_USD", "10")),
    max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "500")),
    max_positions=int(os.getenv("MAX_POSITIONS", "10")),
    trade_delay_seconds=int(os.getenv("TRADE_DELAY_SECONDS", "5")),
    monitoring_interval_seconds=int(os.getenv("MONITORING_INTERVAL_SECONDS", "10")),
    min_market_liquidity_usd=float(os.getenv("MIN_MARKET_LIQUIDITY_USD", "1000"))
)

polymarket_config = PolymarketConfig()

bot_config = BotConfig(
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
    copy_merge_actions=os.getenv("COPY_MERGE_ACTIONS", "true").lower() == "true",
    copy_redeem_actions=os.getenv("COPY_REDEEM_ACTIONS", "true").lower() == "true",
    auto_redeem_enabled=os.getenv("AUTO_REDEEM_ENABLED", "true").lower() == "true",
    auto_redeem_interval_minutes=int(os.getenv("AUTO_REDEEM_INTERVAL_MINUTES", "60"))
)
