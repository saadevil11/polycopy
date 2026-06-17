"""
Market filtering functionality for the Polymarket copy trading bot
"""
import os
from typing import List
from loguru import logger
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class MarketFilter:
    """Filter markets based on patterns and rules"""
    
    DEFAULT_PATTERNS = [
        "Bitcoin Up or Down on",
        "Ethereum Up or Down on",
        "Solana Up or Down on"
    ]
    
    def __init__(self):
        # Load filters from environment or use defaults
        env_filters = os.getenv("MARKET_FILTERS", None)
        
        if env_filters is None:
            # MARKET_FILTERS not set in .env - use defaults
            self.enabled_patterns = self.DEFAULT_PATTERNS.copy()
            logger.info(f"Using default filters: {self.enabled_patterns}")
        elif env_filters.strip() == "":
            # MARKET_FILTERS set but empty - copy ALL markets
            self.enabled_patterns = []
            logger.info("MARKET_FILTERS is empty - copying ALL markets (no filtering)")
        else:
            # MARKET_FILTERS has values - use them
            self.enabled_patterns = [p.strip() for p in env_filters.split(",") if p.strip()]
            logger.info(f"Loaded filters from environment: {self.enabled_patterns}")
        
        # Load 15-minute market setting
        self.allow_15min_markets = os.getenv("ALLOW_15MIN_MARKETS", "false").lower() == "true"
        if self.allow_15min_markets:
            logger.warning("⚠️  15-minute markets ENABLED - high risk of slippage!")
        
        # Load hourly market setting
        self.allow_hourly_markets = os.getenv("ALLOW_HOURLY_MARKETS", "true").lower() == "true"
        if not self.allow_hourly_markets:
            logger.warning("⚠️  Hourly markets DISABLED - will skip hourly interval markets")
    
    def load_default_patterns(self):
        """Load default market patterns"""
        self.enabled_patterns = self.DEFAULT_PATTERNS.copy()
    
    def should_copy_market(self, market_title: str) -> bool:
        """Check if market should be copied based on patterns"""
        if not self.enabled_patterns:  # If no patterns, copy all markets
            return True
            
        if not market_title:
            return False
            
        # If no patterns are enabled, copy all markets
        if not self.enabled_patterns:
            logger.debug("No filters enabled - copying all markets")
            return True
            
        # Check if market title contains any enabled pattern (case insensitive)
        market_title_lower = market_title.lower()
        
        # Check each pattern
        for pattern in self.enabled_patterns:
            pattern_lower = pattern.lower()
            if pattern_lower in market_title_lower:
                # Additional check: Exclude 15-minute markets (unless explicitly allowed)
                if not self.allow_15min_markets and self._is_fifteen_minute_market(market_title):
                    logger.debug(f"❌ Skipping 15-minute market: {market_title}")
                    return False
                
                # Additional check: Exclude hourly markets (unless explicitly allowed)
                if not self.allow_hourly_markets and self._is_hourly_market(market_title):
                    logger.debug(f"❌ Skipping hourly market: {market_title}")
                    return False
                
                logger.debug(f"✅ Filter match: {pattern}")
                return True
                
        # No matches found
        logger.debug(f"❌ No filter match: {market_title}")
        return False
    
    def _is_fifteen_minute_market(self, market_title: str) -> bool:
        """Check if market is a 15-minute interval market"""
        import re
        
        # Patterns for 15-minute markets:
        # "12:30AM-12:45AM"
        # "12:15AM-12:30AM"
        # "1:45PM-2:00PM"
        # etc.
        
        fifteen_min_patterns = [
            r'\d{1,2}:\d{2}[AP]M-\d{1,2}:\d{2}[AP]M',  # Time range like 12:30AM-12:45AM
        ]
        
        for pattern in fifteen_min_patterns:
            if re.search(pattern, market_title, re.IGNORECASE):
                # Found a time range - this is a 15-minute market
                return True
        
        return False
    
    def _is_hourly_market(self, market_title: str) -> bool:
        """Check if market is an hourly interval market"""
        import re
        
        # Patterns for hourly markets:
        # "Bitcoin Up or Down - December 8, 10AM ET"
        # "BTC Price - December 8, 3PM ET"
        # "Ethereum Up or Down - December 8, 11PM ET"
        # etc.
        
        hourly_patterns = [
            r'\b\d{1,2}[AP]M\s+ET\b',  # Time like "10AM ET", "3PM ET"
            r'\b\d{1,2}[AP]M\s+EST\b', # Time like "10AM EST", "3PM EST"
            r'\b\d{1,2}[AP]M\s+EDT\b', # Time like "10AM EDT", "3PM EDT"
        ]
        
        for pattern in hourly_patterns:
            if re.search(pattern, market_title, re.IGNORECASE):
                # Found an hourly time pattern - this is an hourly market
                return True
        
        return False
    
    def get_enabled_patterns(self) -> List[str]:
        """Get list of enabled patterns"""
        return self.enabled_patterns.copy()
    
    def set_enabled_patterns(self, patterns: List[str]):
        """Set enabled patterns"""
        self.enabled_patterns = patterns.copy()
    
    def add_pattern(self, pattern: str):
        """Add a new pattern"""
        if pattern and pattern not in self.enabled_patterns:
            self.enabled_patterns.append(pattern)
    
    def remove_pattern(self, pattern: str):
        """Remove a pattern"""
        if pattern in self.enabled_patterns:
            self.enabled_patterns.remove(pattern)
    
    def clear_patterns(self):
        """Clear all patterns"""
        self.enabled_patterns = []
