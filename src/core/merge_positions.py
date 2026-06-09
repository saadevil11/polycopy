"""
Merge positions functionality for Polymarket
"""
import json
from typing import Dict, List, Optional
from loguru import logger
from web3 import Web3
from eth_account import Account
from src.core.polymarket_client import PolymarketClient
from src.core.config import bot_config

class PositionMerger:
    """Handle merging of YES/NO positions back to USDC"""
    
    CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Polygon mainnet
    USDC_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # pUSD collateral (CLOB V2)
    
    # ConditionalTokens ABI for merge and redeem functions
    CONDITIONAL_TOKENS_ABI = [
        {
            "inputs": [
                {"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "partition", "type": "uint256[]"},
                {"name": "amount", "type": "uint256"}
            ],
            "name": "mergePositions",
            "outputs": [],
            "payable": False,
            "stateMutability": "nonpayable",
            "type": "function"
        },
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
        }
    ]
    
    def __init__(self, polymarket_client: PolymarketClient):
        self.client = polymarket_client
        self.w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        
        # Initialize contract
        self.conditional_tokens = self.w3.eth.contract(
            address=self.CONDITIONAL_TOKENS_ADDRESS,
            abi=self.CONDITIONAL_TOKENS_ABI
        )
    
    def get_mergeable_positions(self) -> List[Dict]:
        """Get positions that can be merged (user has both YES and NO tokens)"""
        try:
            # Get user's current positions
            positions = self.client.get_current_positions()
            
            # Group by market/condition
            market_positions = {}
            for position in positions:
                market_id = position.get('market_id')
                if market_id not in market_positions:
                    market_positions[market_id] = {'YES': None, 'NO': None}
                
                # Determine if this is YES or NO based on token price/position
                # In Polymarket, YES tokens typically have higher token IDs
                token_id = position.get('token_id')
                size = float(position.get('size', 0))
                
                if size > 0:
                    # Simple heuristic: if price > 0.5, likely YES token
                    price = float(position.get('avg_price', 0))
                    if price > 0.5:
                        market_positions[market_id]['YES'] = position
                    else:
                        market_positions[market_id]['NO'] = position
            
            # Find markets where user has both YES and NO positions
            mergeable = []
            for market_id, positions in market_positions.items():
                if positions['YES'] and positions['NO']:
                    yes_size = float(positions['YES'].get('size', 0))
                    no_size = float(positions['NO'].get('size', 0))
                    
                    # Can merge the minimum of both positions
                    mergeable_amount = min(yes_size, no_size)
                    if mergeable_amount > 0:
                        mergeable.append({
                            'market_id': market_id,
                            'yes_position': positions['YES'],
                            'no_position': positions['NO'],
                            'mergeable_amount': mergeable_amount,
                            'estimated_usdc_return': mergeable_amount  # 1:1 ratio for complete sets
                        })
            
            return mergeable
            
        except Exception as e:
            logger.error(f"Error getting mergeable positions: {e}")
            return []
    
    def should_copy_merge(self, market_id: str, target_trader_address: str) -> bool:
        """Check if we should merge based on target trader's merge action"""
        try:
            # Only merge if target trader merged this specific market
            # This will be called when we detect a merge action from target trader
            return True
            
        except Exception as e:
            logger.error(f"Error determining merge copy: {e}")
            return False
    
    def merge_positions(self, market_id: str, amount: float, dry_run: bool = False) -> bool:
        """Merge YES and NO positions back to USDC"""
        try:
            if dry_run:
                logger.info(f"[DRY RUN] Would merge {amount} positions for market {market_id}")
                return True
            
            logger.info(f"Attempting to merge {amount} positions for market {market_id}")
            
            # Get market condition ID and other required parameters
            # This would need to be implemented based on Polymarket's specific format
            condition_id = self._get_condition_id(market_id)
            parent_collection_id = "0x" + "00" * 32  # Usually zero for base collateral
            partition = [1, 2]  # YES and NO outcomes
            
            # Convert amount to Wei (USDC has 6 decimals)
            amount_wei = int(amount * 10**6)
            
            # Build transaction
            tx_data = self.conditional_tokens.functions.mergePositions(
                self.USDC_ADDRESS,
                parent_collection_id,
                condition_id,
                partition,
                amount_wei
            ).build_transaction({
                'from': self.client.get_address(),
                'gas': 200000,  # Estimate
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(self.client.get_address())
            })
            
            # Sign and send transaction
            private_key = self.client.get_private_key()
            signed_tx = self.w3.eth.account.sign_transaction(tx_data, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logger.success(f"Merge transaction sent: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            if receipt.status == 1:
                logger.success(f"Successfully merged {amount} positions for market {market_id}")
                return True
            else:
                logger.error(f"Merge transaction failed for market {market_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error merging positions: {e}")
            return False
    
    def _get_condition_id(self, market_id: str) -> str:
        """Get condition ID from market ID"""
        # This would need to be implemented based on how Polymarket
        # maps market IDs to condition IDs
        # For now, return a placeholder
        return "0x" + market_id.replace("0x", "").ljust(64, "0")
    
    def copy_merge_for_market(self, market_id: str, dry_run: bool = False) -> Dict:
        """Merge all possible positions for a specific market (copying target trader)"""
        try:
            logger.info(f"Copying merge action for market {market_id}")
            
            # Get mergeable positions for this specific market
            all_mergeable = self.get_mergeable_positions()
            market_position = None
            
            for position in all_mergeable:
                if position['market_id'] == market_id:
                    market_position = position
                    break
            
            if not market_position:
                logger.info(f"No mergeable positions found for market {market_id}")
                return {'merged': 0, 'reason': 'no_mergeable_positions'}
            
            # Merge ALL possible shares for this market (like target trader does)
            mergeable_amount = market_position['mergeable_amount']
            
            logger.info(f"Merging {mergeable_amount} shares for market {market_id} (copying target trader)")
            
            success = self.merge_positions(market_id, mergeable_amount, dry_run)
            
            if success:
                result = {
                    'merged': 1,
                    'market_id': market_id,
                    'amount_merged': mergeable_amount,
                    'usdc_recovered': mergeable_amount
                }
                logger.success(f"Successfully copied merge: {result}")
                return result
            else:
                return {'merged': 0, 'failed': 1, 'reason': 'merge_failed'}
            
        except Exception as e:
            logger.error(f"Error copying merge for market {market_id}: {e}")
            return {'error': str(e)}
    
    def get_redeemable_positions(self) -> List[Dict]:
        """Get positions that can be redeemed (winning tokens in resolved markets)"""
        try:
            # Get user's current positions
            positions = self.client.get_current_positions()
            
            redeemable = []
            for position in positions:
                market_id = position.get('market_id')
                token_id = position.get('token_id')
                size = float(position.get('size', 0))
                
                if size > 0:
                    # Check if market is resolved and this is a winning token
                    # This would need market resolution data from Polymarket API
                    # For now, we'll return positions that could potentially be redeemed
                    redeemable.append({
                        'market_id': market_id,
                        'token_id': token_id,
                        'position': position,
                        'redeemable_amount': size,
                        'estimated_usdc_return': size  # 1:1 ratio for winning tokens
                    })
            
            return redeemable
            
        except Exception as e:
            logger.error(f"Error getting redeemable positions: {e}")
            return []
    
    def redeem_positions(self, market_id: str, amount: float, dry_run: bool = False) -> bool:
        """Redeem winning positions back to USDC"""
        try:
            if dry_run:
                logger.info(f"[DRY RUN] Would redeem {amount} winning tokens for market {market_id}")
                return True
            
            logger.info(f"Attempting to redeem {amount} winning tokens for market {market_id}")
            
            # Get market condition ID and other required parameters
            condition_id = self._get_condition_id(market_id)
            parent_collection_id = "0x" + "00" * 32  # Usually zero for base collateral
            
            # For redeem, we need to specify which outcome won
            # This would need to be determined from market resolution data
            # For now, we'll assume outcome index 0 (YES) won
            winning_outcome_index = [1]  # This should be determined from market data
            
            # Convert amount to Wei (USDC has 6 decimals)
            amount_wei = int(amount * 10**6)
            
            # Build transaction
            tx_data = self.conditional_tokens.functions.redeemPositions(
                self.USDC_ADDRESS,
                parent_collection_id,
                condition_id,
                winning_outcome_index
            ).build_transaction({
                'from': self.client.get_address(),
                'gas': 200000,  # Estimate
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(self.client.get_address())
            })
            
            # Sign and send transaction
            private_key = self.client.get_private_key()
            signed_tx = self.w3.eth.account.sign_transaction(tx_data, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logger.success(f"Redeem transaction sent: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            if receipt.status == 1:
                logger.success(f"Successfully redeemed {amount} winning tokens for market {market_id}")
                return True
            else:
                logger.error(f"Redeem transaction failed for market {market_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error redeeming positions: {e}")
            return False
    
    def copy_redeem_for_market(self, market_id: str, dry_run: bool = False) -> Dict:
        """Redeem winning positions for a specific market (copying target trader)"""
        try:
            logger.info(f"Copying redeem action for market {market_id}")
            
            # Get redeemable positions for this specific market
            all_redeemable = self.get_redeemable_positions()
            market_position = None
            
            for position in all_redeemable:
                if position['market_id'] == market_id:
                    market_position = position
                    break
            
            if not market_position:
                logger.info(f"No redeemable positions found for market {market_id}")
                return {'redeemed': 0, 'reason': 'no_redeemable_positions'}
            
            # Redeem ALL winning tokens for this market (like target trader does)
            redeemable_amount = market_position['redeemable_amount']
            
            logger.info(f"Redeeming {redeemable_amount} winning tokens for market {market_id} (copying target trader)")
            
            success = self.redeem_positions(market_id, redeemable_amount, dry_run)
            
            if success:
                result = {
                    'redeemed': 1,
                    'market_id': market_id,
                    'amount_redeemed': redeemable_amount,
                    'usdc_recovered': redeemable_amount
                }
                logger.success(f"Successfully copied redeem: {result}")
                return result
            else:
                return {'redeemed': 0, 'failed': 1, 'reason': 'redeem_failed'}
            
        except Exception as e:
            logger.error(f"Error copying redeem for market {market_id}: {e}")
            return {'error': str(e)}
