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
from typing import Optional, List, Tuple


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
    large_order_pressure: Optional[float] = None  # 🆕 ضغط الصفقات الكبيرة فقط (Order Flow متقدم) — يعزل تدخل اللاعبين الكبار عن ضجيج التجزئة


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
    score_breakdown: list = None  # قائمة {factor, points, confirmed, earned} — تُضبط لكل استراتيجية
    signal_score: float = 100.0   # مجموع النقاط المكتسبة فعلياً من أصل 100


def build_score_breakdown(factors: list) -> tuple:
    """يوزّع 100 نقطة بالتساوي على قائمة عوامل تحليلية [(اسم العامل, تحقق؟), ...]،
    ويرجع (قائمة التفصيل، مجموع النقاط المكتسبة). كل استراتيجية تحدد عواملها الخاصة
    بناءً على شروطها التحليلية الفعلية (فيبوناتشي=عامل، فوليوم=عامل، وهكذا)."""
    n = len(factors)
    if n == 0:
        return [], 100.0
    points_each = round(100.0 / n, 2)
    breakdown = []
    total = 0.0
    for name, confirmed in factors:
        earned = points_each if confirmed else 0.0
        breakdown.append({"factor": name, "points": points_each, "confirmed": bool(confirmed), "earned": earned})
        total += earned
    return breakdown, round(total, 1)


# ---------------------------------------------------------------------------
# Basic indicators
# ---------------------------------------------------------------------------

def atr(klines: List[Kline], period: int = 14) -> float:
    """متوسط المدى الحقيقي (ATR) — بتنعيم وايلدر (Wilder's Smoothing / RMA)، نفس
    الطريقة القياسية المستخدَمة بأغلب منصات التداول الحقيقية (TradingView الافتراضي
    مثلاً). 🔴 تحسين (بطلب صريح): الحساب القديم كان متوسط بسيط لآخر `period` مدى
    حقيقي فقط — يعني شمعة شاذة وحدة ضمن آخر 14 فترة تؤثر بوزن كامل ومباشر على
    القيمة. تنعيم وايلدر يعطي "ذاكرة" تراكمية من كل التاريخ المتوفر، فيمتص تأثير
    شمعة شاذة واحدة تدريجياً بدل ما تسيطر على القيمة فوراً — نفس فلسفة الأسقف
    الاحترازية المضافة اليوم بالاستراتيجيات، بس هنا بمصدر الحساب نفسه."""
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
    atr_val = sum(tr_list[:period]) / period  # البذرة: متوسط بسيط لأول `period` مدى حقيقي متوفر
    for tr in tr_list[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period  # تنعيم وايلدر: وزن تراكمي متناقص أسّياً
    return atr_val


def atr_series(klines: List[Kline], period: int = 14) -> List[float]:
    """🆕 نسخة متسلسلة من atr() — ترجع قيمة ATR (بتنعيم وايلدر، نفس المنهجية
    بالضبط) لكل نقطة زمنية متاحة، مو رقم أخير واحد بس. أُضيفت لحل ازدواجية
    حقيقية كانت موجودة بـanalyze_explosive_breakout: حساب يدوي منفصل لسلسلة ATR
    بمتوسط بسيط قديم (غير متسق مع atr() المركزية بعد تحديثها لتنعيم وايلدر) —
    الآن كلا القيمتين (اللحظية والسلسلة) تُحسبان بنفس المنهجية بالضبط."""
    if len(klines) <= period:
        return []
    tr_list = []
    for i in range(1, len(klines)):
        cur, prev = klines[i], klines[i - 1]
        tr1 = cur.high - cur.low
        tr2 = abs(cur.high - prev.close)
        tr3 = abs(cur.low - prev.close)
        tr_list.append(max(tr1, tr2, tr3))
    if len(tr_list) < period:
        return []
    series = []
    atr_val = sum(tr_list[:period]) / period
    series.append(atr_val)
    for tr in tr_list[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
        series.append(atr_val)
    return series


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


def _wma_series(values: List[float], period: int) -> List[float]:
    """متوسط متحرك مرجّح (Weighted Moving Average) — يعطي وزن أكبر للقيم الأحدث
    بشكل خطي متدرّج، أساس حساب Hull Moving Average أدناه."""
    if period < 1:
        period = 1
    weights = list(range(1, period + 1))
    weight_sum = sum(weights)
    result = []
    for i in range(len(values)):
        if i + 1 < period:
            result.append(values[i])
            continue
        subset = values[i + 1 - period:i + 1]
        result.append(sum(w * v for w, v in zip(weights, subset)) / weight_sum)
    return result


def hma(closes: List[float], period: int) -> float:
    """Hull Moving Average — مؤشر مصمم خصيصاً يحل التناقض الكلاسيكي بين "السرعة"
    و"الاستقرار" اللي تعاني منه EMA: يستجيب أسرع للتغيّرات الحقيقية، لكن بتذبذب
    أقل بكثير من ضجيج لحظي عادي. يُحسب بأخذ فرق مرجّح بين WMA بنصف الفترة وWMA
    بالفترة الكاملة، ثم تنعيمه بـWMA على جذر الفترة."""
    if len(closes) < period:
        period = max(2, len(closes))
    half = max(1, period // 2)
    sqrt_p = max(1, int(round(period ** 0.5)))
    wma_half = _wma_series(closes, half)
    wma_full = _wma_series(closes, period)
    raw = [2 * h - f for h, f in zip(wma_half, wma_full)]
    hma_series = _wma_series(raw, sqrt_p)
    return hma_series[-1] if hma_series else (closes[-1] if closes else 0.0)


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
        # 🔴 إصلاح خلل تصميمي (اكتُشف بمراجعة عميقة): كانت "الفترة الأقدم للمقارنة"
        # ثابتة على أول 20 شمعة بكل المصفوفة المجلوبة (k15m[:20]) — لو محفوظ 400+
        # شمعة، هذا يعني مقارنة "الآن" بـ"قبل ~4 أيام"، مو "قبل بضع ساعات" كما يوحي
        # الاسم. الآن نستخدم نافذة نسبية للحاضر (آخر 14 قبل الـ14 الأحدث مباشرة) —
        # مقارنة ثابتة المعنى بغض النظر عن عمق البيانات المحفوظة كلياً.
        older15m = k15m[-40:-14]
        recent_atr = sum(k.high - k.low for k in recent15m) / len(recent15m)
        older_atr = sum(k.high - k.low for k in older15m) / len(older15m) if older15m else 0
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


def assess_coin_tradability(k1h: List[Kline], k15m: List[Kline], settings: dict) -> Tuple[bool, Optional[str], dict]:
    """🆕 فلتر جودة استباقي على مستوى العملة نفسها (بطلب صريح: "حل جذري نفلتر
    هذي العملات" — مو نظام تعلّم يعتمد على صفقاتنا الفاشلة السابقة). يفحص سلوك
    السعر الفعلي للعملة **قبل** أي استراتيجية، ويرفضها كاملة لهذي الدورة لو
    أظهرت خصائص تخليها غير قابلة للتداول بأمان بغض النظر عن الاستراتيجية أو
    الاتجاه:

    1) كفاءة اتجاهية عامة ضعيفة جداً (ER) — حركة عشوائية/جانبية بلا معنى حقيقي،
       مو بس تراجع مؤقت داخل ترند.
    2) فتيلة/شمعة شاذة متطرفة — مدى شمعة واحدة أكبر بكثير من المدى المعتاد
       (نفس فئة الخلل المكتشف بـstop_hunt وcrowd_trap: عملة فيها شموع "قفزة"
       غير طبيعية تخلي حساب أي وقف/هدف غير موثوق).
    3) تقلب مفرط نسبة للسعر (ATR/السعر) — عملة "برية" جداً حتى لو حركتها نظيفة
       اتجاهياً، المخاطرة بالتحكم بالوقف غير عملية.

    يرجع (قابلة للتداول؟, سبب الرفض أو None, تفاصيل القياسات للتسجيل)."""
    if not settings.get("is_coin_quality_filter_enabled", True):
        return True, None, {}

    metrics = {}
    if len(k15m) >= 210:
        er = efficiency_ratio(k15m, period=200)
        metrics["efficiency_ratio_200"] = round(er, 3)
        min_er = float(settings.get("min_coin_efficiency_ratio", 0.05))
        if er < min_er:
            return False, f"كفاءة اتجاهية عامة ضعيفة جداً ({er:.3f} < {min_er}) — حركة عشوائية/جانبية بلا معنى حقيقي، غير مناسبة لأي استراتيجية اتجاهية", metrics

    if len(k1h) >= 200:
        window = k1h[-200:]
        ranges = sorted(k.high - k.low for k in window if (k.high - k.low) > 0)
        if ranges:
            median_range = ranges[len(ranges) // 2]
            max_recent_range = max(k.high - k.low for k in window[-50:])
            outlier_ratio = (max_recent_range / median_range) if median_range > 0 else 0
            metrics["candle_outlier_ratio"] = round(outlier_ratio, 2)
            max_outlier = float(settings.get("max_coin_wick_outlier_ratio", 6.0))
            if outlier_ratio > max_outlier:
                return False, f"شموع شاذة متطرفة مكتشفة (أكبر شمعة {outlier_ratio:.1f}× المدى المعتاد، الحد {max_outlier}×) — سلوك سعري غير طبيعي يخلي حساب أي وقف/هدف غير موثوق", metrics

    if len(k1h) >= 20:
        atr_val = atr(k1h, 14)
        last_price = k1h[-1].close
        if last_price > 0:
            atr_pct = (atr_val / last_price) * 100
            metrics["atr_pct_1h"] = round(atr_pct, 2)
            max_atr_pct = float(settings.get("max_coin_atr_pct", 8.0))
            if atr_pct > max_atr_pct:
                return False, f"تقلب مفرط جداً نسبة للسعر (ATR = {atr_pct:.1f}% من السعر، الحد {max_atr_pct}%) — عملة برية جداً، أي وقف منطقي يصير واسع جداً لإدارة مخاطرة آمنة", metrics

    return True, None, metrics


def find_significant_swing(klines: List[Kline], atr_val: float, min_move_atr: float = 3.0,
                            min_leg_efficiency: float = 0.35, max_lookback: int = 400):
    """🆕 اكتشاف سوينق (قمة/قاع) حقيقي هيكلياً — بديل عن أخذ أعلى/أدنى نقطة
    بنافذة ثابتة الطول بشكل أعمى (كان يلقط تذبذبات صغيرة داخل رينج أوسع على
    إنها "السوينق"، بدون أي معنى هيكلي حقيقي).

    الفكرة (ZigZag مبسّط):
      1) نمشي عبر الشموع من الأقدم للأحدث ونتتبع أعلى قمة/أدنى قاع مؤقتين.
      2) نأكّد "بيفوت" (نقطة تحول محورية) بس لما الانعكاس من القمة/القاع
         المؤقت يتجاوز عتبة `min_move_atr × ATR` — عتبة موضوعية تفرّق بين
         انعكاس هيكلي حقيقي وتذبذب عادي، بدل الاعتماد على طول نافذة تعسفي.
      3) نرجع بس **آخر رجل حركة (Leg) مؤكدة** بين آخر بيفوتين، ونتحقق إن
         هذي الحركة نفسها "نظيفة" اتجاهياً (Efficiency Ratio ≥ الحد الأدنى)
         — يعني حركة اتجاهية حقيقية، مو رينج متذبذب صدفة أعلاه/أدناه أعلى/
         أدنى نقطة.

    يرجع None لو ما لقى سوينق مؤهل (بدل ما يفرض سوينق ضعيف الجودة).
    """
    if atr_val <= 0 or len(klines) < 20:
        return None

    window = klines[-max_lookback:]
    threshold = atr_val * min_move_atr

    pivots = []  # كل عنصر: (index, price, "high"|"low")
    dir_up = None
    ext_idx, ext_price = 0, window[0].close

    for i, k in enumerate(window):
        if dir_up is None:
            if k.high - ext_price >= threshold:
                pivots.append((ext_idx, ext_price, "low"))
                dir_up, ext_idx, ext_price = True, i, k.high
            elif ext_price - k.low >= threshold:
                pivots.append((ext_idx, ext_price, "high"))
                dir_up, ext_idx, ext_price = False, i, k.low
            continue

        if dir_up:
            if k.high > ext_price:
                ext_idx, ext_price = i, k.high
            elif ext_price - k.low >= threshold:
                pivots.append((ext_idx, ext_price, "high"))
                dir_up, ext_idx, ext_price = False, i, k.low
        else:
            if k.low < ext_price:
                ext_idx, ext_price = i, k.low
            elif k.high - ext_price >= threshold:
                pivots.append((ext_idx, ext_price, "low"))
                dir_up, ext_idx, ext_price = True, i, k.high

    pivots.append((ext_idx, ext_price, "high" if dir_up else "low"))

    if len(pivots) < 2:
        return None  # ما فيه حركة كافية القوة تكوّن بيفوت هيكلي واحد حتى

    # آخر رجل حركة مؤكدة = بين آخر بيفوتين
    (idx_a, price_a, kind_a), (idx_b, price_b, kind_b) = pivots[-2], pivots[-1]
    if idx_b <= idx_a:
        return None

    leg_klines = window[idx_a:idx_b + 1]
    er = efficiency_ratio(leg_klines, period=len(leg_klines) - 1) if len(leg_klines) > 2 else 0.0
    if er < min_leg_efficiency:
        return None  # الحركة موجودة بس "متسخة" (رينج/تذبذب) — مو رجل اتجاهية نظيفة

    if kind_a == "low" and kind_b == "high":
        return {"low_idx": idx_a, "swing_low": price_a, "high_idx": idx_b, "swing_high": price_b,
                "direction": "up", "leg_efficiency": er}
    if kind_a == "high" and kind_b == "low":
        return {"high_idx": idx_a, "swing_high": price_a, "low_idx": idx_b, "swing_low": price_b,
                "direction": "down", "leg_efficiency": er}
    return None


def _hma_bias_pair(closes: List[float]) -> str:
    """يحسب اتجاه صاعد/هابط بمقارنة HMA(21) مقابل HMA(50) — لكن **يحمي من نفس
    الفخ اللي اكتشفناه**: لو البيانات غير كافية لـHMA50 الحقيقية (أقل من 50 نقطة)،
    دالة hma() تقلّص الفترة **بصمت** لعدد أقل، فتصير المقارنة "HMA21 حقيقية مقابل
    HMA35 مزيّفة" مثلاً — غير متسقة وتعطي قرار عشوائي. هنا نقلّل **الفترتين معاً
    وبنفس النسبة** (نحافظ على نسبة 21:50 تقريباً) لو البيانات ناقصة، بدل ما وحدة
    تتقلص بمعزل عن الثانية."""
    period_long = 50
    period_short = 21
    if len(closes) < period_long:
        period_long = max(4, len(closes))
        period_short = max(2, round(period_long * (21.0 / 50.0)))
    h_short = hma(closes, period_short)
    h_long = hma(closes, period_long)
    return "صاعد" if h_short >= h_long else "هابط"


def _get_bias(klines: List[Kline]) -> str:
    if not klines:
        return "صاعد"
    # 🔴 إصلاح جذري (اكتشاف جديد بمراجعة شاملة لكل البيانات): كانت الدالة تستخدم
    # **كل** الشموع بما فيها آخر شمعة، اللي غالباً **لسا قيد التكوين** وقت الفحص
    # (ما أغلقت بعد). سعرها اللحظي المتذبذب يدخل بحساب الاتجاه بوزن كبير نسبياً،
    # فيقلب قرار الاتجاه الرئيسي (صاعد/هابط) بشكل غير مستقر بمجرد تذبذب طبيعي —
    # بالذات خطير عند نقاط الانعكاس، وهذا يأثر مباشرة على 3 استراتيجيات (فيبوناتشي،
    # السكالب، ICT). نستبعد الآن آخر شمعة، ونعتمد بس على الشموع **المكتملة فعلياً**.
    confirmed_klines = klines[:-1] if len(klines) > 1 else klines
    closes = [k.close for k in confirmed_klines]
    # 📊 تحسين إضافي: استبدال EMA بـHull Moving Average — يحل تناقض "السرعة مقابل
    # الاستقرار" الكلاسيكي بـEMA (نفس النوع من عدم الاستقرار اللي اكتشفناه للتو).
    return _hma_bias_pair(closes)


def daily_trend(klines_daily: List[Kline]) -> str:
    if not klines_daily:
        return "صاعد"
    # نفس إصلاحي _get_bias: استبعاد آخر شمعة (قيد التكوين) + HMA بدل EMA + حماية
    # من عدم اتساق الفترة لو البيانات غير كافية
    confirmed = klines_daily[:-1] if len(klines_daily) > 1 else klines_daily
    closes = [k.close for k in confirmed]
    return _hma_bias_pair(closes)


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


def structural_stop_loss(klines: List[Kline], side: str, entry_price: float, atr_val: float, lookback: int = 150) -> float:
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
    current_price: Optional[float] = None,
    **kwargs,
) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    if len(k5m) < 200 or len(k1h) < 100:
        _log("عدد الشموع كافٍ (5د≥30، 1س≥60)", f"5د={len(k5m)}, 1س={len(k1h)}", False)
        return None
    _log("عدد الشموع كافٍ", f"5د={len(k5m)}, 1س={len(k1h)}", True)

    last_k5m = k5m[-1]
    prev_k5m = k5m[-2]
    # 🔴 إصلاح جوهري (نفس فئة الخلل المكتشف بكل استراتيجيات "الدخول الفوري"):
    # last_k5m.close تبقى مستخدَمة صح لتحليل **خصائص الشمعة كنمط** (فحص الجسم،
    # الفتيل، إلخ) — هذا صحيح تماماً بيانات مؤكَّدة. لكن last_price (المرجع
    # للسعر الحالي الفعلي، ولاحقاً entry_price) يجب يعكس اللحظة الحقيقية، لا
    # شمعة مؤكَّدة متأخرة لغاية 5 دقايق. نستخدم current_price المُمرَّر لو متوفر.
    last_price = current_price if current_price is not None else last_k5m.close
    if last_price <= 0.0:
        return None

    closes5m = [k.close for k in k5m]
    basis, upper, lower = bollinger_bands(closes5m, 20, 2.0)
    if basis == 0:
        return None
    band_width = (upper - lower) / basis

    atr5m = atr(k5m, 14)

    # 🔴 إصلاح ازدواجية حقيقية (اكتُشفت بمراجعة شاملة): كان فيه حساب يدوي منفصل
    # لسلسلة ATR بمتوسط بسيط قديم — غير متسق مع atr() أعلاه بعد تحديثها لتنعيم
    # وايلدر. الآن نستخدم atr_series() المشتركة، بنفس المنهجية بالضبط.
    series = atr_series(k5m, 14)
    avg_atr20 = (sum(series[-20:]) / len(series[-20:])) if series else atr5m

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

    # 🔴 نفس إصلاحي _get_bias/daily_trend، مطبَّقين هنا على الانفجار السعري نفسه —
    # الاستراتيجية الأساسية اللي يعتمد عليها التوافق وصيد التصفيات: استبعاد آخر
    # شمعة (قيد التكوين) + HMA بدل EMA (أسرع استجابة وأقل تذبذباً عند الانعكاس)
    confirmed_1h = k1h[:-1] if len(k1h) > 1 else k1h
    closes1h = [k.close for k in confirmed_1h]
    h1_trend = _hma_bias_pair(closes1h)

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
    # 📊 إصلاح مبني على بيانات فعلية: فحص حقيقي لـ6 صفقات خاسرة (5 منها Short) أظهر
    # كل الفائدة المفتوحة سلبية أو شبه صفرية وقت الدخول (-0.77%، -0.23%، -0.78%،
    # -0.07%)، بينما الحد القديم (-1.0%) ما كان يرفض ولا وحدة منها. فائدة مفتوحة
    # هابطة وقت الاختراق تعني الحركة مدفوعة بإغلاق مراكز قائمة (تصفية/تغطية)، مو
    # اقتناع جديد حقيقي بالاتجاه — شددنا الحد لـ-0.5% ليمسك هالنمط فعلياً.
    if oi_change_pct is not None and oi_change_pct < -0.5:
        _log("❌ فلتر الفائدة المفتوحة (OI)", f"تغيّر OI={oi_change_pct:.2f}% (أقل من -0.5%) — رفض", False)
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

    sl = structural_stop_loss(k5m, side, entry_price, effective_atr, lookback=150)
    risk_distance = abs(entry_price - sl)
    if entry_price and risk_distance / entry_price < 0.0015:
        _log("❌ فلتر أدنى مسافة وقف خسارة", f"المسافة {risk_distance/entry_price*100:.3f}% أقل من الحد الأدنى (0.15%) — السوق شبه ساكن", False)
        return None
    _log("✅ كل الشروط تحققت — تم توليد إشارة", side, True)

    # 🔴 إصلاح مبني على مراجعة صفقات حقيقية فشلت (نفس النمط المكتشف بالسكالب
    # الدقيق): فوليوم متطرف جداً (>10x) حتى على شمعة اختراق قد يعني تصريف/استنزاف
    # (Climactic Volume) بدل تأكيد اختراق حقيقي صحي، خصوصاً لو الاختراق نفسه يأتي
    # بعد حركة ممتدة أصلاً. نشترط تأكيد ضغط متداولين حقيقي قبل قبول فوليوم متطرف
    # كذا كـ"تأكيد إيجابي"، بدل قبوله أعمى.
    if vol_ratio > 10.0:
        taker_confirms_extreme = taker_pressure is not None and (
            (side == "Long" and taker_pressure > 0.15) or (side == "Short" and taker_pressure < -0.15)
        )
        _log("⚠️ فوليوم متطرف جداً (>10x) على شمعة الاختراق — يحتاج تأكيد ضغط متداولين إضافي", taker_confirms_extreme, taker_confirms_extreme)
        if not taker_confirms_extreme:
            _log("❌ فلتر الفوليوم المتطرف", f"{vol_ratio:.2f}x عالٍ جداً بدون تأكيد ضغط متداولين حقيقي — رفض احترازي (احتمال استنزاف لا اختراق حقيقي)", False)
            return None

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

    large_order_pressure = micro.large_order_pressure if micro else None
    score_factors = [
        ("اختراق/انضغاط نطاق البولينجر", True),  # بوابة إلزامية أصلاً وصلنا لهنا
        ("زخم الفوليوم المؤكَّد", True),
        ("اتجاه RSI متوافق", True),
        ("شكل الشمعة (جسم قوي + إغلاق حاسم)", True),
        ("تأكيد الفائدة المفتوحة (OI)", oi_change_pct is not None and oi_change_pct > 1.0),
        ("ضغط المتداولين الفعليين (Taker Pressure)", taker_pressure is not None and ((side == "Long" and taker_pressure > 0.1) or (side == "Short" and taker_pressure < -0.1))),
        ("CVD تراكمي متوافق", cvd_pct is not None and ((side == "Long" and cvd_pct > 55) or (side == "Short" and cvd_pct < 45))),
        ("🆕 ضغط صفقات كبيرة متوافق (Order Flow)", large_order_pressure is not None and ((side == "Long" and large_order_pressure > 0.15) or (side == "Short" and large_order_pressure < -0.15))),
    ]
    score_breakdown, signal_score = build_score_breakdown(score_factors)

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
        score_breakdown=score_breakdown,
        signal_score=signal_score,
    )


def analyze(symbol: str, k4h, k1h, k15m, k5m, k_daily, micro=None, trace=None, current_price=None, **kwargs) -> Optional[AnalysisResult]:
    """نقطة الدخول الرئيسية — تعادل OrionAnalyzer.analyze في الأصل (استراتيجية واحدة فقط)."""
    return analyze_explosive_breakout(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro, trace=trace, current_price=current_price, **kwargs)
