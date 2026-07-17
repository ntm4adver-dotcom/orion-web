"""محرك التعلم الذاتي (Coin Learning) — نسخة واقعية وشفافة.

الفكرة: بدل ما نعد بنسبة نجاح ثابتة (وهذا مستحيل تحقيقه بأي سوق مالي حقيقي)،
النظام يراقب أداء كل عملة+اتجاه (Long/Short) فعلياً من الصفقات المغلقة
(HIT_TP أو HIT_SL) المخزّنة بقاعدة البيانات، ويستخدم هذا السجل الحقيقي
للتأثير على قرار قبول/رفض الإشارة القادمة لنفس العملة ونفس الاتجاه:

- سجل ضعيف مُثبت (نسبة نجاح منخفضة بعد عدد كافٍ من الصفقات) → يرفع الحد الأدنى
  المطلوب لقبول إشارة جديدة على هذه العملة، فيصعب الدخول فيها إلا بفرصة أقوى فعلاً.
- سجل قوي مُثبت → يخفف الحد الأدنى المطلوب قليلاً (لأنه أثبت جدارته بالفعل).
- بيانات غير كافية (أقل من الحد الأدنى من الصفقات) → لا تأثير، محايد تماماً.

هذا تحسين تدريجي حقيقي وقابل للقياس، وليس وعداً بنسبة نجاح معينة.
"""
from typing import Optional, Tuple

from . import db


def get_probability_adjustment(symbol: str, side: str, settings: dict) -> Tuple[int, Optional[str]]:
    """يرجع (مقدار التعديل على الحد الأدنى المطلوب، رسالة توضيحية أو None)."""
    if not settings.get("is_coin_learning_enabled", True):
        return 0, None

    perf = db.get_coin_performance_for(symbol, side)
    min_trades = int(settings.get("coin_learning_min_trades", 5))
    if not perf or perf["total"] < min_trades:
        return 0, None

    win_rate = perf["win_rate"]
    weak = float(settings.get("coin_learning_weak_threshold", 35))
    strong = float(settings.get("coin_learning_strong_threshold", 70))

    if win_rate < weak:
        return 15, (f"🧠 [تعلم ذاتي] سجل {symbol} ({side}) ضعيف تاريخياً "
                     f"({win_rate:.0f}% من {perf['total']} صفقة مغلقة) — تم رفع الحد الأدنى المطلوب مؤقتاً +15%.")
    if win_rate >= strong:
        return -5, (f"🧠 [تعلم ذاتي] سجل {symbol} ({side}) قوي تاريخياً "
                     f"({win_rate:.0f}% من {perf['total']} صفقة مغلقة) — تم تخفيف الحد الأدنى المطلوب -5%.")
    return 0, None


def effective_threshold(symbol: str, side: str, settings: dict) -> Tuple[int, Optional[str]]:
    """يرجع (الحد الأدنى الفعّال بعد التعديل، رسالة توضيحية أو None)، مقيّد بين 50% و95%."""
    base = int(settings.get("min_probability", 70))
    adjustment, msg = get_probability_adjustment(symbol, side, settings)
    effective = max(50, min(95, base + adjustment))
    return effective, msg
