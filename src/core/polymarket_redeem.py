"""
Polymarket-specific redemption system using their actual claim mechanism
"""
import asyncio
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from loguru import logger
from web3 import Web3
from eth_abi import encode, decode

from src.core.polymarket_client import PolymarketClient
from src.core.config import bot_config


class PolymarketRedeemer:
    """
    Handles redemption of winnings through Polymarket's actual system
    """
    
    # Polymarket CTF Exchange contract (handles claims)
    CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # Polygon mainnet
    
    # Conditional Tokens Framework contract
    CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Polygon mainnet
    
    # USDC on Polygon
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    
    # Polymarket Neg Risk Adapter (for some markets)
    NEG_RISK_ADAPTER_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    
    # CTF Exchange ABI for claiming
    CTF_EXCHANGE_ABI = [
        {
            "inputs": [
                {"name": "conditionId", "type": "bytes32"}
            ],
            "name": "redeemPositions",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        }
    ]
    
    # ConditionalTokens ABI
    CONDITIONAL_TOKENS_ABI = [
        {
            "inputs": [
                {"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"}
            ],
            "name": "redeemPositions",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "inputs": [
                {"name": "conditionId", "type": "bytes32"},
                {"name": "outcomeSlotCount", "type": "uint256"}
            ],
            "name": "reportPayouts",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "inputs": [
                {"name": "", "type": "bytes32"}
            ],
            "name": "payoutDenominator",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [
                {"name": "", "type": "bytes32"},
                {"name": "", "type": "uint256"}
            ],
            "name": "payoutNumerators",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]
    
    def __init__(self, polymarket_client: PolymarketClient):
        self.client = polymarket_client
        self.config = bot_config
        
        # Initialize Web3
        rpc_url = "https://polygon-rpc.com"
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        if not self.w3.is_connected():
            logger.error("Failed to connect to Polygon network")
            raise Exception("Web3 connection failed")
        
        # Initialize contracts
        self.ctf_exchange = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.CTF_EXCHANGE_ADDRESS),
            abi=self.CTF_EXCHANGE_ABI
        )
        
        self.conditional_tokens = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.CONDITIONAL_TOKENS_ADDRESS),
            abi=self.CONDITIONAL_TOKENS_ABI
        )
        
        # Track redeemed markets
        self.redeemed_markets: Dict[str, datetime] = {}
        
        logger.info("PolymarketRedeemer initialized")
    
    async def check_and_redeem_all(self) -> Dict:
        """Check all positions and redeem winnings using Polymarket's system"""
        try:
            logger.info("🔍 Checking for claimable winnings on Polymarket...")
            
            # Get markets with claimable winnings
            claimable_markets = await self._get_claimable_markets()
            
            if not claimable_markets:
                logger.info("No claimable winnings found")
                return {'total_claimed': 0, 'markets_claimed': 0}
            
            logger.info(f"Found {len(claimable_markets)} markets with claimable winnings")
            
            total_claimed = 0.0
            markets_claimed = 0
            
            for market in claimable_markets:
                try:
                    result = await self._claim_market_winnings(market)
                    if result['success']:
                        total_claimed += result.get('amount', 0)
                        markets_claimed += 1
                        logger.success(f"✅ Claimed ${result.get('amount', 0):.2f} from {market['title']}")
                except Exception as e:
                    logger.error(f"Error claiming market {market.get('condition_id')}: {e}")
            
            if markets_claimed > 0:
                logger.success(f"🎉 Successfully claimed ${total_claimed:.2f} from {markets_claimed} markets!")
            
            return {
                'total_claimed': total_claimed,
                'markets_claimed': markets_claimed
            }
            
        except Exception as e:
            logger.error(f"Error in check_and_redeem_all: {e}")
            return {'total_claimed': 0, 'markets_claimed': 0}
    
    async def _get_claimable_markets(self) -> List[Dict]:
        """Get markets with claimable winnings - ONLY checks YOUR markets"""
        try:
            claimable = []
            
            # Get YOUR markets from database (markets you actually traded)
            import sqlite3
            from src.core.config import bot_config
            
            conn = sqlite3.connect(bot_config.db_file)
            cursor = conn.cursor()
            
            # Get unique markets where you have executed trades
            cursor.execute("""
                SELECT DISTINCT 
                    tt.market_id,
                    json_extract(tt.market_info, '$.title') as market_title
                FROM copy_trades ct
                JOIN target_trades tt ON ct.original_trade_id = tt.trade_id
                WHERE ct.status = 'executed'
                GROUP BY tt.market_id
            """)
            
            your_markets = cursor.fetchall()
            conn.close()
            
            logger.info(f"Found {len(your_markets)} markets where you have positions")
            
            # Check each of YOUR markets for claimable winnings
            for market_id, market_title in your_markets:
                try:
                    # Skip if already claimed recently
                    if market_id in self.redeemed_markets:
                        if datetime.now() - self.redeemed_markets[market_id] < timedelta(hours=24):
                            continue
                    
                    # Check if this market is resolved
                    if self._check_market_resolved(market_id):
                        logger.info(f"✅ Market resolved and claimable: {market_title}")
                        claimable.append({
                            'condition_id': market_id,
                            'title': market_title or 'Unknown Market',
                            'tokens': []
                        })
                    else:
                        logger.debug(f"Market not resolved yet: {market_title}")
                        
                except Exception as e:
                    logger.debug(f"Error checking market {market_id}: {e}")
                    continue
            
            return claimable
            
        except Exception as e:
            logger.error(f"Error getting claimable markets: {e}")
            return []
    
    def _check_market_resolved(self, condition_id: str) -> bool:
        """Check if a market is resolved and has payouts"""
        try:
            condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id)
            
            # Check payout denominator (if > 0, market is resolved)
            denominator = self.conditional_tokens.functions.payoutDenominator(
                condition_id_bytes
            ).call()
            
            return denominator > 0
            
        except Exception:
            return False
    
    async def _claim_market_winnings(self, market: Dict) -> Dict:
        """Claim winnings for a specific market"""
        try:
            condition_id = market['condition_id']
            
            if self.config.dry_run:
                logger.info(f"[DRY RUN] Would claim winnings from: {market['title']}")
                return {
                    'success': True,
                    'amount': 0,
                    'dry_run': True
                }
            
            logger.info(f"Claiming winnings from: {market['title']}")
            
            # Method 1: Try simple CTF Exchange redeem
            success = await self._claim_via_ctf_exchange(condition_id)
            
            if not success:
                # Method 2: Try direct ConditionalTokens redeem
                success = await self._claim_via_conditional_tokens(condition_id)
            
            if success:
                self.redeemed_markets[condition_id] = datetime.now()
                return {
                    'success': True,
                    'amount': 0  # Would need to parse logs for exact amount
                }
            
            return {'success': False}
            
        except Exception as e:
            logger.error(f"Error claiming winnings: {e}")
            return {'success': False}
    
    async def _claim_via_ctf_exchange(self, condition_id: str) -> bool:
        """Claim using CTF Exchange contract"""
        try:
            funder_address = Web3.to_checksum_address(self.client.config.funder_address)
            condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id)
            
            # Build transaction
            tx_data = self.ctf_exchange.functions.redeemPositions(
                condition_id_bytes
            ).build_transaction({
                'from': funder_address,
                'gas': 300000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(funder_address)
            })
            
            # Sign and send
            private_key = self.client.config.private_key
            if private_key.startswith('0x'):
                private_key = private_key[2:]
            
            signed_tx = self.w3.eth.account.sign_transaction(tx_data, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logger.info(f"Claim transaction sent via CTF Exchange: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt['status'] == 1:
                logger.success(f"✅ Claim successful! TX: {tx_hash.hex()}")
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"CTF Exchange claim failed: {e}")
            return False
    
    async def _claim_via_conditional_tokens(self, condition_id: str) -> bool:
        """Claim using ConditionalTokens contract directly"""
        try:
            funder_address = Web3.to_checksum_address(self.client.config.funder_address)
            condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id)
            
            # For Polymarket, parentCollectionId is usually 0
            parent_collection_id = bytes(32)
            
            # Index sets for both outcomes
            index_sets = [1, 2]  # Binary markets have 2 outcomes
            
            # Build transaction
            tx_data = self.conditional_tokens.functions.redeemPositions(
                Web3.to_checksum_address(self.USDC_ADDRESS),
                parent_collection_id,
                condition_id_bytes,
                index_sets
            ).build_transaction({
                'from': funder_address,
                'gas': 300000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(funder_address)
            })
            
            # Sign and send
            private_key = self.client.config.private_key
            if private_key.startswith('0x'):
                private_key = private_key[2:]
            
            signed_tx = self.w3.eth.account.sign_transaction(tx_data, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logger.info(f"Claim transaction sent via ConditionalTokens: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt['status'] == 1:
                logger.success(f"✅ Claim successful! TX: {tx_hash.hex()}")
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"ConditionalTokens claim failed: {e}")
            return False
    
    async def start_auto_claim_loop(self, check_interval_minutes: int = 60):
        """Main loop for automatic claiming"""
        logger.info(f"Starting auto-claim loop (checking every {check_interval_minutes} minutes)")
        
        while True:
            try:
                await self.check_and_redeem_all()
                await asyncio.sleep(check_interval_minutes * 60)
            except Exception as e:
                logger.error(f"Error in auto-claim loop: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error
