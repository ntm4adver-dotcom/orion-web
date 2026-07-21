"""
استراتيجية مصيدة الحشد (Crowd Trap Divergence) — تصميم أصيل.

هذي استراتيجية من تصميمي الخاص، مو منقولة أو مبنية على منهج معروف لمتداول أو
مدرسة تحليل معيّنة (بعكس بقية استراتيجيات التطبيق). الفكرة الجوهرية مختلفة تماماً
عن كل الاستراتيجيات الأخرى: **لا تعتمد على أي نمط سعري إطلاقاً** (لا اختراق، لا
ارتداد، لا فيبوناتشي، لا كسر هيكل) — بل تعتمد حصراً على اكتشاف **تناقض (Divergence)**
بين مصدرين مختلفين للبيانات:

  1) "ما يقوله الحشد" — وضعية المتداولين المعلنة: نسبة تمركز الحسابات (Long/Short
     Ratio) + معدل التمويل (Funding Rate). لو الاثنين متوافقين على اتجاه واحد
     متطرف، فالحشد (أغلبية المتداولين العاديين) متمركز بقوة بهذا الاتجاه.

  2) "ما يحصل فعلياً" — التدفق الحقيقي المُنفَّذ: CVD تراكمي 24 ساعة (من صفقات
     حقيقية مسحوبة، مو تخمين) + ضغط المتداولين الفعلي اللحظي (Taker Pressure).

  الفرضية: لو الحشد متمركز بقوة باتجاه معين (مثلاً Long)، لكن التدفق الحقيقي
  المُنفَّذ يتحرك بعكسه فعلياً (بيع صافٍ رغم إن أغلب الحسابات Long) — هذا "تناقض"
  يكشف تصفية هادئة تحصل تحت السطح، ورجّح انعكاس قوي يعصر المراكز المزدحمة
  (Short Squeeze أو Long Squeeze حسب الاتجاه). ندخل **مع التدفق الحقيقي، ضد الحشد**.

  هذا النمط لا يحتاج أي شكل شمعة أو مستوى سعري معيّن — بس تناقض واضح بين
  "الإعلان" و"الواقع" بأربع نقاط بيانات مستقلة تتفق كلها بنفس الاتجاه.

⚠️ ملاحظة صادقة: هذا تصميم تجريبي أصيل، مو مبني على سجل أداء تاريخي مثبت (بعكس
باقي الاستراتيجيات المبنية على مناهج معروفة). راقب أداءه الفعلي بصفحة "التطور"
قبل ما تعتمد عليه بثقة كبيرة.
"""
from typing import Optional, List

from .analyzer import Kline, AnalysisResult, MarketMicrostructure, atr


def analyze_crowd_trap(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                        micro: Optional[MarketMicrostructure] = None,
                        trace: Optional[list] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    if micro is None:
        _log("بيانات البنية الجزئية متوفرة", False, False)
        return None
    if len(k1h) < 20 or len(k5m) < 10:
        return None

    long_short_ratio = micro.long_short_ratio
    funding_rate = micro.funding_rate
    cvd_pct = micro.cvd_pct
    taker_pressure = micro.taker_pressure

    _log("نسبة تمركز الحسابات (Long/Short)", long_short_ratio if long_short_ratio is not None else "غير متوفرة")
    _log("معدل التمويل (Funding)", funding_rate if funding_rate is not None else "غير متوفر")
    _log("CVD تراكمي 24 ساعة", cvd_pct if cvd_pct is not None else "غير متوفر (يحتاج وقت تجميع)")
    _log("ضغط المتداولين الفعلي اللحظي", taker_pressure if taker_pressure is not None else "غير متوفر")

    # لازم الأربع نقاط بيانات متوفرة — هذي الاستراتيجية قائمة بالكامل على التناقض
    # بينها، فبدون أي وحدة منها ما فيه أساس حقيقي نبني عليه القرار
    if None in (long_short_ratio, funding_rate, cvd_pct, taker_pressure):
        _log("❌ القرار النهائي", "بيانات ناقصة — هذي الاستراتيجية تحتاج كل نقاط البيانات الأربعة متوفرة", False)
        return None

    side = None
    crowd_desc = ""

    # حالة 1: الحشد متمركز Long بقوة (نسبة عالية + تمويل موجب) لكن التدفق الحقيقي يبيع
    crowd_long = long_short_ratio > 1.8 and funding_rate > 0.0003
    real_flow_selling = cvd_pct < 42 and taker_pressure < -0.05
    if crowd_long and real_flow_selling:
        side = "Short"
        crowd_desc = (f"الحشد متمركز Long بقوة (نسبة {long_short_ratio:.2f}, تمويل {funding_rate*100:.4f}%) "
                       f"لكن التدفق الحقيقي يبيع فعلياً (CVD={cvd_pct:.1f}%, ضغط={taker_pressure:.2f}) — "
                       f"تناقض يرجّح تصفية مراكز Long المزدحمة")

    # حالة 2 (معاكسة تماماً): الحشد متمركز Short بقوة لكن التدفق الحقيقي يشتري
    crowd_short = long_short_ratio < 0.55 and funding_rate < -0.0003
    real_flow_buying = cvd_pct > 58 and taker_pressure > 0.05
    if crowd_short and real_flow_buying:
        side = "Long"
        crowd_desc = (f"الحشد متمركز Short بقوة (نسبة {long_short_ratio:.2f}, تمويل {funding_rate*100:.4f}%) "
                       f"لكن التدفق الحقيقي يشتري فعلياً (CVD={cvd_pct:.1f}%, ضغط={taker_pressure:.2f}) — "
                       f"تناقض يرجّح عصر مراكز Short المزدحمة (Short Squeeze)")

    _log("تناقض الحشد صاعد (Long) مقابل تدفق بيع حقيقي", crowd_long and real_flow_selling, crowd_long and real_flow_selling or None)
    _log("تناقض الحشد هابط (Short) مقابل تدفق شراء حقيقي", crowd_short and real_flow_buying, crowd_short and real_flow_buying or None)

    if side is None:
        _log("❌ القرار النهائي", "ما فيه تناقض واضح حالياً بين وضعية الحشد والتدفق الحقيقي — لا فرصة", False)
        return None

    _log("✅ تناقض مكتشف", crowd_desc, True)

    # ما فيه نمط سعري نبني عليه الدخول (هذا مقصود بتصميم الاستراتيجية) — نستخدم
    # هيكل السعر الأخير على فريم الساعة لتحديد وقف منطقي، وATR لهدف واقعي
    current_price = k5m[-1].close
    atr_val = atr(k1h, 14)
    if atr_val <= 0 or current_price <= 0:
        return None

    recent_swing_high = max(k.high for k in k1h[-15:])
    recent_swing_low = min(k.low for k in k1h[-15:])
    entry_price = current_price

    if side == "Long":
        min_stop_distance = entry_price * 0.008  # حد أدنى مطلق 0.8% وقائي
        stop_loss = min(recent_swing_low, entry_price - atr_val * 1.2, entry_price - min_stop_distance)
        risk = entry_price - stop_loss
        take_profit = entry_price + risk * 2.5
    else:
        min_stop_distance = entry_price * 0.008
        stop_loss = max(recent_swing_high, entry_price + atr_val * 1.2, entry_price + min_stop_distance)
        risk = stop_loss - entry_price
        take_profit = entry_price - risk * 2.5

    if risk <= 0:
        return None
    reward = abs(take_profit - entry_price)
    rr = round(reward / risk, 2)
    _log("عائد/مخاطرة", f"1:{rr}")
    if rr < 2.0:
        _log("❌ فلتر أدنى عائد/مخاطرة (1:2)", f"1:{rr} غير كافٍ — رفض", False)
        return None

    # قوة التناقض نفسها هي مصدر الثقة الرئيسي هنا (بدل تأكيد نمط سعري)
    probability = 73
    extreme_ratio = long_short_ratio if side == "Short" else (1.0 / long_short_ratio if long_short_ratio > 0 else 0)
    if extreme_ratio > 2.5:
        probability += 6  # ازدحام حشد متطرف جداً = فرصة عصر أقوى
    if (side == "Short" and cvd_pct < 35) or (side == "Long" and cvd_pct > 65):
        probability += 6  # التدفق الحقيقي حاسم جداً بعكس الحشد
    if (side == "Short" and taker_pressure < -0.15) or (side == "Long" and taker_pressure > 0.15):
        probability += 5

    oi_change_pct = micro.oi_change_pct
    if oi_change_pct is not None:
        _log("تغيّر الفائدة المفتوحة (OI)", f"{oi_change_pct:.2f}%")
        if (side == "Short" and oi_change_pct > 1.5) or (side == "Long" and oi_change_pct < -1.5):
            # فائدة مفتوحة ترتفع بنفس اتجاه التناقض = مراكز جديدة تدخل تؤكد الفرضية
            probability += 4

    probability = max(70, min(95, probability))
    _log("✅ القرار النهائي", f"{side} — مصيدة حشد مكتشفة", True)

    behavior = f"🎭 مصيدة الحشد: {crowd_desc}. دخول {side} عند {entry_price:.6g} بناءً على التناقض، بدون الاعتماد على أي نمط سعري."
    volume_analysis = "تناقض Long/Short Ratio + Funding مقابل CVD + Taker Pressure — استراتيجية أصيلة بدون نمط سعري"

    return AnalysisResult(
        symbol=symbol, trend=("صاعد" if side == "Long" else "هابط"), dt="", prob=probability,
        price=current_price, atr=atr_val, side=side, entry_price=entry_price, stop_loss=stop_loss,
        take_profit=take_profit, rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
