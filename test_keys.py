import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

# Load from the explicit path
load_dotenv("/home/niazi/alpaca-agent/.env")

api_key = os.getenv("ALPACA_API_KEY")
api_secret = os.getenv("ALPACA_SECRET_KEY")

print(f"API Key loaded: {api_key[:10] if api_key else 'NOT FOUND'}...")
print(f"Secret loaded: {api_secret[:10] if api_secret else 'NOT FOUND'}...")

if not api_key or not api_secret:
    print("❌ ERROR: Keys not in .env")
    exit(1)

try:
    client = TradingClient(api_key, api_secret, paper=True)
    account = client.get_account()
    print(f"✅ SUCCESS: Authentication works!")
    print(f"✅ Account equity: ${account.equity}")
except Exception as e:
    print(f"❌ FAILED: {e}")
    exit(1)
