#!/usr/bin/env python3
"""
Check which address a private key controls
"""
from web3 import Web3
import sys

def check_private_key(private_key: str):
    """Check which address a private key controls"""
    try:
        # Remove 0x if present
        if private_key.startswith('0x'):
            private_key = private_key[2:]
        
        w3 = Web3()
        account = w3.eth.account.from_key(private_key)
        
        print(f"\n✅ Private key controls address: {account.address}")
        print(f"\nExpected (Polymarket proxy): 0xde9Be3a24222Bf43F7A900F237849de561E8178b")
        
        if account.address.lower() == "0xde9Be3a24222Bf43F7A900F237849de561E8178b".lower():
            print("\n🎉 MATCH! This is the correct private key!")
        else:
            print(f"\n❌ MISMATCH! This private key controls a different address.")
            print(f"You need to export the private key from Phantom for address:")
            print(f"0xde9Be3a24222Bf43F7A900F237849de561E8178b")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_private_key.py <private_key>")
        sys.exit(1)
    
    check_private_key(sys.argv[1])

