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


def get_coin_strategy_adjustment(symbol: str, strategy_key: Optional[str], settings: dict) -> Tuple[int, Optional[str]]:
    """🆕 يرجع (مقدار التعديل، رسالة أو None) بناءً على أداء "هذي الاستراتيجية
    بالذات على هذي العملة بالذات" — يسد فجوة حقيقية بين تعلّم العملة عموماً
    وتعلّم الاستراتيجية عموماً (مثال حقيقي اكتُشف بالباك تيست: صيد الاستوبات قوية
    جداً على SOL/ADA، ضعيفة تحديداً على XRP — تركيبة محددة، مو خاصية عامة).
    وزن أعلى من التعديلين العامين (±20 بدل ±15/±5) لأنه أدق تحديداً وأكثر دلالة."""
    if not settings.get("is_coin_learning_enabled", True) or not strategy_key:
        return 0, None

    perf = db.get_coin_strategy_performance_for(symbol, strategy_key)
    min_trades = int(settings.get("coin_strategy_learning_min_trades", 8))
    if not perf or perf["total"] < min_trades:
        return 0, None

    win_rate = perf["win_rate"]
    weak = float(settings.get("coin_learning_weak_threshold", 35))
    strong = float(settings.get("coin_learning_strong_threshold", 70))

    if win_rate < weak:
        return 20, (f"🎯 [تعلم تركيبة] {strategy_key} على {symbol} تحديداً سجلها ضعيف جداً "
                     f"({win_rate:.0f}% من {perf['total']} صفقة) — رُفع الحد الأدنى +20% (أقوى تأثير، الأدق تحديداً).")
    if win_rate >= strong:
        return -8, (f"🎯 [تعلم تركيبة] {strategy_key} على {symbol} تحديداً سجلها قوي جداً "
                     f"({win_rate:.0f}% من {perf['total']} صفقة) — خُفّف الحد الأدنى -8%.")
    return 0, None


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


def get_coin_overall_adjustment(symbol: str, settings: dict) -> Tuple[int, Optional[str]]:
    """🆕 يرجع (مقدار التعديل، رسالة أو None) بناءً على أداء العملة **الكلي** —
    بدون تجزئة حسب الاتجاه أو الاستراتيجية. يسد فجوة حقيقية اكتُشفت بالملاحظة:
    عملة ممكن تفشل بكل الاتجاهات وكل الاستراتيجيات المجرَّبة عليها، لكن كل
    مقياس مقسَّم (عملة+اتجاه، عملة+استراتيجية) لحاله ما يوصل الحد الأدنى
    ليفعّل، فالنمط الحقيقي (هذي العملة نفسها سيئة — تذبذب مفرط، سيولة ضعيفة،
    سلوك سعري شاذ) يضيع. وزن أعلى من التعديلات التانية لأنه أشمل دليل ممكن —
    فشل بكل الاتجاهات وكل الاستراتيجيات مو صدفة."""
    if not settings.get("is_coin_learning_enabled", True):
        return 0, None

    perf = db.get_coin_overall_performance(symbol)
    min_trades = int(settings.get("coin_overall_learning_min_trades", 6))
    if not perf or perf["total"] < min_trades:
        return 0, None

    win_rate = perf["win_rate"]
    weak = float(settings.get("coin_learning_weak_threshold", 35))
    strong = float(settings.get("coin_learning_strong_threshold", 70))

    if win_rate < weak:
        return 25, (f"🚨 [تعلم عملة كلي] {symbol} سجلها ضعيف جداً على **كل** الاتجاهات والاستراتيجيات معاً "
                     f"({win_rate:.0f}% من {perf['total']} صفقة) — رُفع الحد الأدنى +25% (أقوى تأثير، أشمل دليل).")
    if win_rate >= strong:
        return -8, (f"🚨 [تعلم عملة كلي] {symbol} سجلها قوي جداً على كل الاتجاهات والاستراتيجيات معاً "
                     f"({win_rate:.0f}% من {perf['total']} صفقة) — خُفّف الحد الأدنى -8%.")
    return 0, None


def is_coin_blocked(symbol: str, settings: dict) -> Optional[str]:
    """🆕 رفض قاطع (مو بس رفع حد أدنى) لعملة سجلها الكلي كارثي — نسبة نجاح أقل
    من نصف الحد الضعيف (weak/2) بعد عدد صفقات كافٍ. الفرق عن get_coin_overall_adjustment:
    ذاك يرفع صعوبة الدخول، هذا يمنعه كليّاً لو الدليل ساحق بما يكفي إنه بغض
    النظر عن الاستراتيجية أو الاتجاه، هذي العملة تخسر بثبات."""
    if not settings.get("is_coin_learning_enabled", True) or not settings.get("is_coin_hard_block_enabled", False):
        return None
    perf = db.get_coin_overall_performance(symbol)
    min_trades = int(settings.get("coin_overall_learning_min_trades", 6))
    if not perf or perf["total"] < min_trades:
        return None
    weak = float(settings.get("coin_learning_weak_threshold", 35))
    catastrophic = weak / 2.0
    if perf["win_rate"] < catastrophic:
        return (f"🚨 {symbol} مرفوضة كليّاً — سجلها الكلي كارثي ({perf['win_rate']:.0f}% من "
                f"{perf['total']} صفقة على كل الاتجاهات والاستراتيجيات) — دليل ساحق إن العملة نفسها "
                f"غير قابلة للتداول حالياً بغض النظر عن الطريقة")
    return None


def effective_threshold(symbol: str, side: str, settings: dict, strategy_key: Optional[str] = None) -> Tuple[int, Optional[str]]:
    """يرجع (الحد الأدنى الفعّال بعد التعديل الرباعي: عملة + اتجاه + استراتيجية +
    تركيبة عملة×استراتيجية + أداء العملة الكلي معاً، مقيّد بين 50% و95%)، ورسالة مدمجة."""
    base = int(settings.get("min_probability", 70))

    coin_adj, coin_msg = get_coin_adjustment(symbol, side, settings)
    strat_adj, strat_msg = get_strategy_adjustment(strategy_key, settings)
    combo_adj, combo_msg = get_coin_strategy_adjustment(symbol, strategy_key, settings)
    overall_adj, overall_msg = get_coin_overall_adjustment(symbol, settings)

    effective = max(50, min(95, base + coin_adj + strat_adj + combo_adj + overall_adj))

    messages = [m for m in (coin_msg, strat_msg, combo_msg, overall_msg) if m]
    combined_msg = " | ".join(messages) if messages else None
    return effective, combined_msg
