#!/usr/bin/env python3
"""
Quick script to check your Polymarket account balance
"""
import os
import sys
from dotenv import load_dotenv
from web3 import Web3

# Load environment variables
load_dotenv()

def check_balance():
    """Check USDC balance on Polygon"""
    
    # Polygon RPC
    rpc_url = "https://polygon-rpc.com"
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    if not w3.is_connected():
        print("❌ Could not connect to Polygon network")
        return
    
    # Get funder address from .env
    funder_address = os.getenv("FUNDER_ADDRESS")
    if not funder_address:
        print("❌ FUNDER_ADDRESS not found in .env file")
        return
    
    # Convert to checksum address
    funder_address = w3.to_checksum_address(funder_address)
    print(f"🔍 Checking balance for: {funder_address}")
    
    # USDC contract on Polygon
    usdc_address = w3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
    
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
    
    try:
        # Create contract instance
        usdc_contract = w3.eth.contract(address=usdc_address, abi=erc20_abi)
        
        # Get balance
        balance_wei = usdc_contract.functions.balanceOf(funder_address).call()
        decimals = usdc_contract.functions.decimals().call()
        
        # Convert to human readable
        balance_usdc = balance_wei / (10 ** decimals)
        
        print(f"💰 USDC Balance: {balance_usdc:.2f} USDC")
        
        if balance_usdc < 10:
            print("⚠️  Warning: Low USDC balance. You may want to deposit more funds.")
        
        print("ℹ️  Note: Polymarket handles gas fees automatically - no MATIC needed in your wallet!")
            
    except Exception as e:
        print(f"❌ Error checking balance: {e}")

if __name__ == "__main__":
    print("💰 Polymarket Balance Checker")
    print("=" * 40)
    check_balance()
