#!/usr/bin/env python3
"""
Test the alternative trader monitoring system
"""
import asyncio
import sys
from dotenv import load_dotenv

sys.path.insert(0, '.')

from src.monitors.alternative_trader_monitor import AlternativeTraderMonitor
from src.core.config import trading_config

load_dotenv()

async def test_monitor():
    """Test the alternative monitoring system"""
    
    print("🔍 Testing Alternative Trader Monitor")
    print("=" * 50)
    
    # Create monitor
    monitor = AlternativeTraderMonitor()
    
    print(f"🎯 Target trader: {monitor.target_address}")
    print(f"📊 Monitoring interval: {monitor.config.monitoring_interval_seconds}s")
    
    # Test subgraph query
    print("\n📈 Testing subgraph data retrieval...")
    trades = monitor._get_trades_from_subgraph(limit=5)
    
    print(f"✅ Found {len(trades)} trades from subgraph")
    
    if trades:
        print("\n📋 Sample trades:")
        for i, trade in enumerate(trades[:3]):
            print(f"  {i+1}. {trade.timestamp}: {trade.side.value} {trade.size} @ ${trade.price:.3f}")
            if trade.market_info:
                print(f"      Market: {trade.market_info.title}")
                print(f"      Outcome: {trade.market_info.outcome}")
    else:
        print("⚠️  No trades found. This could mean:")
        print("   - The trader hasn't made recent trades")
        print("   - The address is incorrect")
        print("   - The subgraph data is not available")
    
    # Test a few monitoring cycles
    print(f"\n🔄 Testing monitoring cycles...")
    
    def on_new_trade(trade):
        print(f"🆕 NEW TRADE DETECTED: {trade.side.value} ${trade.amount_usd:.2f}")
    
    monitor.add_new_trade_callback(on_new_trade)
    
    # Run a few check cycles
    for i in range(3):
        print(f"   Cycle {i+1}/3...")
        await monitor._check_for_new_trades()
        await asyncio.sleep(2)
    
    print("\n✅ Alternative monitoring test completed!")
    print("\nStatus:", monitor.get_monitoring_status())

if __name__ == "__main__":
    asyncio.run(test_monitor())
