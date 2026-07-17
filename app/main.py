import os
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db
from . import okx_client
from .auth import is_logged_in, APP_PASSWORD
from .scanner import scanner_state

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app = FastAPI(title="Orion Trading Bot")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("ORION_SECRET_KEY", "orion-dev-secret-change-me"))


@app.on_event("startup")
def on_startup():
    db.init_db()
    settings = db.get_settings()
    if settings.get("is_auto_scanning"):
        scanner_state.start()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.get("/login")
def login_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/")
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    if password == APP_PASSWORD:
        request.session["logged_in"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "كلمة المرور غير صحيحة"})


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


def _guard(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return None


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def dashboard(request: Request):
    g = _guard(request)
    if g:
        return g
    return templates.TemplateResponse("dashboard.html", {"request": request, "active": "dashboard"})


@app.get("/settings")
def settings_page(request: Request):
    g = _guard(request)
    if g:
        return g
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings", "s": db.get_settings(), "saved": False})


@app.post("/settings")
async def settings_save(request: Request):
    g = _guard(request)
    if g:
        return g
    form = await request.form()
    checkboxes = ["is_auto_scanning", "is_single_coin_mode_enabled", "is_telegram_enabled",
                  "is_volume_filter_enabled", "is_vwap_filter_enabled", "is_4h_buyers_filter_enabled",
                  "is_cancel_if_exceeds_target_enabled"]
    updates = {}
    for key in db.DEFAULT_SETTINGS:
        if key in checkboxes:
            updates[key] = 1 if key in form else 0
        elif key in form:
            updates[key] = form[key]
    db.update_settings(updates)
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings", "s": db.get_settings(), "saved": True})


@app.get("/trading")
def trading_page(request: Request):
    g = _guard(request)
    if g:
        return g
    return templates.TemplateResponse("trading.html", {"request": request, "active": "trading", "s": db.get_settings()})


@app.post("/trading")
async def trading_save(request: Request):
    g = _guard(request)
    if g:
        return g
    form = await request.form()
    updates = {
        "okx_api_key": form.get("okx_api_key", ""),
        "okx_api_secret": form.get("okx_api_secret", ""),
        "okx_passphrase": form.get("okx_passphrase", ""),
        "okx_is_testnet": 1 if "okx_is_testnet" in form else 0,
    }
    db.update_settings(updates)
    return RedirectResponse("/trading", status_code=303)


@app.post("/trading/execution")
async def trading_execution_save(request: Request):
    g = _guard(request)
    if g:
        return g
    form = await request.form()
    updates = {
        "okx_is_auto_trading_enabled": 1 if "okx_is_auto_trading_enabled" in form else 0,
        "okx_leverage": form.get("okx_leverage", 10),
        "okx_is_max_leverage_enabled": 1 if "okx_is_max_leverage_enabled" in form else 0,
        "okx_margin_mode": form.get("okx_margin_mode", "cross"),
        "okx_volume_type": form.get("okx_volume_type", "FIXED"),
        "okx_volume_usdt": form.get("okx_volume_usdt", 10.0),
        "okx_volume_percent": form.get("okx_volume_percent", 5.0),
        "is_adaptive_stop_loss_enabled": 1 if "is_adaptive_stop_loss_enabled" in form else 0,
        "adaptive_stop_loss_limit_usdt": form.get("adaptive_stop_loss_limit_usdt", 1.0),
        "is_instant_entry_enabled": 1 if "is_instant_entry_enabled" in form else 0,
    }
    db.update_settings(updates)
    return RedirectResponse("/trading", status_code=303)


# ---------------------------------------------------------------------------
# JSON API (polled by dashboard JS)
# ---------------------------------------------------------------------------

@app.get("/api/status")
def api_status(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {
        "is_scanning_active": scanner_state.is_scanning_active,
        "is_currently_working": scanner_state.is_currently_working,
        "last_scan_time": scanner_state.last_scan_time,
        "countdown_seconds": scanner_state.countdown_seconds,
    }


@app.get("/api/logs")
def api_logs(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return db.get_logs(200)


@app.post("/api/logs/clear")
def api_logs_clear(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.clear_logs()
    return {"ok": True}


@app.get("/api/signals")
def api_signals(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return db.get_signals(100)


@app.get("/signals")
def signals_page(request: Request):
    g = _guard(request)
    if g:
        return g
    return templates.TemplateResponse("signals.html", {"request": request, "active": "signals", "s": db.get_settings()})


@app.post("/api/signals/clear")
def api_signals_clear(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.clear_signals()
    return {"ok": True}


@app.post("/api/signals/execute")
async def api_signals_execute(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    s = db.get_settings()
    if not s["okx_api_key"] or not s["okx_api_secret"] or not s["okx_passphrase"]:
        return {"success": False, "message": "يرجى تعبئة مفاتيح API لربط حساب OKX أولاً في صفحة التداول الآلي."}

    symbol = body.get("symbol", "")
    side = body.get("side", "Long")
    entry_price = float(body.get("entry_price", 0))
    stop_loss = float(body.get("stop_loss", 0))
    take_profit = float(body.get("take_profit", 0))
    side_text = "buy" if side == "Long" else "sell"

    available_balance = None
    if s.get("okx_volume_type") == "PERCENTAGE":
        info = okx_client.fetch_account_info(s["okx_api_key"], s["okx_api_secret"], s["okx_passphrase"], s["okx_is_testnet"])
        available_balance = info.get("available_balance")

    quantity_usdt = okx_client.calculate_order_quantity_usdt(s, entry_price, stop_loss, available_balance)

    success, message = okx_client.place_order(
        symbol=symbol, side=side_text, quantity_usdt=quantity_usdt,
        leverage=s["okx_leverage"], margin_mode=s["okx_margin_mode"],
        stop_loss=stop_loss, take_profit=take_profit,
        api_key=s["okx_api_key"], api_secret=s["okx_api_secret"], passphrase=s["okx_passphrase"],
        is_testnet=s["okx_is_testnet"], is_market_order=s.get("is_instant_entry_enabled", True),
        is_max_leverage_enabled=s.get("okx_is_max_leverage_enabled", False),
    )
    db.add_log(f"{'✅' if success else '❌'} [أمر يدوي] إرسال صفقة {symbol} ({side}) - {message}")
    return {"success": success, "message": message}


@app.post("/api/scan/start")
def api_scan_start(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.update_settings({"is_auto_scanning": 1})
    scanner_state.start()
    return {"ok": True}


@app.post("/api/scan/stop")
def api_scan_stop(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.update_settings({"is_auto_scanning": 0})
    scanner_state.stop()
    return {"ok": True}


@app.post("/api/scan/trigger")
def api_scan_trigger(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    scanner_state.trigger_immediate_scan()
    return {"ok": True}


@app.post("/api/trading/test")
def api_trading_test(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    s = db.get_settings()
    ok, msg = okx_client.test_connection(s["okx_api_key"], s["okx_api_secret"], s["okx_passphrase"], s["okx_is_testnet"])
    if not ok:
        return {"message": msg}
    info = okx_client.fetch_account_info(s["okx_api_key"], s["okx_api_secret"], s["okx_passphrase"], s["okx_is_testnet"])
    positions = okx_client.fetch_positions(s["okx_api_key"], s["okx_api_secret"], s["okx_passphrase"], s["okx_is_testnet"])
    return {
        "message": msg,
        "equity": info["total_equity"],
        "available": info["available_balance"],
        "positions": positions,
    }
