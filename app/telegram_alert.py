"""مرسل تنبيهات تيليجرام — منقول عن TelegramAlertSender.kt."""
import httpx


def _parse_id(raw: str):
    clean = raw.strip()
    if ":" not in clean and "=" not in clean:
        return clean, None
    parts = clean.split(":") if ":" in clean else clean.split("=")
    if len(parts) >= 2:
        p1, p2 = parts[0].strip(), parts[1].strip()
        if p2.lstrip("-").isdigit():
            return p2, (p1 or None)
        if p1.lstrip("-").isdigit():
            return p1, (p2 or None)
        return p1, p2
    return clean, None


def _send_to_all(token: str, chat_ids_string: str, message: str):
    if not token or not chat_ids_string:
        return
    ids = [i.strip() for i in chat_ids_string.split(",") if i.strip()]
    for raw_id in ids:
        chat_id, _name = _parse_id(raw_id)
        if not chat_id:
            continue
        try:
            with httpx.Client(timeout=15) as client:
                client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                )
        except Exception:
            pass


def send_text_alert(token: str, chat_ids_string: str, message: str):
    """إرسال تنبيه نصي حر — يُستخدم لتنبيهات نقص البيانات وغيرها من الإشعارات العامة."""
    _send_to_all(token, chat_ids_string, message)


def send_signal_alert(token: str, chat_ids_string: str, symbol: str, direction: str,
                       entry_price: float, take_profit: float, stop_loss: float,
                       probability: int, quality: str, behavior: str, is_instant: bool = False):
    dir_emoji = "🟢 صعود (Long)" if direction == "Long" else "🔴 هبوط (Short)"
    dir_hashtag = "#Long" if direction == "Long" else "#Short"
    entry_type_desc = "⚡ دخول فوري (Instant Entry)" if is_instant else "⏳ أمر معلق (Limit Order)"
    sl_desc = "❌ بدون وقف خسارة (No SL)" if stop_loss <= 0 else f"{stop_loss:.6g}"
    message = (
        f"🔔 إشارة تداول جديدة: #{symbol}\n\n"
        f"⚙️ نوع الدخول: {entry_type_desc}\n"
        f"📈 الاتجاه: {dir_emoji}\n"
        f"🎯 الدخول: {entry_price:.6g}\n"
        f"🚀 الهدف (TP): {take_profit:.6g}\n"
        f"🛑 الاستوب (SL): {sl_desc}\n\n"
        f"📊 نسبة النجاح: {probability}% (تصنيف {quality})\n"
        f"🧩 التحليل: {behavior}\n\n"
        f"{dir_hashtag} #BinanceFutures #OrionBot"
    )
    _send_to_all(token, chat_ids_string, message)


_STATUS_TEXT = {
    "ACTIVE": "🟢 تم تفعيل الصفقة بنجاح! السعر الحالي واجه نقطة الدخول وجاري مراقبة حركة السوق...",
    "HIT_TP": "🎯 مبروووك! تم ضرب الهدف (Take Profit) بالكامل بنجاح تحقيق ربح رائع 🎉",
    "HIT_SL": "🛑 تنبيه: تم ضرب وقف الخسارة (Stop Loss) والخروج التلقائي من الصفقة.",
    "CANCELLED": "⚠️ تم إلغاء الصفقة: السعر ضرب الهدف مباشرة دون ملامسة سعر الدخول أولاً.",
}
_STATUS_EMOJI = {"ACTIVE": "⚡", "HIT_TP": "💰", "HIT_SL": "📉", "CANCELLED": "❌"}


def send_status_alert(token: str, chat_ids_string: str, symbol: str, direction: str, status: str, price: float):
    if not token or not chat_ids_string:
        return
    status_text = _STATUS_TEXT.get(status, status)
    emoji = _STATUS_EMOJI.get(status, "🔔")
    message = (
        f"{emoji} تحديث صفقة: #{symbol}\n\n"
        f"📈 الاتجاه: {'🟢 صعود (Long)' if direction == 'Long' else '🔴 هبوط (Short)'}\n"
        f"🔄 الحالة الجديدة: {status_text}\n"
        f"💵 السعر اللحظي: {price:.6g}\n\n"
        f"#OrionBot #CryptoUpdates"
    )
    _send_to_all(token, chat_ids_string, message)
