"""
تقدير خارطة التصفية الحرارية (Liquidation Heatmap) — نسخة مجانية تقريبية.

⚠️ ملاحظة صادقة أولاً: المنصات (OKX/Binance) لا تكشف بيانات تصفية حسابات
المتداولين الآخرين علناً (خصوصية وأمان) — لذلك خرائط التصفية "الحقيقية" اللي
تشوفها بمواقع زي CoinGlass مبنية على نماذج تجميع بيانات متخصصة ومدفوعة عبر عدة
منصات دفعة وحدة. هذي النسخة **تقدير إحصائي تقريبي**، مو بيانات حقيقية مؤكدة —
لكنها مبنية على منطق مالي سليم ومفيدة كإشارة توجيهية إضافية.

المنطق:
  1) نبني "بروفايل فوليوم" من الشموع الأخيرة (آخر ~7 أيام) — أي مستوى سعري تداول
     فيه فوليوم أكثر، فيه على الأغلب مراكز أكثر فُتحت هناك (منطق Volume Profile
     القياسي المستخدم بكل أدوات التحليل الاحترافية).
  2) لكل مستوى سعري بهذا البروفايل، نحسب "أين ستُصفّى" مراكز افتراضية فُتحت من
     هناك، عبر رافعات مالية شائعة (5x, 10x, 20x, 25x, 50x) ومعادلة التصفية
     القياسية لعقود Perpetual Futures:
       - تصفية Long (تحصل لو نزل السعر): liq = entry × (1 − 1/leverage + هامش_صيانة)
       - تصفية Short (تحصل لو طلع السعر): liq = entry × (1 + 1/leverage − هامش_صيانة)
  3) نجمّع كل نقاط التصفية المحسوبة بـ"سلال" سعرية (Buckets)، ونرجّح الكثافة حسب
     الفوليوم الأصلي في مستوى الدخول الافتراضي.
  4) نستخدم معدل التمويل (Funding Rate) الحالي كترجيح إضافي: تمويل موجب متطرف
     يعني ازدحام مراكز Long فعلياً، فنرجّح مناطق تصفية الـLong (تحت السعر) أعلى،
     والعكس صحيح لتصفية الـShort (فوق السعر).
"""
from typing import List, Optional, Dict
from .analyzer import Kline

LEVERAGE_TIERS = [5, 10, 20, 25, 50]
MAINTENANCE_MARGIN_RATE = 0.005  # 0.5% تقريب معقول عبر أغلب شرائح الرافعة الشائعة
BUCKET_COUNT = 60
PRICE_RANGE_PCT = 0.20  # نغطي ±20% من السعر الحالي


def estimate_liquidation_heatmap(klines: List[Kline], current_price: float,
                                  funding_rate: Optional[float] = None,
                                  long_short_ratio: Optional[float] = None) -> Dict:
    """يرجع قاموس فيه: buckets (كل سلة سعرية وكثافتها واتجاهها)، وأقوى 3 مناطق
    تصفية Long محتملة (تحت السعر) و3 مناطق تصفية Short محتملة (فوق السعر)."""
    if not klines or current_price <= 0:
        return {"buckets": [], "top_long_liq_zones": [], "top_short_liq_zones": []}

    # بروفايل الفوليوم: كل شمعة تساهم بفوليومها عند سعر إغلاقها (تبسيط معقول)
    volume_by_price = []
    for k in klines:
        if k.close > 0:
            volume_by_price.append((k.close, k.volume))

    if not volume_by_price:
        return {"buckets": [], "top_long_liq_zones": [], "top_short_liq_zones": []}

    # ترجيح حسب ازدحام المراكز (معدل التمويل ونسبة Long/Short)
    long_weight = 1.0
    short_weight = 1.0
    if funding_rate is not None:
        if funding_rate > 0.0005:
            long_weight *= 1.4  # تمويل موجب متطرف = ازدحام Long حقيقي
        elif funding_rate < -0.0005:
            short_weight *= 1.4
    if long_short_ratio is not None:
        if long_short_ratio > 1.5:
            long_weight *= 1.3
        elif long_short_ratio < 0.67:
            short_weight *= 1.3

    low_bound = current_price * (1 - PRICE_RANGE_PCT)
    high_bound = current_price * (1 + PRICE_RANGE_PCT)
    bucket_size = (high_bound - low_bound) / BUCKET_COUNT
    if bucket_size <= 0:
        return {"buckets": [], "top_long_liq_zones": [], "top_short_liq_zones": []}

    buckets: Dict[int, Dict] = {}

    def _add_to_bucket(price: float, intensity: float, side: str):
        if price < low_bound or price > high_bound:
            return
        idx = int((price - low_bound) / bucket_size)
        idx = max(0, min(BUCKET_COUNT - 1, idx))
        bucket_price = low_bound + (idx + 0.5) * bucket_size
        b = buckets.setdefault(idx, {"price": bucket_price, "long_intensity": 0.0, "short_intensity": 0.0})
        if side == "long":
            b["long_intensity"] += intensity
        else:
            b["short_intensity"] += intensity

    for entry_price, vol in volume_by_price:
        for leverage in LEVERAGE_TIERS:
            long_liq = entry_price * (1 - 1.0 / leverage + MAINTENANCE_MARGIN_RATE)
            short_liq = entry_price * (1 + 1.0 / leverage - MAINTENANCE_MARGIN_RATE)
            _add_to_bucket(long_liq, vol * long_weight, "long")
            _add_to_bucket(short_liq, vol * short_weight, "short")

    bucket_list = sorted(buckets.values(), key=lambda b: b["price"])

    long_zones = [b for b in bucket_list if b["price"] < current_price and b["long_intensity"] > 0]
    short_zones = [b for b in bucket_list if b["price"] > current_price and b["short_intensity"] > 0]
    long_zones.sort(key=lambda b: b["long_intensity"], reverse=True)
    short_zones.sort(key=lambda b: b["short_intensity"], reverse=True)

    return {
        "buckets": bucket_list,
        "top_long_liq_zones": [
            {"price": z["price"], "intensity": round(z["long_intensity"], 2),
             "distance_pct": round((current_price - z["price"]) / current_price * 100, 2)}
            for z in long_zones[:3]
        ],
        "top_short_liq_zones": [
            {"price": z["price"], "intensity": round(z["short_intensity"], 2),
             "distance_pct": round((z["price"] - current_price) / current_price * 100, 2)}
            for z in short_zones[:3]
        ],
    }
