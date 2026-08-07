"""
بيانات سوق خارجية عامة (مو خاصة بمنصة OKX) — مصدرها مُجمِّعات بيانات مستقلة
(CoinGecko حالياً). أول مؤشر: **استحواذ البيتكوين (BTC Dominance)**.

الفكرة: استراتيجيات التطبيق كلها تقريباً تتداول ألتكوينز، مو البيتكوين نفسه.
استحواذ البيتكوين يوضح "وين رأس المال يتدفق فعلياً" داخل سوق الكريبتو:
  - ارتفاع الاستحواذ = تدفق من الألتكوينز للبيتكوين → الألتكوينز أضعف نسبياً
    (حتى لو السوق العام "صاعد" بالقيمة الإجمالية)
  - انخفاض الاستحواذ = "موسم ألتكوينز" → الألتكوينز أقوى نسبياً من البيتكوين
"""
import time
from typing import Optional, Dict

try:
    import httpx
except ImportError:
    httpx = None

_dominance_history: list = []  # [(timestamp_ms, dominance_pct), ...]
DOMINANCE_HISTORY_MAX_AGE_MS = 6 * 60 * 60 * 1000  # نافذة 6 ساعات كافية لقياس اتجاه واضح
DOMINANCE_HISTORY_MAX_POINTS = 80

last_error: Dict[str, str] = {}


def fetch_btc_dominance_pct() -> Optional[float]:
    """يجيب النسبة الحالية لاستحواذ البيتكوين من CoinGecko (مجاني، بدون مفتاح API
    للاستخدام الأساسي). يسجّل القيمة بأرشيف محلي تلقائياً لحساب الاتجاه لاحقاً."""
    if httpx is None:
        return None
    error_key = "btc_dominance"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get("https://api.coingecko.com/api/v3/global")
        if resp.status_code != 200:
            last_error[error_key] = f"CoinGecko رجع حالة {resp.status_code}"
            return None
        data = resp.json()
        dominance = float(data["data"]["market_cap_percentage"]["btc"])
        last_error.pop(error_key, None)
        now = int(time.time() * 1000)
        _dominance_history.append((now, dominance))
        cutoff = now - DOMINANCE_HISTORY_MAX_AGE_MS
        _dominance_history[:] = [h for h in _dominance_history if h[0] >= cutoff]
        if len(_dominance_history) > DOMINANCE_HISTORY_MAX_POINTS:
            del _dominance_history[: len(_dominance_history) - DOMINANCE_HISTORY_MAX_POINTS]
        return dominance
    except Exception as e:
        last_error[error_key] = f"خطأ أثناء جلب استحواذ البيتكوين: {type(e).__name__}: {e}"
        return None


def get_btc_dominance_trend(min_points: int = 3) -> Optional[dict]:
    """يحسب اتجاه استحواذ البيتكوين خلال النافذة المحفوظة (حتى 6 ساعات) —
    يرجع {current, change_pct, trend} حيث trend = "صاعد" (استحواذ يرتفع =
    ضغط سلبي على الألتكوينز عموماً) أو "هابط" (استحواذ ينخفض = بيئة داعمة
    للألتكوينز) أو "مستقر" (تغيّر ضئيل جداً)."""
    if len(_dominance_history) < min_points:
        return None
    oldest = _dominance_history[0][1]
    current = _dominance_history[-1][1]
    change_pct = current - oldest  # فرق بالنقاط المئوية (مو نسبة مئوية للتغيّر)
    if abs(change_pct) < 0.15:
        trend = "مستقر"
    elif change_pct > 0:
        trend = "صاعد"
    else:
        trend = "هابط"
    return {"current": current, "change_pct": change_pct, "trend": trend}
