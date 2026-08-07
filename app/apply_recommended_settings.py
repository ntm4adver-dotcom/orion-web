"""
تطبيق الإعدادات الموصى بها — سكربت تشغيل مرة وحدة (بطلب صريح: "خليهم جاهزين
بدل ما أعدلهم يدوياً"). يحدّث قاعدة البيانات الحية مباشرة بنفس الإعدادات
اللي وصلنا لها بعد كل المراجعات والاكتشافات طول الجلسة.

طريقة التشغيل (من مجلد المشروع، وين ملف orion.db موجود):
    python -m app.apply_recommended_settings

⚠️ يحدّث إعداداتك **الحالية** مباشرة — خذ نسخة احتياطية من قاعدة البيانات
أول لو حابب ترجع للإعدادات القديمة لاحقاً.
"""
from . import db

RECOMMENDED_SETTINGS = {
    # ===== مصدر البيانات =====
    "exchange": "okx",  # بطلب صريح — نبقى على OKX حصراً

    # ===== فلاتر الجودة الأساسية (أدلة قوية من صفقات حقيقية) =====
    "is_coin_quality_filter_enabled": 1,   # فلتر استباقي لسلوك العملة (تذبذب/شموع شاذة) — مبني على منطق موضوعي مستقل عن سجلنا
    "min_coin_efficiency_ratio": 0.05,
    "max_coin_wick_outlier_ratio": 10.0,      # وسط بين 6 (شدّد جداً، عارض stop_hunt) و12 (مرن جداً)
    "max_coin_atr_pct": 10.0,                 # وسط بين 8 و15 اللي جرّبناهم

    "is_market_regime_filter_enabled": 1,  # دليل حقيقي: فترة ترند قوي أعطت 80% نجاح فعلياً
    "is_market_alignment_filter_enabled": 1,
    "is_btc_decoupling_exception_enabled": 0,  # بطلبك الصريح — فك الارتباط مؤقت، نتجاهله دايماً

    "is_price_divergence_filter_enabled": 1,   # حماية منطقية بحدود واسعة (0.5%)، خطر رفض خاطئ منخفض
    "max_price_divergence_pct": 0.5,

    # ===== فلاتر جديدة غير مُختبرة بعد على بيانات كافية =====
    "is_taker_pressure_filter_enabled": 0,   # دليل جيد بس بعينة صغيرة (6 صفقات) — فعّلها بنفسك بعد ما تراقب فترة
    "is_top_trader_filter_enabled": 0,       # صفر بيانات حقيقية عليها لسا
    "is_btc_dominance_filter_enabled": 0,    # منطقي لكن غير مُختبر — فعّله بعد أول أسبوع مراقبة
    "is_coin_hard_block_enabled": 0,         # رفض قاطع خطير قبل ما نتأكد من دقة التعلّم الذاتي

    # ===== إدارة الصفقة (R:R) — إصلاح التناقض اللي اكتشفناه =====
    "is_fixed_rr_enabled": 1,
    "fixed_rr_mode": "filter",   # 🔴 رجعناها من "always_force" — كانت تفرض هدف بعيد (3R) فوق وقف ضيّق (سكالب) بدون علاقة منطقية بينهم
    "fixed_rr_value": 2.0,       # 🔴 نزّلناها من 3.0 — يتناسب مع حجم الوقف المُضيَّق الحالي، بدل رقم كان مصمم لأوقاف أوسع

    # ===== إدارة الصفقة الإضافية (دليل حقيقي: نمط "اتجاه صح، خروج غلط" تكرر) =====
    "is_breakeven_stop_enabled": 1,
    "breakeven_trigger_r_multiple": 1.0,
    "is_cancel_if_exceeds_target_enabled": 1,

    # ===== اختيار العملات =====
    "symbol_selection_mode": "oi_spike",  # يناسب طبيعة استراتيجياتك (صيد استوبات/تصفيات) أكثر من top_volume الهادئ
    "symbols_limit": 50,
    "scan_interval_seconds": 300,

    # ===== عتبات "الحركة الحاسمة" لارتداد فوليوم التصريف =====
    "climactic_min_extended_move_pct": 5.0,   # بدون تغيير — العينة اللي راجعناها صغيرة جداً نرفعها بثقة
    "climactic_min_volume_ratio": 8.0,
    "climactic_confirm_margin_atr": 0.3,

    # ===== تعلّم ذاتي (يبقى مفعّل، آمن لأنه تعديل تدريجي مو رفض قاطع) =====
    "is_coin_learning_enabled": 1,
}


def apply():
    db.init_db()
    db.update_settings(RECOMMENDED_SETTINGS)
    print(f"✅ تم تطبيق {len(RECOMMENDED_SETTINGS)} إعداد موصى به على قاعدة البيانات الحية.")
    print("راجع صفحة الإعدادات بالتطبيق للتأكد، وعدّل يدوياً أي شي يناسبك أكثر.")


if __name__ == "__main__":
    apply()
