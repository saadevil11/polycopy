"""
Database operations for storing trade history and bot state
"""
import sqlite3
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from loguru import logger

from src.core.models import TraderTrade, CopyTrade, BotStatus
from src.core.config import bot_config


class Database:
    """Simple SQLite database for storing bot data"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or bot_config.db_file
        self.init_database()
    
    def init_database(self):
        """Initialize database tables"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Target trader trades table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS target_trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_id TEXT UNIQUE,
                        trader_address TEXT,
                        market_id TEXT,
                        token_id TEXT,
                        side TEXT,
                        price REAL,
                        size REAL,
                        amount_usd REAL,
                        timestamp TEXT,
                        transaction_hash TEXT,
                        market_info TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Copy trades table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS copy_trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        original_trade_id TEXT,
                        copy_size REAL,
                        copy_amount_usd REAL,
                        order_type TEXT,
                        limit_price REAL,
                        status TEXT,
                        execution_timestamp TEXT,
                        order_id TEXT,
                        execution_price REAL,
                        error_message TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (original_trade_id) REFERENCES target_trades (trade_id)
                    )
                ''')
                
                # Bot status table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS bot_status (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        status_data TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Brownfox: markets we've already taken our one buy in (the
                # one-buy-per-market guard must persist across restarts).
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS brownfox_markets (
                        market_id TEXT PRIMARY KEY,
                        token_id TEXT,
                        buy_price REAL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create indexes
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_target_trades_timestamp ON target_trades(timestamp)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_copy_trades_status ON copy_trades(status)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_copy_trades_timestamp ON copy_trades(execution_timestamp)')
                
                conn.commit()
                logger.info(f"Database initialized: {self.db_path}")
                
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
    
    def record_brownfox_market(self, market_id: str, token_id: str, buy_price: float) -> None:
        """Persist that we've taken our one brownfox buy in this market."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    'INSERT OR IGNORE INTO brownfox_markets (market_id, token_id, buy_price) VALUES (?, ?, ?)',
                    (market_id, token_id, buy_price)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to record brownfox market {market_id}: {e}")

    def get_brownfox_markets(self) -> set:
        """Set of market_ids we've already taken our one brownfox buy in."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT market_id FROM brownfox_markets')
                return {row[0] for row in cursor.fetchall() if row[0]}
        except Exception as e:
            logger.error(f"Failed to load brownfox markets: {e}")
            return set()

    def has_executed_trade(self, trade_id: str) -> bool:
        """Check if a trade has already been attempted (executed, failed, or skipped)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Check if there's ANY copy trade record for this trade_id
                # This includes EXECUTED, PARTIAL_FILL, FAILED, and SKIPPED
                cursor.execute('''
                    SELECT COUNT(*) FROM copy_trades 
                    WHERE original_trade_id = ?
                ''', (trade_id,))
                
                count = cursor.fetchone()[0]
                return count > 0
                
        except Exception as e:
            logger.error(f"Failed to check if trade executed: {e}")
            return False  # Fail safe: allow trade if check fails
    
    def save_target_trade(self, trade: TraderTrade) -> bool:
        """Save a target trader's trade"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT OR REPLACE INTO target_trades 
                    (trade_id, trader_address, market_id, token_id, side, price, size, 
                     amount_usd, timestamp, transaction_hash, market_info)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    trade.trade_id,
                    trade.trader_address,
                    trade.market_id,
                    trade.token_id,
                    trade.side.value,
                    trade.price,
                    trade.size,
                    trade.amount_usd,
                    trade.timestamp.isoformat(),
                    trade.transaction_hash,
                    json.dumps(trade.market_info.to_dict()) if trade.market_info else None
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"Failed to save target trade: {e}")
            return False
    
    def save_copy_trade(self, copy_trade: CopyTrade) -> bool:
        """Save a copy trade"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO copy_trades 
                    (original_trade_id, copy_size, copy_amount_usd, order_type, limit_price,
                     status, execution_timestamp, order_id, execution_price, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    copy_trade.original_trade.trade_id,
                    copy_trade.copy_size,
                    copy_trade.copy_amount_usd,
                    copy_trade.order_type.value,
                    copy_trade.limit_price,
                    copy_trade.status.value,
                    copy_trade.execution_timestamp.isoformat() if copy_trade.execution_timestamp else None,
                    copy_trade.order_id,
                    copy_trade.execution_price,
                    copy_trade.error_message
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"Failed to save copy trade: {e}")
            return False
    
    def save_bot_status(self, status: BotStatus) -> bool:
        """Save bot status snapshot"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO bot_status (timestamp, status_data)
                    VALUES (?, ?)
                ''', (
                    status.last_check_timestamp.isoformat(),
                    json.dumps(status.to_dict())
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"Failed to save bot status: {e}")
            return False
    
    def get_recent_target_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent target trader trades"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT * FROM target_trades 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (limit,))
                
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()
                
                return [dict(zip(columns, row)) for row in rows]
                
        except Exception as e:
            logger.error(f"Failed to get recent target trades: {e}")
            return []
    
    def get_copy_trades_today(self) -> List[Dict[str, Any]]:
        """Get copy trades from today"""
        try:
            today = datetime.now().date().isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT * FROM copy_trades 
                    WHERE date(created_at) = ?
                    ORDER BY created_at DESC
                ''', (today,))
                
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()
                
                return [dict(zip(columns, row)) for row in rows]
                
        except Exception as e:
            logger.error(f"Failed to get today's copy trades: {e}")
            return []
    
    def get_recent_copy_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent copy trades enriched with their target-trade details.

        Joins copy_trades -> target_trades so each row carries market/token/side
        and the target's price, plus a best-effort market title parsed from the
        stored market_info JSON.
        """
        import json as _json
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT c.original_trade_id, c.copy_size, c.copy_amount_usd,
                           c.status, c.execution_price, c.order_id, c.error_message,
                           c.created_at,
                           t.market_id, t.token_id, t.side, t.price AS target_price,
                           t.size AS target_size, t.market_info
                    FROM copy_trades c
                    LEFT JOIN target_trades t ON c.original_trade_id = t.trade_id
                    ORDER BY c.created_at DESC
                    LIMIT ?
                ''', (limit,))
                columns = [d[0] for d in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

            for r in rows:
                title = ''
                mi = r.get('market_info')
                if mi:
                    try:
                        parsed = _json.loads(mi) if isinstance(mi, str) else mi
                        if isinstance(parsed, dict):
                            title = parsed.get('title') or ''
                    except Exception:
                        title = ''
                r['market_title'] = title
                r.pop('market_info', None)
            return rows
        except Exception as e:
            logger.error(f"Failed to get recent copy trades: {e}")
            return []

    def get_copied_token_ids(self) -> set:
        """Token ids the bot has actually copied (executed BUY copy trades).

        Used to scope the auto-sell limit to markets we copied, so it never
        touches the user's own unrelated positions.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT DISTINCT t.token_id
                    FROM copy_trades c
                    JOIN target_trades t ON c.original_trade_id = t.trade_id
                    WHERE c.status = 'executed' AND t.token_id IS NOT NULL
                ''')
                return {row[0] for row in cursor.fetchall() if row[0]}
        except Exception as e:
            logger.error(f"Failed to get copied token ids: {e}")
            return set()

    def get_trade_statistics(self) -> Dict[str, Any]:
        """Get trading statistics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Total trades
                cursor.execute('SELECT COUNT(*) FROM copy_trades')
                total_trades = cursor.fetchone()[0]
                
                # Successful trades
                cursor.execute('SELECT COUNT(*) FROM copy_trades WHERE status = "executed"')
                successful_trades = cursor.fetchone()[0]
                
                # Failed trades
                cursor.execute('SELECT COUNT(*) FROM copy_trades WHERE status = "failed"')
                failed_trades = cursor.fetchone()[0]
                
                # Today's trades
                today = datetime.now().date().isoformat()
                cursor.execute('SELECT COUNT(*) FROM copy_trades WHERE date(created_at) = ?', (today,))
                today_trades = cursor.fetchone()[0]
                
                # Total volume
                cursor.execute('SELECT SUM(copy_amount_usd) FROM copy_trades WHERE status = "executed"')
                total_volume = cursor.fetchone()[0] or 0
                
                return {
                    'total_trades': total_trades,
                    'successful_trades': successful_trades,
                    'failed_trades': failed_trades,
                    'today_trades': today_trades,
                    'total_volume_usd': total_volume,
                    'success_rate': (successful_trades / total_trades * 100) if total_trades > 0 else 0
                }
                
        except Exception as e:
            logger.error(f"Failed to get trade statistics: {e}")
            return {}
    
    def calculate_daily_pnl(self) -> float:
        """Calculate daily P&L from today's executed trades in database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get today's date
                today = datetime.now().date().isoformat()
                
                # Get all executed trades from today with their details
                cursor.execute('''
                    SELECT 
                        ct.copy_amount_usd,
                        ct.execution_price,
                        ct.copy_size,
                        tt.side,
                        tt.price as target_price
                    FROM copy_trades ct
                    JOIN target_trades tt ON ct.original_trade_id = tt.trade_id
                    WHERE ct.status IN ('EXECUTED', 'PARTIAL_FILL')
                    AND date(ct.created_at) = ?
                ''', (today,))
                
                trades = cursor.fetchall()
                daily_pnl = 0.0
                
                for trade in trades:
                    copy_amount_usd, execution_price, copy_size, side, target_price = trade
                    
                    if execution_price and copy_size:
                        # For BUY: if execution price < target price = good (saved money)
                        # For SELL: if execution price > target price = good (got more money)
                        if side == 'BUY':
                            # Saved money if we bought cheaper than target
                            pnl = (target_price - execution_price) * copy_size if target_price else 0
                        else:  # SELL
                            # Made money if we sold higher than target
                            pnl = (execution_price - target_price) * copy_size if target_price else 0
                        
                        daily_pnl += pnl
                
                logger.debug(f"Calculated daily P&L from database: ${daily_pnl:.2f} ({len(trades)} trades)")
                return daily_pnl
                
        except Exception as e:
            logger.error(f"Failed to calculate daily P&L: {e}")
            return 0.0
    
    def calculate_all_time_pnl(self) -> float:
        """Calculate all-time P&L from all executed trades in database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get all executed trades with their details
                cursor.execute('''
                    SELECT 
                        ct.copy_amount_usd,
                        ct.execution_price,
                        ct.copy_size,
                        tt.side,
                        tt.price as target_price
                    FROM copy_trades ct
                    JOIN target_trades tt ON ct.original_trade_id = tt.trade_id
                    WHERE ct.status IN ('EXECUTED', 'PARTIAL_FILL')
                ''')
                
                trades = cursor.fetchall()
                total_pnl = 0.0
                
                for trade in trades:
                    copy_amount_usd, execution_price, copy_size, side, target_price = trade
                    
                    if execution_price and copy_size:
                        # For BUY: if execution price < target price = good (saved money)
                        # For SELL: if execution price > target price = good (got more money)
                        if side == 'BUY':
                            # Saved money if we bought cheaper than target
                            pnl = (target_price - execution_price) * copy_size if target_price else 0
                        else:  # SELL
                            # Made money if we sold higher than target
                            pnl = (execution_price - target_price) * copy_size if target_price else 0
                        
                        total_pnl += pnl
                
                logger.debug(f"Calculated all-time P&L from database: ${total_pnl:.2f} ({len(trades)} trades)")
                return total_pnl
                
        except Exception as e:
            logger.error(f"Failed to calculate all-time P&L: {e}")
            return 0.0
    
    def cleanup_old_data(self, days_to_keep: int = 30):
        """Clean up old data to keep database size manageable"""
        try:
            cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Clean up old bot status entries (keep only last 7 days)
                status_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
                cursor.execute('DELETE FROM bot_status WHERE created_at < ?', (status_cutoff,))
                
                # Clean up old trades
                cursor.execute('DELETE FROM copy_trades WHERE created_at < ?', (cutoff_date,))
                cursor.execute('DELETE FROM target_trades WHERE created_at < ?', (cutoff_date,))
                
                conn.commit()
                logger.info(f"Cleaned up data older than {days_to_keep} days")
                
        except Exception as e:
            logger.error(f"Failed to cleanup old data: {e}")
