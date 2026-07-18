"""
استراتيجية انعكاس مناطق العرض والطلب (Supply/Demand Reversal).

الفكرة (بناءً على ملاحظة حقيقية من السوق): كثير من إشارات "الانفجار السعري" فعلياً
تكون فخ سيولة (Liquidity Grab) — السعر يتحرك باتجاه معين بقوة (زي ما يقول الانفجار
السعري)، فقط عشان يوصل لأقرب منطقة عرض (Supply) أو طلب (Demand) قوية، ياخذ سيولتها،
ثم **ينعكس فعلياً للاتجاه المعاكس تماماً**.

لذلك هذي الاستراتيجية:
  1) تستخدم "الانفجار السعري" كمُطلِق فقط — يخبرنا إن فيه تحرك/حدث سيولة يحصل الآن.
  2) تبحث عن أقرب منطقة عرض/طلب "طازجة" (لم تُختبر أو تُخترق من قبل) بجهة اتجاه
     الانفجار السعري:
       - لو الانفجار السعري قال Long → نبحث عن أقرب منطقة عرض (Supply) فوق السعر
         الحالي (المكان المتوقع يروح له السعر وياخذ سيولته وينعكس هابط).
       - لو الانفجار السعري قال Short → نبحث عن أقرب منطقة طلب (Demand) تحت السعر
         الحالي (نفس الفكرة بالعكس).
  3) الدخول الفعلي يكون **من داخل تلك المنطقة**، **بعكس اتجاه الانفجار السعري تماماً**.
  4) لو ما فيه منطقة عرض/طلب طازجة بمسافة معقولة، نرفض الفرصة بالكامل — بدون منطقة
     واضحة، ما فيه أساس منطقي للانعكاس.

اكتشاف مناطق العرض/الطلب: منطقة "قاعدة" (شمعة أو نطاق ضيق) تلتها حركة اندفاعية قوية
مبتعدة عنها (على فريم الساعة) — هذا هو التعريف الكلاسيكي لمنطقة العرض/الطلب في تحليل
السيولة الذكي، وهو نفس مفهوم الـ Order Block تقريباً.
"""
from typing import List, Optional, Dict

from .analyzer import analyze, atr, AnalysisResult, MarketMicrostructure, Kline


def _detect_zones(klines: List[Kline], lookback: int = 80) -> List[Dict]:
    """يكتشف مناطق العرض/الطلب: قاعدة ضيقة تلتها حركة اندفاعية قوية مبتعدة عنها."""
    zones = []
    n = len(klines)
    if n < 20:
        return zones
    atr_val = atr(klines, 14)
    if atr_val <= 0:
        return zones

    start = max(1, n - lookback)
    for i in range(start, n - 3):
        base = klines[i]
        base_range = base.high - base.low
        if base_range <= 0 or base_range > atr_val * 1.2:
            continue  # القاعدة لازم تكون ضيقة نسبياً (تجميع، مو حركة عشوائية واسعة)

        c1, c2, c3 = klines[i + 1], klines[i + 2], klines[i + 3]
        move_up = c3.close - base.close
        move_down = base.close - c3.close

        if move_up > atr_val * 2.0 and c1.close > base.high and c2.close >= c1.close:
            zones.append({"type": "demand", "low": base.low, "high": base.high, "index": i})
        elif move_down > atr_val * 2.0 and c1.close < base.low and c2.close <= c1.close:
            zones.append({"type": "supply", "low": base.low, "high": base.high, "index": i})

    return zones


def _is_zone_valid(zone: Dict, klines: List[Kline], atr_val: float) -> bool:
    """منطقة 'صالحة' = يسمح بأي عدد اختبارات/ارتدادات سابقة منها (حتى لو دخل السعر
    داخلها أو لامس حدودها أكثر من مرة)، وتُلغى فقط لو **انكسرت فعلياً وبشكل مؤكد**:
    إغلاق شمعة (مو مجرد ظل/Wick) يتجاوز حدّها الخارجي بهامش أمان (0.15×ATR) لتفادي
    إلغاء المنطقة بسبب اختراق هامشي بسيط لا يمثل انكساراً حقيقياً."""
    buffer = atr_val * 0.15
    for k in klines[zone["index"] + 4:]:
        if zone["type"] == "supply" and k.close > zone["high"] + buffer:
            return False
        if zone["type"] == "demand" and k.close < zone["low"] - buffer:
            return False
    return True


def analyze_supply_demand_reversal(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                                    micro: Optional[MarketMicrostructure] = None,
                                    trace: Optional[list] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    # الخطوة 1: الانفجار السعري كمُطلِق فقط — يخبرنا إن فيه حدث/زخم يحصل الآن
    breakout_trace: list = []
    breakout_result = analyze(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro, trace=breakout_trace)
    if trace is not None:
        trace.append({"check": "── ⚡ المرحلة 1: كاشف الحدث (الانفجار السعري) ──", "value": "", "ok": None})
        trace.extend(breakout_trace)
    if breakout_result is None:
        _log("❌ القرار النهائي", "ما فيه أي حدث انفجار سعري أصلاً — توقفنا هنا", False)
        return None

    atr_val = atr(k1h, 14)
    if atr_val <= 0 or not k1h:
        return None

    current_price = k5m[-1].close if k5m else k1h[-1].close
    zones = _detect_zones(k1h)
    fresh_zones = [z for z in zones if _is_zone_valid(z, k1h, atr_val)]
    _log("مناطق عرض/طلب مكتشفة (خام)", len(zones))
    _log("مناطق عرض/طلب صالحة (لم تنكسر)", len(fresh_zones))

    if breakout_result.side == "Long":
        # نتوقع فخ سيولة صاعد → نبحث عن أقرب منطقة عرض (Supply) فوق السعر الحالي للدخول Short منها
        candidates = [z for z in fresh_zones if z["type"] == "supply" and z["low"] > current_price]
        _log("مناطق عرض (Supply) فوق السعر الحالي", len(candidates), len(candidates) > 0)
        if not candidates:
            _log("❌ القرار النهائي", "ما فيه منطقة عرض طازجة فوق السعر لعكس اتجاه الانفجار الصاعد — رفض", False)
            return None
        nearest = min(candidates, key=lambda z: z["low"] - current_price)
        distance = nearest["low"] - current_price
        _log("مسافة أقرب منطقة عرض (نسبة لـ ATR)", f"{distance/atr_val:.2f}x" if atr_val else "n/a")
        if distance > atr_val * 6 or distance < atr_val * 0.3:
            _log("❌ فلتر مسافة المنطقة المنطقية (0.3x–6x ATR)", f"{distance/atr_val:.2f}x خارج النطاق المسموح — رفض", False)
            return None  # المسافة غير منطقية (بعيدة جداً أو قريبة جداً بلا معنى)

        entry_price = (nearest["low"] + nearest["high"]) / 2.0
        sl = nearest["high"] + atr_val * 0.3
        risk = sl - entry_price
        tp = entry_price - 3.0 * risk
        side = "Short"
        zone_desc = f"منطقة عرض (Supply) عند {nearest['low']:.6g}–{nearest['high']:.6g}"
    else:
        # نتوقع فخ سيولة هابط → نبحث عن أقرب منطقة طلب (Demand) تحت السعر الحالي للدخول Long منها
        candidates = [z for z in fresh_zones if z["type"] == "demand" and z["high"] < current_price]
        _log("مناطق طلب (Demand) تحت السعر الحالي", len(candidates), len(candidates) > 0)
        if not candidates:
            _log("❌ القرار النهائي", "ما فيه منطقة طلب طازجة تحت السعر لعكس اتجاه الانفجار الهابط — رفض", False)
            return None
        nearest = min(candidates, key=lambda z: current_price - z["high"])
        distance = current_price - nearest["high"]
        _log("مسافة أقرب منطقة طلب (نسبة لـ ATR)", f"{distance/atr_val:.2f}x" if atr_val else "n/a")
        if distance > atr_val * 6 or distance < atr_val * 0.3:
            _log("❌ فلتر مسافة المنطقة المنطقية (0.3x–6x ATR)", f"{distance/atr_val:.2f}x خارج النطاق المسموح — رفض", False)
            return None

        entry_price = (nearest["low"] + nearest["high"]) / 2.0
        sl = nearest["low"] - atr_val * 0.3
        risk = entry_price - sl
        tp = entry_price + 3.0 * risk
        side = "Long"
        zone_desc = f"منطقة طلب (Demand) عند {nearest['low']:.6g}–{nearest['high']:.6g}"

    if risk <= 0:
        return None

    probability = 76 + (5 if breakout_result.prob >= 80 else 0)
    probability = min(92, probability)
    rr = round(abs(tp - entry_price) / risk, 2) if risk > 0 else 3.0

    _log("✅ القرار النهائي", f"{side} من {zone_desc}", True)

    behavior = (
        f"🔄 انعكاس عرض/طلب: رصد الانفجار السعري إشارة {breakout_result.side} أولية "
        f"(احتمال فخ سيولة)، فتم البحث عن أقرب {zone_desc} لعكس الاتجاه والدخول {side} "
        f"من داخل المنطقة، توقعاً لسحب السيولة من هناك ثم الانعكاس الحقيقي.\n\n"
        f"⚡ [إشارة الفخ الأولية]: {breakout_result.behavior}"
    )
    volume_analysis = f"دخول عكسي من {zone_desc} بعد فخ سيولة محتمل من الانفجار السعري"

    return AnalysisResult(
        symbol=symbol, trend=breakout_result.trend, dt="", prob=probability, price=current_price,
        atr=atr_val, side=side, entry_price=entry_price, stop_loss=sl, take_profit=tp,
        rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
