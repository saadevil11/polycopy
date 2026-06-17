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
    min_target_trade_value_usd: float = 4.0   # Skip target trades below this value
    excluded_markets: list = None             # List of market IDs to exclude
    allow_15min_markets: bool = False         # Allow trading on 15-minute interval markets (high risk!)
    allow_hourly_markets: bool = True         # Allow trading on hourly interval markets
    
    # Order Execution
    use_gtc_only: bool = False                    # If True, skip FAK and use only GTC orders (more accurate % copying)
    price_slippage_tolerance: float = 0.14        # Price slippage tolerance for GTC orders (14% = $0.50 → $0.57)
    
    # Advanced order execution (auto-configured based on use_gtc_only)
    max_order_retries: int = 1                    # Maximum retry attempts for failed orders
    retry_delay_seconds: float = 0.0              # Delay between retries
    use_gtc_fallback: bool = True                 # Use GTC orders if FAK fails
    order_timeout_seconds: int = 10               # Timeout for order execution
    gtc_use_exact_target_price: bool = False      # If True, GTC uses exact target price (no slippage)
    skip_price_fetch_for_speed: bool = True       # Skip fetching current price to execute faster
    gtc_enforce_min_shares: bool = True           # Ensure GTC orders meet 5-share minimum

    # Weather mode (aggressive GTC price-chasing for weather markets)
    use_weather_mode: bool = False                # If True, chase the price ahead until filled
    weather_max_chases: int = 5                   # Max number of cancel-and-replace attempts
    weather_fill_wait_seconds: float = 2.0        # How long to wait for a chase order to fill before re-checking
    weather_buy_ahead: float = 0.01               # How far above the target's price to place a BUY (1 cent, not 1 tick) - fast markets jump cents, so a full-cent lead avoids constant re-pricing
    weather_max_buy_price: float = 0.996          # Don't chase a BUY above this (avoid overpaying near $1)
    weather_min_sell_price: float = 0.004         # Don't chase a SELL below this

    # Brownfox mode (one fixed-size buy per market at the target's exact price,
    # auto +1c resting sell, follow the target's exit only if they sell below
    # our +1c). See src/core/brownfox.py.
    use_brownfox_mode: bool = False
    brownfox_trade_size_shares: float = 50.0      # Fixed SHARE count per market (not USD, not a % of target)
    brownfox_sell_markup: float = 0.01            # Resting sell at buy price + this (+1 cent)
    brownfox_reconcile_seconds: float = 3.0       # How often to reconcile buy/sell fills
    brownfox_market_sell_retries: int = 8         # Retries to guarantee the forced exit fully sells

    # Trade monitoring source. The live WebSocket is fastest but Polymarket's
    # edge rate-limits datacenter IPs (HTTP 429); set this True to poll the Data
    # API instead, which is reachable from those IPs.
    # (Brownfox/weather both honour this WS-vs-REST choice.)
    use_polling_monitor: bool = False

    # Stale-order sweep: cancel resting orders once their market reaches this
    # price (a buy left below a market that ran to ~1.0 will never fill).
    stale_order_sweep_enabled: bool = True
    stale_order_cancel_price: float = 0.995
    stale_order_sweep_seconds: int = 30

    # Auto-sell limit: keep a resting GTC sell at this price covering our full
    # position in each COPIED market, so we exit at the target's usual price
    # (e.g. 0.999) with no lag. If the target sells at a different price, the
    # limit is cancelled and we sell at their price - 1 tick instead.
    auto_sell_enabled: bool = True
    auto_sell_price: float = 0.999
    auto_sell_maintain_seconds: int = 20
    # If we have no resting limit and the target sells at >= this price, match
    # their exact sell price (no -1 tick). Below it, chase at their price-1 tick.
    auto_sell_exact_threshold: float = 0.97

    def __post_init__(self):
        if self.excluded_markets is None:
            self.excluded_markets = []

@dataclass
class PolymarketConfig:
    """Configuration for Polymarket API"""
    # API endpoints
    clob_api_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"
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
    auto_redeem_interval_minutes: int = 7  # How often to check for redeemable positions

# Global configuration instances
trading_config = TradingConfig(
    target_trader_address=os.getenv("TARGET_TRADER_ADDRESS", ""),
    copy_percentage=float(os.getenv("COPY_PERCENTAGE", "0.1")),
    max_position_size_usd=float(os.getenv("MAX_POSITION_SIZE_USD", "1000")),
    min_position_size_usd=float(os.getenv("MIN_POSITION_SIZE_USD", "0.1")),
    max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "500")),
    max_positions=int(os.getenv("MAX_POSITIONS", "10")),
    trade_delay_seconds=int(os.getenv("TRADE_DELAY_SECONDS", "0")),
    monitoring_interval_seconds=int(os.getenv("MONITORING_INTERVAL_SECONDS", "1")),
    min_market_liquidity_usd=float(os.getenv("MIN_MARKET_LIQUIDITY_USD", "1000")),
    min_target_trade_value_usd=float(os.getenv("MIN_TARGET_TRADE_VALUE_USD", "4.0")),
    allow_15min_markets=os.getenv("ALLOW_15MIN_MARKETS", "false").lower() == "true",
    allow_hourly_markets=os.getenv("ALLOW_HOURLY_MARKETS", "true").lower() == "true",
    # Order execution - main toggle
    use_gtc_only=os.getenv("USE_GTC_ONLY", "false").lower() == "true",
    price_slippage_tolerance=float(os.getenv("PRICE_SLIPPAGE_TOLERANCE", "0.14")),
    # Advanced parameters (auto-configured, rarely need to change)
    max_order_retries=int(os.getenv("MAX_ORDER_RETRIES", "1")),
    retry_delay_seconds=float(os.getenv("RETRY_DELAY_SECONDS", "0.5")),
    use_gtc_fallback=os.getenv("USE_GTC_FALLBACK", "true").lower() == "true",
    order_timeout_seconds=int(os.getenv("ORDER_TIMEOUT_SECONDS", "10")),
    gtc_use_exact_target_price=os.getenv("GTC_USE_EXACT_TARGET_PRICE", "false").lower() == "true",
    skip_price_fetch_for_speed=os.getenv("SKIP_PRICE_FETCH_FOR_SPEED", "true").lower() == "true",
    gtc_enforce_min_shares=os.getenv("GTC_ENFORCE_MIN_SHARES", "true").lower() == "true",
    # Weather mode
    use_weather_mode=os.getenv("USE_WEATHER_MODE", "false").lower() == "true",
    weather_max_chases=int(os.getenv("WEATHER_MAX_CHASES", "5")),
    weather_fill_wait_seconds=float(os.getenv("WEATHER_FILL_WAIT_SECONDS", "2.0")),
    weather_buy_ahead=float(os.getenv("WEATHER_BUY_AHEAD", "0.01")),
    # Brownfox mode
    use_brownfox_mode=os.getenv("USE_BROWNFOX_MODE", "false").lower() == "true",
    brownfox_trade_size_shares=float(os.getenv("BROWNFOX_TRADE_SIZE_SHARES", "50")),
    brownfox_sell_markup=float(os.getenv("BROWNFOX_SELL_MARKUP", "0.01")),
    brownfox_reconcile_seconds=float(os.getenv("BROWNFOX_RECONCILE_SECONDS", "3")),
    brownfox_market_sell_retries=int(os.getenv("BROWNFOX_MARKET_SELL_RETRIES", "8")),
    # Monitor source
    use_polling_monitor=os.getenv("USE_POLLING_MONITOR", "false").lower() == "true",
    # Stale-order sweep
    stale_order_sweep_enabled=os.getenv("STALE_ORDER_SWEEP_ENABLED", "true").lower() == "true",
    stale_order_cancel_price=float(os.getenv("STALE_ORDER_CANCEL_PRICE", "0.995")),
    stale_order_sweep_seconds=int(os.getenv("STALE_ORDER_SWEEP_SECONDS", "30")),
    # Auto-sell limit
    auto_sell_enabled=os.getenv("AUTO_SELL_ENABLED", "true").lower() == "true",
    auto_sell_price=float(os.getenv("AUTO_SELL_PRICE", "0.999")),
    auto_sell_maintain_seconds=int(os.getenv("AUTO_SELL_MAINTAIN_SECONDS", "20")),
    auto_sell_exact_threshold=float(os.getenv("AUTO_SELL_EXACT_THRESHOLD", "0.97"))
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
