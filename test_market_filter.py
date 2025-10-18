#!/usr/bin/env python3
"""Test the market filter logic"""

from src.core.market_filters import MarketFilter

# Create filter instance
filter = MarketFilter()

# Test cases
test_markets = [
    "Bitcoin Up or Down - October 15, 12AM ET",  # Should PASS (hourly)
    "Bitcoin Up or Down - October 15, 12:30AM-12:45AM ET",  # Should FAIL (15-min)
    "Bitcoin Up or Down - October 15, 1:15AM-1:30AM ET",  # Should FAIL (15-min)
    "Ethereum Up or Down on October 16, 3PM ET",  # Should PASS (hourly)
    "Ethereum Up or Down - October 16, 3:30PM-3:45PM ET",  # Should FAIL (15-min)
    "Solana Up or Down on October 17, 5AM ET",  # Should PASS (hourly)
    "Random Market Title",  # Should FAIL (no match)
]

print("Testing Market Filter:\n")
print("=" * 80)

for market in test_markets:
    result = filter.should_copy_market(market)
    status = "✅ COPY" if result else "❌ SKIP"
    print(f"{status}: {market}")
    
print("=" * 80)
print("\nTest complete!")

