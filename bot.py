# -*- coding: utf-8 -*-
"""DanchoStore Telegram shop bot. Python 3.11+, aiogram 3.22."""

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
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


# ------------------------- НАСТРОЙКИ -------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
PORT = int(os.getenv("PORT", "10000"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "store.db")
SELLER_USERNAME = os.getenv("SELLER_USERNAME", "LdaniilV").lstrip("@")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/danchostore")
WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip() or None

if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN.")
if ADMIN_ID <= 0:
    raise RuntimeError("Не задана корректная переменная окружения ADMIN_ID.")
if not HOSTNAME:
    raise RuntimeError("Render не передал RENDER_EXTERNAL_HOSTNAME.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("danchostore")

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())


# ------------------------- СОСТОЯНИЯ -------------------------

class AddProduct(StatesGroup):
    category = State()
    title = State()
    price = State()
    description = State()
    link = State()
    photos = State()


class EditProduct(StatesGroup):
    product_id = State()
    field = State()
    value = State()


class DeleteProduct(StatesGroup):
    product_id = State()


class Buyback(StatesGroup):
    model = State()
    memory = State()
    condition = State()
    battery = State()
    price = State()
    contact = State()


# ------------------------- КЛАВИАТУРЫ -------------------------

def reply_keyboard(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=item) for item in row] for row in rows],
        resize_keyboard=True,
    )


def main_keyboard():
    return reply_keyboard([
        ["🍎 iPhone"],
        ["🤖 Android"],
        ["🔄 Выкуп iPhone"],
        ["💬 Связаться с продавцом"],
    ])


def admin_keyboard():
    return reply_keyboard([
        ["➕ Добавить товар"],
        ["📋 Список товаров"],
        ["✏️ Редактировать товар"],
        ["🗑 Удалить товар"],
        ["🏠 В главное меню"],
    ])


def cancel_keyboard():
    return reply_keyboard([["❌ Отмена"]])


def category_keyboard():
    return reply_keyboard([["🍎 iPhone"], ["🤖 Android"], ["❌ Отмена"]])


def photos_keyboard():
    return reply_keyboard([
        ["✅ Готово с фото"],
        ["⏭ Пропустить фото"],
        ["❌ Отмена"],
    ])


def edit_fields_keyboard():
    return reply_keyboard([
        ["Название", "Цена"],
        ["Описание", "Ссылка"],
        ["❌ Отмена"],
    ])


def product_keyboard(product):
    text = f"Здравствуйте! Хочу купить товар №{product['id']}: {product['title']}."
    seller_url = f"https://t.me/{SELLER_USERNAME}?text={quote(text)}"
    rows = [[InlineKeyboardButton(text="Купить", url=seller_url)]]
    if product.get("link"):
        rows.append([InlineKeyboardButton(text="Пост в Telegram", url=product["link"])])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_admin_keyboard(product_id, is_sold):
    status = "↩️ Вернуть в наличие" if is_sold else "✅ Отметить проданным"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=status, callback_data=f"toggle:{product_id}")],
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{product_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{product_id}"),
        ],
    ])


# ------------------------- БАЗА ДАННЫХ -------------------------

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                photo_file_id TEXT NOT NULL DEFAULT '',
                photo_file_ids TEXT NOT NULL DEFAULT '[]',
                is_sold INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()


async def get_products(category=None, include_sold=False):
    query = "SELECT * FROM products"
    conditions, params = [], []
    if category:
        conditions.append("category = ?")
        params.append(category)
    if not include_sold:
        conditions.append("is_sold = 0")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC"
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]


async def get_product(product_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def send_product(message: Message, product):
    try:
        photo_ids = json.loads(product.get("photo_file_ids") or "[]")
    except (TypeError, json.JSONDecodeError):
        photo_ids = []
    if not photo_ids and product.get("photo_file_id"):
        photo_ids = [product["photo_file_id"]]

    sold_line = "\n\n<b>Статус:</b> Продан" if product["is_sold"] else ""
    caption = (
        f"<b>{product['title']}</b>\n"
        f"<b>Цена:</b> {product['price']} ₽\n\n"
        f"{product['description']}{sold_line}"
    )
    keyboard = None if product["is_sold"] else product_keyboard(product)

    if photo_ids:
        await message.answer_photo(photo_ids[0], caption=caption, reply_markup=keyboard)
        for photo_id in photo_ids[1:]:
            await message.answer_photo(photo_id)
    else:
        await message.answer(caption, reply_markup=keyboard)


async def show_category(message: Message, category):
    products = await get_products(category=category)
    if not products:
        await message.answer(
            "Пока товаров в этом разделе нет.",
            reply_markup=main_keyboard(),
        )
        return
    await message.answer(f"<b>{category}</b>\nАктуальные предложения:")
    for product in products:
        await send_product(message, product)


# ------------------------- ГЛАВНОЕ МЕНЮ -------------------------

@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Добро пожаловать в DanchoStore. Выбирай своё устройство",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("admin"))
async def admin(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer("Панель администратора DanchoStore.", reply_markup=admin_keyboard())


@dp.message(F.text == "🏠 В главное меню")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню.", reply_markup=main_keyboard())


@dp.message(StateFilter(None), F.text == "🍎 iPhone")
async def iphone(message: Message):
    await show_category(message, "iPhone")


@dp.message(StateFilter(None), F.text == "🤖 Android")
async def android(message: Message):
    await show_category(message, "Android")


@dp.message(F.text == "💬 Связаться с продавцом")
async def contact_seller(message: Message):
    await message.answer(
        f"Связаться с продавцом: https://t.me/{SELLER_USERNAME}",
        reply_markup=main_keyboard(),
    )


# ------------------------- ЗАЯВКА НА ВЫКУП -------------------------

@dp.message(F.text == "🔄 Выкуп iPhone")
async def buyback_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Buyback.model)
    await message.answer("Какую модель iPhone хотите продать?", reply_markup=cancel_keyboard())


@dp.message(Buyback.model, F.text == "❌ Отмена")
@dp.message(Buyback.memory, F.text == "❌ Отмена")
@dp.message(Buyback.condition, F.text == "❌ Отмена")
@dp.message(Buyback.battery, F.text == "❌ Отмена")
@dp.message(Buyback.price, F.text == "❌ Отмена")
@dp.message(Buyback.contact, F.text == "❌ Отмена")
async def cancel_buyback(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Заявка отменена.", reply_markup=main_keyboard())


@dp.message(Buyback.model)
async def buyback_model(message: Message, state: FSMContext):
    await state.update_data(model=(message.text or "").strip())
    await state.set_state(Buyback.memory)
    await message.answer("Укажите объём памяти.")


@dp.message(Buyback.memory)
async def buyback_memory(message: Message, state: FSMContext):
    await state.update_data(memory=(message.text or "").strip())
    await state.set_state(Buyback.condition)
    await message.answer("Опишите состояние телефона.")


@dp.message(Buyback.condition)
async def buyback_condition(message: Message, state: FSMContext):
    await state.update_data(condition=(message.text or "").strip())
    await state.set_state(Buyback.battery)
    await message.answer("Укажите состояние аккумулятора в процентах.")


@dp.message(Buyback.battery)
async def buyback_battery(message: Message, state: FSMContext):
    await state.update_data(battery=(message.text or "").strip())
    await state.set_state(Buyback.price)
    await message.answer("За какую сумму хотите продать телефон? (₽)")


@dp.message(Buyback.price)
async def buyback_price(message: Message, state: FSMContext):
    await state.update_data(price=(message.text or "").strip())
    await state.set_state(Buyback.contact)
    await message.answer("Оставьте Telegram для связи или другой удобный контакт.")


@dp.message(Buyback.contact)
async def buyback_contact(message: Message, state: FSMContext):
    await state.update_data(contact=(message.text or "").strip())
    data = await state.get_data()
    report = (
        "<b>Новая заявка на выкуп iPhone</b>\n\n"
        f"<b>Модель:</b> {data['model']}\n"
        f"<b>Память:</b> {data['memory']}\n"
        f"<b>Состояние:</b> {data['condition']}\n"
        f"<b>Аккумулятор:</b> {data['battery']}\n"
        f"<b>Желаемая цена:</b> {data['price']} ₽\n"
        f"<b>Контакт:</b> {data['contact']}\n"
        f"<b>Telegram:</b> @{message.from_user.username or 'не указан'}"
    )
    try:
        await bot.send_message(ADMIN_ID, report)
        await message.answer("✅ Заявка отправлена продавцу. С вами свяжутся.", reply_markup=main_keyboard())
    except Exception:
        logger.exception("Не удалось отправить заявку администратору")
        await message.answer("Не удалось отправить заявку. Попробуйте позже или напишите продавцу.", reply_markup=main_keyboard())
    finally:
        await state.clear()


# ------------------------- ДОБАВЛЕНИЕ ТОВАРА -------------------------

@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await state.set_state(AddProduct.category)
    await message.answer("Выберите категорию товара.", reply_markup=category_keyboard())


@dp.message(AddProduct.category, F.text == "❌ Отмена")
@dp.message(AddProduct.title, F.text == "❌ Отмена")
@dp.message(AddProduct.price, F.text == "❌ Отмена")
@dp.message(AddProduct.description, F.text == "❌ Отмена")
@dp.message(AddProduct.link, F.text == "❌ Отмена")
@dp.message(AddProduct.photos, F.text == "❌ Отмена")
async def cancel_add(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Добавление товара отменено.", reply_markup=admin_keyboard())


@dp.message(AddProduct.category)
async def add_product_category(message: Message, state: FSMContext):
    if message.text not in ("🍎 iPhone", "🤖 Android"):
        await message.answer("Выберите категорию кнопкой.")
        return
    category = "iPhone" if message.text == "🍎 iPhone" else "Android"
    await state.update_data(category=category)
    await state.set_state(AddProduct.title)
    await message.answer("Введите название, модель и объём памяти.", reply_markup=cancel_keyboard())


@dp.message(AddProduct.title)
async def add_product_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название не должно быть пустым.")
        return
    await state.update_data(title=title)
    await state.set_state(AddProduct.price)
    await message.answer("Введите цену в рублях.")


@dp.message(AddProduct.price)
async def add_product_price(message: Message, state: FSMContext):
    price = (message.text or "").strip().replace(" ", "").replace(",", ".")
    if not price.replace(".", "", 1).isdigit():
        await message.answer("Введите цену числом, например 45000.")
        return
    await state.update_data(price=price)
    await state.set_state(AddProduct.description)
    await message.answer("Введите состояние и описание товара.")


@dp.message(AddProduct.description)
async def add_product_description(message: Message, state: FSMContext):
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(AddProduct.link)
    await message.answer("Отправьте ссылку на пост товара в Telegram или напишите «Нет».")


@dp.message(AddProduct.link)
async def add_product_link(message: Message, state: FSMContext):
    link = (message.text or "").strip()
    if link.lower() in ("нет", "no", "-"):
        link = ""
    elif link and not link.startswith("https://t.me/"):
        await message.answer("Нужна ссылка вида https://t.me/... или напишите «Нет».")
        return
    await state.update_data(link=link, photos=[])
    await state.set_state(AddProduct.photos)
    await message.answer(
        "Отправьте до 5 фотографий по одной. Когда закончите, нажмите «Готово с фото».",
        reply_markup=photos_keyboard(),
    )


@dp.message(AddProduct.photos, F.photo)
async def add_product_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) >= 5:
        await message.answer("Можно добавить не больше 5 фотографий.")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(f"Фото добавлено: {len(photos)}/5.")


async def save_new_product(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO products
            (category, title, price, description, link, photo_file_id, photo_file_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                data["category"], data["title"], data["price"],
                data["description"], data.get("link", ""),
                photos[0] if photos else "",
                json.dumps(photos, ensure_ascii=False),
            ),
        )
        await db.commit()
    await state.clear()
    await message.answer("✅ Товар успешно добавлен.", reply_markup=admin_keyboard())


@dp.message(AddProduct.photos, F.text.in_(["✅ Готово с фото", "⏭ Пропустить фото"]))
async def finish_product_photos(message: Message, state: FSMContext):
    await save_new_product(message, state)


# ------------------------- СПИСОК ТОВАРОВ -------------------------

@dp.message(F.text == "📋 Список товаров")
async def admin_products(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    products = await get_products(include_sold=True)
    if not products:
        await message.answer("Товаров пока нет.", reply_markup=admin_keyboard())
        return
    await message.answer(f"Всего товаров: {len(products)}", reply_markup=admin_keyboard())
    for product in products[:50]:
        status = "Продан" if product["is_sold"] else "В наличии"
        text = (
            f"<b>№{product['id']} — {product['title']}</b>\n"
            f"Категория: {product['category']}\n"
            f"Цена: {product['price']} ₽\n"
            f"Статус: {status}"
        )
        await message.answer(
            text,
            reply_markup=product_admin_keyboard(product["id"], product["is_sold"]),
        )


# ------------------------- РЕДАКТИРОВАНИЕ -------------------------

@dp.message(F.text == "✏️ Редактировать товар")
async def edit_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await state.set_state(EditProduct.product_id)
    await message.answer("Введите номер товара для редактирования.", reply_markup=cancel_keyboard())


@dp.message(EditProduct.product_id, F.text == "❌ Отмена")
@dp.message(EditProduct.field, F.text == "❌ Отмена")
@dp.message(EditProduct.value, F.text == "❌ Отмена")
async def cancel_edit(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Редактирование отменено.", reply_markup=admin_keyboard())


@dp.message(EditProduct.product_id)
async def edit_choose_id(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        await message.answer("Введите номер товара цифрами.")
        return
    product_id = int(message.text)
    if not await get_product(product_id):
        await message.answer("Товар не найден.")
        return
    await state.update_data(product_id=product_id)
    await state.set_state(EditProduct.field)
    await message.answer("Что изменить?", reply_markup=edit_fields_keyboard())


@dp.message(EditProduct.field)
async def edit_choose_field(message: Message, state: FSMContext):
    fields = {"Название": "title", "Цена": "price", "Описание": "description", "Ссылка": "link"}
    if message.text not in fields:
        await message.answer("Выберите поле кнопкой.")
        return
    await state.update_data(field=fields[message.text])
    await state.set_state(EditProduct.value)
    await message.answer("Введите новое значение.", reply_markup=cancel_keyboard())


@dp.message(EditProduct.value)
async def edit_save(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data["field"]
    value = (message.text or "").strip()
    if not value:
        await message.answer("Значение не должно быть пустым.")
        return
    if field == "price":
        value = value.replace(" ", "").replace(",", ".")
        if not value.replace(".", "", 1).isdigit():
            await message.answer("Введите цену числом.")
            return
    if field == "link" and value and not value.startswith("https://t.me/"):
        await message.answer("Ссылка должна начинаться с https://t.me/")
        return
    if field not in {"title", "price", "description", "link"}:
        await state.clear()
        await message.answer("Недопустимое поле.", reply_markup=admin_keyboard())
        return
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"UPDATE products SET {field} = ? WHERE id = ?",
            (value, data["product_id"]),
        )
        await db.commit()
    await state.clear()
    await message.answer("✅ Товар обновлён.", reply_markup=admin_keyboard())


@dp.callback_query(F.data.startswith("edit:"))
async def edit_from_card(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split(":")[1])
    if not await get_product(product_id):
        await callback.answer("Товар не найден.")
        return
    await state.clear()
    await state.update_data(product_id=product_id)
    await state.set_state(EditProduct.field)
    await callback.message.answer("Что изменить?", reply_markup=edit_fields_keyboard())
    await callback.answer()


# ------------------------- УДАЛЕНИЕ -------------------------

@dp.message(F.text == "🗑 Удалить товар")
async def delete_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await state.set_state(DeleteProduct.product_id)
    await message.answer("Введите номер товара для удаления.", reply_markup=cancel_keyboard())


@dp.message(DeleteProduct.product_id, F.text == "❌ Отмена")
async def cancel_delete(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Удаление отменено.", reply_markup=admin_keyboard())


@dp.message(DeleteProduct.product_id)
async def delete_by_id(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        await message.answer("Введите номер товара цифрами.")
        return
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("DELETE FROM products WHERE id = ?", (int(message.text),))
        await db.commit()
        deleted = cursor.rowcount
    await state.clear()
    await message.answer(
        "🗑 Товар удалён." if deleted else "Товар с таким номером не найден.",
        reply_markup=admin_keyboard(),
    )


@dp.callback_query(F.data.startswith("delete:"))
async def delete_from_card(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split(":")[1])
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        await db.commit()
    try:
        await callback.message.edit_text("🗑 Товар удалён.")
    except Exception:
        await callback.message.answer("🗑 Товар удалён.")
    await callback.answer("Удалено.")


# ------------------------- СТАТУС ТОВАРА -------------------------

@dp.callback_query(F.data.startswith("toggle:"))
async def toggle_product(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
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
        reply_markup=product_admin_keyboard(product_id, new_status)
    )
    await callback.answer("Статус обновлён.")


# ------------------------- НЕИЗВЕСТНЫЕ СООБЩЕНИЯ -------------------------

@dp.message()
async def fallback(message: Message):
    if message.text and message.text.startswith("/"):
        await message.answer("Неизвестная команда. Отправьте /start.")
        return
    if message.from_user and message.from_user.id == ADMIN_ID:
        await message.answer(
            "Для управления магазином используйте /admin.",
            reply_markup=admin_keyboard(),
        )
    else:
        await message.answer(
            "Пожалуйста, выберите раздел в меню.",
            reply_markup=main_keyboard(),
        )


# ------------------------- WEBHOOK / ЗАПУСК -------------------------

async def health(request: web.Request):
    return web.Response(text="DanchoStore bot is running")


async def on_startup():
    await init_db()
    webhook_url = f"https://{HOSTNAME}{WEBHOOK_PATH}"
    await bot.set_webhook(
        webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info("Webhook установлен: %s", webhook_url)


async def on_shutdown():
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

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("DanchoStore запущен на порту %s", PORT)

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())