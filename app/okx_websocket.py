"""
عميل WebSocket لمنصة OKX — بديل عن السحب المتكرر (REST Polling) لجزئين
محدَّدين تحديداً (بطلب صريح بعد توضيح المخاطر):

  1) OKXPublicStream: شموع حية لحظية — تُستخدم لتغذية الشارت (`/chart`) ببيانات
     لحظية حقيقية بدل استعلام كل 20 ثانية.
  2) OKXPrivateStream: تحديثات صفقات/مراكز حقيقية لحظية من OKX نفسها — إشعار
     فوري لحظة تنفيذ وقف/هدف على المنصة الحقيقية، بدل انتظار دورة فحص كاملة.

⚠️ عمداً: محرك القرار (الاستراتيجيات + الفحص الدوري) **ما تغيّر ولا اتلمس** —
يبقى يعتمد على السحب كل 5 دقايق (`scan_interval_seconds`) زي ما هو مُختبر طوال
هذي الجلسة. هذا الملف بنية تحتية إضافية بس، مو بديل عن محرك الفحص.

يتطلب مكتبة `websocket-client` (pip install websocket-client) — غير مثبَّتة
بالبيئة اللي بنيت فيها الكود (بدون إنترنت هنا)، لازم تثبّتها بمشروعك الحقيقي.
"""
import json
import threading
import time
import hmac
import hashlib
import base64
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

try:
    import websocket  # websocket-client
except ImportError:
    websocket = None  # يُفحص عند الاستخدام الفعلي، عشان الاستيراد ما يفشّل كل التطبيق

from .analyzer import Kline

PUBLIC_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
PRIVATE_WS_URL = "wss://ws.okx.com:8443/ws/v5/private"

_BAR_MAP = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}


def _to_inst_id(symbol: str) -> str:
    """نفس منطق التحويل المستخدم بـ okx_client.py (BTCUSDT -> BTC-USDT-SWAP)."""
    base = symbol.upper().replace("USDT", "").replace("-", "")
    return f"{base}-USDT-SWAP"


class LiveKlineFeed:
    """🆕 مدير البث الحي المركزي — بديل عن تكرار طلب REST كل دورة فحص (بطلب
    صريح: "كل شي حي زي المنصة، الفاحص بس يروح يحلل من البيانات الجاهزة").

    الفكرة: بث WebSocket واحد يغذّي **نفس أرشيف قاعدة البيانات** (`candle_cache`)
    المستخدَم أصلاً بكل مكان بالتطبيق (فحص، شارت، باك-تست) — مو ذاكرة منفصلة.
    كل ما توصل شمعة **مغلقة فعلياً** من البث الحي، تُحفَظ فوراً بالأرشيف. الفاحص
    (`_fetch_klines_cached`) يتحقق أول شي: "هل البث الحي غذّى هذي التركيبة
    (عملة+فريم) حديثاً؟" — لو نعم، يقرأ من الأرشيف مباشرة بدون أي طلب REST
    جديد. لو لأ (بث منقطع، أو تركيبة جديدة لسا ما اشتُرك فيها)، يرجع تلقائياً
    لطلب REST القديم كشبكة أمان — عشان قرار تداول حقيقي ما يعتمد على بيانات
    قديمة لو الاتصال الحي فشل مؤقتاً."""

    def __init__(self, db_module):
        self.db = db_module
        self.stream: Optional[OKXPublicStream] = None
        self._subscribed: set = set()  # {(symbol, interval)}
        self._last_update_ts: Dict[Tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def start(self):
        if self.stream is not None:
            return
        try:
            self.stream = OKXPublicStream(self._on_update)
            self.stream.start()
        except RuntimeError:
            self.stream = None  # مكتبة websocket-client غير مثبَّتة — يبقى الفاحص يعتمد على REST بالكامل بأمان

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream = None
        with self._lock:
            self._subscribed.clear()
            self._last_update_ts.clear()

    def sync_subscriptions(self, symbols: List[str], intervals: List[str]):
        """يشترك بأي تركيبة (عملة+فريم) جديدة مو مشترك فيها بعد. لا نلغي اشتراك
        القديم (تبسيط متعمَّد — تكلفته زيادة بسيطة بعرض النطاق، بلا مخاطرة)."""
        if not self.stream:
            return
        with self._lock:
            for symbol in symbols:
                for interval in intervals:
                    key = (symbol, interval)
                    if key not in self._subscribed:
                        self._subscribed.add(key)
                        self.stream.subscribe(symbol, interval)

    def is_fresh(self, symbol: str, interval: str, max_age_seconds: float) -> bool:
        """هل البث الحي غذّى هذي التركيبة خلال آخر max_age_seconds؟ لو لأ (أو
        ما اشتركنا فيها أصلاً)، الفاحص يرجع لـREST كشبكة أمان."""
        if not self.stream:
            return False
        last = self._last_update_ts.get((symbol, interval))
        if last is None:
            return False
        return (time.time() - last) <= max_age_seconds

    def _on_update(self, symbol: str, interval: str, kline: Kline, is_closed: bool):
        self._last_update_ts[(symbol, interval)] = time.time()
        if not is_closed:
            return  # نحفظ بالأرشيف الدائم بس الشموع المغلقة فعلياً (نفس فلسفة الاستراتيجيات: نستبعد الشمعة الحيّة)
        try:
            self.db.save_candles(symbol, interval, [{
                "open_time": kline.open_time, "open": kline.open, "high": kline.high,
                "low": kline.low, "close": kline.close, "volume": kline.volume,
                "close_time": kline.close_time,
            }], keep_latest=1200)
        except Exception:
            pass  # فشل حفظ لحظي واحد ما يوقف البث — REST هيغطي أي فجوة لاحقاً


class OKXPublicStream:
    """يفتح اتصال WebSocket واحد يدعم اشتراكات متعددة (رمز+فريم)، مع إعادة
    اتصال تلقائية عند الانقطاع. يستدعي `on_candle_update` لكل تحديث (سواء
    الشمعة لسا قيد التكوين أو أُغلقت فعلياً — راجع `confirm` بالبيانات)."""

    def __init__(self, on_candle_update: Callable[[str, str, Kline, bool], None]):
        """on_candle_update(symbol, interval, kline, is_closed)"""
        if websocket is None:
            raise RuntimeError("مكتبة websocket-client غير مثبَّتة — شغّل: pip install websocket-client")
        self.on_candle_update = on_candle_update
        self._ws: Optional["websocket.WebSocketApp"] = None
        self._thread: Optional[threading.Thread] = None
        self._subscriptions: set = set()  # {(symbol, interval)}
        self._stop = False
        self._connected = threading.Event()

    def subscribe(self, symbol: str, interval: str):
        bar = _BAR_MAP.get(interval, interval)
        inst_id = _to_inst_id(symbol)
        self._subscriptions.add((symbol, interval))
        if self._connected.is_set() and self._ws:
            self._send_subscribe(inst_id, bar)

    def _send_subscribe(self, inst_id: str, bar: str):
        msg = {"op": "subscribe", "args": [{"channel": f"candle{bar}", "instId": inst_id}]}
        try:
            self._ws.send(json.dumps(msg))
        except Exception:
            pass

    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run_forever(self):
        backoff = 2
        while not self._stop:
            try:
                self._ws = websocket.WebSocketApp(
                    PUBLIC_WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=lambda ws, err: None,
                    on_close=lambda ws, code, msg: self._connected.clear(),
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            self._connected.clear()
            if self._stop:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)  # تراجع أسّي، سقف 30 ثانية بين المحاولات

    def _on_open(self, ws):
        self._connected.set()
        for symbol, interval in list(self._subscriptions):
            bar = _BAR_MAP.get(interval, interval)
            self._send_subscribe(_to_inst_id(symbol), bar)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return
        if "data" not in data or "arg" not in data:
            return
        channel = data["arg"].get("channel", "")
        inst_id = data["arg"].get("instId", "")
        if not channel.startswith("candle"):
            return
        bar = channel.replace("candle", "")
        interval = next((k for k, v in _BAR_MAP.items() if v == bar), bar)
        symbol = inst_id.replace("-USDT-SWAP", "USDT").replace("-", "")

        for item in data["data"]:
            # صيغة OKX: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
            k = Kline(
                open_time=int(item[0]), open=float(item[1]), high=float(item[2]),
                low=float(item[3]), close=float(item[4]), volume=float(item[5]),
                close_time=int(item[0]),
            )
            is_closed = item[8] == "1" if len(item) > 8 else False
            self.on_candle_update(symbol, interval, k, is_closed)


class OKXPrivateStream:
    """يتصل بقناة OKX الخاصة (تحتاج مصادقة) — يراقب تحديثات المراكز والأوامر
    الحقيقية لحظياً، عشان نعرف فوراً لما وقف/هدف يُنفَّذ فعلياً على المنصة،
    بدل انتظار دورة الفحص الدورية التالية."""

    def __init__(self, api_key: str, api_secret: str, passphrase: str, is_testnet: bool,
                 on_position_update: Callable[[dict], None],
                 on_order_update: Callable[[dict], None]):
        if websocket is None:
            raise RuntimeError("مكتبة websocket-client غير مثبَّتة — شغّل: pip install websocket-client")
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.is_testnet = is_testnet
        self.on_position_update = on_position_update
        self.on_order_update = on_order_update
        self._ws = None
        self._thread = None
        self._stop = False

    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _login_payload(self) -> dict:
        ts = str(int(time.time()))
        prehash = f"{ts}GET/users/self/verify"
        mac = hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256)
        sign = base64.b64encode(mac.digest()).decode()
        return {"op": "login", "args": [{
            "apiKey": self.api_key, "passphrase": self.passphrase,
            "timestamp": ts, "sign": sign,
        }]}

    def _run_forever(self):
        backoff = 2
        while not self._stop:
            try:
                self._ws = websocket.WebSocketApp(
                    PRIVATE_WS_URL, on_open=self._on_open, on_message=self._on_message,
                    on_error=lambda ws, err: None, on_close=lambda ws, code, msg: None,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            if self._stop:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _on_open(self, ws):
        ws.send(json.dumps(self._login_payload()))

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return
        if data.get("event") == "login" and data.get("code") == "0":
            ws.send(json.dumps({"op": "subscribe", "args": [
                {"channel": "positions", "instType": "SWAP"},
                {"channel": "orders", "instType": "SWAP"},
            ]}))
            return
        channel = data.get("arg", {}).get("channel")
        if channel == "positions":
            for pos in data.get("data", []):
                self.on_position_update(pos)
        elif channel == "orders":
            for order in data.get("data", []):
                self.on_order_update(order)
