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
                logger.debug(f"✅ Filter match: {pattern}")
                return True
                
        # No matches found
        logger.debug(f"❌ No filter match: {market_title}")
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
