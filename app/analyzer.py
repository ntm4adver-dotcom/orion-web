"""
Orion Analyzer — Explosive Breakout Hunter Strategy
منقول بأمانة عن app/src/main/java/com/example/analyzer/OrionAnalyzer.kt
(الدالة النشطة الوحيدة فعلياً في التطبيق الأصلي هي analyzeExplosiveBreakout،
حسب تعليق صريح في الكود الأصلي: "Relying strictly on Explosive Breakout Hunter
as the sole strategy!"). هذا الملف يحافظ على نفس المعادلات والعتبات الرقمية
تماماً كما في نسخة Kotlin الأصلية.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Kline:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


@dataclass
class MarketMicrostructure:
    oi_change_pct: Optional[float] = None
    funding_rate: Optional[float] = None
    ob_imbalance: Optional[float] = None
    taker_pressure: Optional[float] = None    # ضغط المتداولين الفعليين اللحظي (-1 بيع كامل .. 1 شراء كامل)
    long_short_ratio: Optional[float] = None  # نسبة تمركز الحسابات (>1 أغلبية شراء، <1 أغلبية بيع)
    cvd_pct: Optional[float] = None           # CVD تراكمي 24 ساعة: 0%=بيع كامل, 50%=تعادل, 100%=شراء كامل


@dataclass
class AnalysisResult:
    symbol: str
    trend: str
    dt: str
    prob: int
    price: float
    atr: float
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rr: float
    quality: str
    conf: int
    behavior: str
    volume_analysis: str
    low_vol: bool
    kill_zone_ok: bool
    news_time: bool
    ranging: bool
    tp1: float = 0.0


# ---------------------------------------------------------------------------
# Basic indicators
# ---------------------------------------------------------------------------

def atr(klines: List[Kline], period: int = 14) -> float:
    if len(klines) <= period:
        return 0.0
    tr_list = []
    for i in range(1, len(klines)):
        cur, prev = klines[i], klines[i - 1]
        tr1 = cur.high - cur.low
        tr2 = abs(cur.high - prev.close)
        tr3 = abs(cur.low - prev.close)
        tr_list.append(max(tr1, tr2, tr3))
    if len(tr_list) < period:
        return 0.0
    return sum(tr_list[-period:]) / period


def bollinger_bands(closes: List[float], period: int = 20, num_std: float = 2.0):
    if len(closes) < period:
        last = closes[-1] if closes else 0.0
        return last, last, last
    recent = closes[-period:]
    basis = sum(recent) / period
    variance = sum((p - basis) ** 2 for p in recent) / period
    std_dev = variance ** 0.5
    dev = num_std * std_dev
    return basis, basis + dev, basis - dev


def calculate_obv(klines: List[Kline]) -> List[float]:
    if not klines:
        return []
    obv = [klines[0].volume]
    for i in range(1, len(klines)):
        cur, prev = klines[i], klines[i - 1]
        last = obv[-1]
        if cur.close > prev.close:
            obv.append(last + cur.volume)
        elif cur.close < prev.close:
            obv.append(last - cur.volume)
        else:
            obv.append(last)
    return obv


def check_obv_divergence(klines: List[Kline]) -> bool:
    if len(klines) < 10:
        return False
    obv = calculate_obv(klines)
    if len(obv) < 10:
        return False
    recent_k = klines[-10:]
    recent_obv = obv[-10:]
    price_start, price_end = recent_k[0].close, recent_k[-1].close
    obv_start, obv_end = recent_obv[0], recent_obv[-1]
    bullish_div = (price_end <= price_start * 1.005) and (obv_end > obv_start * 1.1)
    bearish_div = (price_end >= price_start * 0.995) and (obv_end < obv_start * 0.9)
    return bullish_div or bearish_div


def ema(closes: List[float], span: int) -> float:
    if len(closes) < span:
        return closes[-1] if closes else 0.0
    alpha = 2.0 / (span + 1)
    ema_val = sum(closes[:span]) / span
    for i in range(span, len(closes)):
        ema_val = (closes[i] * alpha) + (ema_val * (1 - alpha))
    return ema_val


def rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def in_kill_zone() -> bool:
    from datetime import datetime, timezone
    hour = datetime.now(timezone.utc).hour
    return (7 <= hour <= 10) or (12 <= hour <= 16)


def low_vol(klines: List[Kline]) -> bool:
    if len(klines) < 40:
        return False
    last30 = klines[-30:]
    p = klines[-1].close
    if p <= 0.0:
        return False
    mean_range = sum(k.high - k.low for k in last30) / len(last30)
    return (mean_range / p) < 0.004


def check_irrational_market(k5m: List[Kline], k15m: List[Kline], k1h: List[Kline]) -> bool:
    if len(k5m) < 10 or len(k15m) < 10:
        return False
    for k in k5m[-2:]:
        if k.open and (k.high - k.low) / k.open > 0.035:
            return True
    for k in k15m[-2:]:
        if k.open and (k.high - k.low) / k.open > 0.05:
            return True
    latest5m = k5m[-1]
    prev5m = k5m[:-1][-10:]
    if prev5m:
        avg_vol = sum(k.volume for k in prev5m) / len(prev5m)
        range_pct = (latest5m.high - latest5m.low) / latest5m.open if latest5m.open else 0
        if latest5m.volume > avg_vol * 5.5 and range_pct > 0.02:
            return True
    if len(k15m) >= 40:
        recent15m = k15m[-14:]
        older15m = k15m[:20][-14:]
        recent_atr = sum(k.high - k.low for k in recent15m) / len(recent15m)
        older_atr = sum(k.high - k.low for k in older15m) / len(older15m)
        if older_atr > 0 and recent_atr > older_atr * 2.8:
            return True
    latest15m = k15m[-1]
    body = abs(latest15m.close - latest15m.open)
    rng = latest15m.high - latest15m.low
    wick = rng - body
    if rng > 0 and (wick / rng) > 0.82 and latest15m.open and (rng / latest15m.open) > 0.03:
        return True
    return False


def correlation_with(klines_a: List[Kline], klines_b: List[Kline], period: int = 30) -> float:
    """معامل ارتباط بيرسون (Pearson Correlation) بين عوائد عملتين — يقيس هل العملة
    فعلاً "تتبع" حركة البيتكوين، أو "فكّت ارتباطها" وتتحرك بمنطقها الخاص. القيمة
    بين -1 و 1: قريبة من 1 = ارتباط قوي موجب (تتحرك مع بعض)، قريبة من 0 = لا علاقة
    (فك ارتباط)، قريبة من -1 = ارتباط عكسي."""
    if len(klines_a) < period + 1 or len(klines_b) < period + 1:
        return 1.0  # افتراضياً نعتبرها مرتبطة لو البيانات غير كافية (الوضع الآمن الافتراضي)

    closes_a = [k.close for k in klines_a[-(period + 1):]]
    closes_b = [k.close for k in klines_b[-(period + 1):]]
    returns_a = [(closes_a[i] - closes_a[i - 1]) / closes_a[i - 1] for i in range(1, len(closes_a)) if closes_a[i - 1] > 0]
    returns_b = [(closes_b[i] - closes_b[i - 1]) / closes_b[i - 1] for i in range(1, len(closes_b)) if closes_b[i - 1] > 0]

    n = min(len(returns_a), len(returns_b))
    if n < 5:
        return 1.0
    returns_a, returns_b = returns_a[-n:], returns_b[-n:]

    mean_a = sum(returns_a) / n
    mean_b = sum(returns_b) / n
    cov = sum((returns_a[i] - mean_a) * (returns_b[i] - mean_b) for i in range(n))
    std_a = sum((x - mean_a) ** 2 for x in returns_a) ** 0.5
    std_b = sum((x - mean_b) ** 2 for x in returns_b) ** 0.5
    if std_a == 0 or std_b == 0:
        return 1.0
    return cov / (std_a * std_b)


def efficiency_ratio(klines: List[Kline], period: int = 20) -> float:
    """نسبة الكفاءة الاتجاهية (Kaufman's Efficiency Ratio) — تقيس هل حركة السعر
    'نظيفة' باتجاه واحد، أو 'عشوائية' (تتذبذب كثير لكن ما توصل لمكان فعلياً).
    = |التغيّر الصافي بالسعر| ÷ مجموع كل الحركات المطلقة خلال نفس الفترة.
    القيمة قريبة من 1 = اتجاه نظيف وقوي. قريبة من 0 = حركة عشوائية/جانبية بلا معنى."""
    if len(klines) < period + 1:
        return 0.0
    closes = [k.close for k in klines[-(period + 1):]]
    net_change = abs(closes[-1] - closes[0])
    total_movement = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if total_movement <= 0:
        return 0.0
    return net_change / total_movement


def _get_bias(klines: List[Kline]) -> str:
    if not klines:
        return "صاعد"
    closes = [k.close for k in klines]
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    return "صاعد" if e21 >= e50 else "هابط"


def daily_trend(klines_daily: List[Kline]) -> str:
    if not klines_daily:
        return "صاعد"
    closes = [k.close for k in klines_daily]
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    return "صاعد" if e21 >= e50 else "هابط"


# ---------------------------------------------------------------------------
# Explosive Breakout Hunter — filters
# ---------------------------------------------------------------------------

def detect_fakeout_rejection(klines: List[Kline], side: str, lookback: int = 3) -> bool:
    if len(klines) < lookback:
        return False
    for k in klines[-lookback:]:
        rng = k.high - k.low
        if rng <= 0.0:
            continue
        body = abs(k.close - k.open)
        upper_wick = k.high - max(k.open, k.close)
        lower_wick = min(k.open, k.close) - k.low
        body_ratio = body / rng
        if side == "Long":
            if (upper_wick / rng) > 0.4 and body_ratio < 0.42:
                return True
        else:
            if (lower_wick / rng) > 0.4 and body_ratio < 0.42:
                return True
    return False


def detect_immediate_reversal_after_sweep(klines: List[Kline], side: str) -> bool:
    if len(klines) < 3:
        return False
    prev = klines[-2]
    last = klines[-1]
    if side == "Long":
        return prev.high > klines[-3].high and last.high <= prev.high and last.close < prev.close
    return prev.low < klines[-3].low and last.low >= prev.low and last.close > prev.close


def structural_stop_loss(klines: List[Kline], side: str, entry_price: float, atr_val: float, lookback: int = 8) -> float:
    if len(klines) < lookback or atr_val <= 0.0:
        return entry_price - atr_val * 0.8 if side == "Long" else entry_price + atr_val * 0.8
    zone = klines[-lookback:]
    if side == "Long":
        structural_low = min(k.low for k in zone)
        candidate = structural_low - atr_val * 0.25
        max_allowed = entry_price - atr_val * 0.8
        min_allowed = entry_price - atr_val * 2.2
        return min(max(candidate, min_allowed), max_allowed)
    else:
        structural_high = max(k.high for k in zone)
        candidate = structural_high + atr_val * 0.25
        min_allowed = entry_price + atr_val * 0.8
        max_allowed = entry_price + atr_val * 2.2
        return max(min(candidate, max_allowed), min_allowed)


def analyze_explosive_breakout(
    symbol: str,
    k4h: List[Kline],
    k1h: List[Kline],
    k15m: List[Kline],
    k5m: List[Kline],
    k_daily: List[Kline],
    min_rr_floor: float = 3.0,
    micro: Optional[MarketMicrostructure] = None,
    trace: Optional[list] = None,
) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    if len(k5m) < 30 or len(k1h) < 60:
        _log("عدد الشموع كافٍ (5د≥30، 1س≥60)", f"5د={len(k5m)}, 1س={len(k1h)}", False)
        return None
    _log("عدد الشموع كافٍ", f"5د={len(k5m)}, 1س={len(k1h)}", True)

    last_k5m = k5m[-1]
    prev_k5m = k5m[-2]
    last_price = last_k5m.close
    if last_price <= 0.0:
        return None

    closes5m = [k.close for k in k5m]
    basis, upper, lower = bollinger_bands(closes5m, 20, 2.0)
    if basis == 0:
        return None
    band_width = (upper - lower) / basis

    atr5m = atr(k5m, 14)

    if len(k5m) < 16:
        avg_atr20 = atr5m
    else:
        tr_all = []
        for i in range(1, len(k5m)):
            cur, prev = k5m[i], k5m[i - 1]
            tr1 = cur.high - cur.low
            tr2 = abs(cur.high - prev.close)
            tr3 = abs(cur.low - prev.close)
            tr_all.append(max(tr1, tr2, tr3))
        if len(tr_all) < 14:
            avg_atr20 = atr5m
        else:
            atr_series = []
            window_sum = sum(tr_all[:14])
            atr_series.append(window_sum / 14)
            for i in range(14, len(tr_all)):
                window_sum += tr_all[i] - tr_all[i - 14]
                atr_series.append(window_sum / 14)
            last20 = atr_series[-20:]
            avg_atr20 = sum(last20) / len(last20)

    effective_atr = max(atr5m, avg_atr20 * 0.75)

    compression_window = k5m[-8:]
    max_high_c = max(k.high for k in compression_window)
    min_low_c = min(k.low for k in compression_window)
    range_height = (max_high_c - min_low_c) / last_price

    is_compressed = (band_width < 0.03) and (atr5m < avg_atr20 * 0.6) and (range_height < 0.015)

    prev20_vol = [k.volume for k in k5m[:-1][-20:]]
    avg_vol20 = (sum(prev20_vol) / len(prev20_vol)) if prev20_vol else 1.0
    vol_ratio = (last_k5m.volume / avg_vol20) if avg_vol20 > 0 else 1.0
    vol_accelerating = last_k5m.volume > prev_k5m.volume * 1.25

    has_obv_div = check_obv_divergence(k5m)

    closes1h = [k.close for k in k1h]
    ema21 = ema(closes1h, 21)
    ema50 = ema(closes1h, 50)
    h1_trend = "صاعد" if ema21 > ema50 else "هابط"

    rsi_val = rsi(closes5m, 14)
    rsi_prev = rsi(closes5m[:-1], 14)
    rsi_rising = rsi_val > rsi_prev

    last_range = last_k5m.high - last_k5m.low
    last_body = abs(last_k5m.close - last_k5m.open)
    body_ratio = (last_body / last_range) if last_range > 0 else 0.0
    closes_near_high = last_range > 0 and (last_k5m.high - last_k5m.close) / last_range < 0.25
    closes_near_low = last_range > 0 and (last_k5m.close - last_k5m.low) / last_range < 0.25

    side = ""
    is_triggered = False
    is_early_entry = False

    _log("اتجاه فريم الساعة (1H Bias)", h1_trend)
    _log("عرض نطاق البولينجر (Band Width)", round(band_width, 5))
    _log("انضغاط السعر (Compression)", is_compressed)
    _log("مؤشر RSI (5د)", round(rsi_val, 1))
    _log("معدل الفوليوم الحالي مقابل المتوسط", f"{vol_ratio:.2f}x")
    _log("نسبة جسم الشمعة للنطاق الكامل", round(body_ratio, 2))
    _log("السعر الحالي", last_price)
    _log("النطاق العلوي/السفلي للبولينجر", f"{lower:.6g} / {upper:.6g}")

    # ---- PATH A: Pre-Breakout Early Trigger ----
    if is_compressed and len(k5m) >= 3:
        last3 = k5m[-3:]
        higher_lows = last3[1].low >= last3[0].low and last3[2].low >= last3[1].low
        lower_highs = last3[1].high <= last3[0].high and last3[2].high <= last3[1].high

        if (h1_trend == "صاعد" and last_price >= upper * 0.998 and rsi_val > 45.0 and rsi_rising
                and vol_ratio > 1.7 and vol_accelerating and body_ratio > 0.55 and closes_near_high and higher_lows):
            side, is_triggered, is_early_entry = "Long", True, True
        elif (h1_trend == "هابط" and last_price <= lower * 1.002 and rsi_val < 55.0 and not rsi_rising
                and vol_ratio > 1.7 and vol_accelerating and body_ratio > 0.55 and closes_near_low and lower_highs):
            side, is_triggered, is_early_entry = "Short", True, True
        _log("مسار أ: دخول مبكر قبل الاختراق (يحتاج انضغاط + زخم مبكر)", f"صاعد={h1_trend=='صاعد'}, RSI صاعد={rsi_rising}, فوليوم>1.7x={vol_ratio>1.7}", is_triggered)
    else:
        _log("مسار أ: دخول مبكر", "السعر غير منضغط حالياً (شرط أساسي للمسار)، تم تخطي هذا المسار", False)

    # ---- PATH B: Confirmed Breakout Trigger ----
    if not is_triggered:
        if (h1_trend == "صاعد" and last_price > upper and rsi_val > 50.0
                and last_k5m.volume > avg_vol20 * 2.5 and body_ratio > 0.45 and closes_near_high):
            side, is_triggered = "Long", True
        elif (h1_trend == "هابط" and last_price < lower and rsi_val < 50.0
                and last_k5m.volume > avg_vol20 * 2.5 and body_ratio > 0.45 and closes_near_low):
            side, is_triggered = "Short", True
        _log("مسار ب: اختراق مؤكَّد (يحتاج تجاوز فعلي للنطاق + فوليوم>2.5x)",
             f"تجاوز النطاق={'نعم' if (last_price>upper or last_price<lower) else 'لا'}, فوليوم={last_k5m.volume/avg_vol20:.2f}x" if avg_vol20 > 0 else "n/a",
             is_triggered)

    if not is_triggered:
        _log("❌ القرار النهائي", "لا يوجد اختراق أو دخول مبكر مؤكَّد بأي من المسارين — هذا سبب الرفض", False)
        return None
    _log("✅ الاتجاه المرشَّح", side, True)

    if detect_fakeout_rejection(k5m, side, lookback=3):
        _log("❌ فلتر فخ الاختراق (Fakeout Rejection)", "اكتُشف نمط فخ اختراق حديث — رفض", False)
        return None

    if detect_immediate_reversal_after_sweep(k5m, side):
        _log("❌ فلتر الانعكاس الفوري بعد السحب", "اكتُشف انعكاس فوري بعد سحب سيولة — رفض", False)
        return None

    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct < -1.0:
        _log("❌ فلتر الفائدة المفتوحة (OI)", f"تغيّر OI={oi_change_pct:.2f}% (أقل من -1%) — رفض", False)
        return None
    _log("الفائدة المفتوحة (OI) تغيّر", f"{oi_change_pct:.2f}%" if oi_change_pct is not None else "غير متوفرة")

    # 📊 تعديل مبني على بيانات إنتاج فعلية (تصدير صفقات حقيقي): كان هذا الشرط إلزامياً
    # 100% (يرفض الصفقة لو ما توفرت البيانات، حتى لو باقي كل الشروط ممتازة). بفحص سجل
    # حقيقي لعدة ساعات تشغيل، ثبت إن هذا الشرط أوقف تماماً 4 استراتيجيات كاملة تعتمد
    # على هذي البوابة كمُطلِق (الانفجار السعري، التأكيد المزدوج، الانفجار الموجّه بـICT،
    # انعكاس عرض/طلب) — صفر إشارات من الأربعة رغم توفر فرص حقيقية. الحل: نفس فكرة
    # التأكيد الحقيقي من الصفقات الفعلية، لكن كفلتر رفض عند **تعارض واضح** فقط (مو غياب
    # البيانات)، ونضيفه كنقطة ثقة إضافية بدل بوابة إلزامية توقف النظام بالكامل.
    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        if side == "Long" and taker_pressure < -0.25:
            _log("❌ فلتر ضغط المتداولين الفعليين", f"القيمة {taker_pressure:.2f} تعاكس صفقة الشراء بوضوح — رفض", False)
            return None
        if side == "Short" and taker_pressure > 0.25:
            _log("❌ فلتر ضغط المتداولين الفعليين", f"القيمة {taker_pressure:.2f} تعاكس صفقة البيع بوضوح — رفض", False)
            return None
        _log("✅ فلتر ضغط المتداولين الفعليين", f"{taker_pressure:.2f} — لا يعارض الصفقة", True)
    else:
        _log("فلتر ضغط المتداولين الفعليين", "بيانات غير متوفرة هذي المرة — تم تخطي هذا الفلتر (مو رفض)", None)

    ob_imbalance = micro.ob_imbalance if micro else None
    if is_early_entry and ob_imbalance is not None:
        if side == "Long" and ob_imbalance < -0.15:
            _log("❌ فلتر عمق السوق (دخول مبكر فقط)", f"توازن الأوامر {ob_imbalance:.2f} يعاكس صفقة الشراء — رفض", False)
            return None
        if side == "Short" and ob_imbalance > 0.15:
            _log("❌ فلتر عمق السوق (دخول مبكر فقط)", f"توازن الأوامر {ob_imbalance:.2f} يعاكس صفقة البيع — رفض", False)
            return None

    # نقطة الدخول: بدل مطاردة السعر بالسوق اللحظي بعد ما يكون قد تحرك فعلاً، نستخدم
    # أمر محدد (Limit) عند المستوى الهيكلي الحقيقي:
    #  - دخول مبكر (Path A): السعر أصلاً قريب من النطاق ولسا ما اخترق بعد، فسعر السوق
    #    الحالي منطقي كنقطة دخول (ما فيه "مطاردة" حقيقية بهذي الحالة).
    #  - اختراق مؤكَّد (Path B): السعر تجاوز النطاق فعلاً، فندخل عند حافة النطاق نفسها
    #    (upper/lower) على أمل إعادة اختبار (Retest) — نقطة أدق ومخاطرة أقل من الشراء
    #    بالسعر المرتفع مباشرة بعد الاختراق.
    if is_early_entry:
        entry_price = last_price
        entry_note = "دخول مبكر (السعر لسا عند حافة النطاق، لا حاجة لإعادة اختبار)"
    else:
        entry_price = upper if side == "Long" else lower
        entry_note = f"دخول محدد (Limit) عند مستوى النطاق المكسور {entry_price:.6g} — بانتظار إعادة اختبار (Retest)"
    _log("📍 منطق نقطة الدخول", entry_note)

    sl = structural_stop_loss(k5m, side, entry_price, effective_atr, lookback=8)
    risk_distance = abs(entry_price - sl)
    if entry_price and risk_distance / entry_price < 0.0015:
        _log("❌ فلتر أدنى مسافة وقف خسارة", f"المسافة {risk_distance/entry_price*100:.3f}% أقل من الحد الأدنى (0.15%) — السوق شبه ساكن", False)
        return None
    _log("✅ كل الشروط تحققت — تم توليد إشارة", side, True)

    measured_move = max_high_c - min_low_c
    tp1 = entry_price + max(effective_atr * 2.5, measured_move) if side == "Long" else entry_price - max(effective_atr * 2.5, measured_move)
    tp2 = entry_price + max(effective_atr * 5.0, measured_move * 2.0) if side == "Long" else entry_price - max(effective_atr * 5.0, measured_move * 2.0)

    min_rr = 3.0
    reward_distance = abs(tp2 - entry_price)
    if risk_distance > 0 and reward_distance / risk_distance < min_rr:
        tp2 = entry_price + risk_distance * min_rr if side == "Long" else entry_price - risk_distance * min_rr
    rr = abs(tp2 - entry_price) / risk_distance if risk_distance > 0 else min_rr

    prob = 80 if is_early_entry else 82
    if is_compressed:
        prob += 5
    if has_obv_div:
        prob += 5
    if vol_ratio > 3.0:
        prob += 3
    if is_early_entry and vol_accelerating:
        prob += 2
    if oi_change_pct is not None and oi_change_pct > 1.5:
        prob += 4
    if ob_imbalance is not None:
        ob_aligned = (side == "Long" and ob_imbalance > 0.1) or (side == "Short" and ob_imbalance < -0.1)
        if ob_aligned:
            prob += 3
    funding_rate = micro.funding_rate if micro else None
    funding_crowded = funding_rate is not None and (
        (side == "Long" and funding_rate > 0.001) or (side == "Short" and funding_rate < -0.001)
    )
    if funding_crowded:
        prob -= 4

    # مكافأة تأكيد إضافي: ضغط متداولين قوي جداً (فوق الحد الأدنى الإلزامي 0.10 اللي
    # سبق التحقق منه أعلاه) — يعطي نقاط زيادة للحالات اللي فيها ضغط شراء/بيع طاغي وواضح
    if taker_pressure is not None:
        taker_aligned = (side == "Long" and taker_pressure > 0.15) or (side == "Short" and taker_pressure < -0.15)
        if taker_aligned:
            prob += 3

    # فلتر ازدحام: أغلبية الحسابات متمركزة فعلاً بنفس اتجاهنا (خطر تصفية مزدحمة قريبة)
    long_short_ratio = micro.long_short_ratio if micro else None
    if long_short_ratio is not None:
        crowded_same_side = (side == "Long" and long_short_ratio > 2.2) or (side == "Short" and long_short_ratio < 0.45)
        if crowded_same_side:
            prob -= 3

    # CVD تراكمي 24 ساعة (Cumulative Volume Delta) — تأكيد إضافي بمنظور زمني أوسع من
    # ضغط المتداولين اللحظي، يعكس هيمنة الشراء/البيع الفعلية على مدى اليوم كامل
    cvd_pct = micro.cvd_pct if micro else None
    if cvd_pct is not None:
        cvd_aligned = (side == "Long" and cvd_pct > 60) or (side == "Short" and cvd_pct < 40)
        if cvd_aligned:
            prob += 3

    prob = max(70, min(95, prob))

    parts = []
    parts.append("⚡ صائد الانفجارات - دخول مبكر قبل التأكيد الكامل (Pre-Breakout Catch)" if is_early_entry
                  else "🎯 استراتيجية صائد الانفجارات السعرية (Explosive Breakout Hunter)")
    if is_compressed:
        parts.append("📦 تم رصد تضييق وضغط سيولة حاد (Compression Phase)")
    if has_obv_div:
        parts.append("🐋 تجميع مؤسساتي خفي مكتشف عبر تباعد مؤشر OBV")
    parts.append(f"⚡ زخم متسارع بحجم تداول ({vol_ratio:.1f}x المتوسط)" + (" ومتسارع لحظياً عن الشمعة السابقة" if vol_accelerating else ""))
    parts.append("✅ تم استبعاد احتمال سحب السيولة (Liquidity Grab) - إغلاق قوي وليس ذيل رفض")
    if oi_change_pct is not None:
        parts.append(f"📊 تغير المراكز المفتوحة (OI): {oi_change_pct:.2f}% - {'تأكيد دخول سيولة حقيقية جديدة' if oi_change_pct > 1.5 else 'محايد'}")
    if ob_imbalance is not None:
        parts.append(f"📖 توازن دفتر الأوامر الحي (Order Book): {ob_imbalance:.2f}")
    if funding_crowded:
        parts.append("⚠️ تنبيه: معدل التمويل (Funding) مزدحم بنفس اتجاه الصفقة - خطر ارتداد مفاجئ أعلى من المعتاد")
    if taker_pressure is not None:
        parts.append(f"💥 ضغط المتداولين الفعليين (Taker Pressure): {taker_pressure:.2f} - {'قوي جداً (تأكيد إضافي)' if (side=='Long' and taker_pressure>0.15) or (side=='Short' and taker_pressure<-0.15) else 'محايد'}")
    if long_short_ratio is not None:
        crowded_txt = "⚠️ ازدحام حسابات بنفس اتجاهنا - خطر تصفية مزدحمة" if ((side=='Long' and long_short_ratio>2.2) or (side=='Short' and long_short_ratio<0.45)) else "طبيعي"
        parts.append(f"👥 نسبة تمركز الحسابات (Long/Short): {long_short_ratio:.2f} - {crowded_txt}")
    if cvd_pct is not None:
        cvd_txt = "متوافق (تأكيد هيمنة شراء/بيع حقيقية على مدى اليوم)" if ((side=='Long' and cvd_pct>60) or (side=='Short' and cvd_pct<40)) else "محايد"
        parts.append(f"📊 CVD تراكمي (24س): {cvd_pct:.1f}% شراء - {cvd_txt}")
    parts.append("🛡️ ستوب لوز هيكلي عند حدود منطقة التجميع - اختراقه يعني انعكاس حقيقي وليس فخ سيولة")
    parts.append(f"📈 توافق تام مع اتجاه فريم الساعة (1H Bias: {h1_trend})")
    parts.append(f"🎯 الهدف الأول (TP1): {tp1}")
    parts.append(f"🚀 الهدف الثاني (TP2): {tp2}")
    parts.append(f"🛡️ الستوب لوز (SL): {sl}")
    parts.append(f"⚖️ نسبة العائد للمخاطرة: 1:{rr:.1f}")

    return AnalysisResult(
        symbol=symbol,
        trend=h1_trend,
        dt=daily_trend(k_daily),
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
        conf=7 if is_early_entry else 8,
        behavior="، ".join(parts),
        volume_analysis=f"متوسط حجم الـ 20 فترة: {avg_vol20} | الحجم الحالي: {last_k5m.volume} | نسبة التسارع: {vol_ratio:.2f}x",
        low_vol=low_vol(k5m),
        kill_zone_ok=in_kill_zone(),
        news_time=check_irrational_market(k5m, k15m, k1h),
        ranging=is_compressed,
    )


def analyze(symbol: str, k4h, k1h, k15m, k5m, k_daily, micro=None, trace=None) -> Optional[AnalysisResult]:
    """نقطة الدخول الرئيسية — تعادل OrionAnalyzer.analyze في الأصل (استراتيجية واحدة فقط)."""
    return analyze_explosive_breakout(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro, trace=trace)
