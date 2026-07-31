import os
import time
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db
from . import okx_client
from . import learning
from . import backup_scheduler
from . import gdrive_backup
from . import backtest
from .strategies import get_strategy_options, strategy_label
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
    backup_scheduler.scheduler.start()  # النسخ الاحتياطي التلقائي يشتغل دائماً بغض النظر عن حالة الفحص


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
    return templates.TemplateResponse("settings.html", {
        "request": request, "active": "settings", "s": db.get_settings(), "saved": False,
        "strategy_options": get_strategy_options(),
    })


@app.post("/settings")
async def settings_save(request: Request):
    g = _guard(request)
    if g:
        return g
    form = await request.form()
    checkboxes = ["is_auto_scanning", "is_single_coin_mode_enabled", "is_telegram_enabled",
                  "is_volume_filter_enabled", "is_vwap_filter_enabled", "is_4h_buyers_filter_enabled",
                  "is_cancel_if_exceeds_target_enabled",
                  "is_efficiency_filter_enabled", "is_market_alignment_filter_enabled",
                  "is_breakeven_stop_enabled", "is_auto_breakeven_half_target_enabled",
                  "is_split_targets_enabled", "is_market_regime_filter_enabled", "is_reverse_mode_enabled"]
    updates = {}
    for key in db.DEFAULT_SETTINGS:
        if key in checkboxes:
            updates[key] = 1 if key in form else 0
        elif key in form:
            updates[key] = form[key]

    # صناديق اختيار الاستراتيجيات المفعّلة داخل وضع "الكل معاً" — أسماء متعددة بنفس الحقل
    checked_strategies = form.getlist("combined_strategies")
    updates["combined_enabled_strategies"] = ",".join(checked_strategies)

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

@app.get("/diagnose")
def diagnose_page(request: Request):
    g = _guard(request)
    if g:
        return g
    return templates.TemplateResponse("diagnose.html", {"request": request, "active": "diagnose", "s": db.get_settings()})


@app.get("/api/liquidation-heatmap")
def api_liquidation_heatmap(request: Request, symbol: str):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    from . import binance_client, okx_client
    from .liquidation_heatmap import estimate_liquidation_heatmap

    symbol = symbol.strip().upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    s = db.get_settings()
    exchange = okx_client if s["exchange"] == "okx" else binance_client
    exchange_name = "OKX" if s["exchange"] == "okx" else "Binance"

    # آخر ~7 أيام على فريم الساعة (168 شمعة) كنافذة "بروفايل الفوليوم" لتقدير مواقع الدخول المحتملة
    klines = exchange.fetch_klines(symbol, "1h", 168)
    if len(klines) < 20:
        return {"symbol": symbol, "exchange": exchange_name, "error": "بيانات غير كافية لهذا الرمز"}

    current_price = klines[-1].close
    funding_rate = exchange.fetch_funding_rate(symbol) if hasattr(exchange, "fetch_funding_rate") else None
    long_short_ratio = exchange.fetch_long_short_ratio(symbol) if hasattr(exchange, "fetch_long_short_ratio") else None

    result = estimate_liquidation_heatmap(klines, current_price, funding_rate=funding_rate, long_short_ratio=long_short_ratio)
    return {
        "symbol": symbol, "exchange": exchange_name, "current_price": current_price,
        "funding_rate": funding_rate, "long_short_ratio": long_short_ratio,
        "top_long_liq_zones": result["top_long_liq_zones"],
        "top_short_liq_zones": result["top_short_liq_zones"],
        "is_estimate": True,
        "disclaimer": "تقدير إحصائي تقريبي مبني على بروفايل الفوليوم ورافعات شائعة — مو بيانات تصفية حقيقية مؤكدة من المنصة.",
    }


@app.get("/api/diagnose")
def api_diagnose(request: Request, symbol: str):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    import inspect
    from . import binance_client, okx_client
    from .analyzer import MarketMicrostructure
    from .strategies import STRATEGY_REGISTRY

    symbol = symbol.strip().upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    s = db.get_settings()
    exchange = okx_client if s["exchange"] == "okx" else binance_client
    exchange_name = "OKX" if s["exchange"] == "okx" else "Binance"

    k4h = exchange.fetch_klines(symbol, "4h", 100)
    k1h = exchange.fetch_klines(symbol, "1h", 170)
    k15m = exchange.fetch_klines(symbol, "15m", 110)
    k5m = exchange.fetch_klines(symbol, "5m", 150)
    k_daily = exchange.fetch_klines(symbol, "1d", 100)

    data_status = {
        "4h": len(k4h), "1h": len(k1h), "15m": len(k15m), "5m": len(k5m), "1d": len(k_daily),
    }

    if len(k5m) < 30 or len(k1h) < 60:
        return {
            "symbol": symbol, "exchange": exchange_name, "data_status": data_status,
            "error": "بيانات الشموع غير كافية لهذا الرمز — إما الرمز غير صحيح، أو المنصة لا تدرجه، أو فيه حظر مؤقت حالياً.",
            "last_error": getattr(exchange, "last_error", {}).get(symbol),
        }

    micro = MarketMicrostructure(
        oi_change_pct=exchange.fetch_open_interest_change_pct(symbol),
        funding_rate=exchange.fetch_funding_rate(symbol),
        ob_imbalance=exchange.fetch_order_book_imbalance(symbol),
        taker_pressure=exchange.fetch_taker_pressure(symbol) if hasattr(exchange, "fetch_taker_pressure") else None,
        long_short_ratio=exchange.fetch_long_short_ratio(symbol) if hasattr(exchange, "fetch_long_short_ratio") else None,
        cvd_pct=exchange.get_cvd_24h_pct(symbol) if hasattr(exchange, "get_cvd_24h_pct") else None,
    )

    def _fmt_result(r):
        if r is None:
            return None
        return {
            "side": r.side, "probability": r.prob, "entry_price": r.entry_price,
            "stop_loss": r.stop_loss, "take_profit": r.take_profit, "rr": r.rr, "quality": r.quality,
        }

    # نشغّل كل استراتيجية مسجّلة بالسجل المركزي تلقائياً — أي استراتيجية تُضاف مستقبلاً
    # تظهر هنا بدون أي تعديل إضافي بهذا الملف، بنفس مبدأ "الكل معاً"
    strategies_out = {}
    for key, info in STRATEGY_REGISTRY.items():
        fn = info["fn"]
        trace: list = []
        try:
            # لو الاستراتيجية تدعم معامل trace نمرره، وإلا نستدعيها بدونه بدون ما نكسر التوافق
            if "trace" in inspect.signature(fn).parameters:
                result = fn(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro, trace=trace)
            else:
                result = fn(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro)
                trace = [{"check": "ℹ️ هذي الاستراتيجية لا تدعم التتبع التفصيلي بعد", "value": "", "ok": None}]
        except Exception as e:
            result = None
            trace = [{"check": "❌ خطأ أثناء التحليل", "value": str(e), "ok": False}]

        strategies_out[key] = {
            "label": strategy_label(key),
            "result": _fmt_result(result),
            "trace": trace,
        }

    micro_errors = {}
    if hasattr(exchange, "last_error"):
        for field, key_prefix in (("taker_pressure", "taker_pressure"),):
            err = exchange.last_error.get(f"{key_prefix}:{symbol}")
            if err:
                micro_errors[field] = err

    return {
        "symbol": symbol, "exchange": exchange_name, "data_status": data_status,
        "microstructure": {
            "oi_change_pct": micro.oi_change_pct, "funding_rate": micro.funding_rate,
            "ob_imbalance": micro.ob_imbalance, "taker_pressure": micro.taker_pressure,
            "long_short_ratio": micro.long_short_ratio, "cvd_pct": micro.cvd_pct,
        },
        "microstructure_errors": micro_errors,
        "strategies": strategies_out,
    }


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
    return db.get_signals(300)


@app.get("/api/signals/stats")
def api_signals_stats(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return db.get_signal_stats()


@app.get("/evolution")
def evolution_page(request: Request):
    g = _guard(request)
    if g:
        return g
    return templates.TemplateResponse("evolution.html", {"request": request, "active": "evolution", "s": db.get_settings()})


@app.post("/api/learning/settings")
async def api_learning_settings(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    updates = {
        "is_coin_learning_enabled": 1 if body.get("is_coin_learning_enabled") else 0,
        "coin_learning_min_trades": body.get("coin_learning_min_trades", 5),
        "coin_learning_weak_threshold": body.get("coin_learning_weak_threshold", 35),
        "coin_learning_strong_threshold": body.get("coin_learning_strong_threshold", 70),
        "strategy_learning_min_trades": body.get("strategy_learning_min_trades", 10),
        "strategy_learning_weak_threshold": body.get("strategy_learning_weak_threshold", 35),
        "strategy_learning_strong_threshold": body.get("strategy_learning_strong_threshold", 70),
    }
    db.update_settings(updates)
    return {"ok": True}


@app.get("/api/telegram/contacts")
def api_telegram_contacts_list(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return db.get_telegram_contacts()


@app.post("/api/telegram/contacts/add")
async def api_telegram_contacts_add(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    name = (body.get("name") or "").strip()
    chat_id = (body.get("chat_id") or "").strip()
    if not chat_id:
        return {"success": False, "message": "أدخل معرّف Chat ID."}
    contacts = db.add_telegram_contact(name, chat_id)
    return {"success": True, "contacts": contacts}


@app.post("/api/telegram/contacts/remove")
async def api_telegram_contacts_remove(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    contacts = db.remove_telegram_contact((body.get("chat_id") or "").strip())
    return {"success": True, "contacts": contacts}


@app.get("/api/watchlist")
def api_watchlist_list(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return db.get_watchlist()


@app.post("/api/watchlist/add")
async def api_watchlist_add(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    symbol = (body.get("symbol") or "").strip()
    if not symbol:
        return {"success": False, "message": "أدخل رمز العملة."}
    watchlist = db.add_watchlist_symbol(symbol)
    return {"success": True, "watchlist": watchlist}


@app.post("/api/watchlist/remove")
async def api_watchlist_remove(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    watchlist = db.remove_watchlist_symbol((body.get("symbol") or "").strip())
    return {"success": True, "watchlist": watchlist}


@app.get("/api/backup/gdrive/status")
def api_gdrive_status(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {
        "configured": gdrive_backup.is_configured(),
        "connected": gdrive_backup.is_connected(),
    }


@app.get("/api/backup/gdrive/connect")
def api_gdrive_connect(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login")
    if not gdrive_backup.is_configured():
        return JSONResponse({"error": "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET غير مضبوطة بمتغيرات البيئة"}, status_code=400)
    redirect_uri = str(request.base_url).rstrip("/") + "/api/backup/gdrive/callback"
    return RedirectResponse(gdrive_backup.build_auth_url(redirect_uri))


@app.get("/api/backup/gdrive/callback")
def api_gdrive_callback(request: Request, code: str = "", error: str = ""):
    if not is_logged_in(request):
        return RedirectResponse("/login")
    if error:
        return RedirectResponse(f"/settings?gdrive_error={error}")
    redirect_uri = str(request.base_url).rstrip("/") + "/api/backup/gdrive/callback"
    ok, msg = gdrive_backup.exchange_code_for_tokens(code, redirect_uri)
    if ok:
        return RedirectResponse("/settings?gdrive_connected=1")
    return RedirectResponse(f"/settings?gdrive_error={msg}")


@app.post("/api/backup/gdrive/disconnect")
def api_gdrive_disconnect(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    gdrive_backup.disconnect()
    return {"ok": True}


@app.post("/api/backup/auto-settings")
async def api_backup_auto_settings(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    db.update_settings({
        "is_auto_backup_enabled": 1 if body.get("is_auto_backup_enabled") else 0,
        "auto_backup_interval_hours": body.get("auto_backup_interval_hours", 6),
        "auto_backup_retention_count": body.get("auto_backup_retention_count", 10),
    })
    return {"ok": True}


@app.get("/api/backup/list")
def api_backup_list(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return backup_scheduler.list_backups()


@app.get("/api/backup/download/{filename}")
def api_backup_download(filename: str, request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    # حماية من مسارات خارج مجلد النسخ الاحتياطية
    safe_name = os.path.basename(filename)
    filepath = os.path.join(backup_scheduler.BACKUP_DIR, safe_name)
    if not os.path.isfile(filepath):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(filepath, filename=safe_name, media_type="application/json")


@app.get("/api/backup/export")
def api_backup_export(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    backup = db.export_backup()
    filename = f"orion-backup-{backup['exported_at']}.json"
    return JSONResponse(
        content=backup,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/backup/import")
async def api_backup_import(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        form = await request.form()
        file = form.get("file")
        mode = form.get("mode", "merge")
        if not file:
            return {"success": False, "message": "لم يتم اختيار ملف."}
        content = await file.read()
        import json
        data = json.loads(content)
        result = db.import_backup(data, mode=mode)
        db.add_log(f"📦 تم استيراد نسخة احتياطية: {result['restored_signals']} إشارة مُستعادة، "
                    f"{result['skipped_duplicates']} مكررة تم تخطيها، {result['settings_restored']} إعداد.")
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "message": f"فشل الاستيراد: {e}"}


@app.get("/api/strategy-performance")
def api_strategy_performance(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    settings = db.get_settings()
    perf = db.get_strategy_performance()
    for p in perf:
        p["label"] = strategy_label(p["strategy"]) if p["strategy"] != "غير محدد" else "غير محدد (صفقات قديمة قبل هذا التحديث)"
        adj, _ = learning.get_strategy_adjustment(p["strategy"], settings)
        base = int(settings.get("min_probability", 70))
        p["effective_threshold"] = max(50, min(95, base + adj))
    return perf


@app.get("/api/probability-calibration")
def api_probability_calibration(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    calibration = db.get_probability_calibration()
    for c in calibration:
        c["label"] = strategy_label(c["strategy"]) if c["strategy"] != "غير محدد" else "غير محدد"
    return calibration


@app.get("/api/learning")
def api_learning(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    settings = db.get_settings()
    perf = db.get_coin_performance()
    for p in perf:
        threshold, msg = learning.effective_threshold(p["symbol"], p["side"], settings)
        p["effective_threshold"] = threshold
        p["is_weak"] = p["win_rate"] < settings.get("coin_learning_weak_threshold", 35) and p["total"] >= settings.get("coin_learning_min_trades", 5)
        p["is_strong"] = p["win_rate"] >= settings.get("coin_learning_strong_threshold", 70) and p["total"] >= settings.get("coin_learning_min_trades", 5)
    return perf


def _drawdown_analysis(signals):
    """يلخّص أقصى تراجع (Maximum Adverse Excursion) وأعلى ربح عائم (Maximum Favorable
    Excursion) لكل استراتيجية — يفرّق بين 'فوز نظيف' (تراجع بسيط قبل النجاح، دخول
    دقيق) و'فوز مهزوز' (تراجع كبير قبل ما يرجع وينجح)، وبين 'خسارة كانت أصلاً خطأ
    اتجاه' و'خسارة كانت رابحة وانعكست' (صعدت للربح ثم رجعت وضربت الوقف — يعني
    الاتجاه كان صح لكن الخروج/الهدف يحتاج تحسين، مو الدخول نفسه)."""
    by_strategy = {}
    for s in signals:
        if s["status"] not in ("HIT_TP", "HIT_SL"):
            continue
        strat = s.get("strategy") or "غير محدد"
        by_strategy.setdefault(strat, {"win_dd": [], "loss_dd": [], "loss_mfe": []})
        dd = s.get("max_drawdown_pct") or 0.0
        mfe = s.get("max_favorable_pct") or 0.0
        if s["status"] == "HIT_TP":
            by_strategy[strat]["win_dd"].append(dd)
        else:
            by_strategy[strat]["loss_dd"].append(dd)
            by_strategy[strat]["loss_mfe"].append(mfe)

    result = []
    for strat, d in by_strategy.items():
        wins_dd, losses_dd, losses_mfe = d["win_dd"], d["loss_dd"], d["loss_mfe"]
        # خسارة "انعكاس حقيقي": كانت بربح عائم واضح (>0.5%) قبل ما ترجع تضرب الوقف —
        # الاتجاه كان صحيحاً، المشكلة بإدارة الخروج مو باختيار نقطة الدخول
        reversed_losses = sum(1 for x in losses_mfe if x > 0.5)
        strat_signals = [s for s in signals if (s.get("strategy") or "غير محدد") == strat]
        breakeven_count = sum(1 for s in strat_signals if s.get("breakeven_activated"))
        result.append({
            "strategy": strat,
            "avg_drawdown_on_wins_pct": round(sum(wins_dd) / len(wins_dd), 3) if wins_dd else None,
            "avg_drawdown_on_losses_pct": round(sum(losses_dd) / len(losses_dd), 3) if losses_dd else None,
            "max_drawdown_on_a_win_pct": round(max(wins_dd), 3) if wins_dd else None,
            "shaky_wins_count": sum(1 for x in wins_dd if x > 0.5),
            "reversed_losses_count": reversed_losses,
            "reversed_losses_note": ("صعدت للربح (>0.5%) ثم رجعت وضربت الوقف — الاتجاه كان صح، "
                                      "المشكلة بالهدف/الخروج مو بالدخول") if reversed_losses else None,
            "avg_mfe_before_loss_pct": round(sum(losses_mfe) / len(losses_mfe), 3) if losses_mfe else None,
            "breakeven_activated_count": breakeven_count,
        })
    return result


@app.get("/api/signals/export")
def api_signals_export(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    import json
    from datetime import datetime

    signals = db.get_signals(limit=100000)  # كل الصفقات المسجّلة بدون حد
    strategy_perf = db.get_strategy_performance()
    coin_perf = db.get_coin_performance(limit=1000)
    settings = db.get_settings()

    # نحذف الحقول الحساسة (مفاتيح API وكلمات المرور) قبل التصدير، حتى لو المستخدم أرسل
    # الملف لجهة خارجية للمراجعة
    safe_settings = {k: v for k, v in settings.items()
                      if k not in ("okx_api_key", "okx_api_secret", "okx_passphrase", "telegram_token")}

    export_data = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "total_signals": len(signals),
        "summary": {
            "wins": sum(1 for s in signals if s["status"] == "HIT_TP"),
            "losses": sum(1 for s in signals if s["status"] == "HIT_SL"),
            "breakeven": sum(1 for s in signals if s["status"] == "BREAKEVEN"),
            "active": sum(1 for s in signals if s["status"] == "ACTIVE"),
            "pending": sum(1 for s in signals if s["status"] == "PENDING"),
            "cancelled": sum(1 for s in signals if s["status"] in ("CANCELLED", "REPLACED")),
        },
        "strategy_performance": strategy_perf,
        "coin_performance": coin_perf,
        "drawdown_analysis": _drawdown_analysis(signals),
        "probability_calibration": db.get_probability_calibration(),
        "probability_calibration_note": "يقارن الاحتمالية المعلنة وقت إنشاء كل صفقة بنسبة النجاح الحقيقية الفعلية لكل فئة — يكشف هل رقم الثقة له معنى حقيقي أو لأ",
        "filter_rejection_counts": db.get_rejection_counts(),
        "filter_rejection_note": "عدد مرات رفض كل فلتر لإشارة منذ آخر تصفير — يفيد لتقييم هل حدود الفلاتر الحالية متشددة أو متساهلة فعلياً",
        "active_settings_snapshot": safe_settings,
        "signals": signals,
    }

    content = json.dumps(export_data, ensure_ascii=False, indent=2, default=str)
    filename = f"orion_signals_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    tp1_price = float(body.get("tp1_price", 0) or 0)
    is_split = bool(body.get("split_targets_used")) and tp1_price > 0
    side_text = "buy" if side == "Long" else "sell"

    available_balance = None
    if s.get("okx_volume_type") == "PERCENTAGE":
        info = okx_client.fetch_account_info(s["okx_api_key"], s["okx_api_secret"], s["okx_passphrase"], s["okx_is_testnet"])
        available_balance = info.get("available_balance")

    quantity_usdt = okx_client.calculate_order_quantity_usdt(s, entry_price, stop_loss, available_balance)

    if is_split:
        success, message = okx_client.place_split_orders(
            symbol=symbol, side=side_text, quantity_usdt=quantity_usdt,
            leverage=s["okx_leverage"], margin_mode=s["okx_margin_mode"],
            stop_loss=stop_loss, tp1=tp1_price, tp2=take_profit,
            api_key=s["okx_api_key"], api_secret=s["okx_api_secret"], passphrase=s["okx_passphrase"],
            is_testnet=s["okx_is_testnet"], is_market_order=s.get("is_instant_entry_enabled", False),
            is_max_leverage_enabled=s.get("okx_is_max_leverage_enabled", False),
            entry_price=entry_price,
        )
    else:
        success, message = okx_client.place_order(
            symbol=symbol, side=side_text, quantity_usdt=quantity_usdt,
            leverage=s["okx_leverage"], margin_mode=s["okx_margin_mode"],
            stop_loss=stop_loss, take_profit=take_profit,
            api_key=s["okx_api_key"], api_secret=s["okx_api_secret"], passphrase=s["okx_passphrase"],
            is_testnet=s["okx_is_testnet"], is_market_order=s.get("is_instant_entry_enabled", False),
            is_max_leverage_enabled=s.get("okx_is_max_leverage_enabled", False),
            entry_price=entry_price,
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


@app.post("/api/trading/cancel-order")
async def api_trading_cancel_order(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    inst_id = body.get("inst_id", "")
    ord_id = body.get("ord_id", "")
    if not inst_id or not ord_id:
        return {"success": False, "message": "بيانات الأمر ناقصة"}
    s = db.get_settings()
    success, message = okx_client.cancel_pending_order(
        inst_id, ord_id, s["okx_api_key"], s["okx_api_secret"], s["okx_passphrase"], s["okx_is_testnet"],
    )
    db.add_log(f"{'✅' if success else '❌'} [إلغاء أمر معلّق] {inst_id} - {message}")
    return {"success": success, "message": message}


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
    pending_orders = okx_client.fetch_pending_orders(s["okx_api_key"], s["okx_api_secret"], s["okx_passphrase"], s["okx_is_testnet"])
    return {
        "message": msg,
        "equity": info["total_equity"],
        "available": info["available_balance"],
        "positions": positions,
        "pending_orders": pending_orders,
    }


@app.post("/api/backtest/start")
async def api_backtest_start(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    symbols = body.get("symbols") or []
    days_back = int(body.get("days_back", 30))
    strategy_keys = body.get("strategy_keys") or None
    if not symbols:
        return {"success": False, "message": "لازم تحدد عملة واحدة على الأقل"}
    days_back = max(3, min(365, days_back))  # حماية: بين 3 أيام وسنة كاملة
    use_live_settings = bool(body.get("use_live_settings", True))

    # 🔴 نثبّت المنصة على OKX دائماً بالاختبار الخلفي (بطلب صريح) — Binance تحظر
    # بمناطق كثيرة، فما نعتمد على إعداد "exchange" الحالي بالإعدادات، نضمن OKX دوماً
    started = backtest.start_backtest_job(symbols, days_back, okx_client, strategy_keys, use_live_settings)
    if not started:
        return {"success": False, "message": "فيه اختبار خلفي شغّال بالفعل — انتظر يخلص أو راجع النتيجة الحالية"}
    mode_text = "بإعداداتك الحقيقية (فلاتر + تعادل + تقسيم)" if use_live_settings else "بالوضع الخام (بدون أي فلتر أو إدارة صفقة إضافية)"
    return {"success": True, "message": f"بدأ الاختبار الخلفي {mode_text} على {len(symbols)} عملة لآخر {days_back} يوم — هذا ممكن ياخذ عدة دقائق."}


@app.get("/api/backtest/status")
def api_backtest_status(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    status = backtest.get_job_status()
    # نرجع النتائج الخام بس لو صغيرة (تفادي حمولة ضخمة) — الإحصائيات الملخّصة تكفي عادة
    if status.get("results") and len(status["results"]) > 500:
        status = dict(status)
        status["results"] = status["results"][:500]
        status["results_truncated"] = True
    return status


@app.get("/api/backtest/export")
def api_backtest_export(request: Request):
    """🆕 تصدير كامل نتائج آخر اختبار خلفي بصيغة JSON — نفس روح تصدير الإشارات
    الحية، عشان ترسلها للتطوير المستقبلي وتحليلها بعمق."""
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    status = backtest.get_job_status()
    if status["running"]:
        return JSONResponse({"error": "الاختبار الخلفي لسا شغّال، انتظر يخلص أول"}, status_code=400)
    if not status["stats"]:
        return JSONResponse({"error": "ما فيه نتيجة اختبار خلفي محفوظة بعد"}, status_code=404)

    export_data = {
        "exported_at": int(time.time() * 1000),
        "backtest_type": "historical_simulation",
        "mode": "live_settings" if status.get("use_live_settings", True) else "raw",
        "mode_description": "بإعداداتك الحقيقية (فلاتر + تعادل + تقسيم أهداف)" if status.get("use_live_settings", True) else "raw — بدون أي فلتر أو إدارة صفقة إضافية، كل صفقة خام على الوقف/الهدف الأصليين فقط",
        "note": "هذي نتائج محاكاة على بيانات تاريخية حقيقية (بدون تحيّز نظر مستقبلي) — مو صفقات حية فعلية. الاستراتيجيات المعتمدة على بيانات لحظية (فابيو، مصيدة الحشد) مستبعدة لعدم توفر بياناتها تاريخياً.",
        "started_at": status["started_at"],
        "finished_at": status["finished_at"],
        "duration_seconds": round(status["finished_at"] - status["started_at"], 1) if status["finished_at"] and status["started_at"] else None,
        "active_settings_snapshot": status.get("settings_snapshot"),
        "stats": status["stats"],
        "signals": status["results"],
    }
    return JSONResponse(content=export_data, headers={
        "Content-Disposition": f"attachment; filename=orion_backtest_export_{int(time.time())}.json"
    })
