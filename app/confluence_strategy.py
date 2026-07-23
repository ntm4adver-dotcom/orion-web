"""
استراتيجية التوافق (Confluence Strategy) — تصميم أصيل بطلب صريح.

الفكرة: بدل ما تكون استراتيجية جديدة بمنطقها الخاص، هذي "استراتيجية اجتماع" —
تشغّل **كل** الاستراتيجيات الثمانية المسجّلة بالنظام على نفس العملة بنفس اللحظة،
وما تولّد إشارة إلا لو **اتفقت استراتيجيتان أو أكثر** على نفس الاتجاه (Long/Short)
لنفس العملة بنفس الوقت. الفرضية: لو أكثر من منهج تحليلي مستقل توصّل لنفس النتيجة
بنفس اللحظة، هذا تأكيد أقوى بكثير من أي استراتيجية منفردة.

توزيع النقاط: 100 نقطة تُقسم بالتساوي على **كل الاستراتيجيات الثمانية** (12.5 نقطة
لكل وحدة)، وأي استراتيجية توافقت على نفس الاتجاه "تكسب" نقاطها. النقطة الإجمالية
المعروضة = مجموع نقاط الاستراتيجيات المتوافقة فقط.

نقطة الدخول والوقف: **الأفضل (الأدق) من بين كل الاستراتيجيات المتوافقة** — نقارن
مسافة كل وقف عن سعر الدخول (نسبة مئوية) ونختار أضيقها (الأكثر دقة/تحفظاً)، ونفس
الشيء لنقطة الدخول (الأقرب لسعر السوق الحالي من ناحية منطقية سليمة).

الهدف: عائد/مخاطرة لا يقل عن 1:5 دائماً (بغض النظر عن أهداف الاستراتيجيات
الفردية المتوافقة) — بطلب صريح، مفروض حقيقياً بدون تحايل، يُرفض لو ما تحقق.
"""
from typing import Optional

from .analyzer import AnalysisResult, MarketMicrostructure, build_score_breakdown


def _get_member_strategies():
    """يرجع كل الاستراتيجيات المسجّلة بالسجل المركزي عدا التوافق نفسه — ديناميكي
    بالكامل، أي استراتيجية جديدة تُضاف للسجل تدخل هنا تلقائياً بدون أي تعديل.
    الاستيراد هنا (داخل الدالة، مو أعلى الملف) مقصود — يكسر حلقة الاستيراد
    الدائري بين هذا الملف وملف strategies.py اللي يسجّله."""
    from . import strategies as _strategies_module
    return [(k, v["fn"], v["label"]) for k, v in _strategies_module.STRATEGY_REGISTRY.items()
            if k != "confluence"]


def analyze_confluence(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                        micro: Optional[MarketMicrostructure] = None,
                        trace: Optional[list] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    members = _get_member_strategies()
    n_members = len(members)
    if n_members < 2:
        return None
    points_each = round(100.0 / n_members, 2)

    long_agreements = []   # [(key, label, AnalysisResult), ...]
    short_agreements = []

    for key, fn, label in members:
        try:
            sub_trace: list = []
            result = fn(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro, trace=sub_trace)
        except Exception:
            result = None
        if result is None:
            _log(f"▫️ {label}", "لم تولّد إشارة", None)
            continue
        _log(f"✅ {label}", f"{result.side} (احتمالية {result.prob}%)", True)
        if result.side == "Long":
            long_agreements.append((key, label, result))
        elif result.side == "Short":
            short_agreements.append((key, label, result))

    if len(long_agreements) >= 2:
        agreements, side = long_agreements, "Long"
    elif len(short_agreements) >= 2:
        agreements, side = short_agreements, "Short"
    else:
        _log("❌ القرار النهائي", f"ما فيه توافق كافٍ (استراتيجيتين+ بنفس الاتجاه بنفس الوقت) — "
             f"Long: {len(long_agreements)}, Short: {len(short_agreements)} — رفض", False)
        return None

    _log(f"✅ توافق مكتشف ({side})", f"{len(agreements)} استراتيجية متفقة: " + ", ".join(a[1] for a in agreements), True)

    # نقطة الدخول: الأقرب منطقياً لسعر السوق الحالي (الأكثر واقعية للتنفيذ) من بين المتوافقين
    current_price = k5m[-1].close if k5m else agreements[0][2].price
    best_entry_candidate = min(agreements, key=lambda a: abs(a[2].entry_price - current_price))
    entry_price = best_entry_candidate[2].entry_price

    # الوقف: الأضيق (الأكثر دقة/تحفظاً) من بين وقفات المتوافقين، بشرط يبقى بالاتجاه الصحيح عن الدخول
    def _risk_pct(a):
        return abs(a[2].stop_loss - entry_price) / entry_price if entry_price else 999
    best_stop_candidate = min(agreements, key=_risk_pct)
    stop_loss = best_stop_candidate[2].stop_loss

    # تصحيح: الوقف لازم يكون بالاتجاه الصحيح عن نقطة الدخول المختارة (قد تختلف عن
    # الاستراتيجية الأصلية اللي جابت هذا الوقف بالذات)
    if side == "Long" and stop_loss >= entry_price:
        stop_loss = entry_price * 0.99
    if side == "Short" and stop_loss <= entry_price:
        stop_loss = entry_price * 1.01

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None

    # الهدف: عائد/مخاطرة لا يقل عن 1:5 دائماً، بطلب صريح — مفروض حقيقياً
    if side == "Long":
        take_profit = entry_price + risk * 5.0
    else:
        take_profit = entry_price - risk * 5.0

    reward = abs(take_profit - entry_price)
    rr = round(reward / risk, 2)
    _log("عائد/مخاطرة (مفروض 1:5 كحد أدنى دائماً)", f"1:{rr}")
    if rr < 5.0:
        _log("❌ فلتر أدنى عائد/مخاطرة (1:5)", f"1:{rr} غير كافٍ — رفض", False)
        return None

    # النقاط: كل استراتيجية متوافقة تكسب نصيبها الكامل من الـ100 نقطة الموزّعة على الجميع
    agreed_keys = {a[0] for a in agreements}
    score_factors = [(label, key in agreed_keys) for key, _, label in members]
    score_breakdown, signal_score = build_score_breakdown(score_factors)

    avg_prob = round(sum(a[2].prob for a in agreements) / len(agreements))
    probability = min(96, avg_prob + (len(agreements) - 2) * 3)  # مكافأة إضافية لكل استراتيجية زايدة عن الحد الأدنى (2)

    strategies_txt = "، ".join(f"{a[1]} ({a[2].side}, {a[2].prob}%)" for a in agreements)
    behavior = (
        f"🤝 استراتيجية التوافق: اتفقت {len(agreements)} استراتيجية مستقلة على نفس الاتجاه "
        f"({side}) بنفس اللحظة: {strategies_txt}. نقطة الدخول والوقف من الأدق بينهم، والهدف "
        f"عائد/مخاطرة 1:5 مفروض. قوة التوافق: {signal_score}/100 نقطة "
        f"({len(agreements)} من أصل {n_members} استراتيجية)."
    )
    volume_analysis = f"توافق {len(agreements)}/{n_members} استراتيجية مستقلة على نفس الاتجاه بنفس اللحظة"

    return AnalysisResult(
        symbol=symbol, trend=("صاعد" if side == "Long" else "هابط"), dt="", prob=probability,
        price=current_price, atr=agreements[0][2].atr, side=side, entry_price=entry_price,
        stop_loss=stop_loss, take_profit=take_profit, rr=rr,
        quality="A" if signal_score >= 37.5 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
        score_breakdown=score_breakdown, signal_score=signal_score,
    )
