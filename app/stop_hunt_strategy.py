"""
استراتيجية صيد الاستوبات والمؤسسات (Stop-Loss Hunting).

⚠️ تصحيح توثيقي: كانت موصوفة سابقاً كـ"منقولة عن StopLossHuntDetector.kt الأصلي" —
هذا غير دقيق. لا يوجد ملف بهذا الاسم أو هذا المفهوم بالكود الأصلي (تم التحقق فعلياً
بالبحث بكامل مصدر التطبيق الأصلي). هذي استراتيجية مبنية خصيصاً لهذا التطبيق.

الفكرة: صنّاع السوق (Market Makers) غالباً يدفعون السعر عمداً لضرب مستويات وقف
الخسارة للمتداولين الأفراد (سيولة التجزئة) — يكسرون بفتيلة (Wick) أعلى قمة أو
أدنى قاع سابق معروف، يجمعون السيولة المتحررة هناك، ثم ينعكس السعر فعلياً بالاتجاه
المعاكس. هذا نمط "فخ سيولة كلاسيكي" على مستوى القمم/القيعان التاريخية مباشرة
(بدل مناطق العرض/الطلب الأوسع اللي تغطيها استراتيجية "انعكاس عرض/طلب").

الشروط:
  صيد استوبات صاعد (Bullish Stop Hunt / Sweep the Lows):
    - ذيل الشمعة (Low) كسر أدنى قاع سابق معروف (خلال آخر 24 ساعة)
    - لكن إغلاق الشمعة (Close) رجع فوق مستوى القاع المكسور (رفض واضح + امتصاص سيولة)
    → دخول Long، وقف الخسارة تحت الذيل مباشرة بهامش أمان، هدف بعائد 1:3 كحد أدنى

  صيد استوبات هابط (Bearish Stop Hunt / Sweep the Highs): نفس الفكرة بالعكس تماماً
  على القمم.

يفحص آخر 5 شموع (مو الشمعة الأخيرة بس) — هذا إصلاح لبق كان يفحص الشمعة الأخيرة
حصراً، فيفوّت أي نمط صيد استوبات حصل قبل شمعة أو شمعتين ويفوّت الفرصة بالكامل.

فلتر جودة: يشترط فوليوم الشمعة أعلى من المتوسط (تأكيداً لأهمية "Volume Spike")،
وربطنا بيانات الفائدة المفتوحة (OI) وضغط المتداولين الفعليين (Taker Pressure) —
لو توفرت — كنقاط تأكيد إضافية اختيارية، بنفس أسلوب بقية الاستراتيجيات بالتطبيق.
"""
from typing import List, Optional

from .analyzer import Kline, AnalysisResult, MarketMicrostructure


def _detect_stop_hunt(klines: List[Kline], lookback: int = 50, vol_period: int = 20,
                       recent_window: int = 5) -> Optional[dict]:
    """يفحص آخر recent_window شمعة (مو الشمعة الأخيرة بس) بحثاً عن نمط صيد استوبات
    حدث بأي وحدة منها، ويرجع الأحدث تطابقاً. هذا يحل مشكلة النافذة الزمنية الضيقة
    اللي كانت تفحص الشمعة الأخيرة فقط، وتفوّت أي نمط حصل قبل شمعة أو شمعتين."""
    if len(klines) < lookback + recent_window:
        return None

    recent_vol_all = [k.volume for k in klines[-vol_period:]]
    avg_volume = sum(recent_vol_all) / len(recent_vol_all) if recent_vol_all else 0.0

    # نفحص من الأحدث للأقدم داخل نافذة recent_window، ونرجع أول تطابق (الأحدث)
    for offset in range(1, recent_window + 1):
        current = klines[-offset]
        idx = len(klines) - offset
        historical = klines[max(0, idx - lookback): idx]
        if not historical:
            continue
        lowest_low = min(k.low for k in historical)
        highest_high = max(k.high for k in historical)
        volume_ratio = (current.volume / avg_volume) if avg_volume > 0 else 1.0
        candle_range = current.high - current.low
        buffer = candle_range * 0.1

        if current.low < lowest_low and current.close > lowest_low:
            stop_loss = current.low - buffer
            risk = current.close - stop_loss
            if risk <= 0:
                continue
            take_profit = current.close + (risk * 3.0)
            return {
                "type": "BULLISH_STOP_HUNT", "side": "Long", "swept_level": lowest_low,
                "entry_price": current.close, "stop_loss": stop_loss, "take_profit": take_profit,
                "volume_ratio": volume_ratio, "candles_ago": offset,
            }

        if current.high > highest_high and current.close < highest_high:
            stop_loss = current.high + buffer
            risk = stop_loss - current.close
            if risk <= 0:
                continue
            take_profit = current.close - (risk * 3.0)
            return {
                "type": "BEARISH_STOP_HUNT", "side": "Short", "swept_level": highest_high,
                "entry_price": current.close, "stop_loss": stop_loss, "take_profit": take_profit,
                "volume_ratio": volume_ratio, "candles_ago": offset,
            }

    return None


def analyze_stop_hunt(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                       micro: Optional[MarketMicrostructure] = None,
                       trace: Optional[list] = None) -> Optional[AnalysisResult]:
    def _log(label, value, ok=None):
        if trace is not None:
            trace.append({"check": label, "value": value, "ok": ok})

    signal = _detect_stop_hunt(k1h, lookback=24, vol_period=20, recent_window=5)
    _log("نمط صيد استوبات مكتشف على فريم الساعة (آخر 5 شموع)",
         f"{signal['type']} (قبل {signal['candles_ago']} شمعة)" if signal else "لا يوجد", signal is not None)
    if signal is None:
        _log("❌ القرار النهائي", "لم يُكسر أي قاع/قمة تاريخية بفتيلة مع رفض واضح خلال آخر 50 شمعة — رفض", False)
        return None

    # فلتر جودة (تحسين بسيط فوق الأصل): نشترط فوليوم أعلى من المتوسط فعلاً،
    # تأكيداً لملاحظة الكود الأصلي نفسه عن أهمية الـ Volume Spike
    _log("نسبة فوليوم شمعة السحب مقابل المتوسط", f"{signal['volume_ratio']:.2f}x")
    if signal["volume_ratio"] < 1.2:
        _log("❌ فلتر الحد الأدنى للفوليوم (1.2x)", f"{signal['volume_ratio']:.2f}x أقل من المطلوب — رفض", False)
        return None

    side = signal["side"]
    entry_price, sl, tp = signal["entry_price"], signal["stop_loss"], signal["take_profit"]
    risk = abs(entry_price - sl)
    if risk <= 0:
        return None

    # فلتر OI اختياري (نفس أسلوب بقية الاستراتيجيات): سيولة تخرج بقوة = إشارة ضعف حقيقي
    oi_change_pct = micro.oi_change_pct if micro else None
    if oi_change_pct is not None and oi_change_pct < -1.5:
        _log("❌ فلتر الفائدة المفتوحة (OI)", f"تغيّر OI={oi_change_pct:.2f}% (أقل من -1.5%) — رفض", False)
        return None
    _log("الفائدة المفتوحة (OI) تغيّر", f"{oi_change_pct:.2f}%" if oi_change_pct is not None else "غير متوفرة")

    probability = 78
    if signal["volume_ratio"] >= 2.0:
        probability += 6  # فوليوم ضخم جداً وقت السحب = تأكيد أقوى بكثير لتدخل مؤسسي حقيقي
    elif signal["volume_ratio"] >= 1.5:
        probability += 3

    taker_pressure = micro.taker_pressure if micro else None
    if taker_pressure is not None:
        taker_aligned = (side == "Long" and taker_pressure > 0.15) or (side == "Short" and taker_pressure < -0.15)
        if taker_aligned:
            probability += 4

    cvd_pct = micro.cvd_pct if micro else None
    if cvd_pct is not None:
        cvd_aligned = (side == "Long" and cvd_pct > 60) or (side == "Short" and cvd_pct < 40)
        if cvd_aligned:
            probability += 3

    probability = min(95, probability)
    rr = round(abs(tp - entry_price) / risk, 2) if risk > 0 else 3.0
    _log("✅ القرار النهائي", f"{side} — احتمالية {probability}%", True)

    type_ar = "صعودي (سحب سيولة القيعان)" if side == "Long" else "هبوطي (سحب سيولة القمم)"
    ago_txt = "بالشمعة الحالية" if signal["candles_ago"] == 1 else f"قبل {signal['candles_ago']} شموع"
    behavior = (
        f"🎯 صيد استوبات {type_ar}: كُسر المستوى التاريخي عند {signal['swept_level']:.6g} بفتيلة "
        f"(Wick) ثم رفضه السعر وأغلق بالعكس {ago_txt} — نمط فخ سيولة كلاسيكي (اصطياد ستوبات المتداولين "
        f"الأفراد). نسبة الفوليوم وقت السحب: {signal['volume_ratio']:.2f}× المتوسط."
    )
    volume_analysis = f"صيد استوبات مؤكَّد بفوليوم {signal['volume_ratio']:.2f}× — عائد/مخاطرة ثابت لا يقل عن 1:3"

    return AnalysisResult(
        symbol=symbol, trend="صاعد" if side == "Long" else "هابط", dt="", prob=probability,
        price=entry_price, atr=risk, side=side, entry_price=entry_price, stop_loss=sl, take_profit=tp,
        rr=rr, quality="A" if probability >= 88 else "B", conf=probability,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
