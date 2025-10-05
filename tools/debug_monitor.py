#!/usr/bin/env python3
"""
Debug script to test if the bot can detect trades from target trader
"""
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.monitors.websocket_trader_monitor import WebSocketTraderMonitor
from loguru import logger

load_dotenv()

async def test_monitor():
    """Test the WebSocket monitor"""
    
    target_address = os.getenv("TARGET_TRADER_ADDRESS")
    if not target_address:
        print("❌ TARGET_TRADER_ADDRESS not set in environment")
        return
    
    print(f"🎯 Testing WebSocket monitor for: {target_address}")
    print(f"🔌 Connecting to: wss://ws-live-data.polymarket.com")
    print("=" * 60)
    
    # Create monitor
    monitor = WebSocketTraderMonitor()
    
    # Add callback to print trades
    def on_trade(trade):
        print(f"\n✅ TRADE DETECTED!")
        print(f"   Market: {trade.market_info.title if trade.market_info else 'Unknown'}")
        print(f"   Side: {trade.side.value}")
        print(f"   Size: {trade.size}")
        print(f"   Price: {trade.price}")
        print(f"   Amount: ${trade.amount_usd:.2f}")
        print()
    
    monitor.add_new_trade_callback(on_trade)
    
    # Start monitoring
    monitor.start_monitoring()
    
    print("✅ Monitor started!")
    print("⏳ Waiting for trades from target trader...")
    print("   (This will run until you press Ctrl+C)")
    print()
    
    try:
        # Keep running
        while True:
            await asyncio.sleep(10)
            status = monitor.get_monitoring_status()
            print(f"📊 Status: Connected={status.get('connected', False)}, "
                  f"Trades seen={len(monitor.seen_trade_ids)}")
    except KeyboardInterrupt:
        print("\n👋 Stopping monitor...")
        monitor.stop_monitoring()

if __name__ == "__main__":
    print("🔍 Polymarket Trade Monitor Debug Tool")
    print("=" * 60)
    asyncio.run(test_monitor())
