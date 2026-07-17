"""عميل Binance Futures — بيانات عامة فقط (لا يحتاج مفاتيح API)."""
import time
import httpx
from typing import List, Optional, Dict

from .analyzer import Kline

BASE_URL = "https://fapi.binance.com"

_oi_history: Dict[str, list] = {}
OI_HISTORY_MAX_AGE_MS = 45 * 60 * 1000
OI_HISTORY_MAX_POINTS = 12


def fetch_klines(symbol: str, interval: str, limit: int = 150) -> List[Kline]:
    url = f"{BASE_URL}/fapi/v1/klines"
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(url, params={"symbol": symbol, "interval": interval, "limit": limit})
            r.raise_for_status()
            data = r.json()
            return [
                Kline(
                    open_time=int(item[0]),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                    close_time=int(item[6]),
                )
                for item in data
            ]
    except Exception:
        return []


def fetch_top_symbols(limit_count: int = 10) -> List[str]:
    url = f"{BASE_URL}/fapi/v1/ticker/24hr"
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
            candidates = []
            for obj in data:
                symbol = obj.get("symbol", "")
                if not symbol.endswith("USDT"):
                    continue
                quote_volume = float(obj.get("quoteVolume", 0) or 0)
                last_price = float(obj.get("lastPrice", 0) or 0)
                if quote_volume < 10_000_000.0:
                    continue
                if last_price < 0.0001:
                    continue
                candidates.append((symbol, quote_volume))
            candidates.sort(key=lambda x: x[1], reverse=True)
            result = [c[0] for c in candidates[:limit_count]]
            return result if result else _default_symbols()
    except Exception:
        return _default_symbols()


def fetch_all_prices() -> Dict[str, float]:
    url = f"{BASE_URL}/fapi/v1/ticker/price"
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
            return {obj["symbol"]: float(obj["price"]) for obj in data}
    except Exception:
        return {}


def _default_symbols():
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT"]


def fetch_open_interest_change_pct(symbol: str) -> Optional[float]:
    url = f"{BASE_URL}/fapi/v1/openInterest"
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(url, params={"symbol": symbol})
            r.raise_for_status()
            oi = float(r.json().get("openInterest", 0) or 0)
    except Exception:
        return None

    now = int(time.time() * 1000)
    history = _oi_history.setdefault(symbol, [])
    history[:] = [h for h in history if now - h[0] <= OI_HISTORY_MAX_AGE_MS]
    oldest = history[0] if history else None
    history.append((now, oi))
    if len(history) > OI_HISTORY_MAX_POINTS:
        history.pop(0)
    if oldest and oldest[1] > 0:
        return ((oi - oldest[1]) / oldest[1]) * 100.0
    return None


def fetch_funding_rate(symbol: str) -> Optional[float]:
    url = f"{BASE_URL}/fapi/v1/premiumIndex"
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(url, params={"symbol": symbol})
            r.raise_for_status()
            val = r.json().get("lastFundingRate")
            return float(val) if val is not None else None
    except Exception:
        return None


def fetch_order_book_imbalance(symbol: str, depth: int = 20) -> Optional[float]:
    url = f"{BASE_URL}/fapi/v1/depth"
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(url, params={"symbol": symbol, "limit": depth})
            r.raise_for_status()
            data = r.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            bid_qty = sum(float(b[1]) for b in bids)
            ask_qty = sum(float(a[1]) for a in asks)
            total = bid_qty + ask_qty
            return (bid_qty - ask_qty) / total if total > 0 else None
    except Exception:
        return None
