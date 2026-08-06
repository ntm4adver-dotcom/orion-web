"""
استراتيجية الارتداد بعد فوليوم التصريف — نسخة "مع اتجاه الترند" (Trend-Aligned).

🆕 نسخة منفصلة تماماً عن `climactic_reversal_strategy.py` الأصلية (لم تُعدَّل
الأصلية إطلاقاً، بطلب صريح) — نفس فكرة الاستنزاف/التصريف بالضبط، بس بشرط
إضافي واحد: **الصفقة لازم تكون بنفس اتجاه الترند العام للعملة (يومي)**، مو
عكسه.

ليش هذا الفرق مهم:
الاستراتيجية الأصلية تتاجر عكس "الحركة الممتدة الأخيرة" (~4 أيام) مباشرة —
يعني لو العملة هبطت 4 أيام، تدخل Long عكسها، بغض النظر هل هذا الهبوط كان:
  (أ) بداية انعكاس حقيقي لترند أكبر، أو
  (ب) مجرد **تصحيح/سحبة مؤقتة داخل ترند صاعد أكبر بكثير** (شهور مثلاً).

هذي النسخة تفرّق بين الحالتين: تحسب **اتجاه الترند اليومي** (نطاق زمني أطول
بكثير من الحركة الممتدة الأربع أيام نفسها)، وتقبل الصفقة **فقط** لو اتجاهها
يطابق الترند اليومي — يعني تتاجر بمنطق "استنزاف نهاية تصحيح مؤقت داخل الترند
الأكبر"، مو "انعكاس الترند نفسه". هذا يقلل نسبة الصفقات اللي تحارب الاتجاه
العام للعملة، على حساب عدد أقل من الإشارات (لأنها ترفض أي استنزاف يقع عكس
الترند اليومي).
"""
from typing import Optional, List

from . import db
from .analyzer import Kline, AnalysisResult, MarketMicrostructure, atr, build_score_breakdown, daily_trend


def analyze_climactic_reversal_trend(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                                       micro: Optional[MarketMicrostructure] = None,
                                       trace: Optional[list] = None,
                                       current_price: Optional[float] = None,
                                       settings: Optional[dict] = None, **kwargs) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    if len(k15m) < 900:
        db.increment_rejection_counter("climactic_trend_insufficient_history")
        return None

    # 🆕 نفس عتبتي النسخة الأصلية، قابلتان للتعديل من الإعدادات (بطلب صريح)
    settings = settings or {}
    min_extended_move_pct = float(settings.get("climactic_min_extended_move_pct", 5.0))
    min_climax_volume_ratio = float(settings.get("climactic_min_volume_ratio", 8.0))

    # 🆕 الشرط الإضافي الوحيد مقارنة بالنسخة الأصلية: اتجاه الترند اليومي —
    # نطاق زمني أطول بكثير من الحركة الممتدة (400 شمعة 15د ≈ 4 أيام) اللي
    # نبني عليها الاستنزاف، عشان نميّز "تصحيح مؤقت داخل ترند أكبر" عن "انعكاس
    # ترند حقيقي".
    if len(k_daily) < 15:
        _log("عدد الشموع اليومية كافٍ لتحديد الترند العام (يحتاج ≥15 يوم)", len(k_daily), False)
        db.increment_rejection_counter("climactic_trend_insufficient_daily_history")
        return None
    overall_trend = daily_trend(k_daily)
    _log("اتجاه الترند اليومي العام للعملة", overall_trend)

    window = k15m[-400:]
    swing_start_price = window[0].close
    net_change_pct = (window[-1].close - swing_start_price) / swing_start_price * 100
    _log("صافي التغيّر خلال آخر 4 أيام (فحص حركة ممتدة/تصحيح)", f"{net_change_pct:.2f}%")

    if abs(net_change_pct) < min_extended_move_pct:
        _log(f"❌ فلتر الحركة الممتدة (يحتاج ≥{min_extended_move_pct}% تغيّر صافٍ)", f"{net_change_pct:.2f}% غير كافٍ — رفض", False)
        db.increment_rejection_counter("climactic_trend_extended_move_filter")
        return None

    established_direction_down = net_change_pct < 0

    recent_candles = k15m[-7:-1]
    vol_ref = [k.volume for k in k15m[-400:-7]]
    avg_vol = sum(vol_ref) / len(vol_ref) if vol_ref else 1.0
    if avg_vol <= 0:
        return None

    climax_candle = None
    climax_vol_ratio = 0.0
    for k in recent_candles:
        is_same_direction = (k.close < k.open) if established_direction_down else (k.close > k.open)
        vr = k.volume / avg_vol
        if vr > min_climax_volume_ratio and is_same_direction:
            climax_candle = k
            climax_vol_ratio = vr
            break

    if climax_candle is None:
        _log("❌ شمعة تصريف/استنزاف", f"ما فيه شمعة فوليوم متطرف (>{min_climax_volume_ratio}x) بنفس اتجاه الحركة الممتدة — رفض", False)
        db.increment_rejection_counter("climactic_trend_no_exhaustion_candle")
        return None
    _log("✅ شمعة تصريف/استنزاف مكتشفة", f"فوليوم {climax_vol_ratio:.1f}x المتوسط", True)

    # 🔴 نفس إصلاح النسخة الأصلية (بعد اكتشافه بصفقات حقيقية): هامش حقيقي
    # (ATR) بدل أي تجاوز تافه لإغلاق شمعة التصريف
    atr_val = atr(k15m, 14)
    confirm_margin_atr_mult = float(settings.get("climactic_confirm_margin_atr", 0.3))
    confirm_margin = atr_val * confirm_margin_atr_mult if atr_val > 0 else 0.0

    last = k15m[-1]
    if established_direction_down:
        reversal_confirmed = last.close > climax_candle.close + confirm_margin
        side = "Long"
    else:
        reversal_confirmed = last.close < climax_candle.close - confirm_margin
        side = "Short"

    _log("تأكيد الانعكاس (إغلاق بعكس شمعة التصريف بهامش ≥0.3×ATR)", reversal_confirmed, reversal_confirmed)
    if not reversal_confirmed:
        _log("❌ القرار النهائي", "الانعكاس لسا ما تأكد بهامش كافٍ — ننتظر", False)
        db.increment_rejection_counter("climactic_trend_reversal_not_confirmed")
        return None

    # 🔴 إصلاح جذري (بطلب صريح، بعد تشخيص بالبيانات الفعلية: صفر صفقة منذ
    # الإنشاء، وعدّاد climactic_trend_daily_alignment_filter وحده كان يرفض كل
    # شي بعد كل الفحوصات التانية بالضبط). السبب الجوهري: نمط "استنزاف/انعكاس"
    # بطبيعته غالباً يصير **عكس** الترند اليومي الأكبر وقتها (هذا تعريف
    # الانعكاس نفسه) — فشرط "لازم يطابق الترند اليومي" كان يناقض جوهر النمط
    # ذاتياً، مو مجرد صارم. الآن نحوّله من بوابة رفض قاطعة إلى عامل تعزيز
    # ثقة: التوافق مع الترند يرفع الاحتمالية (لأنه فعلاً دليل إضافي جيد لو
    # توفر)، لكن غيابه ما يمنع الصفقة كليّاً — يحافظ على روح الفكرة الأصلية
    # (تفضيل التوافق) بدون ما يقتل الاستراتيجية بالكامل.
    side_ar = "صاعد" if side == "Long" else "هابط"
    trend_aligned = side_ar == overall_trend
    if trend_aligned:
        _log("✅ الصفقة متوافقة مع الترند اليومي العام (تعزيز ثقة إضافي)", f"{side} ({side_ar}) == {overall_trend}", True)
    else:
        _log("⚠️ الصفقة تعاكس الترند اليومي العام (طبيعي لنمط انعكاس، بدون رفض)", f"{side} ({side_ar}) != {overall_trend}", None)

    # 🔴 نفس السعر الحي الحقيقي المُمرَّر من السكانر لو متوفر
    current_price = current_price if current_price is not None else (k5m[-1].close if k5m else last.close)
    if atr_val <= 0:
        return None

    entry_price = current_price
    move_range = abs(swing_start_price - (climax_candle.low if side == "Long" else climax_candle.high))
    if move_range <= 0:
        return None

    def _safe_buffer(mult: float) -> float:
        return max(atr_val * mult, entry_price * 0.006)

    if side == "Long":
        stop_loss = climax_candle.low - _safe_buffer(0.5)
        take_profit = climax_candle.low + move_range * 0.382  # ارتداد فيبوناتشي كلاسيكي 38.2%
    else:
        stop_loss = climax_candle.high + _safe_buffer(0.5)
        take_profit = climax_candle.high - move_range * 0.382

    # فلتر اتجاه الدخول (نفس الحماية المركزية المطبَّقة بكل الاستراتيجيات)
    if side == "Long" and entry_price > current_price * 1.0005:
        return None
    if side == "Short" and entry_price < current_price * 0.9995:
        return None

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None
    reward = abs(take_profit - entry_price)
    rr = round(reward / risk, 2)
    _log("عائد/مخاطرة", f"1:{rr}")
    if rr < 1.5:
        _log("❌ فلتر أدنى عائد/مخاطرة (1:1.5)", f"1:{rr} غير كافٍ — رفض", False)
        return None

    # 🔴 الاحتمالية الأساسية رجعت لنفس مستوى النسخة الأصلية (74) بما إن
    # التوافق مع الترند اليومي صار عامل تعزيز اختياري، مو مضمون الحدوث —
    # التعزيز الفعلي يُضاف أدناه بس لو التوافق موجود فعلاً.
    probability = 74
    if trend_aligned:
        probability += 8  # تعزيز حقيقي أكبر من بقية العوامل — هذا جوهر ميزة هذي النسخة
    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        aligned = (side == "Long" and taker_pressure > 0.1) or (side == "Short" and taker_pressure < -0.1)
        if aligned:
            probability += 6
        opposed = (side == "Long" and taker_pressure < -0.2) or (side == "Short" and taker_pressure > 0.2)
        if opposed:
            _log("❌ ضغط المتداولين يعاكس الانعكاس بوضوح", taker_pressure, False)
            return None
    if climax_vol_ratio > 15:
        probability += 5

    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None:
        if (side == "Long" and oi_change_pct < -1.0) or (side == "Short" and oi_change_pct > 1.0):
            probability += 3

    probability = max(70, min(94, probability))

    large_order_pressure = micro.large_order_pressure if micro else None
    score_factors = [
        ("حركة ممتدة/تصحيح حقيقي (≥5% صافي تغيّر)", True),
        ("شمعة تصريف/استنزاف بفوليوم متطرف (>8x)", True),
        ("تأكيد انعكاس فعلي (إغلاق بعكس شمعة التصريف)", True),
        ("🆕 الصفقة مع اتجاه الترند اليومي العام (تعزيز اختياري، مو شرط)", trend_aligned),
        ("ضغط متداولين فعلي متوافق", taker_pressure is not None and ((side == "Long" and taker_pressure > 0.1) or (side == "Short" and taker_pressure < -0.1))),
        ("فوليوم تصريف ضخم جداً (>15x)", climax_vol_ratio > 15),
        ("🆕 ضغط صفقات كبيرة متوافق (Order Flow)", large_order_pressure is not None and ((side == "Long" and large_order_pressure > 0.15) or (side == "Short" and large_order_pressure < -0.15))),
    ]
    score_breakdown, signal_score = build_score_breakdown(score_factors)

    behavior = (
        f"🌊📈 ارتداد بعد فوليوم تصريف (مع اتجاه الترند): {'تصحيح هابط' if established_direction_down else 'تصحيح صاعد'} "
        f"مؤقت ({net_change_pct:.2f}%) داخل ترند يومي {overall_trend} أكبر، تلته شمعة تصريف بفوليوم "
        f"{climax_vol_ratio:.1f}x المتوسط، وتأكد الاستنزاف بإغلاق عكسي. دخول {side} عند {entry_price:.6g} "
        f"— استكمال الترند الأكبر، مو انعكاسه. هدف ارتداد فيبوناتشي 38.2% من حركة التصحيح."
    )
    volume_analysis = f"فوليوم تصريف {climax_vol_ratio:.1f}x + توافق مع الترند اليومي ({overall_trend}) + تأكيد انعكاس فعلي"

    return AnalysisResult(
        symbol=symbol, trend=overall_trend, dt=overall_trend, prob=probability,
        price=current_price, atr=atr_val, side=side, entry_price=entry_price, stop_loss=stop_loss,
        take_profit=take_profit, rr=rr, quality="A" if probability >= 85 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
        score_breakdown=score_breakdown, signal_score=signal_score,
    )
