from app.binance_client import fetch_klines
import httpx

data = fetch_klines("BTCUSDT", "1h", 100)
print("عدد الشموع من دالة البرنامج:", len(data))

r = httpx.get("https://fapi.binance.com/fapi/v1/klines", params={"symbol": "BTCUSDT", "interval": "1h", "limit": 100})
print("رمز الحالة المباشر:", r.status_code, "عدد الشموع:", len(r.json()))