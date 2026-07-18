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
    return fetch_screened_symbols("top_volume", limit_count)


def fetch_screened_symbols(mode: str, limit_count: int = 10) -> List[str]:
    """يجيب قائمة عملات مصنَّفة حسب معيار مختار:
    - top_volume: الأعلى سيولة وحجم تداول (السلوك الافتراضي، كما هو)
    - big_movers: الأكبر تحركاً سعرياً خلال 24 ساعة (صعوداً أو هبوطاً)
    - high_funding: الأعلى تطرفاً بمعدل التمويل (إشارة ازدحام مراكز واحتمال انعكاس)
    - oi_spike: الأكبر قفزة بالفائدة المفتوحة (يحتاج دورتين فحص متتاليتين ليبدأ يعطي نتائج،
      لأنه يحتاج قياسين متباعدين زمنياً لحساب نسبة التغيّر)
    كل الأوضاع تلتزم بنفس حد السيولة الأدنى (10 مليون دولار) لتجنب عملات وهمية منخفضة السيولة."""
    if _is_banned():
        last_error["_top_symbols"] = get_ban_status()
        return _default_symbols()[:limit_count]

    url = f"{BASE_URL}/fapi/v1/ticker/24hr"
    r = _request("GET", url, None, "fetch_screened_symbols")
    if r is None:
        last_error["_top_symbols"] = get_ban_status() or "تعذر الاتصال بـ Binance لجلب قائمة العملات"
        return _default_symbols()[:limit_count]
    try:
        r.raise_for_status()
        data = r.json()
        liquid_pool = []
        for obj in data:
            symbol = obj.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            quote_volume = float(obj.get("quoteVolume", 0) or 0)
            last_price = float(obj.get("lastPrice", 0) or 0)
            if quote_volume < 10_000_000.0 or last_price < 0.0001:
                continue
            price_change_pct = float(obj.get("priceChangePercent", 0) or 0)
            liquid_pool.append({"symbol": symbol, "volume": quote_volume, "change_pct": price_change_pct})

        if not liquid_pool:
            last_error["_top_symbols"] = "لم يتم إيجاد عملات مطابقة لشروط السيولة"
            return _default_symbols()[:limit_count]

        if mode == "big_movers":
            liquid_pool.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
            result = [c["symbol"] for c in liquid_pool[:limit_count]]

        elif mode == "high_funding":
            # نأخذ أعلى 40 سيولة كمجمع مرشحين، ثم نعيد ترتيبهم حسب تطرف معدل التمويل
            liquid_pool.sort(key=lambda x: x["volume"], reverse=True)
            pool = liquid_pool[:40]
            funding_map = []
            for c in pool:
                fr = fetch_funding_rate(c["symbol"])
                if fr is not None:
                    funding_map.append((c["symbol"], abs(fr)))
                time.sleep(0.15)
            funding_map.sort(key=lambda x: x[1], reverse=True)
            result = [s for s, _ in funding_map[:limit_count]]

        elif mode == "oi_spike":
            liquid_pool.sort(key=lambda x: x["volume"], reverse=True)
            pool = liquid_pool[:40]
            oi_map = []
            for c in pool:
                change = fetch_open_interest_change_pct(c["symbol"])
                if change is not None:
                    oi_map.append((c["symbol"], abs(change)))
                time.sleep(0.15)
            if not oi_map:
                last_error["_top_symbols"] = "لسا يجمع بيانات القياس الأول للفائدة المفتوحة — النتائج ستكون جاهزة بالدورة القادمة"
                result = [c["symbol"] for c in liquid_pool[:limit_count]]  # مؤقتاً أعلى سيولة لحد ما تجهز البيانات
            else:
                oi_map.sort(key=lambda x: x[1], reverse=True)
                result = [s for s, _ in oi_map[:limit_count]]

        else:  # top_volume (افتراضي)
            liquid_pool.sort(key=lambda x: x["volume"], reverse=True)
            result = [c["symbol"] for c in liquid_pool[:limit_count]]

        if result:
            last_error.pop("_top_symbols", None)
            return result
        return _default_symbols()[:limit_count]
    except Exception as e:
        last_error["_top_symbols"] = str(e)
        return _default_symbols()[:limit_count]


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
    return [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT",
        "AVAXUSDT", "LINKUSDT", "TONUSDT", "SUIUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT",
        "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "MATICUSDT", "ATOMUSDT",
    ]


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


_cvd_history: Dict[str, list] = {}
CVD_HISTORY_MAX_AGE_MS = 24 * 60 * 60 * 1000  # نافذة تراكمية 24 ساعة
CVD_HISTORY_MAX_POINTS = 500  # سقف حماية من تضخم الذاكرة


def fetch_taker_pressure(symbol: str, limit: int = 100) -> Optional[float]:
    """ضغط المتداولين الفعليين (Taker Buy/Sell Pressure) — انظر التوثيق بعميل OKX
    لنفس المنطق. يعتمد على حقل isBuyerMaker: إذا True فالبائع هو من بادر بالصفقة
    (ضغط بيع)، وإذا False فالمشتري هو من بادر (ضغط شراء).
    كل استدعاء لهذي الدالة يغذّي أيضاً متتبع CVD التراكمي (انظر get_cvd_24h_pct)."""
    if _is_banned():
        return None
    url = f"{BASE_URL}/fapi/v1/trades"
    r = _request("GET", url, {"symbol": symbol, "limit": limit}, "fetch_taker_pressure")
    if r is None:
        return None
    try:
        r.raise_for_status()
        data = r.json()
        buy_vol = 0.0
        sell_vol = 0.0
        for t in data:
            qty = float(t.get("qty", 0) or 0)
            if t.get("isBuyerMaker"):
                sell_vol += qty  # البائع بادر بالصفقة = ضغط بيع
            else:
                buy_vol += qty  # المشتري بادر بالصفقة = ضغط شراء
        _record_cvd_sample(symbol, buy_vol, sell_vol)
        total = buy_vol + sell_vol
        return (buy_vol - sell_vol) / total if total > 0 else None
    except Exception:
        return None


def _record_cvd_sample(symbol: str, buy_vol: float, sell_vol: float):
    now = int(time.time() * 1000)
    history = _cvd_history.setdefault(symbol, [])
    history.append((now, buy_vol, sell_vol))
    cutoff = now - CVD_HISTORY_MAX_AGE_MS
    history[:] = [h for h in history if h[0] >= cutoff]
    if len(history) > CVD_HISTORY_MAX_POINTS:
        del history[: len(history) - CVD_HISTORY_MAX_POINTS]


def get_cvd_24h_pct(symbol: str, min_samples: int = 3) -> Optional[float]:
    """CVD تراكمي (Cumulative Volume Delta) على مدى 24 ساعة — نسبة هيمنة الشراء الفعلي
    من إجمالي حجم التداول المُعايَن. مبني على عيّنات دورية من صفقات فعلية (مو تخمين)،
    تتجمّع تلقائياً كل ما اشتغل الفحص. القيمة تصير أدق كل ما اشتغل البوت لفترة أطول
    (تحتاج 24 ساعة تشغيل متواصل لتغطية كاملة للنافذة الزمنية).
    القيمة: 0% = بيع كامل، 50% = تعادل، 100% = شراء كامل."""
    history = _cvd_history.get(symbol, [])
    now = int(time.time() * 1000)
    cutoff = now - CVD_HISTORY_MAX_AGE_MS
    recent = [h for h in history if h[0] >= cutoff]
    if len(recent) < min_samples:
        return None
    total_buy = sum(h[1] for h in recent)
    total_sell = sum(h[2] for h in recent)
    total = total_buy + total_sell
    if total <= 0:
        return None
    return (total_buy / total) * 100.0


def fetch_long_short_ratio(symbol: str) -> Optional[float]:
    """نسبة تمركز الحسابات (Long/Short Account Ratio) — انظر التوثيق بعميل OKX لنفس المنطق."""
    if _is_banned():
        return None
    url = f"{BASE_URL}/futures/data/globalLongShortAccountRatio"
    r = _request("GET", url, {"symbol": symbol, "period": "5m", "limit": 1}, "fetch_long_short_ratio")
    if r is None:
        return None
    try:
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[0].get("longShortRatio", 0) or 0)
        return None
    except Exception:
        return None
