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
    
    # Gnosis Safe contracts
    GNOSIS_SAFE_PROXY_FACTORY = "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2"  # Gnosis Safe Proxy Factory
    GNOSIS_SAFE_MASTER_COPY = "0x3E5c63644E683549055b9Be8653de26E0B4CD36E"  # Gnosis Safe L2 1.3.0
    
    # Gnosis Safe ABI - execTransaction function
    GNOSIS_SAFE_ABI = [
        {
            "inputs": [
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "data", "type": "bytes"},
                {"name": "operation", "type": "uint8"},
                {"name": "safeTxGas", "type": "uint256"},
                {"name": "baseGas", "type": "uint256"},
                {"name": "gasPrice", "type": "uint256"},
                {"name": "gasToken", "type": "address"},
                {"name": "refundReceiver", "type": "address"},
                {"name": "signatures", "type": "bytes"}
            ],
            "name": "execTransaction",
            "outputs": [{"name": "success", "type": "bool"}],
            "stateMutability": "payable",
            "type": "function"
        },
        {
            "inputs": [],
            "name": "nonce",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]
    
    # CTF Exchange ABI for claiming - CORRECTED with proper signature
    CTF_EXCHANGE_ABI = [
        {
            "inputs": [
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"}
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
        },
        {
            "inputs": [
                {"name": "owner", "type": "address"},
                {"name": "positionId", "type": "uint256"}
            ],
            "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [
                {"name": "collateralToken", "type": "address"},
                {"name": "collectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSet", "type": "uint256"}
            ],
            "name": "getPositionId",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "pure",
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
        
        # Track redeemed markets in memory (for this session)
        self.redeemed_markets: Dict[str, datetime] = {}
        
        # Initialize database table for claimed markets
        self._init_claimed_markets_table()
        
        logger.info("PolymarketRedeemer initialized")
    
    def _init_claimed_markets_table(self):
        """Create table to track claimed markets (persists across restarts)"""
        try:
            import sqlite3
            
            conn = sqlite3.connect(self.config.db_file)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS claimed_markets (
                    market_id TEXT PRIMARY KEY,
                    market_title TEXT,
                    claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tx_hash TEXT
                )
            """)
            
            # 🧹 CLEANUP: Remove incorrectly marked claims (no real tx_hash)
            cursor.execute("""
                DELETE FROM claimed_markets 
                WHERE tx_hash = 'manually_claimed' OR tx_hash = '' OR tx_hash IS NULL
            """)
            deleted = cursor.rowcount
            
            if deleted > 0:
                logger.info(f"🧹 Cleaned up {deleted} incorrectly marked claimed markets")
            
            conn.commit()
            conn.close()
            logger.debug("✅ Claimed markets table initialized")
            
        except Exception as e:
            logger.warning(f"Failed to init claimed markets table: {e}")
    
    def _mark_market_claimed(self, market_id: str, market_title: str, tx_hash: str = ""):
        """Permanently mark a market as claimed in database"""
        try:
            import sqlite3
            
            conn = sqlite3.connect(self.config.db_file)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO claimed_markets (market_id, market_title, claimed_at, tx_hash)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?)
            """, (market_id, market_title, tx_hash))
            
            conn.commit()
            conn.close()
            
            # Also track in memory
            self.redeemed_markets[market_id] = datetime.now()
            
            logger.debug(f"✅ Marked market as claimed: {market_title}")
            
        except Exception as e:
            logger.warning(f"Failed to mark market as claimed: {e}")
    
    def _is_market_claimed(self, market_id: str) -> bool:
        """Check if market was already claimed (from database)"""
        try:
            import sqlite3
            
            conn = sqlite3.connect(self.config.db_file)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT market_id FROM claimed_markets WHERE market_id = ?
            """, (market_id,))
            
            result = cursor.fetchone()
            conn.close()
            
            return result is not None
            
        except Exception as e:
            logger.debug(f"Failed to check claimed status: {e}")
            return False
    
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
                        markets_claimed += 1
                        tx_hash = result.get('tx_hash', '')
                        logger.success(f"✅ Successfully claimed winnings from: {market['title']}")
                        logger.success(f"   TX: https://polygonscan.com/tx/{tx_hash}")
                        logger.info(f"   💰 Check your Polymarket balance to see the amount!")
                except Exception as e:
                    logger.error(f"Error claiming market {market.get('condition_id')}: {e}")
            
            if markets_claimed > 0:
                logger.success(f"🎉 Successfully claimed winnings from {markets_claimed} market(s)!")
                logger.info(f"💰 Your Polymarket balance has been updated!")
            
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
            checked_count = 0
            skipped_count = 0
            
            for market_id, market_title in your_markets:
                try:
                    # ⚡ OPTIMIZATION: Skip if already claimed (from database)
                    if self._is_market_claimed(market_id):
                        logger.debug(f"⏭️  Skipping already claimed market: {market_title}")
                        skipped_count += 1
                        continue
                    
                    checked_count += 1
                    
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
            
            if skipped_count > 0:
                logger.info(f"⚡ Skipped {skipped_count} already-claimed markets (checked {checked_count} new ones)")
            
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
    
    async def _get_claimed_amount(self, tx_hash: str) -> float:
        """Get the actual amount claimed from transaction logs"""
        try:
            # Get transaction receipt
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            
            # USDC Transfer event signature
            transfer_topic = self.w3.keccak(text="Transfer(address,address,address)").hex()
            
            # Look for USDC transfers to the user
            for log in receipt['logs']:
                if log['address'].lower() == self.USDC_ADDRESS.lower():
                    # This is a USDC transfer
                    if len(log['topics']) >= 3:
                        # topics[2] is the 'to' address
                        to_address = '0x' + log['topics'][2].hex()[-40:]
                        
                        # Check if transfer is TO our wallet (claiming winnings)
                        private_key = self.client.config.private_key
                        if private_key.startswith('0x'):
                            private_key = private_key[2:]
                        account = self.w3.eth.account.from_key(private_key)
                        
                        if to_address.lower() == account.address.lower():
                            # Parse the amount (USDC has 6 decimals)
                            amount_raw = int(log['data'].hex(), 16)
                            amount = amount_raw / 1e6
                            logger.debug(f"Found USDC transfer: {amount} USDC")
                            return amount
            
            # If no transfer found, try to estimate from balance change
            logger.debug("No USDC transfer found in logs, returning 0")
            return 0.0
            
        except Exception as e:
            logger.debug(f"Error getting claimed amount: {e}")
            return 0.0
    
    def _has_claimable_tokens(self, condition_id: str) -> bool:
        """Check if user actually has tokens to claim for this market"""
        try:
            funder_address = Web3.to_checksum_address(self.client.config.funder_address)
            condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id)
            parent_collection_id = bytes(32)  # 0x0000...
            
            # Check both YES (index 1) and NO (index 2) positions
            for index_set in [1, 2]:
                try:
                    # Get position ID for this outcome
                    position_id = self.conditional_tokens.functions.getPositionId(
                        Web3.to_checksum_address(self.USDC_ADDRESS),
                        parent_collection_id,
                        condition_id_bytes,
                        index_set
                    ).call()
                    
                    # Check balance
                    balance = self.conditional_tokens.functions.balanceOf(
                        funder_address,
                        position_id
                    ).call()
                    
                    if balance > 0:
                        logger.debug(f"Found {balance} tokens for index {index_set}")
                        return True
                        
                except Exception as e:
                    logger.debug(f"Error checking position for index {index_set}: {e}")
                    continue
            
            return False
            
        except Exception as e:
            logger.debug(f"Error checking claimable tokens: {e}")
            # If we can't check, assume there might be tokens (better safe than sorry)
            return True
    
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
            
            # Use Gnosis Safe proxy to claim (required for Polymarket proxies)
            success, tx_hash = await self._claim_via_gnosis_safe(condition_id)
            
            if success:
                # ✅ Mark as claimed in database (persists across restarts)
                self._mark_market_claimed(condition_id, market['title'], tx_hash)
                
                return {
                    'success': True,
                    'amount': 0,  # Amount is in the blockchain transaction
                    'tx_hash': tx_hash
                }
            
            return {'success': False}
            
        except Exception as e:
            logger.error(f"Error claiming winnings: {e}")
            return {'success': False}
    
    async def _claim_via_gnosis_safe(self, condition_id: str) -> Tuple[bool, str]:
        """Claim through Gnosis Safe proxy. Returns (success, tx_hash)"""
        try:
            logger.info(f"🔄 Attempting claim via Gnosis Safe proxy for condition: {condition_id[:10]}...")
            
            # Get proxy address (Gnosis Safe)
            proxy_address = Web3.to_checksum_address(self.client.config.funder_address)
            
            # Get private key
            private_key = self.client.config.private_key
            if private_key.startswith('0x'):
                private_key = private_key[2:]
            
            # Derive signer address
            account = self.w3.eth.account.from_key(private_key)
            signer_address = account.address
            
            logger.info(f"Gnosis Safe proxy: {proxy_address}")
            logger.info(f"Signer (owner): {signer_address}")
            
            # Prepare the redeemPositions call data
            condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id)
            parent_collection_id = bytes(32)
            index_sets = [1, 2]
            
            # Encode the function call
            redeem_call_data = self.conditional_tokens.functions.redeemPositions(
                Web3.to_checksum_address(self.USDC_ADDRESS),
                parent_collection_id,
                condition_id_bytes,
                index_sets
            )._encode_transaction_data()
            
            logger.debug(f"Encoded redeem call data: {redeem_call_data[:66]}...")
            
            # Get Safe contract
            safe_contract = self.w3.eth.contract(
                address=proxy_address,
                abi=self.GNOSIS_SAFE_ABI
            )
            
            # Get nonce
            safe_nonce = safe_contract.functions.nonce().call()
            
            logger.debug(f"Safe nonce: {safe_nonce}")
            
            # Create Safe transaction hash for signing
            safe_tx_hash = self.w3.keccak(
                encode(
                    ['address', 'uint256', 'bytes', 'uint8', 'uint256', 'uint256', 'uint256', 'address', 'address', 'uint256'],
                    [
                        Web3.to_checksum_address(self.CONDITIONAL_TOKENS_ADDRESS),
                        0,  # value
                        bytes.fromhex(redeem_call_data[2:]),
                        0,  # operation
                        0,  # safeTxGas
                        0,  # baseGas
                        0,  # gasPrice
                        Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),
                        Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),
                        safe_nonce
                    ]
                )
            )
            
            # Sign the Safe transaction hash
            from eth_account.messages import encode_defunct
            message = encode_defunct(safe_tx_hash)
            safe_signature = account.sign_message(message)
            
            # Format signature for Gnosis Safe (r + s + v)
            signature_bytes = safe_signature.signature
            
            logger.debug(f"Safe signature created: {signature_bytes.hex()[:20]}...")
            
            # Build Safe transaction with proper signature
            safe_tx_data = safe_contract.functions.execTransaction(
                Web3.to_checksum_address(self.CONDITIONAL_TOKENS_ADDRESS),  # to
                0,  # value
                bytes.fromhex(redeem_call_data[2:]),  # data
                0,  # operation (0 = CALL)
                0,  # safeTxGas
                0,  # baseGas
                0,  # gasPrice
                "0x0000000000000000000000000000000000000000",  # gasToken
                "0x0000000000000000000000000000000000000000",  # refundReceiver
                signature_bytes  # Proper signature
            ).build_transaction({
                'from': signer_address,
                'gas': 500000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(signer_address)
            })
            
            logger.info("Signing and sending transaction...")
            
            # Sign and send the blockchain transaction
            signed_tx = self.w3.eth.account.sign_transaction(safe_tx_data, private_key)
            raw_tx = signed_tx.raw_transaction if hasattr(signed_tx, 'raw_transaction') else signed_tx.rawTransaction
            tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
            
            logger.info(f"✅ Safe transaction sent: {tx_hash.hex()}")
            logger.info("Waiting for confirmation (up to 2 minutes)...")
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt['status'] == 1:
                logger.success(f"🎉 Claim successful via Gnosis Safe! TX: https://polygonscan.com/tx/{tx_hash.hex()}")
                return True, tx_hash.hex()
            else:
                logger.error(f"❌ Transaction failed. Receipt: {receipt}")
            
            return False, ""
            
        except Exception as e:
            logger.error(f"❌ Gnosis Safe claim failed: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return False, ""
    
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
