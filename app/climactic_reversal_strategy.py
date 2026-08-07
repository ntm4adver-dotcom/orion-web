"""
استراتيجية الارتداد بعد فوليوم التصريف/الاستنزاف (Climactic Volume Reversal).

الفكرة (مبنية على مبدأ فني كلاسيكي موثّق — Selling/Buying Climax): حركة سعرية
ممتدة (هبوط أو صعود واضح) تنتهي غالباً بشمعة فوليوم **متطرف جداً** (تصريف/استنزاف) —
وهذا عكس ما يبدو للوهلة الأولى: فوليوم ضخم كذا بنهاية حركة ممتدة غالباً يعني
"استنزاف" (كل البائعين/المشترين خرجوا دفعة وحدة) مو استمرار، ويسبق ارتداد حقيقي.

هذي الاستراتيجية اكتُشفت مباشرة من مراجعة صفقة حقيقية فشلت (COREUSDT عبر
سكالب دقيق) — كانت الاستراتيجية الأخرى تعتبر فوليوم 16x "تأكيد استمرار"، بينما
الشارت أظهر إنه كان فعلياً نقطة انعكاس. هذي الاستراتيجية تتاجر **بنفس المنطق
بس بالاتجاه الصحيح**: عكس الحركة الممتدة، مو معها.

الخطوات:
  1) نتأكد من حركة ممتدة حقيقية (تغيّر صافي واضح) على فريم 15 دقيقة.
  2) نبحث عن شمعة "تصريف" — فوليوم متطرف (>8x المتوسط) بنفس اتجاه الحركة الممتدة.
  3) ننتظر شمعة تأكيد تغلق **بعكس** اتجاه شمعة التصريف (يؤكد الاستنزاف فعلاً).
  4) الدخول بعكس الحركة الأصلية، الوقف خلف نقطة التصريف نفسها (لو انكسرت تبطل
     الفرضية)، والهدف ارتداد فيبوناتشي كلاسيكي (38.2%) من الحركة الممتدة.
"""
from typing import Optional, List

from . import db
from .analyzer import Kline, AnalysisResult, MarketMicrostructure, atr, build_score_breakdown, find_strongest_reaction_level


def analyze_climactic_reversal(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                                 micro: Optional[MarketMicrostructure] = None,
                                 trace: Optional[list] = None,
                                 current_price: Optional[float] = None,
                                 settings: Optional[dict] = None,
                                 k1m: Optional[list] = None, **kwargs) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    if len(k15m) < 900:
        db.increment_rejection_counter("climactic_insufficient_history")
        return None

    # 🆕 عتبتان قابلتان للتعديل من الإعدادات (بطلب صريح، بعد مراجعة صفقات
    # فترة كانت نتائجها قوية — العينة صغيرة جداً (6 صفقات) وما قدرت ألقى رقم
    # واحد يفصل "الحركة الحاسمة" عن الهامشية بثقة كافية. بدل ما أفرض رقم
    # تخميني، حوّلت العتبتين الأساسيتين (كانتا ثابتتين بالكود) لإعدادات تقدر
    # تجرّبها وترفعها بنفسك، وتراقب هل ترفع نسبة الصفقات "الحاسمة" فعلياً.
    settings = settings or {}
    min_extended_move_pct = float(settings.get("climactic_min_extended_move_pct", 5.0))
    min_climax_volume_ratio = float(settings.get("climactic_min_volume_ratio", 8.0))

    window = k15m[-400:]
    swing_start_price = window[0].close
    net_change_pct = (window[-1].close - swing_start_price) / swing_start_price * 100
    _log("صافي التغيّر خلال آخر 4 أيام (فحص حركة ممتدة)", f"{net_change_pct:.2f}%")

    if abs(net_change_pct) < min_extended_move_pct:
        _log(f"❌ فلتر الحركة الممتدة (يحتاج ≥{min_extended_move_pct}% تغيّر صافٍ)", f"{net_change_pct:.2f}% غير كافٍ — رفض", False)
        db.increment_rejection_counter("climactic_extended_move_filter")
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
        db.increment_rejection_counter("climactic_no_exhaustion_candle")
        return None
    _log("✅ شمعة تصريف/استنزاف مكتشفة", f"فوليوم {climax_vol_ratio:.1f}x المتوسط", True)

    # 🔴 إصلاح خطأ حقيقي (بعد مراجعة صفقات مغلقة فعلية: UBUSDT وZORAUSDT خسرتا
    # بأقصى ربح 0.1% تقريباً — تأكيد الانعكاس كان يتفعّل بأي تجاوز تافه لإغلاق
    # شمعة التصريف، حتى لو مجرد استراحة مؤقتة داخل استمرار الحركة الأصلية).
    # الآن نشترط هامش حقيقي (ATR) بدل أي تجاوز مهما كان صغيراً — يميّز انعكاس
    # فعلي عن ضجيج عابر يُبتلع فوراً بمواصلة الحركة الأصلية.
    atr_val = atr(k15m, 14)
    confirm_margin_atr_mult = float(settings.get("climactic_confirm_margin_atr", 0.3))
    confirm_margin = atr_val * confirm_margin_atr_mult if atr_val > 0 else 0.0

    # تأكيد الانعكاس: آخر شمعة تغلق بعكس اتجاه شمعة التصريف بهامش حقيقي (مو أي تجاوز تافه)
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
        db.increment_rejection_counter("climactic_reversal_not_confirmed")
        return None

    # 🔴 إصلاح جوهري: نستخدم السعر الحي الحقيقي المُمرَّر من السكانر لو متوفر
    current_price = current_price if current_price is not None else (k5m[-1].close if k5m else last.close)
    if atr_val <= 0:
        return None

    entry_price = current_price

    # 🆕 اختيار أقوى نقطة دخول (بطلب صريح — نفس الإصلاح بكل الاستراتيجيات):
    if k1m and len(k1m) >= 40:
        reaction = find_strongest_reaction_level(k1m, side=side, current_price=entry_price, max_distance_pct=1.5)
        if reaction is not None:
            candidate_level = reaction["level"]
            is_better_entry = (side == "Long" and candidate_level < entry_price) or (side == "Short" and candidate_level > entry_price)
            if is_better_entry:
                entry_price = candidate_level
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

    probability = 74
    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        aligned = (side == "Long" and taker_pressure > 0.1) or (side == "Short" and taker_pressure < -0.1)
        if aligned:
            probability += 8
        opposed = (side == "Long" and taker_pressure < -0.2) or (side == "Short" and taker_pressure > 0.2)
        if opposed:
            _log("❌ ضغط المتداولين يعاكس الانعكاس بوضوح", taker_pressure, False)
            return None
    if climax_vol_ratio > 15:
        probability += 6  # كل ما كان التصريف أضخم، كل ما كان الاستنزاف أوضح

    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None:
        if (side == "Long" and oi_change_pct < -1.0) or (side == "Short" and oi_change_pct > 1.0):
            probability += 4  # فائدة مفتوحة تنخفض بجهة الحركة الأصلية = تصفية مراكز، يدعم الانعكاس

    probability = max(70, min(93, probability))

    large_order_pressure = micro.large_order_pressure if micro else None
    score_factors = [
        ("حركة ممتدة حقيقية (≥5% صافي تغيّر)", True),
        ("شمعة تصريف/استنزاف بفوليوم متطرف (>8x)", True),
        ("تأكيد انعكاس فعلي (إغلاق بعكس شمعة التصريف)", True),
        ("ضغط متداولين فعلي متوافق", taker_pressure is not None and ((side == "Long" and taker_pressure > 0.1) or (side == "Short" and taker_pressure < -0.1))),
        ("فوليوم تصريف ضخم جداً (>15x)", climax_vol_ratio > 15),
        ("🆕 ضغط صفقات كبيرة متوافق (Order Flow)", large_order_pressure is not None and ((side == "Long" and large_order_pressure > 0.15) or (side == "Short" and large_order_pressure < -0.15))),
    ]
    score_breakdown, signal_score = build_score_breakdown(score_factors)

    behavior = (
        f"🌊 ارتداد بعد فوليوم تصريف/استنزاف: حركة {'هابطة' if established_direction_down else 'صاعدة'} "
        f"ممتدة ({net_change_pct:.2f}%)، تلتها شمعة تصريف بفوليوم {climax_vol_ratio:.1f}x المتوسط، "
        f"وتأكد الانعكاس بإغلاق عكسي. دخول {side} عند {entry_price:.6g}، هدف ارتداد فيبوناتشي 38.2% "
        f"من الحركة الأصلية."
    )
    volume_analysis = f"فوليوم تصريف {climax_vol_ratio:.1f}x + تأكيد انعكاس فعلي — استراتيجية أصيلة مبنية على مبدأ Selling/Buying Climax"

    return AnalysisResult(
        symbol=symbol, trend=("هابط" if established_direction_down else "صاعد"), dt="", prob=probability,
        price=current_price, atr=atr_val, side=side, entry_price=entry_price, stop_loss=stop_loss,
        take_profit=take_profit, rr=rr, quality="A" if probability >= 85 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
        score_breakdown=score_breakdown, signal_score=signal_score,
    )
