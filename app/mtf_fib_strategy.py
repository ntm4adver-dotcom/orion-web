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

from . import db
from .analyzer import Kline, AnalysisResult, MarketMicrostructure, atr, _get_bias, build_score_breakdown, find_significant_swing, find_swing_via_macd


def _find_swing_extremes(window: List[Kline]):
    high_idx = max(range(len(window)), key=lambda i: window[i].high)
    low_idx = min(range(len(window)), key=lambda i: window[i].low)
    return high_idx, window[high_idx].high, low_idx, window[low_idx].low


def analyze_mtf_fib_trend(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                           micro: Optional[MarketMicrostructure] = None,
                           trace: Optional[list] = None,
                           current_price: Optional[float] = None, **kwargs) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    # 🔴 إصلاح جذري (بق حقيقي مكتشف): كان الحد الأدنى 30 شمعة بس، لكن _get_bias
    # تعتمد داخلياً على HMA بفترة 50! لو الشموع المتوفرة بين 30-50، دالة hma()
    # تعدّل الفترة **بصمت** لعدد أقل (مثلاً HMA35 بدل HMA50 الحقيقية) — فتقارن
    # HMA21 حقيقية مقابل HMA50 "مزيّفة"، وتعطي قراءة اتجاه عشوائية غير موثوقة.
    # رفعناه لـ55 (يضمن بعد استبعاد شمعة قيد التكوين، يبقى 54+ — أكثر من كافٍ).
    if len(k15m) < 55 or len(k5m) < 350:
        _log("عدد شموع كافٍ (15د≥55، 5د≥350)", f"15د={len(k15m)}, 5د={len(k5m)}", False)
        return None

    main_trend = _get_bias(k15m)
    _log("الاتجاه الرئيسي (15 دقيقة)", main_trend)

    # 🔴 تصحيح تعارض تحليلي حقيقي (اكتُشف بمراجعة تناسق زمني بين أجزاء الاستراتيجية،
    # مو خلل برمجي): بمرحلة سابقة رفعنا النافذة لـ400 شمعة (33.3 ساعة) بدون فحص
    # تناسقها مع باقي المنطق — وهذا **أوسع زمنياً** من ذاكرة الاتجاه الرئيسي نفسه
    # (HMA50 على 15د ≈ 12.5 ساعة)! غير منطقي: "تراجع حالي" يفترض يقع **داخل**
    # أفق الاتجاه المكتشف، مو يتجاوزه بأكثر من الضعف. الآن 150 شمعة (12.5 ساعة)
    # تطابق أفق HMA50 بالضبط — واسعة كفاية لتفادي ضجيج النافذة الضيقة القديمة
    # (35 شمعة)، لكن متسقة زمنياً مع معنى "تراجع حالي" الحقيقي.
    # 🔴 إصلاح جذري (بناءً على مراجعة شارت حقيقي MUBARAKUSDT): الطريقة القديمة
    # كانت تاخذ أعلى/أدنى نقطة بنافذة ثابتة 150 شمعة بشكل أعمى، بدون أي تحقق
    # هل هذي النقطة فعلاً "بيفوت" هيكلي ذو معنى، أو مجرد تذبذب صغير داخل رينج
    # أوسع بكثير (بالضبط اللي صار: فيبوناتشي ارتسم على نطاق ضيق جداً غير ممثل
    # للحركة الحقيقية). الآن نستخدم find_significant_swing: يشترط انعكاس
    # يتجاوز عتبة ATR (بيفوت هيكلي حقيقي)، ويتحقق إن رجل الحركة نفسها نظيفة
    # اتجاهياً (Efficiency Ratio)، مو رينج متذبذب. لو ما لقى سوينق مؤهل، نرفض
    # الإشارة كاملة بدل ما نفرض سوينق ضعيف الجودة.
    atr_5m = atr(k5m, 14)
    window = k5m[-400:]
    swing = find_significant_swing(window, atr_5m, min_move_atr=3.0, min_leg_efficiency=0.35)
    if swing is None:
        _log("❌ سوينق هيكلي مؤهل (ZigZag + Efficiency Ratio)", "لا يوجد انعكاس بحجم كافٍ أو الحركة غير نظيفة اتجاهياً — رفض", False)
        return None
    high_idx, swing_high = swing["high_idx"], swing["swing_high"]
    low_idx, swing_low = swing["low_idx"], swing["swing_low"]
    swing_range = swing_high - swing_low
    _log("سوينق هيكلي مكتشف (ZigZag)", f"قمة {swing_high:.6g} @ {high_idx} ← قاع {swing_low:.6g} @ {low_idx}, ER={swing['leg_efficiency']:.2f}", True)

    # 🆕 تحقق مزدوج بمصدر مستقل تماماً (بطلب صريح): نفس السوينق لازم "يتأكد"
    # من زاوية ثانية غير السعر الخام — قمة/قاع الزخم الحقيقي بأعمدة هستوجرام
    # MACD (وين الزخم كان يتصاعد ثم بدأ يتباطأ فعلاً، مو مجرد تجاوز عتبة ATR
    # بالسعر). لو المصدرين ما يتفقان على نفس منطقة السوينق تقريباً، معناها
    # السوينق مو واضح/حاسم بما يكفي — نرفض الإشارة بدل رسم فيبوناتشي على نقطة
    # مشكوك فيها (بالضبط سيناريو RAVEUSDT اللي راجعناه).
    macd_swing = find_swing_via_macd(window, lookback=len(window))
    if macd_swing is None:
        _log("❌ تأكيد MACD المستقل للسوينق", "ما لقى قمة/قاع زخم واضح بالهستوجرام — رفض (السوينق غير مؤكَّد من مصدر مستقل)", False)
        db.increment_rejection_counter("mtf_fib_macd_swing_not_found")
        return None
    macd_range = macd_swing["swing_high"] - macd_swing["swing_low"]
    if macd_range <= 0 or swing_range <= 0:
        return None
    # نسبة التداخل بين نطاقي السوينق (ZigZag وMACD) — لازم يكون فيه تداخل حقيقي
    overlap_low = max(swing_low, macd_swing["swing_low"])
    overlap_high = min(swing_high, macd_swing["swing_high"])
    overlap_pct = max(0.0, overlap_high - overlap_low) / min(swing_range, macd_range)
    _log("مقارنة سوينق MACD المستقل", f"قمة {macd_swing['swing_high']:.6g} ← قاع {macd_swing['swing_low']:.6g} | تداخل={overlap_pct:.0%}", overlap_pct >= 0.4)
    if overlap_pct < 0.4:
        _log("❌ تأكيد MACD المستقل للسوينق", f"السوينقين مختلفين جداً (تداخل {overlap_pct:.0%} فقط) — السوينق غير حاسم، رفض", False)
        db.increment_rejection_counter("mtf_fib_macd_swing_disagreement")
        return None

    # 🔴 تقوية دور MACD (بطلب صريح: "اعتمد على أعمدة الماكد لتحديد القمم
    # والقيعان" — مو بس فحص تحقق ثانوي فوق ZigZag). بعد ما يتأكد التوافق،
    # **نستخدم قمة/قاع الزخم المكتشفة من أعمدة MACD نفسها** كالقيم الفعلية
    # لرسم الفيبوناتشي، بدل قيم ZigZag الخام — لأن MACD يحدد فعلياً وين
    # الزخم بدأ يتباطأ (نقطة التحول الحقيقية)، مو مجرد تجاوز عتبة سعر.
    _log("✅ القمة/القاع النهائية المُعتمَدة (من أعمدة MACD)", f"قمة {macd_swing['swing_high']:.6g} ← قاع {macd_swing['swing_low']:.6g}", True)
    swing_high, swing_low = macd_swing["swing_high"], macd_swing["swing_low"]
    high_idx, low_idx = macd_swing["high_idx"], macd_swing["low_idx"]
    swing_range = swing_high - swing_low

    if swing_range <= 0:
        return None

    # 🆕 تأكيد سحب سيولة (Liquidity Sweep) — بطلب صريح: نتحقق هل القاع/القمة
    # المكتشفة كانت فعلاً "سحب سيولة" حقيقي (اختراق تحت/فوق أدنى/أعلى مستوى
    # بنافذة أوسع قبلها مباشرة، يدل على اصطياد أوامر وقف متراكمة)، مو مجرد قاع/قمة
    # محلية عشوائية بلا سياق هيكلي. نضيفها كعامل نقاط إضافي (بونص)، مو شرط إلزامي،
    # عشان نجمع بيانات حقيقية أول قبل ما نقرر رفض الصفقات اللي بدونها.
    # 🔴 إصلاح عكس منطقي حقيقي (اكتُشف بتتبع دقيق للتسلسل، مو خلل برمجي سطحي):
    # الشرط كان `if low_idx < high_idx` يتحقق سحب سيولة "هابط" (تحت القاع) — لكن
    # هذا يتحقق فعلياً بس بسيناريو main_trend=="هابط" (حيث القاع يجي أولاً زمنياً،
    # low_idx<high_idx)! نقطة الانعكاس **المهمة** بسيناريو هابط هي **القمة**
    # (آخر نقطة زمنياً، حيث الاستعادة الهابطة تبدأ)، مو القاع! الكود كان يتحقق
    # من الطرف **المعاكس تماماً** بكلا السيناريوهين. الإصلاح: نتحقق سحب السيولة
    # عند **آخر نقطة زمنياً بالتسلسل** (نقطة الانعكاس الحقيقية)، مو الأولى.
    # 🔴 تصحيح تناسق: بعد التحول لـfind_significant_swing (نافذة متغيرة 400
    # شمعة بدل 150 ثابتة)، pre_window لازم يكون نسبي لبداية السوينق المكتشف
    # فعلياً (min(low_idx, high_idx))، مو تقطيعة ثابتة [-350:-150] كانت مبنية
    # على افتراض النافذة القديمة 150 فقط.
    leg_start_idx = min(low_idx, high_idx)
    pre_window = window[max(0, leg_start_idx - 150):leg_start_idx]
    liquidity_swept = False
    if pre_window:
        if high_idx < low_idx:  # القمة أولاً، القاع أخيراً (main_trend صاعد) — نقطة الانعكاس = القاع
            prior_low = min(k.low for k in pre_window)
            liquidity_swept = swing_low < prior_low
        else:  # القاع أولاً، القمة أخيراً (main_trend هابط) — نقطة الانعكاس = القمة
            prior_high = max(k.high for k in pre_window)
            liquidity_swept = swing_high > prior_high
    _log("🆕 تأكيد سحب سيولة حقيقي (اختراق مستوى هيكلي سابق)", liquidity_swept)

    last_close = window[-1].close
    # 🔴 إصلاح خطأ حقيقي (نفس فئة الباق المكتشف بـclimactic_reversal بعد
    # مراجعة صفقات مغلقة فعلية): "الكسر الحقيقي (CHoCH)" كان يتفعّل بأي تجاوز
    # تافه لقمة/قاع التعافي، حتى لو 0.001% بس — يشعل صفقة على استراحة عابرة
    # تُبتلع فوراً بمواصلة التراجع، بدل انعكاس حقيقي. نشترط هامش حقيقي (ATR).
    atr_break_margin = atr(k5m, 14) * 0.25
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
        genuine_break = last_close > recent_peak_after_low + atr_break_margin
        _log("كسر حقيقي (CHoCH) صاعد يؤكد انتهاء التراجع (بهامش ≥0.25×ATR)", f"إغلاق={last_close:.6g} مقابل قمة تعافي={recent_peak_after_low:.6g}", genuine_break)
        if not genuine_break:
            _log("❌ القرار النهائي", "التراجع لسا ما انكسر كسراً حقيقياً — ننتظر", False)
            return None

        side = "Long"
        # 🔴 إصلاح جذري (بطلب صريح، مبني على مراجعة شارت حقيقية): كان الدخول عند
        # منطقة فيبوناتشي 0.72 **بالضبط** — هذا مستوى كلاسيكي معروف جداً بين
        # المتداولين والخوارزميات (منطقة "Golden Zone")، وبالتالي عرضة جداً لاصطياد
        # سيولة (السعر يوصله، يُصطاد الوقف/الدخول المتجمع هناك، ثم يرتد) — بالضبط
        # النمط اللي لوحظ على الشارت الحقيقي (السعر يتوقف عند نقطة الدخول ويتذبذب
        # أو يسحب سيولة قبل ما يكمل). الآن نطلب اختراق إضافي بسيط **تحت** 0.72
        # (نحو القاع أكثر، محاكاة السحب المتوقع) قبل ما نثق بنقطة الدخول — ندخل
        # بعد التأكيد، مو عند المستوى المعروف مباشرة.
        fib_072 = swing_low + 0.72 * swing_range
        liquidity_sweep_margin = swing_range * 0.04  # هامش 4% من مدى الحركة الكاملة
        entry_price = fib_072 - liquidity_sweep_margin
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
        genuine_break = last_close < recent_trough_after_high - atr_break_margin
        _log("كسر حقيقي (CHoCH) هابط يؤكد انتهاء التراجع (بهامش ≥0.25×ATR)", f"إغلاق={last_close:.6g} مقابل قاع تعافي={recent_trough_after_high:.6g}", genuine_break)
        if not genuine_break:
            _log("❌ القرار النهائي", "التراجع لسا ما انكسر كسراً حقيقياً — ننتظر", False)
            return None

        side = "Short"
        # نفس الإصلاح بالفرع الصاعد — هامش اختراق إضافي **فوق** 0.72 (نحو القمة
        # أكثر) قبل الدخول، محاكاة اصطياد السيولة المتوقع عند هذا المستوى المعروف
        fib_072 = swing_high - 0.72 * swing_range
        liquidity_sweep_margin = swing_range * 0.04
        entry_price = fib_072 + liquidity_sweep_margin
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
    # 🔴 إصلاح جوهري: نقارن ضد السعر الحي الحقيقي (لو متوفر) بدل شمعة مؤكَّدة
    # متأخرة — فحص "هل فاتت الفرصة" يجب يعكس اللحظة الفعلية، لا الماضي القريب.
    reference_price = current_price if current_price is not None else last_close
    if side == "Long" and entry_price > reference_price * 1.0005:
        _log("❌ اتجاه منطقة الدخول غير صالح", f"منطقة 0.72 ({entry_price:.6g}) أصبحت فوق السعر الحالي ({reference_price:.6g}) — السعر لسا ما نزل لمنطقة الدخول، أو تجاوزها بسرعة — الفرصة فاتت هذي الدورة", False)
        return None
    if side == "Short" and entry_price < reference_price * 0.9995:
        _log("❌ اتجاه منطقة الدخول غير صالح", f"منطقة 0.72 ({entry_price:.6g}) أصبحت تحت السعر الحالي ({reference_price:.6g}) — السعر تجاوز منطقة الدخول بسرعة أكبر من المتوقع — الفرصة فاتت هذي الدورة", False)
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

    score_factors = [
        ("اتجاه رئيسي واضح على 15 دقيقة", True),
        ("تراجع معاكس بشكل صحيح على 5 دقائق", True),
        ("كسر حقيقي (CHoCH) يؤكد انتهاء التراجع", True),
        ("منطقة فيبوناتشي 0.72 بمسافة منطقية عن السعر", True),
        ("اتجاه منطقة الدخول صحيح (لا مطاردة)", True),
        ("CVD أو ضغط متداولين متوافق", (cvd_pct is not None and ((side == "Long" and cvd_pct > 58) or (side == "Short" and cvd_pct < 42))) or (taker_pressure is not None and ((side == "Long" and taker_pressure > 0.1) or (side == "Short" and taker_pressure < -0.1)))),
        ("🆕 تأكيد سحب سيولة حقيقي (اختراق مستوى هيكلي سابق)", liquidity_swept),
    ]
    score_breakdown, signal_score = build_score_breakdown(score_factors)

    return AnalysisResult(
        symbol=symbol, trend=main_trend, dt="", prob=probability, price=last_close,
        atr=atr(k5m, 14), side=side, entry_price=entry_price, stop_loss=stop_loss,
        take_profit=take_profit, rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
        score_breakdown=score_breakdown, signal_score=signal_score,
    )
