"""
سجل الاستراتيجيات المركزي (Strategy Registry).

هذا الملف هو المصدر الوحيد للحقيقة بخصوص الاستراتيجيات المتاحة بالتطبيق. أي استراتيجية
جديدة تُضاف هنا فقط — تلقائياً بتظهر في:
  1) القائمة المنسدلة بصفحة الإعدادات
  2) خيار "🔀 الكل معاً" (يشغّلها مع بقية الاستراتيجيات على كل عملة تلقائياً)
  3) صفحة "التطور" في جدول مقارنة أداء الاستراتيجيات (رابحة/خاسرة/نسبة نجاح)

لإضافة استراتيجية جديدة مستقبلاً: فقط أضف مفتاح جديد بقاموس STRATEGY_REGISTRY أدناه
بنفس الشكل، ولا حاجة لتعديل أي ملف آخر بالتطبيق (settings.html، scanner.py، evolution.html
كلها تقرأ من هذا السجل تلقائياً).
"""
from .analyzer import analyze
from .ict_strategy import analyze_ict_smart_sweep
from .hybrid_strategy import analyze_hybrid_confirmation
from .ict_guided_strategy import analyze_ict_guided_entry

# كل استراتيجية: مفتاح فريد -> {label: الاسم المعروض, fn: دالة التحليل}
# توقيع دالة التحليل الموحّد: fn(symbol, k4h, k1h, k15m, k5m, k_daily, micro=None) -> Optional[AnalysisResult]
STRATEGY_REGISTRY = {
    "explosive_breakout": {
        "label": "⚡ الانفجار السعري (Explosive Breakout Hunter) — الأصلية",
        "fn": analyze,
    },
    "ict_smart_sweep": {
        "label": "🧠 النمط الذكي لسحب السيولة (ICT / Smart Money Concepts)",
        "fn": analyze_ict_smart_sweep,
    },
    "hybrid_confirmation": {
        "label": "🔗 التأكيد المزدوج (يشترط تأكيد ICT، وإلا تُرفض الصفقة)",
        "fn": analyze_hybrid_confirmation,
    },
    "ict_guided_entry": {
        "label": "🎯 الانفجار الموجّه بـICT (بدون رفض — ICT يحسّن الدخول إن توفر فقط)",
        "fn": analyze_ict_guided_entry,
    },
}

COMBINED_STRATEGY_KEY = "combined"
COMBINED_STRATEGY_LABEL = "🔀 الكل معاً (يشغّل كل الاستراتيجيات المتاحة على كل عملة)"


def get_strategy_options() -> list:
    """قائمة (المفتاح، الاسم المعروض) لكل الاستراتيجيات + خيار الدمج، بترتيب العرض بالقائمة المنسدلة."""
    options = [(key, info["label"]) for key, info in STRATEGY_REGISTRY.items()]
    options.append((COMBINED_STRATEGY_KEY, COMBINED_STRATEGY_LABEL))
    return options


def get_active_strategies(active_strategy_setting: str) -> list:
    """يرجع قائمة (مفتاح الاستراتيجية، دالة التحليل) اللي لازم تشتغل فعلياً حسب اختيار المستخدم.
    لو اختار 'combined' يرجع كل الاستراتيجيات المسجّلة تلقائياً — أي إضافة مستقبلية للسجل
    بتنعكس هنا بدون أي تعديل إضافي."""
    if active_strategy_setting == COMBINED_STRATEGY_KEY:
        return [(key, info["fn"]) for key, info in STRATEGY_REGISTRY.items()]
    if active_strategy_setting in STRATEGY_REGISTRY:
        return [(active_strategy_setting, STRATEGY_REGISTRY[active_strategy_setting]["fn"])]
    # قيمة غير معروفة (مثلاً من نسخة قديمة) → نرجع للاستراتيجية الافتراضية الأولى
    default_key = next(iter(STRATEGY_REGISTRY))
    return [(default_key, STRATEGY_REGISTRY[default_key]["fn"])]


def strategy_label(key: str) -> str:
    if key == COMBINED_STRATEGY_KEY:
        return COMBINED_STRATEGY_LABEL
    return STRATEGY_REGISTRY.get(key, {}).get("label", key)
