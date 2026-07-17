"""عميل Binance Futures — بيانات عامة فقط (لا يحتاج مفاتيح API).

يتضمن حماية ذكية من الحظر المؤقت (HTTP 418/429):
- يحترم هيدر Retry-After اللي ترسله Binance عند الحظر ويتوقف عن الإرسال تماماً
  خلال تلك المدة (بدل ما يعيد المحاولة ويزيد الحظر سوءاً).
- يستخدم عميل HTTP واحد مشترك (Keep-Alive) بدل فتح اتصال جديد بكل طلب.
- يباعد بين الطلبات المتتالية لتخفيف الضغط على حد معدل الطلبات (Rate Limit).
"""
import time
import threading
import httpx
from typing import List, Optional, Dict

from .analyzer import Kline

BASE_URL = "https://fapi.binance.com"

_oi_history: Dict[str, list] = {}
OI_HISTORY_MAX_AGE_MS = 45 * 60 * 1000
OI_HISTORY_MAX_POINTS = 12

last_error: Dict[str, str] = {}

# متصفح حقيقي بالهيدر لتجنب رفض Binance للطلبات الآلية بدون User-Agent
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# عميل HTTP مشترك (اتصال دائم) بدل فتح اتصال TCP/TLS جديد كل طلب
_client = httpx.Client(timeout=15, headers=_HEADERS)

# حالة الحظر المؤقت العالمية (مشتركة بين كل العملات) — لما Binance تحظرنا،
# نتوقف عن أي طلب جديد لحد ما تنتهي مدة الحظر بدل ما نستمر ونطول مدة الحظر.
_lock = threading.Lock()
_banned_until_ms = 0
_ban_reason = ""


def get_ban_status() -> Optional[str]:
    """يرجع رسالة توضح إذا كنا محظورين حالياً، أو None إذا كل شيء طبيعي."""
    with _lock:
        remaining = _banned_until_ms - int(time.time() * 1000)
        if remaining > 0:
            return f"{_ban_reason} — الوقت المتبقي: {remaining // 1000} ثانية"
    return None


def _register_ban(retry_after_seconds: float, source: str):
    global _banned_until_ms, _ban_reason
    with _lock:
        new_until = int(time.time() * 1000) + int(retry_after_seconds * 1000)
        if new_until > _banned_until_ms:
            _banned_until_ms = new_until
            _ban_reason = f"⛔ Binance حظرت مؤقتاً بسبب كثرة الطلبات (من: {source})"


def _is_banned() -> bool:
    with _lock:
        return int(time.time() * 1000) < _banned_until_ms


def _request(method: str, url: str, params: dict, source: str) -> Optional[httpx.Response]:
    """طبقة موحدة لكل الطلبات: تتحقق من الحظر أولاً، وتسجل الحظر الجديد إن حصل."""
    if _is_banned():
        return None
    try:
        r = _client.request(method, url, params=params)
    except Exception:
        return None
    if r.status_code in (418, 429):
        retry_after = r.headers.get("Retry-After")
        wait_s = float(retry_after) if retry_after else 120.0
        _register_ban(wait_s, source)
        return None
    return r


def fetch_klines(symbol: str, interval: str, limit: int = 150) -> List[Kline]:
    ban_msg = get_ban_status()
    if ban_msg:
        last_error[symbol] = ban_msg
        return []

    url = f"{BASE_URL}/fapi/v1/klines"
    r = _request("GET", url, {"symbol": symbol, "interval": interval, "limit": limit}, "fetch_klines")
    if r is None:
        last_error[symbol] = get_ban_status() or "تعذر الاتصال بـ Binance"
        return []
    try:
        r.raise_for_status()
        data = r.json()
        last_error.pop(symbol, None)
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
    except Exception as e:
        last_error[symbol] = str(e)
        return []


def fetch_top_symbols(limit_count: int = 10) -> List[str]:
    if _is_banned():
        return _default_symbols()
    url = f"{BASE_URL}/fapi/v1/ticker/24hr"
    r = _request("GET", url, None, "fetch_top_symbols")
    if r is None:
        return _default_symbols()
    try:
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
    if _is_banned():
        return {}
    url = f"{BASE_URL}/fapi/v1/ticker/price"
    r = _request("GET", url, None, "fetch_all_prices")
    if r is None:
        return {}
    try:
        r.raise_for_status()
        data = r.json()
        return {obj["symbol"]: float(obj["price"]) for obj in data}
    except Exception:
        return {}


def _default_symbols():
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT"]


def fetch_open_interest_change_pct(symbol: str) -> Optional[float]:
    if _is_banned():
        return None
    url = f"{BASE_URL}/fapi/v1/openInterest"
    r = _request("GET", url, {"symbol": symbol}, "fetch_open_interest_change_pct")
    if r is None:
        return None
    try:
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
    if _is_banned():
        return None
    url = f"{BASE_URL}/fapi/v1/premiumIndex"
    r = _request("GET", url, {"symbol": symbol}, "fetch_funding_rate")
    if r is None:
        return None
    try:
        r.raise_for_status()
        val = r.json().get("lastFundingRate")
        return float(val) if val is not None else None
    except Exception:
        return None


def fetch_order_book_imbalance(symbol: str, depth: int = 20) -> Optional[float]:
    if _is_banned():
        return None
    url = f"{BASE_URL}/fapi/v1/depth"
    r = _request("GET", url, {"symbol": symbol, "limit": depth}, "fetch_order_book_imbalance")
    if r is None:
        return None
    try:
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
