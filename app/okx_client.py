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


def _public_get(path: str) -> Optional[dict]:
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(BASE_URL + path)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


def test_connection(api_key: str, api_secret: str, passphrase: str, is_testnet: bool) -> Tuple[bool, str]:
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


def place_order(symbol: str, side: str, quantity_usdt: float, leverage: int, margin_mode: str,
                 stop_loss: float, take_profit: float, api_key: str, api_secret: str,
                 passphrase: str, is_testnet: bool, is_market_order: bool = True) -> Tuple[bool, str]:
    """side: 'buy' أو 'sell'. ينفذ أمر سوق فوري بحجم quantity_usdt دولار مع ربط SL/TP اختيارياً."""
    inst_id = _to_inst_id(symbol)
    set_leverage(symbol, leverage, margin_mode, api_key, api_secret, passphrase, is_testnet)

    px_resp = _public_get(f"/api/v5/market/ticker?instId={inst_id}")
    try:
        last_price = float(px_resp["data"][0]["last"])
    except Exception:
        return False, "تعذر جلب السعر الحالي لحساب الكمية"
    if last_price <= 0:
        return False, "سعر غير صالح"

    sz = str(round((quantity_usdt * leverage) / last_price, 6))

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
        return True, f"تم تنفيذ الأمر بنجاح ({sz} عقد)"
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
