"""
الاستراتيجية الثانية: النمط الذكي لسحب السيولة (Smart Liquidity Sweep - ICT/SMC)
منقولة بأمانة عن smartLiquiditySweepAnalysis() في OrionAnalyzer.kt الأصلي — دالة كاملة
وجاهزة كانت موجودة بالكود لكن غير مفعّلة (dead code)، لأن التطبيق كان يعتمد حصرياً على
"Explosive Breakout Hunter". هذا الملف يفعّلها كاستراتيجية ثانية بديلة قابلة للاختيار.

فكرة الاستراتيجية (بنفس أسلوب ICT / Smart Money Concepts):
  1) اتجاه عام على فريم 4 ساعات (Bias)
  2) سحب سيولة (Liquidity Sweep) على فريم الساعة — كسر أدنى/أعلى سعر سابق ثم الإغلاق
     فوقه/تحته مباشرة (فخ سيولة كلاسيكي)
  3) فجوة سعرية (FVG - Fair Value Gap) تتشكل مباشرة بعد السحب على فريم الساعة
  4) كسر هيكل (CHoCH) يتأكد على فريم 15 دقيقة
  5) نقطة الدخول تُحسب من تقاطع: المنطقة الذهبية لفيبوناتشي (61.8%) + منتصف FVG على
     15 دقيقة داخل نفس المنطقة + أعلى شمعة حجم تداول (Volume Node) داخل نفس المنطقة
  6) الهدف يُحسب بعائد/مخاطرة ثابت 1:3

ملاحظة على التبسيط: النسخة الأصلية بالكوتلن كانت تستقبل "اتجاه العملة" و"قيمة اتجاه
السوق العام" كمعاملين خارجيين (كانت تُحسب على الأرجح من ارتباط العملة بالبيتكوين
والسوق ككل بمكان آخر من التطبيق لم يتوفر لنا الوصول له). هنا نشتقهما داخلياً من
الاتجاه اليومي لنفس العملة (نفس دالة daily_trend المستخدمة بالاستراتيجية الأولى)
حفاظاً على نفس منطق "تأكيد الاتجاه متعدد الأطر الزمنية" دون الحاجة لبيانات خارجية إضافية.
"""
from __future__ import annotations

from typing import List, Optional

from .analyzer import (
    Kline, AnalysisResult, MarketMicrostructure,
    atr, _get_bias, daily_trend, in_kill_zone,
)


def _find_fvg_in_zone(klines: List[Kline], zone_low: float, zone_high: float, bullish: bool) -> Optional[float]:
    """يبحث عن فجوة سعرية (FVG) تقع مركزها داخل المنطقة الذهبية المحددة."""
    if len(klines) < 10:
        return None
    recent = klines[-15:]
    for i in range(1, len(recent) - 1):
        c1, c3 = recent[i - 1], recent[i + 1]
        if bullish:
            if c3.low > c1.high:
                mid = (c1.high + c3.low) / 2.0
                if zone_low <= mid <= zone_high:
                    return mid
        else:
            if c1.low > c3.high:
                mid = (c1.low + c3.high) / 2.0
                if zone_low <= mid <= zone_high:
                    return mid
    return None


def _find_high_volume_node(klines: List[Kline], zone_low: float, zone_high: float) -> Optional[float]:
    """يبحث عن أعلى شمعة من حيث حجم التداول ومنتصفها يقع داخل المنطقة الذهبية."""
    if len(klines) < 10:
        return None
    window = klines[-20:]
    max_candle = max(window, key=lambda k: k.volume)
    mid = (max_candle.high + max_candle.low) / 2.0
    return mid if zone_low <= mid <= zone_high else None


def analyze_ict_smart_sweep(symbol: str, k4h: List[Kline], k1h: List[Kline], k15m: List[Kline],
                             k5m: List[Kline], k_daily: List[Kline],
                             micro: Optional[MarketMicrostructure] = None) -> Optional[AnalysisResult]:
    if not k4h or not k1h or not k15m:
        return None
    if len(k1h) < 26 or len(k15m) < 15:
        return None

    last_price = k15m[-1].close
    atr_val = atr(k1h, 14)
    if atr_val <= 0 or last_price <= 0:
        return None

    # فلتر جلسة التداول (Kill Zone) — الاستراتيجية الأصلية تشترطه لكل إشارة
    if not in_kill_zone():
        return None

    # 1) الاتجاه العام على 4 ساعات
    trend4h = _get_bias(k4h)
    if trend4h not in ("صاعد", "هابط"):
        closes4h = [k.close for k in k4h]
        sma20 = sum(closes4h[-20:]) / min(20, len(closes4h))
        sma50 = sum(closes4h[-50:]) / min(50, len(closes4h))
        trend4h = "صاعد" if sma20 > sma50 else "هابط"

    coin_trend = daily_trend(k_daily) if k_daily else trend4h
    market_trend_value = 70 if coin_trend == "صاعد" else 30

    # 2) سحب سيولة على فريم الساعة
    is_bullish_sweep = False
    sweep_low_value = 0.0
    prev_1h = k1h[:-2]
    if len(prev_1h) >= 24:
        prev_min_low = min(k.low for k in prev_1h[-24:])
        last2 = k1h[-2:]
        min_low_recent = min(k.low for k in last2)
        max_close_recent = max(k.close for k in last2)
        if min_low_recent < prev_min_low and max_close_recent > prev_min_low:
            is_bullish_sweep = True
            sweep_low_value = min_low_recent

    is_bearish_sweep = False
    sweep_high_value = 0.0
    if len(prev_1h) >= 24:
        prev_max_high = max(k.high for k in prev_1h[-24:])
        last2 = k1h[-2:]
        max_high_recent = max(k.high for k in last2)
        min_close_recent = min(k.close for k in last2)
        if max_high_recent > prev_max_high and min_close_recent < prev_max_high:
            is_bearish_sweep = True
            sweep_high_value = max_high_recent

    # 3) فجوة سعرية (FVG) مباشرة بعد السحب على فريم الساعة
    bullish_fvg_exists = False
    bearish_fvg_exists = False
    if len(k1h) >= 5:
        recent_1h = k1h[-5:]
        for i in range(1, len(recent_1h) - 1):
            c1, c3 = recent_1h[i - 1], recent_1h[i + 1]
            min_gap = recent_1h[i].close * 0.0004
            if c3.low - c1.high >= min_gap:
                bullish_fvg_exists = True
            if c1.low - c3.high >= min_gap:
                bearish_fvg_exists = True

    # 4) كسر الهيكل (CHoCH) على فريم 15 دقيقة
    bullish_choch = False
    bearish_choch = False
    if len(k15m) >= 15:
        last_close = k15m[-1].close
        prev_15m = k15m[:-2]
        if len(prev_15m) >= 12:
            prev_swing_high = max(k.high for k in prev_15m[-12:])
            prev_swing_low = min(k.low for k in prev_15m[-12:])
            if last_close > prev_swing_high:
                bullish_choch = True
            if last_close < prev_swing_low:
                bearish_choch = True

    is_long_setup = (trend4h == "صاعد" and is_bullish_sweep and bullish_fvg_exists and bullish_choch
                      and coin_trend == "صاعد" and market_trend_value >= 50)
    is_short_setup = (trend4h == "هابط" and is_bearish_sweep and bearish_fvg_exists and bearish_choch
                       and coin_trend == "هابط" and market_trend_value < 50)

    matched = is_long_setup or is_short_setup
    if not matched:
        return None

    side = "Long" if is_long_setup else "Short"

    if side == "Long":
        swing_low = sweep_low_value if sweep_low_value > 0 else min(k.low for k in k15m[-20:])
        swing_high = max(k.high for k in k15m[-20:])
        swing_range = swing_high - swing_low
        if swing_range <= 0:
            return None

        fib50 = swing_high - 0.50 * swing_range
        fib618 = swing_high - 0.618 * swing_range
        fib786 = swing_high - 0.786 * swing_range

        fvg_price = _find_fvg_in_zone(k15m, fib786, fib50, bullish=True) or fib618
        hv_price = _find_high_volume_node(k15m, fib786, fib50) or fib618

        entry_price = (fib618 * 0.4) + (fvg_price * 0.3) + (hv_price * 0.3)
        if entry_price > last_price * 1.01 or entry_price < last_price * 0.95:
            entry_price = last_price * 0.995

        sl = swing_low - (atr_val * 0.15)
        if sl >= entry_price:
            sl = entry_price - (atr_val * 0.5)

        risk = entry_price - sl
        tp = entry_price + 3.0 * risk
        behavior = (f"🧠 النمط الذكي: تجميع كلاستر [فيبوناتشي الذهبي 61.8% + جاب فجوة سعرية FVG + "
                    f"تكتل حجم فوليوم مرتفع] بعد سحب سيولة قاع 1H ({swing_low:.6g}) وتأكيد كسر الاتجاه CHoCH صاعد فريم 15د.")
    else:
        swing_high = sweep_high_value if sweep_high_value > 0 else max(k.high for k in k15m[-20:])
        swing_low = min(k.low for k in k15m[-20:])
        swing_range = swing_high - swing_low
        if swing_range <= 0:
            return None

        fib50 = swing_low + 0.50 * swing_range
        fib618 = swing_low + 0.618 * swing_range
        fib786 = swing_low + 0.786 * swing_range

        fvg_price = _find_fvg_in_zone(k15m, fib50, fib786, bullish=False) or fib618
        hv_price = _find_high_volume_node(k15m, fib50, fib786) or fib618

        entry_price = (fib618 * 0.4) + (fvg_price * 0.3) + (hv_price * 0.3)
        if entry_price < last_price * 0.99 or entry_price > last_price * 1.05:
            entry_price = last_price * 1.005

        sl = swing_high + (atr_val * 0.15)
        if sl <= entry_price:
            sl = entry_price + (atr_val * 0.5)

        risk = sl - entry_price
        tp = entry_price - 3.0 * risk
        behavior = (f"🧠 النمط الذكي: تجميع كلاستر [فيبوناتشي الذهبي 61.8% + جاب فجوة سعرية FVG + "
                    f"تكتل حجم فوليوم مرتفع] بعد سحب سيولة قمة 1H ({swing_high:.6g}) وتأكيد كسر الاتجاه CHoCH هابط فريم 15د.")

    if risk <= 0:
        return None

    probability = 82
    if is_long_setup or is_short_setup:
        probability += 8
    if bullish_fvg_exists or bearish_fvg_exists:
        probability += 3
    if bullish_choch or bearish_choch:
        probability += 3
    probability = min(95, probability)

    rr = round(abs(tp - entry_price) / risk, 2) if risk > 0 else 3.0
    volume_analysis = "سحب سيولة + فجوة سعرية (FVG) + كسر هيكل (CHoCH) — استراتيجية ICT/SMC"

    return AnalysisResult(
        symbol=symbol, trend=trend4h, dt="", prob=probability, price=last_price,
        atr=atr_val, side=side, entry_price=entry_price, stop_loss=sl, take_profit=tp,
        rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
