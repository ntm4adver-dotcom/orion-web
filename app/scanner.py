"""محرك الفحص الخلفي — يعادل OrionScanner.kt / OrionScannerService.kt الأصليين.
يعمل كـ Thread واحد داخل نفس عملية الويب، يفحص العملات دورياً، يحفظ الإشارات،
يرسل تنبيهات تيليجرام، ويتابع حالة الصفقات المفتوحة (PENDING/ACTIVE/HIT_TP/HIT_SL).
"""
import threading
import time
from typing import Optional

from . import db
from . import binance_client
from . import okx_client
from . import telegram_alert
from . import learning
from .analyzer import MarketMicrostructure, Kline, assess_coin_tradability
from .strategies import get_active_strategies, strategy_label


_INTERVAL_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _find_candle_gaps(klines: list, timeframe: str) -> list:
    """🆕 يفحص **الأرشيف الكامل** المخزَّن (مو بس حافة الجلب الجديد) عن أي فجوات
    زمنية داخلية — شموع مفقودة بمنتصف السلسلة (عطل مؤقت بالمنصة، عملة انحذفت
    وارتجعت، إلخ). المؤشرات كلها (ATR, HMA, Efficiency Ratio, سوينق ZigZag)
    تفترض ضمنياً تباعد زمني منتظم بين الشموع — فجوة داخلية غير مكتشفة ممكن تفسد
    حسابها بصمت (مثلاً: تُحسب كـ'شمعة ضخمة شاذة' وهمية). يرجع قائمة فجوات
    (كل عنصر: وقت البداية، وقت النهاية، عدد الشموع الناقصة تقريباً)."""
    interval_ms = _INTERVAL_MS.get(timeframe)
    if not interval_ms or len(klines) < 2:
        return []
    gaps = []
    for i in range(1, len(klines)):
        delta = klines[i].open_time - klines[i - 1].open_time
        if delta > interval_ms * 1.5:  # هامش بسيط فوق الفترة الطبيعية (تذبذب توقيت طفيف مقبول)
            missing = round(delta / interval_ms) - 1
            gaps.append({"from": klines[i - 1].open_time, "to": klines[i].open_time, "missing_candles": missing})
    return gaps


def _fetch_klines_cached(exchange, symbol: str, timeframe: str, target_count: int) -> list:
    """🆕 جلب ذكي تراكمي (بطلب صريح — 'عمارة طوبة فوق طوبة'): بدل إعادة جلب
    1000 شمعة من الصفر كل دورة فحص (بطيء جداً وضغط كبير على المنصة)، نبني أرشيف
    محلي دائم لكل (عملة+فريم) بشكل مستقل تماماً عن الباقي:
      - أول مرة (ما فيه أرشيف): جلب كامل بالترقيم (fetch_historical_klines).
      - كل مرة بعدها: جلب "خفيف" بس (آخر شموع قليلة عبر fetch_klines العادية
        الأسرع بكثير)، نضيف بس الجديد فعلاً (بمقارنة الوقت)، ونحدّث الشمعة
        الأخيرة لو كانت "حيّة" وقت آخر حفظ وصارت مؤكَّدة الآن — ثم نقرأ الأرشيف
        الكامل المحدَّث من قاعدة البيانات (سريع جداً، بدون انتظار شبكة)."""
    latest_cached = db.get_latest_cached_open_time(symbol, timeframe)

    if latest_cached is None:
        # أول مرة لهذي التركيبة — جلب كامل بالترقيم
        if hasattr(exchange, "fetch_historical_klines"):
            fresh = exchange.fetch_historical_klines(symbol, timeframe, target_count)
        else:
            fresh = exchange.fetch_klines(symbol, timeframe, min(target_count, 300))
        if fresh:
            db.save_candles(symbol, timeframe, [
                {"open_time": k.open_time, "open": k.open, "high": k.high, "low": k.low,
                 "close": k.close, "volume": k.volume, "close_time": k.close_time} for k in fresh
            ], keep_latest=target_count + 200)
        return fresh

    # فيه أرشيف سابق — جلب خفيف بس (آخر شموع قليلة تكفي لتغطية الفجوة منذ آخر فحص)
    light_fetch_count = 300  # هامش أمان كبير يغطي أي فجوة زمنية معقولة بين دورات الفحص
    recent = exchange.fetch_klines(symbol, timeframe, light_fetch_count)

    # 🔴 إصلاح ثغرة حقيقية (اكتُشفت بمراجعة ذاتية): لو انقطع التطبيق فترة طويلة،
    # أو فاصل الفحص كان طويلاً جداً، الجلب الخفيف (300 شمعة) ممكن ما يكفي يغطي
    # الفجوة الحقيقية بين آخر شمعة مخزَّنة والآن — فيصير "ثقب" بمنتصف الأرشيف
    # (شموع مفقودة)، يفسد حسابات القمم/القيعان والمؤشرات المبنية عليه. نتحقق الآن:
    # هل أقدم شمعة بالجلب الخفيف لسا "متصلة" بآخر شمعة مخزَّنة (فجوة معقولة)، أو
    # فيه ثقب حقيقي؟ لو فيه ثقب، نسوي تعبئة كاملة (جلب كامل بالترقيم) بدل الاكتفاء
    # بالجلب الخفيف الناقص.
    if recent:
        earliest_new = min(k.open_time for k in recent)
        interval_ms = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}.get(timeframe, 300_000)
        gap_ms = earliest_new - latest_cached
        max_acceptable_gap = interval_ms * (light_fetch_count + 5)  # هامش أمان بسيط فوق سعة الجلب الخفيف
        if gap_ms > max_acceptable_gap:
            db.add_log(f"⚠️ [{symbol}/{timeframe}] فجوة كبيرة بأرشيف الشموع (انقطاع طويل محتمل) — تعبئة كاملة تلقائية بدل الجلب الخفيف الناقص")
            if hasattr(exchange, "fetch_historical_klines"):
                recent = exchange.fetch_historical_klines(symbol, timeframe, target_count)
            # لو ما فيه دعم ترقيم بالمنصة، نكتفي بالخفيف رغم الفجوة (احتياط أخير، أفضل من الانهيار)

        db.save_candles(symbol, timeframe, [
            {"open_time": k.open_time, "open": k.open, "high": k.high, "low": k.low,
             "close": k.close, "volume": k.volume, "close_time": k.close_time} for k in recent
        ], keep_latest=target_count + 200)

    cached = db.get_cached_candles(symbol, timeframe)
    result_klines = [Kline(open_time=c["open_time"], open=c["open"], high=c["high"], low=c["low"],
                            close=c["close"], volume=c["volume"], close_time=c["close_time"]) for c in cached][-target_count:]

    # 🔴 إصلاح فجوة حقيقية (بطلب صريح: فحص شامل لتكاملية الشموع): الفحص السابق
    # كان يتحقق بس من فجوة عند **حافة** الجلب الجديد (بين آخر شمعة مخزَّنة وأول
    # شمعة جديدة) — أي ثقب **داخل** الأرشيف نفسه (منتصف السلسلة) كان يمر بدون
    # أي كشف، ويفسد بصمت حسابات المؤشرات (ATR, HMA, Efficiency Ratio, السوينق)
    # اللي تفترض تباعد زمني منتظم. نفحص الآن السلسلة **الكاملة الراجعة فعلياً**
    # للاستراتيجيات، ولو فيه فجوة حقيقية، نسوي تعبئة كاملة تلقائية (إعادة جلب
    # بالترقيم من الصفر) بدل تمرير بيانات فيها ثقب صامت.
    gaps = _find_candle_gaps(result_klines, timeframe)
    if gaps:
        total_missing = sum(g["missing_candles"] for g in gaps)
        db.add_log(f"⚠️ [{symbol}/{timeframe}] فجوة داخلية حقيقية بأرشيف الشموع ({len(gaps)} فجوة، ~{total_missing} شمعة مفقودة تقريباً) — تعبئة كاملة تلقائية")
        if hasattr(exchange, "fetch_historical_klines"):
            fresh = exchange.fetch_historical_klines(symbol, timeframe, target_count)
            if fresh:
                db.save_candles(symbol, timeframe, [
                    {"open_time": k.open_time, "open": k.open, "high": k.high, "low": k.low,
                     "close": k.close, "volume": k.volume, "close_time": k.close_time} for k in fresh
                ], keep_latest=target_count + 200)
                # نتحقق مرة ثانية بعد التعبئة — لو الفجوة حقيقية بجانب المنصة نفسها
                # (بيانات ناقصة فعلياً عندها، مو مشكلة عندنا)، نسجّلها كتحذير نهائي
                # بدل محاولة إصلاح لا نهائية
                remaining_gaps = _find_candle_gaps(fresh, timeframe)
                if remaining_gaps:
                    db.add_log(f"⚠️ [{symbol}/{timeframe}] الفجوة موجودة حتى بعد التعبئة الكاملة — على الأغلب نقص حقيقي ببيانات المنصة نفسها لهذي الفترة، مو خطأ بالأرشيف المحلي")
                return fresh
    return result_klines


def evaluate_signal_filters(settings: dict, symbol: str, strategy_key: str, result,
                             k4h, k1h, k15m, k5m, btc_trend=None, btc_klines=None,
                             market_regime_er=None, micro=None, current_live_price=None) -> tuple:
    """🆕 دالة مشتركة نقية (بدون أي استدعاءات جانبية لقاعدة البيانات) تحتوي **كل**
    منطق الفلاتر بالضبط — يستخدمها الفحص الحي (scanner.py) **والاختبار الخلفي**
    (backtest.py) بنفس الطريقة تماماً، بنفس الإعدادات الفعلية، عشان الاختبار
    الخلفي يعكس فعلياً نفس سلوك التطبيق الحي، مو أرقام ثابتة منفصلة.
    ترجع (accepted: bool, reason: str, counter_key: str). تُعدّل result بمكانها
    لو تحقق تعزيز الثقة (نفس التعديل يصير بالحي والباك تيست معاً)."""
    # 🔴 حارس تسلسل منطقي صارم (بطلب صريح، بعد اكتشاف عكس منطقي حقيقي بفيبوناتشي):
    # فحوصات هيكلية أساسية **لازم** تتحقق دائماً بغض النظر عن الاستراتيجية — تمسك
    # أي تناقض هيكلي (وقف/هدف بالجهة الغلط، عائد/مخاطرة سلبي أو صفري، اتجاه غير
    # صالح) فوراً، قبل حتى ما نصل لأي فلتر آخر. يُطبَّق على **كل** استراتيجية
    # (حالية ومستقبلية) بالحي والباك تيست معاً — طبقة حماية شاملة لا تعتمد على
    # مراجعة يدوية لكل استراتيجية لحالها.
    if result.side not in ("Long", "Short"):
        return False, f"اتجاه غير صالح: '{result.side}'", "sequence_integrity_invalid_side"
    if result.side == "Long":
        if not (result.stop_loss < result.entry_price < result.take_profit):
            return False, f"تسلسل هيكلي غلط لصفقة شراء: وقف={result.stop_loss:.6g}، دخول={result.entry_price:.6g}، هدف={result.take_profit:.6g} — يفترض وقف<دخول<هدف", "sequence_integrity_broken"
    else:
        if not (result.take_profit < result.entry_price < result.stop_loss):
            return False, f"تسلسل هيكلي غلط لصفقة بيع: هدف={result.take_profit:.6g}، دخول={result.entry_price:.6g}، وقف={result.stop_loss:.6g} — يفترض هدف<دخول<وقف", "sequence_integrity_broken"
    risk = abs(result.entry_price - result.stop_loss)
    reward = abs(result.take_profit - result.entry_price)
    if risk <= 0 or reward <= 0:
        return False, f"مخاطرة أو عائد صفري/سلبي (مخاطرة={risk:.6g}, عائد={reward:.6g})", "sequence_integrity_zero_risk_reward"

    # 🆕 فلتر عام إلزامي (زر تفعيل/إلغاء، بطلب صريح): يُطبَّق على **كل**
    # الاستراتيجيات بدون استثناء (حتى الارتدادية الثلاث المُعفاة من فلاتر
    # تانية) — بناءً على مراجعة صفقات حقيقية مغلقة أظهرت فاصل واضح 100% بين
    # الرابح والخاسر بـscalp_precision تحديداً: كل خسارة كانت بالضبط الحالة
    # اللي غاب فيها تأكيد ضغط المتداولين الفعليين، وكل ربح كان فيه هذا
    # التأكيد موجود ومتوافق مع اتجاه الصفقة.
    # ملاحظة مهمة: بالاختبار الخلفي (backtest) `micro` دايماً None لأن بيانات
    # ضغط المتداولين لحظية حية فقط، ما تُحفَظ تاريخياً — فالفلتر هنا **يتخطى
    # بدون رفض** لو micro غير متوفرة (بدل ما يرفض كل شي بالباك-تست)، ويشتغل
    # بصرامة فقط بالفحص الحي حيث البيانات دايماً متوفرة.
    if settings.get("is_taker_pressure_filter_enabled", False) and micro is not None:
        tp = micro.taker_pressure
        aligned = tp is not None and ((result.side == "Long" and tp > 0.2) or (result.side == "Short" and tp < -0.2))
        if not aligned:
            return False, f"ضغط المتداولين الفعليين غير متوفر أو غير كافٍ لدعم اتجاه الصفقة ({tp if tp is not None else 'غير متوفر'})", "taker_pressure_filter"

    # 🔴 إصلاح باق حقيقي (بطلب صريح، بعد ملاحظة daily_breakout ما جابت ولا صفقة):
    # كان الكود يعيد حساب "السعر الحالي" من k5m[-1] — لكن k5m المُمرَّرة هنا هي
    # النسخة **المؤكَّدة** (تستبعد الشمعة الحيّة قيد التكوين)، يعني السعر المحسوب
    # فعلياً متأخر بشمعة كاملة (5 دقايق) عن السعر اللحظي الحقيقي. لأغلب
    # الاستراتيجيات (دخول فوري قريب من السعر الحالي) الفرق ما يُلاحَظ غالباً،
    # لكن daily_breakout بالذات دخولها Limit عند مستوى محدَّد (إعادة اختبار) —
    # حساسة جداً لدقة السعر اللحظي، فأي تأخر بالسعر يخلي فلتر "اتجاه الدخول"
    # يرفضها بالخطأ رغم إن الدخول منطقي فعلياً. الآن نستخدم السعر اللحظي
    # الحقيقي الممرَّر من حلقة الفحص الرئيسية (current_live_price) لو متوفر،
    # بدل إعادة حسابه من بيانات متأخرة.
    current_live_price = current_live_price if current_live_price else (k5m[-1].close if k5m else None)

    if current_live_price and current_live_price > 0:
        tolerance = current_live_price * 0.0005
        if result.side == "Long" and result.entry_price > current_live_price + tolerance:
            return False, f"نقطة الدخول ({result.entry_price:.6g}) أعلى من السعر الحالي ({current_live_price:.6g}) بصفقة شراء", "entry_direction_check"
        if result.side == "Short" and result.entry_price < current_live_price - tolerance:
            return False, f"نقطة الدخول ({result.entry_price:.6g}) أقل من السعر الحالي ({current_live_price:.6g}) بصفقة بيع", "entry_direction_check"

    # 📊 إصلاح مبني على دليل تجريبي مباشر (مقارنة باك تيست خام مقابل بإعدادات
    # حقيقية): stop_hunt وliquidation_hunter أظهرتا نمط معكوس تماماً — نسبة نجاح
    # الصفقات "المرفوضة" بفلتر التوافق كانت أعلى من "المقبولة" (30.6% مقابل 15.9%
    # لصيد الاستوبات، 40% مقابل 20% لصيد التصفيات) — دليل قوي إن الفلتر يرفض
    # بالضبط أفضل صفقاتهم (طبيعة ارتدادية/زخم لحظي قد يعاكس الترند الأكبر أحياناً).
    _reversal_strategies = {"climactic_reversal", "stop_hunt", "liquidation_hunter"}
    is_reversed_signal = strategy_key.endswith("_REVERSED")
    if is_reversed_signal:
        _reversal_strategies = _reversal_strategies | {strategy_key}

    if (settings.get("is_market_regime_filter_enabled", False) and market_regime_er is not None
            and strategy_key not in _reversal_strategies):
        min_regime = settings.get("min_market_regime_er", 0.3)
        side_trend_check = "صاعد" if result.side == "Long" else "هابط"
        fighting_strong_trend = (market_regime_er >= min_regime and btc_trend and side_trend_check != btc_trend)
        choppy_market = market_regime_er < min_regime
        if fighting_strong_trend:
            return False, f"الصفقة تحارب ترند سوق عام قوي ونظيف (كفاءة {market_regime_er:.2f})", "market_regime_filter_fighting_trend"
        if choppy_market:
            return False, f"نظام السوق العام ضعيف/متذبذب (كفاءة {market_regime_er:.2f} أقل من {min_regime})", "market_regime_filter"

    if settings.get("is_efficiency_filter_enabled", True) and strategy_key not in _reversal_strategies:
        from .analyzer import efficiency_ratio
        er = efficiency_ratio(k15m, period=16)
        min_er = settings.get("min_efficiency_ratio", 0.15)
        if er < min_er:
            return False, f"العملة تتحرك بشكل عشوائي/جانبي (كفاءة اتجاهية {er:.2f} أقل من {min_er})", "efficiency_ratio_filter"

    if (settings.get("is_market_alignment_filter_enabled", True) and not symbol.startswith("BTC")
            and strategy_key not in _reversal_strategies):
        side_trend = "صاعد" if result.side == "Long" else "هابط"
        is_decoupled = False
        correlation = None
        if btc_klines:
            from .analyzer import correlation_with
            correlation = correlation_with(k4h, btc_klines, period=30)
            min_corr = settings.get("min_btc_correlation", 0.35)
            is_decoupled = abs(correlation) < min_corr

        # 🔴 إصلاح خطأ منطقي (بطلب صريح بعد مراجعة الكود): abs(correlation) صحيح
        # لتحديد *هل فيه علاقة أصلاً*، لكن الخطوة اللي بعدها كانت تقارن الاتجاه
        # مباشرة بغض النظر عن إشارة الارتباط. لو الارتباط سالب قوي (العملة تتحرك
        # عكس البيتكوين تماماً)، الاتجاه "المتوافق" المتوقع للعملة هو *عكس*
        # اتجاه البيتكوين، مو نفسه. بدون هالتصحيح، صفقة صحيحة فعلياً (متوافقة مع
        # نمط الارتباط العكسي) كانت تُرفض خطأً على إنها "تعاكس السوق العام".
        expected_trend = btc_trend
        if btc_trend and correlation is not None and correlation < 0:
            expected_trend = "هابط" if btc_trend == "صاعد" else "صاعد"

        # 🆕 لو المستخدم عطّل استثناء "فك الارتباط" (بطلب صريح: فك الارتباط أحياناً
        # مؤقت، والسوق يجبر العملة ترجع تتبع البيتكوين لاحقاً فتفشل الصفقة اللي
        # أُنشئت بناءً على اتجاه العملة وحدها) — نلغي حالة is_decoupled بالكامل،
        # وكل الصفقات تُقيَّم دايماً بالنسبة للاتجاه المتوقع من البيتكوين
        # (expected_trend، بعد تصحيح إشارة الارتباط أعلاه)، بغض النظر عن قوة
        # الارتباط اللحظية.
        if not settings.get("is_btc_decoupling_exception_enabled", True):
            is_decoupled = False

        if is_decoupled:
            from .analyzer import _get_bias as _get_coin_bias
            coin_trend = _get_coin_bias(k4h)
            if side_trend != coin_trend:
                return False, f"العملة فكّت ارتباطها بالبيتكوين، لكن الصفقة تعاكس اتجاه العملة نفسها ({coin_trend})", "market_alignment_filter_decoupled_own_trend"
        elif expected_trend and side_trend != expected_trend:
            return False, f"الصفقة تعاكس اتجاه السوق العام (البيتكوين: {btc_trend}, الاتجاه المتوقع للعملة بناءً على الارتباط: {expected_trend})", "market_alignment_filter_btc"

        if (market_regime_er is not None and market_regime_er >= 0.4 and expected_trend and side_trend == expected_trend):
            boost = min(6, round(market_regime_er * 10))
            result.prob = min(96, result.prob + boost)
            result.signal_score = min(100.0, (getattr(result, "signal_score", 100.0) or 100.0) + boost)
            result.behavior += f" 🌊 [تعزيز: توافق مع نظام سوق عام قوي ونظيف — كفاءة {market_regime_er:.2f}]"

    block_reason = learning.is_coin_blocked(result.symbol, settings)
    if block_reason:
        return False, block_reason, "coin_hard_block"

    req_prob, _ = learning.effective_threshold(result.symbol, result.side, settings, strategy_key=strategy_key)
    if result.prob < req_prob:
        return False, f"نسبة النجاح ({result.prob}%) أقل من الحد المطلوب ({req_prob}%)", "min_probability_filter"

    min_score = settings.get("min_signal_score", 0)
    signal_score = getattr(result, "signal_score", 100.0)
    if min_score and signal_score < min_score:
        return False, f"نقاط القوة ({signal_score:.1f}/100) أقل من الحد المطلوب ({min_score})", "min_signal_score_filter"

    if settings["is_volume_filter_enabled"]:
        v1h = [k.volume for k in k1h[-50:]]
        vol_avg = sum(v1h) / len(v1h) if v1h else 1.0
        vol_ratio = (v1h[-1] / vol_avg) if vol_avg > 0 else 1.0
        if vol_ratio < settings["min_volume_ratio"]:
            return False, f"معدل الحجم ({vol_ratio:.2f}x) أقل من الحد الأدنى", "volume_filter"

    # 🔴 إصلاح تعارض تحليلي (اكتُشف بمراجعة استغلال البيانات المتوفرة، مو خلل
    # برمجي): كانت هذي النافذة 20 شمعة (3.3 يوم) رغم توفر 999 شمعة فعلياً بفريم
    # 4 ساعات — استخدام ضئيل جداً (2%). رفعناها لـ80 شمعة (13.3 يوم) — توسيع
    # معتدل (مو جذري زي الاستراتيجيات الفردية) لأن VWAP/نسبة المشتريات بطبيعتها
    # مرجع "حديث نسبياً"، مو مستوى هيكلي بعيد المدى يستفيد من أشهر من البيانات.
    if settings["is_vwap_filter_enabled"] and strategy_key not in _reversal_strategies:
        last20 = k4h[-80:]
        v_sum = sum(k.volume for k in last20)
        vwap4h = ((sum(k.volume * (k.high + k.low + k.close) / 3.0 for k in last20) / v_sum)
                  if v_sum > 0 else last20[-1].close)
        last_price = k5m[-1].close
        if result.side == "Long" and last_price <= vwap4h:
            return False, "السعر تحت خط VWAP", "vwap_filter"
        if result.side == "Short" and last_price >= vwap4h:
            return False, "السعر فوق خط VWAP", "vwap_filter"

    if settings["is_4h_buyers_filter_enabled"] and strategy_key not in _reversal_strategies:
        last20 = k4h[-80:]
        green = sum(k.volume for k in last20 if k.close > k.open)
        red = sum(k.volume for k in last20 if k.close < k.open)
        total = green + red
        buy_pct = int(green / total * 100) if total > 0 else 50
        if result.side == "Long" and buy_pct < settings["min_4h_buyers_percentage"]:
            return False, f"نسبة المشتريات ({buy_pct}%) غير كافية", "4h_buyers_filter"
        if result.side == "Short" and (100 - buy_pct) < settings["min_4h_buyers_percentage"]:
            return False, "نسبة المبيعات غير كافية", "4h_buyers_filter"

    # 🔴 إصلاح جذري + نقل معماري (بطلب صريح، بعد تشخيص عميق لنمط خسائر حقيقي،
    # وبعد اكتشاف إضافي إن هذا الفحص كان بمكان يستدعيه الفحص الحي بس، مو
    # الباك-تست): كان الكود يمحي هدف كل استراتيجية المحسوب من هيكل سوق حقيقي
    # (فيبوناتشي، VAH/VAL، Measured Move...) ويستبدله برقم رياضي أعمى
    # (مخاطرة×3) بدون أي علاقة بأقرب مقاومة/دعم فعلي. الحل: حد أدنى كفلتر
    # رفض، مو استبدال قسري — لو هدف الاستراتيجية الحقيقي يحقق الحد الأدنى،
    # نُبقيه كما هو. لو أقل، نرفض الصفقة كليّاً بدل تمديد الهدف تعسفياً.
    # موجود هنا الآن (evaluate_signal_filters) بدل _process_signal عشان
    # الفحص الحي والباك-تست يطبّقان نفس المنطق بالضبط، بدون أي اختلاف سلوك.
    if settings.get("is_fixed_rr_enabled", False):
        min_rr = float(settings.get("fixed_rr_value", 3.0))
        if min_rr > 0:
            risk_distance = abs(result.entry_price - result.stop_loss)
            reward_distance = abs(result.take_profit - result.entry_price)
            if risk_distance > 0:
                actual_rr = reward_distance / risk_distance
                if actual_rr < min_rr:
                    return False, f"عائد/مخاطرة الهدف الحقيقي (هيكل السوق) = 1:{actual_rr:.2f}، أقل من الحد الأدنى المطلوب 1:{min_rr:.1f} — رفض بدل تمديد الهدف تعسفياً", "min_structural_rr_filter"
                result.rr = round(actual_rr, 2)
                result.behavior = f"🎯 [هدف واقعي من هيكل السوق، عائد/مخاطرة محقَّق ≥ 1:{min_rr:.1f}] " + result.behavior

    return True, "", ""


class ScannerState:
    def __init__(self):
        self.is_scanning_active = False
        self.is_currently_working = False
        self.last_scan_time: Optional[int] = None
        self.countdown_seconds = 0
        self._thread: Optional[threading.Thread] = None
        self._price_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._trigger_immediate = threading.Event()
        self._notified_transitions = set()

    def start(self):
        if self._thread and self._thread.is_alive():
            db.add_log("عملية الفحص المجدولة تعمل بالفعل.")
            return
        self._stop_flag.clear()
        self.is_scanning_active = True
        db.add_log("تم بدء تشغيل محرك أوريون الذكي للفحص التلقائي...")
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not (self._price_thread and self._price_thread.is_alive()):
            self._price_thread = threading.Thread(target=self._price_update_loop, daemon=True)
            self._price_thread.start()

    def stop(self):
        self.is_scanning_active = False
        self._stop_flag.set()
        db.add_log("تم إيقاف الفحص التلقائي.")

    def trigger_immediate_scan(self):
        self._trigger_immediate.set()

    def _price_update_loop(self):
        """خيط مستقل خفيف يحدّث السعر اللحظي لكل الصفقات المفتوحة كل 5 ثوانٍ،
        بدون انتظار اكتمال دورة الفحص الكاملة (اللي قد تاخذ وقت أطول لكل العملات)."""
        while not self._stop_flag.is_set():
            try:
                settings = db.get_settings()
                self._update_signal_prices(settings)
            except Exception:
                pass
            for _ in range(10):  # 5 ثوانٍ مقسّمة لفحص متكرر لعلم الإيقاف
                if self._stop_flag.is_set():
                    return
                time.sleep(0.5)

    def _seconds_until_next_boundary(self, interval_seconds: int) -> float:
        """يحسب الثواني المتبقية لأقرب حد زمني متزامن مع الساعة الحقيقية (UTC) —
        بالضبط زي إغلاق شموع الشارتات. لو الفاصل 60 ثانية (دقيقة)، يرجع الوقت لحد
        ثانية 00 من أقرب دقيقة جاية. لو 900 ثانية (ربع ساعة)، يرجع الوقت لحد
        00/15/30/45 دقيقة بالضبط. epoch الأساسي (time.time()) نفسه متزامن أصلاً مع
        حدود الدقيقة/الساعة بتوقيت UTC، فالباقي (modulo) يعطي التزامن الصحيح مباشرة."""
        now = time.time()
        remainder = now % interval_seconds
        wait_time = interval_seconds - remainder
        return wait_time if wait_time > 0.5 else wait_time + interval_seconds

    def _run_loop(self):
        while not self._stop_flag.is_set():
            try:
                settings = db.get_settings()
                if not settings["is_auto_scanning"]:
                    db.add_log("البوت في وضع الخمول - الفحص غير نشط.")
                    self._wait(5)
                    continue

                self.is_currently_working = True
                self._run_scan_cycle(settings)
                self.last_scan_time = int(time.time() * 1000)
            except Exception as e:
                db.add_log(f"⚠️ خطأ في دورة الفحص: {e}")
            finally:
                self.is_currently_working = False

            interval = max(settings.get("scan_interval_seconds", 300), 5)
            wait_seconds = self._seconds_until_next_boundary(interval)
            self._wait(wait_seconds)

    def _wait(self, seconds):
        seconds = max(1, int(seconds) + 1)  # نقرّب لأعلى لضمان تجاوز الحد الزمني المستهدف فعلياً
        self.countdown_seconds = seconds
        for _ in range(seconds * 2):
            if self._stop_flag.is_set() or self._trigger_immediate.is_set():
                self._trigger_immediate.clear()
                self.countdown_seconds = 0
                return
            time.sleep(0.5)
            self.countdown_seconds = max(0, self.countdown_seconds - 0.5)
        self.countdown_seconds = 0

    def _resolve_symbols(self, settings: dict):
        if settings["is_single_coin_mode_enabled"]:
            raw = [s.strip().upper() for s in settings["single_coin_symbol"].split(",") if s.strip()]
            symbols = [s if s.endswith(("USDT", "BUSD")) else f"{s}USDT" for s in raw]
            db.add_log(f"🎯 [مراقبة مخصصة] جاري مراقبة وتحليل عملات: {', '.join(symbols)}")
            return symbols
        limit = settings.get("symbols_limit", 10)
        client = okx_client if settings["exchange"] == "okx" else binance_client
        exchange_name = "OKX" if settings["exchange"] == "okx" else "Binance"
        mode = settings.get("symbol_selection_mode", "top_volume")
        mode_labels = {
            "top_volume": "الأعلى سيولة وحجم تداول",
            "big_movers": "الأكبر تحركاً سعرياً (Big Movers)",
            "high_funding": "الأعلى تطرفاً بمعدل التمويل (High Funding)",
            "oi_spike": "الأكبر قفزة بالفائدة المفتوحة (OI Spike)",
        }
        db.add_log(f"جاري استعلام {exchange_name} عن أزواج العملات — المعيار: {mode_labels.get(mode, mode)}...")
        symbols = client.fetch_screened_symbols(mode, limit)
        fallback_reason = getattr(client, "last_error", {}).get("_top_symbols")
        if fallback_reason:
            db.add_log(f"⚠️ تعذر جلب قائمة العملات الحقيقية من {exchange_name}، تم استخدام قائمة احتياطية مؤقتة — السبب: {fallback_reason}")
        db.add_log(f"✅ تم العثور على {len(symbols)} زوج: {', '.join(symbols)}")
        return symbols

    def _run_scan_cycle(self, settings: dict):
        symbols = self._resolve_symbols(settings)
        exchange = okx_client if settings["exchange"] == "okx" else binance_client
        db.add_log(f"[{time.strftime('%H:%M:%S')}] بدء فحص حزمة الأزواج الذكية المكتشفة...")
        incomplete_data_notes = []  # نجمّع كل نقص بيانات بالدورة، ونرسل تنبيه تيليجرام واحد بالنهاية بدل إغراق المستخدم برسائل

        # تحقق من الحظر المؤقت مرة واحدة بداية الدورة بدل ما نكرر نفس الخطأ لكل عملة
        if hasattr(exchange, "get_ban_status"):
            ban_msg = exchange.get_ban_status()
            if ban_msg:
                db.add_log(f"⏸️ تم إيقاف هذه الدورة مؤقتاً — {ban_msg}")
                if settings.get("is_telegram_enabled"):
                    telegram_alert.send_text_alert(
                        settings["telegram_token"], settings["telegram_chat_ids"],
                        f"⏸️ *تنبيه توقف الفحص*\nتم إيقاف دورة الفحص كاملة بسبب حظر مؤقت:\n{ban_msg}",
                    )
                return

        # جلب اتجاه السوق العام (البيتكوين كمؤشر مرجعي) مرة وحدة بداية الدورة — يُستخدم
        # كفلتر توافق إلزامي لكل صفقة بكل استراتيجية: لا تُقبل صفقة تعاكس اتجاه السوق
        # العام، لأن أغلب العملات البديلة مرتبطة بحركة البيتكوين بقوة، وصفقة تعاكسه
        # معرّضة لانعكاس مفاجئ بغض النظر عن قوة إعداد العملة نفسها محلياً.
        btc_trend = None
        btc_klines = None
        try:
            btc_klines = exchange.fetch_klines("BTCUSDT", "4h", 60)
            if btc_klines and len(btc_klines) >= 20:
                from .analyzer import _get_bias as _get_market_bias
                btc_trend = _get_market_bias(btc_klines)

                # 📊 تحسين مبني على بيانات فعلية: يوم هبوط حقيقي بالسوق (23-24 يوليو
                # 2026، البيتكوين -1.17% وكل العملات الكبرى حمراء) كشف إن اتجاه EMA
                # على فريم 4 ساعات بطيء نسبياً ويتأخر بالتقاط انعكاسات حقيقية سريعة.
                # نضيف تأكيد زخم أسرع: صافي تغيّر البيتكوين آخر ~8 ساعات (فريم ساعة) —
                # لو عارض بوضوح اتجاه الـEMA البطيء، نثق بالزخم الأحدث بدلاً منه.
                btc_1h = exchange.fetch_klines("BTCUSDT", "1h", 10)
                if btc_1h and len(btc_1h) >= 8:
                    recent_change_pct = (btc_1h[-1].close - btc_1h[-8].close) / btc_1h[-8].close * 100
                    if recent_change_pct < -1.2 and btc_trend == "صاعد":
                        btc_trend = "هابط"
                        db.add_log(f"⚠️ تصحيح اتجاه البيتكوين: EMA البطيء يقول صاعد، لكن آخر 8 ساعات فعلياً {recent_change_pct:.2f}% — نعتمد الزخم الأحدث (هابط)")
                    elif recent_change_pct > 1.2 and btc_trend == "هابط":
                        btc_trend = "صاعد"
                        db.add_log(f"⚠️ تصحيح اتجاه البيتكوين: EMA البطيء يقول هابط، لكن آخر 8 ساعات فعلياً +{recent_change_pct:.2f}% — نعتمد الزخم الأحدث (صاعد)")

                db.add_log(f"📊 اتجاه السوق العام (البيتكوين، 4 ساعات): {btc_trend}")
        except Exception:
            btc_trend = None
            btc_klines = None

        # 🌊 مقياس "قوة نظام السوق" (Market Regime Strength) — بطلب صريح، مبني على
        # اكتشاف حقيقي: فترة سوق بترند هابط واضح وقوي (27 يوليو، 20:00-22:00) أعطت
        # 80% نجاح لكل الصفقات المتوافقة معه (4 من 4 Short نجحت، الوحيدة المعاكسة
        # خسرت) — بينما فترات السوق المتذبذب أعطت نتائج ضعيفة جداً. نقيس "نظافة"
        # الترند العام بنفس مفهوم الكفاءة الاتجاهية (Efficiency Ratio)، لكن على
        # البيتكوين نفسه بدل عملة فردية — ونزيد الثقة بأي صفقة تتوافق مع ترند قوي.
        market_regime_er = None
        try:
            if btc_klines and len(btc_klines) >= 21:
                from .analyzer import efficiency_ratio
                market_regime_er = efficiency_ratio(btc_klines, period=20)
                regime_label = "قوي ونظيف 💪" if market_regime_er >= 0.4 else ("متوسط" if market_regime_er >= 0.2 else "متذبذب/عشوائي ⚠️")
                db.add_log(f"🌊 قوة نظام السوق العام: {market_regime_er:.2f} ({regime_label})")
        except Exception:
            market_regime_er = None

        for idx, symbol in enumerate(symbols):
            if self._stop_flag.is_set():
                break
            if idx > 0:
                time.sleep(1.2)  # تأخير أكبر بين كل عملة وأخرى لتجنب تقييد معدل الطلبات من المنصة
            try:
                db.add_log(f"جاري سحب بيانات الشموع لزوج {symbol} (نطاق موسّع 1000+ شمعة لكل فريم)...")
                # 🔴 إصلاح جذري وشامل (بطلب صريح): "اجلب 1000+ شمعة لكل فريم، وقت
                # التحليل مو مهم، الأهم دقة وصحة التحليل". الحد الأقصى للطلب الواحد
                # بـ/api/v5/market/candles محدود (~300 شمعة)، فلا نقدر نجيب 1000+
                # بطلب وحد — نستخدم الآن fetch_historical_klines (نفس دالة الترقيم
                # المبنية أصلاً لنظام الاختبار الخلفي، تتجاوز الحد عبر عدة طلبات
                # متتالية) بدل fetch_klines المحدودة، لكل فريم بالفحص الحي أيضاً.
                def _fetch(interval: str, count: int, fallback_count: int):
                    return _fetch_klines_cached(exchange, symbol, interval, count)

                k4h = _fetch("4h", 1000, 100)      # 1000 شمعة 4 ساعات ≈ 5.5 شهر
                time.sleep(0.15)
                k1h = _fetch("1h", 1000, 170)      # 1000 شمعة ساعة ≈ 6 أسابيع
                time.sleep(0.15)
                k15m = _fetch("15m", 1000, 130)    # 1000 شمعة 15 دقيقة ≈ 10.4 يوم
                time.sleep(0.15)
                k5m = _fetch("5m", 1000, 220)      # 1000 شمعة 5 دقايق ≈ 3.5 يوم
                time.sleep(0.15)
                k_daily = _fetch("1d", 500, 100)   # 500 شمعة يومية ≈ 1.4 سنة (يومي لا يحتاج 1000، أطول أفق زمني أصلاً)

                if len(k5m) < 30 or len(k1h) < 60:
                    reason = getattr(exchange, "last_error", {}).get(symbol) if hasattr(exchange, "last_error") else None
                    if reason:
                        db.add_log(f"▫️ {symbol}: بيانات غير كافية للتحليل — السبب: {reason}")
                    else:
                        db.add_log(f"▫️ {symbol}: بيانات غير كافية للتحليل.")
                    incomplete_data_notes.append(f"{symbol}: نقص بالشموع (4س={len(k4h)}, 1س={len(k1h)}, 15د={len(k15m)}, 5د={len(k5m)}, يومي={len(k_daily)})" + (f" — {reason}" if reason else ""))
                    # إذا صرنا محظورين أثناء الفحص، نوقف بقية الدورة فوراً بدل تكرار المحاولة على كل عملة
                    if hasattr(exchange, "get_ban_status") and exchange.get_ban_status():
                        db.add_log(f"⏸️ تم إيقاف بقية الدورة — {exchange.get_ban_status()}")
                        return
                    continue

                micro = MarketMicrostructure(
                    oi_change_pct=exchange.fetch_open_interest_change_pct(symbol),
                    funding_rate=exchange.fetch_funding_rate(symbol),
                    ob_imbalance=exchange.fetch_order_book_imbalance(symbol),
                    taker_pressure=exchange.fetch_taker_pressure(symbol) if hasattr(exchange, "fetch_taker_pressure") else None,
                    long_short_ratio=exchange.fetch_long_short_ratio(symbol) if hasattr(exchange, "fetch_long_short_ratio") else None,
                    cvd_pct=exchange.get_cvd_24h_pct(symbol) if hasattr(exchange, "get_cvd_24h_pct") else None,
                    large_order_pressure=exchange.fetch_large_order_pressure(symbol) if hasattr(exchange, "fetch_large_order_pressure") else None,
                )

                # ضغط المتداولين (Taker Pressure) صار شرط إلزامي بالانفجار السعري — لو غاب،
                # كل الاستراتيجيات المبنية عليه بترفض تلقائياً، فنسجّله كنقص بيانات حرج
                if micro.taker_pressure is None:
                    incomplete_data_notes.append(f"{symbol}: بيانات ضغط المتداولين الفعليين (Taker Pressure) غير متوفرة — سيتم رفض كل صفقات الانفجار السعري لهذي العملة بهذي الدورة")

                def _fmt(v, suffix=""):
                    return f"{v:.3f}{suffix}" if v is not None else "غير متوفر"

                db.add_log(
                    f"📥 [{symbol}] تم سحب: 4س={len(k4h)} | 1س={len(k1h)} | 15د={len(k15m)} | "
                    f"5د={len(k5m)} | يومي={len(k_daily)} شمعة | OI={_fmt(micro.oi_change_pct, '%')} | "
                    f"تمويل={_fmt(micro.funding_rate)} | عمق السوق={_fmt(micro.ob_imbalance)} | "
                    f"ضغط متداولين={_fmt(micro.taker_pressure)} | CVD={_fmt(micro.cvd_pct, '%')}"
                )

                # 🔴 إصلاح معماري جوهري (بطلب صريح، ينطبق على كل الاستراتيجيات
                # دفعة وحدة): آخر شمعة بأي فريم مجلوب من المنصة هي الشمعة **الحيّة
                # قيد التكوين فعلياً** (لم تُغلق بعد وقت الفحص) — سلوك طبيعي لأي API
                # منصة تداول. لو مررناها للاستراتيجيات كأنها "شمعة مغلقة"، فكل حساب
                # نمط/دخول/تأكيد يعتمد عليها فعلياً يستخدم بيانات لحظية "حيّة" مموّهة
                # كشمعة كاملة — بالضبط سبب ملاحظة "يحلل على اللحظي مو فريم حقيقي".
                # الآن ننشئ نسخ **مؤكَّدة** (تستبعد آخر شمعة) بمكان مركزي واحد، تُمرَّر
                # لكل الاستراتيجيات — بدل ترقيع كل استراتيجية لحالها (معرّض للنسيان،
                # زي ما حصل فعلاً بأكثر من مكان قبل). السعر اللحظي الحقيقي (للتحقق
                # من اتجاه الدخول ومقارنته لاحقاً) يبقى منفصلاً من النسخة الخام غير
                # المقصوصة، عشان يعكس اللحظة الفعلية بدقة، لا شمعة سابقة مؤكَّدة.
                current_live_price = k5m[-1].close if k5m else None
                k4h_confirmed = k4h[:-1] if len(k4h) > 1 else k4h
                k1h_confirmed = k1h[:-1] if len(k1h) > 1 else k1h
                k15m_confirmed = k15m[:-1] if len(k15m) > 1 else k15m
                k5m_confirmed = k5m[:-1] if len(k5m) > 1 else k5m
                k_daily_confirmed = k_daily[:-1] if len(k_daily) > 1 else k_daily

                # 🆕 فلتر جودة استباقي على مستوى العملة نفسها (بطلب صريح: حل جذري
                # يفلتر العملات "السيئة السلوك" — مو نظام يتعلم من صفقاتنا الفاشلة
                # لاحقاً). يفحص سلوك السعر الفعلي للعملة قبل أي استراتيجية، ويرفضها
                # كاملة هذي الدورة (كل الاستراتيجيات، بدون استثناء) لو أظهرت تذبذب
                # عشوائي، شموع شاذة متطرفة، أو تقلب مفرط — بغض النظر عن أي اتجاه
                # أو استراتيجية مُجرَّبة عليها.
                is_tradable, block_reason, quality_metrics = assess_coin_tradability(k1h_confirmed, k15m_confirmed, settings)
                if not is_tradable:
                    db.add_log(f"🚫 [{symbol}] عملة مرفوضة كاملة هذي الدورة (فلتر جودة العملة): {block_reason}")
                    db.increment_rejection_counter("coin_quality_filter")
                    continue

                matched_any = False
                for strategy_key, strategy_fn in get_active_strategies(
                        settings.get("active_strategy", "explosive_breakout"),
                        settings.get("combined_enabled_strategies", "")):
                    result = strategy_fn(symbol, k4h_confirmed, k1h_confirmed, k15m_confirmed, k5m_confirmed, k_daily_confirmed, micro=micro, current_price=current_live_price, settings=settings)
                    if result is None:
                        continue
                    matched_any = True
                    self._process_signal(settings, symbol, strategy_key, result, k4h_confirmed, k1h_confirmed, k15m_confirmed, k5m_confirmed,
                                          btc_trend, btc_klines, market_regime_er, current_live_price, micro)

                if not matched_any:
                    db.add_log(f"▫️ {symbol}: ليس له اتجاه كافٍ حالياً.")

            except Exception as e:
                db.add_log(f"❌ [{symbol}] خطأ أثناء التحليل: {e}")
                incomplete_data_notes.append(f"{symbol}: خطأ استثنائي أثناء سحب/تحليل البيانات — {e}")
            time.sleep(0.2)

        if incomplete_data_notes and settings.get("is_telegram_enabled"):
            preview = incomplete_data_notes[:15]
            extra = len(incomplete_data_notes) - len(preview)
            body = "\n".join(f"• {note}" for note in preview)
            if extra > 0:
                body += f"\n… و {extra} حالة إضافية أخرى"
            telegram_alert.send_text_alert(
                settings["telegram_token"], settings["telegram_chat_ids"],
                f"⚠️ *تنبيه اكتمال البيانات*\nبهذي الدورة، {len(incomplete_data_notes)} عملة لم تُجلب لها البيانات كاملة "
                f"أو حصل خطأ أثناء التحليل:\n\n{body}",
            )

    def _process_signal(self, settings: dict, symbol: str, strategy_key: str, result, k4h, k1h, k15m, k5m, btc_trend=None, btc_klines=None, market_regime_er=None, current_live_price=None, micro=None):
        # 🔴 إصلاح موقع معماري (اكتُشف بمراجعة شاملة): فلتر الحد الأدنى لعائد/مخاطرة
        # الهدف الحقيقي (min_structural_rr_filter) كان هنا بـ_process_signal —
        # دالة خاصة بالفحص الحي بس، ما يستدعيها الباك-تست إطلاقاً. يعني نتائج
        # الباك-تست كانت تختبر صفقات كان الفحص الحي يرفضها فعلياً (عائد/مخاطرة
        # أقل من الحد الأدنى)، فتختلف نتائج الباك-تست جوهرياً عن السلوك الحي.
        # نُقل الآن لـevaluate_signal_filters (الدالة المشتركة اللي كلا المسارين
        # يستدعيانها بالضبط) — تناسق كامل بين الفحص الحي والباك-تست من الآن.

        # 🔴 current_live_price الآن يُمرَّر صراحة من المستدعي (يعكس السعر اللحظي
        # الحقيقي وقت الفحص) — k4h/k1h/k15m/k5m هنا أصبحت نسخ "مؤكَّدة" (بدون آخر
        # شمعة حيّة قيد التكوين)، فما نقدر نشتق السعر اللحظي الحقيقي منها بعد الآن.
        # نحتفظ بحساب احتياطي (fallback) بس لو استُدعيت الدالة من مكان قديم بدون
        # تمرير القيمة صراحة (حماية توافقية، حالة نادرة).
        if current_live_price is None:
            current_live_price = k5m[-1].close if k5m else None

        # 🔄🛡️ وضع الهيدج التجريبي (بطلب صريح): بدل استبدال الإشارة الأصلية
        # بالمعكوسة، نولّد **الاثنين معاً بنفس اللحظة** — زوج هيدج حقيقي (Long
        # وShort على نفس الفرصة بنفس الوقت)، يطلعون سوا بنفس التصدير للمقارنة
        # المباشرة بدون الحاجة لتشغيل نسختين منفصلتين من التطبيق. **لا نستخدم نفس
        # سعر الدخول الأصلي للنسخة المعكوسة** — لأنه محسوب بالنسبة لاتجاه مختلف
        # تماماً، فلو استخدمناه كما هو للاتجاه المعاكس، يصير بالجهة الغلط ويخالف
        # قاعدة "انتظار السعر" (يدخل فوراً أو يُرفض). بدلاً من هذا: **ننعكس حول
        # السعر الحالي نفسه** (2×السعر_الحالي - الدخول_الأصلي) فيصير الدخول
        # تلقائياً بالجهة الصحيحة، مع الحفاظ على **نفس مسافات الوقف والهدف بالضبط**.
        if (settings.get("is_reverse_mode_enabled", False) and current_live_price and current_live_price > 0
                and not strategy_key.endswith("_REVERSED")):
            import copy
            reversed_result = copy.copy(result)
            original_entry = result.entry_price
            risk_distance = abs(original_entry - result.stop_loss)
            reward_distance = abs(result.take_profit - original_entry)
            new_entry = 2 * current_live_price - original_entry
            new_side = "Short" if result.side == "Long" else "Long"
            if new_side == "Short":
                new_stop = new_entry + risk_distance
                new_target = new_entry - reward_distance
            else:
                new_stop = new_entry - risk_distance
                new_target = new_entry + reward_distance
            reversed_result.side = new_side
            reversed_result.entry_price = new_entry
            reversed_result.stop_loss = new_stop
            reversed_result.take_profit = new_target
            reversed_result.behavior = (
                f"🔄🛡️ [زوج هيدج — الجهة المعاكسة] نفس فرصة {strategy_key} بنفس اللحظة، "
                f"لكن بالاتجاه المعاكس ({new_side}) وبنفس مسافات الوقف/الهدف بالضبط. " + result.behavior
            )
            # نعالج النسخة المعكوسة بشكل مستقل تماماً (استدعاء منفصل، نفس كل الفلاتر)،
            # واللاحقة _REVERSED تمنع أي عودية لانهائية (ما يدخل هذا الشرط مرة ثانية)
            self._process_signal(settings, symbol, f"{strategy_key}_REVERSED", reversed_result,
                                  k4h, k1h, k15m, k5m, btc_trend, btc_klines, market_regime_er, current_live_price, micro)
            # نكمل الآن معالجة الصفقة **الأصلية** بشكل طبيعي تماماً (بدون أي تعديل عليها)


        # 🔴 كل منطق الفلاتر الآن بدالة مشتركة واحدة (evaluate_signal_filters أعلى
        # الملف) — يستخدمها الفحص الحي والاختبار الخلفي بنفس الطريقة بالضبط، بنفس
        # الإعدادات الفعلية، عشان الاختبار الخلفي يعكس فعلياً سلوك التطبيق الحي.
        accepted, reason, counter_key = evaluate_signal_filters(
            settings, symbol, strategy_key, result, k4h, k1h, k15m, k5m,
            btc_trend, btc_klines, market_regime_er, micro, current_live_price,
        )
        if not accepted:
            db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي الإشارة: {reason}.")
            db.increment_rejection_counter(counter_key)
            return


        # منع التكرار: تجاهل الإشارة الجديدة إذا فيه صفقة (معلقة أو نشطة) بالفعل لنفس
        # العملة ونفس الاتجاه **ونفس الاستراتيجية** — استراتيجيات مختلفة تقدر تفتح
        # صفقات مستقلة على نفس العملة بنفس الوقت (مفيد لمقارنة أدائها الحقيقي ببعض)
        existing = db.get_active_or_pending_signal(result.symbol, result.side, strategy_key)
        if existing:
            status_ar = "نشطة" if existing["status"] == "ACTIVE" else "معلقة"
            db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تجاهل الإشارة الجديدة ({result.side}) لوجود صفقة {status_ar} بنفس الاستراتيجية بالفعل من نفس الاتجاه (بروبابيليتي {existing['probability']}%).")
            db.increment_rejection_counter("duplicate_active_signal")
            return

        # منع إعادة اكتشاف نفس النمط اللي أُغلق (رابحاً أو خاسراً) خلال آخر ساعات قليلة —
        # يحل مشكلة إعادة التقاط نفس شمعة الفريم الأعلى كإشارة "جديدة" فوراً بعد إغلاقها
        recent_dup = db.get_recent_similar_signal(result.symbol, result.side, strategy_key, result.entry_price)
        if recent_dup:
            db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تجاهل إشارة مكررة — نفس النمط تقريباً ظهر بآخر ساعات (سعر دخول قريب من صفقة سابقة برقم #{recent_dup['id']}).")
            db.increment_rejection_counter("recent_duplicate_pattern")
            return

        strategy_display = strategy_label(strategy_key)
        db.add_log(f"🎯 [{symbol}] ({strategy_display}) تم رصد فرصة {result.side}! الاحتمالية: {result.prob}% | الجودة: {result.quality}")
        signal_id = db.add_signal({
            "symbol": result.symbol, "side": result.side, "entry_price": result.entry_price,
            "stop_loss": result.stop_loss, "take_profit": result.take_profit, "rr": result.rr,
            "probability": result.prob, "quality": result.quality, "behavior": result.behavior,
            "volume_analysis": result.volume_analysis, "strategy": strategy_key,
            "signal_score": getattr(result, "signal_score", 100.0),
            "score_breakdown": getattr(result, "score_breakdown", None),
            "split_targets_used": settings.get("is_split_targets_enabled", False),
        })

        if settings["is_telegram_enabled"]:
            telegram_alert.send_signal_alert(
                settings["telegram_token"], settings["telegram_chat_ids"], result.symbol,
                result.side, result.entry_price, result.take_profit, result.stop_loss,
                result.prob, result.quality, result.behavior,
                exchange_name=settings.get("exchange", ""),
            )

        if settings["okx_is_auto_trading_enabled"]:
            self._execute_auto_trade(settings, result, signal_id, current_live_price)

    def _execute_auto_trade(self, settings: dict, result, signal_id: int, current_live_price=None):
        side_text = "buy" if result.side == "Long" else "sell"
        db.add_log(f"🤖 [التداول الآلي] جاري إرسال أمر إلى OKX ({result.symbol} | {side_text})...")
        try:
            # 🔴 إصلاح جذري (بطلب صريح، بعد تفعيل التداول الآلي الحقيقي): كان
            # الكود يعتمد على إعداد عام واحد (is_instant_entry_enabled) لتحديد
            # نوع الأمر (سوق/محدد) بغض النظر عن نقطة الدخول اللي حسبتها
            # الاستراتيجية فعلياً. بعض الاستراتيجيات (زي مصيدة الحشد، اختراق
            # اليوم السابق) تحسب نقطة دخول محدَّدة (إعادة اختبار مستوى) تختلف
            # عن السعر اللحظي عمداً — تنفيذها كأمر سوق فوري يدخل الصفقة بسعر
            # غلط تماماً (السعر الحالي، مو نقطة الدخول المحسوبة)، ويفتح مركز
            # حقيقي فوراً حتى لو السعر لسا ما وصل لنقطة الدخول المقصودة أصلاً.
            # الحل: لو نقطة الدخول تختلف فعلياً عن السعر اللحظي (فرق >0.15%)،
            # **نلزم استخدام أمر Limit عند نقطة الدخول بالضبط** — بغض النظر عن
            # إعداد "الدخول الفوري"، لأن هذي حالة "انتظار إعادة اختبار" مقصودة
            # من الاستراتيجية نفسها، مو مجرد تفضيل عام.
            price_gap_pct = 0.0
            if current_live_price and current_live_price > 0:
                price_gap_pct = abs(result.entry_price - current_live_price) / current_live_price
            is_retest_entry = price_gap_pct > 0.0015
            use_market_order = settings.get("is_instant_entry_enabled", True) and not is_retest_entry
            if is_retest_entry:
                db.add_log(f"⏳ [{result.symbol}] نقطة الدخول ({result.entry_price:.6g}) تختلف عن السعر الحالي ({current_live_price:.6g}) — سيُرسَل أمر Limit معلّق بانتظار إعادة الاختبار، مو أمر سوق فوري.")

            available_balance = None
            if settings.get("okx_volume_type") == "PERCENTAGE":
                info = okx_client.fetch_account_info(
                    settings["okx_api_key"], settings["okx_api_secret"],
                    settings["okx_passphrase"], settings["okx_is_testnet"],
                )
                available_balance = info.get("available_balance")

            quantity_usdt = okx_client.calculate_order_quantity_usdt(
                settings, result.entry_price, result.stop_loss, available_balance,
            )

            # 🎯 تقسيم الأهداف (بطلب صريح) — لو مفعّل، ننفّذ أمرين منفصلين بنصف
            # الكمية لكل وحدة (نفس منطق التنفيذ اليدوي بالضبط)، بدل أمر واحد بالهدف
            # الكامل — يطابق التقسيم الداخلي المُتتبَّع بقاعدة البيانات تماماً.
            if settings.get("is_split_targets_enabled", False):
                tp1_price = result.entry_price + (result.take_profit - result.entry_price) * 0.5
                success, message, order_ids = okx_client.place_split_orders(
                    symbol=result.symbol, side=side_text, quantity_usdt=quantity_usdt,
                    leverage=settings["okx_leverage"], margin_mode=settings["okx_margin_mode"],
                    stop_loss=result.stop_loss, tp1=tp1_price, tp2=result.take_profit,
                    api_key=settings["okx_api_key"], api_secret=settings["okx_api_secret"],
                    passphrase=settings["okx_passphrase"], is_testnet=settings["okx_is_testnet"],
                    is_market_order=use_market_order,
                    is_max_leverage_enabled=settings.get("okx_is_max_leverage_enabled", False),
                    entry_price=result.entry_price,
                )
            else:
                success, message, ord_id = okx_client.place_order(
                    symbol=result.symbol, side=side_text, quantity_usdt=quantity_usdt,
                    leverage=settings["okx_leverage"], margin_mode=settings["okx_margin_mode"],
                    stop_loss=result.stop_loss, take_profit=result.take_profit,
                    api_key=settings["okx_api_key"], api_secret=settings["okx_api_secret"],
                    passphrase=settings["okx_passphrase"], is_testnet=settings["okx_is_testnet"],
                    is_market_order=use_market_order,
                    is_max_leverage_enabled=settings.get("okx_is_max_leverage_enabled", False),
                    entry_price=result.entry_price,
                )
                order_ids = [ord_id] if ord_id else []
            if success:
                db.add_log(f"✅ [التداول الآلي] تم تنفيذ الصفقة بنجاح: {message}")
                if order_ids:
                    # 🆕 نخزّن رقم الأمر الحقيقي — ضروري عشان نقدر نلغيه فعلياً
                    # على OKX لاحقاً لو الإشارة اتلغت داخلياً قبل الامتلاء
                    # (خصوصاً بحالة أمر Limit معلّق ينتظر إعادة الاختبار).
                    inst_id = okx_client._to_inst_id(result.symbol)
                    db.save_okx_order_ref(signal_id, inst_id, order_ids)
            else:
                db.add_log(f"❌ [التداول الآلي] فشل تنفيذ الصفقة: {message}")
        except Exception as e:
            db.add_log(f"❌ [التداول الآلي] خطأ استثنائي: {e}")

    def _update_signal_prices(self, settings: dict):
        open_signals = db.get_open_signals()
        if not open_signals:
            return
        exchange = okx_client if settings["exchange"] == "okx" else binance_client
        prices = exchange.fetch_all_prices()
        if not prices:
            return

        for signal in open_signals:
            live_price = prices.get(signal["symbol"])
            if not live_price or live_price <= 0:
                continue

            new_status = signal["status"]
            changed = False

            if signal["status"] == "PENDING":
                if signal["side"] == "Long":
                    if live_price <= signal["entry_price"]:
                        new_status, changed = "ACTIVE", True
                    elif live_price >= signal["take_profit"] and settings["is_cancel_if_exceeds_target_enabled"]:
                        new_status, changed = "CANCELLED", True
                else:
                    if live_price >= signal["entry_price"]:
                        new_status, changed = "ACTIVE", True
                    elif live_price <= signal["take_profit"] and settings["is_cancel_if_exceeds_target_enabled"]:
                        new_status, changed = "CANCELLED", True

                # 🔴 إصلاح ثغرة حقيقية (بطلب صريح بعد تفعيل التداول الآلي): كان
                # الإلغاء هنا داخلي بحت (قاعدة بيانات فقط) — لو أمر Limit حقيقي
                # مُرسَل فعلاً على OKX (حالة "إعادة اختبار" لم تُملأ بعد)، يبقى
                # معلّقاً فعلياً على المنصة رغم إن التطبيق يعرضه "ملغى". الآن،
                # لما نلغي داخلياً، نلغي **فعلياً** نفس الأمر على OKX أيضاً —
                # بنفس أسلوب وقف التعادل بالضبط (تحقق ثم نفّذ، مع تسجيل واضح).
                # 🔴 إصلاح إضافي (اكتُشف بفحص شامل للمشروع): الشرط كان يتحقق أيضاً
                # من settings["exchange"]=="okx" — لكن هذا الإعداد يتحكم بـ**مصدر
                # بيانات الفحص فقط** (ممكن يكون Binance)، بينما التداول الحقيقي
                # دايماً على OKX بغض النظر عنه (_execute_auto_trade ما يتحقق من
                # exchange إطلاقاً). لو المستخدم يستخدم Binance كمصدر بيانات مع
                # تفعيل تداول OKX، الشرط القديم كان يمنع الإلغاء التلقائي هنا
                # بصمت رغم وجود أمر حقيقي معلّق فعلاً. أزلته، وأبقيت بس التحقق من
                # وجود مفتاح API فعلي.
                if (new_status == "CANCELLED" and settings.get("okx_is_auto_trading_enabled")
                        and settings.get("okx_api_key") and signal.get("okx_order_id")
                        and signal.get("okx_inst_id")):
                    for ord_id in str(signal["okx_order_id"]).split(","):
                        ord_id = ord_id.strip()
                        if not ord_id:
                            continue
                        try:
                            ok, msg = okx_client.cancel_pending_order(
                                signal["okx_inst_id"], ord_id,
                                settings["okx_api_key"], settings["okx_api_secret"],
                                settings["okx_passphrase"], settings["okx_is_testnet"],
                            )
                            if ok:
                                db.add_log(f"✅ [{signal['symbol']}] تم إلغاء أمر Limit المعلّق فعلياً على OKX (وصل الهدف قبل الدخول).")
                            else:
                                db.add_log(f"⚠️ [{signal['symbol']}] الإشارة أُلغيت داخلياً، لكن فشل إلغاء الأمر على OKX: {msg}")
                        except Exception as e:
                            db.add_log(f"⚠️ [{signal['symbol']}] خطأ أثناء محاولة إلغاء الأمر على OKX: {e}")
            elif signal["status"] == "ACTIVE":
                # قياس التراجع اللحظي من نقطة الدخول (مو الربح/الخسارة النهائي) —
                # يقيس "كم رجع السعر ضدنا" أثناء الصفقة، مفيد لتقييم قوة نقطة الدخول
                # نفسها بمعزل عن نتيجة الصفقة بالنهاية (رابحة أو خاسرة)
                entry_price = signal["entry_price"]
                if entry_price and entry_price > 0:
                    if signal["side"] == "Long":
                        adverse_pct = max(0.0, (entry_price - live_price) / entry_price * 100.0)
                        favorable_pct = max(0.0, (live_price - entry_price) / entry_price * 100.0)
                    else:
                        adverse_pct = max(0.0, (live_price - entry_price) / entry_price * 100.0)
                        favorable_pct = max(0.0, (entry_price - live_price) / entry_price * 100.0)
                    db.update_max_drawdown_if_worse(signal["id"], adverse_pct)
                    db.update_max_favorable_if_better(signal["id"], favorable_pct)

                    # 🎯 وقف التعادل التلقائي (Breakeven Stop) — بمجرد ما الربح العائم
                    # يعادل نسبة R محددة، ننقل الوقف لنقطة الدخول (داخلياً + على OKX
                    # فعلياً لو فيه مركز مفتوح حقيقي). فيه وضعين:
                    #  - يدوي: نسبة R ثابتة تحددها بالإعدادات (breakeven_trigger_r_multiple)
                    #  - تلقائي (بطلب صريح): نصف عائد/مخاطرة **الصفقة نفسها** — صفقة
                    #    هدفها 6R تتفعّل عند 3R، وصفقة هدفها 4R تتفعّل عند 2R، وهكذا.
                    initial_risk = signal.get("initial_risk_pct") or 0
                    if settings.get("is_auto_breakeven_half_target_enabled", False):
                        breakeven_r = (signal.get("rr") or 2.0) / 2.0
                    else:
                        breakeven_r = settings.get("breakeven_trigger_r_multiple", 1.0)
                    if (settings.get("is_breakeven_stop_enabled", True) and not signal.get("breakeven_activated")
                            and initial_risk > 0 and favorable_pct >= initial_risk * breakeven_r):
                        db.activate_breakeven(signal["id"], entry_price)
                        signal["stop_loss"] = entry_price  # نحدّث النسخة المحلية بنفس هذي الدورة أيضاً
                        db.add_log(f"🎯 [{signal['symbol']}] تفعيل وقف التعادل تلقائياً — الصفقة حققت ربح {round(breakeven_r,2)}R، الوقف انتقل لنقطة الدخول لحماية الأرباح.")

                        # 🔴 إرسال أمر حقيقي لتعديل وقف الخسارة على منصة OKX — فقط لو
                        # فيه مركز مفتوح فعلياً لهذي العملة بالذات (ما نرسل أمر عبثاً
                        # لو المستخدم ما نفّذ هذي الإشارة فعلياً على المنصة). الدالة
                        # نفسها تتحقق من وجود المركز أولاً قبل أي محاولة تعديل.
                        if settings.get("okx_api_key"):
                            try:
                                ok, msg = okx_client.amend_position_stop_loss(
                                    signal["symbol"], entry_price,
                                    settings["okx_api_key"], settings["okx_api_secret"], settings["okx_passphrase"],
                                    settings["okx_is_testnet"],
                                )
                                if ok:
                                    db.add_log(f"✅ [{signal['symbol']}] تم تعديل وقف الخسارة فعلياً على OKX لنقطة التعادل.")
                                elif "لا يوجد مركز" not in msg:
                                    db.add_log(f"⚠️ [{signal['symbol']}] تفعّل التعادل داخلياً، لكن فشل تعديله على OKX: {msg}")
                            except Exception as e:
                                db.add_log(f"⚠️ [{signal['symbol']}] خطأ أثناء محاولة تعديل الوقف على OKX: {e}")

                # 🎯 تقسيم الأهداف (بطلب صريح): لو مفعّل وما تحقق الهدف الأول بعد،
                # نتحقق هل وصل السعر له — لو نعم، نسجّل "نصف الكمية" وتستمر الصفقة
                # نشطة لبقية الكمية نحو الهدف الثاني أو وقف الخسارة، بدون ما نغلقها.
                if (signal.get("split_targets_used") and not signal.get("tp1_hit")
                        and signal.get("tp1_price")):
                    tp1_reached = ((signal["side"] == "Long" and live_price >= signal["tp1_price"]) or
                                    (signal["side"] == "Short" and live_price <= signal["tp1_price"]))
                    if tp1_reached:
                        # 📊 نحسب مساهمة الهدف الأول فوراً (نصف الكمية × نصف عائد/مخاطرة
                        # الصفقة) ونضيفها **فوراً** لعدّادات الأداء العامة — بدون ما ننتظر
                        # إغلاق الصفقة نهائياً، لأن هذا الجزء ربح مؤكَّد ومحقَّق فعلياً.
                        rr_full = signal.get("rr") or 0.0
                        tp1_contribution = round(0.5 * (rr_full / 2.0), 3)
                        db.mark_tp1_hit(signal["id"], tp1_contribution)
                        signal["tp1_hit"] = 1
                        db.add_log(f"🎯 [{signal['symbol']}] تحقق الهدف الأول! خرجت نصف الكمية بربح ({tp1_contribution}R محقَّق فوراً) — بقية الكمية مستمرة نحو الهدف النهائي.")
                        if settings["is_telegram_enabled"]:
                            telegram_alert.send_text_alert(
                                settings["telegram_token"], settings["telegram_chat_ids"],
                                f"🎯 {signal['symbol']}: تحقق الهدف الأول (+{tp1_contribution}R محقَّق) — خرجت نصف الكمية بربح، والباقي مستمر نحو الهدف النهائي.",
                            )

                        # 🔴 بمجرد ما نصف الكمية تخرج بربح مؤكَّد، النصف الباقي يصير
                        # منطقياً ينتقل لوقف تعادل فوري (حماية رأس المال) — بدل ما ننتظر
                        # شرط الـR العام المنفصل، اللي ممكن ما يتزامن بالضبط مع الهدف الأول.
                        if settings.get("is_breakeven_stop_enabled", True) and not signal.get("breakeven_activated"):
                            db.activate_breakeven(signal["id"], entry_price)
                            signal["breakeven_activated"] = 1
                            signal["stop_loss"] = entry_price
                            db.add_log(f"🎯 [{signal['symbol']}] انتقل وقف النصف الباقي لنقطة الدخول تلقائياً (مرتبط بتحقق الهدف الأول).")
                            if settings.get("okx_api_key"):
                                try:
                                    ok, msg = okx_client.amend_position_stop_loss(
                                        signal["symbol"], entry_price,
                                        settings["okx_api_key"], settings["okx_api_secret"], settings["okx_passphrase"],
                                        settings["okx_is_testnet"],
                                    )
                                    if ok:
                                        db.add_log(f"✅ [{signal['symbol']}] تم تعديل وقف النصف الباقي فعلياً على OKX لنقطة التعادل.")
                                    elif "لا يوجد مركز" not in msg:
                                        db.add_log(f"⚠️ [{signal['symbol']}] فشل تعديل وقف النصف الباقي على OKX: {msg}")
                                except Exception as e:
                                    db.add_log(f"⚠️ [{signal['symbol']}] خطأ أثناء تعديل وقف النصف الباقي على OKX: {e}")

                if signal["side"] == "Long":
                    if live_price <= signal["stop_loss"]:
                        # لو الوقف الحالي هو وقف التعادل المُفعَّل (مو الوقف الأصلي)، هذي
                        # "تعادل" وقائي حمى رأس المال، مو خسارة حقيقية بالتحليل نفسه
                        new_status = "BREAKEVEN" if signal.get("breakeven_activated") else "HIT_SL"
                        changed = True
                    elif live_price >= signal["take_profit"]:
                        new_status, changed = "HIT_TP", True
                else:
                    if live_price >= signal["stop_loss"]:
                        new_status = "BREAKEVEN" if signal.get("breakeven_activated") else "HIT_SL"
                        changed = True
                    elif live_price <= signal["take_profit"]:
                        new_status, changed = "HIT_TP", True

            already_notified = signal["last_notified_status"] == new_status
            transition_key = f"{signal['id']}_{new_status}"
            should_notify = changed and not already_notified and transition_key not in self._notified_transitions

            # 🎯 حساب العائد الفعلي (R) للصفقات المقسّمة اللي تحقق فيها الهدف الأول
            # فعلاً قبل الإغلاق النهائي — يجمع مساهمة كل نصف من الصفقة بدقة، بدل
            # الافتراض العام (رابح=RR كامل / خاسر=-1R) اللي ما يراعي التقسيم
            if changed and signal.get("split_targets_used") and signal.get("tp1_hit"):
                rr_full = signal.get("rr") or 0.0
                tp1_contribution = 0.5 * (rr_full / 2.0)  # نصف الكمية بنصف الـR
                if new_status == "HIT_TP":
                    actual_r = tp1_contribution + 0.5 * rr_full  # النصف الثاني وصل الهدف الكامل
                elif new_status in ("HIT_SL", "BREAKEVEN"):
                    actual_r = tp1_contribution - 0.5 * 1.0  # النصف الثاني ضرب وقف كامل (-1R على نصف الكمية)
                else:
                    actual_r = None
                if actual_r is not None:
                    db.close_signal_with_actual_r(signal["id"], new_status, live_price, actual_r,
                                                   new_status if should_notify else signal["last_notified_status"])
                    db.add_log(f"📊 [{signal['symbol']}] صفقة مقسّمة الأهداف أُغلقت — العائد الفعلي المجمّع: {actual_r:.2f}R")
                else:
                    db.update_signal_status(signal["id"], new_status, live_price,
                                             new_status if should_notify else signal["last_notified_status"])
            else:
                db.update_signal_status(signal["id"], new_status, live_price,
                                         new_status if should_notify else signal["last_notified_status"])

            if should_notify:
                self._notified_transitions.add(transition_key)
                db.add_log(f"🔄 [{signal['symbol']}] تغيرت حالة الصفقة إلى {new_status} (السعر الحالي: {live_price})")
                if settings["is_telegram_enabled"]:
                    telegram_alert.send_status_alert(
                        settings["telegram_token"], settings["telegram_chat_ids"],
                        signal["symbol"], signal["side"], new_status, live_price,
                    )


scanner_state = ScannerState()
