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

# مفتاح عام (يُضبط من scanner.py أو صفحة التشخيص حسب إعدادات المستخدم) لتجاهل قيد
# جلسة التداول (Kill Zone) — الافتراضي هو التقيّد بالجلسة (False) كما بالتصميم الأصلي.
_ignore_kill_zone = False


def set_ignore_kill_zone(value: bool):
    global _ignore_kill_zone
    _ignore_kill_zone = bool(value)


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


def _pick_cluster_entry(fib618: float, fvg_price: Optional[float], hv_price: Optional[float],
                         swing_range: float) -> tuple:
    """يكشف تجمّع (Cluster) حقيقي بين نقاط الدخول المرشحة بدل خلطها دائماً بشكل مصطنع.
    فيبوناتشي 61.8% هو المرجع الأساسي الثابت دائماً. أي نقطة إضافية (FVG أو Volume Node)
    تُضاف للتجميع فقط لو كانت **قريبة فعلياً** من فيبوناتشي (تقارب حقيقي = كلاستر قوي)،
    وإلا تُهمَل تماماً بدل ما تُخلط بنقطة بعيدة تشوّه الدخول لمكان غير منطقي.
    ترجع: (نقطة الدخول النهائية، قائمة أسماء النقاط المتجمّعة، عدد نقاط التجمّع)."""
    tolerance = max(swing_range * 0.08, 1e-9)  # نطاق تقارب معقول نسبة لحجم الحركة الكلي
    cluster_prices = [fib618]
    cluster_labels = ["فيبوناتشي الذهبي 61.8%"]

    if fvg_price is not None and abs(fvg_price - fib618) <= tolerance:
        cluster_prices.append(fvg_price)
        cluster_labels.append("فجوة سعرية (FVG)")
    if hv_price is not None and abs(hv_price - fib618) <= tolerance:
        cluster_prices.append(hv_price)
        cluster_labels.append("أعلى حجم تداول (Volume Node)")

    entry_price = sum(cluster_prices) / len(cluster_prices)
    return entry_price, cluster_labels, len(cluster_prices)


def analyze_ict_smart_sweep(symbol: str, k4h: List[Kline], k1h: List[Kline], k15m: List[Kline],
                             k5m: List[Kline], k_daily: List[Kline],
                             micro: Optional[MarketMicrostructure] = None,
                             trace: Optional[list] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    if not k4h or not k1h or not k15m:
        return None
    if len(k1h) < 26 or len(k15m) < 15:
        _log("عدد الشموع كافٍ (1س≥26، 15د≥15)", f"1س={len(k1h)}, 15د={len(k15m)}", False)
        return None
    _log("عدد الشموع كافٍ", f"1س={len(k1h)}, 15د={len(k15m)}", True)

    last_price = k15m[-1].close
    atr_val = atr(k1h, 14)
    if atr_val <= 0 or last_price <= 0:
        return None

    # فلتر جلسة التداول (Kill Zone) — الاستراتيجية الأصلية تشترطه لكل إشارة، إلا لو
    # فعّل المستخدم خيار تجاهل الجلسة صراحة من الإعدادات
    if not _ignore_kill_zone and not in_kill_zone():
        _log("❌ فلتر جلسة التداول (Kill Zone)", "الوقت الحالي خارج نطاق جلسات لندن/نيويورك النشطة — رفض إلزامي", False)
        return None
    _log("✅ جلسة تداول نشطة (Kill Zone)" if not _ignore_kill_zone else "▫️ قيد الجلسة (Kill Zone)",
         "داخل النطاق" if not _ignore_kill_zone else "متجاهَل حسب إعدادات المستخدم — يشتغل بأي وقت", True)

    # 1) الاتجاه العام على 4 ساعات
    trend4h = _get_bias(k4h)
    if trend4h not in ("صاعد", "هابط"):
        closes4h = [k.close for k in k4h]
        sma20 = sum(closes4h[-20:]) / min(20, len(closes4h))
        sma50 = sum(closes4h[-50:]) / min(50, len(closes4h))
        trend4h = "صاعد" if sma20 > sma50 else "هابط"

    coin_trend = daily_trend(k_daily) if k_daily else trend4h
    market_trend_value = 70 if coin_trend == "صاعد" else 30
    _log("اتجاه 4 ساعات", trend4h)
    _log("اتجاه العملة اليومي", coin_trend)

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

    _log("سحب سيولة صاعد (1H)", is_bullish_sweep)
    _log("سحب سيولة هابط (1H)", is_bearish_sweep)

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

    _log("فجوة سعرية صاعدة (FVG)", bullish_fvg_exists)
    _log("فجوة سعرية هابطة (FVG)", bearish_fvg_exists)

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

    _log("كسر هيكل صاعد (CHoCH)", bullish_choch)
    _log("كسر هيكل هابط (CHoCH)", bearish_choch)

    is_long_setup = (trend4h == "صاعد" and is_bullish_sweep and bullish_fvg_exists and bullish_choch
                      and coin_trend == "صاعد" and market_trend_value >= 50)
    is_short_setup = (trend4h == "هابط" and is_bearish_sweep and bearish_fvg_exists and bearish_choch
                       and coin_trend == "هابط" and market_trend_value < 50)

    matched = is_long_setup or is_short_setup
    if not matched:
        _log("❌ القرار النهائي", "لم تتحقق كل الشروط الخمسة بنفس الوقت (اتجاه 4س + سحب سيولة + FVG + CHoCH + اتجاه يومي) — هذا سبب الرفض", False)
        return None

    side = "Long" if is_long_setup else "Short"
    if trace is not None:
        trace.append({"check": "✅ كل الشروط الخمسة تحققت بنفس الوقت", "value": side, "ok": True})

    if side == "Long":
        swing_low = sweep_low_value if sweep_low_value > 0 else min(k.low for k in k15m[-20:])
        swing_high = max(k.high for k in k15m[-20:])
        swing_range = swing_high - swing_low
        if swing_range <= 0:
            return None

        fib50 = swing_high - 0.50 * swing_range
        fib618 = swing_high - 0.618 * swing_range
        fib786 = swing_high - 0.786 * swing_range

        fvg_price = _find_fvg_in_zone(k15m, fib786, fib50, bullish=True)
        hv_price = _find_high_volume_node(k15m, fib786, fib50)

        entry_price, cluster_labels, cluster_count = _pick_cluster_entry(fib618, fvg_price, hv_price, swing_range)
        if entry_price > last_price * 1.01 or entry_price < last_price * 0.95:
            entry_price = last_price * 0.995
            cluster_labels, cluster_count = ["فيبوناتشي الذهبي 61.8% (بعيد عن السعر — تم تعديل الدخول)"], 1

        sl = swing_low - (atr_val * 0.15)
        if sl >= entry_price:
            sl = entry_price - (atr_val * 0.5)

        risk = entry_price - sl
        tp = entry_price + 3.0 * risk
        cluster_txt = " + ".join(cluster_labels)
        behavior = (f"🧠 النمط الذكي: نقطة دخول من التقاء {cluster_count} عنصر [{cluster_txt}] "
                    f"بعد سحب سيولة قاع 1H ({swing_low:.6g}) وتأكيد كسر الاتجاه CHoCH صاعد فريم 15د.")
        _log("📍 عدد نقاط تجمّع الدخول (Cluster)", f"{cluster_count} — [{cluster_txt}]", cluster_count >= 2)
    else:
        swing_high = sweep_high_value if sweep_high_value > 0 else max(k.high for k in k15m[-20:])
        swing_low = min(k.low for k in k15m[-20:])
        swing_range = swing_high - swing_low
        if swing_range <= 0:
            return None

        fib50 = swing_low + 0.50 * swing_range
        fib618 = swing_low + 0.618 * swing_range
        fib786 = swing_low + 0.786 * swing_range

        fvg_price = _find_fvg_in_zone(k15m, fib50, fib786, bullish=False)
        hv_price = _find_high_volume_node(k15m, fib50, fib786)

        entry_price, cluster_labels, cluster_count = _pick_cluster_entry(fib618, fvg_price, hv_price, swing_range)
        if entry_price < last_price * 0.99 or entry_price > last_price * 1.05:
            entry_price = last_price * 1.005
            cluster_labels, cluster_count = ["فيبوناتشي الذهبي 61.8% (بعيد عن السعر — تم تعديل الدخول)"], 1

        sl = swing_high + (atr_val * 0.15)
        if sl <= entry_price:
            sl = entry_price + (atr_val * 0.5)

        risk = sl - entry_price
        tp = entry_price - 3.0 * risk
        cluster_txt = " + ".join(cluster_labels)
        behavior = (f"🧠 النمط الذكي: نقطة دخول من التقاء {cluster_count} عنصر [{cluster_txt}] "
                    f"بعد سحب سيولة قمة 1H ({swing_high:.6g}) وتأكيد كسر الاتجاه CHoCH هابط فريم 15د.")
        _log("📍 عدد نقاط تجمّع الدخول (Cluster)", f"{cluster_count} — [{cluster_txt}]", cluster_count >= 2)

    if risk <= 0:
        return None

    # ربط بيانات البنية الجزئية (OI / CVD / ضغط المتداولين) بالاستراتيجية — لم تكن
    # مستخدمة هنا سابقاً رغم توفرها، وهذا يقوّي جودة الفلترة بنفس روح الاستراتيجية الأولى
    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct < -1.5:
        # فائدة مفتوحة تنخفض بقوة أثناء إعداد الصفقة = سيولة تخرج من السوق، إشارة ضعف حقيقي
        _log("❌ فلتر الفائدة المفتوحة (OI)", f"تغيّر OI={oi_change_pct:.2f}% (أقل من -1.5%) — رفض", False)
        return None

    probability = 72
    if is_long_setup or is_short_setup:
        probability += 8
    if bullish_fvg_exists or bearish_fvg_exists:
        probability += 3
    if bullish_choch or bearish_choch:
        probability += 3

    # مكافأة تجمّع نقاط الدخول (Cluster) — كلما اجتمعت نقاط أكثر بنفس المكان، الثقة أعلى
    if cluster_count >= 3:
        probability += 8  # فيبوناتشي + FVG + Volume Node اجتمعوا معاً — تقاطع قوي جداً
    elif cluster_count == 2:
        probability += 4  # عنصرين اجتمعا معاً — تقاطع جيد

    if oi_change_pct is not None and oi_change_pct > 1.5:
        probability += 4  # فائدة مفتوحة ترتفع = دخول سيولة/أموال جديدة حقيقية تدعم الاختراق

    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        taker_aligned = (side == "Long" and taker_pressure > 0.15) or (side == "Short" and taker_pressure < -0.15)
        if taker_aligned:
            probability += 3

    cvd_pct = micro.cvd_pct if micro else None
    if cvd_pct is not None:
        cvd_aligned = (side == "Long" and cvd_pct > 60) or (side == "Short" and cvd_pct < 40)
        if cvd_aligned:
            probability += 3

    probability = min(95, probability)

    rr = round(abs(tp - entry_price) / risk, 2) if risk > 0 else 3.0
    volume_analysis = "سحب سيولة + فجوة سعرية (FVG) + كسر هيكل (CHoCH) — استراتيجية ICT/SMC"
    if oi_change_pct is not None:
        behavior += f" | 📊 تغيّر الفائدة المفتوحة (OI): {oi_change_pct:.2f}%"
    if cvd_pct is not None:
        behavior += f" | 📈 CVD تراكمي (24س): {cvd_pct:.1f}% شراء"

    return AnalysisResult(
        symbol=symbol, trend=trend4h, dt="", prob=probability, price=last_price,
        atr=atr_val, side=side, entry_price=entry_price, stop_loss=sl, take_profit=tp,
        rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
