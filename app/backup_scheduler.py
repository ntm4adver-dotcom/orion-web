"""
النسخ الاحتياطي التلقائي الدوري (Rotating Backups).

يشتغل بخيط مستقل تماماً عن الفحص — يعني حتى لو أوقفت الفحص، النسخ الاحتياطي يستمر
طول ما التطبيق شغّال. يحفظ نسخ JSON دورية بنفس مجلد قاعدة البيانات (نفس القرص الدائم
على Render)، ويحذف تلقائياً أقدم نسخة كل ما تجاوز عدد النسخ المحتفظ فيها الحد الأقصى
(rotation) عشان ما تمتلئ مساحة القرص بمرور الوقت.
"""
import os
import json
import time
import threading
import glob

from . import db

BACKUP_DIR = os.path.join(os.path.dirname(db.DB_PATH), "backups")


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def list_backups():
    _ensure_backup_dir()
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup-*.json")), reverse=True)
    result = []
    for f in files:
        try:
            stat = os.stat(f)
            result.append({
                "filename": os.path.basename(f),
                "size_kb": round(stat.st_size / 1024, 1),
                "created_at": int(stat.st_mtime * 1000),
            })
        except Exception:
            continue
    return result


def create_backup_snapshot() -> str:
    """يأخذ نسخة احتياطية فورية ويحفظها على القرص، ويحذف الأقدم لو تجاوزنا الحد الأقصى."""
    _ensure_backup_dir()
    backup = db.export_backup()
    filename = f"backup-{backup['exported_at']}.json"
    filepath = os.path.join(BACKUP_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False)
    return filename


def _prune_old_backups(retention_count: int):
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup-*.json")))
    excess = len(files) - retention_count
    for f in files[:max(0, excess)]:
        try:
            os.remove(f)
        except Exception:
            pass


class BackupScheduler:
    def __init__(self):
        self._thread = None
        self._stop_flag = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_flag.set()

    def _loop(self):
        while not self._stop_flag.is_set():
            try:
                settings = db.get_settings()
                if settings.get("is_auto_backup_enabled", True):
                    interval_hours = max(1, int(settings.get("auto_backup_interval_hours", 6)))
                    retention = max(1, int(settings.get("auto_backup_retention_count", 10)))
                    backups = list_backups()
                    needs_backup = True
                    if backups:
                        newest_age_ms = int(time.time() * 1000) - backups[0]["created_at"]
                        needs_backup = newest_age_ms >= interval_hours * 60 * 60 * 1000
                    if needs_backup:
                        filename = create_backup_snapshot()
                        _prune_old_backups(retention)
                        db.add_log(f"💾 [نسخ احتياطي تلقائي] تم حفظ نسخة جديدة: {filename}")
            except Exception as e:
                db.add_log(f"❌ [نسخ احتياطي تلقائي] خطأ: {e}")
            # نتحقق كل دقيقة هل حان وقت النسخة القادمة، بدل النوم لساعات طويلة دفعة وحدة
            for _ in range(60):
                if self._stop_flag.is_set():
                    return
                time.sleep(1)


scheduler = BackupScheduler()
