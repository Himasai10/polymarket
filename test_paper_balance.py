#!/usr/bin/env python3
"""Quick test of paper balance setup."""

import sys

sys.path.insert(0, ".")

from src.core.config import load_settings
from src.core.wallet import WalletManager
from src.monitoring.logger import setup_logging

setup_logging(log_level="INFO", json_output=False)

settings = load_settings()
print(f"Trading mode: {settings.trading_mode}")
print(f"Paper balance: ${settings.paper_balance_usd}")
print(f"Is live: {settings.is_live}")

wallet = WalletManager(settings)
wallet.initialize()
balance = wallet.get_usdc_balance()
print(f"Wallet balance returned: ${balance}")
