"""
استراتيجية الانفجار الموجّه بـ ICT (ICT-Guided Entry) — تختلف عن "التأكيد المزدوج"
بفرق جوهري واحد: **لا يوجد شرط رفض**. كل إشارة يلقاها الانفجار السعري تتحول لصفقة
فعلية دائماً، بس ICT يحاول يحسّن نقطة الدخول/الاتجاه لو قدر يلقى تأكيد.

  الخطوة 1: تشغيل "الانفجار السعري" كمُطلِق — لو ما لقى شي، ننتهي (نفس المنطق).

  الخطوة 2: تشغيل "النمط الذكي (ICT)" على نفس البيانات:
    - لو ICT لقى نقطة دخول واضحة (بأي اتجاه، حتى لو عاكس الانفجار السعري) → نعتمد
      اتجاهه ونقاطه هو، لأنه أدق.
    - لو ICT ما لقى شي (شروطه الصارمة ما تحققت بهذي اللحظة) → **لا نرفض الصفقة**،
      بل نعتمد نقاط الانفجار السعري الأصلية كما هي، لأنه أصلاً كان فيه حدث حقيقي.

هذا يعني عملياً: عدد الإشارات هنا = نفس عدد إشارات الانفجار السعري بالضبط (كل إشارة
تمر، ولا وحدة تُرفض)، لكن جزء منها (كلما توفر تأكيد ICT) بيكون بنقاط دخول/اتجاه أدق
من ICT بدل الانفجار السعري الخام.
"""
from typing import Optional

from .analyzer import analyze, AnalysisResult, MarketMicrostructure
from .ict_strategy import analyze_ict_smart_sweep


def analyze_ict_guided_entry(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                              micro: Optional[MarketMicrostructure] = None) -> Optional[AnalysisResult]:
    # الخطوة 1: الانفجار السعري كمُطلِق — بدونه ما فيه أي شي نحلله
    breakout_result = analyze(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro)
    if breakout_result is None:
        return None

    # الخطوة 2: نحاول نحسّن الدخول عبر ICT، بدون ما نرفض الصفقة لو ما توفر
    ict_result = analyze_ict_smart_sweep(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro)

    if ict_result is not None:
        same_direction = ict_result.side == breakout_result.side
        note = (f"بنفس اتجاه الانفجار السعري ({breakout_result.side}) — تأكيد إضافي." if same_direction
                else f"بعكس اتجاه الانفجار السعري ({breakout_result.side} → {ict_result.side}) — "
                     f"تم اعتماد اتجاه ICT الأدق لاحتمال فخ سيولة.")
        behavior = (
            f"🎯 انفجار موجّه: رُصد حدث عبر الانفجار السعري، وICT قدر يحدد نقطة دخول دقيقة {note}\n\n"
            f"⚡ [الحدث]: {breakout_result.behavior}\n\n"
            f"🧠 [التوجيه]: {ict_result.behavior}"
        )
        return AnalysisResult(
            symbol=symbol, trend=ict_result.trend, dt="", prob=ict_result.prob, price=ict_result.price,
            atr=ict_result.atr, side=ict_result.side,
            entry_price=ict_result.entry_price, stop_loss=ict_result.stop_loss, take_profit=ict_result.take_profit,
            rr=ict_result.rr, quality=ict_result.quality, conf=ict_result.prob,
            behavior=behavior, volume_analysis="انفجار سعري موجّه بنقطة دخول ICT الدقيقة",
            low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
        )

    # ICT ما لقى نقطة واضحة بهذي اللحظة — نعتمد الانفجار السعري كما هو، بدون رفض
    behavior = (
        f"🎯 انفجار موجّه: رُصد حدث عبر الانفجار السعري، لكن ICT لم يجد نقطة دخول أدق "
        f"بهذي اللحظة (شروطه لم تكتمل) — تم اعتماد نقاط الانفجار السعري الأصلية مباشرة.\n\n"
        f"⚡ [التحليل الكامل]: {breakout_result.behavior}"
    )
    return AnalysisResult(
        symbol=symbol, trend=breakout_result.trend, dt="", prob=breakout_result.prob, price=breakout_result.price,
        atr=breakout_result.atr, side=breakout_result.side,
        entry_price=breakout_result.entry_price, stop_loss=breakout_result.stop_loss,
        take_profit=breakout_result.take_profit, rr=breakout_result.rr,
        quality=breakout_result.quality, conf=breakout_result.prob,
        behavior=behavior, volume_analysis="انفجار سعري موجّه (بدون تأكيد ICT هذي المرة)",
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
