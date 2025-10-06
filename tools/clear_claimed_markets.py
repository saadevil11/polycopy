#!/usr/bin/env python3
"""
Clear all claimed markets from database (reset auto-claim tracking)
Use this if markets were incorrectly marked as claimed
"""
import sqlite3
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import bot_config

def clear_claimed_markets():
    """Clear all claimed markets from database"""
    try:
        conn = sqlite3.connect(bot_config.db_file)
        cursor = conn.cursor()
        
        # Get count before
        cursor.execute("SELECT COUNT(*) FROM claimed_markets")
        count_before = cursor.fetchone()[0]
        
        # Clear all
        cursor.execute("DELETE FROM claimed_markets")
        
        conn.commit()
        conn.close()
        
        print(f"✅ Cleared {count_before} claimed markets from database")
        print("The bot will now re-check all resolved markets")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("🧹 Clearing claimed markets database...")
    clear_claimed_markets()

