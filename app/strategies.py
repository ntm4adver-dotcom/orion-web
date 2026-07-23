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
from .supply_demand_strategy import analyze_supply_demand_reversal
from .stop_hunt_strategy import analyze_stop_hunt
from .scalp_strategy import analyze_scalp_precision
from .liquidation_strategy import analyze_liquidation_hunter
from .fabio_scalper_strategy import analyze_fabio_scalper
from .mtf_fib_strategy import analyze_mtf_fib_trend
from .crowd_trap_strategy import analyze_crowd_trap
from .confluence_strategy import analyze_confluence

# ⚠️ استراتيجيات أُزيلت من السجل (لسا موجودة كملفات، بس مو مسجّلة/مفعّلة):
#  - ict_smart_sweep: بيانات فعلية أظهرت صفر إشارة بأي وقت رغم أيام تشغيل — شروطها
#    الخمسة الصارمة ما تحققت ولا مرة واحدة، فلا فائدة فعلية من إبقائها مفعّلة.
#  - hybrid_confirmation: تعتمد كلياً على نجاح ict_smart_sweep — بما إنها ما تنجح
#    أبداً، هذي الاستراتيجية ميتة تلقائياً بنفس السبب (لا يمكن تفعيلها منطقياً).
#  - ict_guided_entry: مصمَّمة "بدون رفض" — تعتمد نقاط ICT لو توفرت، وإلا تعتمد نقاط
#    الانفجار السعري كما هي بدون أي تعديل. بما إن ICT ما توفرت ولا مرة، فهذي
#    الاستراتيجية أصبحت فعلياً **مطابقة تماماً** لاستراتيجية الانفجار السعري —
#    تكرار حرفي بدون أي قيمة مضافة حقيقية.

# كل استراتيجية: مفتاح فريد -> {label: الاسم المعروض, fn: دالة التحليل}
# توقيع دالة التحليل الموحّد: fn(symbol, k4h, k1h, k15m, k5m, k_daily, micro=None) -> Optional[AnalysisResult]
STRATEGY_REGISTRY = {
    "explosive_breakout": {
        "label": "⚡ الانفجار السعري (Explosive Breakout Hunter) — الأصلية",
        "fn": analyze,
    },
    "supply_demand_reversal": {
        "label": "🔄 انعكاس عرض/طلب (يعكس اتجاه الانفجار السعري من أقرب منطقة Supply/Demand)",
        "fn": analyze_supply_demand_reversal,
    },
    "stop_hunt": {
        "label": "🎣 صيد الاستوبات والمؤسسات (Stop-Loss Hunting)",
        "fn": analyze_stop_hunt,
    },
    "scalp_precision": {
        "label": "🎯 السكالب السريع الدقيق (R:R≥5 مفروض حقيقياً)",
        "fn": analyze_scalp_precision,
    },
    "liquidation_hunter": {
        "label": "🔥 صيد التصفيات (Liquidation Hunter) — تقدير تقريبي",
        "fn": analyze_liquidation_hunter,
    },
    "fabio_scalper": {
        "label": "📊 سكالب فابيو فالنتيني (Direction-Location-Aggression)",
        "fn": analyze_fabio_scalper,
    },
    "mtf_fib_trend": {
        "label": "📐 فيبوناتشي الترند المتعدد الفريمات (15د اتجاه / 5د تراجع منكسر / دخول 0.72)",
        "fn": analyze_mtf_fib_trend,
    },
    "crowd_trap": {
        "label": "🎭 مصيدة الحشد (Crowd Trap Divergence) — تصميم أصيل، بدون نمط سعري",
        "fn": analyze_crowd_trap,
    },
    "confluence": {
        "label": "🤝 استراتيجية التوافق (تحتاج اتفاق استراتيجيتين أو أكثر، R:R≥5)",
        "fn": analyze_confluence,
    },
}

COMBINED_STRATEGY_KEY = "combined"
COMBINED_STRATEGY_LABEL = "🔀 الكل معاً (يشغّل كل الاستراتيجيات المتاحة على كل عملة)"


def get_strategy_options() -> list:
    """قائمة (المفتاح، الاسم المعروض) لكل الاستراتيجيات + خيار الدمج، بترتيب العرض بالقائمة المنسدلة."""
    options = [(key, info["label"]) for key, info in STRATEGY_REGISTRY.items()]
    options.append((COMBINED_STRATEGY_KEY, COMBINED_STRATEGY_LABEL))
    return options


def get_active_strategies(active_strategy_setting: str, combined_enabled_keys: str = "") -> list:
    """يرجع قائمة (مفتاح الاستراتيجية، دالة التحليل) اللي لازم تشتغل فعلياً حسب اختيار المستخدم.
    لو اختار 'combined' يرجع الاستراتيجيات المسجّلة المفعّلة فقط (حسب combined_enabled_keys —
    نص مفصول بفاصلة؛ فاضي أو غير محدد = كل الاستراتيجيات مفعّلة افتراضياً، بما فيها أي
    استراتيجية جديدة تُضاف مستقبلاً دون حاجة لتحديث هذا الإعداد يدوياً)."""
    if active_strategy_setting == COMBINED_STRATEGY_KEY:
        enabled = set(k.strip() for k in combined_enabled_keys.split(",") if k.strip())
        if not enabled:
            return [(key, info["fn"]) for key, info in STRATEGY_REGISTRY.items()]
        return [(key, info["fn"]) for key, info in STRATEGY_REGISTRY.items() if key in enabled]
    if active_strategy_setting in STRATEGY_REGISTRY:
        return [(active_strategy_setting, STRATEGY_REGISTRY[active_strategy_setting]["fn"])]
    # قيمة غير معروفة (مثلاً من نسخة قديمة) → نرجع للاستراتيجية الافتراضية الأولى
    default_key = next(iter(STRATEGY_REGISTRY))
    return [(default_key, STRATEGY_REGISTRY[default_key]["fn"])]


def strategy_label(key: str) -> str:
    if key == COMBINED_STRATEGY_KEY:
        return COMBINED_STRATEGY_LABEL
    return STRATEGY_REGISTRY.get(key, {}).get("label", key)
