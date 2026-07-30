"""
نظام الاختبار الخلفي (Backtesting) — يشغّل كل استراتيجية على شهور من البيانات
التاريخية الحقيقية دفعة وحدة، بدل انتظار أيام لكل تجربة حية.

المبدأ الأهم: **صفر تحيّز نظر مستقبلي (Zero Look-Ahead Bias)**. بكل نقطة زمنية
"تُحاكى" كأنها اللحظة الحالية، الاستراتيجية ترى فقط الشموع المكتملة حتى تلك
اللحظة بالضبط — تماماً زي وضعها الحقيقي وقت الفحص الحي، بدون أي غش أو نظر للمستقبل.

⚠️ محدودية معروفة وصريحة: بيانات البنية الجزئية اللحظية (CVD، ضغط المتداولين،
الفائدة المفتوحة، معدل التمويل، عمق دفتر الأوامر) **غير متوفرة تاريخياً** من
واجهات المنصات العامة — هذي بيانات "اللحظة الحالية" بس، مو سجل تاريخي. الاختبار
الخلفي يشغّل الاستراتيجيات بدون هذي البيانات (micro=None)، فالاستراتيجيات اللي
تعتمد عليها **كشرط إلزامي** (فابيو تحديداً، تحتاج CVD/ضغط متداولين لتحديد
Direction) لن تولّد أي إشارة بالاختبار الخلفي — هذا صادق ومتوقع، مو خطأ بالنظام.
النتائج الموثوقة هنا هي للاستراتيجيات المعتمدة أساساً على السعر والفوليوم
(فيبوناتشي، السكالب، الانفجار السعري، صيد الاستوبات، الارتداد بعد التصريف).
"""
import threading
import time
from typing import List, Dict, Any, Optional
from .analyzer import Kline, efficiency_ratio, _get_bias
from .strategies import STRATEGY_REGISTRY
from . import db

# 🆕 حالة المهمة الخلفية العالمية — يشتغل الاختبار الخلفي بخيط منفصل (نفس نمط
# السكانر الحي بالضبط)، بدون ما يعطّل الخادم أو يحجب باقي الطلبات وقت التشغيل
_job_state = {
    "running": False,
    "progress": 0,
    "total": 0,
    "current_symbol": "",
    "results": [],
    "stats": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "settings_snapshot": None,
    "use_live_settings": True,
}
_job_lock = threading.Lock()


def get_job_status() -> Dict[str, Any]:
    with _job_lock:
        return dict(_job_state)


def _run_backtest_job(symbols: List[str], days_back: int, exchange, strategy_keys: Optional[List[str]] = None,
                       use_live_settings: bool = True):
    with _job_lock:
        _job_state.update({"running": True, "progress": 0, "total": len(symbols), "results": [],
                            "stats": None, "error": None, "started_at": time.time(), "finished_at": None,
                            "settings_snapshot": None, "use_live_settings": use_live_settings})
    all_results = []
    try:
        # 🔴 بطلب صريح: نجيب إعداداتك الحقيقية المحفوظة بالتطبيق (نفس الفلاتر،
        # التعادل، تقسيم الأهداف اللي مفعّلة عندك الآن)، ونستخدمها بالاختبار
        # الخلفي كامل — مو إعدادات افتراضية مصطنعة، نفس تجربتك الحقيقية بالضبط.
        settings = db.get_settings()

        # 🆕 إصلاح مهم (بطلب صريح): نسجّل لقطة كاملة من الإعدادات الفعلية
        # المستخدمة وقت هذا التشغيل بالذات — بدونها ما نقدر نقارن بدقة بين
        # اختبارين مختلفين لاحقاً (نفس المشكلة اللي واجهناها لما اختلفت نتيجة
        # XRP لوحدها عن نتيجة 3 عملات مع بعض، وما قدرنا نتأكد هل السبب فرق
        # بالإعدادات أو فرق بالعملات/العيّنة لأن التصدير ما كان يسجّل الإعدادات).
        # لو الوضع الخام (use_live_settings=False)، نسجّل هذا صراحة بدل الإعدادات
        # الحقيقية، عشان يكون واضح تماماً بالتصدير أي وضع استُخدم بالضبط.
        if use_live_settings:
            safe_settings = {k: v for k, v in settings.items()
                             if not any(k.startswith(p) for p in ("okx_", "telegram_", "gdrive_"))}
        else:
            safe_settings = {"mode": "raw — بدون أي فلتر أو إدارة صفقة إضافية (تعادل/تقسيم)، كل صفقة خام على الوقف والهدف الأصليين فقط"}
        with _job_lock:
            _job_state["settings_snapshot"] = safe_settings

        # نجيب بيانات البيتكوين مرة وحدة لكل الفترة (لفلاتر التوافق ونظام السوق)
        btc_data = None
        if use_live_settings and (settings.get("is_market_alignment_filter_enabled") or settings.get("is_market_regime_filter_enabled")):
            btc_data = fetch_backtest_data("BTCUSDT", days_back, exchange)

        for idx, symbol in enumerate(symbols):
            with _job_lock:
                _job_state["current_symbol"] = symbol
                _job_state["progress"] = idx

            def _progress_cb(step, total_steps, sym=symbol, base=idx):
                with _job_lock:
                    _job_state["current_symbol"] = f"{sym} ({step}/{total_steps} نقطة قرار)"

            symbol_results = run_symbol_backtest(symbol, days_back, exchange, strategy_keys,
                                                  settings=settings, btc_data=btc_data,
                                                  use_live_settings=use_live_settings,
                                                  progress_callback=_progress_cb)
            all_results.extend(symbol_results)
    except Exception as e:
        with _job_lock:
            _job_state["error"] = str(e)
    finally:
        stats = compute_backtest_stats(all_results)
        with _job_lock:
            _job_state.update({"running": False, "progress": len(symbols), "results": all_results,
                                "stats": stats, "finished_at": time.time()})


def start_backtest_job(symbols: List[str], days_back: int, exchange, strategy_keys: Optional[List[str]] = None,
                        use_live_settings: bool = True) -> bool:
    """يبدأ مهمة اختبار خلفي جديدة بخيط منفصل — يرجع False لو فيه مهمة شغّالة أصلاً."""
    with _job_lock:
        if _job_state["running"]:
            return False
    t = threading.Thread(target=_run_backtest_job, args=(symbols, days_back, exchange, strategy_keys, use_live_settings), daemon=True)
    t.start()
    return True


# استراتيجيات تعتمد بشكل إلزامي على بيانات لحظية غير متوفرة تاريخياً — نستثنيها
# من الاختبار الخلفي صراحة بدل ما نعطي نتيجة "صفر إشارة" مضلِّلة بلا توضيح
_MICRO_DEPENDENT_STRATEGIES = {"fabio_scalper", "crowd_trap"}


class _TimeframeCursor:
    """يتتبع مؤشر متقدم بقائمة شموع مرتبة زمنياً — يعطي "كل الشموع المكتملة حتى
    وقت X" بكفاءة O(n) إجمالية (المؤشر يتقدم للأمام بس، ما يرجع للخلف أبداً)،
    بدل قص القائمة (slice) بكل تكرار وهذا مكلف O(n) بكل مرة."""
    def __init__(self, klines: List[Kline]):
        self.klines = klines
        self.idx = 0

    def advance_to(self, timestamp: int) -> List[Kline]:
        while self.idx < len(self.klines) and self.klines[self.idx].close_time <= timestamp:
            self.idx += 1
        return self.klines[:self.idx]


def _choose_fine_interval(days_back: int) -> str:
    """🆕 نفس فكرة التدرّج، لكن لفريم "الدقة الدقيقة" المستخدم بمحاكاة نتيجة كل
    صفقة (تحديد أيهم انضرب أول: الوقف أو الهدف). رُفعت الحدود بشكل كبير (بطلب
    صريح، قبول وقت جلب/معالجة أطول مقابل دقة أعلى) — دقة عالية حتى فترات
    طويلة نسبياً، مو بس أقل من شهر."""
    if days_back <= 60:
        return "5m"
    elif days_back <= 180:
        return "15m"
    else:
        return "1h"


def fetch_backtest_data(symbol: str, days_back: int, exchange) -> Optional[Dict[str, List[Kline]]]:
    """يجيب كل الفريمات المطلوبة لفترة الاختبار الخلفي دفعة وحدة."""
    fine_tf = _choose_fine_interval(days_back)
    candles_per_day = {"5m": 288, "15m": 96, "1h": 24}[fine_tf]
    candles_fine = days_back * candles_per_day
    candles_15m = days_back * 96 if fine_tf == "5m" else 0  # نحتاجها بس لو الفريم الدقيق أخشن منها
    candles_1h = days_back * 24 + 60
    candles_4h = days_back * 6 + 60
    candles_daily = days_back + 60

    k_fine = exchange.fetch_historical_klines(symbol, fine_tf, candles_fine)
    if not k_fine or len(k_fine) < 100:
        return None
    k15m = k_fine if fine_tf == "15m" else exchange.fetch_historical_klines(symbol, "15m", max(candles_15m, days_back * 96))
    k1h = k_fine if fine_tf == "1h" else exchange.fetch_historical_klines(symbol, "1h", candles_1h)
    k4h = exchange.fetch_historical_klines(symbol, "4h", candles_4h)
    k_daily = exchange.fetch_historical_klines(symbol, "1d", candles_daily)
    return {"fine": k_fine, "fine_tf": fine_tf, "15m": k15m, "1h": k1h, "4h": k4h, "1d": k_daily}


def _simulate_trade_outcome(result, k_future: List[Kline], settings: dict, max_candles: int = 800) -> Dict[str, Any]:
    """يمشي عبر الشموع **بعد** لحظة توليد الإشارة، يطبّق **نفس التعادل وتقسيم
    الأهداف المفعّلين عندك بالإعدادات الحقيقية** بالضبط (مو محاكاة خام)، ويحدد
    النتيجة النهائية + العائد الفعلي المحقَّق."""
    entry, sl_original, tp, side = result.entry_price, result.stop_loss, result.take_profit, result.side
    rr_full = result.rr or 0.0
    initial_risk_pct = abs(entry - sl_original) / entry * 100 if entry else 0.0

    is_split = bool(settings.get("is_split_targets_enabled", False))
    tp1_price = entry + (tp - entry) * 0.5 if is_split else None
    tp1_hit = False

    # 🔴 إصلاح خلل حقيقي: كانت القيم الافتراضية (fallback) True — فحتى بالوضع
    # الخام (قاموس settings فاضٍ)، كان .get(key, True) يرجع True بالخطأ ويفعّل
    # التعادل تلقائياً! الآن fallback = False دائماً، فالقاموس الفاضي = كل شي
    # معطّل فعلياً كما هو متوقع بالوضع الخام، بغض النظر عن الافتراضي بالتطبيق الحي.
    is_breakeven_enabled = bool(settings.get("is_breakeven_stop_enabled", False))
    is_auto_be_half = bool(settings.get("is_auto_breakeven_half_target_enabled", False))
    manual_be_r = settings.get("breakeven_trigger_r_multiple", 1.0)
    breakeven_r = (rr_full / 2.0) if is_auto_be_half else manual_be_r
    breakeven_activated = False
    current_sl = sl_original

    entered = False
    max_favorable_pct = 0.0

    def _final_result(status: str, resolve_idx: int) -> Dict[str, Any]:
        actual_r = None
        if is_split and tp1_hit:
            tp1_contribution = 0.5 * (rr_full / 2.0)
            if status == "HIT_TP":
                actual_r = tp1_contribution + 0.5 * rr_full
            else:  # HIT_SL أو BREAKEVEN على النصف الباقي
                remainder = 0.0 if (status == "BREAKEVEN") else -0.5
                actual_r = tp1_contribution + remainder
        return {"status": status, "candles_to_resolve": resolve_idx,
                "max_favorable_pct": max_favorable_pct, "actual_r_achieved": actual_r,
                "breakeven_activated": breakeven_activated, "tp1_hit": tp1_hit}

    for i, k in enumerate(k_future[:max_candles]):
        if not entered:
            if side == "Long" and k.low <= entry:
                entered = True
            elif side == "Short" and k.high >= entry:
                entered = True
            if not entered:
                continue

        if side == "Long":
            favorable_pct = (k.high - entry) / entry * 100
        else:
            favorable_pct = (entry - k.low) / entry * 100
        max_favorable_pct = max(max_favorable_pct, favorable_pct)

        # 🎯 تقسيم الأهداف: تحقق الهدف الأول (لو مفعّل ولسا ما تحقق)
        if is_split and not tp1_hit and tp1_price:
            if (side == "Long" and k.high >= tp1_price) or (side == "Short" and k.low <= tp1_price):
                tp1_hit = True
                # بمجرد تحقق الهدف الأول، النصف الباقي ينتقل لوقف تعادل تلقائياً
                # (نفس منطق السكانر الحي بالضبط — حماية فورية لرأس المال)
                if is_breakeven_enabled and not breakeven_activated:
                    breakeven_activated = True
                    current_sl = entry

        # ⚖️ التعادل التلقائي (لو ما تفعّل أصلاً عبر تحقق الهدف الأول أعلاه)
        if is_breakeven_enabled and not breakeven_activated and initial_risk_pct > 0:
            if favorable_pct >= initial_risk_pct * breakeven_r:
                breakeven_activated = True
                current_sl = entry

        if side == "Long":
            if k.high >= tp:
                return _final_result("HIT_TP", i)
            if k.low <= current_sl:
                status = "BREAKEVEN" if (breakeven_activated and current_sl == entry) else "HIT_SL"
                return _final_result(status, i)
        else:
            if k.low <= tp:
                return _final_result("HIT_TP", i)
            if k.high >= current_sl:
                status = "BREAKEVEN" if (breakeven_activated and current_sl == entry) else "HIT_SL"
                return _final_result(status, i)

    if not entered:
        return {"status": "NEVER_FILLED", "candles_to_resolve": None, "max_favorable_pct": 0.0,
                "actual_r_achieved": None, "breakeven_activated": False, "tp1_hit": False}
    return _final_result("TIMEOUT", max_candles)


def _choose_decision_interval(days_back: int) -> str:
    """🆕 رُفعت الحدود بشكل كبير (بطلب صريح، قبول وقت تنفيذ أطول مقابل دقة أعلى):
    فترات حتى شهرين تستخدم دقة عالية (15 دقيقة)، حتى نصف سنة دقة متوسطة (ساعة)،
    وبس الفترات الأطول من ذلك (6+ أشهر) تستخدم دقة أخشن (4 ساعات) — تنازل معقول
    بس بحالات نادرة جداً (اختبارات سنة كاملة)، مو بالشهر أو حتى بضع أشهر عادية."""
    if days_back <= 60:
        return "15m"
    elif days_back <= 180:
        return "1h"
    else:
        return "4h"


def run_symbol_backtest(symbol: str, days_back: int, exchange,
                         strategy_keys: Optional[List[str]] = None,
                         decision_interval: Optional[str] = None,
                         settings: Optional[dict] = None,
                         btc_data: Optional[Dict[str, List[Kline]]] = None,
                         use_live_settings: bool = True,
                         progress_callback=None) -> List[Dict[str, Any]]:
    """يشغّل الاختبار الخلفي الكامل لعملة وحدة عبر كل الاستراتيجيات المطلوبة.
    يرجع قائمة نتائج (كل عنصر = صفقة محاكاة كاملة بنفس بنية الصفقات الحقيقية).

    use_live_settings=True (افتراضي): تُطبَّق نفس فلاترك الحقيقية بالتطبيق (توافق
    الترند، الكفاءة الاتجاهية، نظام السوق) + نفس إدارة الصفقة (التعادل، تقسيم
    الأهداف) — يحاكي تجربتك الحقيقية بالضبط.

    use_live_settings=False (🆕 الوضع الخام — بطلب صريح): يتجاوز **كل** الفلاتر
    (تُقبل كل إشارة تولّدها الاستراتيجية مباشرة)، وتُلغى محاكاة التعادل وتقسيم
    الأهداف بالكامل — كل صفقة تُحسب فوز/خسارة خام على الوقف/الهدف الأصليين بس.
    هذا يعطي صورة "الأداء الخام المحض" للاستراتيجية نفسها، بمعزل عن أي إعداد،
    عشان تقارنه بدقة مقابل نتيجة الوضع بالإعدادات الحقيقية."""
    from . import db as _db
    settings = settings or _db.get_settings()
    outcome_settings = settings if use_live_settings else {}  # قاموس فاضي = كل شي معطّل تلقائياً (لا تعادل، لا تقسيم)
    decision_interval = decision_interval or _choose_decision_interval(days_back)
    data = fetch_backtest_data(symbol, days_back, exchange)
    if not data:
        return []

    strategy_keys = strategy_keys or [k for k in STRATEGY_REGISTRY if k != "confluence"]
    decision_klines = data[decision_interval]
    if len(decision_klines) < 60:
        return []

    cursors = {tf: _TimeframeCursor(klines) for tf, klines in data.items() if tf != "fine_tf"}
    k_fine_full = data["fine"]
    k_fine_index_by_time = {k.open_time: idx for idx, k in enumerate(k_fine_full)}

    # 🔴 مؤشرات زمنية منفصلة لبيانات البيتكوين (لو متوفرة) — نفس الفكرة، نتقدّم
    # بالتوازي مع الوقت الحالي، بدون أي تحيّز نظر مستقبلي لبيانات البيتكوين أيضاً
    btc_cursors = None
    is_btc_symbol = symbol.upper().startswith("BTC")
    if btc_data and not is_btc_symbol:
        btc_cursors = {tf: _TimeframeCursor(klines) for tf, klines in btc_data.items() if tf != "fine_tf"}

    # 🔴 إصلاح منهجي خطير (اكتشاف مباشر بمراجعة تصدير حقيقي): كان الاختبار الخلفي
    # **يعيد اكتشاف نفس فرصة صيد الاستوبات كل نقطة قرار** (كل 15 دقيقة) طول ما
    # السعر لسا داخل نفس الهيكل التاريخي — نفس مستوى الوقف بالضبط تكرر 16 مرة
    # متتالية بتصدير حقيقي، وكل تكرار احتُسب "صفقة فوز مستقلة"! هذا يضخّم النتائج
    # بشكل وهمي (100% نجاح، أرقام "مثالية" غير واقعية). بالفحص الحي فيه منع تكرار
    # حقيقي (ما نفتح صفقة جديدة على نفس العملة+الاستراتيجية+الاتجاه طول ما فيه
    # صفقة نشطة بالفعل)، لكن الباك تيست كان يفتقده تماماً. نتتبع الآن "آخر وقت
    # نشاط" لكل تركيبة، ونمنع أي صفقة جديدة عليها طول ما السابقة نظرياً نشطة.
    active_until: Dict[tuple, int] = {}

    results = []
    start_idx = 60
    total_steps = len(decision_klines) - start_idx

    for step, i in enumerate(range(start_idx, len(decision_klines))):
        decision_point = decision_klines[i]
        current_time = decision_point.close_time

        if progress_callback and step % 200 == 0:
            progress_callback(step, total_steps)

        k4h_asof = cursors["4h"].advance_to(current_time)
        k1h_asof = cursors["1h"].advance_to(current_time)
        k15m_asof = cursors["15m"].advance_to(current_time)
        k_fine_asof = cursors["fine"].advance_to(current_time)
        k_daily_asof = cursors["1d"].advance_to(current_time)

        if len(k4h_asof) < 30 or len(k1h_asof) < 30 or len(k15m_asof) < 30 or len(k_fine_asof) < 30:
            continue

        # حساب اتجاه البيتكوين وقوة نظام السوق "حتى هذي اللحظة بالضبط" — نفس
        # المنطق المستخدم بالفحص الحي، بدون أي نظر مستقبلي لبيانات البيتكوين
        btc_trend = None
        btc_klines_asof = None
        market_regime_er = None
        if btc_cursors:
            btc_klines_asof = btc_cursors["4h"].advance_to(current_time)
            if len(btc_klines_asof) >= 30:
                btc_trend = _get_bias(btc_klines_asof)
                market_regime_er = efficiency_ratio(btc_klines_asof, period=20)
        elif is_btc_symbol:
            btc_trend = _get_bias(k4h_asof)
            market_regime_er = efficiency_ratio(k4h_asof, period=20)

        for strategy_key in strategy_keys:
            if strategy_key in _MICRO_DEPENDENT_STRATEGIES:
                continue  # مستبعدة صراحة — تحتاج بيانات لحظية غير متوفرة تاريخياً
            fn = STRATEGY_REGISTRY[strategy_key]["fn"]
            try:
                # نمرّر الفريم الدقيق المتدرّج بمكان k5m — أدق فريم متاح لهذي الفترة
                result = fn(symbol, k4h_asof, k1h_asof, k15m_asof, k_fine_asof, k_daily_asof, micro=None, trace=None)
            except Exception:
                continue
            if not result:
                continue

            # 🔴 فحص منع التكرار (نفس منطق الفحص الحي بالضبط): لو فيه صفقة سابقة
            # على نفس (الرمز، الاستراتيجية، الاتجاه) لسا نظرياً نشطة بهذي اللحظة،
            # نتخطى — هذا يمنع احتساب نفس الفرصة الحقيقية عدة مرات كصفقات مستقلة
            dup_key = (symbol, strategy_key, result.side)
            if active_until.get(dup_key, -1) >= current_time:
                continue

            # 🔴 نفس دالة الفلاتر المشتركة المستخدمة بالفحص الحي بالضبط — لكن
            # بالوضع الخام (use_live_settings=False) نتجاوزها بالكامل، نقبل كل
            # إشارة تولّدها الاستراتيجية مباشرة (بدون أي فلترة إضافية)
            if use_live_settings:
                from .scanner import evaluate_signal_filters
                accepted, _reason, _counter = evaluate_signal_filters(
                    settings, symbol, strategy_key, result, k4h_asof, k1h_asof, k15m_asof, k_fine_asof,
                    btc_trend, btc_klines_asof, market_regime_er,
                )
                if not accepted:
                    continue

            # نجيب شموع الفريم الدقيق اللي بعد لحظة الإشارة فعلياً (بيانات حقيقية
            # مستقبلية بالنسبة لهذي اللحظة — هذا صحيح ومقصود هنا، لأننا الآن "نتحقق
            # من النتيجة" بعد ما ولّدنا الإشارة بناءً على بيانات ماضية فقط، مو نغش)
            future_start = k_fine_index_by_time.get(k_fine_asof[-1].open_time, len(k_fine_full) - 1) + 1
            k_fine_future = k_fine_full[future_start:]
            if not k_fine_future:
                continue

            outcome = _simulate_trade_outcome(result, k_fine_future, outcome_settings)
            if outcome["status"] == "NEVER_FILLED":
                continue  # السعر ما وصل نقطة الدخول إطلاقاً — نتجاهلها (نفس منطق الإشارات المُلغاة حياً)

            # نحسب وقت الإغلاق الحقيقي (من عدد الشموع المستغرقة بالمحاكاة) ونسجّله
            # كـ"مشغول" لهذي التركيبة — أي صفقة جديدة عليها قبل هذا الوقت تُتخطى
            resolve_idx = outcome.get("candles_to_resolve")
            if resolve_idx is not None and resolve_idx < len(k_fine_future):
                active_until[dup_key] = k_fine_future[resolve_idx].close_time
            else:
                active_until[dup_key] = k_fine_future[-1].close_time  # انتهت المهلة القصوى بدون قرار

            results.append({
                "symbol": symbol, "strategy": strategy_key, "side": result.side,
                "entry_price": result.entry_price, "stop_loss": result.stop_loss,
                "take_profit": result.take_profit, "rr": result.rr,
                "probability": result.prob, "quality": result.quality,
                "signal_score": getattr(result, "signal_score", 100.0),
                "breakeven_activated": outcome.get("breakeven_activated", False),
                "tp1_hit": outcome.get("tp1_hit", False),
                "actual_r_achieved": outcome.get("actual_r_achieved"),
                "timestamp": current_time, "status": outcome["status"],
                "max_favorable_pct": outcome["max_favorable_pct"],
            })

    return results


def compute_backtest_stats(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """يحسب إحصائيات ملخّصة — نفس منطق get_strategy_performance() الحي بالضبط،
    مطبَّق على نتائج الاختبار الخلفي. يستخدم العائد الفعلي المحسوب (actual_r_achieved)
    لو الصفقة مقسّمة الأهداف، بدل الافتراض العام (رابح=rr كامل/خاسر=-1R)."""
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for r in results:
        strat = r["strategy"]
        by_strategy.setdefault(strat, {"wins": 0, "losses": 0, "breakeven": 0, "timeout": 0,
                                        "total_win_r": 0.0, "total_loss_r": 0.0})
        actual_r = r.get("actual_r_achieved")
        if r["status"] == "HIT_TP":
            by_strategy[strat]["wins"] += 1
            by_strategy[strat]["total_win_r"] += actual_r if actual_r is not None else (r["rr"] or 0.0)
        elif r["status"] == "HIT_SL":
            by_strategy[strat]["losses"] += 1
            if actual_r is not None:
                if actual_r >= 0:
                    by_strategy[strat]["total_win_r"] += actual_r
                else:
                    by_strategy[strat]["total_loss_r"] += -actual_r
            else:
                by_strategy[strat]["total_loss_r"] += 1.0
        elif r["status"] == "BREAKEVEN":
            by_strategy[strat]["breakeven"] += 1
            if actual_r is not None and actual_r != 0:
                if actual_r >= 0:
                    by_strategy[strat]["total_win_r"] += actual_r
                else:
                    by_strategy[strat]["total_loss_r"] += -actual_r
        else:
            by_strategy[strat]["timeout"] += 1

    summary = []
    for strat, v in by_strategy.items():
        closed = v["wins"] + v["losses"]
        win_rate = round((v["wins"] / closed) * 100, 1) if closed > 0 else 0.0
        net_r = round(v["total_win_r"] - v["total_loss_r"], 2)
        summary.append({
            "strategy": strat, "total_trades": closed + v["breakeven"] + v["timeout"], "closed_trades": closed,
            "wins": v["wins"], "losses": v["losses"], "breakeven": v["breakeven"], "timeout": v["timeout"],
            "win_rate": win_rate, "total_win_r": round(v["total_win_r"], 2),
            "total_loss_r": round(-v["total_loss_r"], 2), "net_r": net_r,
        })
    summary.sort(key=lambda x: -x["closed_trades"])
    return {"per_strategy": summary, "total_signals": len(results)}
