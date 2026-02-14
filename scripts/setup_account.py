#!/usr/bin/env python3
"""Guided onboarding wizard for Polymarket Trading Bot (CORE-11).

Interactive CLI that walks through:
1. Wallet setup (private key -> derive funder address)
2. API key configuration
3. Balance verification
4. First test trade
5. .env file generation
6. Verification that everything works

Usage:
    python -m scripts.setup_account
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def print_banner() -> None:
    print()
    print("=" * 60)
    print("  Polymarket Trading Bot - Account Setup Wizard")
    print("=" * 60)
    print()
    print("This wizard will guide you through setting up your bot.")
    print("You'll need:")
    print("  1. A Polymarket account (https://polymarket.com)")
    print("  2. Your wallet private key")
    print("  3. CLOB API credentials")
    print("  4. Some USDC on Polygon for trading")
    print()


def prompt(message: str, default: str = "", secret: bool = False) -> str:
    """Prompt user for input with optional default value."""
    if default:
        display = f"{message} [{default}]: "
    else:
        display = f"{message}: "

    if secret:
        import getpass

        value = getpass.getpass(display)
    else:
        value = input(display)

    return value.strip() or default


def confirm(message: str, default: bool = True) -> bool:
    """Ask yes/no question."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    response = input(message + suffix).strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


def step_wallet_setup() -> tuple[str, str]:
    """Step 1: Configure wallet."""
    print()
    print("-" * 40)
    print("Step 1: Wallet Configuration")
    print("-" * 40)
    print()
    print("Your wallet private key is used to sign orders on Polymarket.")
    print("It is stored ONLY in your local .env file and NEVER transmitted anywhere.")
    print()

    private_key = prompt("Enter your wallet private key (0x...)", secret=True)
    if not private_key:
        print("ERROR: Private key is required.")
        sys.exit(1)

    # Normalize
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    # Derive funder address
    try:
        from eth_account import Account

        account = Account.from_key(private_key)
        funder_address = account.address
        print(f"\nDerived wallet address: {funder_address}")
    except Exception as e:
        print(f"\nERROR: Invalid private key: {e}")
        sys.exit(1)

    # Ask if they want a custom funder address (for proxy wallets)
    print()
    print("Polymarket uses a proxy wallet system. Your 'funder' address")
    print("is typically the address derived from your private key above.")
    if not confirm("Use derived address as funder?"):
        funder_address = prompt("Enter custom funder address (0x...)")

    return private_key, funder_address


def step_api_keys() -> tuple[str, str, str]:
    """Step 2: Configure CLOB API credentials."""
    print()
    print("-" * 40)
    print("Step 2: API Key Configuration")
    print("-" * 40)
    print()
    print("You need CLOB API credentials from Polymarket.")
    print("Generate them at: https://polymarket.com (Account Settings -> API Keys)")
    print()
    print("IMPORTANT: You must have made at least ONE manual trade on Polymarket")
    print("before API keys will work. This is a Polymarket requirement.")
    print()

    api_key = prompt("CLOB API Key")
    api_secret = prompt("CLOB API Secret", secret=True)
    api_passphrase = prompt("CLOB API Passphrase", secret=True)

    if not all([api_key, api_secret, api_passphrase]):
        print("WARNING: Missing API credentials. You can add them to .env later.")

    return api_key, api_secret, api_passphrase


def step_network() -> str:
    """Step 3: Configure Polygon RPC."""
    print()
    print("-" * 40)
    print("Step 3: Network Configuration")
    print("-" * 40)
    print()
    print("The bot needs a Polygon RPC endpoint.")
    print("The default public RPC works but can be slow/unreliable.")
    print("For production, consider Alchemy (free tier: https://alchemy.com)")
    print("or Infura (https://infura.io).")
    print()

    rpc_url = prompt("Polygon RPC URL", default="https://polygon-rpc.com")
    return rpc_url


def step_telegram() -> tuple[str, str]:
    """Step 4: Configure Telegram notifications (optional)."""
    print()
    print("-" * 40)
    print("Step 4: Telegram Notifications (Optional)")
    print("-" * 40)
    print()

    if not confirm("Set up Telegram notifications?", default=False):
        return "", ""

    print()
    print("To set up Telegram:")
    print("  1. Message @BotFather on Telegram")
    print("  2. Send /newbot and follow the prompts")
    print("  3. Copy the bot token")
    print("  4. Start a chat with your bot")
    print("  5. Send a message, then visit:")
    print("     https://api.telegram.org/bot<TOKEN>/getUpdates")
    print("     to find your chat_id")
    print()

    bot_token = prompt("Telegram Bot Token", secret=True)
    chat_id = prompt("Telegram Chat ID")

    return bot_token, chat_id


def step_verify_balance(private_key: str, rpc_url: str, funder_address: str) -> None:
    """Step 5: Verify wallet balance."""
    print()
    print("-" * 40)
    print("Step 5: Balance Verification")
    print("-" * 40)
    print()
    print("Checking your USDC balance on Polygon...")

    try:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            print(f"WARNING: Cannot connect to RPC at {rpc_url}")
            return

        # USDC contract on Polygon
        usdc_address = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
        # Minimal ERC20 ABI for balanceOf
        erc20_abi = [
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function",
            }
        ]
        usdc_contract = w3.eth.contract(address=usdc_address, abi=erc20_abi)
        raw_balance = usdc_contract.functions.balanceOf(
            Web3.to_checksum_address(funder_address)
        ).call()
        usdc_balance = raw_balance / 1_000_000  # 6 decimals

        # MATIC balance
        matic_balance = w3.eth.get_balance(Web3.to_checksum_address(funder_address))
        matic = w3.from_wei(matic_balance, "ether")

        print(f"\n  USDC Balance: ${usdc_balance:,.2f}")
        print(f"  MATIC Balance: {matic:.4f} MATIC")

        if usdc_balance < 10:
            print("\n  WARNING: Low USDC balance. You need USDC on Polygon to trade.")
            print("  Transfer USDC to your wallet on Polygon network.")

        if float(matic) < 0.01:
            print("\n  WARNING: Low MATIC balance. You need MATIC for gas fees.")
            print("  Get some MATIC from a faucet or exchange.")

    except ImportError:
        print("WARNING: web3 not installed. Run 'uv sync' first to install dependencies.")
    except Exception as e:
        print(f"WARNING: Could not verify balance: {e}")


def step_verify_api(
    api_key: str, api_secret: str, api_passphrase: str, funder_address: str
) -> None:
    """Step 6: Verify API connectivity."""
    print()
    print("-" * 40)
    print("Step 6: API Verification")
    print("-" * 40)
    print()

    if not all([api_key, api_secret, api_passphrase]):
        print("Skipping API verification (credentials not provided)")
        return

    print("Testing CLOB API connectivity...")

    try:
        from py_clob_client.client import ClobClient

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=api_key,
            secret=api_secret,
            passphrase=api_passphrase,
            signature_type=1,
            chain_id=137,
            funder=funder_address,
        )

        # Test by fetching open orders
        orders = client.get_orders()
        print(f"  API connected successfully!")
        print(f"  Open orders: {len(orders) if orders else 0}")

    except ImportError:
        print("WARNING: py-clob-client not installed. Run 'uv sync' first.")
    except Exception as e:
        print(f"WARNING: API verification failed: {e}")
        print("This may be normal if you haven't made a first trade on Polymarket yet.")


def step_write_env(
    private_key: str,
    funder_address: str,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    rpc_url: str,
    telegram_token: str,
    telegram_chat_id: str,
) -> None:
    """Step 7: Write .env file."""
    print()
    print("-" * 40)
    print("Step 7: Save Configuration")
    print("-" * 40)
    print()

    env_path = PROJECT_ROOT / ".env"

    if env_path.exists():
        if not confirm(f".env already exists at {env_path}. Overwrite?", default=False):
            print("Skipping .env write. You can edit it manually.")
            return

    env_content = f"""# Polymarket Trading Bot - Generated by setup wizard
# {"-" * 50}

# Polymarket CLOB API Credentials
POLYMARKET_API_KEY={api_key}
POLYMARKET_API_SECRET={api_secret}
POLYMARKET_API_PASSPHRASE={api_passphrase}

# Wallet
WALLET_PRIVATE_KEY={private_key}
FUNDER_ADDRESS={funder_address}

# Network
POLYGON_RPC_URL={rpc_url}

# Telegram (optional)
TELEGRAM_BOT_TOKEN={telegram_token}
TELEGRAM_CHAT_ID={telegram_chat_id}

# Trading mode: paper or live
TRADING_MODE=paper

# Logging
LOG_LEVEL=INFO

# Database
DATABASE_URL=sqlite:///data/polybot.db
"""

    env_path.write_text(env_content)
    print(f"Configuration saved to {env_path}")
    print()
    print("SECURITY REMINDER:")
    print("  - .env is in .gitignore and will NOT be committed")
    print("  - Never share your private key or API credentials")
    print("  - The bot starts in PAPER mode by default (no real trades)")


def step_summary(funder_address: str) -> None:
    """Final summary."""
    print()
    print("=" * 60)
    print("  Setup Complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. Install dependencies:  uv sync")
    print("  2. Start the bot:         uv run polybot")
    print("  3. Check status:          uv run polybot --status")
    print("  4. Enable live trading:   uv run polybot --live")
    print()
    print("Before going live, make sure you:")
    print(f"  - Have USDC in your wallet ({funder_address})")
    print("  - Have made at least one manual trade on Polymarket")
    print("  - Have configured strategies in config/strategies.yaml")
    print("  - Have added whale wallets to config/wallets.yaml")
    print()
    print("Read the docs: .planning/ROADMAP.md for the full feature roadmap")
    print()


def main() -> None:
    """Run the setup wizard."""
    print_banner()

    if not confirm("Ready to begin setup?"):
        print("Exiting.")
        sys.exit(0)

    # Step 1: Wallet
    private_key, funder_address = step_wallet_setup()

    # Step 2: API keys
    api_key, api_secret, api_passphrase = step_api_keys()

    # Step 3: Network
    rpc_url = step_network()

    # Step 4: Telegram
    telegram_token, telegram_chat_id = step_telegram()

    # Step 5: Verify balance
    step_verify_balance(private_key, rpc_url, funder_address)

    # Step 6: Verify API
    step_verify_api(api_key, api_secret, api_passphrase, funder_address)

    # Step 7: Write .env
    step_write_env(
        private_key,
        funder_address,
        api_key,
        api_secret,
        api_passphrase,
        rpc_url,
        telegram_token,
        telegram_chat_id,
    )

    # Summary
    step_summary(funder_address)


if __name__ == "__main__":
    main()
