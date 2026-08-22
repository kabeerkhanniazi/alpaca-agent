import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType, AssetStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

load_dotenv()
KEY, SEC = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")

trading = TradingClient(KEY, SEC, paper=True)
stocks = StockHistoricalDataClient(KEY, SEC)

# 1. Where is SPY right now?
trade = stocks.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols="SPY"))
spot = float(trade["SPY"].price)
print(f"SPY spot: ${spot:.2f}\n")

# 2. Puts expiring 7-14 days out, strikes 3-8% below spot
req = GetOptionContractsRequest(
    underlying_symbols=["SPY"],
    status=AssetStatus.ACTIVE,
    type=ContractType.PUT,
    expiration_date_gte=(datetime.now() + timedelta(days=7)).date(),
    expiration_date_lte=(datetime.now() + timedelta(days=14)).date(),
    strike_price_gte=str(round(spot * 0.92)),
    strike_price_lte=str(round(spot * 0.97)),
    limit=40,
)
contracts = trading.get_option_contracts(req).option_contracts

print(f"{'SYMBOL':<22} {'STRIKE':>8} {'EXPIRES':>12} {'% OTM':>7}")
print("-" * 52)
for c in sorted(contracts, key=lambda x: (x.expiration_date, float(x.strike_price))):
    strike = float(c.strike_price)
    otm = (spot - strike) / spot * 100
    print(f"{c.symbol:<22} {strike:>8.0f} {str(c.expiration_date):>12} {otm:>6.1f}%")
