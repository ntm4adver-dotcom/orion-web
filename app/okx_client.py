"""
عميل OKX — بيانات السوق العامة + قسم التداول الفعلي (يتطلب مفاتيح API الخاصة
بالمستخدم، تُدخل من واجهة الويب ولا تُخزَّن إلا مشفّرة محلياً في قاعدة البيانات).

⚠️ تنبيه: أي أمر يُرسل عبر place_order ينفّذ صفقة حقيقية على حساب المستخدم إن لم
يكن الحساب في وضع Demo/Testnet. استُخدم توثيق OKX v5 الرسمي (نفس الـ endpoints
التي كان يستخدمها OkxClient.kt الأصلي: /api/v5/account/*، /api/v5/trade/order،
/api/v5/market/*، /api/v5/public/*).
"""
import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

import httpx

from .analyzer import Kline

BASE_URL = "https://www.okx.com"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def _sign(timestamp: str, method: str, path: str, body: str, secret: str) -> str:
    prehash = f"{timestamp}{method.upper()}{path}{body}"
    mac = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _request(method: str, path: str, body: dict | None, api_key: str, api_secret: str,
             passphrase: str, is_testnet: bool) -> Optional[dict]:
    if not api_key or not api_secret or not passphrase:
        return None
    body_str = "" if not body else __import__("json").dumps(body, separators=(",", ":"))
    timestamp = _timestamp()
    sig = _sign(timestamp, method, path, body_str, api_secret)
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "x-simulated-trading": "1" if is_testnet else "0",
        "Content-Type": "application/json",
    }
    for base in (BASE_URL, "https://aws.okx.com"):
        try:
            with httpx.Client(timeout=15) as client:
                if method.upper() == "POST":
                    r = client.post(base + path, headers=headers, content=body_str)
                else:
                    r = client.get(base + path, headers=headers)
                return r.json()
        except Exception:
            continue
    return None


def _public_get(path: str, error_key: Optional[str] = None) -> Optional[dict]:
    """نسخة مطوّرة: تسجّل سبب الفشل الحقيقي (شبكة/انتهاء مهلة/رفض من OKX) بدل ابتلاعه
    بصمت — عشان نقدر نشخّص المشكلة الفعلية بدل التخمين. لو error_key محدد، السبب
    يُسجَّل بقاموس last_error تحت هذا المفتاح، وتقدر تشوفه عبر صفحة التشخيص."""
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(BASE_URL + path)
            if r.status_code != 200:
                if error_key:
                    last_error[error_key] = f"رفض HTTP {r.status_code} من OKX — {r.text[:150]}"
                return None
            data = r.json()
            if error_key:
                code = data.get("code")
                if code not in (None, "0"):
                    last_error[error_key] = f"رفضت OKX الطلب: {data.get('msg', 'بدون تفاصيل')} (كود {code})"
                else:
                    last_error.pop(error_key, None)
            return data
    except httpx.TimeoutException:
        if error_key:
            last_error[error_key] = "انتهت مهلة الاتصال (Timeout 15 ثانية) — الشبكة بطيئة أو OKX لا يستجيب"
        return None
    except httpx.ConnectError as e:
        if error_key:
            last_error[error_key] = f"فشل الاتصال بالخادم: {e}"
        return None
    except Exception as e:
        if error_key:
            last_error[error_key] = f"خطأ غير متوقع: {type(e).__name__}: {e}"
        return None


def test_connection(api_key: str, api_secret: str, passphrase: str, is_testnet: bool) -> Tuple[bool, str]:
    if not api_key or not api_secret or not passphrase:
        return False, "لم يتم إدخال مفاتيح API بعد. أدخلها بالأعلى واضغط «حفظ ومزامنة»."
    resp = _request("GET", "/api/v5/account/config", None, api_key, api_secret, passphrase, is_testnet)
    if not resp:
        return False, "فشل الاتصال بالخادم. تأكد من اتصال الإنترنت."
    code = resp.get("code", "-1")
    if code == "0":
        data = resp.get("data") or []
        pos_mode = data[0].get("posMode", "unknown") if data else "unknown"
        mode_text = "صافي المراكز (Net Mode)" if pos_mode == "net_mode" else "التحوط ثنائي الاتجاه (Hedge)"
        return True, f"تم الاتصال بنجاح! وضع الحساب: {mode_text}"
    return False, f"خطأ من منصة OKX: {resp.get('msg', 'خطأ غير معروف')} (كود: {code})"


def fetch_account_info(api_key: str, api_secret: str, passphrase: str, is_testnet: bool) -> dict:
    resp = _request("GET", "/api/v5/account/balance?ccy=USDT", None, api_key, api_secret, passphrase, is_testnet)
    if not resp or resp.get("code") != "0":
        return {"total_equity": 0.0, "available_balance": 0.0, "currency": "USDT",
                "status_message": (resp or {}).get("msg", "تعذر جلب بيانات الحساب")}
    try:
        details = resp["data"][0]["details"]
        usdt = next((d for d in details if d.get("ccy") == "USDT"), details[0] if details else {})
        return {
            "total_equity": float(usdt.get("eq", 0) or 0),
            "available_balance": float(usdt.get("availBal", 0) or 0),
            "currency": "USDT",
            "status_message": "OK",
        }
    except Exception:
        return {"total_equity": 0.0, "available_balance": 0.0, "currency": "USDT", "status_message": "تعذر تحليل الاستجابة"}


def fetch_positions(api_key: str, api_secret: str, passphrase: str, is_testnet: bool) -> List[dict]:
    resp = _request("GET", "/api/v5/account/positions", None, api_key, api_secret, passphrase, is_testnet)
    if not resp or resp.get("code") != "0":
        return []
    out = []
    for p in resp.get("data", []):
        pos = float(p.get("pos", 0) or 0)
        if pos == 0:
            continue
        out.append({
            "inst_id": p.get("instId", ""),
            "pos_side": p.get("posSide", ""),
            "pos": pos,
            "avg_px": float(p.get("avgPx", 0) or 0),
            "upl": float(p.get("upl", 0) or 0),
            "leverage": p.get("lever", ""),
        })
    return out


def _to_inst_id(symbol: str) -> str:
    base = symbol.replace("USDT", "").replace("USDC", "").replace("/", "").replace("-", "").upper()
    return f"{base}-USDT-SWAP"


def set_leverage(symbol: str, leverage: int, margin_mode: str, api_key: str, api_secret: str,
                  passphrase: str, is_testnet: bool) -> Tuple[bool, str]:
    body = {"instId": _to_inst_id(symbol), "lever": str(leverage), "mgnMode": margin_mode}
    resp = _request("POST", "/api/v5/account/set-leverage", body, api_key, api_secret, passphrase, is_testnet)
    if resp and resp.get("code") == "0":
        return True, "تم ضبط الرافعة"
    return False, (resp or {}).get("msg", "فشل ضبط الرافعة")


def fetch_max_leverage(inst_id: str, margin_mode: str, api_key: str, api_secret: str,
                        passphrase: str, is_testnet: bool, default_fallback: int = 10) -> int:
    """يعادل fetchMaxLeverage في OkxClient.kt الأصلي — يجلب أقصى رافعة متاحة لهذه العملة تحديداً."""
    resp = _request("GET", f"/api/v5/account/max-leverage?instId={inst_id}&mgnMode={margin_mode}",
                     None, api_key, api_secret, passphrase, is_testnet)
    if resp and resp.get("code") == "0" and resp.get("data"):
        try:
            lev = int(float(resp["data"][0].get("maxLever", default_fallback)))
            return max(1, lev)
        except Exception:
            pass
    return default_fallback


_ctval_cache: Dict[str, float] = {}


def fetch_contract_value(inst_id: str) -> float:
    """يعادل fetchInstrumentContractValue الأصلي — قيمة العقد الواحد (ctVal) لكل رمز.
    ضروري لحساب حجم الصفقة (sz) بدقة، لأن عقود OKX Swap ليست دائماً 1 وحدة = 1 عقد."""
    if inst_id in _ctval_cache:
        return _ctval_cache[inst_id]
    resp = _public_get(f"/api/v5/public/instruments?instType=SWAP&instId={inst_id}")
    if resp and resp.get("code") == "0" and resp.get("data"):
        try:
            ct_val = float(resp["data"][0].get("ctVal", 1.0) or 1.0)
            _ctval_cache[inst_id] = ct_val
            return ct_val
        except Exception:
            pass
    _ctval_cache[inst_id] = 1.0
    return 1.0


def calculate_order_quantity_usdt(settings: dict, entry_price: float, stop_loss: float,
                                   available_balance: Optional[float] = None) -> float:
    """يعادل منطق حساب calculatedQuantityUsdt في OrionViewModel.kt الأصلي:
    1) لو التكيف التلقائي (Adaptive Sizing) مفعّل ولدينا وقف خسارة صالح → نحسب الحجم
       بحيث تكون الخسارة القصوى المحتملة = adaptive_stop_loss_limit_usdt بالضبط.
    2) وإلا لو نوع الحجم PERCENTAGE → نسبة من الرصيد المتاح.
    3) وإلا → المبلغ الثابت المُدخل يدوياً (okx_volume_usdt)."""
    quantity_usdt = float(settings.get("okx_volume_usdt", 10.0))

    if settings.get("is_adaptive_stop_loss_enabled") and stop_loss and entry_price and stop_loss > 0 and entry_price > 0:
        price_diff = abs(entry_price - stop_loss)
        leverage = max(1, int(settings.get("okx_leverage", 10)))
        if price_diff > 0:
            adaptive_qty = (float(settings.get("adaptive_stop_loss_limit_usdt", 1.0)) * entry_price) / (leverage * price_diff)
            if adaptive_qty > 0:
                quantity_usdt = adaptive_qty
    elif settings.get("okx_volume_type") == "PERCENTAGE" and available_balance is not None:
        pct_value = available_balance * (float(settings.get("okx_volume_percent", 5.0)) / 100.0)
        if pct_value > 0:
            quantity_usdt = pct_value

    return quantity_usdt if quantity_usdt > 0 else 10.0  # قيمة احتياطية آمنة


def place_order(symbol: str, side: str, quantity_usdt: float, leverage: int, margin_mode: str,
                 stop_loss: float, take_profit: float, api_key: str, api_secret: str,
                 passphrase: str, is_testnet: bool, is_market_order: bool = True,
                 is_max_leverage_enabled: bool = False) -> Tuple[bool, str]:
    """side: 'buy' أو 'sell'. ينفذ أمر سوق فوري بحجم quantity_usdt دولار مع ربط SL/TP اختيارياً."""
    inst_id = _to_inst_id(symbol)

    final_leverage = leverage
    if is_max_leverage_enabled:
        final_leverage = fetch_max_leverage(inst_id, margin_mode, api_key, api_secret, passphrase,
                                             is_testnet, default_fallback=leverage)

    set_leverage(symbol, final_leverage, margin_mode, api_key, api_secret, passphrase, is_testnet)

    px_resp = _public_get(f"/api/v5/market/ticker?instId={inst_id}")
    try:
        last_price = float(px_resp["data"][0]["last"])
    except Exception:
        return False, "تعذر جلب السعر الحالي لحساب الكمية"
    if last_price <= 0:
        return False, "سعر غير صالح"

    ct_val = fetch_contract_value(inst_id)
    leverage = final_leverage
    sz = str(round((quantity_usdt * leverage) / (last_price * ct_val), 6))

    body = {
        "instId": inst_id,
        "tdMode": margin_mode,
        "side": side,
        "ordType": "market" if is_market_order else "limit",
        "sz": sz,
    }
    if stop_loss and stop_loss > 0:
        body["slTriggerPx"] = str(stop_loss)
        body["slOrdPx"] = "-1"
    if take_profit and take_profit > 0:
        body["tpTriggerPx"] = str(take_profit)
        body["tpOrdPx"] = "-1"

    resp = _request("POST", "/api/v5/trade/order", body, api_key, api_secret, passphrase, is_testnet)
    if not resp:
        return False, "فشل الاتصال بمنصة OKX"
    if resp.get("code") == "0":
        return True, f"تم تنفيذ الأمر بنجاح ({sz} عقد) برافعة x{leverage} ({margin_mode})"
    detail = resp.get("data", [{}])
    msg = detail[0].get("sMsg") if detail else resp.get("msg", "خطأ غير معروف")
    return False, msg or resp.get("msg", "خطأ غير معروف")


def fetch_klines(symbol: str, interval: str, limit: int = 100) -> List[Kline]:
    inst_id = _to_inst_id(symbol)
    bar_map = {"1h": "1H", "4h": "4H", "1d": "1D", "1D": "1D"}
    bar = bar_map.get(interval, interval)
    resp = _public_get(f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}")
    if not resp or resp.get("code") != "0":
        return []
    klines = []
    for item in resp.get("data", []):
        klines.append(Kline(
            open_time=int(item[0]),
            open=float(item[1]), high=float(item[2]), low=float(item[3]), close=float(item[4]),
            volume=float(item[6]),
            close_time=int(item[0]) + 60000,
        ))
    klines.reverse()
    return klines


def fetch_all_prices() -> Dict[str, float]:
    resp = _public_get("/api/v5/market/tickers?instType=SWAP")
    prices = {}
    if resp and resp.get("code") == "0":
        for obj in resp.get("data", []):
            inst_id = obj.get("instId", "")
            if inst_id.endswith("-USDT-SWAP"):
                base = inst_id.replace("-USDT-SWAP", "")
                prices[f"{base}USDT"] = float(obj.get("last", 0) or 0)
    return prices


last_error: Dict[str, str] = {}


def _default_symbols():
    return [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT",
        "AVAXUSDT", "LINKUSDT", "TONUSDT", "SUIUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT",
        "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "MATICUSDT", "ATOMUSDT",
    ]


def fetch_top_symbols(limit_count: int = 10) -> List[str]:
    return fetch_screened_symbols("top_volume", limit_count)


def fetch_screened_symbols(mode: str, limit_count: int = 10) -> List[str]:
    """يجيب قائمة عملات مصنَّفة حسب معيار مختار — انظر التوثيق بعميل Binance لشرح كل وضع.
    ملاحظة: وضع oi_spike بـ OKX أدق من Binance لأنه يستخدم نداء واحد مجمّع لكل العملات
    دفعة وحدة (bulk endpoint)، بدل نداء منفصل لكل عملة."""
    resp = _public_get("/api/v5/market/tickers?instType=SWAP")
    if not resp or resp.get("code") != "0":
        last_error["_top_symbols"] = (resp or {}).get("msg", "تعذر الاتصال بـ OKX لجلب قائمة العملات") if resp else "تعذر الاتصال بـ OKX لجلب قائمة العملات"
        return _default_symbols()[:limit_count]
    try:
        liquid_pool = []
        for obj in resp.get("data", []):
            inst_id = obj.get("instId", "")
            if not inst_id.endswith("-USDT-SWAP"):
                continue
            quote_volume = float(obj.get("volCcy24h", 0) or 0)
            last_price = float(obj.get("last", 0) or 0)
            open24h = float(obj.get("open24h", 0) or 0)
            if quote_volume < 10_000_000.0 or last_price < 0.0001:
                continue
            change_pct = ((last_price - open24h) / open24h * 100.0) if open24h > 0 else 0.0
            symbol = inst_id.replace("-USDT-SWAP", "") + "USDT"
            liquid_pool.append({"symbol": symbol, "inst_id": inst_id, "volume": quote_volume, "change_pct": change_pct})

        if not liquid_pool:
            last_error["_top_symbols"] = "لم يتم إيجاد عملات مطابقة لشروط السيولة على OKX"
            return _default_symbols()[:limit_count]

        if mode == "big_movers":
            liquid_pool.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
            result = [c["symbol"] for c in liquid_pool[:limit_count]]

        elif mode == "high_funding":
            liquid_pool.sort(key=lambda x: x["volume"], reverse=True)
            pool = liquid_pool[:40]
            funding_map = []
            for c in pool:
                fr = fetch_funding_rate(c["symbol"])
                if fr is not None:
                    funding_map.append((c["symbol"], abs(fr)))
                time.sleep(0.1)
            funding_map.sort(key=lambda x: x[1], reverse=True)
            result = [s for s, _ in funding_map[:limit_count]]

        elif mode == "oi_spike":
            # نداء مجمّع واحد يجيب الفائدة المفتوحة لكل عملات OKX Swap دفعة وحدة
            oi_resp = _public_get("/api/v5/public/open-interest?instType=SWAP")
            oi_map = []
            if oi_resp and oi_resp.get("code") == "0":
                now = int(time.time() * 1000)
                pool_ids = {c["inst_id"] for c in liquid_pool}
                for item in oi_resp.get("data", []):
                    inst_id = item.get("instId", "")
                    if inst_id not in pool_ids:
                        continue
                    try:
                        oi_val = float(item.get("oi", 0) or 0)
                    except Exception:
                        continue
                    history = _oi_history.setdefault(inst_id, [])
                    history[:] = [h for h in history if now - h[0] <= OI_HISTORY_MAX_AGE_MS]
                    oldest = history[0] if history else None
                    history.append((now, oi_val))
                    if len(history) > OI_HISTORY_MAX_POINTS:
                        history.pop(0)
                    if oldest and oldest[1] > 0:
                        change = ((oi_val - oldest[1]) / oldest[1]) * 100.0
                        symbol = inst_id.replace("-USDT-SWAP", "") + "USDT"
                        oi_map.append((symbol, abs(change)))
            if not oi_map:
                last_error["_top_symbols"] = "لسا يجمع بيانات القياس الأول للفائدة المفتوحة — النتائج ستكون جاهزة بالدورة القادمة"
                liquid_pool.sort(key=lambda x: x["volume"], reverse=True)
                result = [c["symbol"] for c in liquid_pool[:limit_count]]
            else:
                oi_map.sort(key=lambda x: x[1], reverse=True)
                result = [s for s, _ in oi_map[:limit_count]]

        else:  # top_volume (افتراضي)
            liquid_pool.sort(key=lambda x: x["volume"], reverse=True)
            result = [c["symbol"] for c in liquid_pool[:limit_count]]

        if result:
            last_error.pop("_top_symbols", None)
            return result
        last_error["_top_symbols"] = "لم يتم إيجاد عملات مطابقة لشروط السيولة على OKX"
        return _default_symbols()[:limit_count]
    except Exception as e:
        last_error["_top_symbols"] = str(e)
        return _default_symbols()[:limit_count]


_oi_history: Dict[str, list] = {}
OI_HISTORY_MAX_AGE_MS = 45 * 60 * 1000
OI_HISTORY_MAX_POINTS = 12


def fetch_open_interest_change_pct(symbol: str) -> Optional[float]:
    import time as _time
    inst_id = _to_inst_id(symbol)
    resp = _public_get(f"/api/v5/public/open-interest?instId={inst_id}")
    if not resp or resp.get("code") != "0" or not resp.get("data"):
        return None
    try:
        oi = float(resp["data"][0].get("oi", 0) or 0)
    except Exception:
        return None
    now = int(_time.time() * 1000)
    history = _oi_history.setdefault(symbol, [])
    history[:] = [h for h in history if now - h[0] <= OI_HISTORY_MAX_AGE_MS]
    oldest = history[0] if history else None
    history.append((now, oi))
    if len(history) > OI_HISTORY_MAX_POINTS:
        history.pop(0)
    if oldest and oldest[1] > 0:
        return ((oi - oldest[1]) / oldest[1]) * 100.0
    return None


def fetch_funding_rate(symbol: str) -> Optional[float]:
    inst_id = _to_inst_id(symbol)
    resp = _public_get(f"/api/v5/public/funding-rate?instId={inst_id}")
    if not resp or resp.get("code") != "0" or not resp.get("data"):
        return None
    try:
        return float(resp["data"][0].get("fundingRate", 0) or 0)
    except Exception:
        return None


def fetch_order_book_imbalance(symbol: str, depth: int = 20) -> Optional[float]:
    inst_id = _to_inst_id(symbol)
    resp = _public_get(f"/api/v5/market/books?instId={inst_id}&sz={depth}")
    if not resp or resp.get("code") != "0" or not resp.get("data"):
        return None
    try:
        book = resp["data"][0]
        bid_qty = sum(float(b[1]) for b in book.get("bids", []))
        ask_qty = sum(float(a[1]) for a in book.get("asks", []))
        total = bid_qty + ask_qty
        return (bid_qty - ask_qty) / total if total > 0 else None
    except Exception:
        return None


_cvd_history: Dict[str, list] = {}
CVD_HISTORY_MAX_AGE_MS = 24 * 60 * 60 * 1000  # نافذة تراكمية 24 ساعة
CVD_HISTORY_MAX_POINTS = 500  # سقف حماية من تضخم الذاكرة


def fetch_taker_pressure(symbol: str, limit: int = 100) -> Optional[float]:
    """ضغط المتداولين الفعليين (Taker Buy/Sell Pressure): يفحص آخر الصفقات المنفَّذة
    فعلياً بالسوق (مو مجرد أوامر معلّقة بالـ order book) ويحسب هل الأغلبية اشترت
    بأوامر سوق (taker buy) أو باعت (taker sell). إشارة أقوى وأدق من فوليوم الشمعة
    العادي لتأكيد إن الاختراق مدعوم بضغط شراء/بيع حقيقي، وليس مجرد تقلب عابر.
    ترجع رقم بين -1 (ضغط بيع كامل) و 1 (ضغط شراء كامل).
    كل استدعاء لهذي الدالة يغذّي أيضاً متتبع CVD التراكمي (انظر get_cvd_24h_pct)."""
    inst_id = _to_inst_id(symbol)
    error_key = f"taker_pressure:{symbol}"
    resp = _public_get(f"/api/v5/market/trades?instId={inst_id}&limit={limit}", error_key=error_key)
    if not resp or resp.get("code") != "0":
        return None  # السبب مسجّل تلقائياً بـ last_error[error_key] عبر _public_get
    if not resp.get("data"):
        last_error[error_key] = "لا توجد صفقات حديثة مسجّلة لهذي العملة على OKX (سيولة منخفضة جداً أو الزوج غير نشط)"
        return None
    try:
        buy_vol = 0.0
        sell_vol = 0.0
        for t in resp["data"]:
            sz = float(t.get("sz", 0) or 0)
            if t.get("side") == "buy":
                buy_vol += sz
            elif t.get("side") == "sell":
                sell_vol += sz
        _record_cvd_sample(symbol, buy_vol, sell_vol)
        total = buy_vol + sell_vol
        if total <= 0:
            last_error[error_key] = "إجمالي حجم الصفقات المسحوبة صفر (بيانات غير صالحة من المنصة)"
            return None
        last_error.pop(error_key, None)
        return (buy_vol - sell_vol) / total
    except Exception as e:
        last_error[error_key] = f"خطأ أثناء معالجة بيانات الصفقات: {type(e).__name__}: {e}"
        return None


def _record_cvd_sample(symbol: str, buy_vol: float, sell_vol: float):
    now = int(time.time() * 1000)
    history = _cvd_history.setdefault(symbol, [])
    history.append((now, buy_vol, sell_vol))
    cutoff = now - CVD_HISTORY_MAX_AGE_MS
    history[:] = [h for h in history if h[0] >= cutoff]
    if len(history) > CVD_HISTORY_MAX_POINTS:
        del history[: len(history) - CVD_HISTORY_MAX_POINTS]


def get_cvd_24h_pct(symbol: str, min_samples: int = 3) -> Optional[float]:
    """CVD تراكمي (Cumulative Volume Delta) على مدى 24 ساعة — نسبة هيمنة الشراء الفعلي
    من إجمالي حجم التداول المُعايَن. مبني على عيّنات دورية من صفقات فعلية (مو تخمين)،
    تتجمّع تلقائياً كل ما اشتغل الفحص. القيمة تصير أدق كل ما اشتغل البوت لفترة أطول
    (تحتاج 24 ساعة تشغيل متواصل لتغطية كاملة للنافذة الزمنية).
    القيمة: 0% = بيع كامل، 50% = تعادل، 100% = شراء كامل."""
    history = _cvd_history.get(symbol, [])
    now = int(time.time() * 1000)
    cutoff = now - CVD_HISTORY_MAX_AGE_MS
    recent = [h for h in history if h[0] >= cutoff]
    if len(recent) < min_samples:
        return None
    total_buy = sum(h[1] for h in recent)
    total_sell = sum(h[2] for h in recent)
    total = total_buy + total_sell
    if total <= 0:
        return None
    return (total_buy / total) * 100.0


def fetch_long_short_ratio(symbol: str) -> Optional[float]:
    """نسبة تمركز الحسابات (Long/Short Account Ratio): تكشف هل أغلب المتداولين على
    هذه العملة متمركزين شراء أو بيع حالياً. ازدحام شديد باتجاه واحد (نسبة متطرفة)
    يزيد احتمال انعكاس مفاجئ بسبب تصفية المراكز المزدحمة (Liquidation Squeeze)،
    فنستخدمها كفلتر حذر إضافي مشابه لفلتر معدل التمويل الحالي."""
    inst_id = _to_inst_id(symbol)
    resp = _public_get(f"/api/v5/rubik/stat/contracts/long-short-account-ratio-contract?instId={inst_id}&period=5m&limit=1")
    if not resp or resp.get("code") != "0" or not resp.get("data"):
        return None
    try:
        return float(resp["data"][0][1])
    except Exception:
        return None
