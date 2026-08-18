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
import asyncpg
from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
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
PRIVACY_URL = os.getenv("PRIVACY_URL", "https://example.com/privacy").strip()
OFFER_URL = os.getenv("OFFER_URL", "https://example.com/offer").strip()
CONSENT_URL = os.getenv("CONSENT_URL", "https://example.com/consent").strip()
MARKETING_URL = os.getenv("MARKETING_URL", "https://example.com/marketing").strip()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 8080))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

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
        "description": "4 индивидуальных тренировок в месяц",
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
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing in .env")


# ============================================================
# UI TEXTS
# ============================================================

START_TEXT = "Выберите продукт, который хотите приобрести:"

ENERGY_TEXT = """🌿 <b>ЭНЕРГОКОМПЛЕКС</b>

В один день хочется больше движения и динамики. В другой — задержаться в асанах, почувствовать работу тела и никуда не торопиться.
Поэтому я специально сделала практики разными:

⚡ Динамичные — плавно двигаемся из асаны в асану практически без остановок.

🌿 Сбалансированные — задерживаемся примерно на одно дыхание и сохраняем ритм практики.

🧘‍♀️ Более статичные — остаёмся в асанах до пяти дыханий, чтобы было время отстроиться, почувствовать тело и удерживать внимание.

Ты не подстраиваешь себя под одну тренировку — ты выбираешь практику под сегодняшний день.

Все занятия полноценные: настройка → практика → шавасана.

<b>Внутри: 5 тренировок по 20–25 минут</b>
<b>Доступ: навсегда</b>
<b>Стоимость: 999 ₽</b>"""

ENERGY_CARD_TEXT = """📚 Продукт: <b>«ЭНЕРГОКОМПЛЕКС»</b>
♾️ Бессрочный доступ к тренировочным материалам"""

INDIVIDUAL_TEXT = """<b>Индивидуальные занятия</b>

Персональное сопровождение в формате 1:1 для тех, кто хочет выстроить регулярную практику с учетом своих целей, уровня подготовки и особенностей тела.

В программу входят 4 тренировки в месяц продолжительностью 1–1,5 часа. Каждая практика составляется индивидуально: с учетом ваших пожеланий, физической подготовки и задач — будь то развитие силы, гибкости, улучшение осанки, повышение концентрации или общее укрепление тела.

<b>Стоимость — 15 000 ₽ в месяц.</b>"""

INDIVIDUAL_CARD_TEXT = """📚 Продукт: <b>«ИНДИВИДУАЛЬНЫЕ ЗАНЯТИЯ»</b>
4 индивидуальных тренировок в месяц
Стоимость — 15 000 ₽ в месяц."""

ABOUT_TEXT = """Привет! 👋
Меня зовут Вера, я сертифицированный йога-тренер.

Я создаю практики, которые помогают сделать тело сильнее и гибче, а ум — спокойнее.

Канал: https://t.me/yogaloversclub"""

SUPPORT_TEXT = """Если возникли вопросы — напишите мне напрямую: @veranikkiri"""

TRIAL_TEXT = """🎁 <b>Пробная тренировка 🎁</b>

ПОДПИШИСЬ, чтобы получить практику👇:
https://t.me/yogaloversclub/603"""


def legal_text(product_key: str) -> str:
    if product_key == "energy":
        product_title = "ЭНЕРГОКОМПЛЕКС"
        access = "К папке на Яндекс Диск с тренировками"
        period = "Бессрочный"
        price = "999 RUB"
    else:
        product_title = "ИНДИВИДУАЛЬНЫЕ ЗАНЯТИЯ"
        access = "После оплаты я с Вами свяжусь для уточнения графика тренировок."
        period = "4 занятий в месяц"
        price = "15 000 RUB"

    return f"""📚 Продукт: <b>«{product_title}»</b>

— Период: <b>{period}</b>
— Сумма к оплате: <b>{price}</b>

После оплаты будет предоставлен доступ:
{access}

ℹ️ Нажимая <b>«✅ Согласен и продолжить»</b>, Вы подтверждаете, что ознакомились и принимаете условия <a href="{html.escape(OFFER_URL)}">Публичной оферты</a>, ознакомились с <a href="{html.escape(PRIVACY_URL)}">Политикой конфиденциальности</a> и даёте <a href="{html.escape(CONSENT_URL)}">Согласие на обработку персональных данных</a>.

<a href="{html.escape(MARKETING_URL)}">Согласие на получение рекламных и информационных сообщений</a> является добровольным."""


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
            [InlineKeyboardButton(text="💳 Оплатить", callback_data=f"details:{product_key}")],
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
            [
                InlineKeyboardButton(
                    text="✅ Согласен и продолжить",
                    callback_data=f"marketing_yes:{product_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➡️ Продолжить без рассылки",
                    callback_data=f"marketing_no:{product_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=f"product:{product_key}",
                )
            ],
        ]
    )


def payment_keyboard(url: str, product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к оплате 💳", url=url)],
            [InlineKeyboardButton(text="← Назад", callback_data=f"product:{product_key}")],
        ]
    )


def after_purchase_keyboard(product_key: str) -> InlineKeyboardMarkup:
    rows = []
    if product_key == "energy" and YANDEX_DISK_URL:
        rows.append([InlineKeyboardButton(text="🧘 Открыть тренировки", url=YANDEX_DISK_URL)])
    rows.append([InlineKeyboardButton(text="💬 Написать Вере", url="https://t.me/veranikkiri")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# DATABASE (PostgreSQL)
# ============================================================

CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    service_message_id BIGINT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    marketing_consent INTEGER,
    marketing_consent_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    yookassa_payment_id TEXT UNIQUE,
    telegram_id BIGINT NOT NULL,
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
    receipt_sent_at TEXT,
    marketing_consent INTEGER,
    marketing_consent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_payments_tg ON payments(telegram_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
"""

db_pool: Optional[asyncpg.Pool] = None

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with db_pool.acquire() as conn:
        await conn.execute(CREATE_SCHEMA)

async def upsert_user(message_or_query_user):
    user = message_or_query_user
    now = utcnow_iso()
    full_name = " ".join(x for x in [user.first_name, user.last_name] if x).strip()
    await db_pool.execute(
        """INSERT INTO users(telegram_id, username, full_name, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT(telegram_id) DO UPDATE SET
          username=EXCLUDED.username, full_name=EXCLUDED.full_name, updated_at=EXCLUDED.updated_at""",
        user.id, user.username, full_name, now, now,
    )

async def get_service_message_id(telegram_id: int):
    return await db_pool.fetchval(
        "SELECT service_message_id FROM users WHERE telegram_id=$1", telegram_id
    )

async def set_service_message_id(telegram_id: int, message_id: int):
    await db_pool.execute(
        "UPDATE users SET service_message_id=$1, updated_at=$2 WHERE telegram_id=$3",
        message_id, utcnow_iso(), telegram_id,
    )

async def set_marketing_consent(telegram_id: int, consent: bool):
    now = utcnow_iso()
    await db_pool.execute(
        """UPDATE users SET marketing_consent=$1, marketing_consent_at=$2, updated_at=$3
        WHERE telegram_id=$4""",
        1 if consent else 0, now, now, telegram_id,
    )

async def insert_pending_payment(payment_id: str, telegram_id: int, username: Optional[str], full_name: str, email: str, product_key: str):
    product = PRODUCTS[product_key]
    consent_row = await db_pool.fetchrow(
        "SELECT marketing_consent, marketing_consent_at FROM users WHERE telegram_id=$1", telegram_id
    )
    marketing_consent = consent_row["marketing_consent"] if consent_row else None
    marketing_consent_at = consent_row["marketing_consent_at"] if consent_row else None
    await db_pool.execute(
        """INSERT INTO payments(
            yookassa_payment_id, telegram_id, username, full_name, email, product_key,
            product_title, amount, currency, status, created_at, marketing_consent, marketing_consent_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'RUB','pending',$9,$10,$11)
        ON CONFLICT (yookassa_payment_id) DO NOTHING""",
        payment_id, telegram_id, username, full_name, email, product_key, product["title"],
        product["price"], utcnow_iso(), marketing_consent, marketing_consent_at,
    )

async def get_payment(payment_id: str):
    return await db_pool.fetchrow(
        "SELECT * FROM payments WHERE yookassa_payment_id=$1", payment_id
    )

async def mark_paid(payment_id: str) -> bool:
    result = await db_pool.execute(
        """UPDATE payments SET status='succeeded', paid_at=$1
        WHERE yookassa_payment_id=$2 AND status!='succeeded'""",
        utcnow_iso(), payment_id,
    )
    return result == "UPDATE 1"

async def mark_notification_sent(payment_id: str):
    await db_pool.execute(
        "UPDATE payments SET notification_sent=1 WHERE yookassa_payment_id=$1", payment_id
    )

async def mark_access_sent(payment_id: str):
    await db_pool.execute(
        "UPDATE payments SET access_sent=1 WHERE yookassa_payment_id=$1", payment_id
    )

async def paid_buyers_rows():
    return await db_pool.fetch(
        """SELECT paid_at, telegram_id, full_name, username, email, product_title, amount, currency,
        yookassa_payment_id, marketing_consent, marketing_consent_at
        FROM payments WHERE status='succeeded' ORDER BY paid_at DESC"""
    )

async def user_paid_purchases(telegram_id: int, limit: int = 10):
    return await db_pool.fetch(
        """SELECT paid_at, telegram_id, email, product_key, product_title, amount, currency,
        yookassa_payment_id, receipt_sent FROM payments
        WHERE telegram_id=$1 AND status='succeeded' ORDER BY paid_at DESC LIMIT $2""",
        telegram_id, limit,
    )

async def successful_payments_full():
    return await db_pool.fetch(
        """SELECT paid_at, product_key, product_title, amount, currency, yookassa_payment_id, receipt_sent
        FROM payments WHERE status='succeeded' ORDER BY paid_at DESC"""
    )

async def mark_receipt_sent(payment_id: str):
    result = await db_pool.execute(
        """UPDATE payments SET receipt_sent=1, receipt_sent_at=$1
        WHERE yookassa_payment_id=$2 AND status='succeeded'""",
        utcnow_iso(), payment_id,
    )
    return result == "UPDATE 1"

async def clear_payments():
    deleted_count = await db_pool.fetchval("SELECT COUNT(*) FROM payments")
    await db_pool.execute("TRUNCATE TABLE payments RESTART IDENTITY")
    return deleted_count


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


async def show_service_panel(
    message: Message,
    text: str,
    *,
    disable_web_page_preview: bool = True,
):
    """Show bottom-menu content in one persistent bot message."""
    user_id = message.from_user.id
    chat_id = message.chat.id

    # ReplyKeyboard buttons arrive as normal user messages.
    # Delete them when Telegram allows it so the chat stays clean.
    with suppress(Exception):
        await message.delete()

    service_message_id = await get_service_message_id(user_id)

    if service_message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=service_message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=disable_web_page_preview,
            )
            return
        except Exception as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.info(
                "Could not edit service panel for user %s; creating a new one",
                user_id,
            )

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=disable_web_page_preview,
        reply_markup=persistent_keyboard(),
    )
    await set_service_message_id(user_id, sent.message_id)


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    await upsert_user(message.from_user)

    # First bot message is a separate persistent window for the bottom menu.
    service_message = await message.answer(
        "🌿 <b>Yoga Lovers Club</b>\n\n",
        parse_mode=ParseMode.HTML,
        reply_markup=persistent_keyboard(),
    )
    await set_service_message_id(message.from_user.id, service_message.message_id)

    # Product menu remains a separate message and keeps its existing in-place navigation.
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
    await safe_edit(query, legal_text(key), details_keyboard(key))
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


async def begin_email_checkout(query: CallbackQuery, state: FSMContext, key: str):
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
                [InlineKeyboardButton(text="← Назад", callback_data=f"product:{key}")]
            ]
        ),
    )


@router.callback_query(F.data.startswith("marketing_yes:"))
async def marketing_yes_handler(query: CallbackQuery, state: FSMContext):
    key = query.data.split(":", 1)[1]
    if key not in PRODUCTS:
        await query.answer("Неизвестный продукт", show_alert=True)
        return

    await set_marketing_consent(query.from_user.id, True)
    await begin_email_checkout(query, state, key)
    await query.answer("✅ Согласие на рассылку сохранено")


@router.callback_query(F.data.startswith("marketing_no:"))
async def marketing_no_handler(query: CallbackQuery, state: FSMContext):
    key = query.data.split(":", 1)[1]
    if key not in PRODUCTS:
        await query.answer("Неизвестный продукт", show_alert=True)
        return

    await set_marketing_consent(query.from_user.id, False)
    await begin_email_checkout(query, state, key)
    await query.answer()


@router.callback_query(F.data.startswith("email:"))
async def email_handler(query: CallbackQuery, state: FSMContext):
    key = query.data.split(":", 1)[1]
    if key not in PRODUCTS:
        await query.answer("Неизвестный продукт", show_alert=True)
        return

    await begin_email_checkout(query, state, key)
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

    # Remove the user's email message when Telegram permissions allow it.
    # Then move the checkout to the bottom of the chat so the payment button
    # is immediately visible after email entry.
    with suppress(Exception):
        await message.delete()

    text = legal_text(key) + (
        f"\n\n📧 Email: <b>{html.escape(email)}</b>\n"
        "Нажмите кнопку ниже — откроется защищённая страница ЮKassa."
    )

    # Delete the previous checkout window so the chat keeps only one
    # active payment card instead of accumulating duplicate messages.
    with suppress(Exception):
        await bot.delete_message(
            chat_id=menu_chat_id,
            message_id=menu_message_id,
        )

    await bot.send_message(
        chat_id=menu_chat_id,
        text=text,
        reply_markup=payment_keyboard(payment_url, key),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    await state.clear()


@router.message(F.text == "👤 Обо мне")
async def about_handler(message: Message):
    await show_service_panel(message, ABOUT_TEXT)


@router.message(F.text == "💬 Поддержка")
async def support_handler(message: Message):
    await show_service_panel(message, SUPPORT_TEXT)


@router.message(F.text == "🎁 Пробная тренировка")
async def trial_handler(message: Message):
    await show_service_panel(message, TRIAL_TEXT)


@router.message(F.text == "📄 Мои покупки")
async def my_purchases_handler(message: Message):
    rows = await user_paid_purchases(message.from_user.id)

    if not rows:
        await show_service_panel(
            message,
            "📄 <b>Мои покупки</b>\n\n"
            "У Вас пока нет оплаченных покупок.",
        )
        return

    parts = ["📄 <b>Мои покупки</b>"]

    for index, row in enumerate(rows, start=1):
        paid_time = format_moscow_time(row["paid_at"])
        receipt_status = "✅ отправлен" if row["receipt_sent"] else "⏳ ожидает отправки"

        item = (
            f"\n<b>{index}. {html.escape(row['product_title'])}</b>\n"
            f"💰 {row['amount']} {html.escape(row['currency'])}\n"
            f"🕒 {html.escape(paid_time)}\n"
            f"🆔 <b>Payment ID</b>\n"
            f"<code>{html.escape(row['yookassa_payment_id'])}</code>\n"
            f"🧾 Чек: {receipt_status}"
        )

        if row["product_key"] == "energy" and YANDEX_DISK_URL:
            item += (
                f'\n♾️ <a href="{html.escape(YANDEX_DISK_URL)}">'
                "Открыть тренировки</a>"
            )

        parts.append(item)

    parts.append(
        "\nЕсли возник вопрос по оплате, отправьте в поддержку "
        "соответствующий Payment ID."
    )

    await show_service_panel(
        message,
        "\n".join(parts),
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
                "Рекламная рассылка разрешена",
                "Дата согласия/отказа UTC",
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
                    "ДА" if row["marketing_consent"] == 1 else "НЕТ",
                    row["marketing_consent_at"] or "",
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

    breakdown = "\n".join(
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
            "Использование:\n<code>/receipt PAYMENT_ID</code>\n\n"
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
📧 <b>Email</b>
<code>{html.escape(payment_row["email"])}</code>

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
        f"✅ Чек отмечен как отправленный.\n"
        f"Покупатель уведомлён.\n"
        f"Payment ID: <code>{html.escape(payment_id)}</code>",
        parse_mode=ParseMode.HTML,
    )
@router.message(Command("clearpayments"))
async def clear_payments_command(message: Message):
    if not is_admin(message.from_user.id):
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Да, очистить",
                    callback_data="confirm_clear_payments",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel_clear_payments",
                )
            ],
        ]
    )

    await message.answer(
        "⚠️ <b>Вы уверены?</b>\n\n"
        "Будут удалены <b>ВСЕ</b> записи о покупках.\n"
        "Это действие нельзя отменить.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
@router.callback_query(F.data == "confirm_clear_payments")
async def confirm_clear_payments(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    deleted_count = await clear_payments()

    await callback.message.edit_text(
        f"✅ База покупок очищена.\n\n"
        f"Удалено записей: <b>{deleted_count}</b>",
        parse_mode=ParseMode.HTML,
    )

    await callback.answer("Готово")


@router.callback_query(F.data == "cancel_clear_payments")
async def cancel_clear_payments(callback: CallbackQuery):
    await callback.message.edit_text(
        "❌ Очистка отменена."
    )
    await callback.answer()
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

    if payment_row["marketing_consent"] == 1:
        marketing_status = "✅ <b>Рекламная рассылка разрешена</b>"
    else:
        marketing_status = (
            "🚫❗ <b>ВАЖНО: РЕКЛАМНУЮ РАССЫЛКУ ЭТОМУ ПОКУПАТЕЛЮ "
            "ОТПРАВЛЯТЬ НЕЛЬЗЯ.</b>"
        )

    admin_text = f"""✅ <b>НОВАЯ ОПЛАТА</b>

📚 Продукт: <b>{html.escape(payment_row["product_title"])}</b>
💰 Сумма: <b>{payment_row["amount"]} RUB</b>
🕒 Время: {html.escape(paid_time)}

👤 Имя: {html.escape(name)}
🔗 Username: {html.escape(username)}
🆔 Telegram ID: <code>{payment_row["telegram_id"]}</code>

📧 <b>Email</b>
<code>{html.escape(payment_row["email"])}</code>

💳 <b>Payment ID</b>
<code>{html.escape(payment_row["yookassa_payment_id"])}</code>

{marketing_status}

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

    # Regular users see only the public start command.
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть меню"),
        ]
    )

    # Each admin sees the admin command menu in a private chat with the bot.
    admin_commands = [
        BotCommand(command="start", description="Открыть меню"),
        BotCommand(command="buyers", description="Таблица покупателей"),
        BotCommand(command="stats", description="Статистика продаж"),
        BotCommand(command="receipt", description="Отметить чек отправленным"),
        BotCommand(command="clearpayments", description="Очистить базу покупок"),
    ]

    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            logger.exception(
                "Could not set Telegram command menu for admin %s",
                admin_id,
            )

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
        if db_pool:
            await db_pool.close()


if __name__ == "__main__":
    asyncio.run(main())