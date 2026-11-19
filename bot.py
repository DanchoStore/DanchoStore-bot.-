
import os
import asyncio
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = "https://t.me/danchostore"
DB = "store.db"

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
    category = State()
    title = State()
    price = State()
    description = State()
    link = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])


def main_menu():
    return keyboard([
        [("📦 Наличие", "stock")],
        [("📱 Phone", "phone")],
        [("🔄 Выкуп iPhone", "buyback")],
        [("⭐ Отзывы", "reviews")],
        [("💬 Связаться с продавцом", "seller")]
    ])


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
        await db.commit()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🖤 Добро пожаловать в DanchoStore.\n"
        "Выбирай своё устройство.",
        reply_markup=main_menu()
    )


@dp.callback_query(F.data == "seller")
async def seller(call: CallbackQuery):
    await call.message.answer(
        "💬 Связаться с продавцом:\nhttps://t.me/LdaniilV"
    )
    await call.answer()


@dp.callback_query(F.data == "reviews")
async def reviews(call: CallbackQuery):
    await call.message.answer(
        "⭐ Отзывы DanchoStore:\n" + CHANNEL
    )
    await call.answer()


@dp.callback_query(F.data == "phone")
async def phone(call: CallbackQuery):
    await call.message.answer(
        "Выбери категорию:",
        reply_markup=keyboard([
            [("🍎 iPhone", "iphone"), ("🤖 Android", "android")],
            [("⬅️ Назад", "home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery):
    await call.message.answer(
        "Главное меню DanchoStore:",
        reply_markup=main_menu()
    )
    await call.answer()


@dp.callback_query(F.data.in_(["stock", "iphone", "android"]))
async def show_stock(call: CallbackQuery):
    category = None if call.data == "stock" else call.data

    async with aiosqlite.connect(DB) as db:
        if category:
            cursor = await db.execute(
                "SELECT id, category, title, price, description, link "
                "FROM products WHERE category=?",
                (category,)
            )
        else:
            cursor = await db.execute(
                "SELECT id, category, title, price, description, link "
                "FROM products"
            )
        products = await cursor.fetchall()

    if not products:
        await call.message.answer("📦 Пока товаров нет в наличии.")
    else:
        for p in products:
            text = (
                f"📱 {p[2]}\n"
                f"💰 Цена: {p[3]} ₽\n"
                f"{p[4]}"
            )
            await call.message.answer(
                text,
                reply_markup=keyboard([
                    [("Открыть публикацию ↗", f"product_{p[0]}")]
                ])
            )
    await call.answer()


@dp.callback_query(F.data.startswith("product_"))
async def product_link(call: CallbackQuery):
    product_id = int(call.data.split("_")[1])
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT link FROM products WHERE id=?",
            (product_id,)
        )
        result = await cursor.fetchone()

    if result:
        await call.message.answer("Ссылка на товар:\n" + result[0])
    await call.answer()


@dp.callback_query(F.data == "buyback")
async def buyback_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Buyback.model)
    await call.message.answer("📱 Какая модель iPhone?")
    await call.answer()


@dp.message(Buyback.model)
async def buy_model(message: Message, state: FSMContext):
    await state.update_data(model=message.text)
    await state.set_state(Buyback.memory)
    await message.answer("Какой объём памяти?")


@dp.message(Buyback.memory)
async def buy_memory(message: Message, state: FSMContext):
    await state.update_data(memory=message.text)
    await state.set_state(Buyback.condition)
    await message.answer("В каком состоянии телефон?")


@dp.message(Buyback.condition)
async def buy_condition(message: Message, state: FSMContext):
    await state.update_data(condition=message.text)
    await state.set_state(Buyback.battery)
    await message.answer("Состояние аккумулятора в процентах?")


@dp.message(Buyback.battery)
async def buy_battery(message: Message, state: FSMContext):
    await state.update_data(battery=message.text)
    await state.set_state(Buyback.price)
    await message.answer("За какую сумму хочешь продать iPhone? В ₽")


@dp.message(Buyback.price)
async def buy_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text)
    await state.set_state(Buyback.contact)
    await message.answer("Оставь свой Telegram username для связи.")


@dp.message(Buyback.contact)
async def buy_contact(message: Message, state: FSMContext):
    data = await state.get_data()

    text = (
        "🔔 НОВАЯ ЗАЯВКА НА ВЫКУП\n\n"
        f"Модель: {data['model']}\n"
        f"Память: {data['memory']}\n"
        f"Состояние: {data['condition']}\n"
        f"Аккумулятор: {data['battery']}\n"
        f"Цена: {data['price']} ₽\n"
        f"Контакт: {message.text}\n"
        f"Telegram ID: {message.from_user.id}"
    )

    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, text)

    await message.answer(
        "✅ Заявка отправлена продавцу!\n"
        "С тобой свяжутся после рассмотрения."
    )
    await state.clear()


@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id != ADMIN_ID or not ADMIN_ID:
        return

    await message.answer(
        "🔐 Панель администратора",
        reply_markup=keyboard([
            [("➕ Добавить товар", "add")],
            [("📋 Список товаров", "admin_list")]
        ])
    )


@dp.callback_query(F.data == "add")
async def add_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID or not ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(AddProduct.category)
    await call.message.answer("Категория: iphone или android")
    await call.answer()


@dp.message(AddProduct.category)
async def add_category(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    category = message.text.lower().strip()
    if category not in ("iphone", "android"):
        await message.answer("Напиши iphone или android.")
        return
    await state.update_data(category=category)
    await state.set_state(AddProduct.title)
    await message.answer("Название товара:")


@dp.message(AddProduct.title)
async def add_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(AddProduct.price)
    await message.answer("Цена в рублях:")


@dp.message(AddProduct.price)
async def add_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text)
    await state.set_state(AddProduct.description)
    await message.answer("Описание товара:")


@dp.message(AddProduct.description)
async def add_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AddProduct.link)
    await message.answer("Ссылка на публикацию товара в канале:")


@dp.message(AddProduct.link)
async def add_link(message: Message, state: FSMContext):
    if not message.text.startswith("https://t.me/"):
        await message.answer("Нужна ссылка вида https://t.me/...")
        return

    data = await state.get_data()
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO products "
            "(category, title, price, description, link) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                data["category"],
                data["title"],
                data["price"],
                data["description"],
                message.text
            )
        )
        await db.commit()

    await message.answer("✅ Товар добавлен в каталог!")
    await state.clear()


@dp.callback_query(F.data == "admin_list")
async def admin_list(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID or not ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT id, title, price FROM products"
        )
        products = await cursor.fetchall()

    if not products:
        await call.message.answer("Товаров пока нет.")
    else:
        for p in products:
            await call.message.answer(
                f"#{p[0]} — {p[1]} — {p[2]} ₽"
            )
    await call.answer()


async def main():
    if not TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN")
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
