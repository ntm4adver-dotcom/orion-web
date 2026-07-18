"""
استراتيجية التأكيد المزدوج (Hybrid Confirmation) — تجمع الاستراتيجيتين معاً بتسلسل واحد:

  الخطوة 1: تشغيل "الانفجار السعري" (Explosive Breakout Hunter) أولاً كمُطلِق (Trigger).
            لو ما لقى أي فرصة، ننتهي فوراً — ما فيه شي نؤكده.

  الخطوة 2: لو لقى فرصة، نشغّل "النمط الذكي لسحب السيولة" (ICT / Smart Money Concepts)
            على نفس العملة ونفس البيانات، كطبقة تأكيد مستقلة.

  الخطوة 3: نقبل الإشارة فقط لو الاستراتيجيتين اتفقتا على نفس الاتجاه (Long/Long أو
            Short/Short). أي تعارض أو عدم تأكيد من ICT = رفض الفرصة بالكامل.

  الخطوة 4: نقاط الدخول والوقف والهدف النهائية تُؤخذ من تحليل ICT الدقيق (فيبوناتشي
            الذهبية + FVG + Volume Node)، لأنها أدق بحساب مستويات الدخول/الخروج من
            منطق الانفجار السعري (اللي يعتمد على سعر الإغلاق اللحظي كنقطة دخول).
            الاحتمالية النهائية = متوسط الاستراتيجيتين + مكافأة تأكيد مزدوج.

هذا يعني عملياً: عدد الإشارات هنا أقل بكثير من كل استراتيجية لحالها (لأنها تحتاج
توافق الاثنتين معاً)، لكن من المتوقع أنها أعلى جودة لأنها مرّت بفلترين مستقلين متتاليين.
"""
from typing import Optional

from .analyzer import analyze, AnalysisResult, MarketMicrostructure
from .ict_strategy import analyze_ict_smart_sweep


def analyze_hybrid_confirmation(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                                 micro: Optional[MarketMicrostructure] = None) -> Optional[AnalysisResult]:
    # الخطوة 1: الانفجار السعري كمُطلِق أولي
    breakout_result = analyze(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro)
    if breakout_result is None:
        return None

    # الخطوة 2: تأكيد مستقل عبر النمط الذكي (ICT) على نفس البيانات
    ict_result = analyze_ict_smart_sweep(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro)
    if ict_result is None:
        return None  # ما تأكدت الفرصة بطبقة التحليل الثانية

    # الخطوة 3: لازم الاتجاهين متوافقين تماماً
    if ict_result.side != breakout_result.side:
        return None

    # الخطوة 4: النقاط النهائية من تحليل ICT الدقيق + احتمالية مدمجة مع مكافأة تأكيد مزدوج
    combined_prob = round((breakout_result.prob + ict_result.prob) / 2) + 5
    combined_prob = min(97, combined_prob)

    behavior = (
        f"🔗 تأكيد مزدوج: رُصدت فرصة انفجار سعري أولية (ثقة {breakout_result.prob}%)، "
        f"ثم تأكدت مستقلاً عبر تحليل النمط الذكي (ICT) بنفس الاتجاه ({ict_result.side}) "
        f"بثقة {ict_result.prob}%. نقاط الدخول/الوقف/الهدف مأخوذة من تحليل ICT الدقيق.\n\n"
        f"⚡ [مُطلِق الانفجار السعري]: {breakout_result.behavior}\n\n"
        f"🧠 [تأكيد النمط الذكي]: {ict_result.behavior}"
    )
    volume_analysis = "تأكيد مزدوج مستقل: انفجار سعري (Breakout) + سحب سيولة ذكي (ICT/SMC)"

    return AnalysisResult(
        symbol=symbol, trend=ict_result.trend, dt="", prob=combined_prob, price=ict_result.price,
        atr=ict_result.atr, side=ict_result.side,
        entry_price=ict_result.entry_price, stop_loss=ict_result.stop_loss, take_profit=ict_result.take_profit,
        rr=ict_result.rr, quality="A" if combined_prob >= 88 else "B", conf=combined_prob,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
