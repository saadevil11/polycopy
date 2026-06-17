"""
Setup script to configure token allowances for MetaMask/EOA users
This needs to be run once before trading if you're using MetaMask or hardware wallet
"""
import os
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# Contract addresses on Polygon (CLOB V2)
USDC_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # pUSD collateral (V2)
CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# Contracts to approve (V2 exchanges)
EXCHANGE_ADDRESSES = [
    "0xE111180000d2663C0091e4f400237545B87B996B",  # CTF Exchange V2
    "0xe2222d279d744050d28e00520010520000310F59",  # Neg risk CTF Exchange V2
    "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",  # Neg risk adapter
]

# ERC20 ABI (minimal for approve function)
ERC20_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]

def setup_allowances():
    """Set up token allowances for trading"""
    
    # Get configuration
    private_key = os.getenv("PRIVATE_KEY")
    rpc_url = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
    
    if not private_key:
        logger.error("PRIVATE_KEY not found in environment variables")
        return False
    
    try:
        # Connect to Polygon
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        if not w3.is_connected():
            logger.error("Failed to connect to Polygon network")
            return False
        
        # Get account
        account = Account.from_key(private_key)
        logger.info(f"Setting up allowances for account: {account.address}")
        
        # Maximum allowance (2^256 - 1)
        max_allowance = 2**256 - 1
        
        # Set up USDC allowances
        usdc_contract = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)
        
        for exchange_address in EXCHANGE_ADDRESSES:
            logger.info(f"Setting USDC allowance for {exchange_address}")
            
            # Check current allowance
            current_allowance = usdc_contract.functions.allowance(
                account.address, 
                exchange_address
            ).call()
            
            if current_allowance >= max_allowance // 2:  # If allowance is already high enough
                logger.info(f"USDC allowance already sufficient: {current_allowance}")
                continue
            
            # Build transaction
            tx = usdc_contract.functions.approve(
                exchange_address,
                max_allowance
            ).build_transaction({
                'from': account.address,
                'nonce': w3.eth.get_transaction_count(account.address),
                'gas': 100000,
                'gasPrice': w3.eth.gas_price,
            })
            
            # Sign and send transaction
            signed_tx = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logger.info(f"USDC approval transaction sent: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            
            if receipt.status == 1:
                logger.success(f"USDC approval confirmed for {exchange_address}")
            else:
                logger.error(f"USDC approval failed for {exchange_address}")
        
        # Set up Conditional Tokens allowances
        ct_contract = w3.eth.contract(address=CONDITIONAL_TOKENS_ADDRESS, abi=ERC20_ABI)
        
        for exchange_address in EXCHANGE_ADDRESSES:
            logger.info(f"Setting Conditional Tokens allowance for {exchange_address}")
            
            # Check current allowance
            current_allowance = ct_contract.functions.allowance(
                account.address, 
                exchange_address
            ).call()
            
            if current_allowance >= max_allowance // 2:  # If allowance is already high enough
                logger.info(f"CT allowance already sufficient: {current_allowance}")
                continue
            
            # Build transaction
            tx = ct_contract.functions.approve(
                exchange_address,
                max_allowance
            ).build_transaction({
                'from': account.address,
                'nonce': w3.eth.get_transaction_count(account.address),
                'gas': 100000,
                'gasPrice': w3.eth.gas_price,
            })
            
            # Sign and send transaction
            signed_tx = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logger.info(f"CT approval transaction sent: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            
            if receipt.status == 1:
                logger.success(f"CT approval confirmed for {exchange_address}")
            else:
                logger.error(f"CT approval failed for {exchange_address}")
        
        logger.success("All allowances set up successfully!")
        logger.info("You can now start trading with the bot")
        return True
        
    except Exception as e:
        logger.error(f"Error setting up allowances: {e}")
        return False

if __name__ == "__main__":
    logger.info("Setting up token allowances for Polymarket trading...")
    
    print("⚠️  WARNING: This will set maximum allowances for Polymarket contracts.")
    print("⚠️  Only run this if you trust the Polymarket contracts.")
    print("⚠️  Make sure you're on the correct network (Polygon mainnet).")
    
    confirm = input("Do you want to continue? (yes/no): ")
    
    if confirm.lower() in ['yes', 'y']:
        success = setup_allowances()
        if success:
            print("✅ Setup completed successfully!")
        else:
            print("❌ Setup failed. Check the logs for details.")
    else:
        print("Setup cancelled.")
