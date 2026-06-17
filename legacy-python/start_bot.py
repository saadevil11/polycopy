#!/usr/bin/env python3
"""
Startup script for the Polymarket Copy Trading Bot
"""
import sys
import os
import asyncio
from pathlib import Path

# Add the current directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.copy_trading_bot import main

if __name__ == "__main__":
    print("🤖 Polymarket Copy Trading Bot")
    print("=" * 50)
    
    # Load .env file if it exists (for local development)
    # Railway/cloud deployments use environment variables directly
    env_file = Path(".env")
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv()
        print("✅ Loaded configuration from .env file")
    else:
        print("ℹ️  Using environment variables (cloud deployment)")
    
    # Check if required environment variables are set
    required_vars = ["PRIVATE_KEY", "FUNDER_ADDRESS", "TARGET_TRADER_ADDRESS"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("📝 Please set these in Railway dashboard → Variables tab")
        print(f"   Missing: {', '.join(missing_vars)}")
        sys.exit(1)
    
    print("✅ Configuration validated")
    print(f"🎯 Target trader: {os.getenv('TARGET_TRADER_ADDRESS')}")
    print(f"💰 Funder address: {os.getenv('FUNDER_ADDRESS')}")
    
    # Show DRY_RUN status prominently
    dry_run_active = os.getenv('DRY_RUN', 'false').lower() == 'true'
    if dry_run_active:
        print("🧪 DRY_RUN MODE: ON - Simulating trades without executing")
        print("   ⚡ Balance checks bypassed")
        print("   ⚡ No real money will be used")
    else:
        print("💸 LIVE MODE: ON - Real trades will be executed")
        print("   ⚠️  Real money will be used")
        print("   ⚠️  Balance checks active")
    print()
    
    # Start the bot
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"\n💥 Bot crashed: {e}")
        sys.exit(1)
