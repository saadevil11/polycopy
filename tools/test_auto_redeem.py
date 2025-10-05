#!/usr/bin/env python3
"""
Test script for automatic redemption functionality
"""
import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.polymarket_client import PolymarketClient
from src.core.auto_redeem import AutoRedeemer
from loguru import logger

load_dotenv()

async def test_auto_redeem():
    """Test the auto-redemption system"""
    
    print("🧪 Testing Auto-Redemption System")
    print("=" * 60)
    
    # Initialize client
    client = PolymarketClient()
    if not client.initialize():
        print("❌ Failed to initialize Polymarket client")
        return
    
    print("✅ Polymarket client initialized")
    
    # Initialize auto-redeemer
    try:
        redeemer = AutoRedeemer(client)
        print("✅ AutoRedeemer initialized")
    except Exception as e:
        print(f"❌ Failed to initialize AutoRedeemer: {e}")
        return
    
    print()
    print("🔍 Checking for redeemable positions...")
    print("=" * 60)
    
    # Run a single check
    await redeemer.check_and_redeem_all()
    
    print()
    print("=" * 60)
    print("✅ Test complete!")
    print()
    print("💡 To run continuous auto-redemption:")
    print("   - Set AUTO_REDEEM_ENABLED=true in .env")
    print("   - The bot will check every hour automatically")

if __name__ == "__main__":
    print()
    asyncio.run(test_auto_redeem())
