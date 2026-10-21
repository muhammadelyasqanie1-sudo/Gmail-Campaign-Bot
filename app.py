"""Telegram bot starter for admin-managed Gmail campaign settings.

This scaffold does not send email. Gmail API/OAuth integration is a separate step.
"""
import logging
import os
import re
import sqlite3
import httpx
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot_data.sqlite3").strip()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
WHATSAPP_SERVICE_URL = os.getenv("WHATSAPP_SERVICE_URL", "").strip().rstrip("/")
WHATSAPP_SERVICE_TOKEN = os.getenv("WHATSAPP_SERVICE_TOKEN", "").strip()
DEFAULT_TEMPLATE = "سلام، امیدوارم حالتان خوب باشد.\nشماره تماس: {{user_phone}}"


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and ADMIN_ID_RAW.isdigit()
                and update.effective_user.id == int(ADMIN_ID_RAW))


def connect_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_db()) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                phone TEXT,
                phone_submitted_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS recipients (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wa_usage (
                telegram_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL DEFAULT (date('now')),
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (telegram_id, usage_date)
            );
        """)
        defaults = {
            "subject": "پیام برای شما",
            "template": DEFAULT_TEMPLATE,
            "interval_seconds": "60",
            "duration_minutes": "2",
            "whatsapp_enabled": "0",
            "whatsapp_template": "سلام، امیدوارم حالتان خوب باشد.",
            "whatsapp_daily_limit_per_user": "5",
            "whatsapp_require_confirmation": "1",
        }
        conn.executemany(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            defaults.items(),
        )
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with closing(connect_db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with closing(connect_db()) as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return
    with closing(connect_db()) as conn:
        conn.execute(
            "INSERT INTO users(telegram_id, username) VALUES(?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username",
            (user.id, user.username or ""),
        )
        conn.commit()
    if is_admin(update):
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📨 ارسال پیام")], [KeyboardButton("⚙️ مدیریت واتساپ")]],
            resize_keyboard=True,
        )
        greeting = "سلام ادمین! برای ارسال پیام یا مدیریت واتساپ یکی را انتخاب کن. دستورها: /admin"
    else:
        keyboard = ReplyKeyboardMarkup([[KeyboardButton("📨 ارسال پیام")]], resize_keyboard=True)
        greeting = "سلام! خوش آمدید. برای شروع فقط روی «ارسال پیام» بزنید."
    await update.message.reply_text(greeting, reply_markup=keyboard)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    value = (update.message.text or "").strip()
    uid = update.effective_user.id
    if value == "⚙️ مدیریت واتساپ" and is_admin(update):
        await admin_help(update, context)
        return
    if value == "📨 ارسال پیام":
        if not is_admin(update) and get_setting("whatsapp_enabled", "0") != "1":
            await update.message.reply_text("ارسال پیام فعلاً توسط ادمین غیرفعال است.")
            return
        context.user_data["wa_step"] = "phone"
        await update.message.reply_text(
            "شمارهٔ گیرنده را با کد کشور وارد کنید؛ مثال +93700123456.\n"
            "فقط به گیرنده‌ای پیام بفرستید که دریافت پیام را پذیرفته باشد."
        )
        return

    step = context.user_data.get("wa_step")
    if step == "phone":
        phone = re.sub(r"\D", "", value)
        if not 8 <= len(phone) <= 15:
            await update.message.reply_text("شماره معتبر نیست؛ با کد کشور وارد کنید، مثل +93700123456.")
            return
        template = get_setting("whatsapp_template", "سلام، امیدوارم حالتان خوب باشد.")
        message = template.replace("{{user_phone}}", phone).replace("{{recipient_phone}}", phone)
        context.user_data["wa_phone"] = phone
        context.user_data["wa_message"] = message
        context.user_data["wa_step"] = "confirm"
        await update.message.reply_text(
            "پیش‌نمایش پیام:\n\n" + message +
            "\n\nاگر گیرنده اجازهٔ دریافت پیام را داده، برای ادامه دقیقاً «تأیید و ارسال» بنویسید؛ برای لغو «لغو» بنویسید."
        )
        return

    if step == "confirm":
        if value in {"لغو", "cancel", "نه"}:
            context.user_data.pop("wa_step", None)
            context.user_data.pop("wa_phone", None)
            context.user_data.pop("wa_message", None)
            await update.message.reply_text("درخواست لغو شد.")
            return
        if value != "تأیید و ارسال":
            await update.message.reply_text("برای ارسال «تأیید و ارسال» یا برای لغو «لغو» بنویسید.")
            return
        if not is_admin(update) and get_setting("whatsapp_enabled", "0") != "1":
            context.user_data.clear()
            await update.message.reply_text("ارسال پیام توسط ادمین غیرفعال شده است.")
            return
        phone = context.user_data.get("wa_phone", "")
        message = context.user_data.get("wa_message", "")
        try:
            with closing(connect_db()) as conn:
                row = conn.execute(
                    "SELECT count FROM wa_usage WHERE telegram_id=? AND usage_date=date('now')",
                    (uid,),
                ).fetchone()
                used = int(row["count"]) if row else 0
                limit = int(get_setting("whatsapp_daily_limit_per_user", "5"))
                if not is_admin(update) and used >= limit:
                    context.user_data.pop("wa_step", None)
                    await update.message.reply_text(f"سقف روزانهٔ شما پر شده است ({limit} درخواست).")
                    return
            data = await whatsapp_request("GET", "/accounts")
            available = [a for a in data.get("accounts", []) if a.get("connected")]
            if not available:
                await update.message.reply_text("فعلاً حساب واتساپ آمادهٔ ارسال نیست. بعداً دوباره امتحان کنید.")
                return
            with closing(connect_db()) as conn:
                row = conn.execute("SELECT value FROM settings WHERE key='wa_last_account'").fetchone()
                previous = row["value"] if row else ""
                ids = [a["id"] for a in available]
                account_id = ids[(ids.index(previous) + 1) % len(ids)] if previous in ids else ids[0]
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES('wa_last_account',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (account_id,)
                )
                if not is_admin(update):
                    conn.execute(
                        "INSERT INTO wa_usage(telegram_id,usage_date,count) VALUES(?,date('now'),1) "
                        "ON CONFLICT(telegram_id,usage_date) DO UPDATE SET count=count+1", (uid,)
                    )
                conn.commit()
            result = await whatsapp_request(
                "POST", "/send", {"accountId": account_id, "phone": phone, "message": message}
            )
            context.user_data.pop("wa_step", None)
            context.user_data.pop("wa_phone", None)
            context.user_data.pop("wa_message", None)
            await update.message.reply_text(
                "درخواست ارسال به سرویس واتساپ تحویل شد." if result.get("ok")
                else "ارسال انجام نشد؛ بعداً تلاش کنید."
            )
            if is_admin(update):
                await update.message.reply_text(f"حساب استفاده‌شده: {account_id}")
        except Exception as exc:
            log.warning("WhatsApp send request failed: %s", exc)
            await update.message.reply_text("ارسال ناموفق بود؛ سرویس واتساپ و حساب‌های متصل را بررسی کنید.")
        return
    await update.message.reply_text("برای شروع /start را بزنید یا روی «ارسال پیام» بزنید.")




async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است.")
        return
    await update.message.reply_text(
        "دستورهای ادمین:\n"
        "/setsubject <موضوع>\n"
        "/settemplate <متن؛ از {{user_phone}} برای شماره استفاده کن>\n"
        "/addrecipient <email>\n"
        "/delrecipient <email>\n"
        "/recipients\n"
        "/setinterval <ثانیه>\n"
        "/setduration <دقیقه>\n"
        "/settings\n"
        "/wa_enable on|off — فعال/غیرفعال‌کردن استفادهٔ کاربران از واتساپ\n"
        "/wa_template <متن> — تعیین قالب پیام واتساپ\n"
        "/wa_user_limit <عدد> — سقف روزانهٔ درخواست هر کاربر (۱ تا ۲۰)\n"
        "/wa_policy — مشاهدهٔ سیاست فعلی واتساپ\n\n"
        "نکته: این نسخه فقط تنظیمات را ذخیره می‌کند و ایمیل نمی‌فرستد."
    )


async def set_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    value = " ".join(context.args).strip()
    if not value or len(value) > 200:
        await update.message.reply_text("استفاده: /setsubject متن موضوع (حداکثر ۲۰۰ نویسه)"); return
    set_setting("subject", value)
    await update.message.reply_text("موضوع ذخیره شد.")


async def set_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    value = " ".join(context.args).strip()
    if not value or len(value) > 10000:
        await update.message.reply_text("استفاده: /settemplate متن قالب"); return
    set_setting("template", value)
    await update.message.reply_text("قالب ذخیره شد. جای‌نگهدار اختیاری: {{user_phone}}")


async def add_recipient(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    email = context.args[0].strip().lower() if context.args else ""
    if not EMAIL_RE.fullmatch(email):
        await update.message.reply_text("استفاده: /addrecipient person@example.com\nفقط گیرنده‌ای را اضافه کن که دریافت ایمیل را پذیرفته باشد."); return
    with closing(connect_db()) as conn:
        conn.execute("INSERT OR IGNORE INTO recipients(email) VALUES(?)", (email,))
        conn.commit()
    await update.message.reply_text("گیرنده به فهرست مجاز اضافه شد.")


async def del_recipient(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    email = context.args[0].strip().lower() if context.args else ""
    if not EMAIL_RE.fullmatch(email):
        await update.message.reply_text("استفاده: /delrecipient person@example.com"); return
    with closing(connect_db()) as conn:
        conn.execute("DELETE FROM recipients WHERE email=?", (email,))
        conn.commit()
    await update.message.reply_text("اگر ایمیل در فهرست بود، حذف شد.")


async def list_recipients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    with closing(connect_db()) as conn:
        rows = conn.execute("SELECT email FROM recipients ORDER BY email LIMIT 200").fetchall()
    await update.message.reply_text("گیرندگان مجاز:\n" + ("\n".join(r["email"] for r in rows) if rows else "فهرست خالی است."))


async def set_numeric_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, minimum: int, maximum: int, label: str) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    try:
        value = int(context.args[0])
        if not minimum <= value <= maximum:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text(f"استفاده: /{key} عدد\nمحدودهٔ مجاز: {minimum} تا {maximum}"); return
    set_setting(key, str(value))
    await update.message.reply_text(f"{label} ذخیره شد: {value}")


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Requested interval is stored as configuration only; sending is not implemented.
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    try:
        value = int(context.args[0])
        if not 1 <= value <= 86400: raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /setinterval <ثانیه> (۱ تا ۸۶۴۰۰)"); return
    set_setting("interval_seconds", str(value))
    await update.message.reply_text(f"فاصلهٔ درخواستی ذخیره شد: {value} ثانیه. محدودیت‌های Gmail در زمان اتصال ارسال باید رعایت شوند.")


async def set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    try:
        value = int(context.args[0])
        if not 1 <= value <= 10080: raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /setduration <دقیقه> (۱ تا ۱۰۰۸۰)"); return
    set_setting("duration_minutes", str(value))
    await update.message.reply_text(f"مدت کمپین ذخیره شد: {value} دقیقه.")


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("این دستور فقط برای ادمین است."); return
    with closing(connect_db()) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM recipients").fetchone()["n"]
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    await update.message.reply_text(
        f"تنظیمات فعلی:\nموضوع: {get_setting('subject')}\n"
        f"قالب: {get_setting('template')}\n"
        f"فاصلهٔ درخواستی: {get_setting('interval_seconds')} ثانیه\n"
        f"مدت: {get_setting('duration_minutes')} دقیقه\n"
        f"گیرندگان مجاز: {count}\nکاربران ثبت‌شده: {users}\n\n"
        "ارسال Gmail هنوز فعال نیست."
    )



async def wa_enable_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    value = context.args[0].lower() if context.args else ""
    if value not in {"on", "off"}:
        await update.effective_message.reply_text("استفاده: /wa_enable on یا /wa_enable off")
        return
    set_setting("whatsapp_enabled", "1" if value == "on" else "0")
    await update.effective_message.reply_text(
        "دسترسی کاربران به قابلیت واتساپ فعال شد." if value == "on"
        else "قابلیت واتساپ برای کاربران غیرفعال شد. دستورات مدیریتی اتصال همچنان فقط برای ادمین هستند."
    )


async def wa_template_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    value = " ".join(context.args).strip()
    if not value or len(value) > 4000:
        await update.effective_message.reply_text("استفاده: /wa_template متن پیام (حداکثر ۴۰۰۰ نویسه)")
        return
    set_setting("whatsapp_template", value)
    await update.effective_message.reply_text("قالب پیش‌فرض واتساپ ذخیره شد.")


async def wa_user_limit_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    try:
        value = int(context.args[0])
        if not 1 <= value <= 20:
            raise ValueError
    except (IndexError, ValueError):
        await update.effective_message.reply_text("استفاده: /wa_user_limit عدد (۱ تا ۲۰ درخواست در روز)")
        return
    set_setting("whatsapp_daily_limit_per_user", str(value))
    await update.effective_message.reply_text(f"سقف روزانهٔ درخواست هر کاربر روی {value} تنظیم شد.")


async def wa_policy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    enabled = get_setting("whatsapp_enabled", "0") == "1"
    await update.effective_message.reply_text(
        "سیاست فعلی واتساپ:\\n"
        f"دسترسی کاربران: {'فعال' if enabled else 'غیرفعال'}\\n"
        f"قالب پیام: {get_setting('whatsapp_template')}\\n"
        f"سقف روزانهٔ درخواست هر کاربر: {get_setting('whatsapp_daily_limit_per_user', '5')}\\n"
        "تأیید قبل از ارسال: الزامی\\n"
        "ارسال باید فقط به مخاطب مجاز و رضایت‌داده انجام شود. عملیات تکراری بلاک/آنبلاک یا گزارش انبوه ارائه نمی‌شود."
    )


async def whatsapp_request(method: str, path: str, payload: dict | None = None) -> dict:
    if not WHATSAPP_SERVICE_URL or not WHATSAPP_SERVICE_TOKEN:
        raise RuntimeError("WhatsApp service is not configured in Railway Variables.")
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.request(
            method,
            f"{WHATSAPP_SERVICE_URL}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {WHATSAPP_SERVICE_TOKEN}"},
        )
        response.raise_for_status()
        return response.json()


async def wa_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    if len(context.args) == 1:
        account_id, raw_phone = "main", context.args[0]
    elif len(context.args) >= 2:
        account_id, raw_phone = context.args[0], context.args[1]
    else:
        await update.effective_message.reply_text("استفاده: /wa_pair <شناسه_حساب> <شماره>\nمثال: /wa_pair work 93700123456")
        return
    phone = re.sub(r"\D", "", raw_phone)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", account_id) or not 8 <= len(phone) <= 15:
        await update.effective_message.reply_text("شناسه حساب یا شماره معتبر نیست.")
        return
    try:
        result = await whatsapp_request("POST", "/pairing-code", {"accountId": account_id, "phone": phone})
        code = result.get("code")
        if code:
            await update.effective_message.reply_text(
                f"شناسه حساب: {account_id}\nکد پیوند: {code}\n\n"
                "در واتساپ: Settings → Linked devices → Link a device → Link with phone number. کد را با کسی شریک نکن."
            )
        else:
            await update.effective_message.reply_text(result.get("message", "کد دریافت نشد."))
    except Exception as exc:
        log.warning("WhatsApp pairing request failed: %s", exc)
        await update.effective_message.reply_text("دریافت کد ناموفق بود؛ سرویس واتساپ را بررسی کن.")


async def wa_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    try:
        result = await whatsapp_request("GET", "/accounts")
        rows = result.get("accounts", [])
        if not rows:
            await update.effective_message.reply_text("هنوز حسابی ثبت نشده. از /wa_pair <شناسه> <شماره> استفاده کن.")
            return
        lines = ["حساب‌های ثبت‌شده (فقط ادمین می‌بیند):"]
        for row in rows:
            lines.append(f"• {row.get('id')}: {'متصل' if row.get('connected') else row.get('status', 'نامشخص')}")
        await update.effective_message.reply_text("\n".join(lines))
    except Exception:
        await update.effective_message.reply_text("فهرست حساب‌ها در دسترس نیست؛ سرویس واتساپ را بررسی کن.")


async def wa_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    try:
        result = await whatsapp_request("GET", "/status")
        await update.effective_message.reply_text(
            "وضعیت WhatsApp: " + ("متصل ✅" if result.get("connected") else "متصل نیست ❌")
        )
    except Exception:
        await update.effective_message.reply_text(
            "به سرویس WhatsApp دسترسی نیست. متغیرهای Railway را بررسی کن."
        )


async def wa_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    if len(context.args) < 3:
        await update.effective_message.reply_text("استفاده: /wa_send <شناسه_حساب> <شماره> <متن پیام>")
        return
    account_id = context.args[0]
    phone = re.sub(r"\D", "", context.args[1])
    message = " ".join(context.args[2:]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", account_id) or not 8 <= len(phone) <= 15 or not message or len(message) > 4000:
        await update.effective_message.reply_text("شناسه حساب، شماره یا متن پیام معتبر نیست.")
        return
    try:
        result = await whatsapp_request("POST", "/send", {"accountId": account_id, "phone": phone, "message": message})
        await update.effective_message.reply_text("درخواست ارسال تحویل شد." if result.get("ok") else "ارسال انجام نشد.")
    except Exception:
        await update.effective_message.reply_text("ارسال ناموفق بود؛ ابتدا /wa_accounts را بررسی کن.")


async def wa_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    await update.effective_message.reply_text(
        "دستورهای WhatsApp (فقط ادمین):\n"
        "/wa_pair <شناسه> <شماره> — افزودن حساب؛ مثال /wa_pair work 93700123456\n"
        "/wa_accounts — فهرست حساب‌ها (فقط ادمین)\n"
        "/wa_status — وضعیت اتصال حساب اصلی\n"
        "/wa_send <شناسه> <شماره> <متن> — ارسال آزمایشی توسط ادمین\n\n"
        "پیام‌های دریافتی مستقیم به چت ادمین فرستاده می‌شوند. "
        "کد پیوند و اطلاعات نشست را با کسی شریک نکن."
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in Railway Variables or .env.")
    if not ADMIN_ID_RAW.isdigit():
        raise RuntimeError("ADMIN_ID must be your numeric Telegram user ID.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("setsubject", set_subject))
    app.add_handler(CommandHandler("settemplate", set_template))
    app.add_handler(CommandHandler("addrecipient", add_recipient))
    app.add_handler(CommandHandler("delrecipient", del_recipient))
    app.add_handler(CommandHandler("recipients", list_recipients))
    app.add_handler(CommandHandler("setinterval", set_interval))
    app.add_handler(CommandHandler("setduration", set_duration))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("wa_enable", wa_enable_setting))
    app.add_handler(CommandHandler("wa_template", wa_template_setting))
    app.add_handler(CommandHandler("wa_user_limit", wa_user_limit_setting))
    app.add_handler(CommandHandler("wa_policy", wa_policy))
    app.add_handler(CommandHandler("wa", wa_help))
    app.add_handler(CommandHandler("wa_pair", wa_pair))
    app.add_handler(CommandHandler("wa_accounts", wa_accounts))
    app.add_handler(CommandHandler("wa_status", wa_status))
    app.add_handler(CommandHandler("wa_send", wa_send))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Gmail Campaign Bot starter is starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
