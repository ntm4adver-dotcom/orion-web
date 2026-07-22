"""
النسخ الاحتياطي على Google Drive عبر OAuth 2.0.

يستخدم Google Drive REST API v3 مباشرة (بدون مكتبة google-api-python-client الثقيلة)
عبر httpx، بنفس أسلوب باقي عملاء API بالمشروع (okx_client.py / binance_client.py).

مطلوب متغيرا بيئة (لا تُكتب بالكود، تُضاف كـ Environment Variables محلياً وعلى Render):
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
"""
import os
import time
import json
import httpx

from . import db

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"

SCOPE = "https://www.googleapis.com/auth/drive.file"  # وصول محدود لملفات أنشأها التطبيق نفسه فقط
BACKUP_FOLDER_NAME = "Orion Trading Bot - Backups"


def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def is_connected() -> bool:
    s = db.get_settings()
    return bool(s.get("gdrive_refresh_token"))


def build_auth_url(redirect_uri: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # ضروري عشان نحصل على refresh_token
        "prompt": "consent",        # يجبر إعادة إظهار شاشة الموافقة عشان نضمن refresh_token دايم
    }
    query = str(httpx.QueryParams(params))
    return f"{AUTH_URL}?{query}"


def exchange_code_for_tokens(code: str, redirect_uri: str) -> tuple:
    try:
        r = httpx.post(TOKEN_URL, data={
            "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        }, timeout=15)
        data = r.json()
        if "refresh_token" not in data:
            return False, data.get("error_description", data.get("error", "لم يتم استلام refresh_token — تأكد إنك وافقت على الصلاحيات كاملة."))
        db.update_settings({
            "gdrive_refresh_token": data["refresh_token"],
            "is_gdrive_backup_enabled": 1,
        })
        return True, "تم الربط بنجاح"
    except Exception as e:
        return False, str(e)


def _get_access_token() -> str:
    s = db.get_settings()
    refresh_token = s.get("gdrive_refresh_token")
    if not refresh_token:
        return ""
    r = httpx.post(TOKEN_URL, data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }, timeout=15)
    data = r.json()
    return data.get("access_token", "")


def _ensure_backup_folder(access_token: str) -> str:
    s = db.get_settings()
    existing = s.get("gdrive_folder_id")
    if existing:
        return existing
    headers = {"Authorization": f"Bearer {access_token}"}
    # نبحث أول إذا المجلد موجود مسبقاً (لتفادي إنشاء مجلد مكرر كل مرة)
    search = httpx.get(DRIVE_FILES_URL, headers=headers, params={
        "q": f"name='{BACKUP_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        "fields": "files(id,name)",
    }, timeout=15)
    found = search.json().get("files", [])
    if found:
        folder_id = found[0]["id"]
    else:
        create = httpx.post(DRIVE_FILES_URL, headers=headers, json={
            "name": BACKUP_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder",
        }, timeout=15)
        folder_id = create.json().get("id", "")
    if folder_id:
        db.update_settings({"gdrive_folder_id": folder_id})
    return folder_id


def upload_backup_file(local_filepath: str, filename: str) -> tuple:
    if not is_connected():
        return False, "Google Drive غير مربوط"
    try:
        access_token = _get_access_token()
        if not access_token:
            return False, "تعذر تجديد رمز الوصول — قد تحتاج تعيد الربط"
        folder_id = _ensure_backup_folder(access_token)
        if not folder_id:
            return False, "تعذر إنشاء/إيجاد مجلد النسخ الاحتياطية بدرايف"

        with open(local_filepath, "rb") as f:
            file_content = f.read()

        metadata = {"name": filename, "parents": [folder_id]}
        files = {
            "metadata": (None, json.dumps(metadata), "application/json; charset=UTF-8"),
            "file": (filename, file_content, "application/json"),
        }
        headers = {"Authorization": f"Bearer {access_token}"}
        r = httpx.post(f"{DRIVE_UPLOAD_URL}?uploadType=multipart", headers=headers, files=files, timeout=30)
        if r.status_code in (200, 201):
            return True, "تم الرفع بنجاح"
        return False, f"فشل الرفع (HTTP {r.status_code}): {r.text[:200]}"
    except Exception as e:
        return False, str(e)


def disconnect():
    db.update_settings({"gdrive_refresh_token": "", "gdrive_folder_id": "", "is_gdrive_backup_enabled": 0})
