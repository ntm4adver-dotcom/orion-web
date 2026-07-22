"""
استراتيجية فيبوناتشي الترند المتعدد الفريمات (Multi-Timeframe Fibonacci Trend Pullback).

الفكرة (بالضبط كما طُلبت):
  1) نحدد الاتجاه الرئيسي على فريم 15 دقيقة.
  2) ننزل لفريم 5 دقائق ونبحث عن ترند معاكس (تراجع/Pullback) ضد الاتجاه الرئيسي.
  3) ننتظر "كسر حقيقي" لهذا الترند المعاكس على فريم 5 دقائق — يعني كسر هيكلي
     (CHoCH) يؤكد إن التراجع انتهى فعلاً والسعر بادئ يرجع لاتجاه الترند الرئيسي.
  4) بعد تأكد الكسر، نرسم فيبوناتشي على حركة التراجع نفسها (من أعلى قمة سابقة
     لأقل قاع سابق، أو العكس حسب الاتجاه)، وننتظر السعر يرجع لمنطقة 0.72 كنقطة
     دخول محددة (Limit) — مو مطاردة السعر فور الكسر.
  5) الدخول دائماً **مع الاتجاه الرئيسي (15 دقيقة)**، أبداً مع اتجاه التراجع.

مثال توضيحي (اتجاه رئيسي صاعد):
  - 15 دقيقة: صاعد.
  - 5 دقائق: تراجع هابط مؤقت (قمة سابقة ← قاع).
  - كسر حقيقي: السعر يكسر فوق قمة صغيرة تكوّنت أثناء محاولة التعافي من القاع
    (كسر هيكل CHoCH صاعد يؤكد انتهاء التراجع).
  - فيبوناتشي: من القاع (0%) للقمة السابقة (100%) — النطاق اللي انسحب فيه التراجع.
  - نقطة الدخول: القاع + 72% من المدى (منطقة 0.72) — ندخل Long هناك بانتظار
    ارتداد جزئي للسعر لهذي المنطقة قبل ما يكمل صعوده مع الترند الرئيسي.
  - نفس المنطق بالعكس تماماً لو الاتجاه الرئيسي هابط.
"""
from typing import Optional, List

from .analyzer import Kline, AnalysisResult, MarketMicrostructure, atr, _get_bias


def _find_swing_extremes(window: List[Kline]):
    high_idx = max(range(len(window)), key=lambda i: window[i].high)
    low_idx = min(range(len(window)), key=lambda i: window[i].low)
    return high_idx, window[high_idx].high, low_idx, window[low_idx].low


def analyze_mtf_fib_trend(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                           micro: Optional[MarketMicrostructure] = None,
                           trace: Optional[list] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    if len(k15m) < 30 or len(k5m) < 35:
        _log("عدد شموع كافٍ (15د≥30، 5د≥35)", f"15د={len(k15m)}, 5د={len(k5m)}", False)
        return None

    main_trend = _get_bias(k15m)
    _log("الاتجاه الرئيسي (15 دقيقة)", main_trend)

    window = k5m[-35:]
    high_idx, swing_high, low_idx, swing_low = _find_swing_extremes(window)
    swing_range = swing_high - swing_low
    if swing_range <= 0:
        return None

    last_close = window[-1].close
    entry_price = None
    stop_loss = None
    take_profit = None
    side = None

    if main_trend == "صاعد":
        # نبحث عن تراجع هابط على 5 دقائق (قمة سابقة ثم قاع) يعقبه كسر حقيقي صاعد
        if not (high_idx < low_idx):
            _log("❌ شكل التراجع المطلوب (قمة ثم قاع) غير متوفر على 5 دقائق", f"high_idx={high_idx}, low_idx={low_idx}", False)
            return None
        _log("تراجع هابط مكتشف على 5 دقائق (معاكس للترند الرئيسي)", f"قمة {swing_high:.6g} ← قاع {swing_low:.6g}", True)

        post_low = window[low_idx + 1:]
        if len(post_low) < 2:
            _log("❌ كسر حقيقي (CHoCH)", "لسا ما فيه شموع كافية بعد القاع للتأكد من الكسر — مبكر", False)
            return None
        recent_peak_after_low = max(k.high for k in post_low[:-1])
        genuine_break = last_close > recent_peak_after_low
        _log("كسر حقيقي (CHoCH) صاعد يؤكد انتهاء التراجع", f"إغلاق={last_close:.6g} مقابل قمة تعافي={recent_peak_after_low:.6g}", genuine_break)
        if not genuine_break:
            _log("❌ القرار النهائي", "التراجع لسا ما انكسر كسراً حقيقياً — ننتظر", False)
            return None

        side = "Long"
        entry_price = swing_low + 0.72 * swing_range  # منطقة 0.72 من القاع نحو القمة
        # 🔴 إصلاح حرج مبني على صفقة حقيقية فشلت خلال 6.4 ثانية بس: كان الوقف يُحسب
        # من نسبة الحركة نفسها بدون أي حد أدنى مطلق — على عملات قليلة التقلب (زي
        # NOTUSDT) هذا ينتج مسافة وقف ضئيلة جداً (0.22% بالحالة الفعلية) تُضرب فوراً
        # بأول تذبذب عادي. نفس الحماية المطبَّقة بباقي الاستراتيجيات: الأكبر بين
        # (مستوى 50% من الحركة) أو (0.8% من السعر كحد أدنى مطلق).
        stop_candidate = swing_low + swing_range * 0.5 - atr(k5m, 14) * 0.2
        min_stop_distance = entry_price * 0.008
        stop_loss = min(stop_candidate, entry_price - min_stop_distance)
        take_profit = swing_high + swing_range * 1.0  # امتداد ما بعد القمة السابقة بنفس مدى الحركة

    elif main_trend == "هابط":
        # نبحث عن تراجع صاعد على 5 دقائق (قاع سابق ثم قمة) يعقبه كسر حقيقي هابط
        if not (low_idx < high_idx):
            _log("❌ شكل التراجع المطلوب (قاع ثم قمة) غير متوفر على 5 دقائق", f"low_idx={low_idx}, high_idx={high_idx}", False)
            return None
        _log("تراجع صاعد مكتشف على 5 دقائق (معاكس للترند الرئيسي)", f"قاع {swing_low:.6g} ← قمة {swing_high:.6g}", True)

        post_high = window[high_idx + 1:]
        if len(post_high) < 2:
            _log("❌ كسر حقيقي (CHoCH)", "لسا ما فيه شموع كافية بعد القمة للتأكد من الكسر — مبكر", False)
            return None
        recent_trough_after_high = min(k.low for k in post_high[:-1])
        genuine_break = last_close < recent_trough_after_high
        _log("كسر حقيقي (CHoCH) هابط يؤكد انتهاء التراجع", f"إغلاق={last_close:.6g} مقابل قاع تعافي={recent_trough_after_high:.6g}", genuine_break)
        if not genuine_break:
            _log("❌ القرار النهائي", "التراجع لسا ما انكسر كسراً حقيقياً — ننتظر", False)
            return None

        side = "Short"
        entry_price = swing_high - 0.72 * swing_range  # منطقة 0.72 من القمة نحو القاع
        stop_candidate = swing_high - swing_range * 0.5 + atr(k5m, 14) * 0.2
        min_stop_distance = entry_price * 0.008
        stop_loss = max(stop_candidate, entry_price + min_stop_distance)
        take_profit = swing_low - swing_range * 1.0

    else:
        return None

    # 🔴 إصلاح جذري: التحقق السابق كان يفحص "مقدار" المسافة بس (abs())، بدون ما
    # يتحقق من "الاتجاه" — يعني منطقة 0.72 ممكن تكون قريبة لكن بالجهة الغلط تماماً
    # (خلف السعر الحالي، مو أمامه)، وهذا ما كان يُكتشف إلا لاحقاً بفحص عام بالسكانر.
    # نتحقق الآن صراحة هنا: لصفقة Long، منطقة 0.72 لازم تكون تحت السعر الحالي أو
    # تساويه (ننتظر نزول). لصفقة Short، لازم تكون فوقه أو تساويه (ننتظر صعود).
    # لو انتهكت هذا الشرط، معناه السعر تجاوز منطقة 0.72 فعلياً قبل ما نكتشف الفرصة
    # — الحركة كانت أسرع من رصدنا لها، والفرصة "فاتت" لهذي الدورة، مو خطأ حسابي.
    if side == "Long" and entry_price > last_close * 1.0005:
        _log("❌ اتجاه منطقة الدخول غير صالح", f"منطقة 0.72 ({entry_price:.6g}) أصبحت فوق السعر الحالي ({last_close:.6g}) — السعر لسا ما نزل لمنطقة الدخول، أو تجاوزها بسرعة — الفرصة فاتت هذي الدورة", False)
        return None
    if side == "Short" and entry_price < last_close * 0.9995:
        _log("❌ اتجاه منطقة الدخول غير صالح", f"منطقة 0.72 ({entry_price:.6g}) أصبحت تحت السعر الحالي ({last_close:.6g}) — السعر تجاوز منطقة الدخول بسرعة أكبر من المتوقع — الفرصة فاتت هذي الدورة", False)
        return None

    # فلتر منطقية: منطقة 0.72 لازم تكون بمسافة واقعية من السعر الحالي (مو بعيدة جداً)
    distance_pct = abs(entry_price - last_close) / last_close * 100 if last_close else 999
    _log("مسافة منطقة الدخول (0.72) عن السعر الحالي", f"{distance_pct:.2f}%")
    if distance_pct > 5.0:
        _log("❌ فلتر مسافة منطقية (حتى 5%)", f"{distance_pct:.2f}% بعيدة جداً — رفض", False)
        return None

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None
    reward = abs(take_profit - entry_price)
    rr = round(reward / risk, 2)
    _log("عائد/مخاطرة", f"1:{rr}")
    if rr < 2.0:
        _log("❌ فلتر أدنى عائد/مخاطرة (1:2)", f"1:{rr} غير كافٍ — رفض", False)
        return None

    probability = 76
    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        aligned = (side == "Long" and taker_pressure > 0.1) or (side == "Short" and taker_pressure < -0.1)
        if aligned:
            probability += 6
        opposed = (side == "Long" and taker_pressure < -0.2) or (side == "Short" and taker_pressure > 0.2)
        if opposed:
            _log("❌ فلتر ضغط المتداولين الفعليين", f"{taker_pressure:.2f} يعاكس الصفقة بوضوح — رفض", False)
            return None

    cvd_pct = micro.cvd_pct if micro else None
    if cvd_pct is not None and ((side == "Long" and cvd_pct > 58) or (side == "Short" and cvd_pct < 42)):
        probability += 5

    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct < -1.5:
        probability -= 6

    probability = max(70, min(95, probability))
    _log("✅ القرار النهائي", f"{side} — دخول من منطقة فيبوناتشي 0.72", True)

    behavior = (
        f"📐 فيبوناتشي الترند المتعدد الفريمات: اتجاه رئيسي {main_trend} على 15 دقيقة، "
        f"تراجع معاكس على 5 دقائق من {swing_high:.6g} إلى {swing_low:.6g} انكسر كسراً حقيقياً "
        f"(CHoCH). دخول {side} من منطقة فيبوناتشي 0.72 عند {entry_price:.6g} — مع الاتجاه "
        f"الرئيسي، بانتظار ارتداد جزئي للسعر لهذي المنطقة قبل الاستمرار."
    )
    volume_analysis = "فيبوناتشي 0.72 على تراجع 5 دقائق منكسر + تأكيد اتجاه 15 دقيقة"

    return AnalysisResult(
        symbol=symbol, trend=main_trend, dt="", prob=probability, price=last_close,
        atr=atr(k5m, 14), side=side, entry_price=entry_price, stop_loss=stop_loss,
        take_profit=take_profit, rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
