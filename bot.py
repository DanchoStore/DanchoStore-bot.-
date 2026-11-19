import os
import asyncio
from urllib.parse import quote

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = "https://t.me/danchostore"
SELLER_USERNAME = "LdaniilV"
DB = os.getenv("DATABASE_PATH", "store.db")
PORT = int(os.getenv("PORT", "10000"))
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в Environment Variables")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Buyback(StatesGroup):
    model = State()
    memory = State()
    condition = State()
    battery = State()
    price = State()
    contact = State()


class AddProduct(StatesGroup):
    photo = State()
    category = State()
    title = State()
    price = State()
    description = State()
    link = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, **kwargs) for text, kwargs in row]
        for row in rows
    ])


def main_menu():
    return keyboard([
        [("📦 Наличие", {"callback_data": "stock"})],
        [("📱 Phone", {"callback_data": "phone"})],
        [("🔄 Выкуп iPhone", {"callback_data": "buyback"})],
        [("⭐ Отзывы", {"callback_data": "reviews"})],
        [("💬 Связаться с продавцом", {"callback_data": "seller"})],
    ])


def admin_menu():
    return keyboard([
        [("➕ Добавить товар", {"callback_data": "add"})],
        [("📋 Список товаров", {"callback_data": "admin_list"})],
        [("⬅️ Главное меню", {"callback_data": "home"})],
    ])


def is_admin(user_id):
    return bool(ADMIN_ID) and user_id == ADMIN_ID


async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                title TEXT,
                price TEXT,
                description TEXT,
                link TEXT
            )
        """)
        # Добавляем колонку для Telegram file_id в старую базу, если её ещё нет.
        cursor = await db.execute("PRAGMA table_info(products)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "photo_file_id" not in columns:
            await db.execute("ALTER TABLE products ADD COLUMN photo_file_id TEXT")
        await db.commit()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🖤 Добро пожаловать в DanchoStore.\n"
        "Выбирай своё устройство.",
        reply_markup=main_menu()
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


@dp.callback_query(F.data == "seller")
async def seller(call: CallbackQuery):
    await call.message.answer(f"💬 Связаться с продавцом: https://t.me/{SELLER_USERNAME}")
    await call.answer()


@dp.callback_query(F.data == "reviews")
async def reviews(call: CallbackQuery):
    await call.message.answer("⭐ Отзывы DanchoStore:\n" + CHANNEL)
    await call.answer()


@dp.callback_query(F.data == "phone")
async def phone(call: CallbackQuery):
    await call.message.answer(
        "Выбери категорию:",
        reply_markup=keyboard([
            [("🍎 iPhone", {"callback_data": "iphone"}),
             ("🤖 Android", {"callback_data": "android"})],
            [("⬅️ Назад", {"callback_data": "home"})],
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery):
    await call.message.answer("Главное меню DanchoStore:", reply_markup=main_menu())
    await call.answer()


async def send_product_card(message: Message, product):
    product_id, category, title, price, description, link, photo_file_id = product
    text = (
        f"📱 {title}\n"
        f"💰 Цена: {price} ₽\n\n"
        f"{description or ''}"
    )
    buy_text = f"Здравствуйте! Интересует {title} из DanchoStore."
    buy_url = f"https://t.me/{SELLER_USERNAME}?text={quote(buy_text)}"

    buttons = [
        [("🛒 Купить", {"url": buy_url})],
    ]
    if link:
        buttons.append([("Открыть публикацию ↗", {"url": link})])

    markup = keyboard(buttons)
    if photo_file_id:
        await message.answer_photo(photo_file_id, caption=text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@dp.callback_query(F.data.in_(["stock", "iphone", "android"]))
async def show_stock(call: CallbackQuery):
    category = None if call.data == "stock" else call.data

    async with aiosqlite.connect(DB) as db:
        if category:
            cursor = await db.execute(
                "SELECT id, category, title, price, description, link, photo_file_id "
                "FROM products WHERE category=? ORDER BY id DESC",
                (category,)
            )
        else:
            cursor = await db.execute(
                "SELECT id, category, title, price, description, link, photo_file_id "
                "FROM products ORDER BY id DESC"
            )
        products = await cursor.fetchall()

    if not products:
        await call.message.answer("📦 Пока товаров нет в наличии.")
    else:
        for product in products:
            await send_product_card(call.message, product)
    await call.answer()


@dp.callback_query(F.data == "buyback")
async def buyback_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Buyback.model)
    await call.message.answer("📱 Какая модель iPhone? Для отмены отправь /cancel.")
    await call.answer()


@dp.message(Buyback.model)
async def buy_model(message: Message, state: FSMContext):
    await state.update_data(model=message.text or "Не указано")
    await state.set_state(Buyback.memory)
    await message.answer("Какой объём памяти?")


@dp.message(Buyback.memory)
async def buy_memory(message: Message, state: FSMContext):
    await state.update_data(memory=message.text or "Не указано")
    await state.set_state(Buyback.condition)
    await message.answer("В каком состоянии телефон?")


@dp.message(Buyback.condition)
async def buy_condition(message: Message, state: FSMContext):
    await state.update_data(condition=message.text or "Не указано")
    await state.set_state(Buyback.battery)
    await message.answer("Состояние аккумулятора в процентах?")


@dp.message(Buyback.battery)
async def buy_battery(message: Message, state: FSMContext):
    await state.update_data(battery=message.text or "Не указано")
    await state.set_state(Buyback.price)
    await message.answer("За какую сумму хочешь продать iPhone? В ₽")


@dp.message(Buyback.price)
async def buy_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text or "Не указано")
    await state.set_state(Buyback.contact)
    await message.answer("Оставь свой Telegram username для связи.")


@dp.message(Buyback.contact)
async def buy_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    username = message.from_user.username
    contact = message.text or "Не указан"
    text = (
        "🔔 НОВАЯ ЗАЯВКА НА ВЫКУП\n\n"
        f"Модель: {data.get('model', '')}\n"
        f"Память: {data.get('memory', '')}\n"
        f"Состояние: {data.get('condition', '')}\n"
        f"Аккумулятор: {data.get('battery', '')}\n"
        f"Желаемая цена: {data.get('price', '')} ₽\n"
        f"Контакт: {contact}\n"
        f"Telegram: @{username if username else 'не указан'}\n"
        f"Telegram ID: {message.from_user.id}"
    )

    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, text)

    await message.answer("✅ Заявка отправлена продавцу! С тобой свяжутся после рассмотрения.")
    await state.clear()


@dp.message(Command("admin"))
async def admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔐 Панель администратора", reply_markup=admin_menu())


@dp.callback_query(F.data == "add")
async def add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddProduct.photo)
    await call.message.answer(
        "📸 Отправь фотографию товара одним фото. Для отмены отправь /cancel."
    )
    await call.answer()


@dp.message(AddProduct.photo)
async def add_photo(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.photo:
        await message.answer("Пожалуйста, отправь именно фотографию.")
        return
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await state.set_state(AddProduct.category)
    await message.answer("Категория товара: напиши iphone или android.")


@dp.message(AddProduct.category)
async def add_category(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    category = (message.text or "").lower().strip()
    if category not in ("iphone", "android"):
        await message.answer("Напиши только iphone или android.")
        return
    await state.update_data(category=category)
    await state.set_state(AddProduct.title)
    await message.answer("Напиши название и объём памяти, например: iPhone 15 Pro 256 GB.")


@dp.message(AddProduct.title)
async def add_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(title=message.text or "")
    await state.set_state(AddProduct.price)
    await message.answer("Укажи цену в рублях, только число или сумму.")


@dp.message(AddProduct.price)
async def add_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(price=message.text or "")
    await state.set_state(AddProduct.description)
    await message.answer("Напиши описание и состояние телефона.")


@dp.message(AddProduct.description)
async def add_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(description=message.text or "")
    await state.set_state(AddProduct.link)
    await message.answer(
        "Отправь ссылку на публикацию этого товара в канале "
        "(https://t.me/...). Если ссылки пока нет, отправь -"
    )


@dp.message(AddProduct.link)
async def add_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    link = (message.text or "").strip()
    if link != "-" and not link.startswith("https://t.me/"):
        await message.answer("Нужна ссылка вида https://t.me/... или отправь -")
        return

    data = await state.get_data()
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO products
            (category, title, price, description, link, photo_file_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                data["category"],
                data["title"],
                data["price"],
                data["description"],
                "" if link == "-" else link,
                data["photo_file_id"],
            )
        )
        await db.commit()

    await message.answer("✅ Товар добавлен в каталог!", reply_markup=admin_menu())
    await state.clear()


@dp.callback_query(F.data == "admin_list")
async def admin_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("SELECT id, title, price, category FROM products ORDER BY id DESC")
        products = await cursor.fetchall()

    if not products:
        await call.message.answer("Товаров пока нет.")
    else:
        for product_id, title, price, category in products:
            await call.message.answer(
                f"#{product_id} — {title} — {price} ₽ ({category})"
            )
    await call.answer()


async def main():
    if not RENDER_HOST:
        raise RuntimeError(
            "Не найдена переменная RENDER_EXTERNAL_HOSTNAME. "
            "Запускай этот файл как Render Web Service."
        )

    await init_db()

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET or None,
    ).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()

    webhook_url = f"https://{RENDER_HOST}{WEBHOOK_PATH}"
    await bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET or None,
        allowed_updates=dp.resolve_used_update_types(),
    )

    print(f"DanchoStore webhook is running at {webhook_url}")

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())