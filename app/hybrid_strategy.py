"""
استراتيجية التأكيد المزدوج (Hybrid Confirmation) — تجمع الاستراتيجيتين معاً بتسلسل واحد:

  الخطوة 1: تشغيل "الانفجار السعري" (Explosive Breakout Hunter) أولاً — لكن ليس كتوقّع
            اتجاه نثق فيه، بل فقط كـ"كاشف حدث" (Event Trigger): هل يوجد زخم/كسر سيولة
            حقيقي يحصل على هذي العملة الآن؟ لو ما لقى شي إطلاقاً، ننتهي فوراً.

  الخطوة 2: لو لقى حدثاً (بأي اتجاه)، نشغّل "النمط الذكي (ICT / Smart Money Concepts)"
            على نفس البيانات ليحدد **الاتجاه الحقيقي الصحيح** ونقاط الدخول/الخروج.

  ملاحظة مهمة عن سبب هذا التصميم: كثير من "الاختراقات" اللي يرصدها الانفجار السعري تكون
  فعلياً "فخ سيولة" (Liquidity Grab / Stop Hunt) — كسر وهمي بجهة معينة يجذب المتداولين،
  ثم ينعكس السعر فعلياً للجهة المعاكسة تماماً. لذلك **لا نشترط** توافق الاتجاهين، بل
  نثق باتجاه ICT النهائي كقرار حاسم (سواء وافق اتجاه الانفجار السعري أو عاكسه)، لأن ICT
  مصمم خصيصاً لكشف هذا النوع من الانعكاسات بعد السحب.

  الخطوة 3: نقاط الدخول والوقف والهدف النهائية تُؤخذ دائماً من تحليل ICT الدقيق.
            الاحتمالية = احتمالية ICT، مع مكافأة إضافية بسيطة لو الاتجاهين اتفقا
            (تأكيد مزدوج حقيقي)، أو ملاحظة توضيحية لو تعاكسا (نمط فخ سيولة).

هذا يعني عملياً: الانفجار السعري هنا دوره "منبّه" بس (هل يستاهل نحلل هذي العملة بعمق
الآن أو لا)، والقرار الفعلي بالاتجاه ونقاط الدخول/الخروج بالكامل بيد تحليل ICT.
"""
from typing import Optional

from .analyzer import analyze, AnalysisResult, MarketMicrostructure
from .ict_strategy import analyze_ict_smart_sweep


def analyze_hybrid_confirmation(symbol: str, k4h, k1h, k15m, k5m, k_daily,
                                 micro: Optional[MarketMicrostructure] = None) -> Optional[AnalysisResult]:
    # الخطوة 1: الانفجار السعري كـ"كاشف حدث" فقط — أي اتجاه يكفي لإثبات وجود زخم حقيقي
    breakout_result = analyze(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro)
    if breakout_result is None:
        return None  # ما فيه أي حدث سيولة/زخم يستاهل التحليل العميق أصلاً

    # الخطوة 2: ICT يحدد الاتجاه الحقيقي ونقاط الدخول/الخروج — قراره نهائي وحاسم
    ict_result = analyze_ict_smart_sweep(symbol, k4h, k1h, k15m, k5m, k_daily, micro=micro)
    if ict_result is None:
        return None  # فيه حدث لكن ICT ما لقى نقطة دخول واضحة وموثوقة بعده

    same_direction = ict_result.side == breakout_result.side

    if same_direction:
        # تأكيد مزدوج حقيقي: نفس الاتجاه من تحليلين مستقلين
        combined_prob = min(97, ict_result.prob + 5)
        direction_note = (f"✅ تأكيد مزدوج: نفس اتجاه الانفجار السعري ({breakout_result.side}) — "
                           f"إشارة قوة إضافية حقيقية.")
    else:
        # نمط فخ سيولة محتمل: الانفجار السعري أشار لاتجاه، لكن ICT كشف الاتجاه
        # الحقيقي المعاكس (كسر وهمي ثم انعكاس) — نثق بقرار ICT النهائي
        combined_prob = ict_result.prob
        direction_note = (f"🔄 نمط فخ سيولة محتمل: الانفجار السعري أشار إلى {breakout_result.side}، "
                           f"لكن تحليل ICT كشف أن الاتجاه الحقيقي بعد سحب السيولة هو "
                           f"{ict_result.side} فعلياً — تم اعتماد قرار ICT النهائي.")

    behavior = (
        f"🔗 تأكيد مزدوج: رُصد حدث زخم/سيولة عبر الانفجار السعري (ثقة {breakout_result.prob}%، "
        f"اتجاه أولي {breakout_result.side})، ثم حلّله ICT بعمق ليحدد الاتجاه الحقيقي "
        f"ونقاط الدخول/الخروج. {direction_note}\n\n"
        f"⚡ [كاشف الحدث - الانفجار السعري]: {breakout_result.behavior}\n\n"
        f"🧠 [القرار النهائي - النمط الذكي]: {ict_result.behavior}"
    )
    volume_analysis = ("تأكيد مزدوج (نفس الاتجاه)" if same_direction else "انعكاس بعد فخ سيولة (اتجاه ICT هو النهائي)")

    return AnalysisResult(
        symbol=symbol, trend=ict_result.trend, dt="", prob=combined_prob, price=ict_result.price,
        atr=ict_result.atr, side=ict_result.side,
        entry_price=ict_result.entry_price, stop_loss=ict_result.stop_loss, take_profit=ict_result.take_profit,
        rr=ict_result.rr, quality="A" if combined_prob >= 88 else "B", conf=combined_prob,
        behavior=behavior, volume_analysis=volume_analysis,
        low_vol=False, kill_zone_ok=True, news_time=False, ranging=False,
    )
