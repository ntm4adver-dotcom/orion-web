import os
from fastapi import Request
from fastapi.responses import RedirectResponse

APP_PASSWORD = os.environ.get("ORION_APP_PASSWORD", "changeme")


def is_logged_in(request: Request) -> bool:
    return request.session.get("logged_in") is True


def require_login(request: Request):
    """يُستخدم داخل الـ route كحارس: يعيد RedirectResponse أو None."""
    if not is_logged_in(request):
        return RedirectResponse(url="/login", status_code=303)
    return None
