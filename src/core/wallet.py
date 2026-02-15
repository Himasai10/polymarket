"""
Wallet management: balance checks, address derivation.

Addresses: CORE-03 (USDC balance via web3.py>=7.0)
Prevents: Pitfall 2 (proxy wallet confusion)
"""

from __future__ import annotations

from typing import Any

import structlog
from eth_account import Account
from web3 import Web3

from .config import Settings

logger = structlog.get_logger()

# Native USDC contract on Polygon (NOT bridged USDC.e)
# C-04 FIX: Was using bridged USDC.e (0x2791Bca1f...) which returns wrong balances
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

# Minimal ERC20 ABI for balanceOf
USDC_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]


class WalletManager:
    """Manages wallet operations: balance checks, address derivation.

    Handles the Polymarket proxy wallet architecture where the
    signing key (private key) differs from the funded address (funder).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._w3: Web3 | None = None
        self._account: Any = None
        self._funder_address: str = ""

    def initialize(self) -> None:
        """Initialize web3 connection and derive addresses."""
        # M-23 FIX: Add connection timeout for web3 RPC calls
        self._w3 = Web3(
            Web3.HTTPProvider(
                self.settings.polygon_rpc_url,
                request_kwargs={"timeout": 30},
            )
        )

        if not self._w3.is_connected():
            logger.error("polygon_connection_failed", rpc_url=self.settings.polygon_rpc_url)
            raise ConnectionError(f"Cannot connect to Polygon RPC: {self.settings.polygon_rpc_url}")

        logger.info("polygon_connected", rpc_url=self.settings.polygon_rpc_url)

        # Derive account from private key
        pk = self.settings.wallet_private_key.get_secret_value()
        if pk:
            self._account = Account.from_key(pk)

            # Use configured funder address, or derive from private key
            if self.settings.funder_address:
                self._funder_address = self.settings.funder_address
            else:
                self._funder_address = self._account.address

            logger.info(
                "wallet_initialized",
                signing_address=self._account.address,
                funder_address=self._funder_address,
            )
        else:
            logger.warning("wallet_skipped", reason="no private key configured")

    @property
    def signing_address(self) -> str:
        """The address derived from the private key (used for signing orders)."""
        if self._account is None:
            raise RuntimeError("Wallet not initialized. Call initialize() first.")
        return self._account.address  # type: ignore[no-any-return]

    @property
    def funder_address(self) -> str:
        """The funder address (holds USDC, may differ from signing address)."""
        if not self._funder_address:
            raise RuntimeError("Wallet not initialized. Call initialize() first.")
        return self._funder_address

    @property
    def is_initialized(self) -> bool:
        """True if private key was provided and account derived."""
        return self._account is not None and bool(self._funder_address)

    def get_usdc_balance(self) -> float:
        """Get USDC balance for the funder address.

        Returns balance in USDC (6 decimal places on Polygon).
        In paper mode, returns the virtual paper balance.
        Addresses: CORE-03
        """
        if self._w3 is None:
            raise RuntimeError("Web3 not initialized. Call initialize() first.")

        # Paper mode: return virtual balance
        if not self.settings.is_live:
            paper_balance = getattr(self.settings, "paper_balance_usd", 100.0)
            logger.debug("paper_balance_used", balance=paper_balance)
            return float(paper_balance)

        if not self.is_initialized:
            logger.debug("usdc_balance_zero", reason="wallet not initialized (no private key)")
            return 0.0

        try:
            usdc_contract = self._w3.eth.contract(
                address=Web3.to_checksum_address(USDC_ADDRESS),
                abi=USDC_ABI,
            )
            raw_balance = usdc_contract.functions.balanceOf(
                Web3.to_checksum_address(self.funder_address)
            ).call()

            # USDC has 6 decimal places on Polygon
            balance = raw_balance / 1e6

            logger.info("usdc_balance_checked", balance=balance, address=self.funder_address)
            return float(balance)

        except Exception as e:
            logger.error("usdc_balance_check_failed", error=str(e))
            raise

    def get_matic_balance(self) -> float:
        """Get MATIC (POL) balance for gas fees.

        Returns 0.0 if the wallet is not initialized.
        """
        if self._w3 is None:
            raise RuntimeError("Web3 not initialized. Call initialize() first.")

        if not self.is_initialized:
            return 0.0

        try:
            raw_balance = self._w3.eth.get_balance(Web3.to_checksum_address(self.funder_address))
            balance = float(self._w3.from_wei(raw_balance, "ether"))
            logger.info("matic_balance_checked", balance=balance)
            return balance

        except Exception as e:
            logger.error("matic_balance_check_failed", error=str(e))
            raise

    def verify_connection(self) -> dict[str, bool | str | float]:
        """Verify wallet setup: connection, balances, address derivation.

        Returns a status dict for health checks.
        """
        status: dict[str, bool | str | float] = {
            "connected": False,
            "signing_address": "",
            "funder_address": "",
            "usdc_balance": 0.0,
            "matic_balance": 0.0,
        }

        try:
            if self._w3 and self._w3.is_connected():
                status["connected"] = True

            if self._account:
                status["signing_address"] = self._account.address
                status["funder_address"] = self._funder_address

            if status["connected"] and self._funder_address:
                status["usdc_balance"] = self.get_usdc_balance()
                status["matic_balance"] = self.get_matic_balance()

        except Exception as e:
            logger.error("wallet_verification_failed", error=str(e))

        return status
