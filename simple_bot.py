import asyncio
import logging
import aiosqlite
import re
import aiohttp
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    InputMediaPhoto
)

from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = "8944368118:AAEyJ0NyafU5W-_4Zc_HAOK1iyL0TvJ-k1g"
ADMIN_ID = 8002472821

# ========== НАСТРОЙКИ ДЛЯ ФОРУМА (ТЕМ) ==========
FORUM_GROUP_ID = -1003953422773
FORUM_TOPIC_ID = 630
# ====================================================

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())


# ======================================================
# DATABASE
# ======================================================

async def create_db():
    async with aiosqlite.connect("scam.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            reputation TEXT DEFAULT 'clean',
            reason TEXT DEFAULT '',
            likes INTEGER DEFAULT 0,
            dislikes INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS guarantors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            deal_amount TEXT,
            reputation INTEGER DEFAULT 100
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voter_id INTEGER,
            target_username TEXT,
            vote_type TEXT,
            UNIQUE(voter_id, target_username)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_images (
            image_type TEXT PRIMARY KEY,
            file_id TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS scammer_posts (
            username TEXT PRIMARY KEY,
            message_id INTEGER,
            forum_group_id INTEGER,
            forum_topic_id INTEGER
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS report_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_message_id INTEGER,
            media_type TEXT,
            file_id TEXT
        )
        """)

        cursor = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (ADMIN_ID,))
        if not await cursor.fetchone():
            await db.execute(
                "INSERT INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
                (ADMIN_ID, ADMIN_ID, datetime.now().isoformat())
            )

        await db.commit()


# ======================================================
# ФУНКЦИИ ДЛЯ КАРТИНОК
# ======================================================

async def save_image_to_db(image_type: str, file_id: str):
    async with aiosqlite.connect("scam.db") as db:
        await db.execute("INSERT OR REPLACE INTO bot_images (image_type, file_id) VALUES (?, ?)", (image_type, file_id))
        await db.commit()


async def get_image_from_db(image_type: str):
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT file_id FROM bot_images WHERE image_type = ?", (image_type,))
        row = await cursor.fetchone()
        return row[0] if row else None


# ======================================================
# ФУНКЦИИ ДЛЯ ПОСТОВ С ДОКАЗАТЕЛЬСТВАМИ
# ======================================================

async def save_scammer_post(username: str, message_id: int, forum_group_id: int, forum_topic_id: int):
    async with aiosqlite.connect("scam.db") as db:
        await db.execute("""
        INSERT OR REPLACE INTO scammer_posts (username, message_id, forum_group_id, forum_topic_id)
        VALUES (?, ?, ?, ?)
        """, (username.lower(), message_id, forum_group_id, forum_topic_id))
        await db.commit()


async def get_scammer_post_link(username: str) -> str:
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute(
            "SELECT message_id, forum_group_id, forum_topic_id FROM scammer_posts WHERE username = ?",
            (username.lower(),)
        )
        row = await cursor.fetchone()
        if row:
            message_id, forum_group_id, forum_topic_id = row
            if forum_group_id and message_id:
                group_id_str = str(forum_group_id)
                clean_group_id = group_id_str.replace("-100", "")
                if forum_topic_id:
                    return f"https://t.me/c/{clean_group_id}/{message_id}?thread={forum_topic_id}"
                else:
                    return f"https://t.me/c/{clean_group_id}/{message_id}"
        return None


# ======================================================
# ФУНКЦИЯ ПРОВЕРКИ ЧАТА С АДМИНОМ
# ======================================================

async def can_send_message(user_id: int) -> bool:
    try:
        await bot.send_chat_action(chat_id=user_id, action="typing")
        return True
    except:
        return False


# ======================================================
# ФУНКЦИИ БАЗЫ ДАННЫХ
# ======================================================

async def is_admin_async(user_id: int) -> bool:
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return await cursor.fetchone() is not None


async def add_admin_async(admin_id: int, added_by: int):
    async with aiosqlite.connect("scam.db") as db:
        await db.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
            (admin_id, added_by, datetime.now().isoformat())
        )
        await db.commit()


async def remove_admin_async(admin_id: int):
    async with aiosqlite.connect("scam.db") as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (admin_id,))
        await db.commit()


async def get_all_admins() -> list:
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT user_id FROM admins")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def has_voted(voter_id: int, target_username: str) -> tuple:
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute(
            "SELECT vote_type FROM votes WHERE voter_id = ? AND target_username = ?",
            (voter_id, target_username)
        )
        row = await cursor.fetchone()
        if row:
            return True, row[0]
        return False, None


async def save_vote(voter_id: int, target_username: str, vote_type: str):
    async with aiosqlite.connect("scam.db") as db:
        await db.execute(
            "INSERT OR REPLACE INTO votes (voter_id, target_username, vote_type) VALUES (?, ?, ?)",
            (voter_id, target_username, vote_type)
        )
        await db.commit()


async def add_scammer_to_db(username: str, reason: str):
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT 1 FROM users WHERE username = ?", (username.lower(),))
        if not await cursor.fetchone():
            await db.execute("INSERT INTO users (username) VALUES (?)", (username.lower(),))
        
        await db.execute("""
        UPDATE users SET reputation = 'scammer', reason = ? WHERE username = ?
        """, (reason, username.lower()))
        await db.commit()


async def is_guarantor(username: str) -> bool:
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT 1 FROM guarantors WHERE username = ?", (username.lower(),))
        return await cursor.fetchone() is not None


# ======================================================
# ПРОВЕРКА USERNAME
# ======================================================

def is_valid_username_format(username: str) -> bool:
    username = username.replace("@", "").lower()
    return bool(re.match(r'^[a-z0-9_]{5,32}$', username))


async def username_exists_in_telegram(username: str) -> bool:
    username = username.replace("@", "").lower()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://t.me/{username}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 404:
                    return False
                text = await resp.text()
                if "Sorry, this username doesn't exist" in text:
                    return False
                if "This channel is not accessible" in text:
                    return False
                return True
    except:
        return True


def normalize_user_input(text: str):
    text = text.strip()
    if text.startswith('@'):
        return ('username', text[1:].lower())
    if text.isdigit() or (text.startswith('-') and text[1:].isdigit()):
        return ('id', text)
    if re.match(r'^[a-zA-Z0-9_]{5,32}$', text):
        return ('username', text.lower())
    return (None, None)


# ======================================================
# КЛАВИАТУРЫ
# ======================================================

main_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔍 Проверить", callback_data="check_user")],
    [InlineKeyboardButton(text="📝 Жалоба", callback_data="report_user"),
     InlineKeyboardButton(text="🏆 Гаранты", callback_data="guarantors")],
    [InlineKeyboardButton(text="🆔 Как узнать ID?", callback_data="howtoid")]
])

admin_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
    [InlineKeyboardButton(text="🏆 Добавить гаранта", callback_data="admin_add_garant")],
    [InlineKeyboardButton(text="👍 Накрутить лайки", callback_data="admin_likes")],
    [InlineKeyboardButton(text="🖼️ ЗАГРУЗИТЬ КАРТИНКИ", callback_data="admin_upload_images"),
     InlineKeyboardButton(text="🖼️ ЗАГРУЗИТЬ МЕНЮ", callback_data="admin_upload_menu")],
    [InlineKeyboardButton(text="👑 Управление админами", callback_data="admin_manage")]
])

admin_manage_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="➕ Добавить админа", callback_data="admin_add_admin"),
     InlineKeyboardButton(text="➖ Удалить админа", callback_data="admin_remove_admin")],
    [InlineKeyboardButton(text="📋 Список админов", callback_data="admin_list_admins")],
    [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]
])


# ======================================================
# STATES
# ======================================================

class CheckState(StatesGroup):
    username = State()


class ReportState(StatesGroup):
    scammer_username = State()
    scammer_id = State()
    reason = State()
    proof_photos = State()
    proof_video = State()


class GarantState(StatesGroup):
    username = State()
    amount = State()


class AddLikesState(StatesGroup):
    username = State()
    amount = State()


class AddAdminState(StatesGroup):
    user_id = State()


class RemoveAdminState(StatesGroup):
    user_id = State()


class UploadImagesState(StatesGroup):
    clean = State()
    scammer = State()
    guarantor = State()
    menu = State()


# ======================================================
# START (С КАРТИНКОЙ МЕНЮ)
# ======================================================

@dp.message(CommandStart())
async def start(message: Message):
    menu_image = await get_image_from_db("menu")
    text = (
        "🔥 Mint base\n\n"
        "🛡 Проверка Telegram пользователей\n"
        "📨 Жалобы на мошенников\n"
        "🏆 Проверенные гаранты\n"
        "⚡ Репутация пользователей\n\n"
        "⚠️ Даже если пользователь чист, соблюдайте осторожность."
    )
    
    if menu_image:
        await message.answer_photo(photo=menu_image, caption=text, reply_markup=main_menu)
    else:
        await message.answer(text, reply_markup=main_menu)


# ======================================================
# HOW TO ID
# ======================================================

@dp.callback_query(F.data == "howtoid")
async def howtoid(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🆔 *КАК УЗНАТЬ ID ПОЛЬЗОВАТЕЛЯ?*\n\n"
        "📌 *Способ 1 — через бота @userinfobot*\n"
        "1. Перешлите любое сообщение от нужного человека в @userinfobot\n"
        "2. Бот пришлёт его ID\n\n"
        "📌 *Способ 2 — через веб-версию Telegram*\n"
        "1. Откройте web.telegram.org\n"
        "2. Нажмите на нужный чат\n"
        "3. В адресной строке будет ID\n\n"
        "📌 *Способ 3 — для своих сообщений*\n"
        "Напишите @userinfobot команду /id\n\n"
        "✅ *Проверить человека можно и по ID, и по @username*",
        parse_mode="Markdown"
    )


# ======================================================
# CHECK USER
# ======================================================

@dp.callback_query(F.data == "check_user")
async def check_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CheckState.username)
    await callback.message.answer(
        "🔍 *ПРОВЕРКА ПОЛЬЗОВАТЕЛЯ*\n\n"
        "Введите @username или числовой ID:\n"
        "🆔 Как узнать ID? — /howtoid",
        parse_mode="Markdown"
    )


@dp.message(CheckState.username)
async def check_user(message: Message, state: FSMContext):
    raw_input = message.text.strip()
    input_type, value = normalize_user_input(raw_input)

    if input_type is None:
        await message.answer(
            "❌ Ошибка!\n\n"
            "Введите корректный username Telegram или числовой ID.\n"
            "Примеры:\n"
            "- @username\n"
            "- 1234567890\n\n"
            "🆔 Как узнать ID? — /howtoid"
        )
        await state.clear()
        return

    username = value if input_type == 'username' else None

    if input_type == 'username':
        exists = await username_exists_in_telegram(username)
        if not exists:
            await message.answer(
                f"❌ Ошибка!\n\n"
                f"Пользователь @{username} не зарегистрирован в Telegram.\n\n"
                f"Проверьте правильность написания username."
            )
            await state.clear()
            return

    async with aiosqlite.connect("scam.db") as db:
        search_username = username if username else value
        cursor = await db.execute("SELECT * FROM users WHERE username=?", (search_username,))
        user = await cursor.fetchone()

        cursor = await db.execute("SELECT username, deal_amount, reputation FROM guarantors WHERE username=?",
                                  (search_username,))
        garant = await cursor.fetchone()

        if not user and username:
            await db.execute("INSERT INTO users (username) VALUES (?)", (username,))
            await db.commit()
            cursor = await db.execute("SELECT * FROM users WHERE username=?", (username,))
            user = await cursor.fetchone()

    clean_img = await get_image_from_db("clean")
    scammer_img = await get_image_from_db("scammer")
    guarantor_img = await get_image_from_db("guarantor")

    display_name = f"@{user[0]}" if user else raw_input
    
    likes = user[3] if user else 0
    dislikes = user[4] if user else 0

    if garant:
        text = f"🏆 ПРОВЕРЕННЫЙ ГАРАНТ\n\n👤 {display_name}\n\n🟢 Пользователь является официальным гарантом.\n\n💰 Гарантия: {garant[1]}\n⭐ Репутация: {garant[2]}/100\n\n👍 Доверие: {likes}\n👎 Жалобы: {dislikes}\n\n✅ Можно работать."
        if guarantor_img:
            await message.answer_photo(photo=guarantor_img, caption=text)
        else:
            await message.answer(text)
    
    elif user and user[1] == "scammer":
        proof_link = await get_scammer_post_link(user[0])
        text = f"🚨 Mint base CHECK\n\n👤 {display_name}\n\n🔴 Репутация: SCAMMER\n\n📄 Причина:\n{user[2]}\n\n👍 Доверие: {likes}\n👎 Жалобы: {dislikes}\n\n❌ НЕ РЕКОМЕНДУЕТСЯ К СОТРУДНИЧЕСТВУ"
        if proof_link:
            text += f"\n\n📎 *Доказательства:* [Смотреть в группе]({proof_link})"
        if scammer_img:
            await message.answer_photo(photo=scammer_img, caption=text, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
    
    else:
        text = f"🛡 Mint base CHECK\n\n👤 {display_name}\n\n🟠 Репутация: чист\nЖалоб не обнаружено.\n\n👍 Доверие: {likes}\n👎 Жалобы: {dislikes}\n\n⚠️ Используйте гарантов для безопасных сделок."
        if clean_img:
            await message.answer_photo(photo=clean_img, caption=text)
        else:
            await message.answer(text)

    if user:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"👍 {likes}", callback_data=f"like:{user[0]}"),
             InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"dislike:{user[0]}")]
        ])
        await message.answer("Оцените пользователя:", reply_markup=kb)
    
    await state.clear()


# ======================================================
# LIKE/DISLIKE
# ======================================================

@dp.callback_query(F.data.startswith("like:"))
async def like(callback: CallbackQuery):
    username = callback.data.split(":")[1]
    voter_id = callback.from_user.id

    voted, vote_type = await has_voted(voter_id, username)
    if voted:
        await callback.answer("❌ Вы уже голосовали за этого пользователя", show_alert=True)
        return

    await save_vote(voter_id, username, "like")
    async with aiosqlite.connect("scam.db") as db:
        await db.execute("UPDATE users SET likes = likes + 1 WHERE username=?", (username,))
        await db.commit()
        cursor = await db.execute("SELECT likes, dislikes FROM users WHERE username=?", (username,))
        new_likes, new_dislikes = await cursor.fetchone()

    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👍 {new_likes}", callback_data=f"like:{username}"),
         InlineKeyboardButton(text=f"👎 {new_dislikes}", callback_data=f"dislike:{username}")]
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except:
        pass
    await callback.answer("👍 Лайк поставлен!")


@dp.callback_query(F.data.startswith("dislike:"))
async def dislike(callback: CallbackQuery):
    username = callback.data.split(":")[1]
    voter_id = callback.from_user.id

    voted, vote_type = await has_voted(voter_id, username)
    if voted:
        await callback.answer("❌ Вы уже голосовали за этого пользователя", show_alert=True)
        return

    await save_vote(voter_id, username, "dislike")
    async with aiosqlite.connect("scam.db") as db:
        await db.execute("UPDATE users SET dislikes = dislikes + 1 WHERE username=?", (username,))
        await db.commit()
        cursor = await db.execute("SELECT likes, dislikes FROM users WHERE username=?", (username,))
        new_likes, new_dislikes = await cursor.fetchone()

    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👍 {new_likes}", callback_data=f"like:{username}"),
         InlineKeyboardButton(text=f"👎 {new_dislikes}", callback_data=f"dislike:{username}")]
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except:
        pass
    await callback.answer("👎 Дизлайк учтён")


# ======================================================
# REPORT SYSTEM
# ======================================================

@dp.callback_query(F.data == "report_user")
async def report_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ReportState.scammer_username)
    await callback.message.answer(
        "📝 *ЖАЛОБА* 📝\n\n"
        "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
        "*Шаг 1 из 6*\n\n"
        "Введите *@username* мошенника:\n\n"
        "Пример: @scammer\n\n"
        "🆔 ID укажем на следующем шаге.",
        parse_mode="Markdown"
    )


@dp.message(ReportState.scammer_username)
async def report_username(message: Message, state: FSMContext):
    raw_input = message.text.strip()
    
    if not raw_input:
        await message.answer("❌ Введите username мошенника (например: @scammer)")
        return
    
    if not is_valid_username_format(raw_input):
        await message.answer(
            "❌ Ошибка!\n\n"
            "Введите корректный username.\n"
            "Пример: @username или просто username\n\n"
            "Правила: от 5 до 32 символов, только буквы, цифры и _"
        )
        return
    
    username = raw_input.replace("@", "").lower()
    
    if message.from_user.username and message.from_user.username.lower() == username:
        await message.answer("❌ Ошибка!\n\nВы не можете пожаловаться на самого себя.")
        return
    
    exists = await username_exists_in_telegram(username)
    if not exists:
        await message.answer(
            f"❌ Ошибка!\n\n"
            f"Пользователь @{username} не зарегистрирован в Telegram.\n\n"
            f"Пожалуйста, проверьте правильность написания username."
        )
        return
    
    await state.update_data(scammer_username=username)
    await state.set_state(ReportState.scammer_id)
    await message.answer(
        "📝 *ЖАЛОБА* 📝\n\n"
        "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
        "*Шаг 2 из 6*\n\n"
        "Введите *ID* мошенника:\n\n"
        "🆔 *Как узнать ID?* — /howtoid\n\n"
        "Пример: 1234567890\n\n"
        "❌ Если не знаете ID — напишите «нет»",
        parse_mode="Markdown"
    )


@dp.message(ReportState.scammer_id)
async def report_id(message: Message, state: FSMContext):
    user_id_input = message.text.strip()
    
    if user_id_input.lower() in ["нет", "no", "не знаю", "unknown"]:
        await state.update_data(scammer_id="unknown")
    else:
        if not user_id_input.isdigit():
            await message.answer(
                "❌ Ошибка!\n\n"
                "ID должен быть числом.\n"
                "Пример: 1234567890\n\n"
                "Если не знаете ID — напишите «нет»",
                parse_mode="Markdown"
            )
            return
        await state.update_data(scammer_id=user_id_input)
    
    await state.set_state(ReportState.reason)
    await message.answer(
        "📝 *ЖАЛОБА* 📝\n\n"
        "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
        "*Шаг 3 из 6*\n\n"
        "📄 Опишите *ситуацию*:\n\n"
        "• Что произошло?\n"
        "• Когда?\n"
        "• На какую сумму?\n"
        "• Какие обещания давал?\n\n"
        "Чем подробнее — тем лучше!",
        parse_mode="Markdown"
    )


@dp.message(ReportState.reason)
async def report_reason(message: Message, state: FSMContext):
    await state.update_data(reason=message.text)
    await state.set_state(ReportState.proof_photos)
    await message.answer(
        "🖼 *СКРИНШОТЫ* 📸\n\n"
        "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
        "*Шаг 4 из 6*\n\n"
        "Отправьте *до 5 фото* подтверждения.\n\n"
        "📌 *Как работает:*\n"
        "• Отправляйте фото по одному\n"
        "• Когда закончите — напишите *«готово»*\n\n"
        "❌ Если нет скриншотов — напишите *«нет»*",
        parse_mode="Markdown"
    )


@dp.message(ReportState.proof_photos)
async def report_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    if 'photo_ids' not in data:
        await state.update_data(photo_ids=[])
        data = await state.get_data()

    if message.text and message.text.lower() in ["готово", "да", "done", "хватит", "нет", "no"]:
        if len(data['photo_ids']) == 0:
            await state.update_data(photo_ids=["no_screenshot"])
        await state.set_state(ReportState.proof_video)
        await message.answer(
            "📹 *ВИДЕО (опционально)* 🎥\n\n"
            "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
            "*Шаг 5 из 6*\n\n"
            "Если есть видео — отправьте.\n"
            "Если нет — напишите *«нет»* или *«готово»*",
            parse_mode="Markdown"
        )
        return

    if message.photo:
        photo_ids = data.get('photo_ids', [])
        photo_ids.append(message.photo[-1].file_id)
        await state.update_data(photo_ids=photo_ids)

        remaining = 5 - len(photo_ids)
        if remaining > 0:
            await message.answer(f"📸 *{len(photo_ids)}/5* — осталось {remaining}. Или напишите «готово»", parse_mode="Markdown")
            return
        else:
            await message.answer("✅ *Собрано 5 скриншотов!*", parse_mode="Markdown")
            await state.set_state(ReportState.proof_video)
            await message.answer(
                "📹 *ВИДЕО (опционально)* 🎥\n\n"
                "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
                "*Шаг 5 из 6*\n\n"
                "Если есть видео — отправьте.\n"
                "Если нет — напишите *«нет»* или *«готово»*",
                parse_mode="Markdown"
            )
            return

    else:
        await message.answer("❌ Отправьте фото или напишите «готово»", parse_mode="Markdown")
        return


@dp.message(ReportState.proof_video)
async def report_video(message: Message, state: FSMContext):
    data = await state.get_data()
    scammer_username = data["scammer_username"]
    scammer_id = data.get("scammer_id", "unknown")
    reason = data["reason"]
    photo_ids = data.get("photo_ids", [])

    video_id = None
    if message.video:
        video_id = message.video.file_id
    elif message.text and message.text.lower() in ["нет", "no", "готово", "done"]:
        video_id = "no_video"
    else:
        await message.answer("❌ Отправьте видео или напишите «нет»", parse_mode="Markdown")
        return

    scammer_info = f"@{scammer_username}"
    if scammer_id != "unknown":
        scammer_info += f" (ID: {scammer_id})"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data="moderate_accept"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data="moderate_decline")]
    ])

    caption = f"🚨 НОВАЯ ЖАЛОБА (МОДЕРАЦИЯ)\n\n👤 {scammer_info}\n\n📄 {reason}"

    admin_ids = await get_all_admins()
    sent_to_admins = False
    admin_message_id = None

    for admin_id in admin_ids:
        try:
            if not await can_send_message(admin_id):
                continue
                
            if photo_ids and photo_ids[0] != "no_screenshot":
                msg = await bot.send_photo(admin_id, photo=photo_ids[0], caption=caption, reply_markup=kb)
                admin_message_id = msg.message_id
                for pid in photo_ids[1:]:
                    await bot.send_photo(admin_id, photo=pid)
            else:
                msg = await bot.send_message(admin_id, caption, reply_markup=kb)
                admin_message_id = msg.message_id
                
            if video_id and video_id != "no_video":
                await bot.send_video(admin_id, video=video_id)
            sent_to_admins = True
            break
        except Exception as e:
            print(f"Не удалось отправить админу {admin_id}: {e}")

    if admin_message_id:
        async with aiosqlite.connect("scam.db") as db:
            for pid in photo_ids:
                if pid != "no_screenshot":
                    await db.execute(
                        "INSERT INTO report_media (admin_message_id, media_type, file_id) VALUES (?, ?, ?)",
                        (admin_message_id, "photo", pid)
                    )
            if video_id and video_id != "no_video":
                await db.execute(
                    "INSERT INTO report_media (admin_message_id, media_type, file_id) VALUES (?, ?, ?)",
                    (admin_message_id, "video", video_id)
                )
            await db.commit()

    await message.answer("✅ Жалоба отправлена на модерацию" if sent_to_admins else "⚠️ Жалоба сохранена, но нет активных администраторов")
    await state.clear()


# ======================================================
# MODERATION
# ======================================================

@dp.callback_query(F.data == "moderate_accept")
async def moderate_accept(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    caption_text = callback.message.caption or ""
    lines = caption_text.split("\n")
    scammer_info = None
    reason = "Не указана"

    for line in lines:
        if line.startswith("👤 "):
            scammer_info = line.replace("👤 ", "").strip()
        elif line.startswith("📄 "):
            reason = line.replace("📄 ", "").strip()

    if not scammer_info:
        await callback.answer("Ошибка: не удалось определить информацию о скамере")
        return

    username_match = re.search(r'@([a-zA-Z0-9_]+)', scammer_info)
    id_match = re.search(r'ID:\s*(\d+)', scammer_info)
    
    scammer_username = username_match.group(1) if username_match else scammer_info
    scammer_id = id_match.group(1) if id_match else "ID не указан"

    await add_scammer_to_db(scammer_username, reason)

    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    post_text = (
        f"🚨 НОВЫЙ СКАМЕР 🚨\n\n"
        f"┌─────────────────────────────┐\n"
        f"│ 👤 Username: @{scammer_username}\n"
        f"│ 🆔 ID: {scammer_id}\n"
        f"│ 📅 Дата: {now}\n"
        f"└─────────────────────────────┘\n\n"
        f"📄 Причина:\n{reason}\n\n"
        f"⚠️ ОСТЕРЕГАЙТЕСЬ! Проверяйте всех через @MintBaseBot"
    )

    admin_message_id = callback.message.message_id
    all_photo_ids = []
    all_video_ids = []

    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute(
            "SELECT media_type, file_id FROM report_media WHERE admin_message_id = ?",
            (admin_message_id,)
        )
        rows = await cursor.fetchall()
        for media_type, file_id in rows:
            if media_type == "photo":
                all_photo_ids.append(file_id)
            elif media_type == "video":
                all_video_ids.append(file_id)

    try:
        sent_message = None
        
        if all_photo_ids:
            media_group = []
            for i, photo_id in enumerate(all_photo_ids):
                if i == 0:
                    media_group.append(InputMediaPhoto(media=photo_id, caption=post_text))
                else:
                    media_group.append(InputMediaPhoto(media=photo_id))
            
            sent_messages = await bot.send_media_group(
                chat_id=FORUM_GROUP_ID,
                media=media_group,
                message_thread_id=FORUM_TOPIC_ID
            )
            sent_message = sent_messages[0] if sent_messages else None
        
        if all_video_ids:
            for video_id in all_video_ids:
                video_msg = await bot.send_video(
                    FORUM_GROUP_ID,
                    video=video_id,
                    message_thread_id=FORUM_TOPIC_ID
                )
                if not sent_message:
                    sent_message = video_msg
        
        if not all_photo_ids and not all_video_ids:
            sent_message = await bot.send_message(
                FORUM_GROUP_ID,
                post_text,
                message_thread_id=FORUM_TOPIC_ID
            )
        
        if sent_message:
            await save_scammer_post(
                scammer_username,
                sent_message.message_id,
                FORUM_GROUP_ID,
                FORUM_TOPIC_ID
            )
            
    except Exception as e:
        print(f"Ошибка отправки в форум: {e}")

    current_caption = callback.message.caption or ""
    await callback.message.edit_caption(caption=current_caption + "\n\n✅ ОДОБРЕНО И ОПУБЛИКОВАНО")
    await callback.answer("Жалоба одобрена и опубликована в форуме")


@dp.callback_query(F.data == "moderate_decline")
async def moderate_decline(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    
    try:
        # Пробуем отредактировать подпись (если есть)
        if callback.message.caption:
            await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ ОТКЛОНЕНО")
        else:
            # Если подписи нет — редактируем текст
            await callback.message.edit_text(callback.message.text + "\n\n❌ ОТКЛОНЕНО")
    except Exception as e:
        # Если не получилось — просто отвечаем
        await callback.answer("❌ Жалоба отклонена", show_alert=True)


# ======================================================
# GUARANTORS
# ======================================================

@dp.callback_query(F.data == "guarantors")
async def guarantors(callback: CallbackQuery):
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT username, deal_amount, reputation FROM guarantors ORDER BY reputation DESC")
        rows = await cursor.fetchall()

    if not rows:
        await callback.message.answer("Список гарантов пуст")
        return

    text = "🏆 ТОП ГАРАНТЫ\n\n"
    for num, row in enumerate(rows, 1):
        text += f"{num}. @{row[0]}\n├ 💰 Гарантия: {row[1]}\n└ ⭐ Репутация: {row[2]}/100\n\n"
    await callback.message.answer(text)


# ======================================================
# ADMIN PANEL
# ======================================================

@dp.message(F.text == "/admin")
async def admin(message: Message):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        return
    await message.answer("⚙️ АДМИН ПАНЕЛЬ", reply_markup=admin_menu)


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE reputation='scammer'")
        scammers = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM admins")
        admin_count = (await cursor.fetchone())[0]
    await callback.message.answer(f"👥 Пользователей: {users}\n🚨 Скамеров: {scammers}\n👑 Администраторов: {admin_count}")


@dp.callback_query(F.data == "admin_manage")
async def admin_manage(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("👑 УПРАВЛЕНИЕ АДМИНАМИ\n\nВыберите действие:", reply_markup=admin_manage_menu)


@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("⚙️ АДМИН ПАНЕЛЬ", reply_markup=admin_menu)


# ======================================================
# НАКРУТКА ЛАЙКОВ
# ======================================================

@dp.callback_query(F.data == "admin_likes")
async def add_likes_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(AddLikesState.username)
    await callback.message.edit_text("👍 НАКРУТКА ЛАЙКОВ\n\nВведите username:")


@dp.message(AddLikesState.username)
async def add_likes_username(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        await state.clear()
        return
    await state.update_data(username=message.text.replace("@", "").lower())
    await state.set_state(AddLikesState.amount)
    await message.answer("👍 Введите количество лайков")


@dp.message(AddLikesState.amount)
async def add_likes_finish(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        await state.clear()
        return
    data = await state.get_data()
    username = data["username"]
    try:
        amount = int(message.text)
    except ValueError:
        await message.answer("❌ Ошибка: нужно ввести число")
        await state.clear()
        return

    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        if not await cursor.fetchone():
            await db.execute("INSERT INTO users (username) VALUES (?)", (username,))
        
        await db.execute("UPDATE users SET likes = likes + ? WHERE username=?", (amount, username))
        await db.commit()
        
        cursor = await db.execute("SELECT likes FROM users WHERE username=?", (username,))
        result = await cursor.fetchone()
        new_likes = result[0] if result else 0

    await message.answer(f"✅ Накручено {amount} лайков пользователю @{username}\n📊 Теперь лайков: {new_likes}")
    await state.clear()


# ======================================================
# ЗАГРУЗКА КАРТИНОК
# ======================================================

@dp.callback_query(F.data == "admin_upload_images")
async def upload_images_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(UploadImagesState.clean)
    await callback.message.edit_text(
        "🖼️ *ЗАГРУЗКА КАРТИНОК*\n\n"
        "📸 *Шаг 1 из 3*\n\n"
        "Отправьте картинку для результата *«ЧИСТЫЙ ПОЛЬЗОВАТЕЛЬ»*:\n\n"
        "Просто отправьте фото в этот чат.\n"
        "❌ Отмена — /cancel",
        parse_mode="Markdown"
    )


@dp.message(UploadImagesState.clean)
async def upload_clean_image(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Отправьте фото, пожалуйста.")
        return
    await save_image_to_db("clean", message.photo[-1].file_id)
    await state.set_state(UploadImagesState.scammer)
    await message.answer(
        "✅ *Картинка «Чистый пользователь» сохранена!*\n\n"
        "🔴 *Шаг 2 из 3*\n\n"
        "Отправьте картинку для результата *«СКАМЕР»*:",
        parse_mode="Markdown"
    )


@dp.message(UploadImagesState.scammer)
async def upload_scammer_image(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Отправьте фото, пожалуйста.")
        return
    await save_image_to_db("scammer", message.photo[-1].file_id)
    await state.set_state(UploadImagesState.guarantor)
    await message.answer(
        "✅ *Картинка «Скамер» сохранена!*\n\n"
        "🟢 *Шаг 3 из 3*\n\n"
        "Отправьте картинку для результата *«ГАРАНТ»*:",
        parse_mode="Markdown"
    )


@dp.message(UploadImagesState.guarantor)
async def upload_guarantor_image(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Отправьте фото, пожалуйста.")
        return
    await save_image_to_db("guarantor", message.photo[-1].file_id)
    await state.clear()
    await message.answer(
        "✅ *Все картинки успешно сохранены!*\n\n"
        "🖼️ Теперь при проверке пользователей бот будет отправлять эти картинки.\n\n"
        "Вернуться в админ-панель: /admin",
        parse_mode="Markdown"
    )


# ======================================================
# ЗАГРУЗКА КАРТИНКИ МЕНЮ
# ======================================================

@dp.callback_query(F.data == "admin_upload_menu")
async def upload_menu_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(UploadImagesState.menu)
    await callback.message.edit_text(
        "🏠 *ЗАГРУЗКА КАРТИНКИ МЕНЮ*\n\n"
        "Отправьте картинку, которая будет показываться\n"
        "в главном меню бота (команда /start).\n\n"
        "📸 Просто отправьте фото в этот чат.\n"
        "❌ Отмена — /cancel",
        parse_mode="Markdown"
    )


@dp.message(UploadImagesState.menu)
async def upload_menu_image(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Отправьте фото, пожалуйста.")
        return
    await save_image_to_db("menu", message.photo[-1].file_id)
    await state.clear()
    await message.answer(
        "✅ *Картинка для главного меню сохранена!*\n\n"
        "🏠 Теперь при команде /start бот будет показывать эту картинку.\n\n"
        "Вернуться в админ-панель: /admin",
        parse_mode="Markdown"
    )


# ======================================================
# ADMIN ADD/REMOVE
# ======================================================

@dp.callback_query(F.data == "admin_add_admin")
async def admin_add_admin_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(AddAdminState.user_id)
    await callback.message.edit_text("👑 ДОБАВЛЕНИЕ АДМИНА\n\nВведите ID пользователя (число):\nПример: 8566976864")


@dp.message(AddAdminState.user_id)
async def admin_add_admin_finish(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        await state.clear()
        return
    try:
        new_admin_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Ошибка: нужно ввести число")
        return
    await add_admin_async(new_admin_id, message.from_user.id)
    await message.answer(f"✅ Пользователь с ID {new_admin_id} добавлен в список администраторов!")
    await state.clear()


@dp.callback_query(F.data == "admin_remove_admin")
async def admin_remove_admin_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(RemoveAdminState.user_id)
    await callback.message.edit_text("👑 УДАЛЕНИЕ АДМИНА\n\nВведите ID пользователя для удаления:")


@dp.message(RemoveAdminState.user_id)
async def admin_remove_admin_finish(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        await state.clear()
        return
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Ошибка: нужно ввести число")
        await state.clear()
        return

    if target_id == message.from_user.id:
        await message.answer("❌ Вы не можете удалить самого себя")
        await state.clear()
        return

    if not await is_admin_async(target_id):
        await message.answer(f"❌ Пользователь с ID {target_id} не является администратором")
        await state.clear()
        return

    admins = await get_all_admins()
    if len(admins) <= 1:
        await message.answer("❌ Нельзя удалить единственного администратора")
        await state.clear()
        return

    await remove_admin_async(target_id)
    await message.answer(f"✅ Пользователь с ID {target_id} удалён из списка администраторов")
    await state.clear()


@dp.callback_query(F.data == "admin_list_admins")
async def admin_list_admins(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    admin_ids = await get_all_admins()
    if not admin_ids:
        await callback.message.edit_text("Список администраторов пуст")
        return

    text = "👑 СПИСОК АДМИНИСТРАТОРОВ\n\n"
    for i, admin_id in enumerate(admin_ids, 1):
        text += f"{i}. {admin_id}"
        if admin_id == ADMIN_ID:
            text += " (главный)"
        text += "\n"
    await callback.message.edit_text(text, reply_markup=admin_manage_menu)


# ======================================================
# ADD GARANT
# ======================================================

@dp.callback_query(F.data == "admin_add_garant")
async def garant_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(GarantState.username)
    await callback.message.edit_text("🏆 ДОБАВЛЕНИЕ ГАРАНТА\n\nВведите username гаранта:")


@dp.message(GarantState.username)
async def garant_username(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        await state.clear()
        return
    username = message.text.replace("@", "").lower()
    await state.update_data(username=username)
    await state.set_state(GarantState.amount)
    await message.answer("💰 Введите сумму гарантии")


@dp.message(GarantState.amount)
async def garant_finish(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        await state.clear()
        return
    data = await state.get_data()
    username = data["username"]
    amount = message.text

    async with aiosqlite.connect("scam.db") as db:
        await db.execute("INSERT OR REPLACE INTO guarantors (username, deal_amount, reputation) VALUES (?, ?, ?)",
                         (username, amount, 100))
        await db.commit()

    await message.answer(f"✅ @{username} добавлен\n💰 Гарантия: {amount}")
    await state.clear()


# ======================================================
# HELPER
# ======================================================

@dp.message(F.text == "/get_topic_id")
async def get_topic_id(message: Message):
    await message.answer(
        f"📌 ID группы: {message.chat.id}\n"
        f"📌 ID темы: {message.message_thread_id or 1}\n"
        f"📌 Название темы: {message.chat.title if message.is_forum else 'Не форум'}"
    )


@dp.message(F.text == "/cancel")
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено")


# ======================================================
# RUN
# ======================================================

async def main():
    await create_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
