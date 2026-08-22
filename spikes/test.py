import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv()

client = TradingClient(
    os.getenv("ALPACA_API_KEY"),
    os.getenv("ALPACA_SECRET_KEY"),
    paper=True
)

account = client.get_account()
print(f"Account ID:    {account.id}")
print(f"Equity:        ${account.equity}")
print(f"Cash:          ${account.cash}")
print(f"Buying Power:  ${account.buying_power}")
print(f"Options Level: {account.options_trading_level}")
