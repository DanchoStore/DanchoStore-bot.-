import asyncio
import json
import logging
import os
from urllib.parse import quote

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    CallbackQuery,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# DanchoStore Telegram shop bot
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DATABASE_PATH", "store.db")
PORT = int(os.getenv("PORT", "10000"))
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or None
CHANNEL_URL = "https://t.me/danchostore"
SELLER_USERNAME = "LdaniilV"

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID environment variable is missing")
if not HOSTNAME:
    raise RuntimeError("RENDER_EXTERNAL_HOSTNAME is missing")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("danchostore")

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


class AddProduct(StatesGroup):
    photos = State()
    category = State()
    title = State()
    price = State()
    description = State()
    link = State()


class EditProduct(StatesGroup):
    value = State()


class Buyback(StatesGroup):
    model = State()
    memory = State()
    condition = State()
    battery = State()
    desired_price = State()
    contact = State()


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ð iPhone"), KeyboardButton(text="ð¤ Android")],
            [KeyboardButton(text="ð ÐÑÐºÑÐ¿ iPhone")],
            [KeyboardButton(text="â­ ÐÑÐ·ÑÐ²Ñ"), KeyboardButton(text="ð¬ Ð¡Ð²ÑÐ·Ð°ÑÑÑÑ Ñ Ð¿ÑÐ¾Ð´Ð°Ð²ÑÐ¾Ð¼")],
        ],
        resize_keyboard=True,
        input_field_placeholder="ÐÑÐ±ÐµÑÐ¸ÑÐµ ÑÐ°Ð·Ð´ÐµÐ»",
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="â ÐÐ¾Ð±Ð°Ð²Ð¸ÑÑ ÑÐ¾Ð²Ð°Ñ")],
            [KeyboardButton(text="ð Ð¡Ð¿Ð¸ÑÐ¾Ðº ÑÐ¾Ð²Ð°ÑÐ¾Ð²")],
            [KeyboardButton(text="â¬ï¸ Ð Ð³Ð»Ð°Ð²Ð½Ð¾Ðµ Ð¼ÐµÐ½Ñ")],
        ],
        resize_keyboard=True,
    )


def category_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ð iPhone"), KeyboardButton(text="ð¤ Android")],
            [KeyboardButton(text="â ÐÑÐ¼ÐµÐ½Ð°")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def photos_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="â ÐÐ¾ÑÐ¾Ð²Ð¾ Ñ ÑÐ¾ÑÐ¾")],
            [KeyboardButton(text="â ÐÑÐ¼ÐµÐ½Ð°")],
        ],
        resize_keyboard=True,
    )


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                description TEXT DEFAULT '',
                link TEXT DEFAULT '',
                photo_file_id TEXT DEFAULT '',
                photo_file_ids TEXT DEFAULT '[]',
                is_sold INTEGER DEFAULT 0
            )
        """)
        # Migrate older store.db files without deleting existing products.
        columns = await (await db.execute("PRAGMA table_info(products)")).fetchall()
        names = {row[1] for row in columns}
        if "photo_file_id" not in names:
            await db.execute("ALTER TABLE products ADD COLUMN photo_file_id TEXT DEFAULT ''")
        if "photo_file_ids" not in names:
            await db.execute("ALTER TABLE products ADD COLUMN photo_file_ids TEXT DEFAULT '[]'")
        if "is_sold" not in names:
            await db.execute("ALTER TABLE products ADD COLUMN is_sold INTEGER DEFAULT 0")
        await db.commit()


async def get_products(category=None, include_sold=False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM products WHERE 1=1"
        args = []
        if category:
            query += " AND category = ?"
            args.append(category)
        if not include_sold:
            query += " AND is_sold = 0"
        query += " ORDER BY id DESC"
        rows = await (await db.execute(query, args)).fetchall()
        return [dict(row) for row in rows]


def product_keyboard(product):
    buttons = [
        [InlineKeyboardButton(
            text="ð ÐÑÐ¿Ð¸ÑÑ",
            url=f"https://t.me/{SELLER_USERNAME}?text={quote('ÐÐ´ÑÐ°Ð²ÑÑÐ²ÑÐ¹ÑÐµ! ÐÐ½ÑÐµÑÐµÑÑÐµÑ ' + product['title'] + ' Ð¸Ð· DanchoStore.')}"
        )]
    ]
    if product.get("link") and product["link"] != "-":
        buttons.append([InlineKeyboardButton(text="ð¢ ÐÐ¾ÑÑ Ð² ÐºÐ°Ð½Ð°Ð»Ðµ", url=product["link"])])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_product(chat_id, product):
    try:
        photos = json.loads(product.get("photo_file_ids") or "[]")
    except (TypeError, json.JSONDecodeError):
        photos = []
    if not photos and product.get("photo_file_id"):
        photos = [product["photo_file_id"]]

    status = "ð´ ÐÐ ÐÐÐÐÐ\n" if product.get("is_sold") else ""
    caption = (
        f"{status}<b>{product['title']}</b>\n"
        f"ð° Ð¦ÐµÐ½Ð°: <b>{product['price']} â½</b>\n\n"
        f"{product.get('description') or 'ÐÐ¾Ð´ÑÐ¾Ð±Ð½Ð¾ÑÑÐ¸ ÑÑÐ¾ÑÐ½ÑÐ¹ÑÐµ Ñ Ð¿ÑÐ¾Ð´Ð°Ð²ÑÐ°.'}"
    )
    markup = product_keyboard(product) if not product.get("is_sold") else None

    if photos:
        await bot.send_photo(
            chat_id=chat_id,
            photo=photos[0],
            caption=caption,
            reply_markup=markup,
        )
        for photo_id in photos[1:]:
            await bot.send_photo(chat_id=chat_id, photo=photo_id)
    else:
        await bot.send_message(chat_id, caption, reply_markup=markup)


async def show_category(message: Message, category: str):
    products = await get_products(category)
    if not products:
        await message.answer(
            f"ÐÐ¾ÐºÐ° Ð½ÐµÑ ÑÐ¾Ð²Ð°ÑÐ¾Ð² Ð² ÑÐ°Ð·Ð´ÐµÐ»Ðµ {category}. ÐÐ°Ð³Ð»ÑÐ½Ð¸ÑÐµ Ð¿Ð¾Ð·Ð¶Ðµ!",
            reply_markup=main_keyboard(),
        )
        return
    await message.answer(f"<b>Ð Ð°Ð·Ð´ÐµÐ» {category}</b> â Ð´Ð¾ÑÑÑÐ¿Ð½ÑÐµ ÑÐ¾Ð²Ð°ÑÑ:")
    for product in products:
        await send_product(message.chat.id, product)


@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "ÐÐ¾Ð±ÑÐ¾ Ð¿Ð¾Ð¶Ð°Ð»Ð¾Ð²Ð°ÑÑ Ð² DanchoStore. ÐÑÐ±Ð¸ÑÐ°Ð¹ ÑÐ²Ð¾Ñ ÑÑÑÑÐ¾Ð¹ÑÑÐ²Ð¾",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID:
        await message.answer("â Ð£ Ð²Ð°Ñ Ð½ÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð° Ðº Ð¿Ð°Ð½ÐµÐ»Ð¸ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ°.")
        return
    await message.answer("ð  <b>ÐÐ°Ð½ÐµÐ»Ñ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ° DanchoStore</b>", reply_markup=admin_keyboard())


@dp.message(F.text == "â¬ï¸ Ð Ð³Ð»Ð°Ð²Ð½Ð¾Ðµ Ð¼ÐµÐ½Ñ")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("ÐÐ»Ð°Ð²Ð½Ð¾Ðµ Ð¼ÐµÐ½Ñ:", reply_markup=main_keyboard())


@dp.message(F.text == "ð iPhone")
async def iphone(message: Message):
    await show_category(message, "iPhone")


@dp.message(F.text == "ð¤ Android")
async def android(message: Message):
    await show_category(message, "Android")


@dp.message(F.text == "â­ ÐÑÐ·ÑÐ²Ñ")
async def reviews(message: Message):
    await message.answer(f"ÐÑÐ·ÑÐ²Ñ Ð¿Ð¾ÐºÑÐ¿Ð°ÑÐµÐ»ÐµÐ¹: {CHANNEL_URL}", reply_markup=main_keyboard())


@dp.message(F.text == "ð¬ Ð¡Ð²ÑÐ·Ð°ÑÑÑÑ Ñ Ð¿ÑÐ¾Ð´Ð°Ð²ÑÐ¾Ð¼")
async def contact_seller(message: Message):
    await message.answer(
        f"Ð¡Ð²ÑÐ·Ð°ÑÑÑÑ Ñ Ð¿ÑÐ¾Ð´Ð°Ð²ÑÐ¾Ð¼: https://t.me/{SELLER_USERNAME}",
        reply_markup=main_keyboard(),
    )


@dp.message(F.text == "ð ÐÑÐºÑÐ¿ iPhone")
async def buyback_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Buyback.model)
    await message.answer("Ð£ÐºÐ°Ð¶Ð¸ÑÐµ Ð¼Ð¾Ð´ÐµÐ»Ñ iPhone:", reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="â ÐÑÐ¼ÐµÐ½Ð°")]], resize_keyboard=True
    ))


@dp.message(Buyback.model)
async def buyback_model(message: Message, state: FSMContext):
    if message.text == "â ÐÑÐ¼ÐµÐ½Ð°":
        await state.clear()
        await message.answer("ÐÑÐ¼ÐµÐ½ÐµÐ½Ð¾.", reply_markup=main_keyboard())
        return
    await state.update_data(model=message.text)
    await state.set_state(Buyback.memory)
    await message.answer("Ð¡ÐºÐ¾Ð»ÑÐºÐ¾ Ð¿Ð°Ð¼ÑÑÐ¸?")


@dp.message(Buyback.memory)
async def buyback_memory(message: Message, state: FSMContext):
    await state.update_data(memory=message.text)
    await state.set_state(Buyback.condition)
    await message.answer("ÐÐ¿Ð¸ÑÐ¸ÑÐµ ÑÐ¾ÑÑÐ¾ÑÐ½Ð¸Ðµ ÑÐµÐ»ÐµÑÐ¾Ð½Ð°:")


@dp.message(Buyback.condition)
async def buyback_condition(message: Message, state: FSMContext):
    await state.update_data(condition=message.text)
    await state.set_state(Buyback.battery)
    await message.answer("Ð£ÐºÐ°Ð¶Ð¸ÑÐµ ÑÐ¾ÑÑÐ¾ÑÐ½Ð¸Ðµ Ð°ÐºÐºÑÐ¼ÑÐ»ÑÑÐ¾ÑÐ° Ð² Ð¿ÑÐ¾ÑÐµÐ½ÑÐ°Ñ:")


@dp.message(Buyback.battery)
async def buyback_battery(message: Message, state: FSMContext):
    await state.update_data(battery=message.text)
    await state.set_state(Buyback.desired_price)
    await message.answer("ÐÐ° ÐºÐ°ÐºÑÑ ÑÑÐ¼Ð¼Ñ ÑÐ¾ÑÐ¸ÑÐµ Ð¿ÑÐ¾Ð´Ð°ÑÑ? (â½)")


@dp.message(Buyback.desired_price)
async def buyback_price(message: Message, state: FSMContext):
    await state.update_data(desired_price=message.text)
    await state.set_state(Buyback.contact)
    await message.answer("ÐÑÑÐ°Ð²ÑÑÐµ Telegram Ð´Ð»Ñ ÑÐ²ÑÐ·Ð¸ Ð¸Ð»Ð¸ Ð½Ð°Ð¿Ð¸ÑÐ¸ÑÐµ, ÐºÐ°Ðº Ñ Ð²Ð°Ð¼Ð¸ ÑÐ²ÑÐ·Ð°ÑÑÑÑ:")


@dp.message(Buyback.contact)
async def buyback_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    data["contact"] = message.text
    text = (
        "ð <b>ÐÐ¾Ð²Ð°Ñ Ð·Ð°ÑÐ²ÐºÐ° Ð½Ð° Ð²ÑÐºÑÐ¿ iPhone</b>\n"
        f"ÐÐ¾Ð´ÐµÐ»Ñ: {data.get('model')}\n"
        f"ÐÐ°Ð¼ÑÑÑ: {data.get('memory')}\n"
        f"Ð¡Ð¾ÑÑÐ¾ÑÐ½Ð¸Ðµ: {data.get('condition')}\n"
        f"ÐÐºÐºÑÐ¼ÑÐ»ÑÑÐ¾Ñ: {data.get('battery')}\n"
        f"ÐÐµÐ»Ð°ÐµÐ¼Ð°Ñ ÑÐµÐ½Ð°: {data.get('desired_price')} â½\n"
        f"ÐÐ¾Ð½ÑÐ°ÐºÑ: {data.get('contact')}\n"
        f"Telegram ID: {message.from_user.id}"
    )
    await bot.send_message(ADMIN_ID, text)
    await state.clear()
    await message.answer("Ð¡Ð¿Ð°ÑÐ¸Ð±Ð¾! ÐÐ°ÑÐ²ÐºÐ° Ð¾ÑÐ¿ÑÐ°Ð²Ð»ÐµÐ½Ð° Ð¿ÑÐ¾Ð´Ð°Ð²ÑÑ.", reply_markup=main_keyboard())


@dp.message(F.text == "â ÐÐ¾Ð±Ð°Ð²Ð¸ÑÑ ÑÐ¾Ð²Ð°Ñ")
async def add_product_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await state.update_data(photos=[])
    await state.set_state(AddProduct.photos)
    await message.answer(
        "ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ Ð´Ð¾ 5 ÑÐ¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ð¹ ÑÐ¾Ð²Ð°ÑÐ° Ð¿Ð¾ Ð¾Ð´Ð½Ð¾Ð¹. ÐÐ¾Ð³Ð´Ð° Ð·Ð°ÐºÐ¾Ð½ÑÐ¸ÑÐµ, Ð½Ð°Ð¶Ð¼Ð¸ÑÐµ Â«â ÐÐ¾ÑÐ¾Ð²Ð¾ Ñ ÑÐ¾ÑÐ¾Â».\n"
        "ÐÐ¾Ð¶Ð½Ð¾ Ð¾ÑÐ¿ÑÐ°Ð²Ð¸ÑÑ Ð¸ Ð±ÐµÐ· ÑÐ¾ÑÐ¾ â ÑÑÐ°Ð·Ñ Ð½Ð°Ð¶Ð¼Ð¸ÑÐµ Â«ÐÐ¾ÑÐ¾Ð²Ð¾ Ñ ÑÐ¾ÑÐ¾Â».",
        reply_markup=photos_keyboard(),
    )


@dp.message(AddProduct.photos, F.photo)
async def add_product_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) >= 5:
        await message.answer("Ð£Ð¶Ðµ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ 5 ÑÐ¾ÑÐ¾. ÐÐ°Ð¶Ð¼Ð¸ÑÐµ Â«â ÐÐ¾ÑÐ¾Ð²Ð¾ Ñ ÑÐ¾ÑÐ¾Â».")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(f"Ð¤Ð¾ÑÐ¾ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾: {len(photos)}/5")


@dp.message(AddProduct.photos, F.text == "â ÐÐ¾ÑÐ¾Ð²Ð¾ Ñ ÑÐ¾ÑÐ¾")
async def add_product_photos_done(message: Message, state: FSMContext):
    await state.set_state(AddProduct.category)
    await message.answer("ÐÑÐ±ÐµÑÐ¸ÑÐµ ÐºÐ°ÑÐµÐ³Ð¾ÑÐ¸Ñ:", reply_markup=category_keyboard())


@dp.message(AddProduct.photos, F.text == "â ÐÑÐ¼ÐµÐ½Ð°")
@dp.message(AddProduct.category, F.text == "â ÐÑÐ¼ÐµÐ½Ð°")
@dp.message(AddProduct.title, F.text == "â ÐÑÐ¼ÐµÐ½Ð°")
@dp.message(AddProduct.price, F.text == "â ÐÑÐ¼ÐµÐ½Ð°")
@dp.message(AddProduct.description, F.text == "â ÐÑÐ¼ÐµÐ½Ð°")
@dp.message(AddProduct.link, F.text == "â ÐÑÐ¼ÐµÐ½Ð°")
async def cancel_add(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("ÐÐ¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¸Ðµ ÑÐ¾Ð²Ð°ÑÐ° Ð¾ÑÐ¼ÐµÐ½ÐµÐ½Ð¾.", reply_markup=admin_keyboard())


@dp.message(AddProduct.category)
async def add_product_category(message: Message, state: FSMContext):
    if message.text not in ("ð iPhone", "ð¤ Android"):
        await message.answer("ÐÑÐ±ÐµÑÐ¸ÑÐµ ð iPhone Ð¸Ð»Ð¸ ð¤ Android.")
        return
    category = "iPhone" if message.text == "ð iPhone" else "Android"
    await state.update_data(category=category)
    await state.set_state(AddProduct.title)
    await message.answer("ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ ÑÐ¾Ð²Ð°ÑÐ°, Ð½Ð°Ð¿ÑÐ¸Ð¼ÐµÑ: iPhone 15 Pro 256 GB")


@dp.message(AddProduct.title)
async def add_product_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(AddProduct.price)
    await message.answer("ÐÐ²ÐµÐ´Ð¸ÑÐµ ÑÐµÐ½Ñ Ð² ÑÑÐ±Ð»ÑÑ (ÑÐ¾Ð»ÑÐºÐ¾ ÑÐ¸ÑÐ»Ð¾):")


@dp.message(AddProduct.price)
async def add_product_price(message: Message, state: FSMContext):
    price = message.text.strip().replace(" ", "")
    if not price.isdigit():
        await message.answer("ÐÐ²ÐµÐ´Ð¸ÑÐµ ÑÐµÐ½Ñ ÑÐ¸ÑÑÐ°Ð¼Ð¸, Ð½Ð°Ð¿ÑÐ¸Ð¼ÐµÑ 65000.")
        return
    await state.update_data(price=price)
    await state.set_state(AddProduct.description)
    await message.answer("ÐÐ²ÐµÐ´Ð¸ÑÐµ ÑÐ¾ÑÑÐ¾ÑÐ½Ð¸Ðµ Ð¸ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ ÑÐ¾Ð²Ð°ÑÐ°:")


@dp.message(AddProduct.description)
async def add_product_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AddProduct.link)
    await message.answer("ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÑÑÐ»ÐºÑ Ð½Ð° Ð¿Ð¾ÑÑ ÑÐ¾Ð²Ð°ÑÐ° Ð² ÐºÐ°Ð½Ð°Ð»Ðµ Ð¸Ð»Ð¸ '-' ÐµÑÐ»Ð¸ ÑÑÑÐ»ÐºÐ¸ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ:")


@dp.message(AddProduct.link)
async def add_product_link(message: Message, state: FSMContext):
    data = await state.get_data()
    link = message.text.strip()
    photos = data.get("photos", [])
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO products
               (category, title, price, description, link, photo_file_id, photo_file_ids, is_sold)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                data["category"], data["title"], data["price"], data["description"],
                link, photos[0] if photos else "", json.dumps(photos),
            ),
        )
        product_id = cursor.lastrowid
        await db.commit()
    await state.clear()
    await message.answer(
        f"â Ð¢Ð¾Ð²Ð°Ñ #{product_id} Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½!\n"
        f"{data['title']} â {data['price']} â½\nÐ¤Ð¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ð¹: {len(photos)}",
        reply_markup=admin_keyboard(),
    )


@dp.message(F.text == "ð Ð¡Ð¿Ð¸ÑÐ¾Ðº ÑÐ¾Ð²Ð°ÑÐ¾Ð²")
async def admin_products(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    products = await get_products(include_sold=True)
    if not products:
        await message.answer("Ð¢Ð¾Ð²Ð°ÑÐ¾Ð² Ð¿Ð¾ÐºÐ° Ð½ÐµÑ.", reply_markup=admin_keyboard())
        return
    for product in products[:30]:
        status = "ð´ ÐÑÐ¾Ð´Ð°Ð½Ð¾" if product.get("is_sold") else "ð¢ Ð Ð½Ð°Ð»Ð¸ÑÐ¸Ð¸"
        try:
            count = len(json.loads(product.get("photo_file_ids") or "[]"))
        except (TypeError, json.JSONDecodeError):
            count = 1 if product.get("photo_file_id") else 0
        actions = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="âï¸ ÐÐ·Ð¼ÐµÐ½Ð¸ÑÑ", callback_data=f"p_edit:{product['id']}"),
                InlineKeyboardButton(
                    text="ð´ ÐÑÐ¾Ð´Ð°Ð½Ð¾" if not product.get("is_sold") else "ð¢ ÐÐµÑÐ½ÑÑÑ Ð² Ð½Ð°Ð»Ð¸ÑÐ¸Ðµ",
                    callback_data=f"p_sold:{product['id']}",
                ),
            ],
            [InlineKeyboardButton(text="ð Ð£Ð´Ð°Ð»Ð¸ÑÑ", callback_data=f"p_delete:{product['id']}")],
        ])
        await message.answer(
            f"#{product['id']} â <b>{product['title']}</b>\\n"
            f"ÐÐ°ÑÐµÐ³Ð¾ÑÐ¸Ñ: {product['category']}\\n"
            f"Ð¦ÐµÐ½Ð°: {product['price']} â½\\n"
            f"Ð¤Ð¾ÑÐ¾: {count}\\n{status}",
            reply_markup=actions,
        )


@dp.callback_query(F.data.startswith("p_edit:"))
async def product_edit_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    product_id = int(callback.data.split(":")[1])
    fields = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ", callback_data=f"p_field:{product_id}:title")],
        [InlineKeyboardButton(text="Ð¦ÐµÐ½Ð°", callback_data=f"p_field:{product_id}:price")],
        [InlineKeyboardButton(text="ÐÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ", callback_data=f"p_field:{product_id}:description")],
        [InlineKeyboardButton(text="Ð¡ÑÑÐ»ÐºÐ° Ð½Ð° Ð¿Ð¾ÑÑ", callback_data=f"p_field:{product_id}:link")],
    ])
    await state.clear()
    await callback.message.answer(f"Ð§ÑÐ¾ Ð¸Ð·Ð¼ÐµÐ½Ð¸ÑÑ Ñ ÑÐ¾Ð²Ð°ÑÐ° #{product_id}?", reply_markup=fields)
    await callback.answer()


@dp.callback_query(F.data.startswith("p_field:"))
async def product_edit_field(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    _, product_id, field = callback.data.split(":")
    await state.update_data(product_id=int(product_id), field=field)
    await state.set_state(EditProduct.value)
    prompts = {
        "title": "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²Ð¾Ðµ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ ÑÐ¾Ð²Ð°ÑÐ°:",
        "price": "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²ÑÑ ÑÐµÐ½Ñ Ð² ÑÑÐ±Ð»ÑÑ:",
        "description": "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²Ð¾Ðµ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ:",
        "link": "ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ Ð½Ð¾Ð²ÑÑ ÑÑÑÐ»ÐºÑ Ð½Ð° Ð¿Ð¾ÑÑ Ð¸Ð»Ð¸ '-' ÑÑÐ¾Ð±Ñ ÑÐ±ÑÐ°ÑÑ ÑÑÑÐ»ÐºÑ:",
    }
    await callback.message.answer(prompts[field])
    await callback.answer()


@dp.message(EditProduct.value)
async def product_edit_save(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data["field"]
    value = (message.text or "").strip()
    if field == "price":
        value = value.replace(" ", "")
        if not value.isdigit():
            await message.answer("Ð¦ÐµÐ½Ð° Ð´Ð¾Ð»Ð¶Ð½Ð° ÑÐ¾Ð´ÐµÑÐ¶Ð°ÑÑ ÑÐ¾Ð»ÑÐºÐ¾ ÑÐ¸ÑÑÑ. ÐÐ¾Ð¿ÑÐ¾Ð±ÑÐ¹ÑÐµ ÐµÑÑ ÑÐ°Ð·:")
            return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE products SET {field} = ? WHERE id = ?",
            (value, data["product_id"]),
        )
        await db.commit()
    await state.clear()
    await message.answer(f"â Ð¢Ð¾Ð²Ð°Ñ #{data['product_id']} Ð¾Ð±Ð½Ð¾Ð²Ð»ÑÐ½.", reply_markup=admin_keyboard())


@dp.callback_query(F.data.startswith("p_sold:"))
async def product_toggle_sold(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    product_id = int(callback.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT is_sold FROM products WHERE id = ?", (product_id,)
        )).fetchone()
        if row is None:
            await callback.answer("Ð¢Ð¾Ð²Ð°Ñ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½", show_alert=True)
            return
        new_value = 0 if row[0] else 1
        await db.execute("UPDATE products SET is_sold = ? WHERE id = ?", (new_value, product_id))
        await db.commit()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Ð¡ÑÐ°ÑÑÑ ÑÐ¾Ð²Ð°ÑÐ° Ð¾Ð±Ð½Ð¾Ð²Ð»ÑÐ½. ÐÐ±Ð½Ð¾Ð²Ð¸ÑÐµ ÑÐ¿Ð¸ÑÐ¾Ðº ÑÐµÑÐµÐ· /admin.")


@dp.callback_query(F.data.startswith("p_delete:"))
async def product_delete(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    product_id = int(callback.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        await db.commit()
    await callback.message.edit_text(f"ð Ð¢Ð¾Ð²Ð°Ñ #{product_id} ÑÐ´Ð°Ð»ÑÐ½.")
    await callback.answer("Ð£Ð´Ð°Ð»ÐµÐ½Ð¾")


@dp.message()
async def fallback(message: Message):
    if message.text and message.text.startswith("/"):
        await message.answer("ÐÐµÐ¸Ð·Ð²ÐµÑÑÐ½Ð°Ñ ÐºÐ¾Ð¼Ð°Ð½Ð´Ð°. ÐÐ°Ð¶Ð¼Ð¸ÑÐµ /start.")
    elif message.from_user.id == ADMIN_ID:
        await message.answer("ÐÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ /admin Ð´Ð»Ñ Ð¿Ð°Ð½ÐµÐ»Ð¸ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ°.")


async def health(request):
    return web.Response(text="DanchoStore bot is running")


async def on_startup(bot: Bot):
    await init_db()
    webhook_url = f"https://{HOSTNAME}{WEBHOOK_PATH}"
    await bot.set_webhook(
        webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info("Webhook configured: %s", webhook_url)


async def on_shutdown(bot: Bot):
    await bot.delete_webhook()


async def main():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("DanchoStore is listening on port %s", PORT)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
    # ==============================
# ДОБАВЛЕНИЕ ТОВАРОВ
# ==============================

@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.clear()
    await state.update_data(photos=[])
    await state.set_state(AddProduct.photos)

    await message.answer(
        "📸 <b>Добавление нового товара</b>\n\n"
        "Отправьте до 5 фотографий товара по одной.\n"
        "Когда закончите, нажмите «✅ Готово с фото».\n\n"
        "Если фотографий нет, просто нажмите кнопку.",
        reply_markup=photos_keyboard(),
    )


@dp.message(AddProduct.photos, F.photo)
async def add_product_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])

    if len(photos) >= 5:
        await message.answer(
            "Вы уже добавили 5 фотографий. "
            "Нажмите «✅ Готово с фото»."
        )
        return

    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)

    await message.answer(
        f"✅ Фото добавлено: {len(photos)}/5"
    )


@dp.message(
    AddProduct.photos,
    F.text == "✅ Готово с фото"
)
async def add_product_photos_done(
    message: Message,
    state: FSMContext
):
    await state.set_state(AddProduct.category)

    await message.answer(
        "Выберите категорию товара:",
        reply_markup=category_keyboard(),
    )


# ==============================
# ОТМЕНА ДОБАВЛЕНИЯ
# ==============================

@dp.message(AddProduct.photos, F.text == "❌ Отмена")
@dp.message(AddProduct.category, F.text == "❌ Отмена")
@dp.message(AddProduct.title, F.text == "❌ Отмена")
@dp.message(AddProduct.price, F.text == "❌ Отмена")
@dp.message(AddProduct.description, F.text == "❌ Отмена")
@dp.message(AddProduct.link, F.text == "❌ Отмена")
async def cancel_add(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "Добавление товара отменено.",
        reply_markup=admin_keyboard(),
    )


# ==============================
# ВЫБОР КАТЕГОРИИ
# ==============================

@dp.message(AddProduct.category)
async def add_product_category(
    message: Message,
    state: FSMContext
):
    if message.text not in ("🍎 iPhone", "🤖 Android"):
        await message.answer(
            "Пожалуйста, выберите категорию кнопкой ниже."
        )
        return

    category = (
        "iPhone"
        if message.text == "🍎 iPhone"
        else "Android"
    )

    await state.update_data(category=category)
    await state.set_state(AddProduct.title)

    await message.answer(
        "Введите название товара.\n\n"
        "Например: iPhone 15 Pro 256 GB",
        reply_markup=cancel_keyboard(),
    )


# ==============================
# НАЗВАНИЕ ТОВАРА
# ==============================

@dp.message(AddProduct.title)
async def add_product_title(
    message: Message,
    state: FSMContext
):
    title = (message.text or "").strip()

    if not title:
        await message.answer("Введите название товара.")
        return

    await state.update_data(title=title)
    await state.set_state(AddProduct.price)

    await message.answer(
        "Введите цену в рублях.\n"
        "Например: 65000",
    )


# ==============================
# ЦЕНА ТОВАРА
# ==============================

@dp.message(AddProduct.price)
async def add_product_price(
    message: Message,
    state: FSMContext
):
    price = (message.text or "").strip().replace(" ", "")

    if not price.isdigit():
        await message.answer(
            "Цена должна содержать только цифры.\n"
            "Например: 65000"
        )
        return

    await state.update_data(price=price)
    await state.set_state(AddProduct.description)

    await message.answer(
        "Введите состояние и описание товара.\n\n"
        "Например: Отличное состояние, "
        "без царапин, комплект полный."
    )


# ==============================
# ОПИСАНИЕ ТОВАРА
# ==============================

@dp.message(AddProduct.description)
async def add_product_description(
    message: Message,
    state: FSMContext
):
    description = (message.text or "").strip()

    if not description:
        await message.answer("Введите описание товара.")
        return

    await state.update_data(description=description)
    await state.set_state(AddProduct.link)

    await message.answer(
        "Отправьте ссылку на пост товара в канале.\n\n"
        "Если ссылки пока нет, отправьте символ -"
    )


# ==============================
# СОХРАНЕНИЕ ТОВАРА
# ==============================

@dp.message(AddProduct.link)
async def add_product_link(
    message: Message,
    state: FSMContext
):
    data = await state.get_data()

    link = (message.text or "").strip()

    if link != "-" and not (
        link.startswith("https://t.me/")
        or link.startswith("http://t.me/")
    ):
        await message.answer(
            "Отправьте корректную ссылку на пост "
            "или символ -."
        )
        return

    photos = data.get("photos", [])

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO products (
                category,
                title,
                price,
                description,
                link,
                photo_file_id,
                photo_file_ids,
                is_sold
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                data["category"],
                data["title"],
                data["price"],
                data["description"],
                link,
                photos[0] if photos else "",
                json.dumps(photos, ensure_ascii=False),
            ),
        )

        product_id = cursor.lastrowid
        await db.commit()

    await state.clear()

    await message.answer(
        f"✅ <b>Товар успешно добавлен!</b>\n\n"
        f"Номер: #{product_id}\n"
        f"Название: {data['title']}\n"
        f"Цена: {data['price']} ₽\n"
        f"Фотографий: {len(photos)}",
        reply_markup=admin_keyboard(),
    )


# ==============================
# СПИСОК ТОВАРОВ
# ==============================

@dp.message(F.text == "📋 Список товаров")
async def admin_products(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    products = await get_products(include_sold=True)

    if not products:
        await message.answer(
            "Пока товаров нет.",
            reply_markup=admin_keyboard(),
        )
        return

    await message.answer(
        "📋 <b>Список товаров DanchoStore</b>"
    )

    for product in products[:30]:
        status = (
            "🔴 Продано"
            if product.get("is_sold")
            else "🟢 В наличии"
        )

        try:
            photos = json.loads(
                product.get("photo_file_ids") or "[]"
            )
            count = len(photos)
        except (TypeError, json.JSONDecodeError):
            count = 1 if product.get("photo_file_id") else 0

        actions = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Изменить",
                        callback_data=f"p_edit:{product['id']}",
                    ),
                    InlineKeyboardButton(
                        text=(
                            "🔴 Продано"
                            if not product.get("is_sold")
                            else "🟢 Вернуть в наличие"
                        ),
                        callback_data=f"p_sold:{product['id']}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить",
                        callback_data=f"p_delete:{product['id']}",
                    )
                ],
            ]
        )

        await message.answer(
            f"#{product['id']} — <b>{product['title']}</b>\n"
            f"Категория: {product['category']}\n"
            f"Цена: {product['price']} ₽\n"
            f"Фотографий: {count}\n"
            f"Статус: {status}",
            reply_markup=actions,
        )


# ==============================
# НАЧАЛО РЕДАКТИРОВАНИЯ
# ==============================

@dp.callback_query(F.data.startswith("p_edit:"))
async def product_edit_start(
    callback: CallbackQuery,
    state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    product_id = int(callback.data.split(":")[1])

    fields = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Название",
                    callback_data=f"p_field:{product_id}:title",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Цена",
                    callback_data=f"p_field:{product_id}:price",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Описание",
                    callback_data=f"p_field:{product_id}:description",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Ссылка на пост",
                    callback_data=f"p_field:{product_id}:link",
                )
            ],
        ]
    )

    await state.clear()

    await callback.message.answer(
        f"Что хотите изменить у товара #{product_id}?",
        reply_markup=fields,
    )

    await callback.answer()


# ==============================
# ВЫБОР ПОЛЯ ДЛЯ РЕДАКТИРОВАНИЯ
# ==============================

@dp.callback_query(F.data.startswith("p_field:"))
async def product_edit_field(
    callback: CallbackQuery,
    state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    _, product_id, field = callback.data.split(":")

    await state.update_data(
        product_id=int(product_id),
        field=field,
    )

    await state.set_state(EditProduct.value)

    prompts = {
        "title": "Введите новое название товара:",
        "price": "Введите новую цену в рублях:",
        "description": "Введите новое описание:",
        "link": (
            "Отправьте новую ссылку на пост "
            "или - чтобы убрать ссылку:"
        ),
    }

    await callback.message.answer(
        prompts[field],
        reply_markup=cancel_keyboard(),
    )

    await callback.answer()


# ==============================
# СОХРАНЕНИЕ ИЗМЕНЕНИЙ
# ==============================

@dp.message(EditProduct.value)
async def product_edit_save(
    message: Message,
    state: FSMContext
):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Редактирование отменено.",
            reply_markup=admin_keyboard(),
        )
        return

    data = await state.get_data()

    field = data["field"]
    value = (message.text or "").strip()

    if not value:
        await message.answer("Введите значение.")
        return

    if field == "price":
        value = value.replace(" ", "")

        if not value.isdigit():
            await message.answer(
                "Цена должна содержать только цифры."
            )
            return

    if field == "link":
        if value != "-" and not (
            value.startswith("https://t.me/")
            or value.startswith("http://t.me/")
        ):
            await message.answer(
                "Отправьте корректную ссылку или -."
            )
            return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE products SET {field} = ? WHERE id = ?",
            (value, data["product_id"]),
        )
        await db.commit()

    await state.clear()

    await message.answer(
        f"✅ Товар #{data['product_id']} успешно обновлён.",
        reply_markup=admin_keyboard(),
    )


# ==============================
# ОТМЕТКА ПРОДАННОГО ТОВАРА
# ==============================

@dp.callback_query(F.data.startswith("p_sold:"))
async def product_toggle_sold(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    product_id = int(callback.data.split(":")[1])

    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT is_sold FROM products WHERE id = ?",
                (product_id,),
            )
        ).fetchone()

        if row is None:
            await callback.answer(
                "Товар не найден.",
                show_alert=True,
            )
            return

        new_value = 0 if row[0] else 1

        await db.execute(
            "UPDATE products SET is_sold = ? WHERE id = ?",
            (new_value, product_id),
        )

        await db.commit()

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer(
        "Статус товара обновлён. "
        "Откройте список товаров заново через /admin."
    )


# ==============================
# УДАЛЕНИЕ ТОВАРА
# ==============================

@dp.callback_query(F.data.startswith("p_delete:"))
async def product_delete(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    product_id = int(callback.data.split(":")[1])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM products WHERE id = ?",
            (product_id,),
        )
        await db.commit()

    await callback.message.edit_text(
        f"🗑 Товар #{product_id} удалён."
    )

    await callback.answer("Удалено.") 
    # ==============================
# ОБРАБОТКА НЕИЗВЕСТНЫХ СООБЩЕНИЙ
# ==============================

@dp.message()
async def fallback(message: Message):
    if message.text and message.text.startswith("/"):
        await message.answer(
            "Неизвестная команда.\n"
            "Нажмите /start, чтобы открыть главное меню."
        )

    elif message.from_user.id == ADMIN_ID:
        await message.answer(
            "Для управления магазином используйте /admin."
        )

    else:
        await message.answer(
            "Пожалуйста, выберите нужный раздел в меню.",
            reply_markup=main_keyboard(),
        )


# ==============================
# ПРОВЕРКА РАБОТЫ СЕРВЕРА
# ==============================

async def health(request):
    return web.Response(
        text="DanchoStore bot is running"
    )


# ==============================
# ЗАПУСК WEBHOOK
# ==============================

async def on_startup(bot: Bot):
    await init_db()

    webhook_url = (
        f"https://{HOSTNAME}{WEBHOOK_PATH}"
    )

    await bot.set_webhook(
        webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dp.resolve_used_update_types(),
    )

    logger.info(
        "Webhook configured: %s",
        webhook_url,
    )


async def on_shutdown(bot: Bot):
    await bot.delete_webhook()


# ==============================
# ОСНОВНОЙ ЗАПУСК
# ==============================

async def main():
    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(
        app,
        path=WEBHOOK_PATH,
    )

    setup_application(
        app,
        dp,
        bot=bot,
    )

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    logger.info(
        "DanchoStore is listening on port %s",
        PORT,
    )

    try:
        await asyncio.Event().wait()

    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
    # =========================
# ВЫКУП IPHONE
# =========================

@dp.message(F.text == "🔄 Выкуп iPhone")
async def buyback_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Buyback.model)

    await message.answer(
        "Напишите модель iPhone, которую хотите продать.",
        reply_markup=cancel_keyboard(),
    )


@dp.message(Buyback.model, F.text == "❌ Отмена")
@dp.message(Buyback.memory, F.text == "❌ Отмена")
@dp.message(Buyback.condition, F.text == "❌ Отмена")
@dp.message(Buyback.battery, F.text == "❌ Отмена")
@dp.message(Buyback.price, F.text == "❌ Отмена")
@dp.message(Buyback.contact, F.text == "❌ Отмена")
async def cancel_buyback(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Заявка отменена.",
        reply_markup=main_keyboard(),
    )


@dp.message(Buyback.model)
async def buyback_model(message: Message, state: FSMContext):
    await state.update_data(model=message.text)
    await state.set_state(Buyback.memory)
    await message.answer("Укажите объём памяти.")


@dp.message(Buyback.memory)
async def buyback_memory(message: Message, state: FSMContext):
    await state.update_data(memory=message.text)
    await state.set_state(Buyback.condition)
    await message.answer("Опишите состояние телефона.")


@dp.message(Buyback.condition)
async def buyback_condition(message: Message, state: FSMContext):
    await state.update_data(condition=message.text)
    await state.set_state(Buyback.battery)
    await message.answer("Укажите состояние аккумулятора в процентах.")


@dp.message(Buyback.battery)
async def buyback_battery(message: Message, state: FSMContext):
    await state.update_data(battery=message.text)
    await state.set_state(Buyback.price)
    await message.answer("За какую сумму хотите продать телефон?")


@dp.message(Buyback.price)
async def buyback_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text)
    await state.set_state(Buyback.contact)
    await message.answer("Оставьте Telegram для связи или напишите «здесь».")


@dp.message(Buyback.contact)
async def buyback_contact(message: Message, state: FSMContext):
    await state.update_data(contact=message.text)
    data = await state.get_data()

    report = (
        "🔄 НОВАЯ ЗАЯВКА НА ВЫКУП IPHONE\n\n"
        f"Модель: {data.get('model')}\n"
        f"Память: {data.get('memory')}\n"
        f"Состояние: {data.get('condition')}\n"
        f"Аккумулятор: {data.get('battery')}\n"
        f"Желаемая цена: {data.get('price')}\n"
        f"Контакт: {data.get('contact')}\n"
        f"Telegram: @{message.from_user.username or 'не указан'}\n"
        f"ID: {message.from_user.id}"
    )

    await bot.send_message(ADMIN_ID, report)
    await state.clear()

    await message.answer(
        "Спасибо! Заявка отправлена продавцу.",
        reply_markup=main_keyboard(),
    )


# =========================
# ДОБАВЛЕНИЕ ТОВАРА
# =========================

@dp.message(F.text == "➕ Добавить товар")
async def add_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.clear()
    await state.set_state(AddProduct.category)

    await message.answer(
        "Выберите категорию:",
        reply_markup=category_keyboard(),
    )


@dp.message(AddProduct.category, F.text == "❌ Отмена")
@dp.message(AddProduct.title, F.text == "❌ Отмена")
@dp.message(AddProduct.price, F.text == "❌ Отмена")
@dp.message(AddProduct.description, F.text == "❌ Отмена")
@dp.message(AddProduct.link, F.text == "❌ Отмена")
@dp.message(AddProduct.photos, F.text == "❌ Отмена")
async def cancel_add(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Добавление отменено.",
        reply_markup=admin_keyboard(),
    )


@dp.message(AddProduct.category)
async def add_category(message: Message, state: FSMContext):
    if message.text not in ("🍎 iPhone", "🤖 Android"):
        await message.answer("Выберите категорию кнопкой.")
        return

    category = (
        "iPhone"
        if message.text == "🍎 iPhone"
        else "Android"
    )

    await state.update_data(
        category=category,
        photos=[],
    )

    await state.set_state(AddProduct.title)
    await message.answer("Введите название товара и память.")


@dp.message(AddProduct.title)
async def add_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(AddProduct.price)
    await message.answer("Введите цену в рублях. Например: 79900.")


@dp.message(AddProduct.price)
async def add_price(message: Message, state: FSMContext):
    price = (message.text or "").replace(" ", "").replace(",", ".")

    if not price.replace(".", "", 1).isdigit():
        await message.answer("Введите цену числом.")
        return

    await state.update_data(price=price)
    await state.set_state(AddProduct.description)
    await message.answer("Введите описание и состояние товара.")


@dp.message(AddProduct.description)
async def add_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text or "")
    await state.set_state(AddProduct.link)

    await message.answer(
        "Отправьте ссылку на пост в канале "
        "или отправьте дефис -, чтобы пропустить."
    )


@dp.message(AddProduct.link)
async def add_link(message: Message, state: FSMContext):
    link = (message.text or "").strip()

    if link == "-":
        link = ""
    elif link and not link.startswith("https://t.me/"):
        await message.answer(
            "Пришлите ссылку вида https://t.me/канал/123 или дефис -."
        )
        return

    await state.update_data(link=link)
    await state.set_state(AddProduct.photos)

    await message.answer(
        "Отправляйте фотографии по одной. Максимум 5.\n"
        "Когда закончите, нажмите «Готово с фото».",
        reply_markup=photos_keyboard(),
    )


@dp.message(AddProduct.photos, F.photo)
async def add_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])

    if len(photos) >= 5:
        await message.answer(
            "Уже добавлено 5 фотографий. Нажмите «Готово с фото»."
        )
        return

    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)

    await message.answer(f"Фото добавлено: {len(photos)}/5.")


async def save_product(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO products
            (
                category,
                title,
                price,
                description,
                link,
                photo_file_id,
                photo_file_ids,
                is_sold
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                data["category"],
                data["title"],
                data["price"],
                data["description"],
                data["link"],
                photos[0] if photos else "",
                json.dumps(photos),
            ),
        )

        product_id = cursor.lastrowid
        await db.commit()

    await state.clear()

    await message.answer(
        f"✅ Товар добавлен. Номер: {product_id}",
        reply_markup=admin_keyboard(),
    )


@dp.message(
    AddProduct.photos,
    F.text.in_({
        "✅ Готово с фото",
        "⏭ Пропустить фото",
    }),
)
async def finish_photos(message: Message, state: FSMContext):
    await save_product(message, state)


@dp.message(AddProduct.photos)
async def wrong_photo_input(message: Message):
    await message.answer(
        "Отправьте фото или нажмите одну из кнопок ниже."
    )


# =========================
# СПИСОК ТОВАРОВ
# =========================

@dp.message(F.text == "📋 Список товаров")
async def admin_products(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    products = await get_products(include_sold=True)

    if not products:
        await message.answer(
            "Товаров пока нет.",
            reply_markup=admin_keyboard(),
        )
        return

    await message.answer("📋 Список товаров:")

    for product in products[:50]:
        status = (
            "Продан"
            if product["is_sold"]
            else "В наличии"
        )

        text = (
            f"Товар №{product['id']}\n"
            f"{product['title']}\n"
            f"Категория: {product['category']}\n"
            f"Цена: {product['price']} ₽\n"
            f"Статус: {status}"
        )

        await message.answer(
            text,
            reply_markup=product_admin_keyboard(
                product["id"],
                product["is_sold"],
            ),
        ) 
        # =========================
# РЕДАКТИРОВАНИЕ ТОВАРОВ
# =========================

@dp.message(F.text == "✏️ Редактировать товар")
async def edit_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.clear()
    await state.set_state(EditProduct.product_id)

    await message.answer(
        "Введите номер товара для редактирования.",
        reply_markup=cancel_keyboard(),
    )


@dp.message(EditProduct.product_id, F.text == "❌ Отмена")
@dp.message(EditProduct.field, F.text == "❌ Отмена")
@dp.message(EditProduct.value, F.text == "❌ Отмена")
async def cancel_edit(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "Редактирование отменено.",
        reply_markup=admin_keyboard(),
    )


@dp.message(EditProduct.product_id)
async def edit_choose_id(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        await message.answer("Введите номер товара цифрами.")
        return

    product = await get_product(int(message.text))

    if not product:
        await message.answer("Товар не найден.")
        return

    await state.update_data(product_id=int(message.text))
    await state.set_state(EditProduct.field)

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Название"),
                KeyboardButton(text="Цена"),
            ],
            [
                KeyboardButton(text="Описание"),
                KeyboardButton(text="Ссылка"),
            ],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
    )

    await message.answer("Что изменить?", reply_markup=keyboard)


@dp.message(EditProduct.field)
async def edit_choose_field(message: Message, state: FSMContext):
    fields = {
        "Название": "title",
        "Цена": "price",
        "Описание": "description",
        "Ссылка": "link",
    }

    if message.text not in fields:
        await message.answer("Выберите поле кнопкой.")
        return

    await state.update_data(field=fields[message.text])
    await state.set_state(EditProduct.value)

    await message.answer(
        "Введите новое значение.",
        reply_markup=cancel_keyboard(),
    )


@dp.message(EditProduct.value)
async def edit_save(message: Message, state: FSMContext):
    data = await state.get_data()

    field = data["field"]
    value = (message.text or "").strip()

    if field == "price":
        value = value.replace(" ", "").replace(",", ".")

        if not value.replace(".", "", 1).isdigit():
            await message.answer("Введите цену числом.")
            return

    if field == "link" and value and not value.startswith("https://t.me/"):
        await message.answer(
            "Ссылка должна начинаться с https://t.me/"
        )
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"UPDATE products SET {field} = ? WHERE id = ?",
            (value, data["product_id"]),
        )
        await db.commit()

    await state.clear()

    await message.answer(
        "✅ Товар обновлён.",
        reply_markup=admin_keyboard(),
    )


# =========================
# УДАЛЕНИЕ ТОВАРОВ
# =========================

@dp.message(F.text == "🗑 Удалить товар")
async def delete_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.clear()
    await state.set_state(DeleteProduct.product_id)

    await message.answer(
        "Введите номер товара для удаления.",
        reply_markup=cancel_keyboard(),
    )


@dp.message(DeleteProduct.product_id, F.text == "❌ Отмена")
async def cancel_delete(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "Удаление отменено.",
        reply_markup=admin_keyboard(),
    )


@dp.message(DeleteProduct.product_id)
async def delete_by_id(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        await message.answer("Введите номер товара цифрами.")
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM products WHERE id = ?",
            (int(message.text),),
        )

        await db.commit()
        deleted = cursor.rowcount

    await state.clear()

    await message.answer(
        "🗑 Товар удалён."
        if deleted
        else "Товар с таким номером не найден.",
        reply_markup=admin_keyboard(),
    )


# =========================
# ОТМЕТКА О ПРОДАЖЕ
# =========================

@dp.callback_query(F.data.startswith("toggle:"))
async def toggle_product(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    product_id = int(callback.data.split(":")[1])
    product = await get_product(product_id)

    if not product:
        await callback.answer("Товар не найден.")
        return

    new_status = 0 if product["is_sold"] else 1

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE products SET is_sold = ? WHERE id = ?",
            (new_status, product_id),
        )

        await db.commit()

    await callback.message.edit_reply_markup(
        reply_markup=product_admin_keyboard(
            product_id,
            new_status,
        )
    )

    await callback.answer("Статус обновлён.")


# =========================
# РЕДАКТИРОВАНИЕ ИЗ КАРТОЧКИ
# =========================

@dp.callback_query(F.data.startswith("edit:"))
async def edit_from_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    product_id = int(callback.data.split(":")[1])
    product = await get_product(product_id)

    if not product:
        await callback.answer("Товар не найден.")
        return

    await state.clear()
    await state.update_data(product_id=product_id)
    await state.set_state(EditProduct.field)

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Название"),
                KeyboardButton(text="Цена"),
            ],
            [
                KeyboardButton(text="Описание"),
                KeyboardButton(text="Ссылка"),
            ],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
    )

    await callback.message.answer(
        "Что изменить?",
        reply_markup=keyboard,
    )

    await callback.answer()


# =========================
# УДАЛЕНИЕ ИЗ КАРТОЧКИ
# =========================

@dp.callback_query(F.data.startswith("delete:"))
async def delete_from_card(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    product_id = int(callback.data.split(":")[1])

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "DELETE FROM products WHERE id = ?",
            (product_id,),
        )

        await db.commit()

    await callback.message.edit_text("🗑 Товар удалён.")
    await callback.answer("Удалено.")


# =========================
# НЕИЗВЕСТНЫЕ СООБЩЕНИЯ
# =========================

@dp.message()
async def fallback(message: Message):
    if message.text and message.text.startswith("/"):
        await message.answer(
            "Неизвестная команда. Отправьте /start."
        )
        return

    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "Для управления магазином используйте /admin."
        )
    else:
        await message.answer(
            "Пожалуйста, выберите раздел в меню.",
            reply_markup=main_keyboard(),
        )


# =========================
# WEBHOOK
# =========================

async def health(request: web.Request):
    return web.Response(
        text="DanchoStore bot is running"
    )


async def on_startup(bot_instance: Bot):
    await init_db()

    webhook_url = (
        f"https://{HOSTNAME}{WEBHOOK_PATH}"
    )

    await bot_instance.set_webhook(
        webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dp.resolve_used_update_types(),
    )

    logging.info("Webhook установлен: %s", webhook_url)


async def on_shutdown(bot_instance: Bot):
    await bot_instance.delete_webhook()


# =========================
# ЗАПУСК СЕРВЕРА
# =========================

async def main():
    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(
        app,
        path=WEBHOOK_PATH,
    )

    setup_application(
        app,
        dp,
        bot=bot,
    )

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    logging.info(
        "DanchoStore запущен на порту %s",
        PORT,
    )

    try:
        await asyncio.Event().wait()

    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())