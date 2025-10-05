"""
Automatic redemption of winning positions from resolved markets
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger
from web3 import Web3

from src.core.polymarket_client import PolymarketClient
from src.core.config import bot_config


class AutoRedeemer:
    """
    Automatically detects resolved markets and redeems winning positions
    """
    
    CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Polygon mainnet
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC on Polygon
    
    # ConditionalTokens ABI for redeem function
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
            "payable": False,
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "inputs": [
                {"name": "conditionId", "type": "bytes32"}
            ],
            "name": "payoutNumerators",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]
    
    # ERC1155 ABI for balance checking
    ERC1155_ABI = [
        {
            "inputs": [
                {"name": "account", "type": "address"},
                {"name": "id", "type": "uint256"}
            ],
            "name": "balanceOf",
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
        self.conditional_tokens = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.CONDITIONAL_TOKENS_ADDRESS),
            abi=self.CONDITIONAL_TOKENS_ABI
        )
        
        # Track checked markets to avoid re-checking
        self.checked_markets: Dict[str, datetime] = {}
        self.recheck_interval = timedelta(hours=1)  # Re-check markets every hour
        
        logger.info("AutoRedeemer initialized")
    
    async def start_auto_redeem_loop(self, check_interval_minutes: int = 60):
        """
        Main loop that periodically checks for redeemable positions
        
        Args:
            check_interval_minutes: How often to check (default: 60 minutes)
        """
        logger.info(f"Starting auto-redeem loop (checking every {check_interval_minutes} minutes)")
        
        while True:
            try:
                await self.check_and_redeem_all()
                await asyncio.sleep(check_interval_minutes * 60)
            except Exception as e:
                logger.error(f"Error in auto-redeem loop: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error
    
    async def check_and_redeem_all(self):
        """Check all positions and redeem any winnings"""
        try:
            logger.info("🔍 Checking for redeemable positions...")
            
            # Get all markets we have positions in
            markets_with_positions = await self._get_markets_with_positions()
            
            if not markets_with_positions:
                logger.info("No positions found to check")
                return
            
            logger.info(f"Found {len(markets_with_positions)} markets with positions")
            
            total_redeemed = 0.0
            redeemed_count = 0
            
            for market_data in markets_with_positions:
                try:
                    result = await self._check_and_redeem_market(market_data)
                    if result['redeemed']:
                        total_redeemed += result['amount_usdc']
                        redeemed_count += 1
                        logger.success(f"✅ Redeemed ${result['amount_usdc']:.2f} from {market_data['title']}")
                except Exception as e:
                    logger.error(f"Error checking market {market_data.get('condition_id')}: {e}")
            
            if redeemed_count > 0:
                logger.success(f"🎉 Auto-redeemed ${total_redeemed:.2f} USDC from {redeemed_count} markets!")
            else:
                logger.info("No redeemable positions found")
                
        except Exception as e:
            logger.error(f"Error in check_and_redeem_all: {e}")
    
    async def _get_markets_with_positions(self) -> List[Dict]:
        """Get all markets where we have positions from database"""
        try:
            # Get positions from our database (trades we've made)
            import sqlite3
            from src.core.config import bot_config
            
            # Connect to database directly
            conn = sqlite3.connect(bot_config.db_file)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT DISTINCT 
                    tt.market_id,
                    json_extract(tt.market_info, '$.title') as market_title,
                    SUM(CASE WHEN tt.side = 'BUY' THEN ct.copy_size ELSE -ct.copy_size END) as net_position
                FROM copy_trades ct
                JOIN target_trades tt ON ct.original_trade_id = tt.trade_id
                WHERE ct.status = 'executed'
                GROUP BY tt.market_id
                HAVING net_position > 0
            """)
            
            markets_with_positions = []
            for row in cursor.fetchall():
                market_id, market_title, net_position = row
                if net_position and net_position > 0:
                    markets_with_positions.append({
                        'condition_id': market_id,
                        'title': market_title or 'Unknown Market',
                        'net_position': net_position
                    })
            
            conn.close()
            logger.info(f"Found {len(markets_with_positions)} markets with positions from database")
            return markets_with_positions
            
        except Exception as e:
            logger.error(f"Error getting markets with positions: {e}")
            # Fallback: try to get from Polymarket API
            try:
                logger.info("Trying to get closed markets from API...")
                markets_response = self.client.client.get_markets()
                
                if not markets_response or 'data' not in markets_response:
                    return []
                
                # Filter only closed markets (potentially resolved)
                closed_markets = [
                    {
                        'condition_id': m.get('condition_id'),
                        'title': m.get('question', 'Unknown Market'),
                        'closed': m.get('closed', False)
                    }
                    for m in markets_response['data']
                    if m.get('closed') and m.get('condition_id')
                ]
                
                logger.info(f"Found {len(closed_markets)} closed markets from API")
                return closed_markets[:10]  # Limit to 10 to avoid too many checks
                
            except Exception as e2:
                logger.error(f"Fallback also failed: {e2}")
                return []
    
    async def _check_and_redeem_market(self, market_data: Dict) -> Dict:
        """Check if a market is resolved and redeem if possible"""
        try:
            condition_id = market_data['condition_id']
            
            # Skip if we checked this recently
            if condition_id in self.checked_markets:
                last_check = self.checked_markets[condition_id]
                if datetime.now() - last_check < self.recheck_interval:
                    return {'redeemed': False, 'reason': 'recently_checked'}
            
            # Check if market is resolved by checking payout numerators
            condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id)
            
            try:
                # Try to get payout numerators - if this fails, market isn't resolved
                payout_numerator_0 = self.conditional_tokens.functions.payoutNumerators(
                    condition_id_bytes, 0
                ).call()
                
                payout_numerator_1 = self.conditional_tokens.functions.payoutNumerators(
                    condition_id_bytes, 1
                ).call()
                
                # If both are 0, market isn't resolved yet
                if payout_numerator_0 == 0 and payout_numerator_1 == 0:
                    self.checked_markets[condition_id] = datetime.now()
                    return {'redeemed': False, 'reason': 'not_resolved'}
                
                # Market is resolved! Determine winning outcome
                if payout_numerator_0 > 0 and payout_numerator_1 == 0:
                    winning_outcome = 0  # NO won
                    logger.info(f"Market resolved: {market_data['title']} - NO won")
                elif payout_numerator_1 > 0 and payout_numerator_0 == 0:
                    winning_outcome = 1  # YES won
                    logger.info(f"Market resolved: {market_data['title']} - YES won")
                else:
                    # Both have payouts (invalid market or draw)
                    logger.info(f"Market resolved with split payout: {market_data['title']}")
                    winning_outcome = None
                
                # Redeem the position
                result = await self._redeem_position(
                    condition_id=condition_id,
                    market_title=market_data['title']
                )
                
                self.checked_markets[condition_id] = datetime.now()
                return result
                
            except Exception as e:
                # If we can't read payout numerators, market likely isn't resolved
                logger.debug(f"Market not resolved yet: {market_data['title']}")
                self.checked_markets[condition_id] = datetime.now()
                return {'redeemed': False, 'reason': 'not_resolved'}
                
        except Exception as e:
            logger.error(f"Error checking market for redemption: {e}")
            return {'redeemed': False, 'reason': str(e)}
    
    async def _redeem_position(self, condition_id: str, market_title: str) -> Dict:
        """Execute the redemption transaction"""
        try:
            if self.config.dry_run:
                logger.info(f"[DRY RUN] Would redeem winnings from: {market_title}")
                return {
                    'redeemed': True,
                    'amount_usdc': 0.0,
                    'dry_run': True
                }
            
            logger.info(f"Redeeming winnings from: {market_title}")
            
            # Prepare transaction
            funder_address = Web3.to_checksum_address(self.client.config.funder_address)
            condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id)
            parent_collection_id = bytes(32)  # Zero bytes for base collateral
            
            # Index sets for both outcomes [1, 2] means redeem both YES and NO
            index_sets = [1, 2]
            
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
            
            # Sign transaction
            private_key = self.client.config.private_key
            if private_key.startswith('0x'):
                private_key = private_key[2:]
            
            signed_tx = self.w3.eth.account.sign_transaction(tx_data, private_key)
            
            # Send transaction
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            logger.info(f"Redeem transaction sent: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt['status'] == 1:
                logger.success(f"✅ Redemption successful! TX: {tx_hash.hex()}")
                
                # Try to estimate amount redeemed (would need to parse logs for exact amount)
                return {
                    'redeemed': True,
                    'amount_usdc': 0.0,  # Would need to parse transaction logs
                    'tx_hash': tx_hash.hex()
                }
            else:
                logger.error(f"Redemption transaction failed")
                return {'redeemed': False, 'reason': 'transaction_failed'}
                
        except Exception as e:
            logger.error(f"Error executing redemption: {e}")
            return {'redeemed': False, 'reason': str(e)}
