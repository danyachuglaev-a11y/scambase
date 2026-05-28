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
    CallbackQuery
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
FORUM_GROUP_ID = -1003953422773 # ← ID группы с темами
FORUM_TOPIC_ID = 630  # ← ID темы
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
            dislikes INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0
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

        cursor = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (ADMIN_ID,))
        if not await cursor.fetchone():
            await db.execute(
                "INSERT INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
                (ADMIN_ID, ADMIN_ID, datetime.now().isoformat())
            )

        await db.commit()


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
        await db.execute("""
        INSERT OR REPLACE INTO users (username, reputation, reason)
        VALUES (?, ?, ?)
        """, (username.lower(), "scammer", reason))
        await db.commit()


async def is_guarantor(username: str) -> bool:
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT 1 FROM guarantors WHERE username = ?", (username.lower(),))
        return await cursor.fetchone() is not None


# ======================================================
# ПРОВЕРКА USERNAME (АНТИФЕЙК)
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


# ======================================================
# КЛАВИАТУРЫ
# ======================================================

main_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔍 Проверить", callback_data="check_user")],
    [InlineKeyboardButton(text="📝 Жалоба", callback_data="report_user"),
     InlineKeyboardButton(text="🏆 Гаранты", callback_data="guarantors")],
    [InlineKeyboardButton(text="📋 Последние скамеры", callback_data="last_scammers")]
])

admin_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
    [InlineKeyboardButton(text="🏆 Добавить гаранта", callback_data="admin_add_garant")],
    [InlineKeyboardButton(text="👍 Накрутить лайки", callback_data="admin_likes"),
     InlineKeyboardButton(text="👁 Накрутить просмотры", callback_data="admin_views")],
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
    username = State()
    reason = State()
    proof = State()


class GarantState(StatesGroup):
    username = State()
    amount = State()


class AddLikesState(StatesGroup):
    username = State()
    amount = State()


class AddViewsState(StatesGroup):
    username = State()
    amount = State()


class AddAdminState(StatesGroup):
    user_id = State()


class RemoveAdminState(StatesGroup):
    user_id = State()


# ======================================================
# START
# ======================================================

@dp.message(CommandStart())
async def start(message: Message):
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE reputation='scammer'")
        scammers = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM guarantors")
        guarantors = (await cursor.fetchone())[0]

    text = (
        "🔥 Mint base\n\n"
        "🛡 Проверка Telegram пользователей\n"
        "📨 Жалобы на мошенников\n"
        "🏆 Проверенные гаранты\n"
        "⚡ Репутация пользователей\n\n"
        f"🚨 Скамеров в базе: {scammers}\n"
        f"🏆 Гарантов: {guarantors}\n\n"
        "⚠️ Даже если пользователь чист, соблюдайте осторожность."
    )
    await message.answer(text, reply_markup=main_menu)


# ======================================================
# CHECK USER (С ПРОВЕРКОЙ)
# ======================================================

@dp.callback_query(F.data == "check_user")
async def check_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CheckState.username)
    await callback.message.answer("🔍 Введите username\n\nПример: @username")


@dp.message(CheckState.username)
async def check_user(message: Message, state: FSMContext):
    raw_input = message.text.strip()

    if not is_valid_username_format(raw_input):
        await message.answer(
            "❌ Ошибка!\n\n"
            "Введите корректный username Telegram.\n"
            "Пример: @username или просто username\n\n"
            "Правила: от 5 до 32 символов, только буквы, цифры и _"
        )
        await state.clear()
        return

    username = raw_input.replace("@", "").lower()

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
        cursor = await db.execute("SELECT * FROM users WHERE username=?", (username,))
        user = await cursor.fetchone()
        cursor = await db.execute("SELECT username, deal_amount, reputation FROM guarantors WHERE username=?",
                                  (username,))
        garant = await cursor.fetchone()

        if not user:
            await db.execute("INSERT INTO users (username) VALUES (?)", (username,))
            await db.commit()
            cursor = await db.execute("SELECT * FROM users WHERE username=?", (username,))
            user = await cursor.fetchone()

        await db.execute("UPDATE users SET views = views + 1 WHERE username=?", (username,))
        await db.commit()
        cursor = await db.execute("SELECT * FROM users WHERE username=?", (username,))
        user = await cursor.fetchone()

    reputation = user[1]
    reason = user[2]
    likes = user[3]
    dislikes = user[4]
    views = user[5]

    if garant:
        text = f"🏆 ПРОВЕРЕННЫЙ ГАРАНТ\n\n👤 @{username}\n👁 Просмотров: {views}\n\n🟢 Пользователь является официальным гарантом.\n\n💰 Гарантия: {garant[1]}\n⭐ Репутация: {garant[2]}/100\n\n👍 Доверие: {likes}\n👎 Жалобы: {dislikes}\n\n✅ Можно работать."
    elif reputation == "scammer":
        text = f"🚨 Mint base CHECK\n\n👤 @{username}\n👁 Просмотров: {views}\n\n🔴 Репутация: SCAMMER\n\n📄 Причина:\n{reason}\n\n👍 Доверие: {likes}\n👎 Жалобы: {dislikes}\n\n❌ НЕ РЕКОМЕНДУЕТСЯ К СОТРУДНИЧЕСТВУ"
    else:
        text = f"🛡 Mint base CHECK\n\n👤 @{username}\n👁 Просмотров: {views}\n\n🟠 Репутация: чист\nЖалоб не обнаружено.\n\n👍 Доверие: {likes}\n👎 Жалобы: {dislikes}\n\n⚠️ Используйте гарантов для безопасных сделок."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👍 {likes}", callback_data=f"like:{username}"),
         InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"dislike:{username}")]
    ])

    await message.answer(text, reply_markup=kb)
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
# REPORT SYSTEM (С ПРОВЕРКАМИ)
# ======================================================

@dp.callback_query(F.data == "report_user")
async def report_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ReportState.username)
    await callback.message.answer("📝 Введите username мошенника")


@dp.message(ReportState.username)
async def report_username(message: Message, state: FSMContext):
    raw_input = message.text.strip()

    if not is_valid_username_format(raw_input):
        await message.answer(
            "❌ Ошибка!\n\n"
            "Введите корректный username Telegram.\n"
            "Пример: @username или просто username\n\n"
            "Правила: от 5 до 32 символов, только буквы, цифры и _"
        )
        return

    username = raw_input.replace("@", "").lower()

    # Нельзя жаловаться на самого себя
    if message.from_user.username and message.from_user.username.lower() == username:
        await message.answer("❌ Ошибка!\n\nВы не можете пожаловаться на самого себя.")
        return

    # Проверяем существование username в Telegram
    exists = await username_exists_in_telegram(username)
    if not exists:
        await message.answer(
            f"❌ Ошибка!\n\n"
            f"Пользователь @{username} не зарегистрирован в Telegram.\n\n"
            f"Пожалуйста, проверьте правильность написания username."
        )
        return

    # Проверяем, не является ли пользователь гарантом
    if await is_guarantor(username):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Всё равно отправить", callback_data="force_report_continue")]
        ])
        await state.update_data(username=username)
        await state.set_state(ReportState.reason)
        await message.answer(
            f"⚠️ Внимание!\n\n"
            f"Пользователь @{username} является ГАРАНТОМ.\n\n"
            f"Вы уверены, что хотите на него пожаловаться?\n\n"
            f"Если да, нажмите кнопку ниже и продолжите.",
            reply_markup=kb
        )
        return

    await state.update_data(username=username)
    await state.set_state(ReportState.reason)
    await message.answer("📄 Опишите ситуацию")


@dp.callback_query(F.data == "force_report_continue")
async def force_report_continue(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ReportState.reason)
    await callback.message.answer("📄 Опишите ситуацию (жалоба на гаранта будет рассмотрена вручную)")


@dp.message(ReportState.reason)
async def report_reason(message: Message, state: FSMContext):
    await state.update_data(reason=message.text)
    await state.set_state(ReportState.proof)
    await message.answer("🖼 Отправьте фото или видео доказательство")


@dp.message(ReportState.proof)
async def report_proof(message: Message, state: FSMContext):
    data = await state.get_data()
    username = data["username"]
    reason = data["reason"]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data="moderate_accept"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data="moderate_decline")]
    ])

    caption = f"🚨 НОВАЯ ЖАЛОБА (МОДЕРАЦИЯ)\n\n👤 @{username}\n\n📄 {reason}"

    admin_ids = await get_all_admins()
    sent = False
    for admin_id in admin_ids:
        try:
            if message.photo:
                await bot.send_photo(admin_id, photo=message.photo[-1].file_id, caption=caption, reply_markup=kb)
            elif message.video:
                await bot.send_video(admin_id, video=message.video.file_id, caption=caption, reply_markup=kb)
            else:
                await bot.send_message(admin_id, caption, reply_markup=kb)
            sent = True
        except:
            pass

    await message.answer("✅ Жалоба отправлена на модерацию" if sent else "❌ Не удалось отправить жалобу")
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
    scammer_username = None
    reason = "Не указана"

    for line in lines:
        if line.startswith("👤 @"):
            scammer_username = line.replace("👤 @", "").strip()
        elif line.startswith("📄 "):
            reason = line.replace("📄 ", "").strip()

    if not scammer_username:
        await callback.answer("Ошибка: не удалось определить username")
        return

    await add_scammer_to_db(scammer_username, reason)

    post_text = f"🚨 НОВЫЙ СКАМЕР (ПОДТВЕРЖДЁН)\n\n👤 @{scammer_username}\n\n📄 {reason}"

    if callback.message.photo:
        await bot.send_photo(FORUM_GROUP_ID, photo=callback.message.photo[-1].file_id, caption=post_text,
                             message_thread_id=FORUM_TOPIC_ID)
    elif callback.message.video:
        await bot.send_video(FORUM_GROUP_ID, video=callback.message.video.file_id, caption=post_text,
                             message_thread_id=FORUM_TOPIC_ID)
    else:
        await bot.send_message(FORUM_GROUP_ID, post_text, message_thread_id=FORUM_TOPIC_ID)

    current_caption = callback.message.caption or ""
    await callback.message.edit_caption(caption=current_caption + "\n\n✅ ОДОБРЕНО И ОПУБЛИКОВАНО")
    await callback.answer("Жалоба одобрена и опубликована в форуме")


@dp.callback_query(F.data == "moderate_decline")
async def moderate_decline(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    current_caption = callback.message.caption or ""
    await callback.message.edit_caption(caption=current_caption + "\n\n❌ ОТКЛОНЕНО")
    await callback.answer("Жалоба отклонена")


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
# LAST SCAMMERS
# ======================================================

@dp.callback_query(F.data == "last_scammers")
async def last_scammers(callback: CallbackQuery):
    async with aiosqlite.connect("scam.db") as db:
        cursor = await db.execute(
            "SELECT username, reason FROM users WHERE reputation='scammer' ORDER BY rowid DESC LIMIT 10")
        rows = await cursor.fetchall()

    text = "🚨 ПОСЛЕДНИЕ СКАМЕРЫ\n\n"
    if not rows:
        text += "База пуста"
    else:
        for row in rows:
            text += f"👤 @{row[0]}\n📄 {row[1]}\n\n"
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
# ADD LIKES
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
    amount = int(message.text)

    async with aiosqlite.connect("scam.db") as db:
        await db.execute("UPDATE users SET likes = likes + ? WHERE username=?", (amount, username))
        await db.commit()

    await message.answer(f"✅ Накручено {amount} лайков пользователю @{username}")
    await state.clear()


# ======================================================
# ADD VIEWS
# ======================================================

@dp.callback_query(F.data == "admin_views")
async def add_views_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(AddViewsState.username)
    await callback.message.edit_text("👁 НАКРУТКА ПРОСМОТРОВ\n\nВведите username:")


@dp.message(AddViewsState.username)
async def add_views_username(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        await state.clear()
        return
    await state.update_data(username=message.text.replace("@", "").lower())
    await state.set_state(AddViewsState.amount)
    await message.answer("👁 Введите количество просмотров")


@dp.message(AddViewsState.amount)
async def add_views_finish(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        await state.clear()
        return
    data = await state.get_data()
    username = data["username"]
    amount = int(message.text)

    async with aiosqlite.connect("scam.db") as db:
        await db.execute("UPDATE users SET views = views + ? WHERE username=?", (amount, username))
        await db.commit()

    await message.answer(f"✅ Накручено {amount} просмотров пользователю @{username}")
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


# ======================================================
# RUN
# ======================================================

async def main():
    await create_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())