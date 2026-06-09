#!/usr/bin/env python3
"""
Manual trade test script for Polymarket
Use this to test placing a single trade on your live account
"""

import asyncio
import os
from datetime import datetime
from loguru import logger
from web3 import Web3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.polymarket_client import PolymarketClient
from src.core.config import polymarket_config, bot_config
from src.core.models import TradeSide

class ManualTradeTest:
    def __init__(self):
        self.client = PolymarketClient()
        
    def initialize(self):
        """Initialize the client"""
        logger.info("Initializing Polymarket client...")
        success = self.client.initialize()
        if not success:
            logger.error("Failed to initialize client")
            return False
        return True
    
    def check_balance(self):
        """Check current USDC balance using Web3"""
        try:
            logger.info("Checking USDC balance on Polygon...")
            
            # Polygon RPC
            rpc_url = "https://polygon-rpc.com"
            w3 = Web3(Web3.HTTPProvider(rpc_url))
            
            if not w3.is_connected():
                logger.error("Could not connect to Polygon network")
                return 0.0
            
            # Get funder address from environment
            funder_address = os.getenv("FUNDER_ADDRESS")
            if not funder_address:
                logger.error("FUNDER_ADDRESS not found in .env file")
                return 0.0
            
            # Convert to checksum address
            funder_address = w3.to_checksum_address(funder_address)
            logger.info(f"Checking balance for: {funder_address}")
            
            # pUSD collateral token on Polygon (CLOB V2 replaced USDC.e with pUSD)
            usdc_address = w3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
            
            # ERC20 ABI for balance check
            erc20_abi = [
                {
                    "constant": True,
                    "inputs": [{"name": "_owner", "type": "address"}],
                    "name": "balanceOf",
                    "outputs": [{"name": "balance", "type": "uint256"}],
                    "type": "function"
                },
                {
                    "constant": True,
                    "inputs": [],
                    "name": "decimals",
                    "outputs": [{"name": "", "type": "uint8"}],
                    "type": "function"
                }
            ]
            
            # Create contract instance
            usdc_contract = w3.eth.contract(address=usdc_address, abi=erc20_abi)
            
            # Get USDC balance
            balance_wei = usdc_contract.functions.balanceOf(funder_address).call()
            decimals = usdc_contract.functions.decimals().call()
            balance_usdc = balance_wei / (10 ** decimals)
            
            logger.info(f"USDC Balance: {balance_usdc:.2f} USDC")
            
            # Warnings
            if balance_usdc < 10:
                logger.warning("Low USDC balance. You may want to deposit more funds.")
            
            logger.info("Polymarket handles gas fees automatically - no MATIC needed!")
            
            return balance_usdc
            
        except Exception as e:
            logger.error(f"Error checking balance: {e}")
            return 0.0
    
    def place_test_trade(self, token_id: str, side: str, amount_usd: float):
        """Place a test trade"""
        try:
            logger.info(f"Placing test trade:")
            logger.info(f"  Token ID: {token_id}")
            logger.info(f"  Side: {side}")
            logger.info(f"  Amount: ${amount_usd}")
            
            # Convert side to TradeSide enum
            trade_side = TradeSide.BUY if side.upper() == "BUY" else TradeSide.SELL
            
            # Place the order
            order_id = self.client.place_market_order(
                token_id=token_id,
                side=trade_side,
                amount_usd=amount_usd
            )
            
            if order_id:
                logger.success(f"Trade placed successfully! Order ID: {order_id}")
                
                # Wait a moment and check order status
                import time
                time.sleep(2)
                
                order_status = self.client.get_order_status(order_id)
                if order_status:
                    logger.info(f"Order status: {order_status}")
                
                return order_id
            else:
                logger.error("Failed to place trade")
                return None
                
        except Exception as e:
            logger.error(f"Error placing trade: {e}")
            return None

def main():
    """Main function"""
    logger.info("=== Manual Trade Test ===")
    
    # Initialize trader
    trader = ManualTradeTest()
    
    if not trader.initialize():
        logger.error("Failed to initialize. Exiting.")
        return
    
    # Check balance
    balance = trader.check_balance()
    if balance < 1.0:
        logger.error("Insufficient balance for testing")
        return
    
    # Interactive trade placement
    print("\n" + "="*50)
    print("MANUAL TRADE TEST")
    print("="*50)
    
    print(f"Current balance: ${balance:.2f} USDC")
    print("\nTo place a test trade, you need:")
    print("1. Token ID (from Polymarket market)")
    print("2. Side (BUY or SELL)")
    print("3. Amount in USD")
    
    print("\nExample token IDs:")
    print("- Find them on Polymarket by inspecting network requests")
    print("- Or use the debug_trades.py script to find active markets")
    
    # Get user input
    try:
        token_id = input("\nEnter token ID: ").strip()
        if not token_id:
            logger.error("Token ID is required")
            return
            
        side = input("Enter side (BUY/SELL): ").strip().upper()
        if side not in ["BUY", "SELL"]:
            logger.error("Side must be BUY or SELL")
            return
            
        amount_str = input("Enter amount in USD (e.g., 1.00): ").strip()
        try:
            amount = float(amount_str)
            if amount <= 0:
                logger.error("Amount must be positive")
                return
            if amount > balance:
                logger.error(f"Amount ${amount} exceeds balance ${balance:.2f}")
                return
        except ValueError:
            logger.error("Invalid amount format")
            return
        
        # Confirm trade
        print(f"\nConfirm trade:")
        print(f"  Token: {token_id}")
        print(f"  Side: {side}")
        print(f"  Amount: ${amount}")
        
        confirm = input("\nProceed? (yes/no): ").strip().lower()
        if confirm not in ["yes", "y"]:
            logger.info("Trade cancelled")
            return
        
        # Place the trade
        logger.info("Placing trade...")
        order_id = trader.place_test_trade(token_id, side, amount)
        
        if order_id:
            print(f"\n✅ Trade successful!")
            print(f"Order ID: {order_id}")
        else:
            print(f"\n❌ Trade failed!")
            
    except KeyboardInterrupt:
        logger.info("Trade cancelled by user")
    except Exception as e:
        logger.error(f"Error in interactive mode: {e}")

if __name__ == "__main__":
    main()
