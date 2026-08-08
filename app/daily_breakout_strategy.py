"""
استراتيجية اختراق قمة/قاع اليوم السابق (Previous Day High/Low Breakout).

🆕 استراتيجية جديدة (بطلب صريح) — تحل محل استراتيجية "التوافق" (Confluence) اللي
أُزيلت من السجل.

الفكرة: قمة وقاع اليوم السابق (Previous Day High/Low) من أكثر المستويات اللي
يتفاعل معها السوق فعلياً — كثير من المتداولين المؤسساتيين يستخدمونها كمرجع
يومي، وكسرها بقوة (مو بفتيلة رفض زي صيد الاستوبات، بل بإغلاق شمعة كامل) غالباً
يعني استمرار حقيقي بنفس الاتجاه، مو فخ سيولة.

الشروط:
  1. **كسر مؤكَّد بإغلاق شمعة 5 دقائق كاملة** (مو فتيلة بس) فوق قمة اليوم
     السابق (Long) أو تحت قاع اليوم السابق (Short) — نستخدم آخر شمعة 5 دقائق
     *مكتملة فعلياً* (نستبعد الشمعة الأخيرة لأنها لسا قيد التكوين).
  2. **فوليوم مؤكِّد** على شمعة الاختراق (أعلى من متوسط آخر 20 شمعة بمعامل
     كافٍ) — يميّز اختراق حقيقي عن حركة عادية عابرة للمستوى.
  3. **شكل شمعة حاسم**: جسم قوي (مو دوجي/تردد) وإغلاق قريب من طرف الشمعة
     بنفس اتجاه الاختراق (مو ذيل رفض يشبه سحب سيولة).
  4. **تأكيدات مساعدة اختيارية** (تزيد الاحتمالية لو توفرت، ما ترفض الصفقة
     لو غابت): ضغط المتداولين الفعليين (Taker Pressure)، الفائدة المفتوحة
     (OI)، CVD تراكمي، ضغط الصفقات الكبيرة (Order Flow) — نفس أسلوب بقية
     استراتيجيات التطبيق.
  5. **رفض فوري** لو النمط أقرب لفخ اختراق (Fakeout) أو انعكاس فوري بعد سحب
     سيولة (نفس الفلترين المستخدَمين بالانفجار السعري).

نقطة الدخول: عند مستوى الاختراق نفسه (قمة/قاع اليوم السابق) — دخول محدد
(Limit) بانتظار إعادة اختبار المستوى، مو مطاردة السعر لحظياً بعد الاختراق —
لتجنب الدخول وسط سحب سيولة أو ارتداد مؤقت (فخ اختراق قصير).
"""
from typing import List, Optional

from .analyzer import (
    Kline, AnalysisResult, MarketMicrostructure, atr, build_score_breakdown,
    structural_stop_loss, detect_fakeout_rejection, detect_immediate_reversal_after_sweep,
    low_vol, in_kill_zone, check_irrational_market, daily_trend, _hma_bias_pair,
    find_previous_day_high_low,
)


def analyze_daily_breakout(
    symbol: str,
    k4h: List[Kline],
    k1h: List[Kline],
    k15m: List[Kline],
    k5m: List[Kline],
    k_daily: List[Kline],
    min_rr_floor: float = 3.0,
    micro: Optional[MarketMicrostructure] = None,
    trace: Optional[list] = None,
    current_price: Optional[float] = None,
    **kwargs,
) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    if len(k_daily) < 2:
        _log("عدد الشموع اليومية كافٍ (يحتاج ≥2: اليوم + أمس)", len(k_daily), False)
        return None
    if len(k5m) < 25:
        _log("عدد شموع 5 دقائق كافٍ", len(k5m), False)
        return None

    # 🔴 إصلاح أدق (بطلب صريح): بدل الاعتماد على موقع بالمصفوفة (index) اللي
    # يعتمد على افتراض هش (هل k_daily مُشذَّبة أو خام) — وهذا بالضبط سبب الباق
    # اللي اكتشفناه قبل شوي — نطابق **التاريخ الفعلي** مباشرة. النتيجة صحيحة
    # دايماً بغض النظر عن حالة المصفوفة أو طولها.
    prev_hl = find_previous_day_high_low(k_daily)
    if prev_hl is None:
        _log("قمة/قاع اليوم السابق (مطابقة تاريخ فعلي)", "ما لقينا شمعة يومية سابقة مؤكَّدة — رفض", False)
        return None
    prev_high, prev_low = prev_hl
    _log("قمة اليوم السابق / قاعه (مطابقة تاريخ فعلي)", f"{prev_high:.6g} / {prev_low:.6g}")

    # آخر شمعة 5 دقائق *مكتملة فعلياً* — نستبعد الشمعة الأخيرة (قيد التكوين)
    closed = k5m[-2]
    prior = k5m[:-2]
    if len(prior) < 21:
        _log("عدد الشموع السابقة كافٍ لحساب متوسط الفوليوم", len(prior), False)
        return None

    # 🔴 إصلاح خطأ حقيقي (بطلب صريح): كان الكود يتحقق بس "هل آخر شمعة مكتملة
    # فوق/تحت المستوى؟" — بدون تأكد إنها *أول* شمعة تكسره. لو السعر كسر
    # المستوى من ساعات وضل فوقه/تحته، كل دورة فحص كانت الشمعة الأخيرة تحقق
    # الشرط برضه، فتطلع صفقة متأخرة بساعات عن لحظة الاختراق الفعلية. الحل:
    # نتأكد إن الشمعة اللي *قبل* شمعة الاختراق المرشحة لم تكن هي نفسها كاسرة
    # للمستوى أصلاً — يعني "closed" فعلاً أول شمعة انتقال (Transition Candle)،
    # مو مجرد شمعة عشوائية لاحقة السعر فيها لسا فوق/تحت مستوى قديم.
    prev_closed = prior[-1]
    already_broken_up = prev_closed.close > prev_high
    already_broken_down = prev_closed.close < prev_low

    last_price = current_price if current_price is not None else closed.close
    if last_price <= 0.0:
        return None

    avg_vol20 = sum(k.volume for k in prior[-20:]) / 20
    vol_ratio = (closed.volume / avg_vol20) if avg_vol20 > 0 else 1.0

    rng = closed.high - closed.low
    body = abs(closed.close - closed.open)
    body_ratio = (body / rng) if rng > 0 else 0.0
    closes_near_high = rng > 0 and (closed.high - closed.close) / rng < 0.25
    closes_near_low = rng > 0 and (closed.close - closed.low) / rng < 0.25

    side = ""
    if closed.close > prev_high and not already_broken_up and body_ratio > 0.5 and closes_near_high:
        side = "Long"
    elif closed.close < prev_low and not already_broken_down and body_ratio > 0.5 and closes_near_low:
        side = "Short"

    _log("إغلاق شمعة 5د مكتملة فوق/تحت المستوى", f"إغلاق={closed.close:.6g}", bool(side))
    _log("هذي أول شمعة تكسر المستوى (مو اختراق قديم)", f"مكسور مسبقاً فوق={already_broken_up}, تحت={already_broken_down}", not (already_broken_up or already_broken_down))
    _log("جسم شمعة الاختراق قوي وحاسم (>50% من المدى)", round(body_ratio, 2), body_ratio > 0.5)

    if not side:
        _log("❌ القرار النهائي", "ما فيه إغلاق شمعة 5د كاملة مؤكِّد كسر قمة/قاع اليوم السابق بشكل حاسم لأول مرة", False)
        return None

    min_vol_ratio = 1.8
    if vol_ratio < min_vol_ratio:
        _log("❌ فلتر فوليوم الاختراق", f"{vol_ratio:.2f}x أقل من الحد الأدنى {min_vol_ratio}x — رفض", False)
        return None
    _log("✅ فوليوم شمعة الاختراق", f"{vol_ratio:.2f}x المتوسط", True)

    # نفس فلتري الحماية المستخدَمين بالانفجار السعري: يرفضون بالضبط النمط اللي
    # يشبه اختراق حقيقي ظاهرياً لكنه فخ سيولة أو انعكاس فوري بعد سحب
    if detect_fakeout_rejection(k5m, side, lookback=3):
        _log("❌ فلتر فخ الاختراق (Fakeout Rejection)", "اكتُشف نمط فخ اختراق حديث — رفض", False)
        return None
    if detect_immediate_reversal_after_sweep(k5m, side):
        _log("❌ فلتر الانعكاس الفوري بعد السحب", "اكتُشف انعكاس فوري بعد سحب سيولة — رفض", False)
        return None

    atr5m = atr(k5m, 14)
    if atr5m <= 0:
        return None

    # 🔴 تعديل جوهري (بطلب صريح): الدخول اللحظي عند current_price كان يخلي
    # الصفقة تدخل بعد ما السعر يكون تحرك فعلاً — وهذا بالضبط الوقت اللي ممكن
    # يصير فيه سحب سيولة أو ارتداد مؤقت (فخ اختراق قصير) بعد الكسر مباشرة.
    # البديل (نفس منطق explosive_breakout المستخدم بالتطبيق أصلاً): دخول محدد
    # (Limit) عند مستوى الاختراق نفسه (قمة/قاع اليوم السابق) — بانتظار إعادة
    # اختبار المستوى، نقطة أدق ومخاطرة أقل من مطاردة السعر مباشرة بعد الكسر.
    entry_price = prev_high if side == "Long" else prev_low
    entry_note = f"دخول محدد (Limit) عند مستوى اليوم السابق المكسور {entry_price:.6g} — بانتظار إعادة اختبار (Retest)، بدل مطاردة السعر الحالي {last_price:.6g}"
    _log("📍 منطق نقطة الدخول", entry_note)
    sl = structural_stop_loss(k5m, side, entry_price, atr5m, lookback=150)
    risk_distance = abs(entry_price - sl)
    if entry_price and risk_distance / entry_price < 0.0015:
        _log("❌ فلتر أدنى مسافة وقف خسارة", f"{risk_distance/entry_price*100:.3f}% أقل من 0.15% — السوق شبه ساكن", False)
        return None

    # --- تأكيدات مساعدة اختيارية (تزيد الاحتمالية، ما ترفض لو غابت) ---
    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        if side == "Long" and taker_pressure < -0.25:
            _log("❌ فلتر ضغط المتداولين الفعليين", f"{taker_pressure:.2f} يعاكس الشراء بوضوح — رفض", False)
            return None
        if side == "Short" and taker_pressure > 0.25:
            _log("❌ فلتر ضغط المتداولين الفعليين", f"{taker_pressure:.2f} يعاكس البيع بوضوح — رفض", False)
            return None

    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct < -0.5:
        _log("❌ فلتر الفائدة المفتوحة (OI)", f"{oi_change_pct:.2f}% — رفض (تصفية/تغطية مراكز، مو اقتناع جديد)", False)
        return None

    day_range = max(prev_high - prev_low, atr5m * 2.0)
    tp1 = entry_price + max(atr5m * 2.5, day_range) if side == "Long" else entry_price - max(atr5m * 2.5, day_range)
    tp2 = entry_price + max(atr5m * 5.0, day_range * 2.0) if side == "Long" else entry_price - max(atr5m * 5.0, day_range * 2.0)

    reward_distance = abs(tp2 - entry_price)
    if risk_distance > 0 and reward_distance / risk_distance < min_rr_floor:
        tp2 = entry_price + risk_distance * min_rr_floor if side == "Long" else entry_price - risk_distance * min_rr_floor
    rr = abs(tp2 - entry_price) / risk_distance if risk_distance > 0 else min_rr_floor

    prob = 78
    if vol_ratio > 3.0:
        prob += 5
    if vol_ratio > 5.0:
        prob += 3
    if body_ratio > 0.7:
        prob += 3
    if taker_pressure is not None:
        taker_aligned = (side == "Long" and taker_pressure > 0.15) or (side == "Short" and taker_pressure < -0.15)
        if taker_aligned:
            prob += 4
    if oi_change_pct is not None and oi_change_pct > 1.5:
        prob += 4
    cvd_pct = micro.cvd_pct if micro else None
    if cvd_pct is not None:
        cvd_aligned = (side == "Long" and cvd_pct > 60) or (side == "Short" and cvd_pct < 40)
        if cvd_aligned:
            prob += 3
    large_order_pressure = micro.large_order_pressure if micro else None
    if large_order_pressure is not None:
        lop_aligned = (side == "Long" and large_order_pressure > 0.15) or (side == "Short" and large_order_pressure < -0.15)
        if lop_aligned:
            prob += 3
    prob = max(70, min(95, prob))

    confirmed_1h = k1h[:-1] if len(k1h) > 1 else k1h
    h1_trend = _hma_bias_pair([k.close for k in confirmed_1h]) if len(confirmed_1h) >= 50 else None

    parts = [
        "📅 اختراق قمة/قاع اليوم السابق (Previous Day High/Low Breakout)",
        f"كُسر مستوى {'القمة' if side == 'Long' else 'القاع'} السابق عند {(prev_high if side=='Long' else prev_low):.6g} بإغلاق شمعة 5د كاملة (مو فتيلة)",
        f"فوليوم شمعة الاختراق: {vol_ratio:.2f}× المتوسط",
        "✅ تم استبعاد احتمال فخ الاختراق والانعكاس الفوري بعد السحب",
        entry_note,
    ]
    if taker_pressure is not None:
        parts.append(f"💥 ضغط المتداولين الفعليين: {taker_pressure:.2f}")
    if oi_change_pct is not None:
        parts.append(f"📊 تغير الفائدة المفتوحة (OI): {oi_change_pct:.2f}%")
    if cvd_pct is not None:
        parts.append(f"📊 CVD تراكمي (24س): {cvd_pct:.1f}% شراء")
    parts.append(f"🎯 الهدف الأول (TP1): {tp1}")
    parts.append(f"🚀 الهدف الثاني (TP2): {tp2}")
    parts.append(f"🛡️ الستوب لوز (SL): {sl}")
    parts.append(f"⚖️ نسبة العائد للمخاطرة: 1:{rr:.1f}")

    score_factors = [
        ("كسر مستوى اليوم السابق بإغلاق شمعة كاملة", True),
        ("فوليوم مؤكِّد على شمعة الاختراق (≥1.8×)", True),
        ("شمعة اختراق حاسمة (جسم قوي + إغلاق حاسم)", True),
        ("لا يوجد نمط فخ اختراق أو انعكاس فوري بعد سحب", True),
        ("ضغط المتداولين الفعليين متوافق", taker_pressure is not None and ((side == "Long" and taker_pressure > 0.1) or (side == "Short" and taker_pressure < -0.1))),
        ("الفائدة المفتوحة (OI) داعمة", oi_change_pct is not None and oi_change_pct > 1.0),
        ("CVD تراكمي متوافق", cvd_pct is not None and ((side == "Long" and cvd_pct > 55) or (side == "Short" and cvd_pct < 45))),
        ("🆕 ضغط صفقات كبيرة متوافق (Order Flow)", large_order_pressure is not None and ((side == "Long" and large_order_pressure > 0.15) or (side == "Short" and large_order_pressure < -0.15))),
    ]
    score_breakdown, signal_score = build_score_breakdown(score_factors)

    return AnalysisResult(
        symbol=symbol,
        trend=h1_trend or side,
        dt=daily_trend(k_daily, already_confirmed=True),
        prob=prob,
        price=last_price,
        atr=atr5m,
        side=side,
        entry_price=entry_price,
        stop_loss=sl,
        take_profit=tp2,
        tp1=tp1,
        rr=rr,
        quality="A" if prob >= 88 else "B",
        conf=7,
        behavior="، ".join(parts),
        volume_analysis=f"متوسط حجم 20 فترة: {avg_vol20:.2f} | حجم شمعة الاختراق: {closed.volume:.2f} | النسبة: {vol_ratio:.2f}x",
        low_vol=low_vol(k5m),
        kill_zone_ok=in_kill_zone(),
        news_time=check_irrational_market(k5m, k15m, k1h),
        ranging=False,
        score_breakdown=score_breakdown,
        signal_score=signal_score,
    )
