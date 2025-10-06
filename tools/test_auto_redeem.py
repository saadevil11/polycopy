#!/usr/bin/env python3
"""
Test script for Polymarket redemption functionality
"""
import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.polymarket_client import PolymarketClient
from src.core.polymarket_redeem import PolymarketRedeemer
from loguru import logger

load_dotenv()

async def test_polymarket_redeem():
    """Test the Polymarket redemption system"""
    
    print("🧪 Testing Polymarket Redemption System")
    print("=" * 60)
    
    # Initialize client
    client = PolymarketClient()
    if not client.initialize():
        print("❌ Failed to initialize Polymarket client")
        return
    
    print("✅ Polymarket client initialized")
    
    # Initialize Polymarket redeemer
    try:
        redeemer = PolymarketRedeemer(client)
        print("✅ PolymarketRedeemer initialized")
    except Exception as e:
        print(f"❌ Failed to initialize PolymarketRedeemer: {e}")
        return
    
    print()
    print("🔍 Checking for claimable winnings on Polymarket...")
    print("=" * 60)
    
    # Run a single check
    result = await redeemer.check_and_redeem_all()
    
    print()
    print("=" * 60)
    if result['markets_claimed'] > 0:
        print(f"✅ Successfully claimed ${result['total_claimed']:.2f} from {result['markets_claimed']} markets!")
    else:
        print("No winnings to claim at this time")
    
    print()
    print("💡 To run continuous auto-claiming:")
    print("   - Set AUTO_REDEEM_ENABLED=true in .env")
    print("   - The bot will check every hour automatically")

if __name__ == "__main__":
    print()
    asyncio.run(test_polymarket_redeem())
