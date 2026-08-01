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
    "active_strategy": "explosive_breakout",
    "is_efficiency_filter_enabled": 1,  # رفض العملات اللي تتحرك عشوائياً/جانبياً (نسبة الكفاءة الاتجاهية)
    "min_efficiency_ratio": 0.15,  # الحد الأدنى لنسبة الكفاءة الاتجاهية (0-1، كل ما زاد كل ما كان الاتجاه أنظف) — خُفّض من 0.28 بناءً على دليل رفض مفرط فعلي
    "is_market_alignment_filter_enabled": 1,  # رفض أي صفقة تعاكس اتجاه السوق العام (البيتكوين)
    "min_btc_correlation": 0.35,  # الحد الأدنى لمعامل الارتباط بالبيتكوين قبل اعتبار العملة "فكّت الارتباط"
    "is_breakeven_stop_enabled": 1,  # نقل الوقف لنقطة الدخول تلقائياً عند تحقيق ربح 1R
    "min_signal_score": 0,  # الحد الأدنى لنقاط قوة الإشارة (0-100) — 0 يعني بدون فلترة إضافية
    "is_market_regime_filter_enabled": 0,  # رفض الصفقات المعاكسة لترند سوق عام ضعيف/متذبذب (فلتر اختياري، مو بس تعزيز)
    "min_market_regime_er": 0.3,  # الحد الأدنى لكفاءة نظام السوق العام قبل قبول أي صفقة (يُستخدم بس لو الفلتر أعلاه مفعّل)
    "is_reverse_mode_enabled": 0,  # وضع الاختبار العكسي: يقلب كل إشارة (Long↔Short) بنفس مسافات الوقف/الهدف لمقارنة الأداء
    "breakeven_trigger_r_multiple": 1.0,  # نسبة المخاطرة (R) المطلوبة لتفعيل وقف التعادل — تُستخدم بس لو الوضع التلقائي أدناه مُلغى
    "is_auto_breakeven_half_target_enabled": 1,  # الافتراضي: تفعيل تلقائي لوقف التعادل عند نصف عائد/مخاطرة الصفقة نفسها
    "is_split_targets_enabled": 0,  # تقسيم الهدف لهدفين: نصف الكمية عند نصف المسافة، والنصف الثاني عند الهدف الكامل
    "is_fixed_rr_enabled": 0,  # 🆕 فرض عائد/مخاطرة ثابت على كل صفقة (يتجاوز هدف الاستراتيجية المحسوب)
    "fixed_rr_value": 3.0,  # 🆕 قيمة R الثابتة المفروضة (لو الإعداد أعلاه مفعّل) - الهدف = الدخول ± (المخاطرة × هذا الرقم)
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
    "coin_strategy_learning_min_trades": 8,  # 🆕 الحد الأدنى لتعلّم تركيبة (عملة+استراتيجية) تحديداً — أعلى من العام لأنه أدق ونحتاج ثقة أكبر
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
                score_breakdown TEXT DEFAULT '[]',
                tp1_price REAL DEFAULT 0,
                tp1_hit INTEGER DEFAULT 0,
                split_targets_used INTEGER DEFAULT 0,
                actual_r_achieved REAL DEFAULT NULL,
                partial_r_banked REAL DEFAULT 0
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
            if "tp1_price" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN tp1_price REAL DEFAULT 0")
            if "tp1_hit" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN tp1_hit INTEGER DEFAULT 0")
            if "split_targets_used" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN split_targets_used INTEGER DEFAULT 0")
            if "actual_r_achieved" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN actual_r_achieved REAL DEFAULT NULL")
            if "partial_r_banked" not in existing_cols:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN partial_r_banked REAL DEFAULT 0")
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
        # 🆕 جدول تخزين الشموع التراكمي (بطلب صريح): بدل إعادة جلب 1000 شمعة من
        # الصفر كل دورة فحص (بطيء وضغط كبير على المنصة)، نبني الأرشيف "طوبة فوق
        # طوبة" — أول جلب يخزّن الألف شمعة كاملة، وكل دورة لاحقة تجلب بس الشموع
        # الجديدة وتضيفها، مع تقليم القديم للحفاظ على نافذة ثابتة. كل (عملة+فريم)
        # مستقلة تماماً عن الباقي.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candle_cache (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                close_time INTEGER NOT NULL,
                PRIMARY KEY (symbol, timeframe, open_time)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candle_cache_lookup ON candle_cache(symbol, timeframe, open_time DESC)")
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
                 "is_efficiency_filter_enabled", "is_market_alignment_filter_enabled",
                 "is_breakeven_stop_enabled", "is_auto_breakeven_half_target_enabled",
                 "is_split_targets_enabled", "is_market_regime_filter_enabled", "is_reverse_mode_enabled", "is_fixed_rr_enabled"):
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
    take_profit = signal["take_profit"]
    initial_risk_pct = abs(entry_price - stop_loss) / entry_price * 100.0 if entry_price else 0.0
    signal_score = signal.get("signal_score", 100.0)
    score_breakdown_json = json.dumps(signal.get("score_breakdown") or [], ensure_ascii=False)

    split_enabled = bool(signal.get("split_targets_used", False))
    tp1_price = (entry_price + (take_profit - entry_price) * 0.5) if split_enabled else 0.0

    with _lock, _connect() as conn:
        cur = conn.execute("""
            INSERT INTO trade_signals
            (timestamp, symbol, side, entry_price, stop_loss, take_profit, rr, probability,
             quality, behavior, volume_analysis, status, update_timestamp, current_price,
             last_notified_status, strategy, initial_risk_pct, signal_score, score_breakdown,
             tp1_price, split_targets_used)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(time.time() * 1000), signal["symbol"], signal["side"], entry_price,
            stop_loss, take_profit, signal["rr"], signal["probability"],
            signal["quality"], signal["behavior"], signal["volume_analysis"], "PENDING",
            int(time.time() * 1000), entry_price, "", signal.get("strategy", ""), initial_risk_pct,
            signal_score, score_breakdown_json, tp1_price, int(split_enabled),
        ))
        conn.commit()
        return cur.lastrowid


def mark_tp1_hit(signal_id: int, partial_r: float = 0.0):
    """يسجّل إن الهدف الأول تحقق (نصف الكمية خرجت بربح) — الصفقة تبقى نشطة
    وتُتابَع لبقية الكمية نحو الهدف الثاني أو وقف الخسارة. يخزّن مقدار العائد
    الجزئي المكتسب فوراً (partial_r) عشان يظهر بالإحصائيات العامة **مباشرة**،
    بدون انتظار إغلاق الصفقة نهائياً — هذا الجزء ربح محقَّق فعلاً."""
    with _lock, _connect() as conn:
        conn.execute("UPDATE trade_signals SET tp1_hit=1, partial_r_banked=? WHERE id=?",
                     (partial_r, signal_id))
        conn.commit()


def close_signal_with_actual_r(signal_id: int, status: str, current_price: float,
                                actual_r: float, last_notified_status: str):
    """يغلق صفقة مقسّمة الأهداف بالعائد R الفعلي المحسوب (يجمع مساهمة الهدف الأول
    المتحقق + نتيجة بقية الكمية)، بدل الافتراض العام (رابح=rr كامل / خاسر=-1R)."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE trade_signals SET status=?, current_price=?, update_timestamp=?, "
            "last_notified_status=?, actual_r_achieved=? WHERE id=?",
            (status, current_price, int(time.time() * 1000), last_notified_status, actual_r, signal_id),
        )
        conn.commit()


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


def get_cached_candles(symbol: str, timeframe: str) -> List[Dict[str, Any]]:
    """🆕 يجيب كل الشموع المخزَّنة لهذي التركيبة (عملة+فريم)، مرتبة زمنياً تصاعدياً
    (الأقدم أول). ترجع قائمة قواميس (مو كائنات Kline مباشرة، عشان db.py ما يعتمد
    على analyzer.py) — المستدعي (okx_client أو scanner) يحوّلها لـKline عند الحاجة."""
    with _lock, _connect() as conn:
        cur = conn.execute("""
            SELECT open_time, open, high, low, close, volume, close_time
            FROM candle_cache WHERE symbol=? AND timeframe=? ORDER BY open_time ASC
        """, (symbol, timeframe))
        return [dict(row) for row in cur.fetchall()]


def save_candles(symbol: str, timeframe: str, candles: List[Dict[str, Any]], keep_latest: int = 1200):
    """🆕 يخزّن شموع جديدة (أو يحدّث الموجود لو نفس open_time — مفيد للشمعة اللي
    كانت 'حيّة' وقت آخر حفظ وصارت مؤكَّدة الآن)، ثم يقلّم الأرشيف للاحتفاظ بس
    بآخر keep_latest شمعة — نافذة متحركة ثابتة الحجم، مو نمو غير محدود بالتخزين."""
    if not candles:
        return
    with _lock, _connect() as conn:
        conn.executemany("""
            INSERT INTO candle_cache (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe, open_time) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume, close_time=excluded.close_time
        """, [(symbol, timeframe, c["open_time"], c["open"], c["high"], c["low"], c["close"], c["volume"], c["close_time"]) for c in candles])

        # تقليم: نحتفظ بس بأحدث keep_latest شمعة، نحذف الباقي
        conn.execute("""
            DELETE FROM candle_cache WHERE symbol=? AND timeframe=? AND open_time NOT IN (
                SELECT open_time FROM candle_cache WHERE symbol=? AND timeframe=?
                ORDER BY open_time DESC LIMIT ?
            )
        """, (symbol, timeframe, symbol, timeframe, keep_latest))
        conn.commit()


def get_latest_cached_open_time(symbol: str, timeframe: str) -> Optional[int]:
    """🆕 يرجع أحدث open_time مخزَّن لهذي التركيبة، أو None لو ما فيه أرشيف بعد
    (يحدد هل نسوي 'أول جلب كامل' أو 'جلب تراكمي للجديد بس')."""
    with _lock, _connect() as conn:
        cur = conn.execute("""
            SELECT MAX(open_time) as latest FROM candle_cache WHERE symbol=? AND timeframe=?
        """, (symbol, timeframe))
        row = cur.fetchone()
        return row["latest"] if row and row["latest"] is not None else None


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
            SELECT status, rr, actual_r_achieved FROM trade_signals
            WHERE status IN ('HIT_TP','HIT_SL')
        """).fetchall()
        # 📊 صفقات لسا نشطة لكن تحقق لها الهدف الأول (تقسيم الأهداف) — نضيف
        # مساهمتها الجزئية المكتسبة فوراً للعدادات العامة، بدون انتظار الإغلاق
        # النهائي، لأن هذا الجزء ربح محقَّق فعلاً (نصف الكمية خرجت بالفعل)
        active_partial_rows = conn.execute("""
            SELECT partial_r_banked FROM trade_signals
            WHERE status = 'ACTIVE' AND tp1_hit = 1
        """).fetchall()

    total_win_r = 0.0
    total_loss_r = 0.0
    for row in closed_rows:
        actual_r = row["actual_r_achieved"]
        if actual_r is not None:
            # صفقة مقسّمة الأهداف — نستخدم العائد الفعلي المحسوب (يجمع مساهمة كل جزء)
            if actual_r >= 0:
                total_win_r += actual_r
            else:
                total_loss_r += -actual_r
        elif row["status"] == "HIT_TP":
            total_win_r += (row["rr"] or 0.0)
        else:
            total_loss_r += 1.0  # كل خسارة = 1R بالتعريف، نجمعها موجبة ونعرضها سالبة لاحقاً

    for row in active_partial_rows:
        total_win_r += (row["partial_r_banked"] or 0.0)

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


def get_probability_calibration() -> List[Dict[str, Any]]:
    """يقارن 'الاحتمالية المعلنة' وقت إنشاء كل صفقة بنسبة النجاح **الحقيقية** الفعلية
    لكل فئة (70-75%، 76-80%...) ولكل استراتيجية — يكشف هل رقم الثقة المعروض له
    معنى حقيقي، أو مجرد نقاط نظرية بدون علاقة فعلية بالنتيجة. لو نسبة النجاح
    الحقيقية أقل بكثير من الفئة المعلنة (أو ما تتزايد مع زيادة الفئة)، هذا دليل
    إن صيغة حساب الاحتمالية بهذي الاستراتيجية تحتاج مراجعة جذرية، مو بس تعديل رقم."""
    with _lock, _connect() as conn:
        rows = conn.execute("""
            SELECT strategy, probability, status FROM trade_signals
            WHERE status IN ('HIT_TP','HIT_SL')
        """).fetchall()

    buckets_def = [(70, 75), (76, 80), (81, 85), (86, 90), (91, 100)]
    by_strategy: Dict[str, Dict] = {}
    for row in rows:
        strat = row["strategy"] or "غير محدد"
        prob = row["probability"] or 0
        bucket_label = None
        for lo, hi in buckets_def:
            if lo <= prob <= hi:
                bucket_label = f"{lo}-{hi}%"
                break
        if bucket_label is None:
            continue
        by_strategy.setdefault(strat, {})
        by_strategy[strat].setdefault(bucket_label, {"total": 0, "wins": 0})
        by_strategy[strat][bucket_label]["total"] += 1
        if row["status"] == "HIT_TP":
            by_strategy[strat][bucket_label]["wins"] += 1

    result = []
    for strat, buckets in by_strategy.items():
        bucket_list = []
        for lo, hi in buckets_def:
            label = f"{lo}-{hi}%"
            if label in buckets:
                total = buckets[label]["total"]
                wins = buckets[label]["wins"]
                bucket_list.append({
                    "declared_range": label,
                    "total_trades": total,
                    "actual_win_rate": round((wins / total) * 100.0, 1) if total > 0 else None,
                })
        result.append({"strategy": strat, "buckets": bucket_list})
    return result


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
                   COUNT(*) AS total_all_statuses
            FROM trade_signals
            GROUP BY strategy
            ORDER BY total_all_statuses DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        r_rows = conn.execute("""
            SELECT COALESCE(NULLIF(strategy,''), 'غير محدد') AS strategy,
                   status, rr, actual_r_achieved
            FROM trade_signals WHERE status IN ('HIT_TP','HIT_SL')
        """).fetchall()
        active_partial_rows = conn.execute("""
            SELECT COALESCE(NULLIF(strategy,''), 'غير محدد') AS strategy, partial_r_banked
            FROM trade_signals WHERE status = 'ACTIVE' AND tp1_hit = 1
        """).fetchall()

    # عائد R: كل خسارة = -1R بالتعريف (مقدار مخاطرتها بالضبط)، وكل ربح = عائد/مخاطرة
    # (rr) المخطط له فعلياً — إلا لو الصفقة مقسّمة الأهداف، فنستخدم العائد الفعلي
    # المحسوب (actual_r_achieved) اللي يجمع مساهمة كل جزء من الصفقة بدقة
    r_by_strategy: Dict[str, Dict[str, float]] = {}
    for row in r_rows:
        strat = row["strategy"]
        r_by_strategy.setdefault(strat, {"win_r": 0.0, "loss_r": 0.0})
        actual_r = row["actual_r_achieved"]
        if actual_r is not None:
            if actual_r >= 0:
                r_by_strategy[strat]["win_r"] += actual_r
            else:
                r_by_strategy[strat]["loss_r"] += -actual_r
        elif row["status"] == "HIT_TP":
            r_by_strategy[strat]["win_r"] += (row["rr"] or 0.0)
        else:
            r_by_strategy[strat]["loss_r"] += 1.0

    for row in active_partial_rows:
        strat = row["strategy"]
        r_by_strategy.setdefault(strat, {"win_r": 0.0, "loss_r": 0.0})
        r_by_strategy[strat]["win_r"] += (row["partial_r_banked"] or 0.0)

    for r in rows:
        closed = r["wins"] + r["losses"]
        r["closed_total"] = closed
        r["win_rate"] = round((r["wins"] / closed) * 100.0, 1) if closed > 0 else 0.0
        rvals = r_by_strategy.get(r["strategy"], {"win_r": 0.0, "loss_r": 0.0})
        total_win_r = round(rvals["win_r"], 2)
        total_loss_r = round(rvals["loss_r"], 2)
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


def get_coin_strategy_performance_for(symbol: str, strategy_key: str) -> Optional[Dict[str, Any]]:
    """🆕 يجيب أداء "هذي الاستراتيجية بالذات على هذي العملة بالذات" — مختلف عن
    get_coin_performance_for (اللي يجمع كل الاستراتيجيات على عملة معينة) وعن
    get_strategy_performance (اللي يجمع كل العملات لاستراتيجية معينة). هذا يسد
    فجوة حقيقية: استراتيجية قوية عموماً ممكن تكون ضعيفة تحديداً على عملة معينة
    (والعكس)، وهذا مو مكتشف بمستوى "العملة عموماً" ولا "الاستراتيجية عموماً"."""
    with _lock, _connect() as conn:
        cur = conn.execute("""
            SELECT
                SUM(CASE WHEN status='HIT_TP' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN status='HIT_SL' THEN 1 ELSE 0 END) AS losses
            FROM trade_signals
            WHERE status IN ('HIT_TP','HIT_SL') AND symbol=? AND strategy=?
        """, (symbol, strategy_key))
        row = cur.fetchone()
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    total = wins + losses
    if total == 0:
        return None
    return {"symbol": symbol, "strategy": strategy_key, "wins": wins, "losses": losses,
            "total": total, "win_rate": round((wins / total) * 100.0, 1)}


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
    """تصفير شامل حقيقي — بطلب صريح: يمسح الصفقات **وكل** البيانات المرتبطة
    (عدادات رفض الفلاتر، سجل الفحص) عشان البدء من صفر نظيف فعلاً، بدون أي بقايا
    بيانات قديمة تخلط حسابات المقارنة (زي عدادات رفض متراكمة من فترة سابقة)."""
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM trade_signals")
        conn.execute("DELETE FROM filter_rejections")
        conn.execute("DELETE FROM scan_logs")
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
