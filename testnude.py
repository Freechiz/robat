import telebot
import sqlite3
import random
import logging
import threading
import time
from telebot import types
import re

# ----------------- تنظیمات اولیه -----------------
logging.basicConfig(level=logging.INFO)

API_TOKEN = '7833271278:AAEH5LWGGxv42IJEfyrYw_2QrDydTb9dE7M'
bot = telebot.TeleBot(API_TOKEN)

# متغیرهای عضویت اجباری و پیام مربوطه
FORCED_GROUP = None
FORCED_MEMBERSHIP_MESSAGE = "لطفاً برای ارسال پیام ابتدا عضو گروه شوید."

# ---------------- اتصال به دیتابیس اصلی ----------------
conn = sqlite3.connect('bot_data.db', check_same_thread=False)
cursor = conn.cursor()

# بررسی و به‌روزرسانی ساختار جدول config
try:
    cursor.execute("SELECT group_id FROM config LIMIT 1")
except sqlite3.OperationalError:
    cursor.execute("DROP TABLE IF EXISTS config")

cursor.execute('''
CREATE TABLE IF NOT EXISTS config (
    group_id INTEGER,
    command TEXT,
    message TEXT,
    PRIMARY KEY (group_id, command)
)
''')

# ایجاد سایر جداول
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    warnings INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0,
    messages_count INTEGER DEFAULT 0
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS owner_groups (
    owner_id INTEGER PRIMARY KEY,
    group_id INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS group_locks (
    group_id INTEGER,
    lock_name TEXT,
    value INTEGER,
    PRIMARY KEY (group_id, lock_name)
)
''')
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS group_members_db (
    group_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY (group_id, user_id)
)
''')
conn.commit()

# ------------------ دیتابیس قفل‌ها -----------------
LOCK_DB_FILE = 'lock_status.db'
lock_conn = None  # متغیر اتصال جداگانه برای دیتابیس قفل‌ها

def init_lock_db():
    global lock_conn
    lock_conn = sqlite3.connect(LOCK_DB_FILE, check_same_thread=False)
    cursor_lock = lock_conn.cursor()
    cursor_lock.execute('''
        CREATE TABLE IF NOT EXISTS locks (
            chat_id INTEGER PRIMARY KEY,
            lock_text INTEGER NOT NULL,
            lock_media INTEGER NOT NULL
        )
    ''')
    lock_conn.commit()

def load_lock_status():
    global lock_text_status, lock_media_status
    cursor_lock = lock_conn.cursor()
    cursor_lock.execute('SELECT chat_id, lock_text, lock_media FROM locks')
    rows = cursor_lock.fetchall()
    for row in rows:
        chat_id, lock_text_val, lock_media_val = row
        lock_text_status[chat_id] = lock_text_val
        lock_media_status[chat_id] = lock_media_val

def update_db(chat_id):
    cursor_lock = lock_conn.cursor()
    cursor_lock.execute('''
        INSERT INTO locks (chat_id, lock_text, lock_media)
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET lock_text=excluded.lock_text, lock_media=excluded.lock_media
    ''', (chat_id, lock_text_status.get(chat_id, 0), lock_media_status.get(chat_id, 0)))
    lock_conn.commit()

# دیکشنری‌های وضعیت قفل اضافه شده جدید
# 0 یعنی باز، 1 یعنی بسته
lock_text_status = {}
lock_media_status = {}

# مقداردهی اولیه دیتابیس قفل‌ها و بارگذاری وضعیت‌ها
init_lock_db()
load_lock_status()


def init_lock_db():
    global lock_conn
    lock_conn = sqlite3.connect(LOCK_DB_FILE, check_same_thread=False)
    cursor_lock = lock_conn.cursor()
    cursor_lock.execute('''
        CREATE TABLE IF NOT EXISTS locks (
            chat_id INTEGER PRIMARY KEY,
            lock_text INTEGER NOT NULL,
            lock_media INTEGER NOT NULL
        )
    ''')
    lock_conn.commit()

def load_lock_status():
    global lock_text_status, lock_media_status
    cursor_lock = lock_conn.cursor()
    cursor_lock.execute('SELECT chat_id, lock_text, lock_media FROM locks')
    rows = cursor_lock.fetchall()
    for row in rows:
        chat_id, lock_text_val, lock_media_val = row
        lock_text_status[chat_id] = lock_text_val
        lock_media_status[chat_id] = lock_media_val

def update_db(chat_id):
    cursor_lock = lock_conn.cursor()
    cursor_lock.execute('''
        INSERT INTO locks (chat_id, lock_text, lock_media)
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET lock_text=excluded.lock_text, lock_media=excluded.lock_media
    ''', (chat_id, lock_text_status.get(chat_id, 0), lock_media_status.get(chat_id, 0)))
    lock_conn.commit()


# تنظیمات پیش‌فرض قفل‌ها
DEFAULT_LOCK_SETTINGS = {
    'banned_words': 1,
    'links': 1,
    'long_text': 1,
    'videos': 1,
    'photos': 1,
    'audio': 1,
    'voice': 1,
    'files': 1,
    'gif': 1,
    'sticker': 1,
    'forward': 1,
    'filter_words': 1,
    'tag_enabled': 1,
    'welcome': 1,
    'force_membership': 1,
    'ad_required': 1  # مقدار پیش‌فرض برای تایید اد
}

BANNED_WORDS = ["مادر جنده", 'badword2']
lottery_entries = {}
MAX_TEXT_LENGTH = 300

# ----------------- توابع تنظیمات قفل‌ها و config -----------------
def get_group_lock_setting(group_id, lock_name):
    cursor.execute("SELECT value FROM group_locks WHERE group_id=? AND lock_name=?", (group_id, lock_name))
    row = cursor.fetchone()
    if row is None:
        default = DEFAULT_LOCK_SETTINGS.get(lock_name, 1)
        cursor.execute("INSERT INTO group_locks (group_id, lock_name, value) VALUES (?, ?, ?)", (group_id, lock_name, default))
        conn.commit()
        return default
    else:
        return row[0]

def set_group_lock_setting(group_id, lock_name, value):
    cursor.execute("INSERT OR REPLACE INTO group_locks (group_id, lock_name, value) VALUES (?, ?, ?)", (group_id, lock_name, value))
    conn.commit()

def get_config(command_name, group_id):
    cursor.execute("SELECT message FROM config WHERE command = ? AND group_id = ?", (command_name, group_id))
    row = cursor.fetchone()
    return row[0] if row else None

def set_config(command_name, message_text, group_id):
    cursor.execute("INSERT OR REPLACE INTO config (group_id, command, message) VALUES (?, ?, ?)", (group_id, command_name, message_text))
    conn.commit()

def get_chat_config(command_name, chat):
    group_id = chat.id if chat.type != "private" else chat.from_user.id
    return get_config(command_name, group_id)

# ------------------ دیکشنری‌های وضعیت قفل اضافه شده جدید------------------
# 0 یعنی باز، 1 یعنی بسته
lock_text_status = {}
lock_media_status = {}

# دیکشنری برای نگهداری تایمرهای قفل زمان‌دار
timed_lock_timers = {}

# دیکشنری برای وضعیت پیام رگباری (0 = غیرفعال، 1 = فعال)
recurring_message_status = {}

# دیکشنری برای نگهداری کاربران سکوت شده به صورت {chat_id: set(user_id, ...)}
muted_users = {}

def get_lock_text(chat_id):
    return lock_text_status.get(chat_id, 0)

def set_lock_text(chat_id, value):
    lock_text_status[chat_id] = value
    update_db(chat_id)

def get_lock_media(chat_id):
    return lock_media_status.get(chat_id, 0)

def set_lock_media(chat_id, value):
    lock_media_status[chat_id] = value
    update_db(chat_id)

def get_recurring_message(chat_id):
    return recurring_message_status.get(chat_id, 0)

def set_recurring_message(chat_id, value):
    recurring_message_status[chat_id] = value

def update_group_permissions(chat_id):
    text_locked = get_lock_text(chat_id)
    media_locked = get_lock_media(chat_id)
    permissions = types.ChatPermissions(
        can_send_messages=(text_locked == 0),
        can_send_media_messages=(media_locked == 0),
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True
    )
    bot.set_chat_permissions(chat_id, permissions)

def is_admin(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except Exception as e:
        return False

# ----------------- توابع کمکی -----------------
def is_admin(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except Exception as e:
        logging.error(f"خطا در بررسی وضعیت مدیر: {e}")
        return False

def main_menu_data(user):
    first_name = user.first_name
    last_name = user.last_name if user.last_name else ""
    full_name = f"{first_name} {last_name}".strip()
    name_tag = f"<a href='tg://user?id={user.id}'>{full_name}</a>"
    text = ( "...")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("گروه پشتیبانی ❤", url="https://t.me/freeguys_ir"))
    keyboard.row(
        types.InlineKeyboardButton("تنظیمات", callback_data="capabilities"),
        types.InlineKeyboardButton("📚 راهنمای ربات", callback_data="help_main")
    )
    keyboard.row(types.InlineKeyboardButton("➕ افزودن ربات به گروه😉", url="https://t.me/freechiz_bot?startgroup=new"))
    return text, keyboard

# دیکشنری‌های سراسری برای اعضای گروه و ثبت تعداد دعوت (اد)
group_members = {}
ad_counts = {}  # ساختار: { group_id: { user_id: count } }

# ----------------- دستورات اصلی -----------------
@bot.message_handler(commands=['start'])
def start(message):
    if message.chat.type != "private":
        return
    text, keyboard = main_menu_data(message.from_user)
    bot.send_message(message.chat.id, text, reply_markup=keyboard, parse_mode="HTML")

@bot.message_handler(commands=['config'])
def config_handler(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "شما مجوز استفاده از این دستور را ندارید.")
        return
    try:
        parts = message.text.split(' ', 2)
        if len(parts) < 3:
            bot.reply_to(message, "استفاده: /config <command> <custom_message>")
            return
        command_name = parts[1].strip()
        custom_message = parts[2].strip()
        group_id = message.chat.id if message.chat.type != "private" else message.from_user.id
        set_config(command_name, custom_message, group_id)
        bot.reply_to(message, f"پیام سفارشی برای دستور '{command_name}' تنظیم شد.")
    except Exception as e:
        logging.error(f"خطا در فرمان /config: {e}")
        bot.reply_to(message, "خطایی در تنظیم پیکربندی رخ داد.")

@bot.message_handler(commands=['warn'])
def warn_handler(message):
    if not message.reply_to_message:
        bot.reply_to(message, "برای اخطار دادن به کاربر، باید به پیام او ریپلای کنید.")
        return
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "شما مجوز استفاده از این دستور را ندارید.")
        return

    target_user_id = message.reply_to_message.from_user.id
    cursor.execute("SELECT warnings FROM users WHERE user_id = ?", (target_user_id,))
    row = cursor.fetchone()
    warnings = row[0] + 1 if row else 1

    if warnings >= 3:
        try:
            bot.ban_chat_member(message.chat.id, target_user_id)
            custom_text = get_config('ban', message.chat.id)
            if custom_text:
                bot.send_message(message.chat.id, custom_text)
            else:
                bot.send_message(message.chat.id, "🚫 کاربر به دلیل اخطارهای متعدد بن شد.")
            cursor.execute("UPDATE users SET banned = 1, warnings = 0 WHERE user_id = ?", (target_user_id,))
        except Exception as e:
            logging.error(f"خطا در بن کردن کاربر: {e}")
            bot.reply_to(message, "عملیات بن انجام نشد.")
    else:
        cursor.execute("INSERT OR REPLACE INTO users (user_id, warnings) VALUES (?, ?)", (target_user_id, warnings))
        custom_text = get_config('warn', message.chat.id)
        if custom_text:
            bot.send_message(message.chat.id, custom_text.replace("{count}", str(warnings)))
        else:
            bot.send_message(message.chat.id, f"⚠️ اخطار {warnings}/3 صادر شد.")
    conn.commit()

@bot.message_handler(commands=['lottery'])
def lottery_handler(message):
    if not message.reply_to_message:
         bot.reply_to(message, "لطفاً برای اجرای قرعه‌کشی، به پیام مربوطه ریپلای کنید.")
         return
    parent_id = message.reply_to_message.message_id
    if parent_id not in lottery_entries or not lottery_entries[parent_id]:
         bot.reply_to(message, "شرکت‌کننده‌ای برای این قرعه‌کشی یافت نشد.")
         return
    winner = random.choice(list(lottery_entries[parent_id]))
    custom_text = get_config('lottery', message.chat.id)
    if custom_text:
         bot.send_message(message.chat.id, custom_text.replace("{winner}", str(winner)))
    else:
         bot.send_message(message.chat.id, f"🎉 برنده قرعه‌کشی: {winner}")

@bot.message_handler(commands=['stats'])
def stats_handler(message):
    cursor.execute("SELECT user_id, messages_count FROM users ORDER BY messages_count DESC LIMIT 5")
    stats = cursor.fetchall()
    if stats:
        stats_message = "📊 کاربران فعال برتر:\n"
        for user_id, count in stats:
            stats_message += f"کاربر {user_id}: {count} پیام\n"
        bot.send_message(message.chat.id, stats_message)
    else:
        bot.send_message(message.chat.id, "آماری موجود نیست.")

@bot.message_handler(commands=['promote'])
def promote_handler(message):
    if not message.reply_to_message:
        bot.reply_to(message, "برای ارتقاء کاربر، باید به پیام او ریپلای کنید.")
        return
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "شما مجوز استفاده از این دستور را ندارید.")
        return
    target_user_id = message.reply_to_message.from_user.id
    try:
        bot.promote_chat_member(message.chat.id, target_user_id,
                                can_change_info=True, can_post_messages=True,
                                can_edit_messages=True, can_delete_messages=True,
                                can_invite_users=True, can_restrict_members=True,
                                can_pin_messages=True, can_promote_members=True)
        custom_text = get_config('promote', message.chat.id)
        if custom_text:
            bot.send_message(message.chat.id, custom_text.replace("{user}", str(target_user_id)))
        else:
            bot.send_message(message.chat.id, f"کاربر {target_user_id} به عنوان مدیر ارتقاء یافت.")
    except Exception as e:
        logging.error(f"خطا در ارتقاء کاربر: {e}")
        bot.reply_to(message, "عملیات ارتقاء انجام نشد.")

@bot.message_handler(commands=['demote'])
def demote_handler(message):
    if not message.reply_to_message:
        bot.reply_to(message, "برای عزل کاربر، باید به پیام او ریپلای کنید.")
        return
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "شما مجوز استفاده از این دستور را ندارید.")
        return
    target_user_id = message.reply_to_message.from_user.id
    try:
        bot.promote_chat_member(message.chat.id, target_user_id,
                                can_change_info=False, can_post_messages=False,
                                can_edit_messages=False, can_delete_messages=False,
                                can_invite_users=False, can_restrict_members=False,
                                can_pin_messages=False, can_promote_members=False)
        custom_text = get_config('demote', message.chat.id)
        if custom_text:
            bot.send_message(message.chat.id, custom_text.replace("{user}", str(target_user_id)))
        else:
            bot.send_message(message.chat.id, f"کاربر {target_user_id} از مدیران عزل شد.")
    except Exception as e:
        logging.error(f"خطا در عزل کاربر: {e}")
        bot.reply_to(message, "عملیات عزل انجام نشد.")

@bot.message_handler(commands=['cleanup'])
def cleanup_handler(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "شما مجوز استفاده از این دستور را ندارید.")
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "استفاده: /cleanup <user_id>")
            return
        target_user_id = int(parts[1])
        bot.send_message(message.chat.id, "پاکسازی آغاز شد. در صورت نیاز، پیام‌های نامطلوب را به‌صورت دستی حذف کنید.")
    except Exception as e:
        logging.error(f"خطا در فرمان /cleanup: {e}")
        bot.reply_to(message, "خطایی در اجرای پاکسازی رخ داد.")

@bot.message_handler(func=lambda message: message.text and message.text.strip() == "تنظیمات")
def settings_command_handler(message):
    if message.chat.type == "private":
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("تنظیمات", callback_data="capabilities"))
        bot.send_message(message.chat.id, "برای دسترسی به قابلیت‌ها روی دکمه زیر کلیک کنید:", reply_markup=keyboard)
    else:
        if not is_admin(message.chat.id, message.from_user.id):
            bot.reply_to(message, "شما مجوز استفاده از این دستور را ندارید.")
            return
        group_id = message.chat.id
        keyboard = get_lock_settings_keyboard(group_id)
        text = f"تنظیمات قفل‌های گروه {group_id}:"
        bot.send_message(message.chat.id, text, reply_markup=keyboard)

@bot.message_handler(func=lambda message: message.text and message.text.strip() == "راهنما")
def help_message_handler(message):
    if message.chat.type != "private" and not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "این دستور فقط برای ادمین‌ها مجاز است.")
        return

    markup = get_main_help_menu(message.chat.type)
    help_text = "📚 لطفاً یکی از گزینه‌های راهنمایی را انتخاب کنید:"
    bot.send_message(message.chat.id, help_text, reply_markup=markup, parse_mode="Markdown")

def get_main_help_menu(chat_type):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("دستور", callback_data="help_dastoor"),
        types.InlineKeyboardButton("کاربردی", callback_data="help_karbordi")
    )
    markup.add(
        types.InlineKeyboardButton("کریپتو", callback_data="help_crypto"),
        types.InlineKeyboardButton("درباره ما", callback_data="help_about")
    )
    if chat_type == "private":
        markup.add(types.InlineKeyboardButton("بازگشت", callback_data="back_to_start"))
    return markup

# ------------------ توابع سکوت/رفع سکوت برای کاربران جدید------------------
def mute_user(chat_id, user_id):
    try:
        bot.restrict_chat_member(chat_id, user_id, permissions=types.ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False
        ))
        # افزودن کاربر به دیکشنری سکوت
        muted_users.setdefault(chat_id, set()).add(user_id)
    except Exception as e:
        print(f"Error muting user {user_id} in chat {chat_id}: {e}")

def unmute_user(chat_id, user_id):
    try:
        bot.restrict_chat_member(chat_id, user_id, permissions=types.ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        ))
        # حذف کاربر از دیکشنری سکوت
        if chat_id in muted_users and user_id in muted_users[chat_id]:
            muted_users[chat_id].remove(user_id)
    except Exception as e:
        print(f"Error unmuting user {user_id} in chat {chat_id}: {e}")

# ------------------ تابع برای آزادسازی قفل زمان‌دار ------------------
def timed_unlock(chat_id):
    set_lock_text(chat_id, 0)
    set_lock_media(chat_id, 0)
    update_group_permissions(chat_id)
    bot.send_message(chat_id, "زمان قفل تمام شد. قفل متن و رسانه غیرفعال شدند.")
    if chat_id in timed_lock_timers:
        del timed_lock_timers[chat_id]

#خوش اومد گویی
@bot.message_handler(content_types=['new_chat_members'])
def welcome_new_member(message):
    group_id = message.chat.id
    if get_group_lock_setting(group_id, 'welcome') != 1:
        return
    for new_member in message.new_chat_members:
        custom_text = get_config('welcome', group_id)
        if custom_text:
            welcome_text = custom_text.replace("{name}", new_member.first_name)
        else:
            welcome_text = f"خوش آمدید {new_member.first_name} به گروه!"
        bot.reply_to(message, welcome_text)

# ------------------ هندلرهای دستورات قفل متنی/رسانه جدید ------------------
@bot.message_handler(commands=['locktext'])
def lock_text_cmd(message):
    if message.chat.type in ['group', 'supergroup']:
        if not is_admin(message.chat.id, message.from_user.id):
            bot.reply_to(message, "فقط ادمین می‌تواند این دستور را استفاده کند.")
            return
        chat_id = message.chat.id
        set_lock_text(chat_id, 1)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل متن فعال شد. اکنون هیچ پیامی (متنی/رسانه‌ای) قابل ارسال نیست.")
    else:
        bot.reply_to(message, "این دستور فقط در گروه قابل استفاده است.")

@bot.message_handler(commands=['unlocktext'])
def unlock_text_cmd(message):
    if message.chat.type in ['group', 'supergroup']:
        if not is_admin(message.chat.id, message.from_user.id):
            bot.reply_to(message, "فقط ادمین می‌تواند این دستور را استفاده کند.")
            return
        chat_id = message.chat.id
        set_lock_text(chat_id, 0)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل متن غیرفعال شد. ارسال پیام‌های متنی مجدداً آزاد است.")
    else:
        bot.reply_to(message, "این دستور فقط در گروه قابل استفاده است.")

@bot.message_handler(commands=['lockmedia'])
def lock_media_cmd(message):
    if message.chat.type in ['group', 'supergroup']:
        if not is_admin(message.chat.id, message.from_user.id):
            bot.reply_to(message, "فقط ادمین می‌تواند این دستور را استفاده کند.")
            return
        chat_id = message.chat.id
        set_lock_media(chat_id, 1)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل رسانه فعال شد. ارسال عکس، ویدیو و سایر مدیاها بسته است.")
    else:
        bot.reply_to(message, "این دستور فقط در گروه قابل استفاده است.")

@bot.message_handler(commands=['unlockmedia'])
def unlock_media_cmd(message):
    if message.chat.type in ['group', 'supergroup']:
        if not is_admin(message.chat.id, message.from_user.id):
            bot.reply_to(message, "فقط ادمین می‌تواند این دستور را استفاده کند.")
            return
        chat_id = message.chat.id
        set_lock_media(chat_id, 0)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل رسانه غیرفعال شد. ارسال انواع مدیا مجدداً آزاد است.")
    else:
        bot.reply_to(message, "این دستور فقط در گروه قابل استفاده است.")

# ------------------ هندلر دستورات متنی و قفل زمان‌دار جدید------------------
@bot.message_handler(func=lambda m: m.text is not None)
def admin_text_commands(message):
    if message.chat.type not in ['group', 'supergroup']:
        return
    if not is_admin(message.chat.id, message.from_user.id):
        return

    chat_id = message.chat.id
    text = message.text.strip()
    
    # مثال: الگوی قفل زمان‌دار مانند "1 ساعت قفل کردن"
    timed_lock_pattern = r'^(\d+(\.\d+)?)\s*ساعت\s*قفل\s*کردن$'
    if re.match(timed_lock_pattern, text):
        match = re.match(timed_lock_pattern, text)
        hours = float(match.group(1))
        seconds = hours * 3600

        set_lock_text(chat_id, 1)
        set_lock_media(chat_id, 1)
        update_group_permissions(chat_id)
        
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
        
        timer = threading.Timer(seconds, timed_unlock, args=[chat_id])
        timed_lock_timers[chat_id] = timer
        timer.start()
        
        bot.reply_to(message, f"قفل متن و رسانه به مدت {hours} ساعت فعال شد.")
        return

    # سایر دستورات متنی برای قفل/باز کردن
    if text == "قفل کردن متن":
        set_lock_text(chat_id, 1)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل متن فعال شد.")
    elif text == "باز کردن متن":
        set_lock_text(chat_id, 0)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل متن غیرفعال شد.")
    elif text == "قفل کردن رسانه":
        set_lock_media(chat_id, 1)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل رسانه فعال شد.")
    elif text == "باز کردن رسانه":
        set_lock_media(chat_id, 0)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل رسانه غیرفعال شد.")
    elif text == "قفل کردن":
        set_lock_text(chat_id, 1)
        set_lock_media(chat_id, 1)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل متن و رسانه فعال شد.")
    elif text == "باز کردن":
        set_lock_text(chat_id, 0)
        set_lock_media(chat_id, 0)
        update_group_permissions(chat_id)
        if chat_id in timed_lock_timers:
            timed_lock_timers[chat_id].cancel()
            del timed_lock_timers[chat_id]
        bot.reply_to(message, "قفل متن و رسانه غیرفعال شد.")

# ------------------ قابلیت ضد اسپم با سکوت جدید------------------
spam_tracker = {}

@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'])
def spam_filter(message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    # اگر کاربر سکوت شده باشد، پیام او حذف شود.
    if chat_id in muted_users and user_id in muted_users[chat_id]:
        try:
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            print(f"Error deleting message from muted user {user_id}: {e}")
        return

    # ادامه‌ی بررسی اسپم
    if is_admin(chat_id, user_id):
        return
    now = time.time()
    key = (chat_id, user_id)
    if key not in spam_tracker:
        spam_tracker[key] = []
    spam_tracker[key].append(now)
    spam_tracker[key] = [t for t in spam_tracker[key] if now - t <= 3]
    if len(spam_tracker[key]) > 10:
        # به جای اخراج، کاربر سکوت می‌شود
        mute_user(chat_id, user_id)
        bot.send_message(chat_id, f"کاربر {message.from_user.first_name} به دلیل ارسال بیش از حد پیام در مدت زمان کوتاه سکوت شد.")
        # پیام‌های اخیر او حذف می‌شوند (در آینده توسط هندلر حذف می‌شود)
        del spam_tracker[key]

# ------------------ هندلر ریپلای برای سکوت و رفع سکوت جدید------------------
@bot.message_handler(func=lambda m: m.text is not None and m.reply_to_message is not None)
def mute_unmute_by_reply(message):
    chat_id = message.chat.id
    if not is_admin(chat_id, message.from_user.id):
        return
    text = message.text.strip()
    target_user_id = message.reply_to_message.from_user.id
    if text == "سکوت":
        mute_user(chat_id, target_user_id)
        bot.reply_to(message, f"کاربر {message.reply_to_message.from_user.first_name} سکوت شد.")
    elif text == "رفع سکوت":
        unmute_user(chat_id, target_user_id)
        bot.reply_to(message, f"سکوت کاربر {message.reply_to_message.from_user.first_name} برداشته شد.")

# -------------------- هندلر اعضای جدید --------------------
@bot.message_handler(content_types=['new_chat_members'])
def new_member_handler(message):
    group_id = message.chat.id
    if group_id not in group_members:
        group_members[group_id] = set()
    if group_id not in ad_counts:
        ad_counts[group_id] = {}
    for member in message.new_chat_members:
        group_members[group_id].add(member.id)
        logging.info(f"کاربر {member.id} به گروه {group_id} اضافه شد.")
        try:
            member_status = bot.get_chat_member(group_id, member.id)
        except Exception as e:
            logging.error("Error getting member status: " + str(e))
            continue
        if member_status.status not in ["creator", "administrator"]:
            required_count = int(get_group_lock_setting(group_id, 'ad_required')) if str(get_group_lock_setting(group_id, 'ad_required')).isdigit() else 0
            if required_count != 0:
                # مقدار اولیه دعوت کاربر در گروه
                if member.id not in ad_counts[group_id]:
                    ad_counts[group_id][member.id] = 0
                bot.send_message(group_id,
                                 f"{member.first_name} عزیز، برای شرکت در چت باید {required_count} نفر اد کنید. برای ثبت اد، از دستور /ad استفاده کنید.",
                                 parse_mode="HTML")

@bot.message_handler(content_types=['left_chat_member'])
def left_member_handler(message):
    group_id = message.chat.id
    left = message.left_chat_member
    if left and group_id in group_members and left.id in group_members[group_id]:
        group_members[group_id].remove(left.id)
        logging.info(f"کاربر {left.id} از گروه {group_id} حذف شد.")

@bot.message_handler(commands=['tag'])
def tag_command_handler(message):
    group_id = message.chat.id
    user_id = message.from_user.id
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "این دستور فقط در گروه‌ها قابل استفاده است.")
        return

    try:
        admins = bot.get_chat_administrators(group_id)
    except Exception as e:
        logging.error("خطا در دریافت ادمین‌ها: " + str(e))
        return
    if not any(admin.user.id == user_id for admin in admins):
        bot.reply_to(message, "تنها ادمین‌ها می‌توانند از این دستور استفاده کنند.")
        return

    if get_group_lock_setting(group_id, 'tag_enabled') == 0:
        bot.reply_to(message, "امکان تگ در این گروه غیرفعال است.")
        return

    if group_id not in group_members or not group_members[group_id]:
        bot.reply_to(message, "لیست اعضا یافت نشد.")
        return

    tag_text = ""
    for member_id in group_members[group_id]:
        tag_text += f"[‎](tg://user?id={member_id}) "

    max_length = 4000
    if len(tag_text) > max_length:
        chunks = [tag_text[i:i+max_length] for i in range(0, len(tag_text), max_length)]
        for chunk in chunks:
            bot.send_message(group_id, chunk, parse_mode="Markdown", disable_web_page_preview=True)
    else:
        bot.send_message(group_id, tag_text, parse_mode="Markdown", disable_web_page_preview=True)

def get_lock_settings_keyboard(group_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    banned_val = get_group_lock_setting(group_id, 'banned_words')
    links_val = get_group_lock_setting(group_id, 'links')
    long_text_val = get_group_lock_setting(group_id, 'long_text')
    videos_val = get_group_lock_setting(group_id, 'videos')
    photos_val = get_group_lock_setting(group_id, 'photos')
    audio_val = get_group_lock_setting(group_id, 'audio')
    voice_val = get_group_lock_setting(group_id, 'voice')
    files_val = get_group_lock_setting(group_id, 'files')
    gif_val = get_group_lock_setting(group_id, 'gif')
    sticker_val = get_group_lock_setting(group_id, 'sticker')
    forward_val = get_group_lock_setting(group_id, 'forward')
    filter_val = get_group_lock_setting(group_id, 'filter_words')
    welcome_val = get_group_lock_setting(group_id, 'welcome')
    tag_val = get_group_lock_setting(group_id, 'tag_enabled')
    force_membership_val = get_group_lock_setting(group_id, 'force_membership')
    
    btn_fahsh = types.InlineKeyboardButton(
        f"فحش: {'فعال' if banned_val==1 else 'غیرفعال'}", 
        callback_data=f"toggle_lock|banned_words|{group_id}"
    )
    btn_links = types.InlineKeyboardButton(
        f"لینک‌ها: {'فعال' if links_val==1 else 'غیرفعال'}", 
        callback_data=f"toggle_lock|links|{group_id}"
    )
    btn_long_text = types.InlineKeyboardButton(
        f"پیام‌های طولانی: {'فعال' if long_text_val==1 else 'غیرفعال'}", 
        callback_data=f"toggle_lock|long_text|{group_id}"
    )
    btn_videos = types.InlineKeyboardButton(
        f"فیلم: {'فعال'if videos_val==1 else 'غیرفعال'}", 
        callback_data=f"toggle_lock|videos|{group_id}"
    )
    btn_photos = types.InlineKeyboardButton(
        f"عکس: {'فعال'if photos_val==1 else 'غیرفعال'}", 
        callback_data=f"toggle_lock|photos|{group_id}"
    )
    btn_audio = types.InlineKeyboardButton(
        f"صدا/موسیقی: {'فعال'if audio_val==1 else 'غیرفعال'}", 
        callback_data=f"toggle_lock|audio|{group_id}"
    )
    btn_voice = types.InlineKeyboardButton(
        f"وییس: {'فعال'if voice_val==1 else 'غیرفعال'}", 
        callback_data=f"toggle_lock|voice|{group_id}"
    )
    btn_files = types.InlineKeyboardButton(
        f"فایل: {'فعال'if files_val==1 else 'غیرفعال'}", 
        callback_data=f"toggle_lock|files|{group_id}"
    )
    btn_gif = types.InlineKeyboardButton(
        f"گیف: {'فعال'if gif_val==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|gif|{group_id}"
    )
    btn_sticker = types.InlineKeyboardButton(
        f"استیکر: {'فعال'if sticker_val==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|sticker|{group_id}"
    )
    btn_forward = types.InlineKeyboardButton(
        f"فروارد: {'فعال'if forward_val==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|forward|{group_id}"
    )
    btn_filter = types.InlineKeyboardButton(
        f"فیلتر کلمات: {'فعال'if filter_val==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|filter_words|{group_id}"
    )
    btn_welcome = types.InlineKeyboardButton(
        f"خوش آمد گویی: {'فعال'if welcome_val==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|welcome|{group_id}"
    )
    btn_tag = types.InlineKeyboardButton(
        f"تگ: {'فعال'if tag_val==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|tag_enabled|{group_id}"
    )
    btn_force_membership = types.InlineKeyboardButton(
        f"عضویت اجباری: {'فعال' if force_membership_val==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|force_membership|{group_id}"
    )
    
    markup.add(btn_fahsh, btn_links)
    markup.add(btn_long_text, btn_videos)
    markup.add(btn_photos, btn_audio)
    markup.add(btn_voice, btn_files)
    markup.add(btn_gif, btn_sticker)
    markup.add(btn_forward, btn_filter)
    markup.add(btn_welcome, btn_tag)
    markup.add(btn_force_membership)
    
    ad_required_val = get_group_lock_setting(group_id, 'ad_required')
    btn_ad_off = types.InlineKeyboardButton(
        ("🔓 " if ad_required_val==0 else "🔒 ") + "اد اجباری: غیرفعال",
        callback_data=f"toggle_lock|ad_required|0"
    )
    btn_ad_1 = types.InlineKeyboardButton(
        ("🔓 " if ad_required_val=="1" else "🔒 ") + "اد یک نفر",
        callback_data=f"toggle_lock|ad_required|1"
    )
    btn_ad_3 = types.InlineKeyboardButton(
        ("🔓 " if ad_required_val=="3" else "🔒 ") + "اد سه نفر",
        callback_data=f"toggle_lock|ad_required|3"
    )
    btn_ad_5 = types.InlineKeyboardButton(
        ("🔓 " if ad_required_val=="5" else "🔒 ") + "اد پنج نفر",
        callback_data=f"toggle_lock|ad_required|5"
    )
    markup.add(btn_ad_off, btn_ad_1, btn_ad_3, btn_ad_5)

 # دکمه‌های جدید برای قفل متنی، قفل رسانه و پیام رگباری
    btn_lock_text = types.InlineKeyboardButton(
        f"قفل متنی: {'فعال' if get_lock_text(group_id)==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|lock_text|{group_id}"
    )
    btn_lock_media = types.InlineKeyboardButton(
        f"قفل رسانه: {'فعال' if get_lock_media(group_id)==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|lock_media|{group_id}"
    )
    btn_lock_complete = types.InlineKeyboardButton(
        f"قفل کامل: {'فعال' if (get_lock_text(group_id)==1 and get_lock_media(group_id)==1) else 'غیرفعال'}",
        callback_data=f"toggle_lock|lock_complete|{group_id}"
    )
    btn_recurring = types.InlineKeyboardButton(
        f"پیام رگباری: {'فعال' if get_recurring_message(group_id)==1 else 'غیرفعال'}",
        callback_data=f"toggle_lock|recurring|{group_id}"
    )
    
    markup.add(btn_lock_text, btn_lock_media)
    markup.add(btn_lock_complete)
    markup.add(btn_recurring)
    
    # ... ادامه دکمه‌های قبلی مانند تنظیمات اد ...
    return markup

@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_lock"))
def toggle_lock_handler(call):
    try:
        group_id = call.message.chat.id
        admins = bot.get_chat_administrators(group_id)
        if call.from_user.id not in [admin.user.id for admin in admins]:
            bot.answer_callback_query(call.id, "⛔ فقط مدیران می‌توانند تنظیمات را تغییر دهند.", show_alert=True)
            return
        parts = call.data.split("|")
        if len(parts) != 3:
            bot.answer_callback_query(call.id, "خطای داده.")
            return
        _, lock_name, value = parts

        # تنظیمات مربوط به قفل اد_required
        if lock_name == 'ad_required':
            new_value = int(value)
            set_group_lock_setting(group_id, lock_name, new_value)
        elif lock_name == 'lock_text':
            new_value = 0 if get_lock_text(group_id) == 1 else 1
            set_lock_text(group_id, new_value)
        elif lock_name == 'lock_media':
            new_value = 0 if get_lock_media(group_id) == 1 else 1
            set_lock_media(group_id, new_value)
        elif lock_name == 'lock_complete':
            # اگر هر دو قفل متنی و رسانه فعال باشند، آنها را غیرفعال می‌کند و در غیر این صورت فعال می‌کند.
            if get_lock_text(group_id) == 1 and get_lock_media(group_id) == 1:
                new_value = 0
            else:
                new_value = 1
            set_lock_text(group_id, new_value)
            set_lock_media(group_id, new_value)
        elif lock_name == 'recurring':
            new_value = 0 if get_recurring_message(group_id) == 1 else 1
            set_recurring_message(group_id, new_value)
        else:
            current_value = get_group_lock_setting(group_id, lock_name)
            new_value = 0 if current_value == 1 else 1
            set_group_lock_setting(group_id, lock_name, new_value)

        # به‌روزرسانی دسترسی گروه برای قفل‌های متنی/رسانه/قفل کامل
        if lock_name in ['lock_text', 'lock_media', 'lock_complete']:
            update_group_permissions(group_id)

        lock_descriptions = {
            'banned_words': "فحش",
            'links': "قفل لینک‌ها",
            'long_text': "قفل پیام‌های طولانی",
            'videos': "قفل فیلم",
            'photos': "قفل عکس",
            'audio': "قفل صدا/موسیقی",
            'voice': "قفل وییس",
            'files': "قفل فایل",
            'gif': "قفل گیف",
            'sticker': "قفل استیکر",
            'forward': "قفل فروارد",
            'filter_words': "فیلتر کلمات",
            'welcome': "قفل خوش‌آمدگویی",
            'tag_enabled': "تگ",
            'force_membership': "عضویت اجباری",
            'ad_required': "اد اجباری",
            'lock_text': "قفل متنی",
            'lock_media': "قفل رسانه",
            'lock_complete': "قفل کامل",
            'recurring': "پیام رگباری"
        }

        if lock_name == 'ad_required':
            lock_status = "غیرفعال" if new_value == 0 else f"فعال ({new_value} نفر)"
        else:
            lock_status = "فعال" if new_value == 1 else "غیرفعال"
        description = lock_descriptions.get(lock_name, lock_name)
        bot.answer_callback_query(call.id, f"{description} {lock_status} شد.")

        if call.message.reply_markup:
            updated_markup = get_lock_settings_keyboard(group_id)
            bot.edit_message_reply_markup(group_id, call.message.message_id, reply_markup=updated_markup)
    except Exception as e:
        logging.error(f"Error in toggle_lock_handler: {e}")
        bot.answer_callback_query(call.id, "خطایی رخ داده است.")

@bot.message_handler(commands=['invites'])
def check_invites(message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    conn_local = sqlite3.connect("bot_data.db", check_same_thread=False)
    cur = conn_local.cursor()
    cur.execute("SELECT invited_count FROM users WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    row = cur.fetchone()
    cur.close()
    conn_local.close()

    invited_count = row[0] if row else 0
    bot.send_message(chat_id, f"📊 شما تاکنون {invited_count} نفر را دعوت کرده‌اید.")

@bot.message_handler(func=lambda message: message.chat.type == "private" and message.text and message.text.isdigit())
def group_id_handler(message):
    group_id = int(message.text)
    cursor.execute("SELECT group_id FROM owner_groups WHERE owner_id = ?", (message.from_user.id,))
    row = cursor.fetchone()
    if row:
        stored_group_id = row[0]
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"گروه {stored_group_id}", callback_data=f"capabilities_main|{stored_group_id}"))
        bot.send_message(message.chat.id,
                         f"گروه {stored_group_id} قبلاً ثبت شده است. برای ورود به تنظیمات روی دکمه زیر کلیک کنید.",
                         reply_markup=markup)
        return
    try:
        member = bot.get_chat_member(group_id, message.from_user.id)
        if member.status == "creator":
            cursor.execute("INSERT INTO owner_groups (owner_id, group_id) VALUES (?, ?)", (message.from_user.id, group_id))
            conn.commit()
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"گروه {group_id}", callback_data=f"capabilities_main|{group_id}"))
            bot.send_message(message.chat.id,
                             f"گروه {group_id} تایید شد. برای ورود به محیط قابلیت‌های گروه، روی دکمه مربوطه کلیک کنید.",
                             reply_markup=markup)
        else:
            bot.send_message(message.chat.id, "شما مالک این گروه نیستید.")
    except Exception as e:
        logging.error(f"Error in group_id_handler: {e}")
        bot.send_message(message.chat.id, "گروه یافت نشد یا مشکلی پیش آمده.")

@bot.callback_query_handler(func=lambda call: call.data == "capabilities")
def capabilities_handler(call):
    if call.message.chat.type == "private":
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text="لطفاً آیدی گروه خود را ارسال کنید:")
        bot.answer_callback_query(call.id)
    else:
        group_id = call.message.chat.id
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_settings = types.InlineKeyboardButton("تنظیمات گروه", callback_data=f"group_settings|{group_id}")
        btn_crypto = types.InlineKeyboardButton("کریپتو و یاداوری", callback_data=f"crypto_reminder|{group_id}")
        btn_back = types.InlineKeyboardButton("بازگشت", callback_data="capabilities_back")
        markup.add(btn_settings, btn_crypto)
        markup.add(btn_back)
        text = f"منوی قابلیت‌های گروه {group_id}:"
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=text,
                              reply_markup=markup)
        bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("capabilities_main|"))
def capabilities_main_handler(call):
    parts = call.data.split("|")
    if len(parts) < 2:
        return
    group_id = parts[1]
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_settings = types.InlineKeyboardButton("تنظیمات گروه", callback_data=f"group_settings|{group_id}")
    btn_crypto = types.InlineKeyboardButton("کریپتو و یاداوری", callback_data=f"crypto_reminder|{group_id}")
    btn_back = types.InlineKeyboardButton("بازگشت", callback_data="capabilities_back")
    markup.add(btn_settings, btn_crypto)
    markup.add(btn_back)
    
    text = f"منوی قابلیت‌های گروه {group_id}:"
    bot.edit_message_text(chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          text=text,
                          reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("group_settings|"))
def group_settings_handler(call):
    parts = call.data.split("|")
    if len(parts) < 2:
        return
    group_id = int(parts[1])
    keyboard = get_lock_settings_keyboard(group_id)
    keyboard.add(types.InlineKeyboardButton("بازگشت", callback_data=f"capabilities_main|{group_id}"))
    text = f"تنظیمات قفل‌های گروه {group_id}:"
    bot.edit_message_text(chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          text=text,
                          reply_markup=keyboard)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("crypto_reminder|"))
def crypto_reminder_handler(call):
    parts = call.data.split("|")
    if len(parts) < 2:
        return
    group_id = parts[1]
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("بازگشت", callback_data=f"capabilities_main|{group_id}"))
    text = f"کریپتو و یاداوری برای گروه {group_id}:\n[متن مورد نظر شما در اینجا قرار می‌گیرد]"
    bot.edit_message_text(chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          text=text,
                          reply_markup=markup,
                          parse_mode="Markdown")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "capabilities_back")
def capabilities_back_handler(call):
    bot.edit_message_text(chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          text="لطفاً آیدی گروه خود را ارسال کنید:")
    bot.answer_callback_query(call.id)

@bot.my_chat_member_handler()
def my_chat_member_update(message):
    try:
        if message.new_chat_member.user.id == bot.get_me().id and message.new_chat_member.status in ['administrator', 'creator']:
            group_id = message.chat.id
            settings_text = "⚙️ تنظیمات قفل‌های گروه:\n"
            settings_text += f"فحش: {'فعال' if get_group_lock_setting(group_id, 'banned_words')==1 else 'غیرفعال'}\n"
            settings_text += f"لینک‌ها: {'فعال' if get_group_lock_setting(group_id, 'links')==1 else 'غیرفعال'}\n"
            settings_text += f"پیام‌های طولانی: {'فعال' if get_group_lock_setting(group_id, 'long_text')==1 else 'غیرفعال'}\n"
            settings_text += f"فیلم: {'فعال' if get_group_lock_setting(group_id, 'videos')==1 else 'غیرفعال'}\n"
            settings_text += f"عکس: {'فعال' if get_group_lock_setting(group_id, 'photos')==1 else 'غیرفعال'}\n"
            settings_text += f"صدا/موسیقی: {'فعال' if get_group_lock_setting(group_id, 'audio')==1 else 'غیرفعال'}\n"
            settings_text += f"وییس: {'فعال' if get_group_lock_setting(group_id, 'voice')==1 else 'غیرفعال'}\n"
            settings_text += f"فایل: {'فعال' if get_group_lock_setting(group_id, 'files')==1 else 'غیرفعال'}\n"
            settings_text += f"گیف: {'فعال' if get_group_lock_setting(group_id, 'gif')==1 else 'غیرفعال'}\n"
            settings_text += f"استیکر: {'فعال' if get_group_lock_setting(group_id, 'sticker')==1 else 'غیرفعال'}\n"
            settings_text += f"فروارد: {'فعال' if get_group_lock_setting(group_id, 'forward')==1 else 'غیرفعال'}\n"
            settings_text += f"فیلتر کلمات: {'فعال' if get_group_lock_setting(group_id, 'filter_words')==1 else 'غیرفعال'}\n"
            settings_text += f"خوش آمد گویی: {'فعال' if get_group_lock_setting(group_id, 'welcome')==1 else 'غیرفعال'}"
            bot.send_message(group_id, settings_text)
    except Exception as e:
        logging.error(f"Error in my_chat_member_handler: {e}")

@bot.message_handler(func=lambda message: message.text and message.text.strip() == "وضعیت" and message.chat.type in ['group', 'supergroup'])
def status_handler(message):
    if not is_admin(message.chat.id, message.from_user.id):
        return
    group_id = message.chat.id
    status_text = "⚙️ وضعیت قفل‌های گروه:\n"
    status_text += f"فحش: {'فعال' if get_group_lock_setting(group_id, 'banned_words')==1 else 'غیرفعال'}\n"
    status_text += f"لینک‌ها: {'فعال' if get_group_lock_setting(group_id, 'links')==1 else 'غیرفعال'}\n"
    status_text += f"پیام‌های طولانی: {'فعال' if get_group_lock_setting(group_id, 'long_text')==1 else 'غیرفعال'}\n"
    status_text += f"فیلم: {'فعال' if get_group_lock_setting(group_id, 'videos')==1 else 'غیرفعال'}\n"
    status_text += f"عکس: {'فعال' if get_group_lock_setting(group_id, 'photos')==1 else 'غیرفعال'}\n"
    status_text += f"صدا/موسیقی: {'فعال' if get_group_lock_setting(group_id, 'audio')==1 else 'غیرفعال'}\n"
    status_text += f"وییس: {'فعال' if get_group_lock_setting(group_id, 'voice')==1 else 'غیرفعال'}\n"
    status_text += f"فایل: {'فعال' if get_group_lock_setting(group_id, 'files')==1 else 'غیرفعال'}\n"
    status_text += f"گیف: {'فعال' if get_group_lock_setting(group_id, 'gif')==1 else 'غیرفعال'}\n"
    status_text += f"استیکر: {'فعال' if get_group_lock_setting(group_id, 'sticker')==1 else 'غیرفعال'}\n"
    status_text += f"فروارد: {'فعال' if get_group_lock_setting(group_id, 'forward')==1 else 'غیرفعال'}\n"
    status_text += f"فیلتر کلمات: {'فعال' if get_group_lock_setting(group_id, 'filter_words')==1 else 'غیرفعال'}\n"
    status_text += f"خوش آمد گویی: {'فعال' if get_group_lock_setting(group_id, 'welcome')==1 else 'غیرفعال'}"
    bot.send_message(message.chat.id, status_text)

# -------------------- دستورات مربوط به اد (افزودن دعوت) --------------------
@bot.message_handler(commands=['ad'])
def ad_command_handler(message):
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "این دستور فقط در گروه‌ها قابل استفاده است.")
        return
    try:
        admins = bot.get_chat_administrators(message.chat.id)
    except Exception as e:
        logging.error("Error fetching admins: " + str(e))
        admins = []
    if message.from_user.id in [admin.user.id for admin in admins]:
        bot.reply_to(message, "ادمین‌ها نیازی به انجام اد ندارند.")
        return

    raw_value = get_group_lock_setting(message.chat.id, 'ad_required')
    try:
        required_count = int(raw_value)
    except ValueError:
        if str(raw_value).lower() == 'off':
            required_count = 0
        else:
            required_count = 1

    if required_count == 0:
        bot.reply_to(message, "این گروه نیازی به اد ندارد.")
        return

    if message.chat.id not in ad_counts:
        ad_counts[message.chat.id] = {}
    current_count = ad_counts[message.chat.id].get(message.from_user.id, 0)
    current_count += 1
    ad_counts[message.chat.id][message.from_user.id] = current_count

    if current_count >= required_count:
        bot.send_message(message.chat.id,
            f"<a href='tg://user?id={message.from_user.id}'>کاربر</a> عزیز، شما اکنون شرایط عضویت کامل را دارید. خوش آمدید!",
            parse_mode="HTML")
    else:
        remaining = required_count - current_count
        bot.send_message(message.chat.id,
            f"<a href='tg://user?id={message.from_user.id}'>کاربر</a> عزیز، شما {current_count} نفر اد کرده‌اید. نیاز دارید {remaining} نفر دیگر اد کنید تا بتوانید چت کنید.",
            parse_mode="HTML")


@bot.message_handler(commands=['tagsettings'])
def tag_settings_handler(message):
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "این دستور فقط در گروه‌ها قابل استفاده است.")
        return
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "فقط ادمین‌ها می‌توانند تنظیمات را مشاهده کنند.")
        return

    current_status = get_group_lock_setting(message.chat.id, 'tag_enabled')
    text = f"امکان تگ در این گروه {'فعال' if current_status == 1 else 'غیرفعال'} است."
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("تغییر وضعیت تگ", callback_data="toggle_tag"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "toggle_tag")
def toggle_tag_handler(call):
    chat_id = call.message.chat.id
    if not is_admin(chat_id, call.from_user.id):
        bot.answer_callback_query(call.id, "فقط ادمین‌ها می‌توانند تنظیمات را تغییر دهند.", show_alert=True)
        return

    current_status = get_group_lock_setting(chat_id, 'tag_enabled')
    new_status = 0 if current_status == 1 else 1
    set_group_lock_setting(chat_id, 'tag_enabled', new_status)

    reply_text = f"امکان تگ اکنون {'فعال' if new_status == 1 else 'غیرفعال'} است."
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("تغییر وضعیت تگ", callback_data="toggle_tag"))
    
    try:
        bot.edit_message_text(text=reply_text, chat_id=chat_id,
                              message_id=call.message.message_id, reply_markup=markup)
    except Exception as e:
        logging.error(f"Error updating tag status message: {e}")
    bot.answer_callback_query(call.id, f"تگ {'فعال' if new_status == 1 else 'غیرفعال'} شد.")

@bot.message_handler(commands=['settings'])
def settings_handler(message):
    chat_id = message.chat.id
    current_status = get_group_lock_setting(chat_id, 'tag_enabled')
    button_text = "غیرفعال کردن تگ" if current_status == 1 else "فعال کردن تگ"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(button_text, callback_data='toggle_tag'))
    bot.send_message(chat_id, "تنظیمات گروه:", reply_markup=markup)

    # -------------------- هندلرهای ترکیبی پیام‌های عمومی --------------------
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'sticker', 'voice', 'animation', 'audio', 'document'])
def combined_global_handler(message):
    # پیام ادمین رو ندید میگیره
    if is_admin(message.chat.id, message.from_user.id):
        return
#برای فرمان هم ندید میگیره 
    # if message.text and message.text.startswith('/'):
    #     return
    # ---------------- بررسی عضویت اجباری و فیلترهای پیام ----------------
    global FORCED_GROUP
    if FORCED_GROUP and message.chat.type in ['group', 'supergroup']:
        try:
            member = bot.get_chat_member(FORCED_GROUP, message.from_user.id)
            if member.status == 'left':
                bot.delete_message(message.chat.id, message.message_id)
                bot.send_message(message.chat.id, FORCED_MEMBERSHIP_MESSAGE)
                return
        except Exception as e:
            logging.error(f"خطا در بررسی عضویت اجباری: {e}")
        # bot.send_message(message.chat.id, f"پیام شما ثبت شد: {message.text}")

    if message.chat.type in ['group', 'supergroup']:
        # بررسی اد: استفاده از تعداد دعوت‌های ثبت شده به جای تایید دکمه‌ای
        raw_value = get_group_lock_setting(message.chat.id, 'ad_required')
        try:
            required_count = int(raw_value)
        except ValueError:
            if str(raw_value).lower() == 'off':
                required_count = 0
            else:
                required_count = 1  # مقدار پیش‌فرض در صورت خطا

        if required_count != 0:
            try:
                admins = bot.get_chat_administrators(message.chat.id)
            except Exception as e:
                logging.error("Error fetching admins: " + str(e))
                admins = []
            if message.from_user.id not in [admin.user.id for admin in admins]:
                if message.chat.id not in ad_counts:
                    ad_counts[message.chat.id] = {}
                user_ad_count = ad_counts[message.chat.id].get(message.from_user.id, 0)
                if user_ad_count < required_count:
                    try:
                        bot.delete_message(message.chat.id, message.message_id)
                    except Exception as e:
                        logging.error(f"Error deleting message for insufficient ad count: {e}")
                    remaining = required_count - user_ad_count
                    bot.send_message(message.chat.id,
                                     f"<a href='tg://user?id={message.from_user.id}'>کاربر</a> عزیز، شما نیاز به افزودن {required_count} نفر دارید تا بتوانید در چت شرکت کنید. هنوز {remaining} نفر باقی مانده.",
                                     parse_mode="HTML")
                    return

        # بررسی فیلترهای محتوا
        if message.text:
            lower_text = message.text.lower()
            if get_group_lock_setting(message.chat.id, 'banned_words') == 1 and any(bad_word in lower_text for bad_word in BANNED_WORDS):
                bot.delete_message(message.chat.id, message.message_id)
                custom_text = get_config('filter', message.chat.id)
                if custom_text:
                    bot.send_message(message.chat.id, custom_text)
                else:
                    bot.send_message(message.chat.id, "⚠️ لطفاً از استفاده از کلمات نامناسب خودداری کنید.")
                return
            if get_group_lock_setting(message.chat.id, 'links') == 1 and "http" in lower_text:
                bot.delete_message(message.chat.id, message.message_id)
                custom_text = get_config('lock_links', message.chat.id)
                if custom_text:
                    bot.send_message(message.chat.id, custom_text)
                else:
                    bot.send_message(message.chat.id, "🚫 ارسال لینک در این گروه مجاز نیست.")
                return
            if get_group_lock_setting(message.chat.id, 'long_text') == 1 and len(message.text) > MAX_TEXT_LENGTH:
                bot.delete_message(message.chat.id, message.message_id)
                custom_text = get_config('lock_long_text', message.chat.id)
                if custom_text:
                    bot.send_message(message.chat.id, custom_text)
                else:
                    bot.send_message(message.chat.id, "🚫 پیام شما بیش از حد طولانی است.")
                return

        if get_group_lock_setting(message.chat.id, 'videos') == 1 and getattr(message, 'video', None):
            bot.delete_message(message.chat.id, message.message_id)
            custom_text = get_config('lock_videos', message.chat.id)
            if custom_text:
                bot.send_message(message.chat.id, custom_text)
            else:
                bot.send_message(message.chat.id, "🚫 ارسال ویدئو در این گروه مجاز نیست.")
            return

        if get_group_lock_setting(message.chat.id, 'photos') == 1 and getattr(message, 'photo', None):
            bot.delete_message(message.chat.id, message.message_id)
            custom_text = get_config('lock_photos', message.chat.id)
            if custom_text:
                bot.send_message(message.chat.id, custom_text)
            else:
                bot.send_message(message.chat.id, "🚫 ارسال عکس در این گروه مجاز نیست.")
            return

        if get_group_lock_setting(message.chat.id, 'audio') == 1 and getattr(message, 'audio', None):
            bot.delete_message(message.chat.id, message.message_id)
            custom_text = get_config('lock_audio', message.chat.id)
            if custom_text:
                bot.send_message(message.chat.id, custom_text)
            else:
                bot.send_message(message.chat.id, "🚫 ارسال پیام صوتی (آهنگ) در این گروه مجاز نیست.")
            return

        if get_group_lock_setting(message.chat.id, 'voice') == 1 and getattr(message, 'voice', None):
            bot.delete_message(message.chat.id, message.message_id)
            custom_text = get_config('lock_voice', message.chat.id)
            if custom_text:
                bot.send_message(message.chat.id, custom_text)
            else:
                bot.send_message(message.chat.id, "🚫 ارسال پیام ویس در این گروه مجاز نیست.")
            return

        if get_group_lock_setting(message.chat.id, 'files') == 1 and getattr(message, 'document', None):
            bot.delete_message(message.chat.id, message.message_id)
            custom_text = get_config('lock_files', message.chat.id)
            if custom_text:
                bot.send_message(message.chat.id, custom_text)
            else:
                bot.send_message(message.chat.id, "🚫 ارسال فایل در این گروه مجاز نیست.")
            return

        # ثبت شرکت در قرعه‌کشی (اگر پیام ریپلای باشد)
        if message.reply_to_message and (not message.text or not message.text.startswith('/')):
            parent_id = message.reply_to_message.message_id
            if parent_id not in lottery_entries:
                lottery_entries[parent_id] = set()
            lottery_entries[parent_id].add(message.from_user.id)

    # ---------------- ثبت آمار پیام‌ها و عضویت در دیکشنری اعضای گروه ----------------
    if message.chat.type in ['group', 'supergroup']:
        if message.chat.id not in group_members:
            group_members[message.chat.id] = set()
        group_members[message.chat.id].add(message.from_user.id)

    # ثبت آمار پیام‌ها در دیتابیس
    try:
        conn_local = sqlite3.connect('bot_data.db', check_same_thread=False)
        cur = conn_local.cursor()
        cur.execute("SELECT messages_count FROM users WHERE user_id = ?", (message.from_user.id,))
        row = cur.fetchone()
        if row:
            new_count = row[0] + 1
            cur.execute("UPDATE users SET messages_count = ? WHERE user_id = ?", (new_count, message.from_user.id))
        else:
            cur.execute("INSERT OR IGNORE INTO users (user_id, messages_count) VALUES (?, ?)", (message.from_user.id, 1))
        conn_local.commit()
        cur.close()
        conn_local.close()
    except Exception as e:
        logging.error(f"Error updating message stats: {e}")

# ------------------- هندلر راهنمایی (Callback Query) -------------------
@bot.callback_query_handler(func=lambda call: call.data.startswith("help_") or call.data in ["help_main", "back_to_start", "help_sample1", "help_sample2"])
def help_callback(call):
    # دکمه "back_to_start" برای بازگشت به منوی اصلی ربات (در حالت پیوی)
    if call.data == "back_to_start":
        text, keyboard = main_menu_data(call.from_user)
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=text,
                              reply_markup=keyboard,
                              parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return

    # دکمه "help_main" برای بازگشت به منوی اصلی راهنما
    if call.data == "help_main":
        markup = get_main_help_menu(call.message.chat.type)
        text = "📚 لطفاً یکی از گزینه‌های راهنمایی را انتخاب کنید:"
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=text,
                              reply_markup=markup,
                              parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    # منوی زیرمجموعه "دستور"
    if call.data == "help_dastoor":
        markup = types.InlineKeyboardMarkup(row_width=1)
        help_topics_dastoor = [
            ("مدیریت گروه", "help_group_management"),
            ("تنظیمات سفارشی", "help_customization"),
            ("پاکسازی", "help_cleanup"),
            ("آمار", "help_stats"),
            ("عضویت اجباری", "help_forced_membership"),
            ("ارتقا/عزل", "help_promote_demote"),
            ("راهنمای کامل", "help_full")
        ]
        for text_item, callback_data in help_topics_dastoor:
            markup.add(types.InlineKeyboardButton(text_item, callback_data=callback_data))
        if call.message.chat.type == "private":
            markup.add(types.InlineKeyboardButton("بازگشت", callback_data="help_main"))
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text="📚 راهنمای دستوری:",
                              reply_markup=markup,
                              parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    # سایر منوها و گزینه‌های راهنما (مانند help_karbordi، help_crypto، help_about و غیره)
    if call.data == "help_karbordi":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("قرعه کشی", callback_data="help_lottery"),
            types.InlineKeyboardButton("کاربردی ۲", callback_data="help_sample1"),
            types.InlineKeyboardButton("کاربردی ۳", callback_data="help_sample2")
        )
        if call.message.chat.type == "private":
            markup.add(types.InlineKeyboardButton("بازگشت", callback_data="help_main"))
        text = "📚 راهنمای کاربردی:\nاین بخش شامل اطلاعات کاربردی مربوط به استفاده از ربات می‌باشد."
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=text,
                              reply_markup=markup,
                              parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if call.data == "help_crypto":
        markup = types.InlineKeyboardMarkup()
        if call.message.chat.type == "private":
            markup.add(types.InlineKeyboardButton("بازگشت", callback_data="help_main"))
        text = "📚 راهنمای کریپتو:\nاطلاعات مربوط به کریپتو در این بخش نمایش داده می‌شود."
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=text,
                              reply_markup=markup,
                              parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if call.data == "help_about":
        markup = types.InlineKeyboardMarkup()
        if call.message.chat.type == "private":
            markup.add(types.InlineKeyboardButton("بازگشت", callback_data="help_main"))
        text = "🤖 درباره ما:\nاین ربات توسط تیم ما توسعه داده شده است."
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=text,
                              reply_markup=markup,
                              parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    # برای سایر گزینه‌های راهنما
    help_texts = {
        "help_group_management": "🔹 **مدیریت گروه:**\n- قفل لینک، استیکر، ویدئو، ویس و غیره.\n- فیلتر کلمات نامناسب.\n- مدیریت تبچی‌ها و کاربران مخرب.",
        "help_customization": "⚙ **تنظیمات سفارشی:**\nبا دستور `/config` می‌توانید متن دلخواه برای هر فرمان را تنظیم کنید.\n📌 مثال: `/config welcome خوش آمدگویی {name}!`",
        "help_lottery": "🎲 **قرعه کشی:**\nبرای قرعه کشی، پیام را ریپلای کنید و دستور `/lottery` را ارسال کنید.",
        "help_cleanup": "🧹 **پاکسازی:**\nبرای حذف پیام‌های کاربر از دستور `/cleanup` همراه با شناسه کاربر استفاده کنید.\n📌 مثال: `/cleanup 123456789`",
        "help_stats": "📊 **آمار فعالیت:**\nبا دستور `/stats`، ۵ کاربر برتر بر اساس تعداد پیام‌های ارسال شده نمایش داده می‌شوند.",
        "help_forced_membership": "🔗 **عضویت اجباری:**\nاگر کانال اجباری تنظیم شده باشد، کاربران قبل از ارسال پیام باید عضو شوند.",
        "help_promote_demote": "👑 **ارتقا/عزل:**\n- `/promote` برای ارتقاء کاربر به مدیر.\n- `/demote` برای عزل مدیر از سمت خود.",
        "help_full": "📖 **راهنمای کامل:**\n1️⃣ **مدیریت گروه:** تنظیم قفل‌ها، فیلتر کلمات و مدیریت کاربران.\n2️⃣ **تنظیمات سفارشی:** تنظیم پیام‌ها با `/config`.\n3️⃣ **پاکسازی:** حذف پیام‌های کاربران با `/cleanup`.\n4️⃣ **آمار:** نمایش ۵ کاربر برتر با `/stats`.\n5️⃣ **عضویت اجباری:** بررسی عضویت در کانال.\n6️⃣ **ارتقا/عزل:** ارتقاء و عزل مدیران با `/promote` و `/demote`.\n7️⃣ **قرعه کشی:** انتخاب برنده از بین ریپلای‌ها با `/lottery`."
    }
    text = help_texts.get(call.data, "⚠ راهنمای مورد نظر یافت نشد.")
    markup = types.InlineKeyboardMarkup()
    if call.message.chat.type == "private":
        markup.add(types.InlineKeyboardButton("بازگشت", callback_data="help_dastoor"))
    bot.edit_message_text(chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          text=text,
                          reply_markup=markup,
                          parse_mode="Markdown")
    bot.answer_callback_query(call.id)


# ------------------- سایر هندلرهای کد (مانند global_message_handler و خوش‌آمدگویی) -------------------
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'sticker', 'voice', 'animation', 'audio', 'document'])
def global_message_handler(message):
    # … (سایر کدهای مورد نظر شما برای پردازش پیام‌ها)
    pass


# ------------------- شروع ربات -------------------
if __name__ == '__main__':
    logging.info("Bot is running...")
    bot.infinity_polling()