"""
بروفايل الفوليوم (Volume Profile) — الأداة الأساسية اللي يعتمد عليها منهج فابيو
فالنتيني (Direction-Location-Aggression) بدل المؤشرات المتأخرة التقليدية.

- POC (Point of Control): السعر اللي تداول عنده أكبر حجم — "نقطة التحكم"، منطقة
  قبول سعري قوية (Fair Value)، غالباً يرتد منها السعر أو يستخدمها كمرجع جاذب.
- Value Area (VAH/VAL): النطاق السعري اللي فيه ~70% من إجمالي حجم التداول، يبدأ
  من POC ويتوسع بالاتجاهين لحد ما يجمع 70%. حوافه (VAH العلوي وVAL السفلي) مناطق
  قرار مهمة — إما ارتداد (رفض تجاوز الحافة) أو اختراق حقيقي (اختلال/Imbalance).
- LVN (Low Volume Node): مناطق تداول فيها فوليوم قليل جداً مقارنة بجيرانها — السعر
  يمر منها بسرعة عادة (فراغ سيولة)، وتُستخدم كمناطق دخول/أهداف انتقالية.
"""
from typing import List, Optional, Dict
from .analyzer import Kline


def compute_volume_profile(klines: List[Kline], num_buckets: int = 40,
                            value_area_pct: float = 0.70) -> Optional[Dict]:
    if not klines or len(klines) < 10:
        return None

    prices = [k.close for k in klines]
    low_bound = min(k.low for k in klines)
    high_bound = max(k.high for k in klines)
    if high_bound <= low_bound:
        return None

    bucket_size = (high_bound - low_bound) / num_buckets
    if bucket_size <= 0:
        return None

    bucket_volumes = [0.0] * num_buckets

    def _bucket_idx(price: float) -> int:
        idx = int((price - low_bound) / bucket_size)
        return max(0, min(num_buckets - 1, idx))

    for k in klines:
        bucket_volumes[_bucket_idx(k.close)] += k.volume

    total_volume = sum(bucket_volumes)
    if total_volume <= 0:
        return None

    poc_idx = max(range(num_buckets), key=lambda i: bucket_volumes[i])
    poc_price = low_bound + (poc_idx + 0.5) * bucket_size

    # بناء منطقة القيمة (Value Area): نتوسع من POC للطرفين، كل مرة نضيف الجانب
    # الأعلى حجماً بين الجانبين، لحد ما نجمع 70% من إجمالي الحجم — الخوارزمية القياسية
    included = {poc_idx}
    accumulated = bucket_volumes[poc_idx]
    low_i, high_i = poc_idx, poc_idx
    while accumulated < total_volume * value_area_pct and (low_i > 0 or high_i < num_buckets - 1):
        next_low = low_i - 1 if low_i > 0 else None
        next_high = high_i + 1 if high_i < num_buckets - 1 else None
        vol_low = bucket_volumes[next_low] if next_low is not None else -1
        vol_high = bucket_volumes[next_high] if next_high is not None else -1
        if vol_high >= vol_low:
            high_i = next_high
            accumulated += vol_high
            included.add(next_high)
        else:
            low_i = next_low
            accumulated += vol_low
            included.add(next_low)

    val_price = low_bound + low_i * bucket_size
    vah_price = low_bound + (high_i + 1) * bucket_size

    # LVN: سلال فوليومها أقل من 30% من متوسط الفوليوم بجيرانها المباشرين — فراغات سيولة حقيقية
    avg_volume = total_volume / num_buckets
    lvns = []
    for i in range(1, num_buckets - 1):
        neighbors_avg = (bucket_volumes[i - 1] + bucket_volumes[i + 1]) / 2.0
        if neighbors_avg > 0 and bucket_volumes[i] < neighbors_avg * 0.3 and bucket_volumes[i] < avg_volume * 0.5:
            lvns.append(low_bound + (i + 0.5) * bucket_size)

    current_price = klines[-1].close
    lvns.sort(key=lambda p: abs(p - current_price))

    return {
        "poc": poc_price, "vah": vah_price, "val": val_price,
        "lvns": lvns[:5], "low_bound": low_bound, "high_bound": high_bound,
        "current_price": current_price,
    }
