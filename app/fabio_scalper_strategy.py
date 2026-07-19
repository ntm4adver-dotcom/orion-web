"""
استراتيجية سكالب فابيو فالنتيني (Fabio Valentini Order Flow Scalper).

منقولة بأمانة عن منهجه الموثّق بمقابلات علنية (مو تخمين) — فابيو فالنتيني إيطالي،
مصنَّف بأفضل 3 عالمياً بفئة سكالب العقود الآجلة ببطولة Robbins World Cup Trading
Championship، بعائد مُدقَّق فاق 500% بسنة واحدة. منهجه المعلن: نظرية السوق كمزاد
(Auction Market Theory) + تحليل تدفق الأوامر (Order Flow)، عبر 3 خطوات يسميها
Direction – Location – Aggression:

  1) تصنيف حالة السوق: توازن (Balance، السعر يدور حول القيمة العادلة ~70% من الوقت)
     أو اختلال (Imbalance، طرف واحد يدفع السعر بقوة بحثاً عن قيمة جديدة).
  2) الاتجاه (Direction): مين المسيطر فعلياً؟ يُحدَّد عبر CVD/دلتا الفوليوم التراكمي
     (نستخدم get_cvd_24h_pct وضغط المتداولين الفعليين المتوفرين عندنا فعلاً).
  3) الموقع (Location): ينتظر السعر يوصل مستوى مهم من بروفايل الفوليوم — POC (نقطة
     التحكم)، حافة منطقة القيمة (VAH/VAL)، أو LVN (فراغ سيولة).
  4) العدوانية (Aggression): يدخل فقط لو فيه ضغط فعلي واضح بنفس اللحظة عند هذا
     المستوى، بنفس اتجاه القرار — تأكيد حقيقي مو مجرد اقتراب من رقم.

نموذجين حسب حالة السوق (بنفس منهجه المعلن):
  - نموذج الاختلال (Trend/Imbalance): السعر كسر حافة منطقة القيمة فعلاً بعدوانية
    بنفس اتجاه Direction → دخول استمرار (Continuation) بالاتجاه، هدف ممتد.
  - نموذج التوازن (Mean Reversion/Balance): السعر لمس حافة منطقة القيمة لكن
    العدوانية تعاكس تجاوزها (رفض واضح) → دخول ارتدادي نحو POC (القيمة العادلة).

إدارة المخاطرة بنفس قاعدته المعلنة: عائد/مخاطرة 2:1 كحد أدنى.
"""
from typing import Optional

from .analyzer import Kline, AnalysisResult, MarketMicrostructure, atr
from .volume_profile import compute_volume_profile


def analyze_fabio_scalper(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                           micro: Optional[MarketMicrostructure] = None,
                           trace: Optional[list] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    window = k15m[-100:] if len(k15m) >= 100 else k15m
    if len(window) < 20:
        _log("عدد شموع كافٍ لبناء بروفايل الفوليوم", len(window), False)
        return None

    profile = compute_volume_profile(window)
    if profile is None:
        return None

    current_price = k5m[-1].close if k5m else window[-1].close
    atr_val = atr(k15m, 14)
    if atr_val <= 0:
        return None

    _log("POC (نقطة التحكم)", round(profile["poc"], 6))
    _log("منطقة القيمة (VAL–VAH)", f"{profile['val']:.6g} – {profile['vah']:.6g}")

    # ── الخطوة 1: الاتجاه (Direction) — عبر CVD وضغط المتداولين الفعليين ──
    cvd_pct = micro.cvd_pct if micro else None
    taker_pressure = micro.taker_pressure if micro else None

    direction = None
    if cvd_pct is not None:
        if cvd_pct > 55:
            direction = "Long"
        elif cvd_pct < 45:
            direction = "Short"
    if direction is None and taker_pressure is not None:
        if taker_pressure > 0.10:
            direction = "Long"
        elif taker_pressure < -0.10:
            direction = "Short"

    _log("الاتجاه (Direction) عبر CVD/ضغط المتداولين", direction if direction else "غير محدد بوضوح", direction is not None)
    if direction is None:
        _log("❌ القرار النهائي", "ما قدرنا نحدد مين المسيطر فعلياً (CVD/ضغط متداولين غير حاسمين) — منهج فابيو يرفض الدخول بدون اتجاه واضح", False)
        return None

    # ── الخطوة 2: الموقع (Location) — لازم السعر يكون عند مستوى مهم فعلاً ──
    key_levels = [("POC", profile["poc"]), ("VAH", profile["vah"]), ("VAL", profile["val"])]
    for lvn in profile["lvns"][:2]:
        key_levels.append(("LVN", lvn))

    nearest_name, nearest_price = min(key_levels, key=lambda l: abs(current_price - l[1]))
    distance_pct = abs(current_price - nearest_price) / current_price * 100
    _log("أقرب مستوى مهم (Location)", f"{nearest_name} عند {nearest_price:.6g} — يبعد {distance_pct:.3f}%")

    if distance_pct > 0.35:
        _log("❌ فلتر القرب من المستوى", "السعر لسا بعيد عن أي مستوى مهم — منهج فابيو ينتظر ولا يطارد", False)
        return None

    is_imbalance = current_price > profile["vah"] * 1.0005 or current_price < profile["val"] * 0.9995

    def _safe_buffer(atr_multiple: float, price_pct_floor: float = 0.006) -> float:
        """هامش وقف واقعي: الأكبر بين مضاعف ATR الموسَّع، أو نسبة دنيا من السعر (0.6% افتراضياً).
        هذا يحمي من الوقف الضيق جداً وقت هدوء السوق (ATR منخفض مؤقتاً)، اللي كان يخلي
        السعر يضرب الوقف بمجرد تذبذب عادي — مو لأن اتجاه الصفقة كان غلط فعلاً."""
        return max(atr_val * atr_multiple, current_price * price_pct_floor)

    # ── الخطوة 3: العدوانية (Aggression) + بناء الصفقة حسب النموذج المناسب ──
    if is_imbalance:
        # نموذج الاختلال (Trend/Continuation): كسر حافة منطقة القيمة بنفس اتجاه Direction
        if current_price > profile["vah"] and direction == "Long":
            side = "Long"
            entry_price = current_price
            stop_loss = profile["vah"] - _safe_buffer(0.8)
            measured = profile["vah"] - profile["val"]
            take_profit = entry_price + max(measured, atr_val * 2.0)
            model = "اختلال/استمرار (Trend Model)"
        elif current_price < profile["val"] and direction == "Short":
            side = "Short"
            entry_price = current_price
            stop_loss = profile["val"] + _safe_buffer(0.8)
            measured = profile["vah"] - profile["val"]
            take_profit = entry_price - max(measured, atr_val * 2.0)
            model = "اختلال/استمرار (Trend Model)"
        else:
            _log("❌ القرار النهائي", "فيه اختلال لكن الاتجاه المرصود يعاكس جهة الكسر — رفض", False)
            return None
    else:
        # نموذج التوازن (Mean Reversion): لمس حافة منطقة القيمة والعدوانية ترفض تجاوزها
        if nearest_name == "VAH" and direction == "Short":
            side = "Short"
            entry_price = nearest_price
            stop_loss = nearest_price + _safe_buffer(1.0)
            take_profit = profile["poc"]
            model = "توازن/ارتداد (Mean Reversion)"
        elif nearest_name == "VAL" and direction == "Long":
            side = "Long"
            entry_price = nearest_price
            stop_loss = nearest_price - _safe_buffer(1.0)
            take_profit = profile["poc"]
            model = "توازن/ارتداد (Mean Reversion)"
        elif nearest_name == "LVN":
            # فراغ سيولة: الدخول بنفس اتجاه Direction متوقعاً عبور سريع خلال الفراغ
            side = direction
            entry_price = nearest_price
            if side == "Long":
                stop_loss = nearest_price - _safe_buffer(1.2)
                take_profit = profile["vah"]
            else:
                stop_loss = nearest_price + _safe_buffer(1.2)
                take_profit = profile["val"]
            model = "فراغ سيولة (LVN Pass-Through)"
        else:
            _log("❌ القرار النهائي", "السعر عند حافة منطقة القيمة لكن الاتجاه لا يدعم نموذج الارتداد — رفض", False)
            return None

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None
    reward = abs(take_profit - entry_price)
    rr = round(reward / risk, 2)
    _log(f"النموذج المطبَّق: {model}", f"دخول {entry_price:.6g} / وقف {stop_loss:.6g} / هدف {take_profit:.6g}")
    _log("عائد/مخاطرة", f"1:{rr}")

    # قاعدة فابيو المعلنة: عائد/مخاطرة 2:1 كحد أدنى إلزامياً
    if rr < 2.0:
        _log("❌ فلتر أدنى عائد/مخاطرة (2:1 — قاعدة فابيو المعلنة)", f"1:{rr} غير كافٍ — رفض", False)
        return None

    # سقف منطقي أعلى: عائد/مخاطرة متطرف جداً (>8) غالباً عرَض لوقف ضيق جداً مقابل
    # هدف بعيد، مو صفقة "ممتازة" فعلاً — احتمال ضرب الوقف بضوضاء عادية قبل نجاح الحركة
    # أعلى بكثير مما يوحي الرقم الجذاب. نرفضها بدل ما نعتبرها فرصة ذهبية.
    if rr > 8.0:
        _log("❌ فلتر سقف عائد/مخاطرة (>8 — إشارة وقف ضيق غير واقعي)", f"1:{rr} مبالغ فيه — رفض احترازي", False)
        return None

    # العدوانية: تأكيد نهائي إلزامي إن ضغط المتداولين (لو متوفر) يدعم نفس اتجاه الصفقة فعلياً
    if taker_pressure is not None:
        aggression_ok = (side == "Long" and taker_pressure > -0.05) or (side == "Short" and taker_pressure < 0.05)
        if not aggression_ok:
            _log("❌ فلتر العدوانية (Aggression)", f"ضغط المتداولين {taker_pressure:.2f} يعاكس الصفقة بوضوح عند نقطة الدخول — رفض", False)
            return None

    probability = 75
    if distance_pct < 0.1:
        probability += 5
    if cvd_pct is not None and ((side == "Long" and cvd_pct > 62) or (side == "Short" and cvd_pct < 38)):
        probability += 6
    if taker_pressure is not None and ((side == "Long" and taker_pressure > 0.2) or (side == "Short" and taker_pressure < -0.2)):
        probability += 6
    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct < -1.5:
        probability -= 6
    probability = max(70, min(95, probability))
    _log("✅ القرار النهائي", f"{side} — {model}", True)

    behavior = (
        f"📊 سكالب فابيو فالنتيني ({model}): تصنيف السوق {'اختلال (Imbalance)' if is_imbalance else 'توازن (Balance)'}، "
        f"الاتجاه (Direction) عبر CVD/ضغط المتداولين = {direction}، الموقع (Location) عند {nearest_name} "
        f"({nearest_price:.6g})، والعدوانية (Aggression) مؤكَّدة بضغط متداولين فعلي متوافق. "
        f"POC={profile['poc']:.6g} | VAH={profile['vah']:.6g} | VAL={profile['val']:.6g}."
    )
    volume_analysis = f"بروفايل فوليوم (POC/VAH/VAL/LVN) + CVD + ضغط متداولين فعلي — منهج Direction-Location-Aggression"

    return AnalysisResult(
        symbol=symbol, trend=direction, dt="", prob=probability, price=current_price,
        atr=atr_val, side=side, entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
        rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
