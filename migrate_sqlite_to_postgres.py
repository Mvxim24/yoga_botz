import asyncio
import os
import sqlite3
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH = os.getenv("SQLITE_PATH", "/app/data/yoga_bot.sqlite3")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

if not Path(SQLITE_PATH).exists():
    raise RuntimeError(f"SQLite database not found: {SQLITE_PATH}")


PG_SCHEMA = """
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


def sqlite_columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def row_dict(row):
    return dict(row)


async def main():
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    user_columns = sqlite_columns(sqlite_conn, "users")
    payment_columns = sqlite_columns(sqlite_conn, "payments")

    users = [row_dict(r) for r in sqlite_conn.execute("SELECT * FROM users").fetchall()]
    payments = [row_dict(r) for r in sqlite_conn.execute("SELECT * FROM payments").fetchall()]

    print("SQLite:")
    print("  USERS:", len(users))
    print("  PAYMENTS:", len(payments))
    print(
        "  SUCCEEDED:",
        sum(1 for p in payments if p.get("status") == "succeeded"),
    )

    pg = await asyncpg.connect(DATABASE_URL)
    try:
        await pg.execute(PG_SCHEMA)

        async with pg.transaction():
            for u in users:
                await pg.execute(
                    """
                    INSERT INTO users(
                        telegram_id, username, full_name, service_message_id,
                        created_at, updated_at, marketing_consent, marketing_consent_at
                    )
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                    ON CONFLICT (telegram_id) DO UPDATE SET
                        username=EXCLUDED.username,
                        full_name=EXCLUDED.full_name,
                        service_message_id=EXCLUDED.service_message_id,
                        created_at=EXCLUDED.created_at,
                        updated_at=EXCLUDED.updated_at,
                        marketing_consent=EXCLUDED.marketing_consent,
                        marketing_consent_at=EXCLUDED.marketing_consent_at
                    """,
                    u.get("telegram_id"),
                    u.get("username"),
                    u.get("full_name"),
                    u.get("service_message_id"),
                    u.get("created_at"),
                    u.get("updated_at"),
                    u.get("marketing_consent") if "marketing_consent" in user_columns else None,
                    u.get("marketing_consent_at") if "marketing_consent_at" in user_columns else None,
                )

            for p in payments:
                await pg.execute(
                    """
                    INSERT INTO payments(
                        yookassa_payment_id, telegram_id, username, full_name, email,
                        product_key, product_title, amount, currency, status,
                        created_at, paid_at, notification_sent, access_sent,
                        receipt_sent, receipt_sent_at, marketing_consent, marketing_consent_at
                    )
                    VALUES(
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
                        $11,$12,$13,$14,$15,$16,$17,$18
                    )
                    ON CONFLICT (yookassa_payment_id) DO UPDATE SET
                        telegram_id=EXCLUDED.telegram_id,
                        username=EXCLUDED.username,
                        full_name=EXCLUDED.full_name,
                        email=EXCLUDED.email,
                        product_key=EXCLUDED.product_key,
                        product_title=EXCLUDED.product_title,
                        amount=EXCLUDED.amount,
                        currency=EXCLUDED.currency,
                        status=EXCLUDED.status,
                        created_at=EXCLUDED.created_at,
                        paid_at=EXCLUDED.paid_at,
                        notification_sent=EXCLUDED.notification_sent,
                        access_sent=EXCLUDED.access_sent,
                        receipt_sent=EXCLUDED.receipt_sent,
                        receipt_sent_at=EXCLUDED.receipt_sent_at,
                        marketing_consent=EXCLUDED.marketing_consent,
                        marketing_consent_at=EXCLUDED.marketing_consent_at
                    """,
                    p.get("yookassa_payment_id"),
                    p.get("telegram_id"),
                    p.get("username"),
                    p.get("full_name"),
                    p.get("email"),
                    p.get("product_key"),
                    p.get("product_title"),
                    p.get("amount"),
                    p.get("currency") or "RUB",
                    p.get("status") or "pending",
                    p.get("created_at"),
                    p.get("paid_at"),
                    p.get("notification_sent", 0) or 0,
                    p.get("access_sent", 0) or 0,
                    p.get("receipt_sent", 0) or 0,
                    p.get("receipt_sent_at"),
                    p.get("marketing_consent") if "marketing_consent" in payment_columns else None,
                    p.get("marketing_consent_at") if "marketing_consent_at" in payment_columns else None,
                )

        pg_users = await pg.fetchval("SELECT COUNT(*) FROM users")
        pg_payments = await pg.fetchval("SELECT COUNT(*) FROM payments")
        pg_succeeded = await pg.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status='succeeded'"
        )

        print("PostgreSQL:")
        print("  USERS:", pg_users)
        print("  PAYMENTS:", pg_payments)
        print("  SUCCEEDED:", pg_succeeded)

        if (
            pg_users == len(users)
            and pg_payments == len(payments)
            and pg_succeeded == sum(1 for p in payments if p.get("status") == "succeeded")
        ):
            print("MIGRATION CHECK: OK")
        else:
            print("MIGRATION CHECK: COUNTS DO NOT MATCH")
            raise SystemExit(2)

    finally:
        await pg.close()
        sqlite_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
