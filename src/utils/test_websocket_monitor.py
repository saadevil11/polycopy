#!/usr/bin/env python3
"""
Test the WebSocket trader monitoring system
"""
import asyncio
import sys
from dotenv import load_dotenv

sys.path.insert(0, '.')

from src.monitors.websocket_trader_monitor import WebSocketTraderMonitor

load_dotenv()

async def test_websocket_monitor():
    """Test the WebSocket monitoring system"""
    
    print("🔍 Testing WebSocket Trader Monitor")
    print("=" * 50)
    
    # Create monitor
    monitor = WebSocketTraderMonitor()
    
    print(f"🎯 Target trader: {monitor.target_address}")
    print(f"🌐 WebSocket URL: {monitor.ws_url}")
    
    # Set up callback
    trade_count = 0
    
    def on_new_trade(trade):
        nonlocal trade_count
        trade_count += 1
        print(f"🆕 NEW TRADE #{trade_count}: {trade.side.value} ${trade.amount_usd:.2f}")
        print(f"   Market: {trade.market_info.title if trade.market_info else 'Unknown'}")
        print(f"   Price: ${trade.price:.3f}")
        print(f"   Size: {trade.size}")
    
    monitor.add_new_trade_callback(on_new_trade)
    
    print("\n🚀 Starting WebSocket monitoring...")
    print("⏰ Will monitor for 30 seconds to detect any trades...")
    print("💡 If your target trader makes a trade during this time, it will be detected!")
    
    # Start monitoring
    monitor.start_monitoring()
    
    # Monitor for 30 seconds
    try:
        await asyncio.sleep(30)
    except KeyboardInterrupt:
        print("\n⏹️  Monitoring interrupted by user")
    
    # Stop monitoring
    monitor.stop_monitoring()
    
    print(f"\n📊 Monitoring Results:")
    print(f"   Trades detected: {trade_count}")
    print(f"   Status: {monitor.get_monitoring_status()}")
    
    if trade_count == 0:
        print("\n💡 No trades detected. This is normal if:")
        print("   - Your target trader didn't trade during the test")
        print("   - The target address is incorrect")
        print("   - WebSocket connection issues")
        print("\n🔧 To test with real trades:")
        print("   1. Find an active trader on Polymarket")
        print("   2. Update TARGET_TRADER_ADDRESS in .env")
        print("   3. Run this test when they're actively trading")

if __name__ == "__main__":
    try:
        asyncio.run(test_websocket_monitor())
    except KeyboardInterrupt:
        print("\n👋 Test interrupted")
