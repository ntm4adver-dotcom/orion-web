"""محرك التعلم الذاتي (Coin & Strategy Learning) — نسخة واقعية وشفافة.

الفكرة: بدل ما نعد بنسبة نجاح ثابتة (وهذا مستحيل تحقيقه بأي سوق مالي حقيقي)،
النظام يراقب أداء كل عملة+اتجاه (Long/Short) **وأداء كل استراتيجية ككل** فعلياً من
الصفقات المغلقة (HIT_TP أو HIT_SL) المخزّنة بقاعدة البيانات، ويستخدم هذا السجل
الحقيقي للتأثير على قرار قبول/رفض الإشارة القادمة:

- سجل ضعيف مُثبت (نسبة نجاح منخفضة بعد عدد كافٍ من الصفقات) → يرفع الحد الأدنى
  المطلوب لقبول إشارة جديدة، فيصعب الدخول إلا بفرصة أقوى فعلاً.
- سجل قوي مُثبت → يخفف الحد الأدنى المطلوب قليلاً (لأنه أثبت جدارته بالفعل).
- بيانات غير كافية (أقل من الحد الأدنى من الصفقات) → لا تأثير، محايد تماماً.

مستوى العملة (Coin-level) ومستوى الاستراتيجية (Strategy-level) يشتغلون معاً بنفس
الوقت ويتراكم تأثيرهم — فلو استراتيجية معينة ضعيفة **ككل** بغض النظر عن العملة،
يرتفع حدها الأدنى تلقائياً حتى لو عملة معينة أداءها طبيعي، والعكس صحيح.

مهم: نسبة "الاحتمال %" المعروضة بكل إشارة هي **تقييم داخلي بمعادلة نقاط**، مو نسبة
نجاح مُختبرة تاريخياً. محرك التعلم هذا هو الآلية الوحيدة اللي تربط القرارات فعلياً
بنتائج حقيقية من السوق، وتصحح نفسها تلقائياً كل ما تراكمت صفقات مغلقة أكثر.
"""
from typing import Optional, Tuple

from . import db


def get_coin_adjustment(symbol: str, side: str, settings: dict) -> Tuple[int, Optional[str]]:
    """يرجع (مقدار التعديل على الحد الأدنى المطلوب، رسالة توضيحية أو None) بناءً على أداء العملة+الاتجاه تحديداً."""
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
        return 15, (f"🧠 [تعلم عملة] سجل {symbol} ({side}) ضعيف تاريخياً "
                     f"({win_rate:.0f}% من {perf['total']} صفقة مغلقة) — رُفع الحد الأدنى +15%.")
    if win_rate >= strong:
        return -5, (f"🧠 [تعلم عملة] سجل {symbol} ({side}) قوي تاريخياً "
                     f"({win_rate:.0f}% من {perf['total']} صفقة مغلقة) — خُفّف الحد الأدنى -5%.")
    return 0, None


def get_strategy_adjustment(strategy_key: Optional[str], settings: dict) -> Tuple[int, Optional[str]]:
    """يرجع (مقدار التعديل، رسالة أو None) بناءً على أداء الاستراتيجية **ككل** بغض النظر عن العملة —
    هذا يصحح تلقائياً استراتيجية ضعيفة الأداء عموماً، مو بس على عملة معينة."""
    if not settings.get("is_coin_learning_enabled", True) or not strategy_key:
        return 0, None

    all_perf = db.get_strategy_performance()
    perf = next((p for p in all_perf if p["strategy"] == strategy_key), None)
    min_trades = int(settings.get("strategy_learning_min_trades", 10))
    if not perf or perf["closed_total"] < min_trades:
        return 0, None

    win_rate = perf["win_rate"]
    weak = float(settings.get("strategy_learning_weak_threshold", 35))
    strong = float(settings.get("strategy_learning_strong_threshold", 70))

    if win_rate < weak:
        return 15, (f"🧠 [تعلم استراتيجية] أداء هذي الاستراتيجية ككل ضعيف تاريخياً "
                     f"({win_rate:.0f}% من {perf['closed_total']} صفقة مغلقة على كل العملات) — رُفع الحد الأدنى +15%.")
    if win_rate >= strong:
        return -5, (f"🧠 [تعلم استراتيجية] أداء هذي الاستراتيجية ككل قوي تاريخياً "
                     f"({win_rate:.0f}% من {perf['closed_total']} صفقة مغلقة على كل العملات) — خُفّف الحد الأدنى -5%.")
    return 0, None


def effective_threshold(symbol: str, side: str, settings: dict, strategy_key: Optional[str] = None) -> Tuple[int, Optional[str]]:
    """يرجع (الحد الأدنى الفعّال بعد التعديل المزدوج: عملة + استراتيجية، مقيّد بين 50% و95%)، ورسالة مدمجة."""
    base = int(settings.get("min_probability", 70))

    coin_adj, coin_msg = get_coin_adjustment(symbol, side, settings)
    strat_adj, strat_msg = get_strategy_adjustment(strategy_key, settings)

    effective = max(50, min(95, base + coin_adj + strat_adj))

    messages = [m for m in (coin_msg, strat_msg) if m]
    combined_msg = " | ".join(messages) if messages else None
    return effective, combined_msg
