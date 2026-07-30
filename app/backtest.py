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
from .analyzer import Kline
from .strategies import STRATEGY_REGISTRY

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
}
_job_lock = threading.Lock()


def get_job_status() -> Dict[str, Any]:
    with _job_lock:
        return dict(_job_state)


def _run_backtest_job(symbols: List[str], days_back: int, exchange, strategy_keys: Optional[List[str]] = None):
    with _job_lock:
        _job_state.update({"running": True, "progress": 0, "total": len(symbols), "results": [],
                            "stats": None, "error": None, "started_at": time.time(), "finished_at": None})
    all_results = []
    try:
        for idx, symbol in enumerate(symbols):
            with _job_lock:
                _job_state["current_symbol"] = symbol
                _job_state["progress"] = idx

            def _progress_cb(step, total_steps, sym=symbol, base=idx):
                with _job_lock:
                    _job_state["current_symbol"] = f"{sym} ({step}/{total_steps} نقطة قرار)"

            symbol_results = run_symbol_backtest(symbol, days_back, exchange, strategy_keys,
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


def start_backtest_job(symbols: List[str], days_back: int, exchange, strategy_keys: Optional[List[str]] = None) -> bool:
    """يبدأ مهمة اختبار خلفي جديدة بخيط منفصل — يرجع False لو فيه مهمة شغّالة أصلاً."""
    with _job_lock:
        if _job_state["running"]:
            return False
    t = threading.Thread(target=_run_backtest_job, args=(symbols, days_back, exchange, strategy_keys), daemon=True)
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
    صفقة (تحديد أيهم انضرب أول: الوقف أو الهدف). لفترات طويلة (أشهر/سنة)، جلب
    شموع 5 دقايق لسنة كاملة (~105,000 شمعة) يحتاج آلاف طلبات الترقيم — غير عملي.
    نستخدم فريم أخشن تدريجياً (لسا أدق من فريم القرار نفسه، يحافظ على دقة معقولة
    لالتقاط لمسات الوقف/الهدف، بدون حجم بيانات غير قابل للتنفيذ)."""
    if days_back <= 20:
        return "5m"
    elif days_back <= 90:
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


def _simulate_trade_outcome(result, k5m_future: List[Kline], max_candles: int = 800) -> Dict[str, Any]:
    """يمشي عبر شموع 5 دقايق **بعد** لحظة توليد الإشارة، يحدد أيهم انضرب أول
    (الوقف أو الهدف)، ويتتبع أعلى ربح عائم (MFE) بالطريق — نفس منطق التتبع الحي
    بالضبط، بس على بيانات تاريخية مؤكَّدة."""
    entry, sl, tp, side = result.entry_price, result.stop_loss, result.take_profit, result.side
    initial_risk_pct = abs(entry - sl) / entry * 100 if entry else 0
    entered = False
    max_favorable_pct = 0.0

    for i, k in enumerate(k5m_future[:max_candles]):
        if not entered:
            # ننتظر السعر يوصل نقطة الدخول (نفس منطق أمر Limit الحقيقي)
            if side == "Long" and k.low <= entry:
                entered = True
            elif side == "Short" and k.high >= entry:
                entered = True
            if not entered:
                continue
        # تتبع الربح العائم
        if side == "Long":
            favorable = (k.high - entry) / entry * 100
        else:
            favorable = (entry - k.low) / entry * 100
        max_favorable_pct = max(max_favorable_pct, favorable)

        if side == "Long":
            if k.low <= sl:
                return {"status": "HIT_SL", "candles_to_resolve": i, "max_favorable_pct": max_favorable_pct}
            if k.high >= tp:
                return {"status": "HIT_TP", "candles_to_resolve": i, "max_favorable_pct": max_favorable_pct}
        else:
            if k.high >= sl:
                return {"status": "HIT_SL", "candles_to_resolve": i, "max_favorable_pct": max_favorable_pct}
            if k.low <= tp:
                return {"status": "HIT_TP", "candles_to_resolve": i, "max_favorable_pct": max_favorable_pct}

    if not entered:
        return {"status": "NEVER_FILLED", "candles_to_resolve": None, "max_favorable_pct": 0.0}
    return {"status": "TIMEOUT", "candles_to_resolve": max_candles, "max_favorable_pct": max_favorable_pct}


def _choose_decision_interval(days_back: int) -> str:
    """🆕 تدرّج ذكي لدقة نقطة القرار حسب طول الفترة — يبقي إجمالي نقاط القرار
    بنطاق معقول (~1500-2500) بغض النظر عن طول الفترة المطلوبة، فوقت التنفيذ
    يبقى قابل للتنبؤ سواء كانت الفترة أسبوع أو سنة كاملة. فترات قصيرة تستخدم
    دقة عالية (15 دقيقة)، فترات طويلة جداً تستخدم دقة أخشن (4 ساعات) — تنازل
    منطقي بين الدقة والسرعة، مو تفريط بالجودة."""
    if days_back <= 20:
        return "15m"
    elif days_back <= 90:
        return "1h"
    else:
        return "4h"


def run_symbol_backtest(symbol: str, days_back: int, exchange,
                         strategy_keys: Optional[List[str]] = None,
                         decision_interval: Optional[str] = None,
                         progress_callback=None) -> List[Dict[str, Any]]:
    """يشغّل الاختبار الخلفي الكامل لعملة وحدة عبر كل الاستراتيجيات المطلوبة.
    يرجع قائمة نتائج (كل عنصر = صفقة محاكاة كاملة بنفس بنية الصفقات الحقيقية)."""
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

    results = []
    # نبدأ من نقطة فيها بيانات تاريخية كافية للمؤشرات (60 شمعة قرار على الأقل كهامش أمان)
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

            # نجيب شموع الفريم الدقيق اللي بعد لحظة الإشارة فعلياً (بيانات حقيقية
            # مستقبلية بالنسبة لهذي اللحظة — هذا صحيح ومقصود هنا، لأننا الآن "نتحقق
            # من النتيجة" بعد ما ولّدنا الإشارة بناءً على بيانات ماضية فقط، مو نغش)
            future_start = k_fine_index_by_time.get(k_fine_asof[-1].open_time, len(k_fine_full) - 1) + 1
            k_fine_future = k_fine_full[future_start:]
            if not k_fine_future:
                continue

            outcome = _simulate_trade_outcome(result, k_fine_future)
            if outcome["status"] == "NEVER_FILLED":
                continue  # السعر ما وصل نقطة الدخول إطلاقاً — نتجاهلها (نفس منطق الإشارات المُلغاة حياً)

            results.append({
                "symbol": symbol, "strategy": strategy_key, "side": result.side,
                "entry_price": result.entry_price, "stop_loss": result.stop_loss,
                "take_profit": result.take_profit, "rr": result.rr,
                "probability": result.prob, "quality": result.quality,
                "signal_score": getattr(result, "signal_score", 100.0),
                "timestamp": current_time, "status": outcome["status"],
                "max_favorable_pct": outcome["max_favorable_pct"],
            })

    return results


def compute_backtest_stats(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """يحسب إحصائيات ملخّصة — نفس منطق get_strategy_performance() الحي بالضبط،
    مطبَّق على نتائج الاختبار الخلفي."""
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for r in results:
        strat = r["strategy"]
        by_strategy.setdefault(strat, {"wins": 0, "losses": 0, "timeout": 0, "total_win_r": 0.0, "total_loss_r": 0.0})
        if r["status"] == "HIT_TP":
            by_strategy[strat]["wins"] += 1
            by_strategy[strat]["total_win_r"] += (r["rr"] or 0.0)
        elif r["status"] == "HIT_SL":
            by_strategy[strat]["losses"] += 1
            by_strategy[strat]["total_loss_r"] += 1.0
        else:
            by_strategy[strat]["timeout"] += 1

    summary = []
    for strat, v in by_strategy.items():
        closed = v["wins"] + v["losses"]
        win_rate = round((v["wins"] / closed) * 100, 1) if closed > 0 else 0.0
        net_r = round(v["total_win_r"] - v["total_loss_r"], 2)
        summary.append({
            "strategy": strat, "total_trades": closed + v["timeout"], "closed_trades": closed,
            "wins": v["wins"], "losses": v["losses"], "timeout": v["timeout"],
            "win_rate": win_rate, "total_win_r": round(v["total_win_r"], 2),
            "total_loss_r": round(-v["total_loss_r"], 2), "net_r": net_r,
        })
    summary.sort(key=lambda x: -x["closed_trades"])
    return {"per_strategy": summary, "total_signals": len(results)}
