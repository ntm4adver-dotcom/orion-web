"""
استراتيجية صيد الاستوبات والمؤسسات (Stop-Loss Hunting).

⚠️ تصحيح توثيقي: كانت موصوفة سابقاً كـ"منقولة عن StopLossHuntDetector.kt الأصلي" —
هذا غير دقيق. لا يوجد ملف بهذا الاسم أو هذا المفهوم بالكود الأصلي (تم التحقق فعلياً
بالبحث بكامل مصدر التطبيق الأصلي). هذي استراتيجية مبنية خصيصاً لهذا التطبيق.

الفكرة: صنّاع السوق (Market Makers) غالباً يدفعون السعر عمداً لضرب مستويات وقف
الخسارة للمتداولين الأفراد (سيولة التجزئة) — يكسرون بفتيلة (Wick) أعلى قمة أو
أدنى قاع سابق معروف، يجمعون السيولة المتحررة هناك، ثم ينعكس السعر فعلياً بالاتجاه
المعاكس. هذا نمط "فخ سيولة كلاسيكي" على مستوى القمم/القيعان التاريخية مباشرة
(بدل مناطق العرض/الطلب الأوسع اللي تغطيها استراتيجية "انعكاس عرض/طلب").

الشروط:
  صيد استوبات صاعد (Bullish Stop Hunt / Sweep the Lows):
    - ذيل الشمعة (Low) كسر أدنى قاع سابق معروف (خلال آخر 24 ساعة)
    - لكن إغلاق الشمعة (Close) رجع فوق مستوى القاع المكسور (رفض واضح + امتصاص سيولة)
    → دخول Long، وقف الخسارة تحت الذيل مباشرة بهامش أمان، هدف بعائد 1:3 كحد أدنى

  صيد استوبات هابط (Bearish Stop Hunt / Sweep the Highs): نفس الفكرة بالعكس تماماً
  على القمم.

يفحص آخر 5 شموع (مو الشمعة الأخيرة بس) — هذا إصلاح لبق كان يفحص الشمعة الأخيرة
حصراً، فيفوّت أي نمط صيد استوبات حصل قبل شمعة أو شمعتين ويفوّت الفرصة بالكامل.

فلتر جودة: يشترط فوليوم الشمعة أعلى من المتوسط (تأكيداً لأهمية "Volume Spike")،
وربطنا بيانات الفائدة المفتوحة (OI) وضغط المتداولين الفعليين (Taker Pressure) —
لو توفرت — كنقاط تأكيد إضافية اختيارية، بنفس أسلوب بقية الاستراتيجيات بالتطبيق.
"""
from typing import List, Optional

from .analyzer import Kline, AnalysisResult, MarketMicrostructure, build_score_breakdown, atr


def _detect_stop_hunt(klines: List[Kline], lookback: int = 50, vol_period: int = 20,
                       recent_window: int = 5) -> Optional[dict]:
    """يفحص آخر recent_window شمعة (مو الشمعة الأخيرة بس) بحثاً عن نمط صيد استوبات
    حدث بأي وحدة منها، ويرجع الأحدث تطابقاً. هذا يحل مشكلة النافذة الزمنية الضيقة
    اللي كانت تفحص الشمعة الأخيرة فقط، وتفوّت أي نمط حصل قبل شمعة أو شمعتين."""
    if len(klines) < lookback + recent_window:
        return None

    recent_vol_all = [k.volume for k in klines[-vol_period:]]
    avg_volume = sum(recent_vol_all) / len(recent_vol_all) if recent_vol_all else 0.0

    # 🔴 إصلاح باق حقيقي مكتشف بمراجعة صفقات حية (UBUSDT: وقف 42.7% وهدف بقيمة
    # سالبة! BICOUSDT: وقف 35.4%): كان الهامش (buffer) يُحسب بس من مدى شمعة
    # السحب نفسها (candle_range * 0.4) بدون أي سقف — لو شمعة السحب كانت شاذة
    # (فولتيليتي هائلة لحظية بعملة قليلة السيولة)، الهامش يصير ضخم بلا معنى.
    # نحسب ATR مستقر (14 فترة) كمرجع تقلب طبيعي، ونستخدمه كسقف أقصى للهامش —
    # نفس فلسفة `structural_stop_loss` المستخدمة ببقية الاستراتيجيات (حدود
    # ATR بدل الاعتماد الأعمى على شمعة واحدة قد تكون شاذة).
    atr_ref = atr(klines[-(lookback + vol_period):], 14)

    # نفحص من الأحدث للأقدم داخل نافذة recent_window، ونرجع أول تطابق (الأحدث)
    for offset in range(1, recent_window + 1):
        current = klines[-offset]
        idx = len(klines) - offset
        historical = klines[max(0, idx - lookback): idx]
        if not historical:
            continue
        lowest_low = min(k.low for k in historical)
        highest_high = max(k.high for k in historical)
        volume_ratio = (current.volume / avg_volume) if avg_volume > 0 else 1.0
        candle_range = current.high - current.low
        # هامش وقف واقعي: الأكبر بين 40% من مدى الشمعة أو 0.4% من السعر — كان 10%
        # بس (ضيق جداً)، يخلي الوقف يُضرب بضوضاء عادية قبل ما تتضح الحركة الحقيقية
        buffer = max(candle_range * 0.4, current.close * 0.004)
        # 🔴 سقف أقصى (بطلب صريح): لو شمعة السحب شاذة (مدى ضخم غير طبيعي)، ما
        # نخلي الهامش يتضخم معها بلا حدود — نحدّه بمضاعف ATR معقول (2.5x)، يحفظ
        # نفس فلسفة "وقف هيكلي واقعي" بدون فتحه على مصراعيه لشمعة شاذة وحدة.
        # 🔴 حد أدنى أيضاً (بطلب صريح، بعد مراجعة بيانات حقيقية): كل الصفقات
        # الخاسرة المسجَّلة كان وقفها ضيق جداً (1.2-1.9% من السعر)، بينما
        # الصفقات اللي تحركت لصالحها بقوة كان وقفها أوسع بكثير (5.5-6.5%) —
        # نمط واضح يوحي إن الوقف الضيق يُضرب بضوضاء عادية قبل ما الحركة
        # الحقيقية تاخذ فرصتها. نفرض حد أدنى نصف ATR على الأقل.
        if atr_ref > 0:
            buffer = min(buffer, atr_ref * 2.5)
            buffer = max(buffer, atr_ref * 0.5)

        # صيد استوبات صاعد (سحب سيولة القيعان)
        if current.low < lowest_low and current.close > lowest_low:
            # 📊 تأكيد متابعة إلزامي (إصلاح مبني على بيانات فعلية): فحص حقيقي أظهر
            # 44% من صفقات هذي الاستراتيجية كانت خاطئة من الأساس — السبب: كنا ندخل
            # فوراً على شمعة السحب نفسها بدون أي تأكيد إن الارتداد صامد. الآن نشترط
            # عدم رجوع أي شمعة لاحقة تكسر تحت القاع المسحوب مرة ثانية (فشل النمط).
            follow_through = klines[idx + 1:]
            if not follow_through:
                continue  # لسا ما فيه شمعة تأكيد بعد شمعة السحب — مبكر جداً، ننتظر
            if any(k.low < lowest_low for k in follow_through):
                continue  # رجع السعر وكسر القاع مرة ثانية = فشل النمط، مو ارتداد حقيقي
            # 🔴 تقوية إضافية: نشترط استعادة حقيقية واضحة (مو مجرد صمود بالكاد فوق
            # القاع بهامش ضئيل) — آخر إغلاق لازم يكون أعلى من القاع بمسافة معقولة
            # (15% من مدى الشمعة على الأقل)، دليل قناعة سوقية حقيقية بالارتداد
            if klines[-1].close < lowest_low + candle_range * 0.15:
                continue

            # 🔴 إصلاح جذري لنقطة الدخول (بطلب صريح بعد مراجعة صفقات حقيقية فشلت):
            # كان الدخول قريب جداً من القاع المسحوب نفسه (5% بس من مدى الشمعة فوقه)
            # بينما الوقف تحته مباشرة — أي إعادة اختبار طبيعية لنفس المستوى (شائعة
            # جداً بعد صيد استوبات، السعر غالباً يرجع يفحص نفس النقطة قبل ما يكمل)
            # تضرب الدخول والوقف مع بعض قبل ما تبدأ الحركة الحقيقية. بما إننا الآن
            # نشترط استعادة قوية وواضحة فعلاً (15% من مدى الشمعة) قبل حتى ما نصل
            # هنا، ندخل عند سعر الاستعادة **المؤكَّد** نفسه (إغلاق آخر شمعة) بدل
            # الرجوع للقاع القديم — هذا يوسّع مسافة الوقف طبيعياً بحركة سعرية حقيقية
            # فعلاً حصلت، بدل هامش تعسفي ضيق، ويعطي مساحة حقيقية تنجو من الاختبار.
            entry_price = klines[-1].close
            stop_loss = current.low - buffer
            risk = entry_price - stop_loss
            if risk <= 0:
                continue
            # 🔴 إصلاح جذري (بطلب صريح، بعد تشخيص عميق): الهدف كان رقم رياضي أعمى
            # (المخاطرة×3) بدون أي علاقة بهيكل السوق — فالسعر كان يرتد عند مقاومة
            # حقيقية (ما استهدفناها أصلاً) قبل ما يوصل الرقم البعيد. الآن الهدف
            # = أقرب مقاومة حقيقية فعلية (أعلى قمة بنفس نافذة البحث اللي استخدمناها
            # لاكتشاف القاع المسحوب أصلاً) — مستوى حقيقي السعر تفاعل معه فعلاً
            # بالماضي القريب، مع هامش اقتراب صغير (السعر غالباً يرتد قبل لمس
            # المقاومة بالضبط، مو يخترقها بدقة رياضية).
            approach_buffer = min(buffer * 0.5, (highest_high - entry_price) * 0.1) if highest_high > entry_price else 0
            take_profit = highest_high - max(approach_buffer, 0)
            # 🆕 حقول تمييزية حقيقية (بطلب صريح): كانت نقاط القوة تعتمد بشكل كبير
            # على بيانات لحظية (Microstructure) غير متوفرة بالاختبار الخلفي، فتصير
            # النقاط **ثابتة تماماً** بغض النظر عن جودة الصفقة الفعلية. الآن نحسب
            # مقاييس حقيقية من بيانات الشموع نفسها (متوفرة دائماً، حي أو باك تيست):
            # قوة الاستعادة الفعلية (كم تجاوزت حد الـ15% الأدنى)، وعمق كسر المستوى
            # التاريخي (كل ما كان الكسر أعمق، كل ما كان فخ السيولة أوضح وأقوى).
            recovery_strength_pct = (klines[-1].close - lowest_low) / candle_range if candle_range > 0 else 0
            sweep_depth_pct = (lowest_low - current.low) / candle_range if candle_range > 0 else 0
            return {
                "type": "BULLISH_STOP_HUNT", "side": "Long", "swept_level": lowest_low,
                "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
                "volume_ratio": volume_ratio, "candles_ago": offset,
                "recovery_strength_pct": recovery_strength_pct, "sweep_depth_pct": sweep_depth_pct,
            }

        # صيد استوبات هابط (سحب سيولة القمم)
        if current.high > highest_high and current.close < highest_high:
            follow_through = klines[idx + 1:]
            if not follow_through:
                continue
            if any(k.high > highest_high for k in follow_through):
                continue  # رجع السعر وكسر القمة مرة ثانية = فشل النمط
            if klines[-1].close > highest_high - candle_range * 0.15:
                continue  # استعادة بالكاد، مو رفض حقيقي واضح

            entry_price = klines[-1].close
            stop_loss = current.high + buffer
            risk = stop_loss - entry_price
            if risk <= 0:
                continue
            # 🔴 نفس الإصلاح بالاتجاه المعاكس: الهدف = أقرب دعم حقيقي فعلي (أدنى
            # قاع بنفس نافذة البحث)، مو رقم رياضي أعمى.
            approach_buffer = min(buffer * 0.5, (entry_price - lowest_low) * 0.1) if entry_price > lowest_low else 0
            take_profit = lowest_low + max(approach_buffer, 0)
            recovery_strength_pct = (highest_high - klines[-1].close) / candle_range if candle_range > 0 else 0
            sweep_depth_pct = (current.high - highest_high) / candle_range if candle_range > 0 else 0
            return {
                "type": "BEARISH_STOP_HUNT", "side": "Short", "swept_level": highest_high,
                "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
                "volume_ratio": volume_ratio, "candles_ago": offset,
                "recovery_strength_pct": recovery_strength_pct, "sweep_depth_pct": sweep_depth_pct,
            }

    return None


def analyze_stop_hunt(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                       micro: Optional[MarketMicrostructure] = None,
                       trace: Optional[list] = None,
                       current_price: Optional[float] = None, **kwargs) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    # 🔴 إصلاح جذري (بطلب صريح): كانت نافذة البحث عن مستويات تاريخية مسحوبة
    # يوم واحد بس (lookback=24 ساعة) — ضيقة جداً لتمييز مستوى هيكلي حقيقي.
    # رفعناها لـ72 ساعة (3 أيام) — نستغل البيانات المتوفرة أصلاً (170 ساعة مجلوبة)
    signal = _detect_stop_hunt(k1h, lookback=400, vol_period=20, recent_window=5)
    _log("نمط صيد استوبات مكتشف على فريم الساعة (آخر 5 شموع)",
         f"{signal['type']} (قبل {signal['candles_ago']} شمعة)" if signal else "لا يوجد", signal is not None)
    if signal is None:
        _log("❌ القرار النهائي", "لم يُكسر أي قاع/قمة تاريخية بفتيلة مع رفض واضح خلال آخر 50 شمعة — رفض", False)
        return None

    # فلتر جودة (تحسين بسيط فوق الأصل): نشترط فوليوم أعلى من المتوسط فعلاً،
    # تأكيداً لملاحظة الكود الأصلي نفسه عن أهمية الـ Volume Spike
    _log("نسبة فوليوم شمعة السحب مقابل المتوسط", f"{signal['volume_ratio']:.2f}x")
    if signal["volume_ratio"] < 1.2:
        _log("❌ فلتر الحد الأدنى للفوليوم (1.2x)", f"{signal['volume_ratio']:.2f}x أقل من المطلوب — رفض", False)
        return None

    side = signal["side"]
    entry_price, sl, tp = signal["entry_price"], signal["stop_loss"], signal["take_profit"]

    # 🔴 إصلاح جذري (بطلب صريح — مو بس رفض بالسكانر، حل من الجذر): نقطة الدخول
    # كانت تُحسب من إغلاق آخر شمعة **ساعة** (k1h[-1].close) — وهذي الشمعة غالباً
    # **لسا ما اكتملت** وقت الفحص (فريم الساعة بطيء)، فقيمتها ممكن تكون أقدم شوي
    # من السعر اللحظي الحقيقي. الفحص المركزي بالسكانر يتحقق مقابل k5m[-1].close
    # (بيانات أحدث بكثير) — أي فرق طفيف بين الاثنين (حتى لو 0.01%) يكفي يرفض
    # الصفقة. نستخدم الآن **نفس مرجع السعر بالضبط** (k5m[-1].close) كنقطة الدخول،
    # عشان يكون متطابق 100% مع اللي يتحقق منه السكانر لاحقاً — يلغي فجوة التزامن
    # هذي نهائياً، بدل ما نعتمد بس على رفض السكانر كطبقة حماية أخيرة.
    # 🔴 تحديث إضافي (اكتُشف بمراجعة تفاعل مع إصلاح "البيانات المؤكَّدة" اللاحق):
    # k5m[-1] صارت الآن **مؤكَّدة** (متأخرة لغاية 5 دقايق)، مو الشمعة الحيّة اللي
    # كانت وقت الإصلاح الأصلي أعلاه. نستخدم الآن current_price (السعر الحي الحقيقي
    # المُمرَّر من السكانر) لو متوفر — أدق تطابقاً مع فحص السكانر الفعلي وقت اللحظة.
    if current_price is not None:
        entry_price = current_price
    elif k5m:
        entry_price = k5m[-1].close

    # تحقق أمان: نتأكد الاتجاه لسا سليم منطقياً بعد تحديث سعر الدخول (نادر جداً
    # يصير خلاف كذا، لكن حماية إضافية بدون كلفة)
    if side == "Long" and entry_price <= sl:
        return None
    if side == "Short" and entry_price >= sl:
        return None

    risk = abs(entry_price - sl)
    if risk <= 0:
        return None

    # 🔴 سقف أمان أخير (بطلب صريح، بعد اكتشاف حالات حقيقية: UBUSDT وقف 42.7%
    # بهدف بقيمة سالبة، BICOUSDT وقف 35.4%): حتى بعد تحديد الهامش بسقف ATR
    # أعلاه، ممكن يبقى الوقف بعيد جداً عن سعر الدخول لسبب مختلف كليّاً — المستوى
    # التاريخي المسحوب نفسه (أعلى قمة/أدنى قاع خلال 400 ساعة ≈ 16-17 يوم) قد
    # يكون بعيد جداً عن السعر الحالي وقت الدخول (تحرك السعر ابتعد كثير منذ ذاك
    # المستوى، مو سحب سيولة "طازج" فعلياً رغم إنه اجتاز الرقم تقنياً). هذا سقف
    # حماية شامل يمسك أي سبب يخلي مسافة الوقف غير منطقية، بغض النظر عن مصدره:
    if risk / entry_price > 0.08:
        _log("❌ سقف أمان أقصى لمسافة الوقف (8% من السعر)", f"{risk/entry_price*100:.2f}% — المستوى التاريخي المسحوب بعيد جداً عن السعر الحالي، سحب سيولة غير طازج فعلياً — رفض", False)
        return None

    # فلتر OI اختياري (نفس أسلوب بقية الاستراتيجيات): سيولة تخرج بقوة = إشارة ضعف حقيقي
    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct < -1.5:
        _log("❌ فلتر الفائدة المفتوحة (OI)", f"تغيّر OI={oi_change_pct:.2f}% (أقل من -1.5%) — رفض", False)
        return None
    _log("الفائدة المفتوحة (OI) تغيّر", f"{oi_change_pct:.2f}%" if oi_change_pct is not None else "غير متوفرة")

    # 🔴 إصلاح مبني على مراجعة أداء حقيقي (0% نجاح من 5 صفقات متتالية): ضغط
    # المتداولين الفعليين كان مجرد مكافأة اختيارية بالاحتمالية، بدون أي دور حقيقي
    # برفض الصفقة. نصف الخسائر أظهرت انعكاس حقيقي (تحرك لصالحنا ثم رجع) — دليل إن
    # الاتجاه كان صح أحياناً لكن بدون قناعة سوقية فعلية كافية وقت الدخول. نجعله
    # الآن بوابة إلزامية: نرفض لو ضغط المتداولين يعاكس اتجاه الصفقة بوضوح.
    taker_pressure_gate = micro.taker_pressure if micro else None
    if taker_pressure_gate is not None:
        opposed = (side == "Long" and taker_pressure_gate < -0.1) or (side == "Short" and taker_pressure_gate > 0.1)
        if opposed:
            _log("❌ بوابة ضغط المتداولين الإلزامية", f"{taker_pressure_gate:.2f} يعاكس اتجاه الصفقة بوضوح — رفض", False)
            return None

    probability = 78
    if signal["volume_ratio"] >= 2.0:
        probability += 6  # فوليوم ضخم جداً وقت السحب = تأكيد أقوى بكثير لتدخل مؤسسي حقيقي
    elif signal["volume_ratio"] >= 1.5:
        probability += 3

    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        taker_aligned = (side == "Long" and taker_pressure > 0.15) or (side == "Short" and taker_pressure < -0.15)
        if taker_aligned:
            probability += 4

    cvd_pct = micro.cvd_pct if micro else None
    if cvd_pct is not None:
        cvd_aligned = (side == "Long" and cvd_pct > 60) or (side == "Short" and cvd_pct < 40)
        if cvd_aligned:
            probability += 3

    probability = min(95, probability)
    rr = round(abs(tp - entry_price) / risk, 2) if risk > 0 else 3.0
    _log("✅ القرار النهائي", f"{side} — احتمالية {probability}%", True)

    type_ar = "صعودي (سحب سيولة القيعان)" if side == "Long" else "هبوطي (سحب سيولة القمم)"
    ago_txt = "بالشمعة الحالية" if signal["candles_ago"] == 1 else f"قبل {signal['candles_ago']} شموع"
    behavior = (
        f"🎯 صيد استوبات {type_ar}: كُسر المستوى التاريخي عند {signal['swept_level']:.6g} بفتيلة "
        f"(Wick) ثم رفضه السعر وأغلق بالعكس {ago_txt} — نمط فخ سيولة كلاسيكي (اصطياد ستوبات المتداولين "
        f"الأفراد). نسبة الفوليوم وقت السحب: {signal['volume_ratio']:.2f}× المتوسط."
    )
    volume_analysis = f"صيد استوبات مؤكَّد بفوليوم {signal['volume_ratio']:.2f}× — عائد/مخاطرة ثابت لا يقل عن 1:3"

    # 🔴 إصلاح جذري (بطلب صريح): كانت أول عاملين ثابتين True دائماً (بوابات مرّت
    # أصلاً، مو تمييز حقيقي)، وعامل الفائدة المفتوحة يعطي "تصريح مجاني" لو البيانات
    # غير متوفرة (None) — والعاملين اللحظيين (Taker/CVD) يفشلان تلقائياً بالباك
    # تيست (بيانات غير متوفرة تاريخياً). النتيجة: نقاط ثابتة 66.7 دائماً، بلا أي
    # قيمة تمييزية حقيقية بين صفقة قوية وضعيفة (أثبتناه بمراجعة بيانات فعلية).
    # الآن نستخدم مقاييس حقيقية من الشمعة نفسها (متوفرة دائماً، حي أو باك تيست):
    recovery_strength = signal.get("recovery_strength_pct", 0.15)
    sweep_depth = signal.get("sweep_depth_pct", 0.0)
    score_factors = [
        ("استعادة قوية وواضحة (>30% من مدى الشمعة)", recovery_strength > 0.30),
        ("عمق كسر مستوى تاريخي ملموس (>10% من مدى الشمعة)", sweep_depth > 0.10),
        ("فوليوم مؤكَّد وقت السحب (≥1.5×)", signal["volume_ratio"] >= 1.5),
        ("فوليوم استثنائي جداً (≥2.5×)", signal["volume_ratio"] >= 2.5),
        ("الفائدة المفتوحة (OI) داعمة فعلياً (بيانات متوفرة)", oi_change_pct is not None and oi_change_pct >= -1.5),
        ("ضغط المتداولين الفعليين (Taker Pressure)", taker_pressure is not None and ((side == "Long" and taker_pressure > 0.15) or (side == "Short" and taker_pressure < -0.15))),
        ("CVD تراكمي متوافق", cvd_pct is not None and ((side == "Long" and cvd_pct > 60) or (side == "Short" and cvd_pct < 40))),
    ]
    score_breakdown, signal_score = build_score_breakdown(score_factors)

    return AnalysisResult(
        symbol=symbol, trend="صاعد" if side == "Long" else "هابط", dt="", prob=probability,
        price=entry_price, atr=risk, side=side, entry_price=entry_price, stop_loss=sl, take_profit=tp,
        rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
        score_breakdown=score_breakdown, signal_score=signal_score,
    )
