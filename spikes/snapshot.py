import os
from dotenv import load_dotenv
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest

load_dotenv()
client = OptionHistoricalDataClient(
    os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
)

symbols = [
    "SPY260828P00744000",  # 3.0% OTM
    "SPY260828P00740000",  # 3.5%
    "SPY260828P00735000",  # 4.1%
    "SPY260828P00730000",  # 4.8%
    "SPY260828P00725000",  # 5.4%
    "SPY260828P00720000",  # 6.1%
    "SPY260828P00715000",  # 6.7%
    "SPY260828P00710000",  # 7.4%
]

snaps = client.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=symbols))

print(f"{'STRIKE':>7} {'BID':>7} {'ASK':>7} {'MID':>7} {'IV':>7} {'DELTA':>7} {'CREDIT':>8}")
print("-" * 60)

for sym in symbols:
    s = snaps.get(sym)
    if not s:
        continue
    strike = int(sym[-8:]) / 1000
    q = s.latest_quote
    bid = float(q.bid_price) if q else 0.0
    ask = float(q.ask_price) if q else 0.0
    mid = (bid + ask) / 2
    iv = f"{s.implied_volatility:.3f}" if s.implied_volatility else "  n/a"
    dlt = f"{s.greeks.delta:+.3f}" if s.greeks else "   n/a"
    print(f"{strike:>7.0f} {bid:>7.2f} {ask:>7.2f} {mid:>7.2f} {iv:>7} {dlt:>7} {mid*100:>7.0f}$")
