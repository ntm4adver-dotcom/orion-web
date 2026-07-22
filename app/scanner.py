"""محرك الفحص الخلفي — يعادل OrionScanner.kt / OrionScannerService.kt الأصليين.
يعمل كـ Thread واحد داخل نفس عملية الويب، يفحص العملات دورياً، يحفظ الإشارات،
يرسل تنبيهات تيليجرام، ويتابع حالة الصفقات المفتوحة (PENDING/ACTIVE/HIT_TP/HIT_SL).
"""
import threading
import time
from typing import Optional

from . import db
from . import binance_client
from . import okx_client
from . import telegram_alert
from . import learning
from .analyzer import MarketMicrostructure
from .strategies import get_active_strategies, strategy_label


class ScannerState:
    def __init__(self):
        self.is_scanning_active = False
        self.is_currently_working = False
        self.last_scan_time: Optional[int] = None
        self.countdown_seconds = 0
        self._thread: Optional[threading.Thread] = None
        self._price_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._trigger_immediate = threading.Event()
        self._notified_transitions = set()

    def start(self):
        if self._thread and self._thread.is_alive():
            db.add_log("عملية الفحص المجدولة تعمل بالفعل.")
            return
        self._stop_flag.clear()
        self.is_scanning_active = True
        db.add_log("تم بدء تشغيل محرك أوريون الذكي للفحص التلقائي...")
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not (self._price_thread and self._price_thread.is_alive()):
            self._price_thread = threading.Thread(target=self._price_update_loop, daemon=True)
            self._price_thread.start()

    def stop(self):
        self.is_scanning_active = False
        self._stop_flag.set()
        db.add_log("تم إيقاف الفحص التلقائي.")

    def trigger_immediate_scan(self):
        self._trigger_immediate.set()

    def _price_update_loop(self):
        """خيط مستقل خفيف يحدّث السعر اللحظي لكل الصفقات المفتوحة كل 5 ثوانٍ،
        بدون انتظار اكتمال دورة الفحص الكاملة (اللي قد تاخذ وقت أطول لكل العملات)."""
        while not self._stop_flag.is_set():
            try:
                settings = db.get_settings()
                self._update_signal_prices(settings)
            except Exception:
                pass
            for _ in range(10):  # 5 ثوانٍ مقسّمة لفحص متكرر لعلم الإيقاف
                if self._stop_flag.is_set():
                    return
                time.sleep(0.5)

    def _run_loop(self):
        while not self._stop_flag.is_set():
            try:
                settings = db.get_settings()
                if not settings["is_auto_scanning"]:
                    db.add_log("البوت في وضع الخمول - الفحص غير نشط.")
                    self._wait(5)
                    continue

                self.is_currently_working = True
                self._run_scan_cycle(settings)
                self.last_scan_time = int(time.time() * 1000)
            except Exception as e:
                db.add_log(f"⚠️ خطأ في دورة الفحص: {e}")
            finally:
                self.is_currently_working = False

            self._wait(max(settings.get("scan_interval_seconds", 30), 5))

    def _wait(self, seconds: int):
        self.countdown_seconds = seconds
        for _ in range(seconds * 2):
            if self._stop_flag.is_set() or self._trigger_immediate.is_set():
                self._trigger_immediate.clear()
                self.countdown_seconds = 0
                return
            time.sleep(0.5)
            self.countdown_seconds = max(0, self.countdown_seconds - 0.5)
        self.countdown_seconds = 0

    def _resolve_symbols(self, settings: dict):
        if settings["is_single_coin_mode_enabled"]:
            raw = [s.strip().upper() for s in settings["single_coin_symbol"].split(",") if s.strip()]
            symbols = [s if s.endswith(("USDT", "BUSD")) else f"{s}USDT" for s in raw]
            db.add_log(f"🎯 [مراقبة مخصصة] جاري مراقبة وتحليل عملات: {', '.join(symbols)}")
            return symbols
        limit = settings.get("symbols_limit", 10)
        client = okx_client if settings["exchange"] == "okx" else binance_client
        exchange_name = "OKX" if settings["exchange"] == "okx" else "Binance"
        mode = settings.get("symbol_selection_mode", "top_volume")
        mode_labels = {
            "top_volume": "الأعلى سيولة وحجم تداول",
            "big_movers": "الأكبر تحركاً سعرياً (Big Movers)",
            "high_funding": "الأعلى تطرفاً بمعدل التمويل (High Funding)",
            "oi_spike": "الأكبر قفزة بالفائدة المفتوحة (OI Spike)",
        }
        db.add_log(f"جاري استعلام {exchange_name} عن أزواج العملات — المعيار: {mode_labels.get(mode, mode)}...")
        symbols = client.fetch_screened_symbols(mode, limit)
        fallback_reason = getattr(client, "last_error", {}).get("_top_symbols")
        if fallback_reason:
            db.add_log(f"⚠️ تعذر جلب قائمة العملات الحقيقية من {exchange_name}، تم استخدام قائمة احتياطية مؤقتة — السبب: {fallback_reason}")
        db.add_log(f"✅ تم العثور على {len(symbols)} زوج: {', '.join(symbols)}")
        return symbols

    def _run_scan_cycle(self, settings: dict):
        from . import ict_strategy
        ict_strategy.set_ignore_kill_zone(settings.get("ict_ignore_kill_zone", False))

        symbols = self._resolve_symbols(settings)
        exchange = okx_client if settings["exchange"] == "okx" else binance_client
        db.add_log(f"[{time.strftime('%H:%M:%S')}] بدء فحص حزمة الأزواج الذكية المكتشفة...")
        incomplete_data_notes = []  # نجمّع كل نقص بيانات بالدورة، ونرسل تنبيه تيليجرام واحد بالنهاية بدل إغراق المستخدم برسائل

        # تحقق من الحظر المؤقت مرة واحدة بداية الدورة بدل ما نكرر نفس الخطأ لكل عملة
        if hasattr(exchange, "get_ban_status"):
            ban_msg = exchange.get_ban_status()
            if ban_msg:
                db.add_log(f"⏸️ تم إيقاف هذه الدورة مؤقتاً — {ban_msg}")
                if settings.get("is_telegram_enabled"):
                    telegram_alert.send_text_alert(
                        settings["telegram_token"], settings["telegram_chat_ids"],
                        f"⏸️ *تنبيه توقف الفحص*\nتم إيقاف دورة الفحص كاملة بسبب حظر مؤقت:\n{ban_msg}",
                    )
                return

        # جلب اتجاه السوق العام (البيتكوين كمؤشر مرجعي) مرة وحدة بداية الدورة — يُستخدم
        # كفلتر توافق إلزامي لكل صفقة بكل استراتيجية: لا تُقبل صفقة تعاكس اتجاه السوق
        # العام، لأن أغلب العملات البديلة مرتبطة بحركة البيتكوين بقوة، وصفقة تعاكسه
        # معرّضة لانعكاس مفاجئ بغض النظر عن قوة إعداد العملة نفسها محلياً.
        btc_trend = None
        btc_klines = None
        try:
            btc_klines = exchange.fetch_klines("BTCUSDT", "4h", 60)
            if btc_klines and len(btc_klines) >= 20:
                from .analyzer import _get_bias as _get_market_bias
                btc_trend = _get_market_bias(btc_klines)
                db.add_log(f"📊 اتجاه السوق العام (البيتكوين، 4 ساعات): {btc_trend}")
        except Exception:
            btc_trend = None
            btc_klines = None

        for idx, symbol in enumerate(symbols):
            if self._stop_flag.is_set():
                break
            if idx > 0:
                time.sleep(1.2)  # تأخير أكبر بين كل عملة وأخرى لتجنب تقييد معدل الطلبات من المنصة
            try:
                db.add_log(f"جاري سحب بيانات الشموع لزوج {symbol}...")
                k4h = exchange.fetch_klines(symbol, "4h", 100)
                time.sleep(0.25)
                # 170 شمعة ساعة (~7 أيام) بدل 100 — استراتيجية "صيد التصفيات" مصممة
                # تحتاج نافذة أسبوع كامل لتقدير خارطة تصفية شاملة (كانت تشتغل بـ4 أيام
                # بس فعلياً أثناء الفحص التلقائي، أقل من المصمَّم له)
                k1h = exchange.fetch_klines(symbol, "1h", 170)
                time.sleep(0.25)
                # 110 شمعة 15د بدل 100 — هامش أمان بسيط فوق حاجة استراتيجية فابيو
                # فالنتيني (بروفايل الفوليوم يحتاج بالضبط آخر 100 شمعة)
                k15m = exchange.fetch_klines(symbol, "15m", 110)
                time.sleep(0.25)
                k5m = exchange.fetch_klines(symbol, "5m", 150)
                time.sleep(0.25)
                k_daily = exchange.fetch_klines(symbol, "1d", 100)

                if len(k5m) < 30 or len(k1h) < 60:
                    reason = getattr(exchange, "last_error", {}).get(symbol) if hasattr(exchange, "last_error") else None
                    if reason:
                        db.add_log(f"▫️ {symbol}: بيانات غير كافية للتحليل — السبب: {reason}")
                    else:
                        db.add_log(f"▫️ {symbol}: بيانات غير كافية للتحليل.")
                    incomplete_data_notes.append(f"{symbol}: نقص بالشموع (4س={len(k4h)}, 1س={len(k1h)}, 15د={len(k15m)}, 5د={len(k5m)}, يومي={len(k_daily)})" + (f" — {reason}" if reason else ""))
                    # إذا صرنا محظورين أثناء الفحص، نوقف بقية الدورة فوراً بدل تكرار المحاولة على كل عملة
                    if hasattr(exchange, "get_ban_status") and exchange.get_ban_status():
                        db.add_log(f"⏸️ تم إيقاف بقية الدورة — {exchange.get_ban_status()}")
                        return
                    continue

                micro = MarketMicrostructure(
                    oi_change_pct=exchange.fetch_open_interest_change_pct(symbol),
                    funding_rate=exchange.fetch_funding_rate(symbol),
                    ob_imbalance=exchange.fetch_order_book_imbalance(symbol),
                    taker_pressure=exchange.fetch_taker_pressure(symbol) if hasattr(exchange, "fetch_taker_pressure") else None,
                    long_short_ratio=exchange.fetch_long_short_ratio(symbol) if hasattr(exchange, "fetch_long_short_ratio") else None,
                    cvd_pct=exchange.get_cvd_24h_pct(symbol) if hasattr(exchange, "get_cvd_24h_pct") else None,
                )

                # ضغط المتداولين (Taker Pressure) صار شرط إلزامي بالانفجار السعري — لو غاب،
                # كل الاستراتيجيات المبنية عليه بترفض تلقائياً، فنسجّله كنقص بيانات حرج
                if micro.taker_pressure is None:
                    incomplete_data_notes.append(f"{symbol}: بيانات ضغط المتداولين الفعليين (Taker Pressure) غير متوفرة — سيتم رفض كل صفقات الانفجار السعري لهذي العملة بهذي الدورة")

                def _fmt(v, suffix=""):
                    return f"{v:.3f}{suffix}" if v is not None else "غير متوفر"

                db.add_log(
                    f"📥 [{symbol}] تم سحب: 4س={len(k4h)} | 1س={len(k1h)} | 15د={len(k15m)} | "
                    f"5د={len(k5m)} | يومي={len(k_daily)} شمعة | OI={_fmt(micro.oi_change_pct, '%')} | "
                    f"تمويل={_fmt(micro.funding_rate)} | عمق السوق={_fmt(micro.ob_imbalance)} | "
                    f"ضغط متداولين={_fmt(micro.taker_pressure)} | CVD={_fmt(micro.cvd_pct, '%')}"
                )

                matched_any = False
                for strategy_key, strategy_fn in get_active_strategies(
                        settings.get("active_strategy", "explosive_breakout"),
                        settings.get("combined_enabled_strategies", "")):
                    result = strategy_fn(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro)
                    if result is None:
                        continue
                    matched_any = True
                    self._process_signal(settings, symbol, strategy_key, result, k4h, k1h, k5m, btc_trend, btc_klines)

                if not matched_any:
                    db.add_log(f"▫️ {symbol}: ليس له اتجاه كافٍ حالياً.")

            except Exception as e:
                db.add_log(f"❌ [{symbol}] خطأ أثناء التحليل: {e}")
                incomplete_data_notes.append(f"{symbol}: خطأ استثنائي أثناء سحب/تحليل البيانات — {e}")
            time.sleep(0.2)

        if incomplete_data_notes and settings.get("is_telegram_enabled"):
            preview = incomplete_data_notes[:15]
            extra = len(incomplete_data_notes) - len(preview)
            body = "\n".join(f"• {note}" for note in preview)
            if extra > 0:
                body += f"\n… و {extra} حالة إضافية أخرى"
            telegram_alert.send_text_alert(
                settings["telegram_token"], settings["telegram_chat_ids"],
                f"⚠️ *تنبيه اكتمال البيانات*\nبهذي الدورة، {len(incomplete_data_notes)} عملة لم تُجلب لها البيانات كاملة "
                f"أو حصل خطأ أثناء التحليل:\n\n{body}",
            )

    def _process_signal(self, settings: dict, symbol: str, strategy_key: str, result, k4h, k1h, k5m, btc_trend=None, btc_klines=None):
        # 🔴 تحقق مركزي حرج (يحمي كل الاستراتيجيات دفعة وحدة، حالياً ومستقبلاً):
        # لصفقة Long، نقطة الدخول يجب تكون **أقل من أو تساوي** السعر الحالي (ننتظر
        # السعر ينزل لها = أمر Limit شراء منطقي). لو طلعت أعلى من السعر الحالي، يعني
        # الصفقة "تُفعَّل" فوراً بأول تحديث سعر (لأن الشرط `السعر<=الدخول` يتحقق مباشرة
        # بدون أي انتظار حقيقي)، وممكن يكون السعر وقتها أصلاً قريب جداً أو تجاوز الوقف
        # — هذا بالضبط سبب صفقات ضربت وقف خلال ثوانٍ من إنشائها. نفس المنطق بالعكس
        # لصفقات Short (الدخول لازم يكون أعلى من أو يساوي السعر الحالي).
        current_live_price = k5m[-1].close if k5m else None
        if current_live_price and current_live_price > 0:
            tolerance = current_live_price * 0.0005  # هامش تقريب بسيط (0.05%) لتفادي رفض حالات حدّية طبيعية
            if result.side == "Long" and result.entry_price > current_live_price + tolerance:
                db.add_log(f"❌ [{symbol}/{strategy_key}] رُفضت الإشارة: نقطة الدخول ({result.entry_price:.6g}) أعلى من السعر الحالي ({current_live_price:.6g}) بصفقة شراء — خطأ منطقي بحساب الاستراتيجية يمنع التفعيل الصحيح.")
                db.increment_rejection_counter("entry_direction_check")
                return
            if result.side == "Short" and result.entry_price < current_live_price - tolerance:
                db.add_log(f"❌ [{symbol}/{strategy_key}] رُفضت الإشارة: نقطة الدخول ({result.entry_price:.6g}) أقل من السعر الحالي ({current_live_price:.6g}) بصفقة بيع — خطأ منطقي بحساب الاستراتيجية يمنع التفعيل الصحيح.")
                db.increment_rejection_counter("entry_direction_check")
                return

        # 🆕 فلتر 1: العملة ما تتحرك عشوائياً — نسبة الكفاءة الاتجاهية (Efficiency Ratio).
        # 📊 إصلاح مبني على بيانات فعلية: كان الفلتر يفحص k1h (20 ساعة كاملة!) رغم إن
        # أغلب استراتيجياتنا سكالب سريع يتداول على فريم 5-15 دقيقة — مقياس بطيء جداً
        # لصفقة سريعة. فحص حقيقي أظهر هذا الفلتر وحده مسؤول عن أكثر من 85% من كل
        # الرفض (365 من 421)، بدون دليل تحسّن جودة يوازي هالانخفاض الهائل بالكمية.
        # الآن يفحص فريم 15 دقيقة (مطابق فعلياً لطبيعة الاستراتيجيات)، والحد خُفّف
        # بشكل كبير (0.28 → 0.15) — يرفض بس الحركة العشوائية المتطرفة جداً.
        if settings.get("is_efficiency_filter_enabled", True):
            from .analyzer import efficiency_ratio
            er = efficiency_ratio(k15m, period=16)
            min_er = settings.get("min_efficiency_ratio", 0.15)
            if er < min_er:
                db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي الإشارة: العملة تتحرك بشكل عشوائي/جانبي (كفاءة اتجاهية {er:.2f} أقل من {min_er}) — لا اتجاه حقيقي واضح.")
                db.increment_rejection_counter("efficiency_ratio_filter")
                return

        # 🆕 فلتر 2: توافق مع اتجاه السوق العام (البيتكوين) — إلزامي لكل استراتيجية،
        # **إلا لو العملة فكّت ارتباطها بالبيتكوين فعلياً**. نحسب معامل الارتباط
        # (Correlation) بين حركة العملة وحركة البيتكوين على فريم 4 ساعات؛ لو الارتباط
        # ضعيف (العملة تتحرك بمنطقها الخاص، مو تابعة للبيتكوين)، نتجاهل شرط توافق
        # البيتكوين، لكن **نشترط بدلاً منه توافق اتجاه العملة نفسها العام (4 ساعات)**
        # — لأن فك الارتباط بالسوق العام ما يعني عدم أهمية اتجاه العملة نفسها.
        if settings.get("is_market_alignment_filter_enabled", True) and not symbol.startswith("BTC"):
            side_trend = "صاعد" if result.side == "Long" else "هابط"
            is_decoupled = False
            correlation = None
            if btc_klines:
                from .analyzer import correlation_with
                correlation = correlation_with(k4h, btc_klines, period=30)
                min_corr = settings.get("min_btc_correlation", 0.35)
                is_decoupled = abs(correlation) < min_corr

            if is_decoupled:
                # فكّت الارتباط بالبيتكوين — نشترط توافق اتجاه العملة نفسها بدلاً منه
                from .analyzer import _get_bias as _get_coin_bias
                coin_trend = _get_coin_bias(k4h)
                if side_trend != coin_trend:
                    db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي الإشارة: العملة فكّت ارتباطها بالبيتكوين (ارتباط {correlation:.2f})، لكن الصفقة ({result.side}) تعاكس اتجاه العملة نفسها ({coin_trend}) — رفض.")
                    db.increment_rejection_counter("market_alignment_filter_decoupled_own_trend")
                    return
                db.add_log(f"ℹ️ [{symbol}/{strategy_key}] العملة فكّت ارتباطها بالبيتكوين (ارتباط {correlation:.2f}) — تم تجاوز فلتر السوق العام، والاعتماد على اتجاه العملة نفسها ({coin_trend}) بدلاً منه.")
            elif btc_trend and side_trend != btc_trend:
                db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي الإشارة: الصفقة ({result.side}) تعاكس اتجاه السوق العام (البيتكوين: {btc_trend}) — رفض وقائي.")
                db.increment_rejection_counter("market_alignment_filter_btc")
                return

        req_prob, learning_msg = learning.effective_threshold(result.symbol, result.side, settings, strategy_key=strategy_key)
        if learning_msg:
            db.add_log(learning_msg)

        if result.prob < req_prob:
            db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي الإشارة: نسبة النجاح ({result.prob}%) أقل من الحد المطلوب ({req_prob}%).")
            db.increment_rejection_counter("min_probability_filter")
            return

        if settings["is_volume_filter_enabled"]:
            v1h = [k.volume for k in k1h[-50:]]
            vol_avg = sum(v1h) / len(v1h) if v1h else 1.0
            vol_ratio = (v1h[-1] / vol_avg) if vol_avg > 0 else 1.0
            if vol_ratio < settings["min_volume_ratio"]:
                db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي الإشارة: معدل الحجم ({vol_ratio:.2f}x) أقل من الحد الأدنى.")
                db.increment_rejection_counter("volume_filter")
                return

        if settings["is_vwap_filter_enabled"]:
            last20 = k4h[-20:]
            v_sum = sum(k.volume for k in last20)
            vwap4h = ((sum(k.volume * (k.high + k.low + k.close) / 3.0 for k in last20) / v_sum)
                      if v_sum > 0 else last20[-1].close)
            last_price = k5m[-1].close
            if result.side == "Long" and last_price <= vwap4h:
                db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي إشارة صعود: السعر تحت خط VWAP.")
                db.increment_rejection_counter("vwap_filter")
                return
            if result.side == "Short" and last_price >= vwap4h:
                db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي إشارة هبوط: السعر فوق خط VWAP.")
                db.increment_rejection_counter("vwap_filter")
                return

        if settings["is_4h_buyers_filter_enabled"]:
            last20 = k4h[-20:]
            green = sum(k.volume for k in last20 if k.close > k.open)
            red = sum(k.volume for k in last20 if k.close < k.open)
            total = green + red
            buy_pct = int(green / total * 100) if total > 0 else 50
            if result.side == "Long" and buy_pct < settings["min_4h_buyers_percentage"]:
                db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي إشارة صعود: نسبة المشتريات ({buy_pct}%) غير كافية.")
                db.increment_rejection_counter("4h_buyers_filter")
                return
            if result.side == "Short" and (100 - buy_pct) < settings["min_4h_buyers_percentage"]:
                db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تخطي إشارة هبوط: نسبة المبيعات غير كافية.")
                db.increment_rejection_counter("4h_buyers_filter")
                return

        # منع التكرار: تجاهل الإشارة الجديدة إذا فيه صفقة (معلقة أو نشطة) بالفعل لنفس
        # العملة ونفس الاتجاه **ونفس الاستراتيجية** — استراتيجيات مختلفة تقدر تفتح
        # صفقات مستقلة على نفس العملة بنفس الوقت (مفيد لمقارنة أدائها الحقيقي ببعض)
        existing = db.get_active_or_pending_signal(result.symbol, result.side, strategy_key)
        if existing:
            status_ar = "نشطة" if existing["status"] == "ACTIVE" else "معلقة"
            db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تجاهل الإشارة الجديدة ({result.side}) لوجود صفقة {status_ar} بنفس الاستراتيجية بالفعل من نفس الاتجاه (بروبابيليتي {existing['probability']}%).")
            db.increment_rejection_counter("duplicate_active_signal")
            return

        # منع إعادة اكتشاف نفس النمط اللي أُغلق (رابحاً أو خاسراً) خلال آخر ساعات قليلة —
        # يحل مشكلة إعادة التقاط نفس شمعة الفريم الأعلى كإشارة "جديدة" فوراً بعد إغلاقها
        recent_dup = db.get_recent_similar_signal(result.symbol, result.side, strategy_key, result.entry_price)
        if recent_dup:
            db.add_log(f"⏳ [{symbol}/{strategy_key}] تم تجاهل إشارة مكررة — نفس النمط تقريباً ظهر بآخر ساعات (سعر دخول قريب من صفقة سابقة برقم #{recent_dup['id']}).")
            db.increment_rejection_counter("recent_duplicate_pattern")
            return

        strategy_display = strategy_label(strategy_key)
        db.add_log(f"🎯 [{symbol}] ({strategy_display}) تم رصد فرصة {result.side}! الاحتمالية: {result.prob}% | الجودة: {result.quality}")
        signal_id = db.add_signal({
            "symbol": result.symbol, "side": result.side, "entry_price": result.entry_price,
            "stop_loss": result.stop_loss, "take_profit": result.take_profit, "rr": result.rr,
            "probability": result.prob, "quality": result.quality, "behavior": result.behavior,
            "volume_analysis": result.volume_analysis, "strategy": strategy_key,
        })

        if settings["is_telegram_enabled"]:
            telegram_alert.send_signal_alert(
                settings["telegram_token"], settings["telegram_chat_ids"], result.symbol,
                result.side, result.entry_price, result.take_profit, result.stop_loss,
                result.prob, result.quality, result.behavior,
            )

        if settings["okx_is_auto_trading_enabled"]:
            self._execute_auto_trade(settings, result, signal_id)

    def _execute_auto_trade(self, settings: dict, result, signal_id: int):
        side_text = "buy" if result.side == "Long" else "sell"
        db.add_log(f"🤖 [التداول الآلي] جاري إرسال أمر إلى OKX ({result.symbol} | {side_text})...")
        try:
            available_balance = None
            if settings.get("okx_volume_type") == "PERCENTAGE":
                info = okx_client.fetch_account_info(
                    settings["okx_api_key"], settings["okx_api_secret"],
                    settings["okx_passphrase"], settings["okx_is_testnet"],
                )
                available_balance = info.get("available_balance")

            quantity_usdt = okx_client.calculate_order_quantity_usdt(
                settings, result.entry_price, result.stop_loss, available_balance,
            )

            success, message = okx_client.place_order(
                symbol=result.symbol, side=side_text, quantity_usdt=quantity_usdt,
                leverage=settings["okx_leverage"], margin_mode=settings["okx_margin_mode"],
                stop_loss=result.stop_loss, take_profit=result.take_profit,
                api_key=settings["okx_api_key"], api_secret=settings["okx_api_secret"],
                passphrase=settings["okx_passphrase"], is_testnet=settings["okx_is_testnet"],
                is_market_order=settings.get("is_instant_entry_enabled", True),
                is_max_leverage_enabled=settings.get("okx_is_max_leverage_enabled", False),
            )
            if success:
                db.add_log(f"✅ [التداول الآلي] تم تنفيذ الصفقة بنجاح: {message}")
            else:
                db.add_log(f"❌ [التداول الآلي] فشل تنفيذ الصفقة: {message}")
        except Exception as e:
            db.add_log(f"❌ [التداول الآلي] خطأ استثنائي: {e}")

    def _update_signal_prices(self, settings: dict):
        open_signals = db.get_open_signals()
        if not open_signals:
            return
        exchange = okx_client if settings["exchange"] == "okx" else binance_client
        prices = exchange.fetch_all_prices()
        if not prices:
            return

        for signal in open_signals:
            live_price = prices.get(signal["symbol"])
            if not live_price or live_price <= 0:
                continue

            new_status = signal["status"]
            changed = False

            if signal["status"] == "PENDING":
                if signal["side"] == "Long":
                    if live_price <= signal["entry_price"]:
                        new_status, changed = "ACTIVE", True
                    elif live_price >= signal["take_profit"] and settings["is_cancel_if_exceeds_target_enabled"]:
                        new_status, changed = "CANCELLED", True
                else:
                    if live_price >= signal["entry_price"]:
                        new_status, changed = "ACTIVE", True
                    elif live_price <= signal["take_profit"] and settings["is_cancel_if_exceeds_target_enabled"]:
                        new_status, changed = "CANCELLED", True
            elif signal["status"] == "ACTIVE":
                # قياس التراجع اللحظي من نقطة الدخول (مو الربح/الخسارة النهائي) —
                # يقيس "كم رجع السعر ضدنا" أثناء الصفقة، مفيد لتقييم قوة نقطة الدخول
                # نفسها بمعزل عن نتيجة الصفقة بالنهاية (رابحة أو خاسرة)
                entry_price = signal["entry_price"]
                if entry_price and entry_price > 0:
                    if signal["side"] == "Long":
                        adverse_pct = max(0.0, (entry_price - live_price) / entry_price * 100.0)
                        favorable_pct = max(0.0, (live_price - entry_price) / entry_price * 100.0)
                    else:
                        adverse_pct = max(0.0, (live_price - entry_price) / entry_price * 100.0)
                        favorable_pct = max(0.0, (entry_price - live_price) / entry_price * 100.0)
                    db.update_max_drawdown_if_worse(signal["id"], adverse_pct)
                    db.update_max_favorable_if_better(signal["id"], favorable_pct)

                    # 🎯 وقف التعادل التلقائي (Breakeven Stop) — إصلاح مباشر مبني على
                    # تحليل بيانات إنتاج فعلية: نسبة كبيرة من الخسائر (37-48% حسب
                    # الاستراتيجية) كانت أصلاً صفقات رابحة قبل ما تنعكس وتضرب الوقف
                    # الأصلي كاملاً. بمجرد ما الربح العائم يعادل المخاطرة الأصلية (1R)،
                    # ننقل الوقف لنقطة الدخول — أي انعكاس لاحق يصير "تعادل" لا خسارة.
                    initial_risk = signal.get("initial_risk_pct") or 0
                    if (settings.get("is_breakeven_stop_enabled", True) and not signal.get("breakeven_activated")
                            and initial_risk > 0 and favorable_pct >= initial_risk):
                        db.activate_breakeven(signal["id"], entry_price)
                        signal["stop_loss"] = entry_price  # نحدّث النسخة المحلية بنفس هذي الدورة أيضاً
                        db.add_log(f"🎯 [{signal['symbol']}] تفعيل وقف التعادل تلقائياً — الصفقة حققت ربح 1R، الوقف انتقل لنقطة الدخول لحماية الأرباح.")

                if signal["side"] == "Long":
                    if live_price <= signal["stop_loss"]:
                        # لو الوقف الحالي هو وقف التعادل المُفعَّل (مو الوقف الأصلي)، هذي
                        # "تعادل" وقائي حمى رأس المال، مو خسارة حقيقية بالتحليل نفسه
                        new_status = "BREAKEVEN" if signal.get("breakeven_activated") else "HIT_SL"
                        changed = True
                    elif live_price >= signal["take_profit"]:
                        new_status, changed = "HIT_TP", True
                else:
                    if live_price >= signal["stop_loss"]:
                        new_status = "BREAKEVEN" if signal.get("breakeven_activated") else "HIT_SL"
                        changed = True
                    elif live_price <= signal["take_profit"]:
                        new_status, changed = "HIT_TP", True

            already_notified = signal["last_notified_status"] == new_status
            transition_key = f"{signal['id']}_{new_status}"
            should_notify = changed and not already_notified and transition_key not in self._notified_transitions

            db.update_signal_status(signal["id"], new_status, live_price,
                                     new_status if should_notify else signal["last_notified_status"])

            if should_notify:
                self._notified_transitions.add(transition_key)
                db.add_log(f"🔄 [{signal['symbol']}] تغيرت حالة الصفقة إلى {new_status} (السعر الحالي: {live_price})")
                if settings["is_telegram_enabled"]:
                    telegram_alert.send_status_alert(
                        settings["telegram_token"], settings["telegram_chat_ids"],
                        signal["symbol"], signal["side"], new_status, live_price,
                    )


scanner_state = ScannerState()
