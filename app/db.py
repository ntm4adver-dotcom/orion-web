"""طبقة قاعدة بيانات SQLite بسيطة — تعادل Room DB (AppSettings, TradeSignal) في التطبيق الأصلي."""
import os
import sqlite3
import time
import threading
from typing import Optional, List, Dict, Any

DB_PATH = os.environ.get("ORION_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "orion.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_lock = threading.Lock()

DEFAULT_SETTINGS: Dict[str, Any] = {
    "scan_interval_seconds": 30,
    "telegram_token": "",
    "telegram_chat_ids": "",
    "min_probability": 70,
    "is_auto_scanning": 1,
    "is_telegram_enabled": 1,
    "selected_symbols": "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,XRPUSDT,ADAUSDT",
    "is_single_coin_mode_enabled": 0,
    "single_coin_symbol": "BTCUSDT",
    "symbols_limit": 10,
    "is_volume_filter_enabled": 0,
    "min_volume_ratio": 0.8,
    "is_vwap_filter_enabled": 0,
    "is_4h_buyers_filter_enabled": 0,
    "min_4h_buyers_percentage": 60,
    "is_cancel_if_exceeds_target_enabled": 1,
    "exchange": "binance",  # 'binance' or 'okx' for market data source
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
                last_notified_status TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER,
                message TEXT
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
                 "is_coin_learning_enabled"):
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
    with _lock, _connect() as conn:
        cur = conn.execute("""
            INSERT INTO trade_signals
            (timestamp, symbol, side, entry_price, stop_loss, take_profit, rr, probability,
             quality, behavior, volume_analysis, status, update_timestamp, current_price, last_notified_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(time.time() * 1000), signal["symbol"], signal["side"], signal["entry_price"],
            signal["stop_loss"], signal["take_profit"], signal["rr"], signal["probability"],
            signal["quality"], signal["behavior"], signal["volume_analysis"], "PENDING",
            int(time.time() * 1000), signal["entry_price"], "",
        ))
        conn.commit()
        return cur.lastrowid


def get_signals(limit: int = 100) -> List[Dict[str, Any]]:
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT * FROM trade_signals ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cur.fetchall()]


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


def get_active_or_pending_signal(symbol: str, side: str) -> Optional[Dict[str, Any]]:
    """يعادل signalDao.getActiveOrPendingSignal الأصلي — يتحقق من وجود صفقة بنفس
    الرمز والاتجاه حالتها PENDING أو ACTIVE، لمنع تكرار نفس الإشارة."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM trade_signals WHERE symbol=? AND side=? AND status IN ('PENDING','ACTIVE') "
            "ORDER BY id DESC LIMIT 1",
            (symbol, side),
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


def clear_signals():
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM trade_signals")
        conn.commit()


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
