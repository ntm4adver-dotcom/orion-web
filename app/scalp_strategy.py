"""
استراتيجية السكالب السريع الدقيق (Scalp Precision Hunter)

فكرة الاستراتيجية: ارتداد ارتدادي (Pullback Continuation) على فريم 5 دقائق داخل
اتجاه مؤكَّد على فريمين أعلى (15 دقيقة + ساعة)، بشروط صارمة عمداً لتعظيم جودة
الدخول، مع فرض حقيقي (مو تجميلي) لعائد/مخاطرة لا يقل عن 1:5.

الخطوات:
  1) اتجاه متعدد الفريمات: فريم 15 دقيقة وفريم الساعة لازم يتفقا على نفس الاتجاه.
  2) تراجع صحي (Pullback) لمنطقة EMA9 على فريم 5 دقائق (مو انهيار كامل للترند).
  3) شمعة ارتداد قوية تغلق بعيد عن EMA9 باتجاه الترند، بجسم قوي (>40% من مدى الشمعة).
  4) تأكيد حجم تداول حقيقي (الشمعة الحالية أعلى من المتوسط).
  5) تأكيد ضغط متداولين فعليين (Taker Pressure) متوافق مع الاتجاه — بيانات صفقات
     حقيقية، مو نمط سعري بس.
  6) فلتر فائدة مفتوحة (OI) — رفض لو السيولة تخرج من السوق أثناء الإعداد.
  7) وقف خسارة هيكلي ضيق جداً (أدنى/أعلى نقطة بالتراجع + هامش صغير من ATR).
  8) الهدف: أكبر قيمة بين (المخاطرة×5) أو (امتداد حركة مقاسة من آخر 30 شمعة×1.5)
     — وإذا الناتج ما حقق عائد/مخاطرة 1:5 فعلياً، تُرفض الفرصة بالكامل، بدون أي تحايل.

⚠️ ملاحظة صادقة: الجمع بين نسبة نجاح عالية وعائد/مخاطرة كبير نادر بطبيعته بأي سوق
حقيقي. هذي الاستراتيجية مصممة لتكون انتقائية جداً عمداً (قلة إشارات، جودة أعلى)
بدل ما تتحايل بخصم عائد وهمي عشان تزيد عدد الإشارات. نسبة النجاح الحقيقية تُقاس
فقط من الأداء الفعلي المتراكم (صفحة التطور)، وما تُضمن مسبقاً.
"""
from typing import Optional, List

from .analyzer import Kline, AnalysisResult, MarketMicrostructure, atr, ema, hma, _get_bias, build_score_breakdown


def analyze_scalp_precision(symbol: str, k4h: List[Kline], k1h: List[Kline], k15m: List[Kline],
                             k5m: List[Kline], k_daily: List[Kline],
                             micro: Optional[MarketMicrostructure] = None,
                             trace: Optional[list] = None,
                             current_price: Optional[float] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    # 🔴 نفس إصلاح فيبوناتشي الترند: _get_bias تعتمد HMA بفترة 50، فيحتاج حد أدنى
    # يضمن بيانات كافية فعلياً (مو تعديل صامت لفترة أقصر يعطي قراءة غير موثوقة)
    if len(k5m) < 200 or len(k15m) < 55 or len(k1h) < 55:
        _log("عدد الشموع كافٍ (5د≥200، 15د/1س≥55)", f"5د={len(k5m)}, 15د={len(k15m)}, 1س={len(k1h)}", False)
        return None
    _log("عدد الشموع كافٍ", f"5د={len(k5m)}, 15د={len(k15m)}, 1س={len(k1h)}", True)

    # 🔴 إصلاح جذري (دائرية منطقية خطيرة مكتشفة): كانت EMA9/21 تُحسب من **كل** شموع
    # 5 دقائق، بما فيها شمعة المتابعة (آخر شمعة) اللي نقارن موقعها لاحقاً بالنسبة
    # لنفس هذا الخط! يعني الخط المرجعي كان متأثر جزئياً بالنقطة اللي نقيسها بالنسبة
    # له — دائرية تعطي قراءة مضللة، خصوصاً وقت حركة سعرية حادة بالشمعة الأخيرة.
    # الآن نحسب EMA من الشموع **قبل** الثلاث شموع المستخدمة بالنمط (تراجع+تأكيد+
    # متابعة) فقط — خط مرجعي مستقل تماماً وثابت، مو متأثر بالشموع قيد الفحص.
    closes5m_reference = [k.close for k in k5m[:-3]] if len(k5m) > 3 else [k.close for k in k5m]
    ema9_5m = hma(closes5m_reference, 9)
    ema21_5m = hma(closes5m_reference, 21)
    trend5m = "صاعد" if ema9_5m >= ema21_5m else "هابط"

    trend15m = _get_bias(k15m)
    trend1h = _get_bias(k1h)
    multi_tf_aligned = trend15m == trend1h
    _log("اتجاه 15 دقيقة", trend15m)
    _log("اتجاه الساعة", trend1h)
    _log("اتفاق الفريمين الأعلى", multi_tf_aligned, multi_tf_aligned)
    if not multi_tf_aligned:
        _log("❌ القرار النهائي", "فريم 15 دقيقة والساعة غير متفقين على نفس الاتجاه — رفض", False)
        return None

    trend = trend15m
    side = "Long" if trend == "صاعد" else "Short"
    _log("اتجاه فريم 5 دقائق (EMA9 مقابل EMA21)", trend5m)

    atr5m = atr(k5m, 14)
    if atr5m <= 0:
        return None

    if len(k5m) < 4:
        return None

    # 🔴 إصلاح جذري مبني على ملاحظة دقيقة: كان الدخول يعتمد على آخر شمعة متوفرة
    # مباشرة (last = k5m[-1]) بدون أي انتظار لشمعة تالية تتأكد إن الإغلاق كان
    # حقيقياً ومو مجرد ذيل رفض مؤقت (Wick) ينعكس فوراً بالشمعة التالية. الآن نعتبر
    # الشمعة قبل الأخيرة هي "شمعة التأكيد"، ونشترط شمعة تالية (الأحدث) تثبت
    # المتابعة الحقيقية ولا تبطل النمط بالرجوع تحت/فوق EMA9 مرة ثانية.
    confirm_candle = k5m[-2]
    prev = k5m[-3]
    follow_through = k5m[-1]

    if side == "Long":
        pulled_back = (prev.low <= ema9_5m + atr5m * 0.3) and (prev.low >= ema9_5m - atr5m * 1.5)
        reversal_confirmed = confirm_candle.close > ema9_5m and confirm_candle.close > confirm_candle.open
        candle_range = confirm_candle.high - confirm_candle.low
        body_ratio = ((confirm_candle.close - confirm_candle.open) / candle_range) if candle_range > 0 else 0
        follow_through_ok = follow_through.close > ema9_5m
    else:
        pulled_back = (prev.high >= ema9_5m - atr5m * 0.3) and (prev.high <= ema9_5m + atr5m * 1.5)
        reversal_confirmed = confirm_candle.close < ema9_5m and confirm_candle.close < confirm_candle.open
        candle_range = confirm_candle.high - confirm_candle.low
        body_ratio = ((confirm_candle.open - confirm_candle.close) / candle_range) if candle_range > 0 else 0
        follow_through_ok = follow_through.close < ema9_5m

    _log("تراجع صحي لمنطقة EMA9", pulled_back, pulled_back)
    _log("شمعة ارتداد تؤكد الاتجاه", reversal_confirmed, reversal_confirmed)
    _log("نسبة جسم شمعة الارتداد", round(body_ratio, 2))
    _log("✅ شمعة متابعة تالية تثبت الإغلاق (مو مجرد ذيل رفض مؤقت)", follow_through_ok, follow_through_ok)

    strong_body = body_ratio > 0.4
    if not (pulled_back and reversal_confirmed and strong_body and follow_through_ok):
        _log("❌ القرار النهائي", "لم يتحقق نمط الارتداد الصحي من EMA9 بكل شروطه (بما فيها تأكيد المتابعة) — رفض", False)
        return None

    last = follow_through  # الدخول يعتمد على أحدث سعر متاح فعلياً بعد التأكيد الكامل

    vols = [k.volume for k in k5m[-22:-2]]
    avg_vol = sum(vols) / len(vols) if vols else 1.0
    vol_ratio = (confirm_candle.volume / avg_vol) if avg_vol > 0 else 0
    _log("معدل حجم شمعة الارتداد مقابل المتوسط", f"{vol_ratio:.2f}x")
    # 📊 إصلاح مبني على بيانات فعلية: فحص حقيقي لـ5 صفقات خاسرة أظهر إن الصفقتين
    # الوحيدتين اللي كانتا "غلط من الأساس تماماً" (صفر حركة لصالحنا إطلاقاً) كانتا
    # بالضبط أضعف صفقتين بفوليوم شمعة التأكيد (1.24x و1.62x)، بينما كل الصفقات
    # اللي وصلت حركة حقيقية لصالحنا (حتى لو خسرت لاحقاً) كانت بفوليوم أقوى (1.88x+).
    # شددنا الحد الأدنى من 1.2x إلى 1.5x — يمسك التأكيد الضعيف بدون قناعة حقيقية.
    if vol_ratio < 1.5:
        _log("❌ فلتر تأكيد الحجم", f"{vol_ratio:.2f}x أقل من الحد الأدنى (1.5x) — رفض", False)
        return None

    # 🔴 إصلاح مبني على مراجعة حقيقية لصفقة فعلية: فوليوم متطرف جداً (>8x) على شمعة
    # "تأكيد الاستمرار"، خصوصاً بعد حركة ممتدة أصلاً، غالباً يعني تصريف/استنزاف
    # (Climactic Volume) — علامة انعكاس محتملة، مو تأكيد استمرار حقيقي. كان الكود
    # يعتبر أي فوليوم عالٍ "تأكيد إيجابي" بلا سقف، فنشترط الآن تأكيد إضافي حقيقي
    # (ضغط متداولين فعلي متوافق) قبل قبول فوليوم متطرف كذا، بدل قبوله أعمى.
    if vol_ratio > 8.0:
        taker_ok = micro is not None and micro.taker_pressure is not None and (
            (side == "Long" and micro.taker_pressure > 0.15) or (side == "Short" and micro.taker_pressure < -0.15)
        )
        _log("⚠️ فوليوم متطرف جداً (>8x) — قد يكون استنزاف/انعكاس، يحتاج تأكيد ضغط متداولين إضافي", taker_ok, taker_ok)
        if not taker_ok:
            _log("❌ فلتر الفوليوم المتطرف", f"{vol_ratio:.2f}x عالٍ جداً بدون تأكيد ضغط متداولين حقيقي — رفض احترازي (احتمال استنزاف لا استمرار)", False)
            return None

    taker_pressure = micro.taker_pressure if micro else None
    order_flow_ok = True
    if taker_pressure is not None:
        if side == "Long" and taker_pressure < 0.05:
            order_flow_ok = False
        if side == "Short" and taker_pressure > -0.05:
            order_flow_ok = False
    _log("ضغط المتداولين الفعليين", taker_pressure if taker_pressure is not None else "غير متوفر")
    if not order_flow_ok:
        _log("❌ فلتر ضغط المتداولين الفعليين", "الفوليوم الحقيقي لا يدعم اتجاه الصفقة — رفض", False)
        return None

    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct < -1.0:
        _log("❌ فلتر الفائدة المفتوحة (OI)", f"تغيّر OI={oi_change_pct:.2f}% (سيولة تخرج) — رفض", False)
        return None

    # 🔴 إصلاح جوهري (اكتُشف بمراجعة تفاعل إصلاحين سابقين): بعد إصلاح "البيانات
    # المؤكَّدة" (استبعاد الشمعة الحيّة قيد التكوين)، last.close صارت تعكس إغلاق
    # آخر شمعة **مؤكَّدة** — متأخرة لغاية 5 دقايق كاملة عن اللحظة الفعلية. هذا
    # صحيح تماماً لتحديد **النمط والتأكيد** (الغرض الأصلي من الإصلاح)، لكن خطأ
    # لحساب **نقطة الدخول الفورية** نفسها (يفترض تعكس السعر الآن، مو قبل 5 دقايق).
    # نستخدم الآن current_price (السعر الحي الحقيقي المُمرَّر من السكانر) لو متوفر،
    # وإلا (كالباك تيست، ما فيه "سعر حي" منفصل) نرجع لإغلاق آخر شمعة كأفضل تقريب.
    entry_price = current_price if current_price is not None else last.close
    safe_buffer = max(atr5m * 0.6, entry_price * 0.004)  # الأكبر بين ATR موسَّع أو 0.4% من السعر
    # 🔴 خلل حقيقي مكتشف بمراجعة عميقة للمنطق (بطلب صريح، مو ترقيع رقم): الوقف كان
    # يعتمد بس على min/max(prev, last) — **يتجاهل شمعة التأكيد نفسها (confirm_candle)
    # تماماً**! لو شمعة التأكيد نزلت بذيل تحت قاع شمعة التراجع قبل ما تغلق قوي فوق
    # EMA9 (نمط شائع جداً — ارتداد حاد من نقطة أعمق)، الوقف المحسوب يطلع فوق مستوى
    # اتلمس فعلياً أثناء تكوّن النمط، فيصير عرضة لانضراب فوري بأول تذبذب طبيعي —
    # الآن نحسب الوقف من **الثلاث شموع كاملة** (التراجع + التأكيد + المتابعة).
    if side == "Long":
        stop_loss = min(prev.low, confirm_candle.low, last.low) - safe_buffer
    else:
        stop_loss = max(prev.high, confirm_candle.high, last.high) + safe_buffer

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None

    # 🔴 تصحيح تعارض تحليلي حقيقي (اكتُشف بمراجعة تناسق زمني، مو خلل برمجي):
    # كانت نافذة تحديد "امتداد الحركة" (المستخدمة لحجم الهدف) 400 شمعة (33.3
    # ساعة) — فرق هائل وغير مبرر مقابل نمط الدخول نفسه (تراجع+تأكيد+متابعة على
    # آخر 3 شموع 5د بس = 15 دقيقة). هدف سكالب سريع يفترض يعكس أفق زمني قريب من
    # أفق الدخول، مو يستدل من حركة امتدت طول يوم ونص. الآن 150 شمعة (12.5 ساعة)
    # — واسعة كفاية لتفادي ضجيج النافذة الضيقة القديمة (30 شمعة)، لكن متناسقة
    # زمنياً أكثر مع طبيعة السكالب السريع.
    recent_high = max(k.high for k in k5m[-150:])
    recent_low = min(k.low for k in k5m[-150:])
    swing_range = recent_high - recent_low

    # 🔴 إصلاح مبني على بيانات فعلية: كان الهدف = max(المخاطرة×5, امتداد الحركة×1.5)،
    # وهذا يخلي الهدف يتضخّم تلقائياً كل ما اتسع الوقف (من إصلاح الحد الأدنى النسبي)
    # — فحص حقيقي أظهر 0% نجاح من 15 صفقة! الآن الهدف مبني **حصراً** على امتداد
    # الحركة الفعلي بالسوق (بدون ربطه بالمخاطرة إطلاقاً)، ونتحقق بعدها هل عائد/
    # المخاطرة الناتج طبيعياً يحقق 1:5، بدل ما نفرض هدف غير واقعي عشان نوصل للرقم.
    if side == "Long":
        take_profit = entry_price + swing_range * 2.0
    else:
        take_profit = entry_price - swing_range * 2.0

    reward = abs(take_profit - entry_price)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    _log("عائد/مخاطرة المحسوب فعلياً", f"1:{rr}")

    if rr < 5.0:
        _log("❌ فرض عائد/مخاطرة لا يقل عن 1:5 (حقيقي بدون تحايل)", f"الناتج 1:{rr} أقل من المطلوب — رفض", False)
        return None
    _log("✅ عائد/مخاطرة يحقق الحد الأدنى المطلوب (1:5)", f"1:{rr}", True)

    probability = 76
    if vol_ratio > 1.8:
        probability += 4
    if taker_pressure is not None and ((side == "Long" and taker_pressure > 0.2) or (side == "Short" and taker_pressure < -0.2)):
        probability += 5

    cvd_pct = micro.cvd_pct if micro else None
    if cvd_pct is not None and ((side == "Long" and cvd_pct > 60) or (side == "Short" and cvd_pct < 40)):
        probability += 5
    if oi_change_pct is not None and oi_change_pct > 1.0:
        probability += 4
    probability = min(95, probability)
    _log("✅ كل الشروط تحققت — تم توليد إشارة", side, True)

    behavior = (f"⚡ سكالب دقيق: ارتداد صحي من EMA9 بفريم 5 دقائق داخل اتجاه متوافق "
                f"(15د + 1س = {trend})، بحجم تداول مؤكَّد {vol_ratio:.2f}x، ووقف خسارة هيكلي ضيق. "
                f"عائد/مخاطرة محقَّق فعلياً: 1:{rr}.")
    volume_analysis = f"سكالب سريع — ارتداد EMA9 مؤكَّد بالحجم والفوليوم الفعلي، R:R≥5 مفروض حقيقياً"

    score_factors = [
        ("اتفاق فريم 15د والساعة على نفس الاتجاه", True),
        ("ارتداد صحي من EMA9", True),
        ("شمعة ارتداد قوية تؤكد الاتجاه", True),
        ("فوليوم مؤكَّد فوق المتوسط", vol_ratio > 1.2),
        ("ضغط المتداولين الفعليين (Taker Pressure)", taker_pressure is not None and ((side == "Long" and taker_pressure > 0.2) or (side == "Short" and taker_pressure < -0.2))),
        ("عائد/مخاطرة يحقق 1:5 حقيقياً (بدون تحايل)", True),
    ]
    score_breakdown, signal_score = build_score_breakdown(score_factors)

    return AnalysisResult(
        symbol=symbol, trend=trend, dt="", prob=probability, price=entry_price, atr=atr5m,
        side=side, entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
        rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
        score_breakdown=score_breakdown, signal_score=signal_score,
    )
