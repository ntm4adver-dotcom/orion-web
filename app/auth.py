import os
import secrets
from fastapi import Request
from fastapi.responses import RedirectResponse

# 🔴 إصلاح ثغرة أمنية حقيقية (اكتُشفت بمراجعة شاملة): كانت القيمة الافتراضية
# "changeme" — كلمة شائعة جداً ومتوقَّعة، تسمح لأي شخص يدخل التطبيق (وفيه
# مفاتيح OKX حقيقية متصلة) لو متغيّر البيئة ORION_APP_PASSWORD مو مضبوط.
# الآن: لو ما تحدد كلمة مرور حقيقية بمتغيّر البيئة، نستخدم كلمة عشوائية
# طويلة تُنشأ تلقائياً كل تشغيل — التطبيق يبقى محمي بأمان (بس تحتاج تشوفها
# باللوق وقت التشغيل)، بدل باب مفتوح بكلمة معروفة للجميع.
_env_password = os.environ.get("ORION_APP_PASSWORD")
if _env_password:
    APP_PASSWORD = _env_password
else:
    APP_PASSWORD = secrets.token_urlsafe(16)
    print(f"⚠️ تحذير أمني: متغيّر البيئة ORION_APP_PASSWORD غير مضبوط — "
          f"تم توليد كلمة مرور عشوائية مؤقتة لهذا التشغيل: {APP_PASSWORD}\n"
          f"   لحماية دائمة، اضبط ORION_APP_PASSWORD بكلمة مرور حقيقية خاصة بك.")


def is_logged_in(request: Request) -> bool:
    return request.session.get("logged_in") is True


def check_password(candidate: str) -> bool:
    """مقارنة آمنة زمنياً (Timing-Safe) بدل == العادية."""
    return secrets.compare_digest(candidate, APP_PASSWORD)


def require_login(request: Request):
    """يُستخدم داخل الـ route كحارس: يعيد RedirectResponse أو None."""
    if not is_logged_in(request):
        return RedirectResponse(url="/login", status_code=303)
    return None
