import asyncio
import csv
import html
import ipaddress
import logging
import os
import re
import secrets
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import aiosqlite
from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "270422682").split(",")
    if x.strip()
}
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-5507039744"))

YANDEX_DISK_URL = os.getenv("YANDEX_DISK_URL", "").strip()
TERMS_URL = os.getenv("TERMS_URL", "https://example.com/terms").strip()
PRIVACY_URL = os.getenv("PRIVACY_URL", "https://example.com/privacy").strip()
OFFER_URL = os.getenv("OFFER_URL", "https://example.com/offer").strip()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 8080))
DATA_DIR = os.getenv("DATA_DIR", ".").strip() or "."
DB_PATH = os.getenv("DB_PATH", str(Path(DATA_DIR) / "yoga_bot.sqlite3"))

# If your hosting passes the real client IP via X-Forwarded-For, set true.
# We still verify the payment directly via YooKassa API, so IP filtering is
# a second line of defence, not the only check.
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"

ENERGY_PRICE = 999
INDIVIDUAL_PRICE = 15000

PRODUCTS = {
    "energy": {
        "title": "ЭНЕРГОКОМПЛЕКС",
        "price": ENERGY_PRICE,
        "description": "Энергокомплекс — 5 тренировок по 20–25 минут",
    },
    "individual": {
        "title": "ИНДИВИДУАЛЬНЫЕ ЗАНЯТИЯ",
        "price": INDIVIDUAL_PRICE,
        "description": "8 индивидуальных тренировок в месяц",
    },
}

EMAIL_RE = re.compile(
    r"^(?=.{3,254}$)[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

YOOKASSA_NETWORKS = [
    ipaddress.ip_network("185.71.76.0/27"),
    ipaddress.ip_network("185.71.77.0/27"),
    ipaddress.ip_network("77.75.153.0/25"),
    ipaddress.ip_network("77.75.156.11/32"),
    ipaddress.ip_network("77.75.156.35/32"),
    ipaddress.ip_network("77.75.154.128/25"),
    ipaddress.ip_network("2a02:5180::/32"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("yoga_bot")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")
if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    raise RuntimeError("YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY are missing in .env")
if not PUBLIC_BASE_URL.startswith("https://"):
    raise RuntimeError("PUBLIC_BASE_URL must be a public HTTPS URL")
if not WEBHOOK_SECRET or len(WEBHOOK_SECRET) < 24:
    raise RuntimeError("WEBHOOK_SECRET must be a long random value (24+ chars)")


# ============================================================
# UI TEXTS
# ============================================================

START_TEXT = "Выберите продукт, который хотите приобрести:"

ENERGY_TEXT = """<b>Энергокомплекс</b> — это 5 тренировок по 20–25 минут для бодрого начала дня.

Практики помогут:
— разбудить тело
— наполниться энергией
— улучшить концентрацию
— снять напряжение.

Комплекс подойдет как новичкам, так и тем, кто уже занимается.

Тренировки можно выполнять в любое время, но лучше всего — утром, сразу после пробуждения.

<b>Цена: 999 рублей</b>"""

ENERGY_CARD_TEXT = """📚 Продукт: <b>«ЭНЕРГОКОМПЛЕКС»</b>
♾️ Бессрочный доступ к тренировочным материалам"""

INDIVIDUAL_TEXT = """<b>Индивидуальные занятия</b>

Персональное сопровождение в формате 1:1 для тех, кто хочет выстроить регулярную практику с учетом своих целей, уровня подготовки и особенностей тела.

В программу входят 8 тренировок в месяц продолжительностью 1–1,5 часа. Каждая практика составляется индивидуально: с учетом ваших пожеланий, физической подготовки и задач — будь то развитие силы, гибкости, улучшение осанки, повышение концентрации или общее укрепление тела.

<b>Стоимость — 15 000 ₽ в месяц.</b>"""

INDIVIDUAL_CARD_TEXT = """📚 Продукт: <b>«ИНДИВИДУАЛЬНЫЕ ЗАНЯТИЯ»</b>
8 индивидуальных тренировок в месяц
Стоимость — 15 000 ₽ в месяц."""

ABOUT_TEXT = """Привет! 👋
Меня зовут Вера, я сертифицированный йога-тренер.

Я создаю практики, которые помогают сделать тело сильнее и гибче, а ум — спокойнее.

Канал: https://t.me/yogaloversclub"""

SUPPORT_TEXT = """Если возникли вопросы — напишите мне напрямую: @veranikkiri"""

TRIAL_TEXT = """🎁 <b>Пробная тренировка</b>

Можете написать мне @veranikkiri, и я пришлю ссылку на пробную практику!"""


def legal_text(product_key: str) -> str:
    if product_key == "energy":
        access = "К папке на Яндекс Диск с тренировками"
        period = "Бессрочный"
        price = "999 RUB"
    else:
        access = "После оплаты я с Вами свяжусь для уточнения графика тренировок."
        period = "8 занятий в месяц"
        price = "15 000 RUB"

    return f"""— Период: <b>{period}</b>
— Сумма к оплате: <b>{price}</b>

После оплаты будет предоставлен доступ:
{access}

ℹ️ Совершая покупку, Вы принимаете условия <a href="{html.escape(TERMS_URL)}">Пользовательского соглашения</a>, <a href="{html.escape(PRIVACY_URL)}">Политики конфиденциальности</a> и <a href="{html.escape(OFFER_URL)}">Публичной оферты</a>."""


# ============================================================
# KEYBOARDS
# ============================================================

def persistent_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👤 Обо мне"),
                KeyboardButton(text="💬 Поддержка"),
            ],
            [
                KeyboardButton(text="🎁 Пробная тренировка"),
                KeyboardButton(text="📄 Мои покупки"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел",
    )


def products_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ЭНЕРГОКОМПЛЕКС", callback_data="product:energy")],
            [InlineKeyboardButton(text="ИНДИВИДУАЛЬНЫЕ ЗАНЯТИЯ", callback_data="product:individual")],
        ]
    )


def product_keyboard(product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", callback_data=f"card:{product_key}")],
            [InlineKeyboardButton(text="← Назад", callback_data="menu")],
        ]
    )


def card_keyboard(product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить 💳", callback_data=f"details:{product_key}")],
            [InlineKeyboardButton(text="← Назад", callback_data=f"product:{product_key}")],
        ]
    )


def details_keyboard(product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к оплате", callback_data=f"email:{product_key}")],
            [InlineKeyboardButton(text="← Назад", callback_data=f"card:{product_key}")],
        ]
    )


def payment_keyboard(url: str, product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти в ЮKassa 💳", url=url)],
            [InlineKeyboardButton(text="← Назад", callback_data=f"details:{product_key}")],
        ]
    )


def after_purchase_keyboard(product_key: str) -> InlineKeyboardMarkup:
    rows = []
    if product_key == "energy" and YANDEX_DISK_URL:
        rows.append([InlineKeyboardButton(text="🧘 Открыть тренировки", url=YANDEX_DISK_URL)])
    rows.append([InlineKeyboardButton(text="💬 Написать Вере", url="https://t.me/veranikkiri")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# DATABASE
# ============================================================

CREATE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    yookassa_payment_id TEXT UNIQUE,
    telegram_id INTEGER NOT NULL,
    username TEXT,
    full_name TEXT,
    email TEXT NOT NULL,
    product_key TEXT NOT NULL,
    product_title TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    paid_at TEXT,
    notification_sent INTEGER NOT NULL DEFAULT 0,
    access_sent INTEGER NOT NULL DEFAULT 0,
    receipt_sent INTEGER NOT NULL DEFAULT 0,
    receipt_sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_payments_tg ON payments(telegram_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
"""

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db():
    db_parent = Path(DB_PATH).parent
    db_parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SCHEMA)

        # Lightweight migration for databases created by older bot versions.
        cur = await db.execute("PRAGMA table_info(payments)")
        existing_columns = {row[1] for row in await cur.fetchall()}

        if "receipt_sent" not in existing_columns:
            await db.execute(
                "ALTER TABLE payments ADD COLUMN receipt_sent INTEGER NOT NULL DEFAULT 0"
            )
        if "receipt_sent_at" not in existing_columns:
            await db.execute(
                "ALTER TABLE payments ADD COLUMN receipt_sent_at TEXT"
            )

        await db.commit()


async def upsert_user(message_or_query_user):
    user = message_or_query_user
    now = utcnow_iso()
    full_name = " ".join(x for x in [user.first_name, user.last_name] if x).strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users(telegram_id, username, full_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
              username=excluded.username,
              full_name=excluded.full_name,
              updated_at=excluded.updated_at
            """,
            (user.id, user.username, full_name, now, now),
        )
        await db.commit()


async def insert_pending_payment(
    payment_id: str,
    telegram_id: int,
    username: Optional[str],
    full_name: str,
    email: str,
    product_key: str,
):
    product = PRODUCTS[product_key]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO payments(
                yookassa_payment_id, telegram_id, username, full_name, email,
                product_key, product_title, amount, currency, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RUB', 'pending', ?)
            """,
            (
                payment_id,
                telegram_id,
                username,
                full_name,
                email,
                product_key,
                product["title"],
                product["price"],
                utcnow_iso(),
            ),
        )
        await db.commit()


async def get_payment(payment_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM payments WHERE yookassa_payment_id = ?",
            (payment_id,),
        )
        return await cur.fetchone()


async def mark_paid(payment_id: str) -> bool:
    """Returns True only for the first successful transition to paid."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE payments
            SET status='succeeded', paid_at=?
            WHERE yookassa_payment_id=? AND status!='succeeded'
            """,
            (utcnow_iso(), payment_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def mark_notification_sent(payment_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET notification_sent=1 WHERE yookassa_payment_id=?",
            (payment_id,),
        )
        await db.commit()


async def mark_access_sent(payment_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET access_sent=1 WHERE yookassa_payment_id=?",
            (payment_id,),
        )
        await db.commit()


async def paid_buyers_rows():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT paid_at, telegram_id, full_name, username, email,
                   product_title, amount, currency, yookassa_payment_id
            FROM payments
            WHERE status='succeeded'
            ORDER BY paid_at DESC
            """
        )
        return await cur.fetchall()


async def user_paid_purchases(telegram_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT paid_at, telegram_id, email, product_key, product_title,
                   amount, currency, yookassa_payment_id, receipt_sent
            FROM payments
            WHERE telegram_id=? AND status='succeeded'
            ORDER BY paid_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        )
        return await cur.fetchall()


async def successful_payments_full():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT paid_at, product_key, product_title, amount, currency,
                   yookassa_payment_id, receipt_sent
            FROM payments
            WHERE status='succeeded'
            ORDER BY paid_at DESC
            """
        )
        return await cur.fetchall()


async def mark_receipt_sent(payment_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE payments
            SET receipt_sent=1, receipt_sent_at=?
            WHERE yookassa_payment_id=? AND status='succeeded'
            """,
            (utcnow_iso(), payment_id),
        )
        await db.commit()
        return cur.rowcount > 0


# ============================================================
# RATE LIMIT
# ============================================================

class SimpleRateLimitMiddleware(BaseMiddleware):
    """
    Per-user in-memory limiter.
    Not a replacement for host/CDN-level DDoS protection, but it prevents
    one Telegram user from hammering handlers.
    """

    def __init__(self, min_interval: float = 0.35):
        self.min_interval = min_interval
        self.last_seen = {}
        self.lock = asyncio.Lock()

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        loop = asyncio.get_running_loop()
        now = loop.time()

        async with self.lock:
            prev = self.last_seen.get(user.id, 0.0)
            if now - prev < self.min_interval:
                if isinstance(event, CallbackQuery):
                    with suppress(Exception):
                        await event.answer("Слишком быстро. Попробуйте ещё раз.", show_alert=False)
                return
            self.last_seen[user.id] = now

            # light cleanup
            if len(self.last_seen) > 10000:
                cutoff = now - 3600
                self.last_seen = {k: v for k, v in self.last_seen.items() if v >= cutoff}

        return await handler(event, data)


# ============================================================
# YOOKASSA API
# ============================================================

async def yookassa_request(method: str, path: str, *, json_data=None, idem_key=None):
    url = f"https://api.yookassa.ru/v3{path}"
    auth = aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    headers = {"Accept": "application/json"}
    if idem_key:
        headers["Idempotence-Key"] = idem_key

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(auth=auth, timeout=timeout) as session:
        async with session.request(method, url, json=json_data, headers=headers) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                logger.error("YooKassa API error %s: %s", resp.status, body)
                raise RuntimeError(f"YooKassa API error: HTTP {resp.status}")
            return body


async def create_yookassa_payment(
    telegram_id: int,
    full_name: str,
    username: Optional[str],
    email: str,
    product_key: str,
):
    product = PRODUCTS[product_key]

    # IMPORTANT:
    # We intentionally require and store email before creating a YooKassa payment.
    # That guarantees the user cannot reach the payment URL without giving email.
    #
    # For a self-employed seller, receipt creation may need to be done in "Мой налог".
    # Therefore we don't send a 54-FZ fiscal receipt object here by default.
    metadata = {
        "telegram_id": str(telegram_id),
        "product_key": product_key,
        "email": email,
    }

    payload = {
        "amount": {
            "value": f'{product["price"]:.2f}',
            "currency": "RUB",
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "https://t.me/",
        },
        "description": product["description"][:128],
        "metadata": metadata,
    }

    idem_key = secrets.token_hex(16)
    data = await yookassa_request(
        "POST",
        "/payments",
        json_data=payload,
        idem_key=idem_key,
    )

    payment_id = data["id"]
    confirmation_url = data["confirmation"]["confirmation_url"]

    await insert_pending_payment(
        payment_id=payment_id,
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
        email=email,
        product_key=product_key,
    )
    return payment_id, confirmation_url


async def fetch_yookassa_payment(payment_id: str):
    return await yookassa_request("GET", f"/payments/{payment_id}")


# ============================================================
# BOT
# ============================================================

router = Router()
bot: Optional[Bot] = None
BOT_USERNAME = ""


class Checkout(StatesGroup):
    waiting_email = State()


async def safe_edit(query: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup):
    try:
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        # Telegram may raise "message is not modified". Don't break UX.
        if "message is not modified" not in str(exc).lower():
            raise


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    await upsert_user(message.from_user)

    # persistent keyboard is attached to the first message;
    # the inline product menu is a separate bot message and then edited in-place.
    await message.answer(
        "Yoga Lovers Club",
        reply_markup=persistent_keyboard(),
    )
    await message.answer(
        START_TEXT,
        reply_markup=products_keyboard(),
    )


@router.callback_query(F.data == "menu")
async def menu_handler(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(query, START_TEXT, products_keyboard())
    await query.answer()


@router.callback_query(F.data.startswith("product:"))
async def product_handler(query: CallbackQuery, state: FSMContext):
    await state.clear()
    key = query.data.split(":", 1)[1]
    if key not in PRODUCTS:
        await query.answer("Неизвестный продукт", show_alert=True)
        return

    text = ENERGY_TEXT if key == "energy" else INDIVIDUAL_TEXT
    await safe_edit(query, text, product_keyboard(key))
    await query.answer()


@router.callback_query(F.data.startswith("card:"))
async def card_handler(query: CallbackQuery, state: FSMContext):
    await state.clear()
    key = query.data.split(":", 1)[1]
    if key not in PRODUCTS:
        await query.answer("Неизвестный продукт", show_alert=True)
        return
    text = ENERGY_CARD_TEXT if key == "energy" else INDIVIDUAL_CARD_TEXT
    await safe_edit(query, text, card_keyboard(key))
    await query.answer()


@router.callback_query(F.data.startswith("details:"))
async def details_handler(query: CallbackQuery, state: FSMContext):
    await state.clear()
    key = query.data.split(":", 1)[1]
    if key not in PRODUCTS:
        await query.answer("Неизвестный продукт", show_alert=True)
        return
    await safe_edit(query, legal_text(key), details_keyboard(key))
    await query.answer()


@router.callback_query(F.data.startswith("email:"))
async def email_handler(query: CallbackQuery, state: FSMContext):
    key = query.data.split(":", 1)[1]
    if key not in PRODUCTS:
        await query.answer("Неизвестный продукт", show_alert=True)
        return

    await state.set_state(Checkout.waiting_email)
    await state.update_data(
        product_key=key,
        menu_chat_id=query.message.chat.id,
        menu_message_id=query.message.message_id,
    )

    text = legal_text(key) + (
        "\n\n<b>Перед оплатой укажите свой email.📩</b>\n"
        "Он нужен для идентификации покупки и отправки чека.\n\n"
        "Отправьте email одним сообщением:👇"
    )
    await safe_edit(
        query,
        text,
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="← Назад", callback_data=f"details:{key}")]
            ]
        ),
    )
    await query.answer()


@router.message(Checkout.waiting_email)
async def receive_email(message: Message, state: FSMContext):
    email = (message.text or "").strip().lower()

    if not EMAIL_RE.fullmatch(email):
        await message.answer(
            "Email выглядит некорректно. Отправьте адрес в формате name@example.com.",
            reply_markup=persistent_keyboard(),
        )
        return

    data = await state.get_data()
    key = data.get("product_key")
    menu_chat_id = data.get("menu_chat_id")
    menu_message_id = data.get("menu_message_id")

    if key not in PRODUCTS or not menu_chat_id or not menu_message_id:
        await state.clear()
        await message.answer("Сессия оплаты устарела. Нажмите /start и попробуйте снова.")
        return

    full_name = " ".join(
        x for x in [message.from_user.first_name, message.from_user.last_name] if x
    ).strip()

    try:
        _, payment_url = await create_yookassa_payment(
            telegram_id=message.from_user.id,
            full_name=full_name,
            username=message.from_user.username,
            email=email,
            product_key=key,
        )
    except Exception:
        logger.exception("Failed to create payment")
        await message.answer(
            "Не удалось создать платёж. Попробуйте ещё раз чуть позже или напишите в поддержку: @veranikkiri"
        )
        return

    # Remove the user's email message when Telegram permissions allow it,
    # so the checkout visually remains as one bot message.
    with suppress(Exception):
        await message.delete()

    text = legal_text(key) + (
        f"\n\n📧 Email: <b>{html.escape(email)}</b>\n"
        "Нажмите кнопку ниже — откроется защищённая страница ЮKassa."
    )

    try:
        await bot.edit_message_text(
            chat_id=menu_chat_id,
            message_id=menu_message_id,
            text=text,
            reply_markup=payment_keyboard(payment_url, key),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Failed to edit checkout message")
        await message.answer(
            "Платёж создан:",
            reply_markup=payment_keyboard(payment_url, key),
        )

    await state.clear()


@router.message(F.text == "👤 Обо мне")
async def about_handler(message: Message):
    await message.answer(
        ABOUT_TEXT,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=persistent_keyboard(),
    )


@router.message(F.text == "💬 Поддержка")
async def support_handler(message: Message):
    await message.answer(SUPPORT_TEXT, reply_markup=persistent_keyboard())


@router.message(F.text == "🎁 Пробная тренировка")
async def trial_handler(message: Message):
    await message.answer(
        TRIAL_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=persistent_keyboard(),
    )


@router.message(F.text == "📄 Мои покупки")
async def my_purchases_handler(message: Message):
    rows = await user_paid_purchases(message.from_user.id)

    if not rows:
        await message.answer(
            "У Вас пока нет оплаченных покупок.",
            reply_markup=persistent_keyboard(),
        )
        return

    parts = ["📄 <b>Мои покупки</b>"]
    for index, row in enumerate(rows, start=1):
        paid_time = format_moscow_time(row["paid_at"])
        receipt_status = "✅ отправлен" if row["receipt_sent"] else "⏳ ожидает отправки"

        item = (
            f"\\n<b>{index}. {html.escape(row['product_title'])}</b>\\n"
            f"💰 {row['amount']} {html.escape(row['currency'])}\\n"
            f"🕒 {html.escape(paid_time)}\\n"
            f"🆔 Payment ID: <code>{html.escape(row['yookassa_payment_id'])}</code>\\n"
            f"🧾 Чек: {receipt_status}"
        )

        if row["product_key"] == "energy" and YANDEX_DISK_URL:
            item += f'\\n♾️ <a href="{html.escape(YANDEX_DISK_URL)}">Открыть тренировки</a>'

        parts.append(item)

    parts.append(
        "\\nЕсли возник вопрос по оплате, отправьте в поддержку соответствующий Payment ID."
    )

    await message.answer(
        "\\n".join(parts),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=persistent_keyboard(),
    )


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router.message(Command("buyers"))
async def buyers_handler(message: Message):
    if not is_admin(message.from_user.id):
        return

    rows = await paid_buyers_rows()
    if not rows:
        await message.answer("Пока нет успешных покупок.")
        return

    export_dir = Path("exports")
    export_dir.mkdir(exist_ok=True)
    filepath = export_dir / f"buyers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    with filepath.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            [
                "Дата оплаты UTC",
                "Telegram ID",
                "Имя",
                "Username",
                "Email",
                "Продукт",
                "Сумма",
                "Валюта",
                "YooKassa payment ID",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["paid_at"],
                    row["telegram_id"],
                    row["full_name"],
                    row["username"] or "",
                    row["email"],
                    row["product_title"],
                    row["amount"],
                    row["currency"],
                    row["yookassa_payment_id"],
                ]
            )

    total = sum(int(row["amount"]) for row in rows)
    caption = f"Покупателей/оплат: {len(rows)}\nСумма успешных оплат: {total:,} ₽".replace(",", " ")
    await message.answer_document(FSInputFile(filepath), caption=caption)

    with suppress(Exception):
        filepath.unlink()


@router.message(Command("stats"))
async def stats_handler(message: Message):
    if not is_admin(message.from_user.id):
        return

    from zoneinfo import ZoneInfo
    now_msk = datetime.now(ZoneInfo("Europe/Moscow"))
    rows = await successful_payments_full()

    today_rows = []
    month_rows = []
    product_counts = {}

    for row in rows:
        if not row["paid_at"]:
            continue
        try:
            paid_dt = datetime.fromisoformat(row["paid_at"]).astimezone(
                ZoneInfo("Europe/Moscow")
            )
        except Exception:
            continue

        if paid_dt.date() == now_msk.date():
            today_rows.append(row)

        if paid_dt.year == now_msk.year and paid_dt.month == now_msk.month:
            month_rows.append(row)
            product_counts[row["product_title"]] = (
                product_counts.get(row["product_title"], 0) + 1
            )

    today_sum = sum(int(r["amount"]) for r in today_rows)
    month_sum = sum(int(r["amount"]) for r in month_rows)
    all_sum = sum(int(r["amount"]) for r in rows)
    pending_receipts = sum(1 for r in rows if not r["receipt_sent"])

    breakdown = "\\n".join(
        f"• {html.escape(title)} — {count}"
        for title, count in sorted(product_counts.items())
    ) or "• продаж пока нет"

    text = f"""📊 <b>Статистика</b>

<b>Сегодня</b>
Продаж: {len(today_rows)}
Сумма: {today_sum:,} ₽

<b>Текущий месяц</b>
Продаж: {len(month_rows)}
Сумма: {month_sum:,} ₽

{breakdown}

<b>За всё время</b>
Продаж: {len(rows)}
Сумма: {all_sum:,} ₽

🧾 Чеков ожидают отметки об отправке: {pending_receipts}""".replace(",", " ")

    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(Command("receipt"))
async def receipt_handler(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer(
            "Использование:\\n<code>/receipt PAYMENT_ID</code>\\n\\n"
            "Команда отмечает чек как отправленный и уведомляет покупателя в Telegram.",
            parse_mode=ParseMode.HTML,
        )
        return

    payment_id = parts[1].strip()
    payment_row = await get_payment(payment_id)

    if not payment_row or payment_row["status"] != "succeeded":
        await message.answer("Успешный платёж с таким Payment ID не найден.")
        return

    if payment_row["receipt_sent"]:
        await message.answer("Для этого платежа чек уже отмечен как отправленный.")
        return

    updated = await mark_receipt_sent(payment_id)
    if not updated:
        await message.answer("Не удалось обновить запись платежа.")
        return

    try:
        await bot.send_message(
            payment_row["telegram_id"],
            f"""🧾 <b>Чек отправлен</b>

Чек по Вашей покупке отправлен на электронную почту:
<b>{html.escape(payment_row["email"])}</b>

🆔 Payment ID:
<code>{html.escape(payment_id)}</code>

Если письмо не пришло, проверьте папку «Спам» или напишите @veranikkiri.""",
            parse_mode=ParseMode.HTML,
            reply_markup=persistent_keyboard(),
        )
    except Exception:
        logger.exception("Could not notify customer that receipt was sent: %s", payment_id)
        await message.answer(
            "Чек отмечен как отправленный, но Telegram-уведомление покупателю доставить не удалось."
        )
        return

    await message.answer(
        f"✅ Чек отмечен как отправленный.\\n"
        f"Покупатель уведомлён.\\n"
        f"Payment ID: <code>{html.escape(payment_id)}</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message()
async def fallback_handler(message: Message):
    # Avoid noisy responses to arbitrary spam while preserving navigation.
    if message.text and message.text.startswith("/"):
        return


# ============================================================
# PAYMENT SUCCESS
# ============================================================

def format_moscow_time(iso_value: Optional[str]) -> str:
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso_value) if iso_value else datetime.now(timezone.utc)
        return dt.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S МСК")
    except Exception:
        return iso_value or utcnow_iso()


async def notify_success(payment_row):
    username = f"@{payment_row['username']}" if payment_row["username"] else "нет"
    name = payment_row["full_name"] or "не указано"
    paid_time = format_moscow_time(payment_row["paid_at"])

    admin_text = f"""✅ <b>НОВАЯ ОПЛАТА</b>

📚 Продукт: <b>{html.escape(payment_row["product_title"])}</b>
💰 Сумма: <b>{payment_row["amount"]} RUB</b>
🕒 Время: {html.escape(paid_time)}

👤 Имя: {html.escape(name)}
🔗 Username: {html.escape(username)}
🆔 Telegram ID: <code>{payment_row["telegram_id"]}</code>
📧 Email: <b>{html.escape(payment_row["email"])}</b>
💳 Payment ID: <code>{html.escape(payment_row["yookassa_payment_id"])}</code>

🧾❗ <b>ВАЖНО: СФОРМИРОВАТЬ ЧЕК В «МОЙ НАЛОГ» И ОБЯЗАТЕЛЬНО ОТПРАВИТЬ ЕГО ПОКУПАТЕЛЮ.</b>\n\nПосле отправки отметьте это командой:\n<code>/receipt {html.escape(payment_row["yookassa_payment_id"])}</code>"""

    destinations = set(ADMIN_IDS)
    destinations.add(ADMIN_GROUP_ID)

    for chat_id in destinations:
        try:
            await bot.send_message(chat_id, admin_text, parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Could not notify admin destination %s", chat_id)

    await mark_notification_sent(payment_row["yookassa_payment_id"])


async def send_customer_success(payment_row):
    tg_id = payment_row["telegram_id"]
    product_key = payment_row["product_key"]
    payment_id = html.escape(payment_row["yookassa_payment_id"])

    if product_key == "energy":
        if YANDEX_DISK_URL:
            text = f"""✅ <b>Платёж принят!</b>

🆔 <b>Payment ID:</b>
<code>{payment_id}</code>

Спасибо за покупку энергокомплекса!

📧 Чек будет отправлен Вам на указанную электронную почту в ближайшее время.

♾️ <b>Доступ к тренировкам:</b>
{html.escape(YANDEX_DISK_URL)}

Если возникнут вопросы, укажите Payment ID — так я смогу быстрее найти Ваш платёж."""
        else:
            text = f"""✅ <b>Платёж принят!</b>

🆔 <b>Payment ID:</b>
<code>{payment_id}</code>

Спасибо за покупку энергокомплекса!

📧 Чек будет отправлен Вам на указанную электронную почту в ближайшее время.

Ссылка на материалы временно не настроена. Напишите @veranikkiri, и я отправлю доступ вручную."""
    else:
        text = f"""✅ <b>Платёж принят!</b>

🆔 <b>Payment ID:</b>
<code>{payment_id}</code>

📧 Чек будет отправлен Вам на указанную электронную почту в ближайшее время.

В ближайшее время я с Вами свяжусь для согласования графика индивидуальных тренировок.

Если возникнут вопросы, укажите Payment ID — так я смогу быстрее найти Ваш платёж."""

    try:
        await bot.send_message(
            tg_id,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=after_purchase_keyboard(product_key),
        )
        await mark_access_sent(payment_row["yookassa_payment_id"])
    except Exception:
        logger.exception(
            "Could not send customer success for payment %s",
            payment_row["yookassa_payment_id"],
        )


# ============================================================
# WEBHOOK SERVER
# ============================================================

def request_ip(request: web.Request) -> Optional[str]:
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()

    peer = request.transport.get_extra_info("peername")
    if peer and peer[0]:
        return str(peer[0])
    return None


def is_yookassa_ip(ip_str: Optional[str]) -> bool:
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in network for network in YOOKASSA_NETWORKS)


async def health_handler(request: web.Request):
    return web.json_response({"ok": True})


async def yookassa_webhook(request: web.Request):
    # Secret URL prevents random scanning from hitting the business handler.
    # YooKassa's own authenticity recommendation is also followed below:
    # fetch payment from YooKassa API and verify status/amount/metadata.
    source_ip = request_ip(request)
    if source_ip and not is_yookassa_ip(source_ip):
        logger.warning("Webhook request from non-YooKassa IP: %s", source_ip)
        # Don't reject solely on IP because some hosts put a reverse proxy in front.
        # API verification below is authoritative.

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")

    if data.get("event") != "payment.succeeded":
        return web.Response(status=200, text="ignored")

    obj = data.get("object") or {}
    payment_id = str(obj.get("id", "")).strip()
    if not payment_id:
        return web.Response(status=400, text="missing payment id")

    local = await get_payment(payment_id)
    if not local:
        logger.warning("Webhook for unknown payment %s", payment_id)
        return web.Response(status=200, text="unknown")

    # CRITICAL: never trust the webhook body as proof of payment.
    # Query YooKassa API directly.
    try:
        remote = await fetch_yookassa_payment(payment_id)
    except Exception:
        logger.exception("Unable to verify payment %s", payment_id)
        # Non-200 makes YooKassa retry.
        return web.Response(status=503, text="verification failed")

    if remote.get("status") != "succeeded" or not remote.get("paid", False):
        logger.warning("Payment %s is not actually succeeded", payment_id)
        return web.Response(status=200, text="not succeeded")

    expected_amount = f'{int(local["amount"]):.2f}'
    actual_amount = str((remote.get("amount") or {}).get("value", ""))
    actual_currency = str((remote.get("amount") or {}).get("currency", ""))

    metadata = remote.get("metadata") or {}
    if (
        actual_amount != expected_amount
        or actual_currency != "RUB"
        or str(metadata.get("telegram_id")) != str(local["telegram_id"])
        or str(metadata.get("product_key")) != str(local["product_key"])
    ):
        logger.error("Payment verification mismatch for %s", payment_id)
        return web.Response(status=200, text="mismatch")

    first_transition = await mark_paid(payment_id)
    payment_row = await get_payment(payment_id)

    # Idempotent business actions. YooKassa can resend notifications.
    if payment_row and not payment_row["notification_sent"]:
        await notify_success(payment_row)
        payment_row = await get_payment(payment_id)

    if payment_row and not payment_row["access_sent"]:
        await send_customer_success(payment_row)

    return web.Response(status=200, text="ok")


async def start_http_server():
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/health", health_handler)
    app.router.add_post(f"/yookassa/{WEBHOOK_SECRET}", yookassa_webhook)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()

    webhook_url = f"{PUBLIC_BASE_URL}/yookassa/{WEBHOOK_SECRET}"
    logger.info("HTTP server listening on %s:%s", HOST, PORT)
    logger.info("YooKassa webhook URL: %s", webhook_url)
    return runner


async def main():
    global bot, BOT_USERNAME

    await init_db()

    bot = Bot(BOT_TOKEN)
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""

    dp = Dispatcher()
    dp.include_router(router)
    dp.message.middleware(SimpleRateLimitMiddleware())
    dp.callback_query.middleware(SimpleRateLimitMiddleware())

    runner = await start_http_server()

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())