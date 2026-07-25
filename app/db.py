"""طبقة قاعدة بيانات SQLite بسيطة — تعادل Room DB (AppSettings, TradeSignal) في التطبيق الأصلي."""
import os
import shutil
import sqlite3
import time
import threading
import json
from typing import Optional, List, Dict, Any

_OLD_DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "orion.db")
DB_PATH = os.environ.get("ORION_DB_PATH", _OLD_DEFAULT_DB_PATH)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# نقل تلقائي آمن لمرة واحدة: لو المستخدم فعّل مسار خارجي جديد (ORION_DB_PATH) لأول مرة
# ولسا ما فيه قاعدة بيانات بهذا المسار الجديد، لكن فيه بيانات قديمة بالمسار الافتراضي
# القديم (جوا مجلد المشروع)، ننسخها تلقائياً للمسار الجديد بدل ما تضيع بصمت.
if (os.path.abspath(DB_PATH) != os.path.abspath(_OLD_DEFAULT_DB_PATH)
        and not os.path.exists(DB_PATH) and os.path.exists(_OLD_DEFAULT_DB_PATH)):
    try:
        shutil.copy2(_OLD_DEFAULT_DB_PATH, DB_PATH)
    except Exception:
        pass

_lock = threading.Lock()

DEFAULT_SETTINGS: Dict[str, Any] = {
    "scan_interval_seconds": 30,
    "telegram_token": "",
    "telegram_chat_ids": "",
    "telegram_contacts_json": "[]",  # [{"name": "...", "chat_id": "..."}, ...] — المصدر الأصلي، telegram_chat_ids مشتق منه تلقائياً
    "min_probability": 70,
    "is_auto_scanning": 1,
    "is_telegram_enabled": 1,
    "selected_symbols": "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,XRPUSDT,ADAUSDT",
    "is_single_coin_mode_enabled": 0,
    "single_coin_symbol": "BTCUSDT",
    "watchlist_json": '["BTCUSDT"]',  # المصدر الأصلي لقائمة المراقبة، single_coin_symbol مشتق منه تلقائياً
    "symbols_limit": 10,
    "is_volume_filter_enabled": 0,
    "min_volume_ratio": 0.8,
    "is_vwap_filter_enabled": 0,
    "is_4h_buyers_filter_enabled": 0,
    "min_4h_buyers_percentage": 60,
    "is_cancel_if_exceeds_target_enabled": 1,
    "exchange": "binance",  # 'binance' or 'okx' for market data source
    "symbol_selection_mode": "top_volume",  # top_volume / big_movers / high_funding / oi_spike
    "is_auto_backup_enabled": 1,
    "auto_backup_interval_hours": 6,
    "auto_backup_retention_count": 10,
    "gdrive_refresh_token": "",
    "gdrive_folder_id": "",
    "is_gdrive_backup_enabled": 0,
    "active_strategy": "explosive_breakout",  # 'explosive_breakout' أو 'ict_smart_sweep'
    "ict_ignore_kill_zone": 0,  # تجاهل قيد جلسة التداول (Kill Zone) لاستراتيجية ICT — تشغيلها بأي وقت
    "is_efficiency_filter_enabled": 1,  # رفض العملات اللي تتحرك عشوائياً/جانبياً (نسبة الكفاءة الاتجاهية)
    "min_efficiency_ratio": 0.15,  # الحد الأدنى لنسبة الكفاءة الاتجاهية (0-1، كل ما زاد كل ما كان الاتجاه أنظف) — خُفّض من 0.28 بناءً على دليل رفض مفرط فعلي
    "is_market_alignment_filter_enabled": 1,  # رفض أي صفقة تعاكس اتجاه السوق العام (البيتكوين)
    "min_btc_correlation": 0.35,  # الحد الأدنى لمعامل الارتباط بالبيتكوين قبل اعتبار العملة "فكّت الارتباط"
    "is_breakeven_stop_enabled": 1,  # نقل الوقف لنقطة الدخول تلقائياً عند تحقيق ربح 1R
    "min_signal_score": 0,  # الحد الأدنى لنقاط قوة الإشارة (0-100) — 0 يعني بدون فلترة إضافية
    "combined_enabled_strategies": "",  # قائمة مفاتيح استراتيجيات مفصولة بفاصلة تعمل داخل وضع "الكل معاً" — فاضي = الكل مفعّل
    # OKX trading connection
    "okx_api_key": "",
    "okx_api_secret": "",
    "okx_passphrase": "",
    "okx_is_testnet": 1,
    "okx_is_auto_trading_enabled": 0,
    "okx_leverage": 10,
    "okx_is_max_leverage_enabled": 0,           # تفعيل "أقصى رافعة" تلقائية متكيفة لكل عملة
    "okx_margin_mode": "cross",                 # cross أو isolated
    "okx_volume_type": "FIXED",                 # FIXED (مبلغ ثابت) أو PERCENTAGE (نسبة من الرصيد)
    "okx_volume_usdt": 10.0,                    # يُستخدم عند FIXED
    "okx_volume_percent": 5.0,                  # يُستخدم عند PERCENTAGE
    "is_adaptive_stop_loss_enabled": 0,         # استراتيجية التكيف التلقائي (Adaptive Sizing)
    "adaptive_stop_loss_limit_usdt": 1.0,       # أقصى خسارة مستهدفة لكل صفقة بالـ USDT
    "is_instant_entry_enabled": 1,              # أمر سوق فوري (Market) بدل أمر محدد (Limit)
    # محرك التعلم الذاتي (Coin Learning) — يتعلم من سجل الصفقات المغلقة الحقيقي فقط
    "is_coin_learning_enabled": 1,
    "coin_learning_min_trades": 5,       # الحد الأدنى من الصفقات المغلقة قبل ما ناخذ قرار بناءً على الأداء
    "coin_learning_weak_threshold": 35,  # أقل من هذه النسبة % = سجل ضعيف، يرفع شرط الدخول
    "coin_learning_strong_threshold": 70,  # أعلى من هذه النسبة % = سجل قوي، يخفف شرط الدخول قليلاً
    "strategy_learning_min_trades": 10,   # نفس الفكرة لكن على مستوى الاستراتيجية ككل (كل العملات مجتمعة)
    "strategy_learning_weak_threshold": 35,
    "strategy_learning_strong_threshold": 70,
}


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                rr REAL,
                probability INTEGER,
                quality TEXT,
                behavior TEXT,
                volume_analysis TEXT,
                status TEXT,
                update_timestamp INTEGER,
                current_price REAL DEFAULT 0,
                last_notified_status TEXT DEFAULT '',
                strategy TEXT DEFAULT '',
                max_drawdown_pct REAL DEFAULT 0,
                max_favorable_pct REAL DEFAULT 0,
                initial_risk_pct REAL DEFAULT 0,
                breakeven_activated INTEGER DEFAULT 0,
                signal_score REAL DEFAULT 100,
                score_breakdown TEXT DEFAULT '[]'
            )
        """)
        # هجرة آمنة: إضافة عمود strategy لو قاعدة البيانات كانت موجودة قبل هذا التحديث
        try:
            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(trade_signals)").fetchall()}
            if "strategy" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN strategy TEXT DEFAULT ''")
            if "max_drawdown_pct" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN max_drawdown_pct REAL DEFAULT 0")
            if "max_favorable_pct" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN max_favorable_pct REAL DEFAULT 0")
            if "initial_risk_pct" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN initial_risk_pct REAL DEFAULT 0")
            if "breakeven_activated" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN breakeven_activated INTEGER DEFAULT 0")
            if "signal_score" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN signal_score REAL DEFAULT 100")
            if "score_breakdown" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN score_breakdown TEXT DEFAULT '[]'")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER,
                message TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS filter_rejections (
                filter_name TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        # seed defaults if missing
        cur = conn.execute("SELECT key FROM app_settings")
        existing = {row["key"] for row in cur.fetchall()}
        for k, v in DEFAULT_SETTINGS.items():
            if k not in existing:
                conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (k, str(v)))
        conn.commit()


def get_settings() -> Dict[str, Any]:
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT key, value FROM app_settings")
        raw = {row["key"]: row["value"] for row in cur.fetchall()}
    settings = dict(DEFAULT_SETTINGS)
    for k, v in raw.items():
        if k not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[k]
        try:
            if isinstance(default, bool):
                settings[k] = v in ("1", "True", "true")
            elif isinstance(default, int):
                settings[k] = int(float(v))
            elif isinstance(default, float):
                settings[k] = float(v)
            else:
                settings[k] = v
        except Exception:
            settings[k] = default
    # normalize booleans stored as 0/1 ints
    for bkey in ("is_auto_scanning", "is_telegram_enabled", "is_single_coin_mode_enabled",
                 "is_volume_filter_enabled", "is_vwap_filter_enabled", "is_4h_buyers_filter_enabled",
                 "is_cancel_if_exceeds_target_enabled", "okx_is_testnet", "okx_is_auto_trading_enabled",
                 "okx_is_max_leverage_enabled", "is_adaptive_stop_loss_enabled", "is_instant_entry_enabled",
                 "is_coin_learning_enabled", "is_auto_backup_enabled", "is_gdrive_backup_enabled",
                 "ict_ignore_kill_zone", "is_efficiency_filter_enabled", "is_market_alignment_filter_enabled",
                 "is_breakeven_stop_enabled"):
        settings[bkey] = bool(int(settings.get(bkey, 0)))
    return settings


def update_settings(updates: Dict[str, Any]):
    with _lock, _connect() as conn:
        for k, v in updates.items():
            if k not in DEFAULT_SETTINGS:
                continue
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )
        conn.commit()


def add_signal(signal: Dict[str, Any]) -> int:
    import json
    entry_price = signal["entry_price"]
    stop_loss = signal["stop_loss"]
    initial_risk_pct = abs(entry_price - stop_loss) / entry_price * 100.0 if entry_price else 0.0
    signal_score = signal.get("signal_score", 100.0)
    score_breakdown_json = json.dumps(signal.get("score_breakdown") or [], ensure_ascii=False)
    with _lock, _connect() as conn:
        cur = conn.execute("""
            INSERT INTO trade_signals
            (timestamp, symbol, side, entry_price, stop_loss, take_profit, rr, probability,
             quality, behavior, volume_analysis, status, update_timestamp, current_price,
             last_notified_status, strategy, initial_risk_pct, signal_score, score_breakdown)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(time.time() * 1000), signal["symbol"], signal["side"], entry_price,
            stop_loss, signal["take_profit"], signal["rr"], signal["probability"],
            signal["quality"], signal["behavior"], signal["volume_analysis"], "PENDING",
            int(time.time() * 1000), entry_price, "", signal.get("strategy", ""), initial_risk_pct,
            signal_score, score_breakdown_json,
        ))
        conn.commit()
        return cur.lastrowid


def increment_rejection_counter(filter_name: str):
    """يزيد عدّاد رفض فلتر معيّن — يبقى متراكم دائم (مو محدود بعدد صفوف زي سجل
    الفحص)، عشان نقدر نقيس فعلياً كم مرة رفض كل فلتر إشارة، ونعرف هل الحدود
    الحالية متشددة أو متساهلة بناءً على بيانات حقيقية."""
    with _lock, _connect() as conn:
        conn.execute("""
            INSERT INTO filter_rejections (filter_name, count) VALUES (?, 1)
            ON CONFLICT(filter_name) DO UPDATE SET count = count + 1
        """, (filter_name,))
        conn.commit()


def get_rejection_counts() -> Dict[str, int]:
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT filter_name, count FROM filter_rejections ORDER BY count DESC")
        return {row["filter_name"]: row["count"] for row in cur.fetchall()}


def get_signal_stats() -> Dict[str, Any]:
    """إحصائيات دقيقة 100% محسوبة من **كامل** جدول الإشارات مباشرة عبر SQL —
    بدون أي حد أقصى (LIMIT) — بعكس get_signals() اللي مصمم للعرض فقط ومحدود.
    هذا يضمن العدادات صحيحة دائماً بغض النظر عن عدد الصفقات الكلي (حتى لو مليون)،
    لأنها ما تعتمد على جلب كل الصفوف للمتصفح، بس عدّها مباشرة بقاعدة البيانات."""
    with _lock, _connect() as conn:
        cur = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM trade_signals GROUP BY status
        """)
        counts = {row["status"]: row["cnt"] for row in cur.fetchall()}
        total_cur = conn.execute("SELECT COUNT(*) as cnt FROM trade_signals")
        total = total_cur.fetchone()["cnt"]

        # 📊 القياس بنظام R-Multiple بدل النسبة المئوية الخام (بطلب صريح): كل صفقة
        # خاسرة (HIT_SL) = -1R بالتعريف (خسرت بالضبط مقدار مخاطرتها المحسوبة مسبقاً،
        # بافتراض عدم وجود انزلاق سعري كبير). كل صفقة رابحة (HIT_TP) = +RR المخطط لها
        # فعلياً (العمود rr المخزَّن بالصفقة نفسها)، لأنها وصلت الهدف بالضبط عند
        # المستوى المحسوب. هذا معيار احترافي قياسي يقيس الأداء **نسبة لحجم المخاطرة**
        # بدل حجم الحركة الخام بالسعر — يسهّل مقارنة صفقات بعملات مختلفة الأسعار.
        closed_rows = conn.execute("""
            SELECT status, rr FROM trade_signals
            WHERE status IN ('HIT_TP','HIT_SL')
        """).fetchall()

    total_win_r = 0.0
    total_loss_r = 0.0
    for row in closed_rows:
        if row["status"] == "HIT_TP":
            total_win_r += (row["rr"] or 0.0)
        else:
            total_loss_r += 1.0  # كل خسارة = 1R بالتعريف، نجمعها موجبة ونعرضها سالبة لاحقاً

    wins = counts.get("HIT_TP", 0)
    losses = counts.get("HIT_SL", 0)  # خسائر حقيقية فقط — التعادل ما يُحسب هنا
    breakeven = counts.get("BREAKEVEN", 0)
    closed = wins + losses
    return {
        "total": total,
        "active": counts.get("ACTIVE", 0),
        "pending": counts.get("PENDING", 0),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "cancelled": counts.get("CANCELLED", 0) + counts.get("REPLACED", 0),
        "closed_total": closed,
        "win_rate": round((wins / closed) * 100.0, 1) if closed > 0 else 0.0,
        "total_win_r": round(total_win_r, 2),
        "total_loss_r": round(-total_loss_r, 2),  # سالبة دائماً — كل خسارة = -1R بالتعريف
        "net_r": round(total_win_r - total_loss_r, 2),
    }


def get_signals(limit: int = 300) -> List[Dict[str, Any]]:
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT * FROM trade_signals ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cur.fetchall()]


def get_strategy_performance() -> List[Dict[str, Any]]:
    """يقارن أداء كل استراتيجية على حدة (رابحة/خاسرة/تعادل/نسبة نجاح + عائد R) من
    الصفقات المغلقة الحقيقية فقط. صفقات التعادل (BREAKEVEN) مستبعدة من حساب نسبة
    النجاح لأنها مو فوز ولا خسارة حقيقية بالتحليل — بس وقاية حمت رأس المال."""
    with _lock, _connect() as conn:
        cur = conn.execute("""
            SELECT COALESCE(NULLIF(strategy,''), 'غير محدد') AS strategy,
                   SUM(CASE WHEN status='HIT_TP' THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN status='HIT_SL' THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN status='BREAKEVEN' THEN 1 ELSE 0 END) AS breakeven,
                   SUM(CASE WHEN status='HIT_TP' THEN rr ELSE 0 END) AS total_win_r,
                   COUNT(*) AS total_all_statuses
            FROM trade_signals
            GROUP BY strategy
            ORDER BY total_all_statuses DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        closed = r["wins"] + r["losses"]
        r["closed_total"] = closed
        r["win_rate"] = round((r["wins"] / closed) * 100.0, 1) if closed > 0 else 0.0
        # عائد R: كل خسارة = -1R بالتعريف (مقدار مخاطرتها بالضبط)، وكل ربح = عائد/مخاطرة
        # (rr) المخطط له فعلياً — معيار احترافي يقيس الأداء نسبة لحجم المخاطرة
        total_win_r = round(r["total_win_r"] or 0.0, 2)
        total_loss_r = r["losses"] * 1.0
        r["total_win_r"] = total_win_r
        r["total_loss_r"] = round(-total_loss_r, 2)
        r["net_r"] = round(total_win_r - total_loss_r, 2)
        r["loss_to_win_ratio"] = (round(total_loss_r / total_win_r, 2) if total_win_r > 0 else None)
    return rows


def get_coin_performance(limit: int = 200) -> List[Dict[str, Any]]:
    """يحسب أداء كل عملة+اتجاه من الصفقات المغلقة فعلياً (HIT_TP/HIT_SL) — هذا هو
    'ذاكرة' محرك التعلم الذاتي، مبني على نتائج حقيقية وليس تخمين."""
    with _lock, _connect() as conn:
        cur = conn.execute("""
            SELECT symbol, side,
                   SUM(CASE WHEN status='HIT_TP' THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN status='HIT_SL' THEN 1 ELSE 0 END) AS losses
            FROM trade_signals
            WHERE status IN ('HIT_TP','HIT_SL')
            GROUP BY symbol, side
            ORDER BY (wins + losses) DESC
            LIMIT ?
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        total = r["wins"] + r["losses"]
        r["total"] = total
        r["win_rate"] = round((r["wins"] / total) * 100.0, 1) if total > 0 else 0.0
    return rows


def get_coin_performance_for(symbol: str, side: str) -> Optional[Dict[str, Any]]:
    with _lock, _connect() as conn:
        cur = conn.execute("""
            SELECT
                SUM(CASE WHEN status='HIT_TP' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN status='HIT_SL' THEN 1 ELSE 0 END) AS losses
            FROM trade_signals
            WHERE status IN ('HIT_TP','HIT_SL') AND symbol=? AND side=?
        """, (symbol, side))
        row = cur.fetchone()
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    total = wins + losses
    if total == 0:
        return None
    return {"symbol": symbol, "side": side, "wins": wins, "losses": losses,
            "total": total, "win_rate": round((wins / total) * 100.0, 1)}


def get_recent_similar_signal(symbol: str, side: str, strategy: str, entry_price: float,
                               tolerance_pct: float = 0.002, since_hours: int = 6) -> Optional[Dict[str, Any]]:
    """يكشف لو نفس النمط (نفس الرمز/الاتجاه/الاستراتيجية/سعر دخول قريب جداً) تكرر بآخر
    عدة ساعات — حتى لو الإشارة السابقة أُغلقت (رابحة أو خاسرة). هذا يمنع مشكلة حقيقية:
    بعض الاستراتيجيات (خصوصاً صيد الاستوبات) تعتمد على شموع فريم أعلى (ساعة مثلاً) ما
    تتغير كل دقيقة، فتكتشف نفس النمط التاريخي مرة ثانية فوراً بعد إغلاق الصفقة السابقة،
    وتفتح صفقة "جديدة" بنفس السعر بالضبط تكراراً — رغم إنها فعلياً نفس القرار الفاشل يتكرر."""
    since_ts = int(time.time() * 1000) - (since_hours * 60 * 60 * 1000)
    with _lock, _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM trade_signals WHERE symbol=? AND side=? AND strategy=? AND timestamp>=? "
            "ORDER BY id DESC LIMIT 10",
            (symbol, side, strategy, since_ts),
        )
        rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        prev_entry = row.get("entry_price") or 0
        if prev_entry > 0 and abs(entry_price - prev_entry) / prev_entry <= tolerance_pct:
            return row
    return None


def get_active_or_pending_signal(symbol: str, side: str, strategy: str = "") -> Optional[Dict[str, Any]]:
    """يتحقق من وجود صفقة بنفس الرمز والاتجاه **ونفس الاستراتيجية** حالتها PENDING
    أو ACTIVE، لمنع تكرار نفس الإشارة من نفس الاستراتيجية فقط. هذا يسمح لاستراتيجيات
    مختلفة إنها تفتح صفقات مستقلة على نفس العملة بنفس الوقت — مفيد لمقارنة أداء
    الاستراتيجيات ببعض على نفس ظروف السوق الحقيقية، بدل ما وحدة تمنع البقية."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM trade_signals WHERE symbol=? AND side=? AND strategy=? AND status IN ('PENDING','ACTIVE') "
            "ORDER BY id DESC LIMIT 1",
            (symbol, side, strategy),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_open_signals() -> List[Dict[str, Any]]:
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT * FROM trade_signals WHERE status IN ('PENDING','ACTIVE')")
        return [dict(row) for row in cur.fetchall()]


def update_signal_status(signal_id: int, status: str, current_price: float, last_notified_status: str):
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE trade_signals SET status=?, current_price=?, update_timestamp=?, last_notified_status=? WHERE id=?",
            (status, current_price, int(time.time() * 1000), last_notified_status, signal_id),
        )
        conn.commit()


def update_max_drawdown_if_worse(signal_id: int, drawdown_pct: float):
    """يحدّث أقصى تراجع سعري من نقطة الدخول **فقط لو الرقم الجديد أسوأ** من المسجَّل
    حالياً — بهذا يبقى العمود دائماً "أسوأ نقطة وصلها السعر ضد الصفقة" طول عمرها،
    مفيد لقياس قوة نقطة الدخول فعلياً (مو بس هل ربحت أو خسرت بالنهاية)."""
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT max_drawdown_pct FROM trade_signals WHERE id=?", (signal_id,))
        row = cur.fetchone()
        if row is None:
            return
        current_max = row["max_drawdown_pct"] or 0.0
        if drawdown_pct > current_max:
            conn.execute("UPDATE trade_signals SET max_drawdown_pct=? WHERE id=?", (drawdown_pct, signal_id))
            conn.commit()


def activate_breakeven(signal_id: int, new_stop_loss: float):
    """ينقل وقف الخسارة لنقطة الدخول (أو قريب منها) بمجرد ما الصفقة تحقق ربح عائم
    يعادل مخاطرتها الأصلية (1R) — يحوّل أي انعكاس لاحق من 'خسارة كاملة' إلى 'تعادل
    تقريبي' بدل ما يخسر كامل الوقف الأصلي. هذا إصلاح مباشر لنمط اكتُشف فعلياً
    بسجل الصفقات: نسبة كبيرة من الخسائر كانت أصلاً صفقات رابحة قبل ما تنعكس."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE trade_signals SET stop_loss=?, breakeven_activated=1 WHERE id=?",
            (new_stop_loss, signal_id),
        )
        conn.commit()


def update_max_favorable_if_better(signal_id: int, favorable_pct: float):
    """المرآة العكسية لأقصى تراجع — يسجّل **أعلى مستوى ربح عائم** وصلته الصفقة قبل
    أي انعكاس، حتى لو انتهت لاحقاً بضرب وقف الخسارة. يجاوب سؤال: 'هل الاتجاه كان
    صحيحاً فعلاً وبس الهدف كان بعيد/الوقف قريب؟' أو 'الاتجاه كان غلط من الأساس؟' —
    فرق جوهري لتحسين اختيار نقاط الدخول والخروج مستقبلاً."""
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT max_favorable_pct FROM trade_signals WHERE id=?", (signal_id,))
        row = cur.fetchone()
        if row is None:
            return
        current_max = row["max_favorable_pct"] or 0.0
        if favorable_pct > current_max:
            conn.execute("UPDATE trade_signals SET max_favorable_pct=? WHERE id=?", (favorable_pct, signal_id))
            conn.commit()


def clear_signals():
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM trade_signals")
        conn.commit()


def get_telegram_contacts() -> List[Dict[str, str]]:
    s = get_settings()
    try:
        return json.loads(s.get("telegram_contacts_json") or "[]")
    except Exception:
        return []


def add_telegram_contact(name: str, chat_id: str):
    contacts = get_telegram_contacts()
    chat_id = chat_id.strip()
    if not any(c["chat_id"] == chat_id for c in contacts):
        contacts.append({"name": name.strip() or chat_id, "chat_id": chat_id})
    _save_telegram_contacts(contacts)
    return contacts


def remove_telegram_contact(chat_id: str):
    contacts = [c for c in get_telegram_contacts() if c["chat_id"] != chat_id]
    _save_telegram_contacts(contacts)
    return contacts


def _save_telegram_contacts(contacts: List[Dict[str, str]]):
    update_settings({
        "telegram_contacts_json": json.dumps(contacts, ensure_ascii=False),
        "telegram_chat_ids": ",".join(c["chat_id"] for c in contacts),
    })


def get_watchlist() -> List[str]:
    s = get_settings()
    try:
        return json.loads(s.get("watchlist_json") or "[]")
    except Exception:
        return []


def add_watchlist_symbol(symbol: str):
    symbol = symbol.strip().upper()
    if symbol and not symbol.endswith(("USDT", "BUSD")):
        symbol += "USDT"
    watchlist = get_watchlist()
    if symbol and symbol not in watchlist:
        watchlist.append(symbol)
    _save_watchlist(watchlist)
    return watchlist


def remove_watchlist_symbol(symbol: str):
    watchlist = [s for s in get_watchlist() if s != symbol]
    _save_watchlist(watchlist)
    return watchlist


def _save_watchlist(watchlist: List[str]):
    update_settings({
        "watchlist_json": json.dumps(watchlist, ensure_ascii=False),
        "single_coin_symbol": ",".join(watchlist),
    })


def export_backup() -> Dict[str, Any]:
    """يصدّر نسخة احتياطية كاملة (كل الإعدادات + كل الإشارات المسجّلة) بصيغة JSON قابلة
    للحفظ محلياً واستعادتها لاحقاً — طبقة أمان مستقلة تماماً عن ملف قاعدة البيانات نفسه."""
    with _lock, _connect() as conn:
        settings_rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        signal_rows = conn.execute("SELECT * FROM trade_signals ORDER BY id").fetchall()
    return {
        "backup_version": 1,
        "exported_at": int(time.time() * 1000),
        "settings": {r["key"]: r["value"] for r in settings_rows},
        "signals": [dict(r) for r in signal_rows],
    }


def import_backup(data: Dict[str, Any], mode: str = "merge") -> Dict[str, Any]:
    """يستعيد نسخة احتياطية. mode='merge' يضيف الإشارات الناقصة فقط (بدون تكرار حسب id)
    ويحدّث الإعدادات، mode='replace' يمسح كل شي حالي ويستبدله بالكامل بمحتوى النسخة."""
    settings_data = data.get("settings", {})
    signals_data = data.get("signals", [])

    with _lock, _connect() as conn:
        if mode == "replace":
            conn.execute("DELETE FROM trade_signals")
            conn.execute("DELETE FROM app_settings")

        for key, value in settings_data.items():
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

        restored = 0
        skipped = 0
        cols = ["timestamp", "symbol", "side", "entry_price", "stop_loss", "take_profit", "rr",
                "probability", "quality", "behavior", "volume_analysis", "status",
                "update_timestamp", "current_price", "last_notified_status", "strategy"]
        for sig in signals_data:
            if mode == "merge":
                existing = conn.execute(
                    "SELECT id FROM trade_signals WHERE symbol=? AND timestamp=? AND side=?",
                    (sig.get("symbol"), sig.get("timestamp"), sig.get("side")),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue
            placeholders = ",".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO trade_signals ({','.join(cols)}) VALUES ({placeholders})",
                tuple(sig.get(c, "" if c in ("symbol", "side", "quality", "behavior", "volume_analysis",
                                              "status", "last_notified_status", "strategy") else 0) for c in cols),
            )
            restored += 1
        conn.commit()

    return {"restored_signals": restored, "skipped_duplicates": skipped, "settings_restored": len(settings_data)}


def add_log(message: str, max_logs: int = 300):
    with _lock, _connect() as conn:
        conn.execute("INSERT INTO scan_logs (timestamp, message) VALUES (?, ?)", (int(time.time() * 1000), message))
        conn.execute("""
            DELETE FROM scan_logs WHERE id NOT IN (
                SELECT id FROM scan_logs ORDER BY id DESC LIMIT ?
            )
        """, (max_logs,))
        conn.commit()


def get_logs(limit: int = 200) -> List[Dict[str, Any]]:
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT * FROM scan_logs ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(row) for row in cur.fetchall()]
        return list(reversed(rows))


def clear_logs():
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM scan_logs")
        conn.commit()
