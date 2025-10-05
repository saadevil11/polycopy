#!/usr/bin/env python3
"""
Test database saving functionality
"""
import sys
from datetime import datetime

sys.path.insert(0, '.')

from src.core.database import Database
from src.core.models import TraderTrade, TradeSide, MarketInfo

def test_database_save():
    """Test saving trades to database"""
    
    print("🗄️ Testing Database Save Functionality")
    print("=" * 50)
    
    # Initialize database
    db = Database()
    
    # Create a test trade
    market_info = MarketInfo(
        market_id="test_market_123",
        token_id="test_token_456",
        title="Test Market: Will this work?",
        description="Testing database functionality",
        outcome="Yes",
        current_price=0.65,
        liquidity_usd=1000,
        volume_24h_usd=500,
        is_active=True
    )
    
    test_trade = TraderTrade(
        trade_id="test_trade_789",
        trader_address="0x35c0732e069faea97c11aa9cab045562eaab81d6",
        market_id="test_market_123",
        token_id="test_token_456",
        side=TradeSide.BUY,
        price=0.65,
        size=100.0,
        amount_usd=65.0,
        timestamp=datetime.now(),
        transaction_hash="0xtest123",
        market_info=market_info
    )
    
    print(f"📝 Saving test trade: {test_trade.trade_id}")
    success = db.save_target_trade(test_trade)
    
    if success:
        print("✅ Trade saved successfully!")
        
        # Check if it's in the database
        recent_trades = db.get_recent_target_trades(limit=5)
        print(f"📊 Found {len(recent_trades)} recent trades in database")
        
        for trade in recent_trades:
            print(f"  - {trade['timestamp']}: {trade['side']} ${trade['amount_usd']}")
            
    else:
        print("❌ Failed to save trade")
    
    # Get statistics
    stats = db.get_trade_statistics()
    print(f"\n📈 Database Statistics:")
    print(f"  Total trades: {stats.get('total_trades', 0)}")
    print(f"  Success rate: {stats.get('success_rate', 0):.1f}%")

if __name__ == "__main__":
    test_database_save()
