"""
استراتيجية صيد التصفيات (Liquidation Hunter).

الفكرة (مختلفة جوهرياً عن "صيد الاستوبات" العادي): تصفية المراكز المرفوعة الضخمة
تسبب **تسارعاً واستمراراً بنفس الاتجاه**، مو ارتداداً — لأن كل تصفية تولّد أمر سوق
إجباري إضافي بنفس اتجاه الحركة (تصفية Long = بيع إجباري يدفع السعر أكثر لتحت،
تصفية Short = شراء إجباري يدفع السعر أكثر لفوق)، وهذا يخلق تسلسل تصفيات متتالي
(Liquidation Cascade) يغذّي الحركة بدل ما يوقفها.

الخطوات:
  1) الانفجار السعري كمُطلِق — يخبرنا إن فيه زخم حقيقي يحصل الآن.
  2) نجيب خارطة التصفية المقدّرة (تقريبية، انظر liquidation_heatmap.py) لنفس العملة.
  3) نبحث عن أقرب/أقوى منطقة تصفية **بنفس اتجاه الانفجار السعري**:
       - انفجار Long → نبحث عن منطقة تصفية Short فوق السعر (يعني لو انكسرت، بيع
         مغطى إجباري يتحول لشراء إجباري يدفع السعر أعلى — تسارع صاعد).
       - انفجار Short → نبحث عن منطقة تصفية Long تحت السعر (نفس الفكرة بالعكس).
  4) نرتّب المرشحين حسب توازن (القوة/القرب) — منطقة قوية جداً لكن بعيدة جداً مو
     نافعة بنفس قيمة منطقة متوسطة القوة لكن قريبة وواقعية الوصول.
  5) الهدف يمتد **بعد** المنطقة (نتوقع كسرها والاستمرار)، والوقف أضيق نسبياً قبل
     المنطقة — يعطي عائد/مخاطرة جيد طبيعياً من نفس هندسة الصفقة.
"""
from typing import Optional

from .analyzer import analyze, AnalysisResult, MarketMicrostructure
from .liquidation_heatmap import estimate_liquidation_heatmap


def analyze_liquidation_hunter(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                                micro: Optional[MarketMicrostructure] = None,
                                trace: Optional[list] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    # الخطوة 1: الانفجار السعري كمُطلِق
    breakout_trace: list = []
    breakout_result = analyze(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro, trace=breakout_trace)
    if trace is not None:
        trace.append({"check": "── ⚡ المرحلة 1: كاشف الحدث (الانفجار السعري) ──", "value": "", "ok": None})
        trace.extend(breakout_trace)
    if breakout_result is None:
        _log("❌ القرار النهائي", "ما فيه أي حدث انفجار سعري أصلاً — توقفنا هنا", False)
        return None

    if not k1h or len(k1h) < 20:
        return None

    current_price = k5m[-1].close if k5m else k1h[-1].close
    funding_rate = micro.funding_rate if micro else None
    long_short_ratio = micro.long_short_ratio if micro else None

    heatmap = estimate_liquidation_heatmap(k1h, current_price, funding_rate=funding_rate,
                                            long_short_ratio=long_short_ratio)
    side = breakout_result.side

    # انفجار Long → نطارد منطقة تصفية Short فوق السعر (تسارع صاعد متوقع عند كسرها)
    # انفجار Short → نطارد منطقة تصفية Long تحت السعر (تسارع هابط متوقع عند كسرها)
    zones = heatmap["top_short_liq_zones"] if side == "Long" else heatmap["top_long_liq_zones"]
    _log(f"مناطق تصفية {'Short فوق السعر' if side=='Long' else 'Long تحت السعر'} المكتشفة", len(zones), len(zones) > 0)

    if not zones:
        _log("❌ القرار النهائي", "ما فيه منطقة تصفية واضحة بنفس اتجاه الانفجار — لا أساس للفرصة", False)
        return None

    # نرتّب حسب توازن (القوة نسبةً للقرب) — منطقة قوية وقريبة أفضل من قوية وبعيدة جداً
    def _score(z):
        dist = max(abs(z["distance_pct"]), 0.05)
        return z["intensity"] / dist

    best_zone = max(zones, key=_score)
    distance_pct = abs(best_zone["distance_pct"])
    _log("أفضل منطقة (الأقوى نسبةً للأقرب)", f"السعر {best_zone['price']:.6g} — المسافة {distance_pct:.2f}%")

    if distance_pct < 0.3 or distance_pct > 8.0:
        _log("❌ فلتر مسافة منطقية (0.3%–8%)", f"{distance_pct:.2f}% خارج النطاق الواقعي — رفض", False)
        return None

    # 📊 إصلاح مبني على بيانات إنتاج فعلية: كان الدخول يطارد سعر السوق مباشرة بعد
    # الانفجار السعري، وهذا غالباً يعني الشراء عند قمة الحركة المؤقتة (أو البيع عند
    # قاعها) — فحص حقيقي أظهر 73% من صفقات هذي الاستراتيجية كانت خاطئة من الأساس
    # (السعر ما تحرك لصالحنا أبداً تقريباً). الحل: دخول محدد (Limit) عند إعادة اختبار
    # واقعية بمسافة ATR بدل مطاردة السعر اللحظي، + بوابة تأكيد زخم إلزامية.
    retest_buffer = breakout_result.atr * 0.5
    if side == "Long":
        entry_price = current_price - retest_buffer
    else:
        entry_price = current_price + retest_buffer
    _log("📍 دخول محدد (Limit) عند إعادة اختبار بدل مطاردة السعر", f"{entry_price:.6g} (بدل سعر السوق {current_price:.6g})")

    # بوابة تأكيد زخم إلزامية: هذي الاستراتيجية تحديداً تعتمد كلياً على استمرار زخم
    # حقيقي، فلازم يكون ضغط المتداولين الفعلي متوافق فعلاً وقت الدخول (لو توفرت البيانات)
    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        aligned = (side == "Long" and taker_pressure > 0.05) or (side == "Short" and taker_pressure < -0.05)
        if not aligned:
            _log("❌ بوابة تأكيد الزخم الإلزامية", f"ضغط المتداولين {taker_pressure:.2f} لا يدعم استمرار الزخم — رفض", False)
            return None

    zone_price = best_zone["price"]
    distance_to_zone = abs(zone_price - entry_price)

    if side == "Long":
        take_profit = zone_price + distance_to_zone * 0.5  # امتداد بعد كسر المنطقة (تسارع الكسكارة)
        stop_loss = entry_price - distance_to_zone * 0.4
    else:
        take_profit = zone_price - distance_to_zone * 0.5
        stop_loss = entry_price + distance_to_zone * 0.4

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None
    reward = abs(take_profit - entry_price)
    rr = round(reward / risk, 2)
    _log("عائد/مخاطرة محسوب من هندسة الكسكارة", f"1:{rr}")

    if rr < 2.0:
        _log("❌ فلتر أدنى عائد/مخاطرة (1:2)", f"1:{rr} غير كافٍ — رفض", False)
        return None

    probability = 74
    if distance_pct <= 2.0:
        probability += 6  # منطقة قريبة = احتمال وصول أعلى واقعياً
    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct > 1.0:
        probability += 5  # فائدة مفتوحة مرتفعة = مراكز مرفوعة أكثر فعلاً = كسكارة أقوى محتملة
    if taker_pressure is not None:
        aligned = (side == "Long" and taker_pressure > 0.15) or (side == "Short" and taker_pressure < -0.15)
        if aligned:
            probability += 5
    cvd_pct = micro.cvd_pct if micro else None
    if cvd_pct is not None:
        aligned = (side == "Long" and cvd_pct > 60) or (side == "Short" and cvd_pct < 40)
        if aligned:
            probability += 5
    probability = min(95, probability)
    _log("✅ القرار النهائي", f"{side} — مطاردة منطقة تصفية عند {zone_price:.6g}", True)

    zone_type = "Short" if side == "Long" else "Long"
    behavior = (
        f"🔥 صيد التصفيات: رصد الانفجار السعري زخم {side}، وتوجد منطقة تصفية {zone_type} "
        f"مقدَّرة عند {zone_price:.6g} (تبعد {distance_pct:.2f}% عن السعر الحالي). الفرضية: كسر هذي "
        f"المنطقة يطلق تصفيات إجبارية متتالية (Liquidation Cascade) تغذّي استمرار الحركة {side} "
        f"بدل ارتدادها، فالهدف يمتد لما بعد المنطقة مباشرة.\n\n"
        f"⚡ [مُطلِق الانفجار السعري]: {breakout_result.behavior}"
    )
    volume_analysis = f"تقدير خارطة تصفية تقريبي (Liquidation Heatmap) + مُطلِق انفجار سعري"

    return AnalysisResult(
        symbol=symbol, trend=breakout_result.trend, dt="", prob=probability, price=current_price,
        atr=breakout_result.atr, side=side, entry_price=entry_price, stop_loss=stop_loss,
        take_profit=take_profit, rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
