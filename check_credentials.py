#!/usr/bin/env python3
"""Confirm the Alpaca CLI is installed and the credentials in .env authenticate.

Run this first when something stops working: it separates "the credentials are
wrong" from "the agent has a bug", which are otherwise easy to confuse.

    python check_credentials.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from options_agent.alpaca_cli import CLIError, cli_path
from options_agent.config import load_config


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")

    try:
        print(f"CLI binary:      {cli_path()}")
    except CLIError as exc:
        print(f"FAILED: {exc}")
        return 1

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001 — surfacing any config problem verbatim
        print(f"FAILED to load config: {exc}")
        return 1

    if config.credentials is None:
        print("FAILED: no credentials found. Copy .env-example to .env and fill it in.")
        return 1

    print(f"API key:         {config.credentials.api_key[:10]}...")
    print(f"Mode:            {'paper' if config.credentials.paper else 'LIVE'}")

    from options_agent.broker import Broker

    try:
        broker = Broker.from_config(config)
        account = broker.get_account()
        clock = broker.get_clock()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        return 1

    print(f"Account:         {account.account_number} ({account.status})")
    print(f"Equity:          ${account.equity}")
    print(f"Options level:   {account.options_trading_level}")
    print(f"Market open:     {clock.is_open}")
    print("OK — credentials authenticate through the CLI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
