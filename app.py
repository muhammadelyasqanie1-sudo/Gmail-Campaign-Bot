"""Telegram bot starter for admin-managed Gmail campaign settings.

This scaffold does not send email. Gmail API/OAuth integration is a separate step.
"""
import logging
import os
import re
import sqlite3
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
        """)
        defaults = {
            "subject": "پیام برای شما",
            "template": DEFAULT_TEMPLATE,
            "interval_seconds": "60",
            "duration_minutes": "2",
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
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📧 ارسال ایمیل")]], resize_keyboard=True
    )
    await update.message.reply_text(
        "سلام! به ربات خوش آمدید.\n"
        "برای ادامه، گزینهٔ «ارسال ایمیل» را انتخاب کنید.\n\n"
        "این نسخه هنوز ارسال ایمیل را انجام نمی‌دهد؛ اتصال امن Gmail باید توسط ادمین پیکربندی شود.",
        reply_markup=keyboard,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    text = (update.message.text or "").strip()
    uid = update.effective_user.id
    if text == "📧 ارسال ایمیل":
        context.user_data["awaiting_phone"] = True
        await update.message.reply_text(
            "لطفاً شمارهٔ تلفن خود را با کد کشور وارد کنید؛ مثلاً +93700123456.\n"
            "این شماره طبق قالبی که ادمین تعیین کرده جایگزین {{user_phone}} می‌شود.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if context.user_data.get("awaiting_phone"):
        if not PHONE_RE.fullmatch(text):
            await update.message.reply_text(
                "فرمت شماره درست نیست. لطفاً با + و کد کشور وارد کنید؛ مثلاً +93700123456."
            )
            return
        with closing(connect_db()) as conn:
            conn.execute("UPDATE users SET phone=?, phone_submitted_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (text, uid))
            conn.commit()
        context.user_data["awaiting_phone"] = False
        await update.message.reply_text(
            "شماره ثبت شد. تنظیمات کمپین از پیش تعیین‌شده دریافت شد.\n"
            "در حال حاضر ارسال ایمیل در این نسخه غیرفعال است تا اتصال Gmail و کنترل رضایت گیرندگان پیکربندی شود."
        )
        return
    await update.message.reply_text("برای شروع /start را بزنید یا از منو گزینهٔ ارسال ایمیل را انتخاب کنید.")


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
        "/settings\n\n"
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Gmail Campaign Bot starter is starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
