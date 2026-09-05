# Premium Hub Store Bot - V25.1 All Fixes Ready
# Python 3.10+
# Dependencies:
#   pip install aiogram aiohttp python-dotenv reportlab
#
# Required .env values:
# BOT_TOKEN=123456:ABC...
# BOT_NAME=Premium Hub Store
# ADMIN_ID=123456789
# PAYMENT_BASE_URL=http://127.0.0.1:8000
# PAYMENT_API_KEY=pk_live_xxxx
# PAYMENT_WEBHOOK_SECRET=whsec_xxxx
# INVOICE_CURRENCY=USDT
# DB_PATH=data/premium_shop.db
# WEB_HOST=0.0.0.0
# WEB_PORT=8080

import asyncio
import hashlib
import hmac
import html
import hashlib
import io
import json
import logging
import os
import re
import smtplib
import ssl
import sqlite3
import threading
import time
try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
except Exception:
    psycopg2 = None
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from math import ceil
from pathlib import Path
from uuid import uuid4

import aiohttp
from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_NAME = os.getenv("BOT_NAME", "Premium Hub Store")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4.1-mini").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@YourSupport").strip()
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID", "").strip()
PUBLIC_CHANNEL_URL = os.getenv("PUBLIC_CHANNEL_URL", "").strip()
ADMIN_ALERT_CHANNEL_ID = os.getenv("ADMIN_ALERT_CHANNEL_ID", "").strip()
CHANNEL_1_ID = os.getenv("CHANNEL_1_ID", "").strip()
CHANNEL_1_URL = os.getenv("CHANNEL_1_URL", PUBLIC_CHANNEL_URL).strip()
CHANNEL_1_NAME = os.getenv("CHANNEL_1_NAME", "Premium Hub Store").strip()
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID", "").strip()
CHANNEL_2_URL = os.getenv("CHANNEL_2_URL", "").strip()
CHANNEL_2_NAME = os.getenv("CHANNEL_2_NAME", "Premium Hub Updates").strip()
REFERRAL_BONUS = Decimal(os.getenv("REFERRAL_BONUS", "0"))
API_KEY = os.getenv("API_KEY", "").strip()
API_SECRET = os.getenv("API_SECRET", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
PAYMENT_BASE_URL = os.getenv("PAYMENT_BASE_URL", "").strip().rstrip("/")
PAYMENT_API_KEY = os.getenv("PAYMENT_API_KEY", "").strip()
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", os.getenv("WEBHOOK_SECRET", "")).strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
INVOICE_CURRENCY = os.getenv("INVOICE_CURRENCY", "USDT").strip().upper()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER).strip()
DB_PATH = os.getenv("DB_PATH", "data/premium_shop.db").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0").strip()
WEB_PORT = int(os.getenv("PORT") or os.getenv("WEB_PORT", "8080") or 8080)
API_BASE = "https://pay.itemodeverify.com"
PRODUCTS_PER_PAGE = 8
LOW_STOCK_THRESHOLDS = (10, 5, 2, 1, 0)
EMAIL_CODE_TTL_MINUTES = int(os.getenv("EMAIL_CODE_TTL_MINUTES", "10") or 10)
EMAIL_CODE_MAX_ATTEMPTS = int(os.getenv("EMAIL_CODE_MAX_ATTEMPTS", "5") or 5)
ALLOW_INSECURE_WEBHOOKS = os.getenv("ALLOW_INSECURE_WEBHOOKS", "0").strip().lower() in {"1", "true", "yes", "on"}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing from .env")

if not DATABASE_URL:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

router = Router()
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")

# =========================
# HOT-PATH RUNTIME CACHE
# =========================
# Very short TTL caches remove repetitive PostgreSQL round-trips from every
# Telegram button press while still reflecting admin changes quickly.
_LANG_CACHE = {}          # uid -> (expires_at, language)
_BLOCK_CACHE = {}         # uid -> (expires_at, blocked)
_MAINT_CACHE = [0.0, False]
LANG_CACHE_TTL = float(os.getenv("LANG_CACHE_TTL", "30") or 30)
BLOCK_CACHE_TTL = float(os.getenv("BLOCK_CACHE_TTL", "5") or 5)
MAINT_CACHE_TTL = float(os.getenv("MAINT_CACHE_TTL", "2") or 2)
BOT_USERNAME = ""



# =========================
# FSM STATES
# =========================

class EmailState(StatesGroup):
    waiting = State()       # waiting for email address
    waiting_code = State()  # waiting for 6-digit verification code


class AIChatState(StatesGroup):
    waiting = State()


class AdminProductState(StatesGroup):
    name = State()
    price = State()
    validity = State()
    warranty = State()
    product_type = State()
    category = State()
    note = State()
    stock = State()


class AdminConfigState(StatesGroup):
    waiting = State()


class AdminAPIState(StatesGroup):
    name = State()
    base_url = State()
    api_key = State()
    auth_header = State()
    products_endpoint = State()
    balance_endpoint = State()
    order_endpoint = State()
    status_endpoint = State()

class AdminStockState(StatesGroup):
    product_id = State()
    items = State()


class AdminBalanceState(StatesGroup):
    user = State()
    amount = State()
    confirm = State()


class AdminManageBalanceState(StatesGroup):
    user = State()
    amount = State()
    confirm = State()


class AdminPriceState(StatesGroup):
    product_id = State()
    new_price = State()


class AdminDeleteProductState(StatesGroup):
    product_id = State()
    confirm = State()


class AdminDeleteStockState(StatesGroup):
    product_id = State()
    amount = State()
    confirm = State()


class AdminEditProductState(StatesGroup):
    product_id = State()
    field = State()
    value = State()


class CustomQtyState(StatesGroup):
    choosing = State()


class TopupState(StatesGroup):
    amount = State()


class PayHubState(StatesGroup):
    waiting_txid = State()


class AdminBroadcastState(StatesGroup):
    waiting = State()


# =========================
# DATABASE
# =========================

def use_postgres() -> bool:
    return bool(DATABASE_URL)


def _pg_sql(sql: str) -> str:
    out = []
    in_s = in_d = False
    for ch in sql:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        if ch == "?" and not in_s and not in_d:
            out.append("%s")
        else:
            out.append(ch)
    text = "".join(out)
    text = text.replace("INSERT OR IGNORE INTO", "INSERT INTO")
    if "INSERT INTO" in text.upper() and "ON CONFLICT" not in text.upper() and text.upper().lstrip().startswith("INSERT INTO"):
        pass
    text = text.replace("excluded.", "EXCLUDED.")
    return text


class _PgCursor:
    def __init__(self, cur):
        self.cur = cur
        self.lastrowid = getattr(cur, "lastrowid", None)

    def fetchone(self):
        row = self.cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self.cur.fetchall()]

    def fetchmany(self, size=None):
        rows = self.cur.fetchmany(size) if size is not None else self.cur.fetchmany()
        return [dict(r) for r in rows]


class _PgConn:
    """Small DB-API compatibility wrapper for PostgreSQL.

    When created from the shared pool, close()/context-manager exit returns the
    connection to the pool instead of opening/closing a new TLS connection for
    every query. This is especially important on Render + Neon.
    """
    def __init__(self, raw, release=None):
        self.raw = raw
        self._release_cb = release
        self._released = False

    def execute(self, sql, params=()):
        sql = sql.strip()
        if sql.upper().startswith("PRAGMA"):
            class Empty:
                def fetchall(self):
                    return []
                def fetchone(self):
                    return None
            return Empty()
        cur = self.raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        pg = _pg_sql(sql)
        if sql.upper().startswith("INSERT") and "RETURNING" not in sql.upper() and any(x in sql.lower() for x in ("into products", "into api_providers")):
            pg = pg.rstrip().rstrip(";") + " RETURNING id"
        cur.execute(pg, params or None)
        if cur.description and "RETURNING" in pg.upper():
            row = cur.fetchone()
            self._last = _PgCursor(cur)
            if row:
                self._last.lastrowid = row.get("id")
            return self._last
        wrapped = _PgCursor(cur)
        wrapped.lastrowid = cur.lastrowid
        return wrapped

    def executemany(self, sql, seq_of_params):
        cur = self.raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.executemany(_pg_sql(sql.strip()), list(seq_of_params))
        return _PgCursor(cur)

    def executescript(self, script: str):
        for part in script.split(";"):
            stmt = part.strip()
            if not stmt or stmt.upper().startswith("PRAGMA"):
                continue
            self.execute(stmt)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def _release(self, broken: bool = False):
        if self._released:
            return
        self._released = True
        if self._release_cb:
            self._release_cb(self.raw, broken)
        else:
            self.raw.close()

    def close(self):
        # If caller manually closes without commit, discard any open transaction
        # before returning the connection to the shared pool.
        with suppress(Exception):
            self.raw.rollback()
        self._release(bool(getattr(self.raw, "closed", 0)))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        broken = False
        try:
            if exc_type:
                self.raw.rollback()
            else:
                self.raw.commit()
        except Exception:
            broken = True
            with suppress(Exception):
                self.raw.rollback()
            raise
        finally:
            self._release(broken or bool(getattr(self.raw, "closed", 0)))
        return False


_PG_POOL = None
_PG_POOL_LOCK = threading.Lock()


def _pg_pool():
    """Create one shared thread-safe PostgreSQL pool per bot process."""
    global _PG_POOL
    if _PG_POOL is not None:
        return _PG_POOL
    if not psycopg2:
        raise RuntimeError("psycopg2 is required for Neon. pip install psycopg2-binary")

    with _PG_POOL_LOCK:
        if _PG_POOL is None:
            min_conn = max(1, int(os.getenv("DB_POOL_MIN", "1") or 1))
            max_conn = max(min_conn, int(os.getenv("DB_POOL_MAX", "5") or 5))
            _PG_POOL = psycopg2.pool.ThreadedConnectionPool(
                min_conn,
                max_conn,
                dsn=DATABASE_URL,
                sslmode="require",
                connect_timeout=max(3, int(os.getenv("DB_CONNECT_TIMEOUT", "10") or 10)),
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
                application_name="premium_hub_bot",
            )
    return _PG_POOL


def _release_pg_connection(raw, broken: bool = False):
    pool = _PG_POOL
    if pool is None:
        with suppress(Exception):
            raw.close()
        return
    close_it = broken or bool(getattr(raw, "closed", 0))
    with suppress(Exception):
        if not close_it:
            raw.rollback()  # ensure a clean transaction state for the next user
    pool.putconn(raw, close=close_it)


def db():
    if use_postgres():
        pool = _pg_pool()
        raw = pool.getconn()
        raw.autocommit = False
        return _PgConn(raw, _release_pg_connection)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db() -> None:
    with db() as con:
        if use_postgres():
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    email TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    email_verification_token TEXT,
                    email_verification_sent_at TEXT,
                    email_verification_attempts INTEGER NOT NULL DEFAULT 0,
                    wallet TEXT NOT NULL DEFAULT '0',
                    language TEXT NOT NULL DEFAULT 'en',
                    blocked INTEGER NOT NULL DEFAULT 0,
                    join_prompt_seen INTEGER NOT NULL DEFAULT 0,
                    region TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    price TEXT NOT NULL,
                    validity TEXT NOT NULL DEFAULT 'N/A',
                    warranty TEXT NOT NULL DEFAULT 'No Warranty',
                    product_type TEXT NOT NULL DEFAULT 'Other',
                    category TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS stock_items (
                    id SERIAL PRIMARY KEY,
                    product_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'AVAILABLE',
                    order_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sold_at TEXT
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    order_id TEXT UNIQUE NOT NULL,
                    telegram_id BIGINT NOT NULL,
                    product_id INTEGER NOT NULL,
                    product_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price TEXT NOT NULL,
                    total_amount TEXT NOT NULL,
                    payment_method TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    delivered_content TEXT,
                    invoice_pdf_token TEXT UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    paid_at TEXT
                );
                CREATE TABLE IF NOT EXISTS wallet_transactions (
                    id SERIAL PRIMARY KEY,
                    tx_id TEXT UNIQUE NOT NULL,
                    telegram_id BIGINT NOT NULL,
                    type TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    balance_before TEXT NOT NULL,
                    balance_after TEXT NOT NULL,
                    reference_id TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS payment_invoices (
                    id SERIAL PRIMARY KEY,
                    invoice_id TEXT UNIQUE NOT NULL,
                    telegram_id BIGINT NOT NULL,
                    payment_kind TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    invoice_amount TEXT NOT NULL,
                    invoice_currency TEXT NOT NULL,
                    pay_amount TEXT,
                    pay_currency TEXT,
                    wallet_id TEXT,
                    tx_hash TEXT UNIQUE,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    paid_at TEXT
                );
                CREATE TABLE IF NOT EXISTS processed_webhooks (
                    id SERIAL PRIMARY KEY,
                    invoice_id TEXT NOT NULL,
                    tx_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(invoice_id, tx_hash)
                );
                CREATE TABLE IF NOT EXISTS stock_alerts (
                    product_id INTEGER NOT NULL,
                    threshold INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(product_id, threshold)
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS referrals (
                    referred_id BIGINT PRIMARY KEY,
                    referrer_id BIGINT NOT NULL,
                    bonus TEXT NOT NULL DEFAULT '0',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    verified_at TEXT
                );
                CREATE TABLE IF NOT EXISTS api_providers (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    auth_header TEXT NOT NULL DEFAULT 'X-API-Key',
                    products_endpoint TEXT NOT NULL DEFAULT '/products',
                    balance_endpoint TEXT NOT NULL DEFAULT '/balance',
                    order_endpoint TEXT NOT NULL DEFAULT '/orders',
                    status_endpoint TEXT NOT NULL DEFAULT '/orders/{id}',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Existing Neon databases also need lightweight migrations; CREATE TABLE IF NOT EXISTS
            # does not add newly introduced columns to an existing table.
            con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS region TEXT")
            con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS join_prompt_seen INTEGER NOT NULL DEFAULT 0")
            con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified INTEGER NOT NULL DEFAULT 0")
            con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_token TEXT")
            con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_sent_at TEXT")
            con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_attempts INTEGER NOT NULL DEFAULT 0")
            con.commit()
            logging.info("Neon / Postgres database ready")
            return
        con.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT NOT NULL,
                email TEXT,
                email_verified INTEGER NOT NULL DEFAULT 0,
                email_verification_token TEXT,
                email_verification_sent_at TEXT,
                email_verification_attempts INTEGER NOT NULL DEFAULT 0,
                wallet TEXT NOT NULL DEFAULT '0',
                language TEXT NOT NULL DEFAULT 'en',
                blocked INTEGER NOT NULL DEFAULT 0,
                join_prompt_seen INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price TEXT NOT NULL,
                validity TEXT NOT NULL DEFAULT 'N/A',
                warranty TEXT NOT NULL DEFAULT 'No Warranty',
                product_type TEXT NOT NULL DEFAULT 'Other',
                category TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS stock_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'AVAILABLE',
                order_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sold_at TEXT,
                FOREIGN KEY(product_id) REFERENCES products(id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                telegram_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price TEXT NOT NULL,
                total_amount TEXT NOT NULL,
                payment_method TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                delivered_content TEXT,
                invoice_pdf_token TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                paid_at TEXT,
                FOREIGN KEY(telegram_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_id TEXT UNIQUE NOT NULL,
                telegram_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount TEXT NOT NULL,
                balance_before TEXT NOT NULL,
                balance_after TEXT NOT NULL,
                reference_id TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payment_invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id TEXT UNIQUE NOT NULL,
                telegram_id INTEGER NOT NULL,
                payment_kind TEXT NOT NULL,
                reference_id TEXT NOT NULL,
                invoice_amount TEXT NOT NULL,
                invoice_currency TEXT NOT NULL,
                pay_amount TEXT,
                pay_currency TEXT,
                wallet_id TEXT,
                tx_hash TEXT UNIQUE,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                paid_at TEXT
            );

            CREATE TABLE IF NOT EXISTS processed_webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id TEXT NOT NULL,
                tx_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(invoice_id, tx_hash)
            );

            CREATE TABLE IF NOT EXISTS stock_alerts (
                product_id INTEGER NOT NULL,
                threshold INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_id, threshold)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS referrals (
                referred_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL,
                bonus TEXT NOT NULL DEFAULT '0',
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                verified_at TEXT
            );

            CREATE TABLE IF NOT EXISTS api_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                auth_header TEXT NOT NULL DEFAULT 'X-API-Key',
                products_endpoint TEXT NOT NULL DEFAULT '/products',
                balance_endpoint TEXT NOT NULL DEFAULT '/balance',
                order_endpoint TEXT NOT NULL DEFAULT '/orders',
                status_endpoint TEXT NOT NULL DEFAULT '/orders/{id}',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # Lightweight migration for existing databases
        user_columns = {row["name"] for row in con.execute("PRAGMA table_info(users)").fetchall()}
        if "region" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN region TEXT")
        if "join_prompt_seen" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN join_prompt_seen INTEGER NOT NULL DEFAULT 0")
        if "email_verified" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        if "email_verification_token" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN email_verification_token TEXT")
        if "email_verification_sent_at" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN email_verification_sent_at TEXT")
        if "email_verification_attempts" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN email_verification_attempts INTEGER NOT NULL DEFAULT 0")
        con.commit()



def get_app_setting(key: str, default: str = "") -> str:
    with db() as con:
        row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_app_setting(key: str, value: str) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        con.commit()
    if key == "maintenance_mode":
        _MAINT_CACHE[0] = 0.0
        _MAINT_CACHE[1] = str(value) == "1"


def refresh_runtime_settings() -> None:
    """Load admin-managed settings from SQLite into the existing runtime globals."""
    global PUBLIC_CHANNEL_ID, PUBLIC_CHANNEL_URL, ADMIN_ALERT_CHANNEL_ID
    global CHANNEL_1_ID, CHANNEL_1_URL, CHANNEL_1_NAME
    global CHANNEL_2_ID, CHANNEL_2_URL, CHANNEL_2_NAME
    global REFERRAL_BONUS
    global SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    global AI_API_KEY, AI_MODEL
    global PAYMENT_BASE_URL, PAYMENT_API_KEY, PAYMENT_WEBHOOK_SECRET

    PUBLIC_CHANNEL_ID = get_app_setting("public_channel_id", PUBLIC_CHANNEL_ID)
    PUBLIC_CHANNEL_URL = get_app_setting("public_channel_url", PUBLIC_CHANNEL_URL)
    ADMIN_ALERT_CHANNEL_ID = get_app_setting("admin_alert_channel_id", ADMIN_ALERT_CHANNEL_ID)

    CHANNEL_1_ID = get_app_setting("channel_1_id", CHANNEL_1_ID)
    CHANNEL_1_URL = get_app_setting("channel_1_url", CHANNEL_1_URL)
    CHANNEL_1_NAME = get_app_setting("channel_1_name", CHANNEL_1_NAME or "Premium Hub Store")
    CHANNEL_2_ID = get_app_setting("channel_2_id", CHANNEL_2_ID)
    CHANNEL_2_URL = get_app_setting("channel_2_url", CHANNEL_2_URL)
    CHANNEL_2_NAME = get_app_setting("channel_2_name", CHANNEL_2_NAME or "Premium Hub Updates")

    try:
        REFERRAL_BONUS = Decimal(get_app_setting("referral_bonus", str(REFERRAL_BONUS)))
    except Exception:
        REFERRAL_BONUS = Decimal("0")

    SMTP_HOST = get_app_setting("smtp_host", SMTP_HOST or "smtp.gmail.com")
    try:
        SMTP_PORT = int(get_app_setting("smtp_port", str(SMTP_PORT or 587)))
    except Exception:
        SMTP_PORT = 587
    SMTP_USER = get_app_setting("smtp_user", SMTP_USER)
    SMTP_PASSWORD = get_app_setting("smtp_password", SMTP_PASSWORD)
    SMTP_FROM = get_app_setting("smtp_from", SMTP_FROM or SMTP_USER)

    AI_API_KEY = get_app_setting("ai_api_key", AI_API_KEY or "")
    AI_MODEL = get_app_setting("ai_model", AI_MODEL or "gpt-4.1-mini").strip() or "gpt-4.1-mini"

    PAYMENT_BASE_URL = get_app_setting("payment_base_url", PAYMENT_BASE_URL or "").strip().rstrip("/")
    PAYMENT_API_KEY = get_app_setting("payment_api_key", PAYMENT_API_KEY or "").strip()
    PAYMENT_WEBHOOK_SECRET = get_app_setting("payment_webhook_secret", PAYMENT_WEBHOOK_SECRET or "").strip()



def maintenance_enabled() -> bool:
    now = time.monotonic()
    if now < _MAINT_CACHE[0]:
        return bool(_MAINT_CACHE[1])
    enabled = get_app_setting("maintenance_mode", "0") == "1"
    _MAINT_CACHE[0] = now + MAINT_CACHE_TTL
    _MAINT_CACHE[1] = enabled
    return enabled


async def broadcast_maintenance(bot: Bot, enabled: bool):
    if enabled:
        text = (
            "🛠 <b>Premium Hub Store Maintenance</b>\n\n"
            "We are currently performing maintenance. "
            "Shopping, purchases and wallet top-ups are temporarily unavailable.\n\n"
            "Please try again later. Thank you for your patience. 🙏"
        )
    else:
        text = (
            "✅ <b>Premium Hub Store is Back Online</b>\n\n"
            "Maintenance has finished. Shopping and other services are available again. 🛒"
        )
    with db() as con:
        users = con.execute("SELECT telegram_id FROM users WHERE blocked=0").fetchall()
    for row in users:
        await safe_send(bot, row["telegram_id"], text)


async def maintenance_guard(callback: CallbackQuery) -> bool:
    if is_admin(callback.from_user.id) or not maintenance_enabled():
        return False
    await callback.answer("🛠 Bot is currently under maintenance.", show_alert=True)
    return True


def money(v) -> str:
    return format(Decimal(str(v)).normalize(), "f")




def migrate_v23_schema():
    if use_postgres():
        return
    with db() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(products)").fetchall()}
        for col, ddl in [
            ("validity", "ALTER TABLE products ADD COLUMN validity TEXT NOT NULL DEFAULT 'N/A'"),
            ("product_type", "ALTER TABLE products ADD COLUMN product_type TEXT NOT NULL DEFAULT 'Other'"),
            ("category", "ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT ''"),
            ("display_order", "ALTER TABLE products ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0"),
        ]:
            if col not in cols:
                con.execute(ddl)
        # Give existing products a stable order based on their original ID.
        con.execute("UPDATE products SET display_order=id WHERE display_order=0")
        referral_columns = {row["name"] for row in con.execute("PRAGMA table_info(referrals)").fetchall()}
        if "status" not in referral_columns:
            con.execute("ALTER TABLE referrals ADD COLUMN status TEXT NOT NULL DEFAULT 'PENDING'")
            # Existing referrals were already credited by the previous version.
            # Mark them verified so upgrading never pays the same bonus twice.
            con.execute("UPDATE referrals SET status='VERIFIED' WHERE status='PENDING'")
        if "verified_at" not in referral_columns:
            con.execute("ALTER TABLE referrals ADD COLUMN verified_at TEXT")
        con.commit()


def register_user(user) -> None:
    if not user:
        return
    with db() as con:
        con.execute(
            """
            INSERT INTO users(telegram_id, username, full_name, blocked)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name
            """,
            (user.id, user.username, user.full_name),
        )
        con.commit()


def get_user(uid: int):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()


def register_user_and_get(user):
    """Register/update a Telegram user and return (row, is_first_start).

    This deliberately uses ONE database connection for the whole /start path.
    On Render + Neon this avoids multiple TLS/database round trips just to show
    the main menu. Existing user data such as wallet/email/language is preserved.
    """
    if not user:
        return None, False
    with db() as con:
        existing = con.execute(
            "SELECT telegram_id FROM users WHERE telegram_id=?", (user.id,)
        ).fetchone()
        con.execute(
            """
            INSERT INTO users(telegram_id, username, full_name, blocked)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name
            """,
            (user.id, user.username, user.full_name),
        )
        row = con.execute(
            "SELECT * FROM users WHERE telegram_id=?", (user.id,)
        ).fetchone()
        con.commit()
        return row, existing is None


def set_user_blocked(uid: int, blocked: bool) -> None:
    with db() as con:
        con.execute("UPDATE users SET blocked=? WHERE telegram_id=?", (1 if blocked else 0, uid))
        con.commit()
    _BLOCK_CACHE[int(uid)] = (time.monotonic() + BLOCK_CACHE_TTL, bool(blocked))


def list_users(limit: int = 50):
    with db() as con:
        return con.execute("SELECT * FROM users ORDER BY created_at DESC, telegram_id DESC LIMIT ?", (limit,)).fetchall()


def set_email(uid: int, email: str | None):
    """Save email and reset verification. Generates a fresh 6-digit code when email is set."""
    with db() as con:
        if email:
            code = f"{uuid4().int % 1000000:06d}"
            con.execute(
                "UPDATE users SET email=?, email_verified=0, email_verification_token=?, email_verification_sent_at=NULL, email_verification_attempts=0 WHERE telegram_id=?",
                (email, code, uid),
            )
        else:
            con.execute(
                "UPDATE users SET email=NULL, email_verified=0, email_verification_token=NULL, email_verification_sent_at=NULL, email_verification_attempts=0 WHERE telegram_id=?",
                (uid,),
            )
        con.commit()


def mark_email_code_sent(uid: int) -> None:
    with db() as con:
        con.execute(
            "UPDATE users SET email_verification_sent_at=CURRENT_TIMESTAMP, email_verification_attempts=0 WHERE telegram_id=?",
            (uid,),
        )
        con.commit()


def get_email_verification(uid: int):
    with db() as con:
        return con.execute(
            "SELECT email, email_verified, email_verification_token, email_verification_sent_at, email_verification_attempts FROM users WHERE telegram_id=?",
            (uid,),
        ).fetchone()


def get_email_code(uid: int) -> str | None:
    row = get_email_verification(uid)
    if not row or not row["email_verification_token"]:
        return None
    return str(row["email_verification_token"])


def verify_email_code(uid: int, code: str) -> bool:
    code = str(code or "").strip()
    with db() as con:
        row = con.execute(
            "SELECT email_verification_token, email_verification_sent_at, email_verification_attempts FROM users WHERE telegram_id=?",
            (uid,),
        ).fetchone()
        if not row or not row["email_verification_token"]:
            return False
        sent_at = row.get("email_verification_sent_at") if isinstance(row, dict) else row["email_verification_sent_at"]
        if not sent_at:
            return False
        try:
            sent_dt = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
            if sent_dt.tzinfo is None:
                sent_dt = sent_dt.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - sent_dt.astimezone(timezone.utc)).total_seconds()
            if age_seconds > max(1, EMAIL_CODE_TTL_MINUTES) * 60:
                con.execute(
                    "UPDATE users SET email_verification_token=NULL, email_verification_sent_at=NULL WHERE telegram_id=?",
                    (uid,),
                )
                con.commit()
                return False
        except Exception:
            return False
        if str(row["email_verification_token"] or "") != code:
            attempts = int(row["email_verification_attempts"] or 0) + 1
            if attempts >= max(1, EMAIL_CODE_MAX_ATTEMPTS):
                con.execute(
                    "UPDATE users SET email_verification_token=NULL, email_verification_sent_at=NULL, email_verification_attempts=? WHERE telegram_id=?",
                    (attempts, uid),
                )
            else:
                con.execute(
                    "UPDATE users SET email_verification_attempts=? WHERE telegram_id=?",
                    (attempts, uid),
                )
            con.commit()
            return False
        con.execute(
            "UPDATE users SET email_verified=1, email_verification_token=NULL, email_verification_sent_at=NULL, email_verification_attempts=0 WHERE telegram_id=?",
            (uid,),
        )
        con.commit()
        return True


def is_email_verified(uid: int) -> bool:
    row = get_email_verification(uid)
    return bool(row and row["email"] and int(row["email_verified"] or 0))


def verify_email_token(token: str) -> bool:
    """Legacy link-based verify (kept for old links). Prefer verify_email_code."""
    with db() as con:
        row = con.execute(
            "SELECT telegram_id FROM users WHERE email_verification_token=? AND email_verification_token IS NOT NULL",
            (token,),
        ).fetchone()
        if not row:
            return False
        con.execute(
            "UPDATE users SET email_verified=1, email_verification_token=NULL WHERE telegram_id=?",
            (row["telegram_id"],),
        )
        con.commit()
        return True


def set_region(uid: int, region: str | None):
    with db() as con:
        con.execute("UPDATE users SET region=? WHERE telegram_id=?", (region, uid))
        con.commit()


def set_language(uid: int, language: str):
    with db() as con:
        con.execute("UPDATE users SET language=? WHERE telegram_id=?", (language, uid))
        con.commit()


def join_prompt_seen(uid: int) -> bool:
    with db() as con:
        row = con.execute(
            "SELECT join_prompt_seen FROM users WHERE telegram_id=?",
            (uid,),
        ).fetchone()
    return bool(row and int(row["join_prompt_seen"] or 0))


def mark_join_prompt_seen(uid: int) -> None:
    with db() as con:
        con.execute(
            "UPDATE users SET join_prompt_seen=1 WHERE telegram_id=?",
            (uid,),
        )
        con.commit()


def user_orders(uid: int, limit: int = 15):
    with db() as con:
        return con.execute(
            "SELECT * FROM orders WHERE telegram_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit),
        ).fetchall()


def list_products():
    with db() as con:
        return con.execute(
            "SELECT * FROM products WHERE active=1 ORDER BY display_order ASC, id ASC"
        ).fetchall()


def get_product(pid: int):
    with db() as con:
        return con.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()


def list_products_with_stock():
    """Fetch every active product and its AVAILABLE stock count in one SQL query."""
    with db() as con:
        return con.execute(
            """
            SELECT p.*,
                   COALESCE((SELECT COUNT(*) FROM stock_items s
                             WHERE s.product_id=p.id AND s.status='AVAILABLE'), 0) AS stock
            FROM products p
            WHERE p.active=1
            ORDER BY p.display_order ASC, p.id ASC
            """
        ).fetchall()


def get_product_with_stock(pid: int):
    with db() as con:
        return con.execute(
            """
            SELECT p.*,
                   COALESCE((SELECT COUNT(*) FROM stock_items s
                             WHERE s.product_id=p.id AND s.status='AVAILABLE'), 0) AS stock
            FROM products p
            WHERE p.id=? AND p.active=1
            """,
            (pid,),
        ).fetchone()


def group_products_with_stock(pid: int):
    """Fetch a standalone product or all products in its category, with stock, without N+1 queries."""
    with db() as con:
        return con.execute(
            """
            SELECT p.*,
                   COALESCE((SELECT COUNT(*) FROM stock_items s
                             WHERE s.product_id=p.id AND s.status='AVAILABLE'), 0) AS stock
            FROM products p
            WHERE p.active=1 AND (
                p.id=? OR (
                    COALESCE((SELECT category FROM products WHERE id=? AND active=1), '') <> ''
                    AND p.category=(SELECT category FROM products WHERE id=? AND active=1)
                )
            )
            ORDER BY p.display_order ASC, p.id ASC
            """,
            (pid, pid, pid),
        ).fetchall()



def referral_stats(uid: int):
    with db() as con:
        count = con.execute("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND status='VERIFIED'", (uid,)).fetchone()["c"]
        earned = con.execute("SELECT COALESCE(SUM(CAST(bonus AS REAL)),0) s FROM referrals WHERE referrer_id=? AND status='VERIFIED'", (uid,)).fetchone()["s"]
    return int(count), Decimal(str(earned or 0))


def add_referral(referred_id: int, referrer_id: int):
    if referred_id == referrer_id or not get_user(referrer_id):
        return False
    with db() as con:
        try:
            con.execute("INSERT INTO referrals(referred_id,referrer_id,bonus,status) VALUES (?,?,?,'PENDING')", (referred_id, referrer_id, str(REFERRAL_BONUS)))
            con.commit()
            return True
        except Exception as exc:
            if "unique" in str(exc).lower() or "integrity" in str(exc).lower() or isinstance(exc, sqlite3.IntegrityError):
                return False
            raise


async def verify_referral_for_user(bot: Bot, referred_id: int) -> bool:
    missing = await required_join_status(bot, referred_id)
    if missing:
        return False
    with db() as con:
        row = con.execute("SELECT * FROM referrals WHERE referred_id=? AND status='PENDING'", (referred_id,)).fetchone()
        if not row:
            return False
        referrer_id = int(row["referrer_id"])
        bonus = Decimal(str(row["bonus"] or "0"))
        if bonus > 0:
            ref = con.execute("SELECT wallet FROM users WHERE telegram_id=?", (referrer_id,)).fetchone()
            if ref:
                new_bal = Decimal(str(ref["wallet"])) + bonus
                con.execute("UPDATE users SET wallet=? WHERE telegram_id=?", (str(new_bal), referrer_id))
        con.execute("UPDATE referrals SET status='VERIFIED', verified_at=CURRENT_TIMESTAMP WHERE referred_id=?", (referred_id,))
        con.commit()
        return True


def stock_count(pid: int) -> int:
    with db() as con:
        return int(con.execute(
            "SELECT COUNT(*) c FROM stock_items WHERE product_id=? AND status='AVAILABLE'",
            (pid,),
        ).fetchone()["c"])


def add_stock(pid: int, items: list[str]) -> int:
    clean = [x.strip() for x in items if x.strip()]
    with db() as con:
        con.executemany(
            "INSERT INTO stock_items(product_id, content) VALUES (?,?)",
            [(pid, x) for x in clean],
        )
        con.execute("DELETE FROM stock_alerts WHERE product_id=?", (pid,))
        con.commit()
    return len(clean)


def create_product(name: str, price: Decimal, validity: str, warranty: str, product_type: str, category: str, note: str) -> int:
    with db() as con:
        next_order = int(con.execute(
            "SELECT COALESCE(MAX(display_order), 0) + 1 AS n FROM products"
        ).fetchone()["n"])
        cur = con.execute(
            "INSERT INTO products(name, price, validity, warranty, product_type, category, note, display_order) VALUES (?,?,?,?,?,?,?,?)",
            (name, str(price), validity, warranty, product_type, category, note, next_order),
        )
        con.commit()
        return int(cur.lastrowid)


def swap_product_order(pid_a: int, pid_b: int) -> None:
    with db() as con:
        a = con.execute("SELECT display_order FROM products WHERE id=? AND active=1", (pid_a,)).fetchone()
        b = con.execute("SELECT display_order FROM products WHERE id=? AND active=1", (pid_b,)).fetchone()
        if not a or not b:
            return
        con.execute("UPDATE products SET display_order=? WHERE id=?", (b["display_order"], pid_a))
        con.execute("UPDATE products SET display_order=? WHERE id=?", (a["display_order"], pid_b))
        con.commit()


def move_product(pid: int, direction: str) -> bool:
    products = list_products()
    ids = [int(p["id"]) for p in products]
    if pid not in ids:
        return False
    idx = ids.index(pid)
    target = idx - 1 if direction == "up" else idx + 1
    if target < 0 or target >= len(ids):
        return False
    swap_product_order(pid, ids[target])
    return True


def update_product_price(pid: int, new_price: Decimal) -> None:
    with db() as con:
        con.execute("UPDATE products SET price=? WHERE id=? AND active=1", (str(new_price), pid))
        con.commit()


def update_product_field(pid: int, field: str, value: str) -> None:
    allowed = {"name", "price", "warranty", "note"}
    if field not in allowed:
        raise ValueError("Unsupported product field")
    with db() as con:
        con.execute(f"UPDATE products SET {field}=? WHERE id=? AND active=1", (value, pid))
        con.commit()


def soft_delete_product(pid: int) -> int:
    """Hide a product and remove only its unsold stock. Order history stays intact."""
    with db() as con:
        count = con.execute(
            "SELECT COUNT(*) c FROM stock_items WHERE product_id=? AND status='AVAILABLE'",
            (pid,),
        ).fetchone()["c"]
        con.execute("DELETE FROM stock_items WHERE product_id=? AND status='AVAILABLE'", (pid,))
        con.execute("DELETE FROM stock_alerts WHERE product_id=?", (pid,))
        con.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
        con.commit()
        return int(count)


def delete_available_stock(pid: int, amount: int | None = None) -> int:
    with db() as con:
        if amount is None:
            rows = con.execute(
                "SELECT id FROM stock_items WHERE product_id=? AND status='AVAILABLE' ORDER BY id ASC",
                (pid,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id FROM stock_items WHERE product_id=? AND status='AVAILABLE' ORDER BY id ASC LIMIT ?",
                (pid, amount),
            ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        con.execute(f"DELETE FROM stock_items WHERE id IN ({marks})", ids)
        con.execute("DELETE FROM stock_alerts WHERE product_id=?", (pid,))
        con.commit()
        return len(ids)



def list_available_stock_items(pid: int):
    """Return the actual unsold stock rows so admin can manage items one by one."""
    with db() as con:
        return con.execute(
            """
            SELECT id, content, created_at
            FROM stock_items
            WHERE product_id=? AND status='AVAILABLE'
            ORDER BY id ASC
            """,
            (pid,),
        ).fetchall()


def delete_stock_item_by_id(pid: int, stock_item_id: int) -> bool:
    """Delete one exact AVAILABLE stock item. Sold stock/order history is never touched."""
    with db() as con:
        cur = con.execute(
            """
            DELETE FROM stock_items
            WHERE id=? AND product_id=? AND status='AVAILABLE'
            """,
            (stock_item_id, pid),
        )
        if cur.rowcount:
            con.execute("DELETE FROM stock_alerts WHERE product_id=?", (pid,))
        con.commit()
        return bool(cur.rowcount)


def create_order(uid: int, product, qty: int) -> str:
    oid = f"ORD-{uuid4().hex[:10].upper()}"
    total = Decimal(product["price"]) * qty
    token = uuid4().hex + uuid4().hex
    with db() as con:
        con.execute(
            """
            INSERT INTO orders(
                order_id, telegram_id, product_id, product_name,
                quantity, unit_price, total_amount, status, invoice_pdf_token
            ) VALUES (?,?,?,?,?,?,?,'PENDING_PAYMENT',?)
            """,
            (oid, uid, product["id"], product["name"], qty, product["price"], str(total), token),
        )
        con.commit()
    return oid


def get_order(oid: str):
    with db() as con:
        return con.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()


def update_order(oid: str, **fields):
    allowed = {"payment_method", "status", "delivered_content", "paid_at"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return
    sql = ", ".join(f"{k}=?" for k in clean)
    with db() as con:
        con.execute(f"UPDATE orders SET {sql} WHERE order_id=?", (*clean.values(), oid))
        con.commit()


def change_wallet(uid: int, amount: Decimal, tx_type: str, ref: str, note: str = "") -> Decimal:
    with db() as con:
        if use_postgres():
            row = con.execute("SELECT wallet FROM users WHERE telegram_id=? FOR UPDATE", (uid,)).fetchone()
        else:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT wallet FROM users WHERE telegram_id=?", (uid,)).fetchone()
        if not row:
            con.rollback()
            raise ValueError("User not found")
        before = Decimal(row["wallet"])
        after = before + amount
        if after < 0:
            con.rollback()
            raise ValueError("Insufficient balance")
        con.execute("UPDATE users SET wallet=? WHERE telegram_id=?", (str(after), uid))
        con.execute(
            """
            INSERT INTO wallet_transactions(
                tx_id, telegram_id, type, amount, balance_before, balance_after, reference_id, note
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (f"TX-{uuid4().hex[:14].upper()}", uid, tx_type, str(amount), str(before), str(after), ref, note),
        )
        con.commit()
        return after


def take_stock(pid: int, qty: int, oid: str) -> list[str]:
    with db() as con:
        if use_postgres():
            rows = con.execute(
                "SELECT id, content FROM stock_items WHERE product_id=? AND status='AVAILABLE' ORDER BY id LIMIT ? FOR UPDATE SKIP LOCKED",
                (pid, qty),
            ).fetchall()
        else:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                "SELECT id, content FROM stock_items WHERE product_id=? AND status='AVAILABLE' ORDER BY id LIMIT ?",
                (pid, qty),
            ).fetchall()
        if len(rows) < qty:
            con.rollback()
            return []
        ids = [r["id"] for r in rows]
        marks = ",".join("?" for _ in ids)
        con.execute(
            f"UPDATE stock_items SET status='SOLD', order_id=?, sold_at=CURRENT_TIMESTAMP WHERE id IN ({marks})",
            (oid, *ids),
        )
        con.commit()
        return [r["content"] for r in rows]


def save_invoice(invoice_id: str, uid: int, kind: str, ref: str, amount, currency, pay_amount, pay_currency, wallet_id):
    with db() as con:
        con.execute(
            """
            INSERT INTO payment_invoices(
                invoice_id, telegram_id, payment_kind, reference_id,
                invoice_amount, invoice_currency, pay_amount, pay_currency, wallet_id
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(invoice_id) DO UPDATE SET
                pay_amount=excluded.pay_amount,
                pay_currency=excluded.pay_currency,
                wallet_id=excluded.wallet_id
            """,
            (invoice_id, uid, kind, ref, str(amount), currency, str(pay_amount), pay_currency, wallet_id),
        )
        con.commit()


def get_saved_invoice(invoice_id: str):
    with db() as con:
        return con.execute("SELECT * FROM payment_invoices WHERE invoice_id=?", (invoice_id,)).fetchone()


def mark_webhook_processed(invoice_id: str, tx_hash: str) -> bool:
    try:
        with db() as con:
            con.execute(
                "INSERT INTO processed_webhooks(invoice_id, tx_hash) VALUES (?,?)",
                (invoice_id, tx_hash),
            )
            con.commit()
        return True
    except Exception as exc:
        if "unique" in str(exc).lower() or "integrity" in str(exc).lower() or isinstance(exc, sqlite3.IntegrityError):
            return False
        raise


# =========================
# PAYHUB PAYMENT API
# =========================

def payhub_configured() -> bool:
    return bool(PAYMENT_BASE_URL and PAYMENT_API_KEY)


async def payhub_post(path: str, payload: dict) -> dict:
    if not payhub_configured():
        raise RuntimeError("PayHub is not configured. Set PAYMENT_BASE_URL and PAYMENT_API_KEY.")
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{PAYMENT_BASE_URL}{path}",
            headers={"X-API-Key": PAYMENT_API_KEY, "Content-Type": "application/json"},
            json=payload,
        ) as r:
            body = await r.text()
            try:
                data = json.loads(body) if body else {}
            except Exception:
                data = {"raw": body}
            if r.status >= 400:
                msg = data.get("message") or data.get("error") or data.get("detail") or body or f"HTTP {r.status}"
                raise RuntimeError(str(msg)[:400])
            if isinstance(data, dict):
                data["_http_status"] = r.status
            return data if isinstance(data, dict) else {"raw": data}


def _normalize_payhub_payload(data: dict | None) -> dict:
    """Accept flat/nested and old/new PayHub response field names.

    Railway/PayHub builds used by this bot have returned both snake_case and
    camelCase fields, and some wrap the actual payload in data/result/invoice.
    Canonical aliases here keep Direct Pay compatible without weakening the
    payment verification decision.
    """
    if not isinstance(data, dict):
        return {}
    merged = dict(data)
    current = data
    for _ in range(4):
        nested = None
        for key in ("data", "result", "invoice", "payment"):
            value = current.get(key) if isinstance(current, dict) else None
            if isinstance(value, dict):
                nested = value
                break
        if not nested:
            break
        merged.update(nested)
        current = nested

    if not merged.get("invoice_id"):
        merged["invoice_id"] = merged.get("invoiceId") or merged.get("invoice_no")
    if not merged.get("uid"):
        merged["uid"] = (
            merged.get("binance_uid") or merged.get("binanceUid")
            or merged.get("pay_uid") or merged.get("payment_uid")
            or merged.get("wallet_id") or merged.get("walletId")
        )
    if not merged.get("txid"):
        merged["txid"] = (
            merged.get("tx_id") or merged.get("transaction_id")
            or merged.get("transactionId") or merged.get("order_id")
            or merged.get("orderId")
        )
    if not merged.get("status"):
        merged["status"] = merged.get("payment_status") or merged.get("state") or ""
    if "ok" not in merged and isinstance(merged.get("success"), bool):
        merged["ok"] = merged.get("success")
    return merged


async def payhub_create_invoice(telegram_id: int, amount) -> dict:
    result = await payhub_post("/api/v1/invoice", {
        "telegram_id": str(telegram_id),
        "amount": str(amount),
        "currency": INVOICE_CURRENCY,
    })
    return _normalize_payhub_payload(result)


async def payhub_verify(invoice_id: str, txid: str) -> dict:
    """Verify a Binance transaction/order ID using current and legacy PayHub fields."""
    entered = str(txid or "").strip()
    last_result = {}
    last_error = None
    for field in ("order_id", "txid", "tx_id"):
        try:
            raw = await payhub_post("/api/v1/verify", {"invoice_id": invoice_id, field: entered})
            result = _normalize_payhub_payload(raw)
            last_result = result
            status = str(result.get("status") or result.get("payment_status") or "").upper()
            if status in {"PAID", "SUCCESS", "COMPLETED", "CONFIRMED"}:
                return result
            if any(result.get(k) is True for k in ("paid", "verified", "confirmed")):
                return result
            # Compatibility with older PayHub builds where successful verification
            # returned ok=true without an explicit PAID status. Never accept known
            # pending/failure states merely because the HTTP request itself was OK.
            failed_states = {
                "FAILED", "INVALID", "MISMATCH", "WRONG_INVOICE", "NOT_FOUND",
                "PENDING", "UNPAID", "EXPIRED", "CANCELLED", "CANCELED",
            }
            if result.get("ok") is True and status not in failed_states:
                return result
        except Exception as exc:
            last_error = exc
    if last_result:
        return last_result
    if last_error:
        raise last_error
    return {}


async def apply_paid_invoice(bot: Bot | None, invoice_id: str, txid: str | None = None, network: str = "BINANCE_PAY", notify: bool = True) -> bool:
    """Credit deposit or complete order once. Returns True if applied now."""
    saved = get_saved_invoice(invoice_id)
    if not saved:
        return False
    if str(saved["status"] or "").upper() == "PAID":
        return False
    marker = txid or invoice_id
    if not mark_webhook_processed(invoice_id, str(marker)):
        return False
    with db() as con:
        con.execute(
            "UPDATE payment_invoices SET status='PAID', tx_hash=?, paid_at=CURRENT_TIMESTAMP WHERE invoice_id=?",
            (txid, invoice_id),
        )
        con.commit()
    if saved["payment_kind"] == "deposit":
        balance = change_wallet(saved["telegram_id"], Decimal(saved["invoice_amount"]), "DEPOSIT", invoice_id, txid or "")
        if bot and notify:
            await safe_send(
                bot,
                saved["telegram_id"],
                "✅ <b>Deposit Received</b>\n\n"
                f"💰 Credited: <b>{money(saved['invoice_amount'])} {saved['invoice_currency']}</b>\n"
                f"👛 New Balance: <b>{money(balance)} {INVOICE_CURRENCY}</b>\n"
                f"🧾 TX: <code>{html.escape(str(txid or '-'))}</code>",
            )
        return True
    oid = saved["reference_id"]
    order = get_order(oid)
    if order and order["status"] == "PENDING_PAYMENT":
        method = f"PAYHUB_{network}"
        update_order(oid, payment_method=method, status="PAID")
        if bot and notify:
            await complete_paid_order(bot, oid, method)
        return True
    return True

# =========================
# INVOICE EMAIL + PDF
# =========================

def invoice_pdf_bytes(order, user) -> bytes:
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=16*mm,leftMargin=16*mm,topMargin=15*mm,bottomMargin=15*mm,title=f"{BOT_NAME} Invoice {order['order_id']}")
    styles=getSampleStyleSheet(); title=styles["Title"].clone("InvoiceTitle"); title.fontName="Helvetica-Bold"; title.fontSize=24; title.textColor="#0F172A"
    normal=styles["Normal"].clone("InvoiceNormal"); normal.fontSize=9.5; normal.leading=13; normal.textColor="#334155"
    small=styles["Normal"].clone("InvoiceSmall"); small.fontSize=8; small.leading=11; small.textColor="#64748B"
    accent="#7C3AED"; accent2="#06B6D4"; dark="#0F172A"; light="#F8FAFC"; border="#CBD5E1"
    story=[Paragraph(f"<b>{html.escape(BOT_NAME)}</b>",title),Paragraph("DIGITAL PURCHASE RECEIPT",small),Spacer(1,8)]
    brand=Table([[Paragraph("<b>ORDER CONFIRMED</b>",normal),Paragraph(f"<b>{html.escape(str(order['order_id']))}</b>",normal)]],colWidths=[82*mm,82*mm])
    brand.setStyle(TableStyle([("BACKGROUND",(0,0),(0,0),accent),("BACKGROUND",(1,0),(1,0),accent2),("TEXTCOLOR",(0,0),(-1,-1),"#FFFFFF"),("ALIGN",(1,0),(1,0),"RIGHT"),("PADDING",(0,0),(-1,-1),11),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    story += [brand,Spacer(1,12)]
    meta=Table([["ISSUED",str(order["created_at"]),"PAYMENT",str(order["payment_method"] or "-")],["STATUS",str(order["status"] or "-"),"CURRENCY",str(INVOICE_CURRENCY)]],colWidths=[24*mm,58*mm,28*mm,54*mm])
    meta.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),light),("GRID",(0,0),(-1,-1),0.5,border),("FONTNAME",(0,0),(-1,-1),"Helvetica-Bold"),("TEXTCOLOR",(0,0),(-1,-1),dark),("PADDING",(0,0),(-1,-1),8)]))
    story += [meta,Spacer(1,12)]
    bill=Table([["CUSTOMER","CONTACT"],["Telegram ID",str(order["telegram_id"])],["Email",str(user["email"] or "Not provided")]],colWidths=[82*mm,82*mm])
    bill.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),dark),("TEXTCOLOR",(0,0),(-1,0),"#FFFFFF"),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),0.5,border),("PADDING",(0,0),(-1,-1),8)]))
    story += [bill,Spacer(1,12)]
    items=Table([["ITEM","QTY","UNIT","TOTAL"],[str(order["product_name"]),str(order["quantity"]),f"{money(order['unit_price'])} {INVOICE_CURRENCY}",f"{money(order['total_amount'])} {INVOICE_CURRENCY}"]],colWidths=[76*mm,18*mm,35*mm,35*mm])
    items.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),accent),("TEXTCOLOR",(0,0),(-1,0),"#FFFFFF"),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),0.5,border),("PADDING",(0,0),(-1,-1),9),("VALIGN",(0,0),(-1,-1),"TOP"),("ALIGN",(1,1),(-1,-1),"RIGHT")]))
    story += [items,Spacer(1,12)]
    total=Table([["TOTAL PAID",f"{money(order['total_amount'])} {INVOICE_CURRENCY}"]],colWidths=[105*mm,59*mm])
    total.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),"#EDE9FE"),("TEXTCOLOR",(0,0),(-1,-1),dark),("FONTNAME",(0,0),(-1,-1),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),13),("ALIGN",(1,0),(1,0),"RIGHT"),("BOX",(0,0),(-1,-1),0.8,accent),("PADDING",(0,0),(-1,-1),11)]))
    story += [total,Spacer(1,20),Paragraph(f"Thank you for choosing <b>{html.escape(BOT_NAME)}</b>.",normal),Paragraph(f"Support: {html.escape(SUPPORT_USERNAME)}",small),Paragraph("Generated automatically • Keep this receipt for your records.",small)]
    doc.build(story); return buf.getvalue()


def invoice_html(order, user, pdf_url: str, bot_username: str | None = None) -> str:
    safe = lambda x: html.escape(str(x))
    status = safe(str(order["status"] or "").upper())
    payment = safe(order["payment_method"] or "-")
    tg_btn = ""
    if bot_username:
        tg_url = f"https://t.me/{bot_username}"
        tg_btn = (
            f"<a href='{safe(tg_url)}' style='display:inline-block;background:#2563eb;color:#fff;"
            f"text-decoration:none;padding:15px 26px;border-radius:13px;font-weight:900;margin-left:10px'>"
            f"🤖 Re-open in Telegram</a>"
        )
    return f"""<!doctype html><html><body style='margin:0;background:#07111f;font-family:Arial,sans-serif;color:#e5eef8;padding:28px 12px'><div style='max-width:720px;margin:auto;background:#0b1726;border:1px solid #1f344b;border-radius:26px;overflow:hidden;box-shadow:0 22px 70px rgba(0,0,0,.28)'><div style='padding:34px;background:linear-gradient(135deg,#7c3aed,#2563eb,#06b6d4);color:#fff'><div style='font-size:11px;letter-spacing:2.8px;font-weight:800'>DIGITAL PURCHASE RECEIPT</div><div style='font-size:31px;font-weight:900;margin-top:9px'>{safe(BOT_NAME)}</div><div style='margin-top:18px;display:inline-block;padding:8px 13px;border-radius:999px;background:rgba(255,255,255,.16);font-size:12px;font-weight:800'>✓ {status}</div></div><div style='padding:30px'><table width='100%' style='border-collapse:collapse;margin-bottom:22px'><tr><td style='color:#8da2b8;font-size:11px;letter-spacing:1.5px'>ORDER</td><td style='color:#8da2b8;font-size:11px;letter-spacing:1.5px;text-align:right'>ISSUED</td></tr><tr><td style='font-size:18px;font-weight:900;padding-top:6px'>{safe(order["order_id"])}</td><td style='font-weight:700;text-align:right;padding-top:6px'>{safe(order["created_at"])}</td></tr></table><table width='100%' style='border-collapse:collapse;border:1px solid #20364d'><tr style='background:#101f31'><td style='padding:14px;color:#a9bad0;font-weight:800'>ITEM</td><td style='padding:14px;color:#a9bad0;font-weight:800;text-align:center'>QTY</td><td style='padding:14px;color:#a9bad0;font-weight:800;text-align:right'>TOTAL</td></tr><tr><td style='padding:18px;font-weight:900'>{safe(order["product_name"])}</td><td style='padding:18px;text-align:center'>{safe(order["quantity"])}</td><td style='padding:18px;text-align:right;font-weight:900'>{safe(money(order["total_amount"]))} {safe(INVOICE_CURRENCY)}</td></tr></table><div style='margin-top:18px;padding:20px;border-radius:18px;background:linear-gradient(135deg,#24124d,#0d3444);border:1px solid #3b4b75'><div style='color:#aebbd0;font-size:12px;letter-spacing:1.2px'>TOTAL PAID</div><div style='font-size:30px;font-weight:950;margin-top:5px'>{safe(money(order["total_amount"]))} {safe(INVOICE_CURRENCY)}</div><div style='color:#9fb0c2;font-size:13px;margin-top:8px'>Payment: {payment} • Customer: {safe(user["email"] or "Email not provided")}</div></div><div style='margin-top:25px;text-align:center'><a href='{safe(pdf_url)}' style='display:inline-block;background:#22c55e;color:#04110a;text-decoration:none;padding:15px 26px;border-radius:13px;font-weight:900'>📥 Download PDF Invoice</a>{tg_btn}</div><div style='margin-top:28px;padding-top:20px;border-top:1px solid #20364d;color:#7f95ab;font-size:12px;line-height:1.8;text-align:center'>Thank you for choosing {safe(BOT_NAME)}.<br>Support: {safe(SUPPORT_USERNAME)}<br>Original store receipt • Generated automatically</div></div></div></body></html>"""


def _smtp_password_value() -> str:
    password = str(SMTP_PASSWORD or "")
    # Gmail displays app passwords grouped with spaces; smtplib needs the raw 16 chars.
    if "gmail.com" in str(SMTP_HOST or "").lower():
        password = password.replace(" ", "")
    return password.strip()


def _smtp_send_message(msg: EmailMessage) -> None:
    """Send mail through either SMTP/STARTTLS (587) or SMTP_SSL (465)."""
    host = str(SMTP_HOST or "").strip()
    user = str(SMTP_USER or "").strip()
    password = _smtp_password_value()
    if not host or not user or not password:
        raise RuntimeError("SMTP is not fully configured")
    port = int(SMTP_PORT or 587)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as server:
            server.login(user, password)
            server.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(user, password)
        server.send_message(msg)


async def send_email_verification_code(uid: int, email: str, code: str) -> bool:
    """Send a 6-digit verification code to the customer's email."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        logging.error("Email verification requested but SMTP settings are incomplete")
        return False
    body = f"""<!doctype html><html><body style='margin:0;background:#07111f;font-family:Arial,sans-serif;color:#eaf2ff;padding:32px'><div style='max-width:620px;margin:auto;background:#0d1b2a;border:1px solid #1e3a56;border-radius:24px;overflow:hidden'><div style='padding:28px 30px;background:linear-gradient(135deg,#7c3aed,#2563eb,#06b6d4);color:#fff'><div style='font-size:12px;letter-spacing:2px'>EMAIL VERIFICATION</div><div style='font-size:30px;font-weight:800;margin-top:8px'>{html.escape(BOT_NAME)}</div></div><div style='padding:30px'><h2>Your verification code</h2><p style='color:#b7c7d9;line-height:1.7'>Use this code in the bot to verify <b>{html.escape(email)}</b> and receive invoices by email.</p><div style='text-align:center;margin:30px 0;padding:18px;border-radius:16px;background:#102033;border:1px solid #2a4a6a'><div style='font-size:36px;font-weight:900;letter-spacing:8px;color:#22c55e'>{html.escape(code)}</div></div><p style='color:#7f95ab;font-size:12px'>This code expires in {EMAIL_CODE_TTL_MINUTES} minutes. If you did not request it, ignore this email.</p></div></div></body></html>"""

    def _send():
        msg = EmailMessage()
        msg["Subject"] = f"{BOT_NAME} • Verification Code: {code}"
        msg["From"] = SMTP_FROM or SMTP_USER
        msg["To"] = email
        msg.set_content(
            f"Your {BOT_NAME} verification code is: {code}\n\n"
            f"This code expires in {EMAIL_CODE_TTL_MINUTES} minutes."
        )
        msg.add_alternative(body, subtype="html")
        _smtp_send_message(msg)

    try:
        await asyncio.to_thread(_send)
        return True
    except Exception:
        logging.exception("Verification code email failed for uid=%s email=%s", uid, email)
        return False


async def send_invoice_email(order, bot_username: str | None = None) -> bool:
    user = get_user(order["telegram_id"])
    if not user or not user["email"] or not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        return False
    if not bot_username and BOT_INSTANCE:
        with suppress(Exception):
            me = await BOT_INSTANCE.get_me()
            bot_username = me.username
    pdf_url = ""
    if PUBLIC_BASE_URL and order.get("invoice_pdf_token"):
        pdf_url = f"{PUBLIC_BASE_URL.rstrip('/')}/invoice/{order['invoice_pdf_token']}"
    body = invoice_html(order, user, pdf_url or "#", bot_username)
    pdf_bytes = invoice_pdf_bytes(order, user)

    def _send():
        msg = EmailMessage()
        msg["Subject"] = f"{BOT_NAME} Invoice - {order['order_id']}"
        msg["From"] = SMTP_FROM or SMTP_USER
        msg["To"] = user["email"]
        text = (
            f"Invoice {order['order_id']}\n"
            f"Total: {order['total_amount']} {INVOICE_CURRENCY}\n"
            f"Thank you for choosing {BOT_NAME}."
        )
        if pdf_url:
            text += f"\nPDF: {pdf_url}"
        msg.set_content(text)
        msg.add_alternative(body, subtype="html")
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=f"invoice-{order['order_id']}.pdf",
        )
        _smtp_send_message(msg)

    try:
        await asyncio.to_thread(_send)
        return True
    except Exception:
        logging.exception("Invoice email failed")
        return False


# =========================
# TRANSLATIONS
# =========================

TRANSLATIONS = {
    "en": {
        "welcome":"✨ <b>Welcome to {bot}</b>", "balance":"👛 Balance: <b>{amount} {currency}</b>", "choose":"👇 Choose an option:",
        "shop":"🛒 Shop", "topup":"💰 Topup Wallet", "settings":"⚙️ Settings", "support":"🎧 Support", "channel":"📢 Channel",
        "back":"◀ Back", "back_products":"◀️ Back to Products", "refresh":"🔄 Refresh", "prev":"⬅ Prev", "next":"Next ➡",
        "buy":"🛒 Buy Now", "custom_qty":"🔢 Custom Quantity", "copy_link":"🔗 Copy Link", "view_note":"📝 View Note",
        "available_products":"🛍 <b>Available Products</b>", "select_product":"👇 Select a product:",
        "price":"💵 Price", "stock":"📦 Available Stock", "warranty":"🛡️ Warranty", "selected":"🔢 Selected Qty", "total":"🧾 Total", "wallet":"👛 Wallet Balance",
        "not_found":"Product not found.", "note":"Note", "no_note":"No note added yet.", "current":"Current", "clear":"Clear", "max":"Max", "confirm":"✅ Confirm",
        "email_required":"⚠️ <b>Email Required</b>\n\nPlease set your email before checkout.", "set_email":"📧 Set Email", "not_enough":"Not enough stock for this quantity.",
        "select_payment":"💳 <b>Select Payment Method</b>", "quantity":"🔢 Quantity", "direct":"⚡ Direct Pay", "wallet_pay":"👛 Wallet Balance",
        "order_unavailable":"Order unavailable.", "insufficient":"Insufficient wallet balance.", "wallet_success":"✅ <b>Wallet Payment Successful</b>", "paid":"Paid",
        "payment_unavailable":"Payment service unavailable.", "payment_inactive":"Binance Pay / USDT BEP20 is not active.", "amount":"Amount", "auto_webhook":"Payment confirms automatically after webhook verification.",
        "topup_title":"💰 <b>Topup Wallet</b>", "topup_prompt":"Send the amount in {currency}.\nAfter that you can choose Binance Pay or USDT BEP20.", "positive":"❌ Send a valid positive amount.",
        "methods_unavailable":"Payment methods unavailable.", "session_expired":"Payment session expired.", "invoice_failed":"Could not create invoice.", "open_qr":"📷 Open QR",
        "cancel_invoice":"❌ Cancel Invoice", "payment_invoice":"🧾 <b>Payment Invoice</b>", "invoice":"Invoice", "pay_exactly":"💵 Pay exactly", "network":"🌐 Network", "address":"📍 Address", "expires":"⏱ Expires",
        "auto_confirm":"✅ Payment confirmation is automatic. No Check Status button is required.", "invoice_cancelled":"❎ <b>Invoice Cancelled</b>",
        "profile":"⚙️ <b>User Profile</b>", "first_name":"🪪 First Name", "username":"👤 Username", "status":"🚀 Status", "started":"started bot", "email":"📧 Email",
        "currency":"🪙 Currency", "language":"🌐 Language", "region":"🗺️ Region", "joined":"📅 Joined", "not_set":"Not set", "region_missing":"<i>not set</i> — tap <b>Set Region</b>", "saved":" — Has been Saved! ✨",
        "my_orders":"📦 My Orders", "email_btn":"📧 Email", "language_btn":"🌐 Language", "set_region":"🗺️ Set Region", "choose_region":"🗺️ <b>Choose Your Region</b>\n\nSelect your region below:",
        "no_orders":"No orders yet.", "order_status":"Status", "select_language":"🌐 <b>Select Language</b>\n\nChoose your preferred language:", "unsupported":"Unsupported language.",
        "email_settings":"📧 <b>Email Settings</b>", "current_email":"Current Email", "change_email":"✏️ Change Email", "delete_email":"🗑️ Delete Email", "back_settings":"◀️ Back to Settings",
        "send_email":"📧 <b>Send your email address.</b>", "invalid_email":"❌ Invalid email. Send a valid email address.", "email_saved":"✅ <b>Email Saved</b>", "email_deleted":"✅ <b>Email Deleted</b>",
        "channel_missing":"Public channel is not configured yet.", "support_text":"🎧 <b>Support</b>\n\nContact: @{username}", "invoice_sent":"📧 HTML invoice sent to your email. The email contains a secure PDF download button."
    },
    "hi": {
        "welcome":"✨ <b>{bot} में आपका स्वागत है</b>", "balance":"👛 बैलेंस: <b>{amount} {currency}</b>", "choose":"👇 एक विकल्प चुनें:",
        "shop":"🛒 शॉप", "topup":"💰 वॉलेट टॉपअप", "settings":"⚙️ सेटिंग्स", "support":"🎧 सहायता", "channel":"📢 चैनल", "back":"◀ वापस", "back_products":"◀️ प्रोडक्ट्स पर वापस",
        "refresh":"🔄 रिफ्रेश", "prev":"⬅ पिछला", "next":"अगला ➡", "buy":"🛒 अभी खरीदें", "custom_qty":"🔢 कस्टम क्वांटिटी", "copy_link":"🔗 लिंक कॉपी", "view_note":"📝 नोट देखें",
        "available_products":"🛍 <b>उपलब्ध प्रोडक्ट्स</b>", "select_product":"👇 एक प्रोडक्ट चुनें:", "price":"💵 कीमत", "stock":"📦 उपलब्ध स्टॉक", "warranty":"🛡️ वारंटी", "selected":"🔢 चुनी मात्रा", "total":"🧾 कुल", "wallet":"👛 वॉलेट बैलेंस",
        "not_found":"प्रोडक्ट नहीं मिला।", "note":"नोट", "no_note":"अभी कोई नोट नहीं है।", "current":"वर्तमान", "clear":"साफ़", "max":"अधिकतम", "confirm":"✅ कन्फर्म",
        "email_required":"⚠️ <b>ईमेल आवश्यक है</b>\n\nचेकआउट से पहले ईमेल सेट करें।", "set_email":"📧 ईमेल सेट करें", "not_enough":"पर्याप्त स्टॉक नहीं है।", "select_payment":"💳 <b>पेमेंट मेथड चुनें</b>", "quantity":"🔢 मात्रा", "direct":"⚡ डायरेक्ट पे", "wallet_pay":"👛 वॉलेट बैलेंस",
        "order_unavailable":"ऑर्डर उपलब्ध नहीं है।", "insufficient":"वॉलेट बैलेंस पर्याप्त नहीं है।", "wallet_success":"✅ <b>वॉलेट पेमेंट सफल</b>", "paid":"भुगतान", "payment_unavailable":"पेमेंट सेवा उपलब्ध नहीं है।", "payment_inactive":"Binance Pay / USDT BEP20 सक्रिय नहीं है।", "amount":"राशि", "auto_webhook":"Webhook verification के बाद payment automatic confirm होगा।",
        "topup_title":"💰 <b>वॉलेट टॉपअप</b>", "topup_prompt":"{currency} में राशि भेजें।\nफिर Binance Pay या USDT BEP20 चुनें।", "positive":"❌ सही positive amount भेजें।", "methods_unavailable":"पेमेंट मेथड उपलब्ध नहीं हैं।", "session_expired":"पेमेंट सेशन समाप्त हो गया।", "invoice_failed":"इनवॉइस नहीं बन सका।", "open_qr":"📷 QR खोलें", "cancel_invoice":"❌ इनवॉइस कैंसल",
        "payment_invoice":"🧾 <b>पेमेंट इनवॉइस</b>", "invoice":"इनवॉइस", "pay_exactly":"💵 ठीक इतना भुगतान करें", "network":"🌐 नेटवर्क", "address":"📍 एड्रेस", "expires":"⏱ समाप्ति", "auto_confirm":"✅ पेमेंट confirmation automatic है।", "invoice_cancelled":"❎ <b>इनवॉइस कैंसल</b>",
        "profile":"⚙️ <b>यूज़र प्रोफाइल</b>", "first_name":"🪪 नाम", "username":"👤 यूज़रनेम", "status":"🚀 स्टेटस", "started":"bot शुरू किया", "email":"📧 ईमेल", "currency":"🪙 करेंसी", "language":"🌐 भाषा", "region":"🗺️ क्षेत्र", "joined":"📅 जुड़ने की तारीख", "not_set":"सेट नहीं", "region_missing":"<i>सेट नहीं</i> — <b>Set Region</b> दबाएँ", "saved":" — सेव है ✨",
        "my_orders":"📦 मेरे ऑर्डर", "email_btn":"📧 ईमेल", "language_btn":"🌐 भाषा", "set_region":"🗺️ क्षेत्र सेट करें", "choose_region":"🗺️ <b>अपना क्षेत्र चुनें</b>\n\nनीचे से चुनें:", "no_orders":"अभी कोई ऑर्डर नहीं।", "order_status":"स्टेटस", "select_language":"🌐 <b>भाषा चुनें</b>\n\nअपनी पसंदीदा भाषा चुनें:", "unsupported":"यह भाषा समर्थित नहीं है।",
        "email_settings":"📧 <b>ईमेल सेटिंग्स</b>", "current_email":"वर्तमान ईमेल", "change_email":"✏️ ईमेल बदलें", "delete_email":"🗑️ ईमेल हटाएँ", "back_settings":"◀️ सेटिंग्स पर वापस", "send_email":"📧 <b>अपना ईमेल एड्रेस भेजें।</b>", "invalid_email":"❌ सही ईमेल भेजें।", "email_saved":"✅ <b>ईमेल सेव हुआ</b>", "email_deleted":"✅ <b>ईमेल हटाया गया</b>", "channel_missing":"Public channel configure नहीं है।", "support_text":"🎧 <b>सहायता</b>\n\nसंपर्क: @{username}", "invoice_sent":"📧 HTML invoice आपके ईमेल पर भेजा गया है, साथ में secure PDF download button है।"
    },
    "ur": {}, "ar": {}, "es": {}, "id": {}
}

# Fill missing languages from English first, then override core UI texts.
for _code in ("ur", "ar", "es", "id"):
    TRANSLATIONS[_code] = dict(TRANSLATIONS["en"])

TRANSLATIONS["ur"].update({"welcome":"✨ <b>{bot} میں خوش آمدید</b>","balance":"👛 بیلنس: <b>{amount} {currency}</b>","choose":"👇 ایک آپشن منتخب کریں:","shop":"🛒 شاپ","topup":"💰 والٹ ٹاپ اپ","settings":"⚙️ سیٹنگز","support":"🎧 سپورٹ","channel":"📢 چینل","back":"◀ واپس","back_products":"◀️ پروڈکٹس پر واپس","refresh":"🔄 ریفریش","buy":"🛒 ابھی خریدیں","custom_qty":"🔢 کسٹم مقدار","copy_link":"🔗 لنک کاپی","view_note":"📝 نوٹ دیکھیں","available_products":"🛍 <b>دستیاب پروڈکٹس</b>","select_product":"👇 ایک پروڈکٹ منتخب کریں:","price":"💵 قیمت","stock":"📦 دستیاب اسٹاک","warranty":"🛡️ وارنٹی","selected":"🔢 منتخب مقدار","total":"🧾 کل","wallet":"👛 والٹ بیلنس","profile":"⚙️ <b>یوزر پروفائل</b>","my_orders":"📦 میرے آرڈرز","language_btn":"🌐 زبان","set_region":"🗺️ علاقہ سیٹ کریں","select_language":"🌐 <b>زبان منتخب کریں</b>\n\nاپنی پسند کی زبان منتخب کریں:"})
TRANSLATIONS["ar"].update({"welcome":"✨ <b>مرحبًا بك في {bot}</b>","balance":"👛 الرصيد: <b>{amount} {currency}</b>","choose":"👇 اختر خيارًا:","shop":"🛒 المتجر","topup":"💰 شحن المحفظة","settings":"⚙️ الإعدادات","support":"🎧 الدعم","channel":"📢 القناة","back":"◀ رجوع","back_products":"◀️ العودة للمنتجات","refresh":"🔄 تحديث","buy":"🛒 اشترِ الآن","custom_qty":"🔢 كمية مخصصة","copy_link":"🔗 نسخ الرابط","view_note":"📝 عرض الملاحظة","available_products":"🛍 <b>المنتجات المتاحة</b>","select_product":"👇 اختر منتجًا:","price":"💵 السعر","stock":"📦 المخزون المتاح","warranty":"🛡️ الضمان","selected":"🔢 الكمية المختارة","total":"🧾 الإجمالي","wallet":"👛 رصيد المحفظة","profile":"⚙️ <b>ملف المستخدم</b>","my_orders":"📦 طلباتي","language_btn":"🌐 اللغة","set_region":"🗺️ تحديد المنطقة","select_language":"🌐 <b>اختر اللغة</b>\n\nاختر لغتك المفضلة:"})
TRANSLATIONS["es"].update({"welcome":"✨ <b>Bienvenido a {bot}</b>","balance":"👛 Saldo: <b>{amount} {currency}</b>","choose":"👇 Elige una opción:","shop":"🛒 Tienda","topup":"💰 Recargar saldo","settings":"⚙️ Ajustes","support":"🎧 Soporte","channel":"📢 Canal","back":"◀ Volver","back_products":"◀️ Volver a productos","refresh":"🔄 Actualizar","buy":"🛒 Comprar ahora","custom_qty":"🔢 Cantidad personalizada","copy_link":"🔗 Copiar enlace","view_note":"📝 Ver nota","available_products":"🛍 <b>Productos disponibles</b>","select_product":"👇 Elige un producto:","price":"💵 Precio","stock":"📦 Stock disponible","warranty":"🛡️ Garantía","selected":"🔢 Cantidad elegida","total":"🧾 Total","wallet":"👛 Saldo de cartera","profile":"⚙️ <b>Perfil de usuario</b>","my_orders":"📦 Mis pedidos","language_btn":"🌐 Idioma","set_region":"🗺️ Configurar región","select_language":"🌐 <b>Selecciona idioma</b>\n\nElige tu idioma preferido:"})
TRANSLATIONS["id"].update({"welcome":"✨ <b>Selamat datang di {bot}</b>","balance":"👛 Saldo: <b>{amount} {currency}</b>","choose":"👇 Pilih opsi:","shop":"🛒 Toko","topup":"💰 Isi saldo","settings":"⚙️ Pengaturan","support":"🎧 Dukungan","channel":"📢 Channel","back":"◀ Kembali","back_products":"◀️ Kembali ke produk","refresh":"🔄 Refresh","buy":"🛒 Beli sekarang","custom_qty":"🔢 Jumlah custom","copy_link":"🔗 Salin link","view_note":"📝 Lihat catatan","available_products":"🛍 <b>Produk tersedia</b>","select_product":"👇 Pilih produk:","price":"💵 Harga","stock":"📦 Stok tersedia","warranty":"🛡️ Garansi","selected":"🔢 Jumlah dipilih","total":"🧾 Total","wallet":"👛 Saldo wallet","profile":"⚙️ <b>Profil pengguna</b>","my_orders":"📦 Pesanan saya","language_btn":"🌐 Bahasa","set_region":"🗺️ Atur wilayah","select_language":"🌐 <b>Pilih bahasa</b>\n\nPilih bahasa yang diinginkan:"})

def user_language(uid):
    if not uid:
        return "en"
    uid = int(uid)
    now = time.monotonic()
    hit = _LANG_CACHE.get(uid)
    if hit and now < hit[0]:
        return hit[1]
    try:
        u = get_user(uid)
        code = str((u["language"] if u else "en") or "en").lower()
        code = code if code in TRANSLATIONS else "en"
    except Exception:
        code = "en"
    _LANG_CACHE[uid] = (now + LANG_CACHE_TTL, code)
    return code

def tr(uid, key: str, **kwargs):
    lang = user_language(uid)
    value = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"].get(key, key))
    return value.format(**kwargs) if kwargs else value


def tr_user(user, key: str, **kwargs):
    try:
        lang = str((user["language"] if user else "en") or "en").lower()
    except Exception:
        lang = "en"
    if lang not in TRANSLATIONS:
        lang = "en"
    value = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"].get(key, key))
    return value.format(**kwargs) if kwargs else value


def add_api_provider(data: dict) -> int:
    with db() as con:
        cur = con.execute(
            """INSERT INTO api_providers(name,base_url,api_key,auth_header,products_endpoint,balance_endpoint,order_endpoint,status_endpoint)
               VALUES (?,?,?,?,?,?,?,?)""",
            (data["name"], data["base_url"].rstrip("/"), data["api_key"], data["auth_header"],
             data["products_endpoint"], data["balance_endpoint"], data["order_endpoint"], data["status_endpoint"]),
        )
        con.commit()
        return int(cur.lastrowid)


def list_api_providers():
    with db() as con:
        return con.execute("SELECT * FROM api_providers ORDER BY id ASC").fetchall()



# =========================
# UI HELPERS
# =========================

def is_admin(uid: int) -> bool:
    return ADMIN_ID and uid == ADMIN_ID



def _blocked_cached(uid: int) -> bool:
    uid = int(uid)
    now = time.monotonic()
    hit = _BLOCK_CACHE.get(uid)
    if hit and now < hit[0]:
        return bool(hit[1])
    user = get_user(uid)
    blocked = bool(user and int(user["blocked"] or 0))
    _BLOCK_CACHE[uid] = (now + BLOCK_CACHE_TTL, blocked)
    return blocked


class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        uid = getattr(getattr(event, "from_user", None), "id", 0)
        if uid and not is_admin(uid) and _blocked_cached(uid):
            if isinstance(event, CallbackQuery):
                with suppress(Exception):
                    await event.answer("🚫 You are banned from using this bot.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("🚫 You are banned from using this bot.")
            return
        return await handler(event, data)
router.message.outer_middleware(BanMiddleware())
router.callback_query.outer_middleware(BanMiddleware())


def main_kb(uid=None):
    channel_button = InlineKeyboardButton(text="📢 Channels", callback_data="menu:channel", style="success")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Shop", callback_data="menu:products", style="success")],
        [
            InlineKeyboardButton(text="💰 Topup Wallet", callback_data="menu:topup", style="primary"),
            InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings", style="success"),
        ],
        [
            InlineKeyboardButton(text="💳 Balance", callback_data="menu:balance", style="primary"),
            InlineKeyboardButton(text="🎁 Refer & Earn", callback_data="menu:referral", style="success"),
        ],
        [
            InlineKeyboardButton(text="🎧 Support", callback_data="menu:support", style="primary"),
            channel_button,
        ],
        [InlineKeyboardButton(text="❓ Help / How to Buy", callback_data="menu:help", style="primary")],
    ])


def back_home(uid=None):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(uid,"back"), callback_data="menu:home", style="danger")]])


async def edit(callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    """Update the existing bot message in-place.

    Important: pressing Refresh must never create a duplicate message.
    Telegram raises "message is not modified" when content is unchanged;
    we simply acknowledge the callback instead of sending a new message.
    """
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logging.warning("Could not edit UI message: %s", exc)
        except Exception:
            logging.exception("Could not update UI message")
    with suppress(Exception):
        await callback.answer()


def welcome_text(user) -> str:
    return tr_user(user,"welcome",bot=html.escape(BOT_NAME))+"\n\n"+tr_user(user,"balance",amount=money(user["wallet"]),currency=INVOICE_CURRENCY)+"\n"+tr_user(user,"choose")


def product_icon(name: str) -> str:
    """Return a compact brand-like emoji for common digital products.

    Telegram inline buttons cannot embed a normal image logo, so we use a
    recognizable emoji/symbol based on the product name.
    """
    n = (name or "").lower()
    if "youtube" in n:
        return "▶️"
    if "netflix" in n:
        return "🎬"
    if "chatgpt" in n or "openai" in n:
        return "🤖"
    if "gemini" in n:
        return "✨"
    if "canva" in n:
        return "🎨"
    if "capcut" in n:
        return "✂️"
    if "nord" in n or "vpn" in n:
        return "🛡️"
    if "surfshark" in n:
        return "🌊"
    if "quillbot" in n:
        return "✍️"
    if "coursera" in n:
        return "🎓"
    if "spotify" in n:
        return "🎵"
    if "telegram" in n:
        return "✈️"
    return "🛍"


def compact_product_button(p, stock: int) -> str:
    """Build a full product button label.

    Format example (same style as popular shops):
    ✨ Gemini Pro 18M Links - $1.6 (Stock: 31)

    Always keep price + stock fully visible. If too long, shorten the name only.
    Telegram inline-button text hard limit is 64 characters.
    """
    name = str(p["name"]).strip()
    icon = product_icon(name)
    price = money(p["price"])
    suffix = f" - ${price} (Stock: {stock})"
    # emoji + space + name + suffix must fit in 64
    prefix = f"{icon} "
    max_name_len = 64 - len(prefix) - len(suffix)
    if max_name_len < 8:
        # Extreme fallback for very long prices
        suffix = f" - ${price} (S:{stock})"
        max_name_len = 64 - len(prefix) - len(suffix)
    if len(name) > max_name_len:
        name = name[: max(1, max_name_len - 1)] + "…"
    return f"{prefix}{name}{suffix}"

def product_catalog_entries():
    """Build customer catalog with ONE product+stock query (no per-product stock queries)."""
    products = list_products_with_stock()
    entries = []
    groups = {}

    for p in products:
        category = str(p["category"] or "").strip()
        if category:
            key = category.casefold()
            groups.setdefault(key, {"name": category, "items": []})["items"].append(p)
        else:
            entries.append({
                "kind": "product", "pid": int(p["id"]), "name": str(p["name"]),
                "stock": int(p.get("stock", 0) or 0), "count": 1,
                "order": int(p["display_order"]), "product": p,
            })

    for g in groups.values():
        items = sorted(g["items"], key=lambda x: (int(x["display_order"]), int(x["id"])))
        entries.append({
            "kind": "group", "pid": int(items[0]["id"]), "name": g["name"],
            "stock": sum(int(x.get("stock", 0) or 0) for x in items),
            "count": len(items), "order": int(items[0]["display_order"]),
        })

    entries.sort(key=lambda x: (x["order"], x["pid"]))
    return entries


def group_products_by_pid(pid: int):
    return group_products_with_stock(pid)


def products_kb(page:int, uid=None):
    entries = product_catalog_entries()
    pages = max(1, ceil(len(entries) / PRODUCTS_PER_PAGE))
    page = min(max(page, 1), pages)
    start = (page - 1) * PRODUCTS_PER_PAGE
    chunk = entries[start:start + PRODUCTS_PER_PAGE]
    rows = []

    for e in chunk:
        if e["kind"] == "group":
            label = f"📂 {e['name']} ({e['count']} plans) - (Stock: {e['stock']})"
            if len(label) > 64:
                label = label[:64]
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"group:{e['pid']}",
                    style="success" if e["stock"] > 0 else "danger",
                )
            ])
        else:
            p = e.get("product")
            if not p:
                continue
            rows.append([
                InlineKeyboardButton(
                    text=compact_product_button(p, e["stock"]),
                    callback_data=f"product:{e['pid']}:1",
                    style="success" if e["stock"] > 0 else "danger",
                )
            ])

    if pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text=tr(uid,"prev"), callback_data=f"products:{page-1}", style="primary"))
        if page < pages:
            nav.append(InlineKeyboardButton(text=tr(uid,"next"), callback_data=f"products:{page+1}", style="primary"))
        if nav:
            rows.append(nav)

    rows.append([
        InlineKeyboardButton(text=tr(uid,"refresh"), callback_data=f"products:{page}", style="success"),
        InlineKeyboardButton(text=f"📊 {page}/{pages}", callback_data="noop", style="primary"),
        InlineKeyboardButton(text=tr(uid,"back"), callback_data="menu:home", style="danger"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows), page, pages


def group_products_kb(pid: int, uid=None):
    variants = group_products_by_pid(pid)
    if not variants:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr(uid,"back_products"), callback_data="menu:products", style="danger")]
        ])
    rows = []
    for p in variants:
        s = int(p.get("stock", 0) or 0)
        rows.append([
            InlineKeyboardButton(
                text=compact_product_button(p, s),
                callback_data=f"product:{p['id']}:1",
                style="success" if s > 0 else "danger",
            )
        ])
    rows.append([
        InlineKeyboardButton(text=tr(uid,"back_products"), callback_data="menu:products", style="danger")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def purchase_confirm_kb(pid: int, qty: int, uid=None):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Purchase", callback_data=f"purchase:confirm:{pid}:{qty}", style="success")],
        [InlineKeyboardButton(text="✏️ Change Quantity", callback_data=f"product:{pid}:{qty}", style="primary")],
        [InlineKeyboardButton(text="🛒 Back to Shop", callback_data="menu:products", style="danger")],
    ])


async def show_purchase_confirmation(callback: CallbackQuery, pid: int, qty: int):
    product = get_product(pid)
    user = get_user(callback.from_user.id)
    if not product or not user:
        return await callback.answer(tr(callback.from_user.id,"not_found"), show_alert=True)
    if stock_count(pid) < qty:
        return await callback.answer(tr(callback.from_user.id,"not_enough"), show_alert=True)
    total = Decimal(str(product["price"])) * qty
    row = get_email_verification(callback.from_user.id)
    email_line = "📧 Email: <b>Not set</b>"
    if row and row["email"]:
        email_line = f"📧 Email: <b>{html.escape(row['email'])}</b> • {'✅ Verified' if int(row['email_verified'] or 0) else '⚠️ Not verified'}"
    await edit(callback,
        "🧾 <b>Confirm Your Purchase</b>\n\n"
        f"📦 Product: <b>{html.escape(product['name'])}</b>\n"
        f"🔢 Quantity: <b>{qty}</b>\n"
        f"💵 Unit Price: <b>{money(product['price'])} {INVOICE_CURRENCY}</b>\n"
        f"🧾 Total: <b>{money(total)} {INVOICE_CURRENCY}</b>\n"
        f"👛 Wallet Balance: <b>{money(user['wallet'])} {INVOICE_CURRENCY}</b>\n"
        f"{email_line}\n\n"
        "Please confirm before continuing to payment.",
        purchase_confirm_kb(pid, qty, callback.from_user.id),
    )


def product_kb(pid:int,qty:int,bot_username:str|None=None,uid=None):
    rows=[[InlineKeyboardButton(text="➖",callback_data=f"qty:-:{pid}:{qty}",style="danger"),InlineKeyboardButton(text=f"📦 {qty}",callback_data="noop",style="primary"),InlineKeyboardButton(text="➕",callback_data=f"qty:+:{pid}:{qty}",style="success")],[InlineKeyboardButton(text=tr(uid,"buy"),callback_data=f"buy:{pid}:{qty}",style="success")],[InlineKeyboardButton(text=tr(uid,"refresh"),callback_data=f"product:{pid}:{qty}",style="primary"),InlineKeyboardButton(text=tr(uid,"custom_qty"),callback_data=f"custom:{pid}:{qty}",style="primary")]]
    if bot_username:
        link=f"https://t.me/{bot_username}?start=product_{pid}"; rows.append([InlineKeyboardButton(text=tr(uid,"copy_link"),copy_text=CopyTextButton(text=link),style="primary"),InlineKeyboardButton(text=tr(uid,"view_note"),callback_data=f"note:{pid}:{qty}",style="primary")])
    else: rows.append([InlineKeyboardButton(text=tr(uid,"view_note"),callback_data=f"note:{pid}:{qty}",style="primary")])
    rows.append([InlineKeyboardButton(text=tr(uid,"back_products"),callback_data="menu:products",style="danger")]); return InlineKeyboardMarkup(inline_keyboard=rows)


def qty_calc_kb(pid:int,value:str,uid=None):
    def b(t,d,style="primary"): return InlineKeyboardButton(text=t,callback_data=d,style=style)
    return InlineKeyboardMarkup(inline_keyboard=[[b("1",f"qcalc:{pid}:1"),b("2",f"qcalc:{pid}:2"),b("3",f"qcalc:{pid}:3")],[b("4",f"qcalc:{pid}:4"),b("5",f"qcalc:{pid}:5"),b("6",f"qcalc:{pid}:6")],[b("7",f"qcalc:{pid}:7"),b("8",f"qcalc:{pid}:8"),b("9",f"qcalc:{pid}:9")],[b("⌫",f"qcalc:{pid}:back"),b("0",f"qcalc:{pid}:0"),b(tr(uid,"clear"),f"qcalc:{pid}:clear","danger")],[b("25",f"qcalc:{pid}:set25"),b("50",f"qcalc:{pid}:set50"),b("100",f"qcalc:{pid}:set100")],[b(tr(uid,"max"),f"qcalc:{pid}:max","success")],[b(tr(uid,"confirm"),f"qconfirm:{pid}","success"),b(tr(uid,"back"),f"product:{pid}:1","danger")]])


def settings_kb(uid=None):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 My Orders", callback_data="settings:orders", style="success"),
         InlineKeyboardButton(text="📧 Email Settings", callback_data="settings:email", style="primary")],
        [InlineKeyboardButton(text="🌍 Set Region", callback_data="settings:region", style="primary")],
        [InlineKeyboardButton(text="🌐 Language", callback_data="settings:language", style="primary")],
        [InlineKeyboardButton(text="🤖 AI Chat", callback_data="menu:ai", style="success")],
        [InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")],
    ])


def language_kb(uid=None):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇬🇧 English",callback_data="lang:en",style="primary"),InlineKeyboardButton(text="🇮🇳 हिन्दी",callback_data="lang:hi",style="primary")],[InlineKeyboardButton(text="🇵🇰 اردو",callback_data="lang:ur",style="primary"),InlineKeyboardButton(text="🇸🇦 العربية",callback_data="lang:ar",style="primary")],[InlineKeyboardButton(text="🇪🇸 Español",callback_data="lang:es",style="primary"),InlineKeyboardButton(text="🇮🇩 Indonesia",callback_data="lang:id",style="primary")],[InlineKeyboardButton(text=tr(uid,"back_settings"),callback_data="menu:settings",style="danger")]])


def region_kb(uid=None):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇧🇩 Bangladesh",callback_data="region:Bangladesh",style="primary"),InlineKeyboardButton(text="🇮🇳 India",callback_data="region:India",style="primary")],[InlineKeyboardButton(text="🇵🇰 Pakistan",callback_data="region:Pakistan",style="primary"),InlineKeyboardButton(text="🇺🇸 USA",callback_data="region:USA",style="primary")],[InlineKeyboardButton(text="🇬🇧 UK",callback_data="region:UK",style="primary"),InlineKeyboardButton(text="🌍 Other",callback_data="region:Other",style="primary")],[InlineKeyboardButton(text=tr(uid,"back_settings"),callback_data="menu:settings",style="danger")]])


def email_kb(uid=None):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(uid,"set_email"),callback_data="email:set",style="success"),InlineKeyboardButton(text=tr(uid,"change_email"),callback_data="email:change",style="primary")],[InlineKeyboardButton(text=tr(uid,"delete_email"),callback_data="email:delete",style="danger")],[InlineKeyboardButton(text=tr(uid,"back_settings"),callback_data="menu:settings",style="danger")]])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Add Product", callback_data="admin:add_product", style="success"),
            InlineKeyboardButton(text="📥 Add Stock", callback_data="admin:add_stock", style="primary"),
        ],
        [
            InlineKeyboardButton(text="✏️ Edit Product", callback_data="admin:edit_product", style="primary"),
            InlineKeyboardButton(text="💲 Change Price", callback_data="admin:change_price", style="success"),
        ],
        [
            InlineKeyboardButton(text="🗑 Delete Product", callback_data="admin:delete_product", style="danger"),
            InlineKeyboardButton(text="🧹 Delete Stock", callback_data="admin:delete_stock", style="danger"),
        ],
        [
            InlineKeyboardButton(text="💵 Add Balance", callback_data="admin:add_balance", style="success"),
            InlineKeyboardButton(text="💳 Manage Balance", callback_data="admin:manage_balance", style="primary"),
        ],
        [
            InlineKeyboardButton(text="👥 Users / Ban", callback_data="admin:users", style="danger"),
            InlineKeyboardButton(text="📦 Products", callback_data="admin:products", style="primary"),
        ],
        [
            InlineKeyboardButton(text="📂 Manage Plans", callback_data="admin:plans", style="primary"),
            InlineKeyboardButton(text="↕️ Reorder Products", callback_data="admin:reorder", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🔌 API Manager", callback_data="admin:api_manager", style="primary"),
        ],
        [
            InlineKeyboardButton(text="📣 Broadcast Message", callback_data="admin:broadcast", style="success"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Bot Settings", callback_data="admin:bot_settings", style="success"),
        ],
        [
            InlineKeyboardButton(
                text=("🛠 Maintenance: ON" if maintenance_enabled() else "🛠 Maintenance: OFF"),
                callback_data="admin:maintenance",
                style=("danger" if maintenance_enabled() else "success"),
            )
        ],
        [InlineKeyboardButton(text="◀ Back to Customer", callback_data="menu:home", style="danger")],
    ])



# =========================
# NOTIFICATIONS
# =========================

async def safe_send(bot: Bot, chat_id, text: str, reply_markup=None):
    if not chat_id:
        return False
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
        return True
    except Exception:
        return False


async def check_stock_alert(bot: Bot, pid: int):
    if not ADMIN_ALERT_CHANNEL_ID:
        return
    p = get_product(pid)
    if not p:
        return
    current = stock_count(pid)
    threshold = next((x for x in sorted(LOW_STOCK_THRESHOLDS) if current <= x), None)
    if threshold is None:
        return
    with db() as con:
        exists = con.execute("SELECT 1 FROM stock_alerts WHERE product_id=? AND threshold=?", (pid, threshold)).fetchone()
        if exists:
            return
        con.execute("INSERT INTO stock_alerts(product_id, threshold) VALUES (?,?)", (pid, threshold))
        con.commit()
    if current == 0:
        text = f"🚨 <b>OUT OF STOCK</b>\n\n📦 {html.escape(p['name'])}\n📊 Remaining Stock: <b>0</b>\n\n📥 Please add new stock."
    else:
        text = f"⚠️ <b>LOW STOCK ALERT</b>\n\n📦 {html.escape(p['name'])}\n📊 Remaining Stock: <b>{current}</b>\n💰 Price: <b>{money(p['price'])} {INVOICE_CURRENCY}</b>"
    await safe_send(bot, ADMIN_ALERT_CHANNEL_ID, text)


async def broadcast_new_product(bot: Bot, pid: int):
    p = get_product(pid)
    if not p:
        return
    s = stock_count(pid)
    me = await bot.get_me()
    text = (
        "🆕 <b>NEW PRODUCT ADDED</b>\n\n"
        f"📦 Product: <b>{html.escape(p['name'])}</b>\n"
        f"💰 Price: <b>{money(p['price'])} {INVOICE_CURRENCY}</b>\n"
        f"📊 Stock: <b>{s}</b>\n"
        f"🛡 Warranty: <b>{html.escape(p['warranty'])}</b>"
    )
    user_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Buy Now", callback_data=f"product:{pid}:1", style="danger")]])
    public_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Buy Now", url=f"https://t.me/{me.username}?start=product_{pid}", style="success")]])

    if PUBLIC_CHANNEL_ID:
        await safe_send(bot, PUBLIC_CHANNEL_ID, text, public_kb)

    with db() as con:
        users = con.execute("SELECT telegram_id FROM users WHERE blocked=0").fetchall()
    for row in users:
        try:
            await bot.send_message(row["telegram_id"], text, reply_markup=user_kb)
            await asyncio.sleep(0.04)
        except Exception:
            with db() as con:
                con.execute("UPDATE users SET blocked=1 WHERE telegram_id=?", (row["telegram_id"],))
                con.commit()


async def broadcast_stock_added(bot: Bot, pid: int, added: int):
    """Notify registered customers and the public channel when stock is replenished."""
    if added <= 0:
        return
    p = get_product(pid)
    if not p:
        return
    total = stock_count(pid)
    me = await bot.get_me()
    text = (
        "📦 <b>STOCK UPDATED</b>\n\n"
        f"🛍 Product: <b>{html.escape(p['name'])}</b>\n"
        f"💰 Price: <b>{money(p['price'])} {INVOICE_CURRENCY}</b>\n"
        f"➕ New Stock: <b>{added}</b>\n"
        f"📊 Available Now: <b>{total}</b>\n\n"
        "⚡ Available now — order before stock runs out."
    )
    user_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text="🛒 Buy Now", callback_data=f"product:{pid}:1", style="success"
        )]]
    )
    public_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text="🛒 Buy Now", url=f"https://t.me/{me.username}?start=product_{pid}", style="success"
        )]]
    )

    if PUBLIC_CHANNEL_ID:
        await safe_send(bot, PUBLIC_CHANNEL_ID, text, public_kb)

    with db() as con:
        users = con.execute("SELECT telegram_id FROM users WHERE blocked=0").fetchall()
    for row in users:
        try:
            await bot.send_message(row["telegram_id"], text, reply_markup=user_kb)
            await asyncio.sleep(0.04)
        except Exception:
            with db() as con:
                con.execute("UPDATE users SET blocked=1 WHERE telegram_id=?", (row["telegram_id"],))
                con.commit()


async def broadcast_price_update(bot: Bot, pid: int, old_price: Decimal, new_price: Decimal):
    """Notify customers and the public channel after a real in-stock price change."""
    p = get_product(pid)
    if not p or stock_count(pid) <= 0 or Decimal(str(old_price)) == Decimal(str(new_price)):
        return
    me = await bot.get_me()
    direction = "📉 PRICE DECREASED" if Decimal(str(new_price)) < Decimal(str(old_price)) else "📈 PRICE UPDATED"
    text = (
        f"{direction}\n\n"
        f"📦 Product: <b>{html.escape(p['name'])}</b>\n"
        f"💵 Old Price: <s>{money(old_price)} {INVOICE_CURRENCY}</s>\n"
        f"✨ New Price: <b>{money(new_price)} {INVOICE_CURRENCY}</b>\n"
        f"📊 Stock: <b>{stock_count(pid)}</b>"
    )
    user_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Buy Now", callback_data=f"product:{pid}:1", style="success")]])
    public_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 Buy Now", url=f"https://t.me/{me.username}?start=product_{pid}", style="success")]])
    if PUBLIC_CHANNEL_ID:
        await safe_send(bot, PUBLIC_CHANNEL_ID, text, public_kb)
    with db() as con:
        users = con.execute("SELECT telegram_id FROM users WHERE blocked=0").fetchall()
    for row in users:
        ok = await safe_send(bot, row["telegram_id"], text, user_kb)
        if not ok:
            with db() as con:
                con.execute("UPDATE users SET blocked=1 WHERE telegram_id=?", (row["telegram_id"],))
                con.commit()
        await asyncio.sleep(0.04)


def mask_telegram_id(uid: int) -> str:
    """Hide middle digits of a Telegram ID for public channel privacy."""
    s = str(uid)
    if len(s) <= 6:
        return s[:2] + "***" + s[-2:]
    return s[:3] + "***" + s[-3:]


async def public_purchase_notice(bot: Bot, order):
    if not PUBLIC_CHANNEL_ID:
        return
    uid = int(order["telegram_id"])
    masked_id = mask_telegram_id(uid)
    # Prefer paid_at, fallback to created_at / now
    raw_time = order["paid_at"] or order["created_at"] or datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Show in local-like readable form (UTC+6 style common for BD)
        from datetime import timedelta
        local = dt.astimezone(timezone(timedelta(hours=6)))
        time_str = local.strftime("%d %b %Y %I:%M %p")
    except Exception:
        time_str = str(raw_time)

    me = await bot.get_me()
    product_icon_emoji = product_icon(str(order["product_name"]))
    text = (
        "🛍 <b>New Purchase!</b>\n\n"
        f"🆔 ID: <code>{masked_id}</code>\n"
        f"💰 Product: {product_icon_emoji} <b>{html.escape(order['product_name'])}</b>\n"
        f"🔢 Quantity: <b>{order['quantity']}</b>\n"
        f"✅ Amount: <b>{money(order['total_amount'])} {INVOICE_CURRENCY}</b>\n"
        f"📅 Time: <b>{html.escape(time_str)}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🛒 Buy Now",
            url=f"https://t.me/{me.username}?start=product_{order['product_id']}",
            style="success",
        )
    ]])
    await safe_send(bot, PUBLIC_CHANNEL_ID, text, kb)


# =========================
# ORDER DELIVERY
# =========================

async def complete_paid_order(bot: Bot, oid: str, method: str):
    order = get_order(oid)
    if not order or order["status"] in ("COMPLETED", "REFUNDED"):
        return
    items = take_stock(order["product_id"], int(order["quantity"]), oid)
    if not items:
        update_order(oid, payment_method=method, status="PAID_WAITING_STOCK", paid_at=datetime.now(timezone.utc).isoformat())
        await bot.send_message(order["telegram_id"], f"🕒 <b>Payment received</b>\n\nOrder <code>{oid}</code> is waiting for stock. Delivery will be automatic when stock is added.")
        return

    delivered = "\n".join(f"{i}. <code>{html.escape(x)}</code>" for i, x in enumerate(items, 1))
    update_order(
        oid,
        payment_method=method,
        status="COMPLETED",
        delivered_content=delivered,
        paid_at=datetime.now(timezone.utc).isoformat(),
    )
    product = get_product(order["product_id"])
    copy_raw = "\n".join(items)
    delivery_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Copy Product Details", copy_text=CopyTextButton(text=copy_raw), style="primary")],
        [InlineKeyboardButton(text="📄 Download Invoice", callback_data=f"invoice:download:{oid}", style="success")],
        [InlineKeyboardButton(text="🛒 Go to Shop", callback_data="menu:products", style="primary")],
    ])
    await bot.send_message(
        order["telegram_id"],
        "✅ <b>Purchase Successful!</b>\n\n"
        "📦 Your product has been delivered.\n\n"
        f"🆔 Order: <code>{oid}</code>\n"
        f"📦 Product: <b>{html.escape(order['product_name'])}</b>\n"
        f"🔢 Quantity: <b>{order['quantity']}</b>\n"
        f"💰 Paid: <b>{money(order['total_amount'])} {INVOICE_CURRENCY}</b>\n"
        f"📅 Validity: <b>{html.escape(product['validity'] if product else 'N/A')}</b>\n"
        f"🛡 Warranty: <b>{html.escape(product['warranty'] if product else 'N/A')}</b>\n\n"
        "🔐 <b>YOUR PRODUCT</b>\n"
        f"{delivered}\n\n"
        "⚠️ Please save your product details securely.\n"
        f"❤️ Thank you for choosing <b>{html.escape(BOT_NAME)}</b>!",
        reply_markup=delivery_kb,
    )
    updated = get_order(oid)
    uid = int(order["telegram_id"])

    # Email flow: verified users get invoice automatically; others are offered once
    if is_email_verified(uid):
        sent = await send_invoice_email(updated)
        if sent:
            await bot.send_message(uid, "📧 <b>Invoice sent</b>\n\nYour PDF invoice has been sent to your saved email.")
        else:
            await bot.send_message(
                uid,
                "⚠️ <b>Invoice email failed</b>\n\n"
                "Your product is delivered. You can still download the invoice from the bot.\n"
                "Admin: check SMTP settings if this keeps happening.",
            )
    else:
        # First-time / unverified: ask after delivery
        email_prompt_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📧 Set Email", callback_data=f"email:after:{oid}", style="success")],
            [InlineKeyboardButton(text="⏭ Skip", callback_data=f"email:skip_after:{oid}", style="primary")],
        ])
        await bot.send_message(
            uid,
            "📧 <b>Set your email to receive your invoice?</b>\n\n"
            "If you set and verify an email, a PDF invoice will be sent to you.\n"
            "You can also skip — your product is already delivered.",
            reply_markup=email_prompt_kb,
        )

    await check_stock_alert(bot, order["product_id"])
    await public_purchase_notice(bot, updated)


async def process_waiting_orders(bot: Bot, pid: int):
    while True:
        with db() as con:
            row = con.execute(
                "SELECT * FROM orders WHERE product_id=? AND status='PAID_WAITING_STOCK' ORDER BY id LIMIT 1",
                (pid,),
            ).fetchone()
        if not row or stock_count(pid) < int(row["quantity"]):
            break
        await complete_paid_order(bot, row["order_id"], row["payment_method"] or "PAID")


# =========================


async def required_join_status(bot: Bot, uid: int):
    """Return configured channels the user still needs to join."""
    missing = []
    channels = [
        (CHANNEL_1_ID, CHANNEL_1_URL, CHANNEL_1_NAME),
        (CHANNEL_2_ID, CHANNEL_2_URL, CHANNEL_2_NAME),
    ]

    for channel_id, channel_url, channel_name in channels:
        channel_id = str(channel_id or "").strip()
        channel_url = str(channel_url or "").strip()
        channel_name = str(channel_name or "Channel").strip()

        if not channel_id and not channel_url:
            continue

        if not channel_id:
            missing.append(("", channel_url, channel_name))
            continue

        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=uid)
            status = str(member.status).lower()
            if status not in {"member", "administrator", "creator", "restricted"}:
                missing.append((channel_id, channel_url, channel_name))
        except Exception as exc:
            logging.warning(
                "Join verification failed for user=%s channel=%s: %s",
                uid, channel_id, exc
            )
            missing.append((channel_id, channel_url, channel_name))

    return missing


def join_kb():
    rows = []

    if CHANNEL_1_URL:
        rows.append([
            InlineKeyboardButton(
                text=f"📢 Join {CHANNEL_1_NAME}",
                url=CHANNEL_1_URL,
                style="success",
            )
        ])

    if CHANNEL_2_URL:
        rows.append([
            InlineKeyboardButton(
                text=f"📢 Join {CHANNEL_2_NAME}",
                url=CHANNEL_2_URL,
                style="primary",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="✅ I'm Joined",
            callback_data="join:verify",
            style="success",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⏭ Skip for Now",
            callback_data="join:skip",
            style="primary",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def channels_configured() -> bool:
    return bool(
        str(CHANNEL_1_ID or "").strip()
        or str(CHANNEL_1_URL or "").strip()
        or str(CHANNEL_2_ID or "").strip()
        or str(CHANNEL_2_URL or "").strip()
    )


# CUSTOMER ROUTES
# =========================

@router.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    # One pooled DB checkout instead of get -> register -> get on separate
    # connections. This materially improves /start latency on Render + Neon.
    user, is_first_start = register_user_and_get(message.from_user)

    args = (message.text or "").split(maxsplit=1)
    start_arg = args[1].strip() if len(args) > 1 else ""

    if start_arg.startswith("ref_"):
        with suppress(Exception):
            add_referral(uid, int(start_arg.split("_", 1)[1]))

    # Show the channel-join prompt only once per Telegram user.
    # The decision (Join/Verify or Skip) is remembered in SQLite.
    if channels_configured() and is_first_start:
        mark_join_prompt_seen(uid)
        await message.answer(
            "💎 <b>Welcome to Premium Hub Store!</b>\n\n"
            "📢 Please join our official channels before continuing.\n"
            "You can join both channels and tap <b>I'm Joined</b>, "
            "or continue with <b>Skip</b>.\n\n"
            "🔒 Fast • Secure • Premium Service",
            reply_markup=join_kb(),
        )
        return

    if start_arg.startswith("product_"):
        with suppress(Exception):
            pid = int(start_arg.split("_", 1)[1])
            product = get_product(pid)
            if product:
                me = await message.bot.get_me()
                s = stock_count(pid)
                u = user or get_user(message.from_user.id)
                total = Decimal(product["price"])
                await message.answer(
                    f"💎 <b>{html.escape(product['name'])}</b>\n\n"
                    f"💵 Price: <b>{money(product['price'])} {INVOICE_CURRENCY}</b>\n"
                    f"📦 Available Stock: <b>{s}</b>\n"
                    f"🛡 Warranty: <b>{html.escape(product['warranty'])}</b>\n"
                    f"🔢 Selected Qty: <b>1</b>\n"
                    f"🧾 Total: <b>{money(total)} {INVOICE_CURRENCY}</b>\n"
                    f"👛 Wallet Balance: <b>{money(u['wallet'])} {INVOICE_CURRENCY}</b>",
                    reply_markup=product_kb(pid, 1, me.username, message.from_user.id),
                )
                return

    await message.answer(
        welcome_text(user),
        reply_markup=main_kb(message.from_user.id),
    )

@router.message(Command("shop"))
async def command_shop(message: Message):
    user, _ = register_user_and_get(message.from_user)
    if maintenance_enabled() and not is_admin(message.from_user.id):
        return await message.answer("🛠 Bot is currently under maintenance.")
    kb, page, pages = products_kb(1, message.from_user.id)
    await message.answer(
        f"{tr(message.from_user.id,'available_products')}  •  <b>{page}/{pages}</b>\n"
        f"{tr(message.from_user.id,'select_product')}",
        reply_markup=kb,
    )


@router.message(Command("topup"))
async def command_topup(message: Message, state: FSMContext):
    register_user(message.from_user)
    if maintenance_enabled() and not is_admin(message.from_user.id):
        return await message.answer("🛠 Bot is currently under maintenance.")
    await state.clear()
    await state.set_state(TopupState.amount)
    prompt = await message.answer(
        "💎 <b>Add Funds</b>\n\n"
        f"Enter how much {INVOICE_CURRENCY} you want to add.\n\n"
        "Example: <code>10</code> or <code>25.5</code>",
        reply_markup=back_home(message.from_user.id),
    )
    await state.update_data(topup_prompt_chat=prompt.chat.id, topup_prompt_msg=prompt.message_id)


@router.message(Command("balance"))
async def command_balance(message: Message):
    register_user(message.from_user)
    user = get_user(message.from_user.id)
    await message.answer(
        "💳 <b>My Balance</b>\n\n"
        f"Available Balance: <b>{money(user['wallet'])} {INVOICE_CURRENCY}</b>",
        reply_markup=main_kb(message.from_user.id),
    )


@router.message(Command("settings"))
async def command_settings(message: Message, state: FSMContext):
    await state.clear()
    register_user(message.from_user)
    user = get_user(message.from_user.id)
    await message.answer(profile_text(user, message.from_user), reply_markup=settings_kb(message.from_user.id))


@router.message(Command("refer"))
async def command_refer(message: Message):
    register_user(message.from_user)
    me = await message.bot.get_me()
    count, earned = referral_stats(message.from_user.id)
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Copy Referral Link", copy_text=CopyTextButton(text=link), style="primary")],
        [InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")],
    ])
    await message.answer(
        "🎁 <b>Refer & Earn</b>\n\n"
        f"💰 Reward: <b>{money(REFERRAL_BONUS)} {INVOICE_CURRENCY}</b> per verified referral\n"
        f"👥 Verified Referrals: <b>{count}</b>\n"
        f"💰 Earnings: <b>{money(earned)} {INVOICE_CURRENCY}</b>\n\n"
        f"🔗 <code>{html.escape(link)}</code>",
        reply_markup=kb,
    )


@router.message(Command("channel"))
async def command_channel(message: Message):
    register_user(message.from_user)
    rows = []
    if CHANNEL_1_URL:
        rows.append([InlineKeyboardButton(text=f"📢 {CHANNEL_1_NAME}", url=CHANNEL_1_URL, style="success")])
    if CHANNEL_2_URL:
        rows.append([InlineKeyboardButton(text=f"📢 {CHANNEL_2_NAME}", url=CHANNEL_2_URL, style="primary")])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")])
    await message.answer(
        "📢 <b>Premium Hub Official Channels</b>\n\nJoin our official channels for product, stock and store updates.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "join:verify")
async def join_verify(callback: CallbackQuery):
    missing = await required_join_status(callback.bot, callback.from_user.id)

    if missing:
        names = "\n".join(f"• {html.escape(name)}" for _, _, name in missing)
        await callback.answer(
            "You still need to join all required channels.",
            show_alert=True,
        )
        await edit(
            callback,
            "❌ <b>Verification Failed</b>\n\n"
            "Please join these channels first:\n"
            f"{names}\n\n"
            "Then tap <b>Verify Joined</b> again.",
            join_kb(),
        )
        return

    with suppress(Exception):
        await callback.message.delete()

    await verify_referral_for_user(callback.bot, callback.from_user.id)
    user = get_user(callback.from_user.id)
    await callback.bot.send_message(
        callback.from_user.id,
        "✅ <b>Successfully Verified!</b>\n\n" + welcome_text(user),
        reply_markup=main_kb(callback.from_user.id),
    )
    await callback.answer("Successfully verified!")


@router.callback_query(F.data == "join:skip")
async def join_skip(callback: CallbackQuery):
    with suppress(Exception):
        await callback.message.delete()

    user = get_user(callback.from_user.id)
    await callback.bot.send_message(
        callback.from_user.id,
        welcome_text(user),
        reply_markup=main_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user, _ = register_user_and_get(callback.from_user)
    await edit(callback, welcome_text(user), main_kb(callback.from_user.id))


@router.callback_query(F.data.in_({"menu:products"}) | F.data.startswith("products:"))
async def products(callback: CallbackQuery):
    if await maintenance_guard(callback):
        return
    page = 1 if callback.data == "menu:products" else int(callback.data.split(":")[1])
    kb, page, pages = products_kb(page, callback.from_user.id)
    await edit(callback, f"{tr(callback.from_user.id,'available_products')}  •  <b>{page}/{pages}</b>\n{tr(callback.from_user.id,'select_product')}", kb)


@router.callback_query(F.data.startswith("group:"))
async def product_group_page(callback: CallbackQuery):
    if await maintenance_guard(callback):
        return
    pid = int(callback.data.split(":", 1)[1])
    variants = group_products_by_pid(pid)
    if not variants:
        return await callback.answer("Product group not found.", show_alert=True)
    title = html.escape(str(variants[0]["category"] or "Product Variants"))
    await edit(
        callback,
        f"📂 <b>{title}</b>\n\n📋 <b>{len(variants)} plans available</b>\n👇 Choose a plan:",
        group_products_kb(pid, callback.from_user.id),
    )


@router.callback_query(F.data.startswith("product:"))
async def product_page(callback: CallbackQuery):
    if await maintenance_guard(callback):
        return
    _, p, q = callback.data.split(":")
    pid, qty = int(p), max(1, int(q))
    product = get_product_with_stock(pid)
    if not product:
        return await callback.answer(tr(callback.from_user.id,"not_found"), show_alert=True)
    s = int(product.get("stock", 0) or 0)
    total = Decimal(product["price"]) * qty
    u = get_user(callback.from_user.id)
    lang_user = u or {"language": user_language(callback.from_user.id), "wallet": "0"}
    await edit(
        callback,
        f"💎 <b>{html.escape(product['name'])}</b>\n\n{tr_user(lang_user,'price')}: <b>{money(product['price'])} {INVOICE_CURRENCY}</b>\n📅 Validity: <b>{html.escape(product['validity'])}</b>\n🛡 Warranty: <b>{html.escape(product['warranty'])}</b>\n{tr_user(lang_user,'stock')}: <b>{s}</b>\n{tr_user(lang_user,'selected')}: <b>{qty}</b>\n{tr_user(lang_user,'total')}: <b>{money(total)} {INVOICE_CURRENCY}</b>\n{tr_user(lang_user,'wallet')}: <b>{money(lang_user['wallet'])} {INVOICE_CURRENCY}</b>",
        product_kb(pid, qty, BOT_USERNAME, callback.from_user.id),
    )


@router.callback_query(F.data.startswith("qty:"))
async def qty_change(callback: CallbackQuery):
    _, op, p, q = callback.data.split(":")
    pid = int(p)
    product = get_product_with_stock(pid)
    if not product:
        return await callback.answer(tr(callback.from_user.id,"not_found"), show_alert=True)
    s = int(product.get("stock", 0) or 0)
    qty = int(q) + (1 if op == "+" else -1)
    qty = max(1, min(qty, max(1, s)))
    total = Decimal(product["price"]) * qty
    u = get_user(callback.from_user.id)
    lang_user = u or {"language": user_language(callback.from_user.id), "wallet": "0"}
    await edit(
        callback,
        f"💎 <b>{html.escape(product['name'])}</b>\n\n{tr_user(lang_user,'price')}: <b>{money(product['price'])} {INVOICE_CURRENCY}</b>\n{tr_user(lang_user,'stock')}: <b>{s}</b>\n{tr_user(lang_user,'warranty')}: <b>{html.escape(product['warranty'])}</b>\n{tr_user(lang_user,'selected')}: <b>{qty}</b>\n{tr_user(lang_user,'total')}: <b>{money(total)} {INVOICE_CURRENCY}</b>\n{tr_user(lang_user,'wallet')}: <b>{money(lang_user['wallet'])} {INVOICE_CURRENCY}</b>",
        product_kb(pid, qty, BOT_USERNAME, callback.from_user.id),
    )


@router.callback_query(F.data.startswith("note:"))
async def note(callback: CallbackQuery):
    _, p, q = callback.data.split(":")
    product = get_product(int(p))
    if not product:
        return await callback.answer(tr(callback.from_user.id,"not_found"), show_alert=True)
    await edit(
        callback,
        f"📜 <b>{html.escape(product['name'])} - {tr(callback.from_user.id,'note')}</b>\n\n{html.escape(product['note'] or tr(callback.from_user.id,'no_note'))}",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(callback.from_user.id,"back"), callback_data=f"product:{p}:{q}", style="danger")]]),
    )


@router.callback_query(F.data.startswith("custom:"))
async def custom_qty(callback: CallbackQuery, state: FSMContext):
    _, p, q = callback.data.split(":")
    await state.set_state(CustomQtyState.choosing)
    await state.update_data(pid=int(p), value=str(q))
    await edit(callback, f"{tr(callback.from_user.id,'custom_qty')}\n\n{tr(callback.from_user.id,'current')}: <b>{q}</b>", qty_calc_kb(int(p), q, callback.from_user.id))


@router.callback_query(F.data.startswith("qcalc:"))
async def qcalc(callback: CallbackQuery, state: FSMContext):
    _, p, key = callback.data.split(":")
    pid = int(p)
    data = await state.get_data()
    value = str(data.get("value", ""))
    if key.isdigit() and len(key) == 1:
        value = (value + key).lstrip("0")[:6] or "0"
    elif key == "back":
        value = value[:-1] or "0"
    elif key == "clear":
        value = "0"
    elif key.startswith("set"):
        value = key[3:]
    elif key == "max":
        value = str(max(1, stock_count(pid)))
    await state.update_data(value=value)
    await edit(callback, f"{tr(callback.from_user.id,'custom_qty')}\n\n{tr(callback.from_user.id,'current')}: <b>{value}</b>", qty_calc_kb(pid, value, callback.from_user.id))


@router.callback_query(F.data.startswith("qconfirm:"))
async def qconfirm(callback: CallbackQuery, state: FSMContext):
    pid = int(callback.data.split(":")[1])
    data = await state.get_data()
    try:
        qty = int(data.get("value", "1"))
    except Exception:
        qty = 1
    qty = min(max(qty, 1), 100000)
    await state.clear()
    product = get_product_with_stock(pid)
    if not product:
        return await callback.answer(tr(callback.from_user.id,"not_found"), show_alert=True)
    s = int(product.get("stock", 0) or 0)
    qty = min(qty, max(1, s))
    total = Decimal(product["price"]) * qty
    u = get_user(callback.from_user.id)
    lang_user = u or {"language": user_language(callback.from_user.id), "wallet": "0"}
    await edit(
        callback,
        f"💎 <b>{html.escape(product['name'])}</b>\n\n{tr_user(lang_user,'price')}: <b>{money(product['price'])} {INVOICE_CURRENCY}</b>\n{tr_user(lang_user,'stock')}: <b>{s}</b>\n{tr_user(lang_user,'warranty')}: <b>{html.escape(product['warranty'])}</b>\n{tr_user(lang_user,'selected')}: <b>{qty}</b>\n{tr_user(lang_user,'total')}: <b>{money(total)} {INVOICE_CURRENCY}</b>\n{tr_user(lang_user,'wallet')}: <b>{money(lang_user['wallet'])} {INVOICE_CURRENCY}</b>",
        product_kb(pid, qty, BOT_USERNAME, callback.from_user.id),
    )


@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery):
    if await maintenance_guard(callback):
        return
    _, p, q = callback.data.split(":")
    pid, qty = int(p), int(q)
    product = get_product(pid)
    user = get_user(callback.from_user.id)
    if not product or not user:
        return await callback.answer("Unable to continue", show_alert=True)
    if stock_count(pid) < qty:
        return await callback.answer(tr(callback.from_user.id,"not_enough"), show_alert=True)
    # Email is optional and asked AFTER delivery (first purchase only)
    await show_purchase_confirmation(callback, pid, qty)


@router.callback_query(F.data.startswith("email:after:"))
async def email_after_order(callback: CallbackQuery, state: FSMContext):
    """After delivery: user chooses to set email for invoice of this order."""
    oid = callback.data.split(":", 2)[2]
    order = get_order(oid)
    if not order or int(order["telegram_id"]) != callback.from_user.id:
        return await callback.answer("Order not found.", show_alert=True)
    await state.set_state(EmailState.waiting)
    await state.update_data(pending_invoice_order=oid)
    await edit(
        callback,
        "📧 <b>Set Email</b>\n\n"
        "Send your email address.\n"
        "A 6-digit verification code will be sent to that email.",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏭ Skip", callback_data=f"email:skip_after:{oid}", style="primary")
        ]]),
    )


@router.callback_query(F.data.startswith("email:skip_after:"))
async def email_skip_after(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(
        callback,
        "✅ <b>Skipped</b>\n\nYour product is already delivered. You can set email later from Settings.",
        back_home(callback.from_user.id),
    )


@router.callback_query(F.data.startswith("purchase:confirm:"))
async def purchase_confirm(callback: CallbackQuery):
    if await maintenance_guard(callback):
        return
    _, _, pid_s, qty_s = callback.data.split(":")
    pid, qty = int(pid_s), int(qty_s)
    product = get_product(pid)
    user = get_user(callback.from_user.id)
    if not product or not user:
        return await callback.answer(tr(callback.from_user.id,"not_found"), show_alert=True)
    if stock_count(pid) < qty:
        return await callback.answer(tr(callback.from_user.id,"not_enough"), show_alert=True)
    oid = create_order(callback.from_user.id, product, qty)
    total = Decimal(str(product["price"])) * qty
    await edit(callback,
        f"{tr(callback.from_user.id,'select_payment')}\n\n🆔 <code>{oid}</code>\n📦 {html.escape(product['name'])}\n{tr(callback.from_user.id,'quantity')}: <b>{qty}</b>\n{tr(callback.from_user.id,'total')}: <b>{money(total)} {INVOICE_CURRENCY}</b>\n{tr(callback.from_user.id,'wallet')}: <b>{money(user['wallet'])} {INVOICE_CURRENCY}</b>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr(callback.from_user.id,"direct"), callback_data=f"pay:direct:{oid}", style="success")],
            [InlineKeyboardButton(text=tr(callback.from_user.id,"wallet_pay"), callback_data=f"pay:wallet:{oid}", style="primary")],
            [InlineKeyboardButton(text="🛒 Back to Shop", callback_data="menu:products", style="danger")],
        ]),
    )


@router.callback_query(F.data.startswith("pay:wallet:"))
async def pay_wallet(callback: CallbackQuery):
    oid = callback.data.split(":", 2)[2]
    order = get_order(oid)
    if not order or order["telegram_id"] != callback.from_user.id or order["status"] != "PENDING_PAYMENT":
        return await callback.answer(tr(callback.from_user.id,"order_unavailable"), show_alert=True)
    total = Decimal(order["total_amount"])
    user = get_user(callback.from_user.id)
    if Decimal(user["wallet"]) < total:
        return await callback.answer(tr(callback.from_user.id,"insufficient"), show_alert=True)
    try:
        balance = change_wallet(callback.from_user.id, -total, "PURCHASE", oid)
    except ValueError:
        return await callback.answer(tr(callback.from_user.id,"insufficient"), show_alert=True)
    update_order(oid, payment_method="WALLET", status="PAID")
    await edit(callback, f"{tr(callback.from_user.id,'wallet_success')}\n\n{tr(callback.from_user.id,'paid')}: <b>{money(total)} {INVOICE_CURRENCY}</b>\n{tr(callback.from_user.id,'balance',amount=money(balance),currency=INVOICE_CURRENCY)}", back_home(callback.from_user.id))
    await complete_paid_order(callback.bot, oid, "WALLET")


def payhub_fail_kb():
    username = SUPPORT_USERNAME.lstrip("@")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")],
        [InlineKeyboardButton(text="🛒 Shop", callback_data="menu:products", style="success")],
        [InlineKeyboardButton(text="🎧 Contact Support", url=f"https://t.me/{username}", style="primary")],
    ])


def payhub_done_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")],
        [InlineKeyboardButton(text="🛒 Shop", callback_data="menu:products", style="success")],
    ])


def progress_bar(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    filled = max(0, min(10, round(pct / 10)))
    return "▰" * filled + "▱" * (10 - filled)


def _payhub_verify_frame(step: int, spinner: str = "◐") -> str:
    steps = [
        (10, spinner, "⏳", "⏳", "⏳", "Detecting payment..."),
        (32, "✅", spinner, "⏳", "⏳", "Synchronizing invoice..."),
        (58, "✅", "✅", spinner, "⏳", "Validating amount..."),
        (82, "✅", "✅", "✅", spinner, "Running security validation..."),
        (94, "✅", "✅", "✅", spinner, "Waiting for secure confirmation..."),
    ]
    pct, a, b, c, d, status = steps[max(0, min(step, len(steps) - 1))]
    return (
        "╭───────────────╮\n"
        "   ⚡ <b>PAYHUB VERIFY</b>\n"
        "╰───────────────╯\n\n"
        f"🔎 Detecting Payment      {a}\n"
        f"🔗 Invoice Synchronizing  {b}\n"
        f"💰 Amount Validation      {c}\n"
        f"🛡️ Security Validation    {d}\n\n"
        f"{progress_bar(pct)} <b>{pct}%</b>\n\n"
        f"🔐 {status}"
    )


async def send_verify_checking(bot: Bot, chat_id: int):
    """Render the first verification frame before any network verification starts."""
    return await bot.send_message(
        chat_id,
        _payhub_verify_frame(0, "◐"),
        parse_mode=ParseMode.HTML,
    )


async def animate_verify_checking(bot: Bot, chat_id: int, message_id: int, stop_event: asyncio.Event):
    """Animate one Telegram message while PayHub verification runs; never delays verification."""
    spinners = ("◐", "◓", "◑", "◒")
    tick = 0
    try:
        while not stop_event.is_set():
            step = min(4, 1 + tick // 2)
            spinner = spinners[tick % len(spinners)]
            with suppress(Exception):
                await bot.edit_message_text(
                    _payhub_verify_frame(step, spinner),
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode=ParseMode.HTML,
                )
            tick += 1
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.55)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        raise


async def stop_verify_animation(task, stop_event: asyncio.Event | None):
    if stop_event:
        stop_event.set()
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


async def finish_verify_checking(bot: Bot, chat_id: int, checking_msg, success: bool):
    """Turn the same animated message into the final success/cancelled result."""
    if success:
        text = (
            "╭───────────────╮\n"
            "   ✅ <b>PAYMENT VERIFIED</b>\n"
            "╰───────────────╯\n\n"
            "🔎 Detecting Payment      ✅\n"
            "🔗 Invoice Synchronizing  ✅\n"
            "💰 Amount Validation      ✅\n"
            "🛡️ Security Validation    ✅\n\n"
            f"{progress_bar(100)} <b>100%</b>\n\n"
            "⚡ Payment confirmed successfully."
        )
        kb = payhub_done_kb()
    else:
        text = (
            "╭───────────────╮\n"
            "   ❌ <b>PAYMENT CANCELLED</b>\n"
            "╰───────────────╯\n\n"
            "Payment could not be verified.\n"
            "No balance was added and no order was completed.\n\n"
            "⚠️ Check the amount, Binance UID and TX / Order ID, then try again."
        )
        kb = payhub_fail_kb()

    if checking_msg:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=checking_msg.message_id,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return checking_msg
        except Exception:
            logging.exception("Could not finalize PayHub verification message")
    return await bot.send_message(chat_id, text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def cancel_payhub_invoice(invoice_id: str | None):
    if not invoice_id:
        return
    with db() as con:
        con.execute(
            "UPDATE payment_invoices SET status='CANCELLED' WHERE invoice_id=? AND status!='PAID'",
            (invoice_id,),
        )
        con.commit()


async def show_payhub_invoice(target, state: FSMContext, invoice: dict, amount, kind: str, ref: str, uid: int):
    invoice = _normalize_payhub_payload(invoice)
    invoice_id = str(invoice.get("invoice_id") or invoice.get("id") or "")
    pay_uid = str(
        invoice.get("uid")
        or invoice.get("binance_uid")
        or invoice.get("pay_uid")
        or invoice.get("wallet_id")
        or ""
    )
    if not invoice_id or not pay_uid:
        raise RuntimeError("PayHub invoice missing invoice_id or uid")
    save_invoice(invoice_id, uid, kind, ref, amount, INVOICE_CURRENCY, amount, INVOICE_CURRENCY, pay_uid)
    await state.set_state(PayHubState.waiting_txid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Copy UID: {pay_uid}", copy_text=CopyTextButton(text=pay_uid), style="success")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="payhub:cancel", style="danger")],
    ])
    text = (
        "🧾 <b>Payment Invoice</b>\n\n"
        f"🆔 Invoice: <code>{html.escape(invoice_id)}</code>\n"
        f"💵 Amount: <b>{money(amount)} {INVOICE_CURRENCY}</b>\n"
        f"👤 Binance UID: <code>{html.escape(pay_uid)}</code>\n\n"
        "1️⃣ Send the <b>exact amount</b> to this Binance UID using Binance Pay\n"
        "2️⃣ Then send the <b>TX / Order ID</b> here\n\n"
        "After verification, credit or product delivery is automatic."
    )
    invoice_msg = None
    if isinstance(target, CallbackQuery):
        await edit(target, text, kb)
        invoice_msg = target.message
    else:
        invoice_msg = await target.answer(text, reply_markup=kb)
    await state.update_data(
        payhub_invoice_id=invoice_id,
        payment_kind=kind,
        payment_ref=ref,
        payhub_chat_id=invoice_msg.chat.id if invoice_msg else uid,
        payhub_msg_id=invoice_msg.message_id if invoice_msg else None,
    )


@router.callback_query(F.data.startswith("pay:direct:"))
async def pay_direct(callback: CallbackQuery, state: FSMContext):
    oid = callback.data.split(":", 2)[2]
    order = get_order(oid)
    if not order or int(order["telegram_id"]) != callback.from_user.id or str(order["status"]) != "PENDING_PAYMENT":
        return await callback.answer(tr(callback.from_user.id,"order_unavailable"), show_alert=True)
    if not payhub_configured():
        return await callback.answer("Payment API is not configured.", show_alert=True)

    # Immediate UI feedback; the API request starts right after this edit.
    await edit(
        callback,
        "⚡ <b>Direct Pay</b>\n\n🔗 Connecting to PayHub and creating your invoice...",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏳ Creating Invoice...", callback_data="noop", style="primary")
        ]]),
    )
    try:
        inv = await payhub_create_invoice(callback.from_user.id, order["total_amount"])
        inv = _normalize_payhub_payload(inv)
        if not (inv.get("invoice_id") or inv.get("id")):
            raise RuntimeError(f"PayHub invoice_id missing: {inv}")
        if not (inv.get("uid") or inv.get("binance_uid") or inv.get("pay_uid") or inv.get("wallet_id")):
            raise RuntimeError(f"PayHub UID missing: {inv}")
        await show_payhub_invoice(
            callback, state, inv, Decimal(order["total_amount"]),
            "order", oid, callback.from_user.id,
        )
    except Exception as exc:
        logging.exception("PayHub Direct Pay invoice create failed for order=%s", oid)
        await state.clear()
        retry_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Retry Direct Pay", callback_data=f"pay:direct:{oid}", style="success")],
            [InlineKeyboardButton(text="🛒 Back to Shop", callback_data="menu:products", style="primary")],
            [InlineKeyboardButton(text="🎧 Contact Support", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}", style="danger")],
        ])
        # Do not answer the callback twice; replace the already-open message with a useful retry screen.
        if callback.message:
            with suppress(Exception):
                await callback.message.edit_text(
                    "❌ <b>Direct Pay is temporarily unavailable.</b>\n\n"
                    "The invoice could not be created. No payment was charged.\n"
                    "Tap <b>Retry Direct Pay</b> to try again.",
                    reply_markup=retry_kb,
                )
        return


@router.callback_query(F.data == "menu:topup")
async def topup(callback: CallbackQuery, state: FSMContext):
    if await maintenance_guard(callback):
        return
    await state.set_state(TopupState.amount)
    await edit(
        callback,
        "💎 <b>Add Funds</b>\n\n"
        f"Enter how much {INVOICE_CURRENCY} you want to add.\n\n"
        f"Example: <code>10</code>  or  <code>25.5</code>\n\n"
        "A Binance Pay UID will be generated for this topup.",
        back_home(),
    )
    if callback.message:
        await state.update_data(topup_prompt_chat=callback.message.chat.id, topup_prompt_msg=callback.message.message_id)


@router.message(TopupState.amount)
async def topup_amount(message: Message, state: FSMContext, bot: Bot):
    try:
        amount = Decimal((message.text or "").strip())
        if amount <= 0:
            raise InvalidOperation
    except Exception:
        return await message.answer("❌ Send a valid amount.\nExample: <code>10</code>")
    data = await state.get_data()
    with suppress(Exception):
        await message.delete()
    if data.get("topup_prompt_msg"):
        with suppress(Exception):
            await bot.delete_message(data["topup_prompt_chat"], int(data["topup_prompt_msg"]))
    wait = await bot.send_message(
        message.chat.id,
        "⏳ <b>Creating invoice...</b>\nPlease wait a moment.",
    )
    if not payhub_configured():
        with suppress(Exception):
            await wait.delete()
        await state.clear()
        return await bot.send_message(message.chat.id, "❌ Payment API is not configured.", reply_markup=back_home(message.from_user.id))
    try:
        inv = await payhub_create_invoice(message.from_user.id, amount)
        if not inv.get("ok", True) and not inv.get("invoice_id"):
            raise RuntimeError(str(inv))
        with suppress(Exception):
            await wait.delete()
        await show_payhub_invoice(message, state, inv, amount, "deposit", f"DEP-{uuid4().hex[:10].upper()}", message.from_user.id)
    except Exception:
        logging.exception("PayHub topup invoice failed")
        with suppress(Exception):
            await wait.delete()
        await state.clear()
        await bot.send_message(message.chat.id, "❌ Could not create payment invoice.", reply_markup=back_home(message.from_user.id))


async def payhub_cleanup_old_messages(message: Message, state_data: dict):
    chat_id = state_data.get("payhub_chat_id") or message.chat.id
    msg_id = state_data.get("payhub_msg_id")
    if msg_id:
        with suppress(Exception):
            await message.bot.delete_message(chat_id, int(msg_id))
    with suppress(Exception):
        await message.delete()


async def payhub_fail_and_cleanup(
    message: Message,
    state: FSMContext,
    invoice_id: str | None,
    checking_msg=None,
    animation_task=None,
    animation_stop: asyncio.Event | None = None,
):
    await stop_verify_animation(animation_task, animation_stop)
    data = await state.get_data()
    await cancel_payhub_invoice(invoice_id)
    await state.clear()
    await payhub_cleanup_old_messages(message, data)
    await finish_verify_checking(
        message.bot,
        message.chat.id,
        checking_msg,
        success=False,
    )


@router.message(PayHubState.waiting_txid)
async def payhub_txid_received(message: Message, state: FSMContext):
    txid = (message.text or "").strip()
    if not txid or len(txid) < 4:
        return await message.answer("❌ Send a valid TX / Order ID.")

    data = await state.get_data()
    invoice_id = data.get("payhub_invoice_id")
    if not invoice_id:
        await state.clear()
        return await message.answer(
            "Payment session expired. Start again.",
            reply_markup=back_home(message.from_user.id),
        )

    saved = get_saved_invoice(invoice_id)
    if not saved or int(saved["telegram_id"]) != message.from_user.id:
        await state.clear()
        return await message.answer(
            "Invoice not found.",
            reply_markup=back_home(message.from_user.id),
        )

    # The first animation frame is sent BEFORE PayHub verification begins.
    checking_msg = await send_verify_checking(message.bot, message.chat.id)
    animation_stop = asyncio.Event()
    animation_task = asyncio.create_task(
        animate_verify_checking(
            message.bot, message.chat.id, checking_msg.message_id, animation_stop
        )
    )

    try:
        result = await payhub_verify(invoice_id, txid)
    except Exception:
        logging.exception("PayHub verify failed for invoice=%s", invoice_id)
        return await payhub_fail_and_cleanup(
            message, state, invoice_id, checking_msg, animation_task, animation_stop
        )

    result = _normalize_payhub_payload(result)
    status = str(result.get("status") or result.get("payment_status") or "").upper()
    paid = status in {"PAID", "SUCCESS", "COMPLETED", "CONFIRMED"}
    if any(result.get(k) is True for k in ("paid", "verified", "confirmed")):
        paid = True
    failed_states = {
        "FAILED", "INVALID", "MISMATCH", "WRONG_INVOICE", "NOT_FOUND",
        "PENDING", "UNPAID", "EXPIRED", "CANCELLED", "CANCELED",
    }
    if result.get("ok") is True and status not in failed_states:
        paid = True

    if not paid:
        return await payhub_fail_and_cleanup(
            message, state, invoice_id, checking_msg, animation_task, animation_stop
        )

    kind = saved["payment_kind"]
    ref = saved["reference_id"]

    try:
        applied = await apply_paid_invoice(
            message.bot,
            invoice_id,
            str(result.get("txid") or result.get("tx_id") or result.get("order_id") or txid),
            str(result.get("network") or "BINANCE_PAY"),
            notify=False,
        )
    except Exception:
        logging.exception("Verified PayHub payment could not be applied invoice=%s", invoice_id)
        await stop_verify_animation(animation_task, animation_stop)
        # Payment is verified, so never falsely mark it cancelled. Keep a clear admin/support path.
        await state.clear()
        await payhub_cleanup_old_messages(message, data)
        if checking_msg:
            with suppress(Exception):
                await message.bot.edit_message_text(
                    "⚠️ <b>PAYMENT VERIFIED — PROCESSING PENDING</b>\n\n"
                    "Your payment was verified, but the balance/order update could not finish.\n"
                    "No second payment is required. Please contact support with your TX / Order ID.",
                    chat_id=message.chat.id,
                    message_id=checking_msg.message_id,
                    reply_markup=payhub_fail_kb(),
                )
        return

    await stop_verify_animation(animation_task, animation_stop)
    await state.clear()
    await payhub_cleanup_old_messages(message, data)

    # Only after successful verification/application does the animated message become 100% success.
    await finish_verify_checking(
        message.bot, message.chat.id, checking_msg, success=True
    )

    if applied and kind != "deposit":
        order = get_order(ref)
        if order and order["status"] == "PAID":
            await complete_paid_order(
                message.bot, ref, order["payment_method"] or "PAYHUB_BINANCE_PAY"
            )


@router.callback_query(F.data == "payhub:cancel")
async def payhub_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await cancel_payhub_invoice(data.get("payhub_invoice_id"))
    await state.clear()
    await callback.answer("Invoice cancelled")
    text = (
        "❎ <b>Invoice Cancelled</b>\n\n"
        "This payment was cancelled.\n"
        "No payment check was started.\n"
        "You can shop or top up again anytime."
    )
    kb = payhub_fail_kb()
    with suppress(Exception):
        await callback.message.edit_text(text, reply_markup=kb)
        return
    await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("invoice:download:"))
async def download_order_invoice(callback: CallbackQuery):
    oid = callback.data.split(":", 2)[2]
    order = get_order(oid)
    if not order:
        return await callback.answer("Invoice not found.", show_alert=True)
    if not is_admin(callback.from_user.id) and int(order["telegram_id"]) != callback.from_user.id:
        return await callback.answer("This invoice does not belong to you.", show_alert=True)
    user = get_user(int(order["telegram_id"]))
    pdf = invoice_pdf_bytes(order, user)
    from aiogram.types import BufferedInputFile
    await callback.bot.send_document(
        callback.from_user.id,
        BufferedInputFile(pdf, filename=f"{oid}.pdf"),
        caption=f"📄 Invoice • <code>{oid}</code>",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_invoice(callback: CallbackQuery):
    invoice_id = callback.data.split(":", 1)[1]
    with db() as con:
        con.execute("UPDATE payment_invoices SET status='CANCELLED' WHERE invoice_id=?", (invoice_id,))
        con.commit()
    await edit(callback, tr(callback.from_user.id,"invoice_cancelled"), back_home())


def profile_text(user,tg_user)->str:
    uid=tg_user.id; username=f"@{html.escape(tg_user.username)}" if tg_user.username else tr(uid,"not_set"); email=html.escape(user["email"]) if user["email"] else tr(uid,"not_set"); region=html.escape(user["region"]) if user["region"] else tr(uid,"region_missing")
    names={"en":"English 🇬🇧","hi":"Hindi 🇮🇳","ur":"Urdu 🇵🇰","ar":"Arabic 🇸🇦","es":"Spanish 🇪🇸","id":"Indonesian 🇮🇩"}; language=html.escape(names.get((user["language"] or "en").lower(),"EN")); joined=user["created_at"] or "-"
    try: joined=datetime.fromisoformat(str(joined).replace("Z","+00:00")).strftime("%d %b %Y")
    except Exception: pass
    suffix=tr(uid,"saved") if user["email"] else ""
    return tr(uid,"profile")+"\n\n"+f"🆔 ID: <code>{uid}</code>\n{tr(uid,'first_name')}: <b>{html.escape(tg_user.first_name or user['full_name'])}</b>\n{tr(uid,'username')}: {username}\n{tr(uid,'status')}: <b>{tr(uid,'started')}</b>\n{tr(uid,'email')}: <b>{email}</b>{suffix}\n💰 Balance: <b>{Decimal(str(user['wallet'])):.3f} {INVOICE_CURRENCY}</b>\n{tr(uid,'currency')}: <b>{INVOICE_CURRENCY}</b>\n{tr(uid,'language')}: <b>{language}</b>\n{tr(uid,'region')}: {region}\n{tr(uid,'joined')}: <b>{html.escape(str(joined))}</b>"


@router.callback_query(F.data == "menu:settings")
async def settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    register_user(callback.from_user)
    user = get_user(callback.from_user.id)
    await edit(callback, profile_text(user, callback.from_user), settings_kb(callback.from_user.id))


@router.callback_query(F.data == "settings:region")
async def region_settings(callback: CallbackQuery):
    await edit(callback, tr(callback.from_user.id,"choose_region"), region_kb(callback.from_user.id))


@router.callback_query(F.data.startswith("region:"))
async def region_selected(callback: CallbackQuery):
    region = callback.data.split(":", 1)[1]
    set_region(callback.from_user.id, region)
    user = get_user(callback.from_user.id)
    await edit(callback, profile_text(user, callback.from_user), settings_kb(callback.from_user.id))


@router.callback_query(F.data == "settings:orders")
async def my_orders(callback: CallbackQuery):
    rows = user_orders(callback.from_user.id)
    if not rows:
        text = tr(callback.from_user.id,"my_orders")+"\n\n"+tr(callback.from_user.id,"no_orders")
    else:
        parts=[tr(callback.from_user.id,"my_orders")]
        for idx, o in enumerate(rows, 1):
            details = html.escape(str(o["product_name"] or "-"))
            order_id = html.escape(str(o["order_id"] or "-"))
            status = html.escape(str(o["status"] or "-"))
            qty = o["quantity"]
            total = money(o["total_amount"])
            validity = html.escape(str(o["validity"] or "-")) if "validity" in o.keys() else "-"
            warranty = html.escape(str(o["warranty"] or "-")) if "warranty" in o.keys() else "-"
            parts.append(
                f"\n<b>{idx}. {details}</b>\n"
                f"🆔 Order: <code>{order_id}</code>\n"
                f"🔢 Quantity: <b>{qty}</b>\n"
                f"💰 Total: <b>{total} {INVOICE_CURRENCY}</b>\n"
                f"📅 Validity: <b>{validity}</b>\n"
                f"🛡 Warranty: <b>{warranty}</b>\n"
                f"📌 Status: <b>{status}</b>"
            )
        text = "\n".join(parts)
    await edit(callback, text, settings_kb(callback.from_user.id))


@router.callback_query(F.data == "settings:language")
async def language(callback: CallbackQuery):
    await edit(
        callback,
        tr(callback.from_user.id,"select_language"),
        language_kb(callback.from_user.id),
    )


@router.callback_query(F.data.startswith("lang:"))
async def language_selected(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1].lower()
    allowed = {"en", "hi", "ur", "ar", "es", "id"}
    if code not in allowed:
        await callback.answer(tr(callback.from_user.id,"unsupported"), show_alert=True)
        return
    set_language(callback.from_user.id, code)
    user = get_user(callback.from_user.id)
    await edit(callback, profile_text(user, callback.from_user), settings_kb(callback.from_user.id))


@router.callback_query(F.data == "settings:email")
async def email_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    u = get_user(callback.from_user.id)
    if u and u["email"]:
        verified = "✅ Verified" if int(u["email_verified"] or 0) else "⚠️ Not verified"
        current = f"{html.escape(u['email'])} • {verified}"
    else:
        current = "Not set"
    await edit(
        callback,
        f"📧 <b>Email Settings</b>\n\nCurrent Email: <b>{current}</b>\n\n"
        "Change email requires a new 6-digit verification code.",
        email_kb(callback.from_user.id),
    )


@router.callback_query(F.data.in_({"email:set", "email:change"}))
async def email_input(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EmailState.waiting)
    await state.update_data(menu_message_id=callback.message.message_id, pending_invoice_order=None)
    await edit(callback, "📧 <b>Send your email address.</b>\n\nA 6-digit verification code will be sent to that email.", back_home())


@router.message(EmailState.waiting)
async def email_received(message: Message, state: FSMContext, bot: Bot):
    email = (message.text or "").strip().lower()
    if not EMAIL_RE.fullmatch(email):
        return await message.answer("❌ Invalid email. Send a valid email address.")
    data = await state.get_data()
    set_email(message.from_user.id, email)
    row = get_email_verification(message.from_user.id)
    code = row["email_verification_token"] if row else None
    with suppress(Exception):
        await message.delete()
    sending_msg = await bot.send_message(
        message.chat.id,
        "📨 <b>Sending verification code...</b>\n\n"
        f"📧 {html.escape(email)}",
    )
    sent = bool(code and await send_email_verification_code(message.from_user.id, email, str(code)))
    with suppress(Exception):
        await sending_msg.delete()
    if not sent:
        # Keep the email unverified and return to Email Settings. Never claim verification succeeded.
        await state.clear()
        return await bot.send_message(
            message.chat.id,
            "❌ <b>Verification email could not be sent.</b>\n\n"
            "The email is saved but remains <b>Not verified</b>.\n"
            "Check Admin → Bot Settings → SMTP / Email, then try Set/Change Email again.",
            reply_markup=email_kb(message.from_user.id),
        )
    mark_email_code_sent(message.from_user.id)
    await state.set_state(EmailState.waiting_code)
    await state.update_data(
        pending_invoice_order=data.get("pending_invoice_order"),
        pending_email=email,
    )
    await bot.send_message(
        message.chat.id,
        "✅ <b>Verification code sent successfully!</b>\n\n"
        f"📧 Code sent to: <b>{html.escape(email)}</b>\n\n"
        f"🔐 Please enter the 6-digit code below.\n"
        f"⏳ The code expires in {EMAIL_CODE_TTL_MINUTES} minutes.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Cancel", callback_data="email:cancel_code", style="danger")
        ]]),
    )


@router.message(EmailState.waiting_code)
async def email_code_received(message: Message, state: FSMContext, bot: Bot):
    code = (message.text or "").strip()
    data = await state.get_data()
    with suppress(Exception):
        await message.delete()
    if not re.fullmatch(r"\d{6}", code):
        return await bot.send_message(
            message.chat.id,
            "❌ Please enter the <b>6-digit</b> verification code from your email.",
        )
    if not verify_email_code(message.from_user.id, code):
        return await bot.send_message(
            message.chat.id,
            "❌ Invalid code. Please enter the correct 6-digit code sent to your email.",
        )
    await state.clear()
    email = data.get("pending_email") or ""
    oid = data.get("pending_invoice_order")
    await bot.send_message(
        message.chat.id,
        "✅ <b>Email Verified</b>\n\n"
        f"Your email has been saved successfully.\n"
        f"📧 <b>{html.escape(str(email))}</b>\n\n"
        "Your invoice will be sent to this email.",
        reply_markup=email_kb(message.from_user.id) if not oid else back_home(message.from_user.id),
    )
    # If this verification was triggered after a purchase, send that order's invoice now
    if oid:
        order = get_order(str(oid))
        if order and int(order["telegram_id"]) == message.from_user.id and order["status"] == "COMPLETED":
            sent = await send_invoice_email(order)
            if sent:
                await bot.send_message(
                    message.chat.id,
                    "🧾 <b>Invoice</b>\n\n📧 PDF invoice has been sent to your email.",
                )
            else:
                await bot.send_message(
                    message.chat.id,
                    "⚠️ Email verified, but invoice email could not be sent right now. "
                    "You can still download the invoice from the bot.",
                )


@router.callback_query(F.data == "email:cancel_code")
async def email_cancel_code(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(
        callback,
        "❎ Verification cancelled. You can set email later from Settings.",
        back_home(callback.from_user.id),
    )


@router.callback_query(F.data == "email:delete")
async def email_delete(callback: CallbackQuery):
    set_email(callback.from_user.id, None)
    await edit(callback, "✅ <b>Email Deleted</b>", email_kb(callback.from_user.id))


@router.callback_query(F.data == "menu:balance")
async def menu_balance(callback: CallbackQuery):
    u = get_user(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Go to Shop", callback_data="menu:products", style="success")],
        [InlineKeyboardButton(text="💰 Topup Wallet", callback_data="menu:topup", style="primary")],
        [InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")],
    ])
    await edit(
        callback,
        "💳 <b>My Balance</b>\n\n"
        f"Available Balance: <b>{money(u['wallet'])} {INVOICE_CURRENCY}</b>\n\n"
        "Your reseller/API provider balance is private and visible only to the admin.",
        kb,
    )


@router.callback_query(F.data == "menu:referral")
async def menu_referral(callback: CallbackQuery):
    me = await callback.bot.get_me()
    count, earned = referral_stats(callback.from_user.id)
    link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Copy Referral Link", copy_text=CopyTextButton(text=link), style="primary")],
        [InlineKeyboardButton(text="🛒 Go to Shop", callback_data="menu:products", style="success")],
        [InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")],
    ])
    await edit(
        callback,
        "🎁 <b>Refer & Earn</b>\n\n"
        f"💰 <b>Reward per verified referral: {money(REFERRAL_BONUS)} {INVOICE_CURRENCY}</b>\n"
        "📌 <b>Requirement:</b> Referred user must join both official channels and complete verification.\n\n"
        f"👥 Verified Referrals: <b>{count}</b>\n"
        f"💰 Referral Earnings: <b>{money(earned)} {INVOICE_CURRENCY}</b>\n\n"
        f"🔗 <b>Your Referral Link</b>\n<code>{html.escape(link)}</code>",
        kb,
    )


@router.callback_query(F.data == "menu:channel")
async def channel_menu(callback: CallbackQuery):
    rows = []
    if CHANNEL_1_URL:
        rows.append([InlineKeyboardButton(text=f"📢 {CHANNEL_1_NAME}", url=CHANNEL_1_URL, style="success")])
    if CHANNEL_2_URL:
        rows.append([InlineKeyboardButton(text=f"📢 {CHANNEL_2_NAME}", url=CHANNEL_2_URL, style="primary")])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")])
    await edit(callback, "📢 <b>Premium Hub Official Channels</b>\n\nJoin our official channels for product, stock and store updates.", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "menu:support")
async def support(callback: CallbackQuery):
    username = SUPPORT_USERNAME.lstrip("@")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(callback.from_user.id,"support"), url=f"https://t.me/{username}", style="primary")],
        [InlineKeyboardButton(text=tr(callback.from_user.id,"back"), callback_data="menu:home", style="danger")],
    ])
    await edit(callback, tr(callback.from_user.id,"support_text",username=html.escape(username)), kb)


@router.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery):
    text = (
        f"❓ <b>Help / How to use {html.escape(BOT_NAME)}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🛍 <b>1) View Products</b>\n"
        "• Tap <b>🛒 Shop</b>\n"
        "• Choose a product from the list\n"
        "• See price, stock, validity & warranty\n\n"
        "🛒 <b>2) How to Buy</b>\n"
        "• Open product → set quantity (➖ / ➕)\n"
        "• Tap <b>🛒 Buy Now</b>\n"
        "• Confirm purchase\n"
        "• Pay with <b>Wallet</b> or <b>Direct Pay</b> (Binance / USDT)\n"
        "• After payment, product is delivered automatically in chat\n"
        "• Use <b>📋 Copy Product Details</b> to save your item\n\n"
        "💰 <b>3) How to Topup Wallet</b>\n"
        "• Tap <b>💰 Topup Wallet</b>\n"
        "• Send the amount (example: 10)\n"
        "• Choose payment method\n"
        "• Pay the invoice\n"
        "• Balance is added automatically after payment confirms\n\n"
        "📧 <b>4) Email & Invoice</b>\n"
        "• Email is <b>optional</b>\n"
        "• After your <b>first delivery</b>, bot may ask to set email\n"
        "• You can Set Email or Skip\n"
        "• If you set email → a <b>6-digit code</b> is sent to your inbox\n"
        "• Enter the code in the bot to verify\n"
        "• After verify, PDF invoice is emailed to you\n"
        "• Next purchases: invoice goes automatically to the same email\n"
        "• Change email anytime from <b>Settings → Email</b>\n\n"
        "💳 <b>5) Balance</b>\n"
        "• Tap <b>💳 Balance</b> to see wallet balance\n\n"
        "🎁 <b>6) Refer & Earn</b>\n"
        "• Tap <b>🎁 Refer & Earn</b>\n"
        "• Share your link — earn bonus when friends join & buy\n\n"
        "⚙️ <b>7) Settings</b>\n"
        "• Email, Language, Region, AI Chat, My Orders\n\n"
        "🎧 <b>8) Support</b>\n"
        "• Tap <b>🎧 Support</b> for help from admin\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"❤️ Thank you for using <b>{html.escape(BOT_NAME)}</b>!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Go to Shop", callback_data="menu:products", style="success")],
        [InlineKeyboardButton(text="💰 Topup Wallet", callback_data="menu:topup", style="primary")],
        [InlineKeyboardButton(text="◀ Back", callback_data="menu:home", style="danger")],
    ])
    await edit(callback, text, kb)


# =========================
# ADMIN ROUTES
# =========================

@router.message(Command("admin"))
async def admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🛠 <b>Admin Panel</b>", reply_markup=admin_kb())



@router.callback_query(F.data == "menu:ai")
async def ai_chat_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIChatState.waiting)
    await edit(
        callback,
        "🤖 <b>AI Shop Assistant</b>\n\n"
        "Ask me in any language about our products, prices, stock, plans, "
        "how to buy, wallet, email verification, or orders.\n\n"
        "Type your question below:",
        back_home(callback.from_user.id),
    )


@router.message(AIChatState.waiting)
async def ai_chat_message(message: Message, state: FSMContext):
    if not AI_API_KEY:
        await message.answer(
            "🤖 AI Chat is not configured yet.\n\n"
            "Admin: open <b>/admin → Bot Settings → 🤖 AI Chat (OpenAI)</b>\n"
            "and set the API Key + Model."
        )
        return

    products = list_products()
    catalog = "\n".join(
        f"- {p['name']} | {money(p['price'])} {INVOICE_CURRENCY} | stock={stock_count(p['id'])} | plan={p['category'] or '-'}"
        for p in products
    )
    system_prompt = (
        f"You are the multilingual shopping assistant for {BOT_NAME}. "
        "Understand and answer the customer's language automatically. "
        "Only state product facts supported by this live catalog. "
        "Explain how to browse, select, confirm, pay, set/verify email, and check orders. "
        "Never invent products, prices, stock, discounts, or policies.\n\n"
        f"LIVE CATALOG:\n{catalog or 'No products currently available.'}"
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": AI_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message.text or ""},
                    ],
                    "temperature": 0.2,
                },
            ) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    raise RuntimeError(data.get("error", {}).get("message", "AI request failed"))
                answer = data["choices"][0]["message"]["content"]
        await message.answer(answer[:4000], reply_markup=back_home(message.from_user.id))
    except Exception as exc:
        logging.exception("AI chat failed")
        await message.answer("❌ AI is temporarily unavailable. Please try again later.")


@router.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Not allowed.", show_alert=True)
    users = list_users(40)
    rows=[]
    for u in users:
        name = html.escape(str(u["full_name"] or u["telegram_id"]))[:24]
        status = "🚫" if int(u["blocked"] or 0) else "✅"
        action = "unban" if int(u["blocked"] or 0) else "ban"
        rows.append([InlineKeyboardButton(
            text=f"{status} {name} • {u['telegram_id']}",
            callback_data=f"admin:user:{action}:{u['telegram_id']}",
            style="danger" if action=="ban" else "success",
        )])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data="admin:home", style="primary")])
    await edit(callback, "👥 <b>Users / Ban</b>\n\nTap a user to ban or unban.", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("admin:user:"))
async def admin_user_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Not allowed.", show_alert=True)
    _, _, action, raw_uid = callback.data.split(":", 3)
    uid = int(raw_uid)
    set_user_blocked(uid, action == "ban")
    await callback.answer("Banned." if action == "ban" else "Unbanned.")
    await admin_users(callback)


@router.callback_query(F.data.startswith("admin:planstock:"))
async def admin_plan_stock(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Not allowed.", show_alert=True)
    pid = int(callback.data.split(":")[-1])
    p = get_product(pid)
    if not p:
        return await callback.answer("Plan/product not found.", show_alert=True)
    await state.set_state(AdminStockState.items)
    await state.update_data(pid=pid)
    await edit(
        callback,
        f"📥 <b>Add Stock</b>\n\n📂 Plan: <b>{html.escape(p['category'] or p['name'])}</b>\n"
        f"📦 Product: <b>{html.escape(p['name'])}</b>\n\n"
        "Send stock items, one item per line.",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="admin:cancel", style="danger")]]),
    )


@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await edit(callback, "🛠 <b>Admin Panel</b>", admin_kb())


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminBroadcastState.waiting)
    await edit(
        callback,
        "📣 <b>Broadcast Message</b>\n\n"
        "Send the message now.\n"
        "Text, photo, video or caption — all supported.\n\n"
        "It will be sent to:\n"
        "• All bot users\n"
        "• Public channel\n"
        "• Official connected channels\n\n"
        "Tap Cancel to stop.",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Cancel", callback_data="admin:home", style="danger")
        ]]),
    )


def broadcast_channel_ids():
    ids = []
    for raw in (PUBLIC_CHANNEL_ID, CHANNEL_1_ID, CHANNEL_2_ID):
        val = str(raw or "").strip()
        if val and val not in ids:
            ids.append(val)
    return ids


@router.message(AdminBroadcastState.waiting)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    status = await message.answer("⏳ Sending broadcast...")
    with db() as con:
        users = con.execute("SELECT telegram_id FROM users WHERE blocked=0").fetchall()
    user_ok = user_fail = 0
    for row in users:
        try:
            await message.bot.copy_message(row["telegram_id"], message.chat.id, message.message_id)
            user_ok += 1
        except Exception:
            user_fail += 1
        await asyncio.sleep(0.03)
    ch_ok = ch_fail = 0
    ch_lines = []
    for cid in broadcast_channel_ids():
        try:
            await message.bot.copy_message(cid, message.chat.id, message.message_id)
            ch_ok += 1
            ch_lines.append(f"✅ Channel <code>{html.escape(str(cid))}</code>")
        except Exception as exc:
            ch_fail += 1
            ch_lines.append(f"❌ Channel <code>{html.escape(str(cid))}</code>\n{html.escape(str(exc)[:80])}")
    if not broadcast_channel_ids():
        ch_lines.append("⚠️ No channel ID set. Add Public / Channel 1 / Channel 2 in Bot Settings.")
    with suppress(Exception):
        await status.delete()
    await message.answer(
        "📣 <b>Broadcast Done</b>\n\n"
        f"👤 Users sent: <b>{user_ok}</b>\n"
        f"👤 Users failed: <b>{user_fail}</b>\n"
        f"📢 Channels sent: <b>{ch_ok}</b>\n"
        f"📢 Channels failed: <b>{ch_fail}</b>\n\n"
        + "\n".join(ch_lines),
        reply_markup=admin_kb(),
    )


@router.callback_query(F.data == "admin:maintenance")
async def admin_maintenance(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    enabled = maintenance_enabled()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=("🔴 Disable Maintenance" if enabled else "🟢 Enable Maintenance"),
                callback_data=("admin:maintenance_off" if enabled else "admin:maintenance_on"),
                style=("success" if enabled else "danger"),
            )
        ],
        [InlineKeyboardButton(text="◀ Back", callback_data="admin:home", style="primary")],
    ])
    await edit(
        callback,
        "🛠 <b>Maintenance Mode</b>\n\n"
        f"Current Status: <b>{'ON 🔴' if enabled else 'OFF 🟢'}</b>\n\n"
        "When enabled:\n"
        "• Customers receive a maintenance notification\n"
        "• Shop/product purchase is temporarily blocked\n"
        "• Wallet top-up is temporarily blocked\n"
        "• Admin can continue using the bot\n"
        "• Status stays saved after bot restart",
        kb,
    )


@router.callback_query(F.data == "admin:maintenance_on")
async def admin_maintenance_on(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    if maintenance_enabled():
        return await callback.answer("Maintenance is already ON.", show_alert=True)
    set_app_setting("maintenance_mode", "1")
    await callback.answer("Maintenance mode enabled.", show_alert=True)
    await edit(
        callback,
        "🔴 <b>Maintenance Mode Enabled</b>\n\n"
        "Customers are being notified. Shop, purchases and top-ups are temporarily blocked.",
        admin_kb(),
    )
    await broadcast_maintenance(callback.bot, True)


@router.callback_query(F.data == "admin:maintenance_off")
async def admin_maintenance_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    if not maintenance_enabled():
        return await callback.answer("Maintenance is already OFF.", show_alert=True)
    set_app_setting("maintenance_mode", "0")
    await callback.answer("Maintenance mode disabled.", show_alert=True)
    await edit(
        callback,
        "🟢 <b>Maintenance Mode Disabled</b>\n\n"
        "Premium Hub Store is available normally again.",
        admin_kb(),
    )
    await broadcast_maintenance(callback.bot, False)



def bot_settings_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢 Public Channel", callback_data="admin:cfg:public_channel", style="primary"),
            InlineKeyboardButton(text="🚨 Admin Alert Channel", callback_data="admin:cfg:admin_alert", style="primary"),
        ],
        [
            InlineKeyboardButton(text="1️⃣ Channel 1", callback_data="admin:cfg:channel1", style="success"),
            InlineKeyboardButton(text="2️⃣ Channel 2", callback_data="admin:cfg:channel2", style="success"),
        ],
        [
            InlineKeyboardButton(text="🎁 Referral Bonus", callback_data="admin:cfg:referral", style="primary"),
            InlineKeyboardButton(text="📧 SMTP / Email", callback_data="admin:cfg:smtp", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🤖 AI Chat (OpenAI)", callback_data="admin:cfg:ai", style="success"),
            InlineKeyboardButton(text="💳 PayHub API", callback_data="admin:cfg:payhub", style="primary"),
        ],
        [InlineKeyboardButton(text="◀ Back", callback_data="admin:home", style="danger")],
    ])


@router.callback_query(F.data == "admin:bot_settings")
async def admin_bot_settings(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await edit(
        callback,
        "⚙️ <b>Bot Settings</b>\n\n"
        "Configure channels, referral, SMTP, AI Chat and Payment API from Admin Panel.\n"
        "These values are saved in the database and stay after restart.",
        bot_settings_kb(),
    )


@router.callback_query(F.data.startswith("admin:cfg:"))
async def admin_cfg_choose(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    section = callback.data.split(":", 2)[2]
    await state.set_state(AdminConfigState.waiting)
    await state.update_data(cfg_section=section)

    prompt = {
        "public_channel":
            "📢 <b>Public Sales Channel</b>\n\n"
            "Send exactly 2 lines:\n"
            "1) Channel ID  (example: -1001234567890)\n"
            "2) Channel URL (example: https://t.me/premiumhubstore)",
        "admin_alert":
            "🚨 <b>Admin Alert Channel</b>\n\n"
            "Send the private admin alert Channel ID.\n"
            "Example: -1001234567890",
        "channel1":
            "1️⃣ <b>Customer Channel 1</b>\n\n"
            "Send exactly 3 lines:\n"
            "1) Channel Name\n"
            "2) Channel ID\n"
            "3) Channel URL",
        "channel2":
            "2️⃣ <b>Customer Channel 2</b>\n\n"
            "Send exactly 3 lines:\n"
            "1) Channel Name\n"
            "2) Channel ID\n"
            "3) Channel URL",
        "referral":
            f"🎁 <b>Referral Bonus</b>\n\n"
            f"Send the reward amount in {INVOICE_CURRENCY}.\n"
            "Example: 0.25\n"
            "Send 0 to disable referral rewards.",
        "smtp":
            "📧 <b>SMTP / Invoice Email</b>\n\n"
            "Send exactly 5 lines:\n"
            "1) SMTP Host       (smtp.gmail.com)\n"
            "2) SMTP Port       (587)\n"
            "3) SMTP Email/User\n"
            "4) Gmail App Password\n"
            "5) From Email      (or type SAME)",
        "ai":
            "🤖 <b>AI Chat (OpenAI)</b>\n\n"
            "Send exactly 2 lines:\n"
            "1) OpenAI API Key   (sk-...)\n"
            "2) Model name       (example: gpt-4.1-mini)\n\n"
            "Get key from: platform.openai.com → API Keys\n"
            "Billing/credit must be active on the OpenAI account.",
        "payhub":
            "💳 <b>PayHub API</b>\n\n"
            "Send exactly 3 lines:\n"
            "1) PAYMENT_BASE_URL   (http://127.0.0.1:8000)\n"
            "2) PAYMENT_API_KEY    (pk_live_xxxx)\n"
            "3) PAYMENT_WEBHOOK_SECRET (whsec_xxxx)\n\n"
            "Used for Direct Pay / Wallet Topup.",
        "binance":
            "💳 Use <b>PayHub API</b> instead.",
    }.get(section, "Send the required configuration.")

    await edit(
        callback,
        prompt,
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Cancel", callback_data="admin:bot_settings", style="danger")
        ]]),
    )


@router.message(AdminConfigState.waiting)
async def admin_cfg_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    section = data.get("cfg_section")
    lines = [x.strip() for x in (message.text or "").splitlines() if x.strip()]

    try:
        if section == "public_channel":
            if len(lines) != 2:
                raise ValueError("Please send exactly 2 lines: Channel ID and Channel URL.")
            set_app_setting("public_channel_id", lines[0])
            set_app_setting("public_channel_url", lines[1])

        elif section == "admin_alert":
            if len(lines) != 1:
                raise ValueError("Please send only the Channel ID.")
            set_app_setting("admin_alert_channel_id", lines[0])

        elif section in ("channel1", "channel2"):
            if len(lines) != 3:
                raise ValueError("Please send exactly 3 lines: Name, ID and URL.")
            prefix = "channel_1" if section == "channel1" else "channel_2"
            set_app_setting(prefix + "_name", lines[0])
            set_app_setting(prefix + "_id", lines[1])
            set_app_setting(prefix + "_url", lines[2])

        elif section == "referral":
            if len(lines) != 1:
                raise ValueError("Please send one amount only.")
            amount = Decimal(lines[0])
            if amount < 0:
                raise ValueError("Referral bonus cannot be negative.")
            set_app_setting("referral_bonus", str(amount))

        elif section == "smtp":
            if len(lines) != 5:
                raise ValueError("Please send exactly 5 SMTP lines.")
            port = int(lines[1])
            if port <= 0:
                raise ValueError("Invalid SMTP port.")
            set_app_setting("smtp_host", lines[0])
            set_app_setting("smtp_port", str(port))
            set_app_setting("smtp_user", lines[2])
            set_app_setting("smtp_password", lines[3])
            set_app_setting("smtp_from", lines[2] if lines[4].upper() == "SAME" else lines[4])

        elif section == "ai":
            if len(lines) != 2:
                raise ValueError("Please send exactly 2 lines: API Key and Model.")
            key = lines[0].strip()
            model = lines[1].strip() or "gpt-4.1-mini"
            if not key.startswith("sk-"):
                raise ValueError("OpenAI API Key usually starts with sk-")
            set_app_setting("ai_api_key", key)
            set_app_setting("ai_model", model)

        elif section in ("payhub", "binance"):
            if len(lines) != 3:
                raise ValueError("Please send exactly 3 lines: BASE URL, API Key, Webhook Secret.")
            base = lines[0].strip().rstrip("/")
            key = lines[1].strip()
            secret = lines[2].strip()
            if not base.startswith("http"):
                raise ValueError("PAYMENT_BASE_URL must start with http:// or https://")
            if not key:
                raise ValueError("PAYMENT_API_KEY cannot be empty.")
            set_app_setting("payment_base_url", base)
            set_app_setting("payment_api_key", key)
            set_app_setting("payment_webhook_secret", secret)

        else:
            raise ValueError("Unknown settings section.")

    except (ValueError, InvalidOperation) as exc:
        return await message.answer(
            f"❌ {html.escape(str(exc))}\n\nPlease send the values again."
        )

    refresh_runtime_settings()
    with suppress(Exception):
        await message.delete()
    await state.clear()

    await message.answer(
        "✅ <b>Settings Saved</b>\n\n"
        "The new configuration is active immediately.",
        reply_markup=bot_settings_kb(),
    )


@router.callback_query(F.data == "admin:api_manager")
async def admin_api_manager(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    providers = list_api_providers()
    lines = ["🔌 <b>API Manager</b>", "", "Connect reseller APIs without exposing credentials to customers."]
    for p in providers:
        lines.append(f"• #{p['id']} <b>{html.escape(p['name'])}</b> — {'Active' if p['active'] else 'Disabled'}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add API Provider", callback_data="admin:api_add", style="success")],
        [InlineKeyboardButton(text="◀ Back", callback_data="admin:home", style="danger")],
    ])
    await edit(callback, "\n".join(lines), kb)


@router.callback_query(F.data == "admin:api_add")
async def admin_api_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.set_state(AdminAPIState.name)
    await edit(callback, "🔌 <b>Add API Provider</b>\n\nSend Provider Name (example: ProdSeller):", back_home())


@router.message(AdminAPIState.name)
async def api_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(AdminAPIState.base_url)
    await message.answer("Send Base URL (example: https://prodseller.com/v1):")


@router.message(AdminAPIState.base_url)
async def api_base(message: Message, state: FSMContext):
    await state.update_data(base_url=(message.text or "").strip())
    await state.set_state(AdminAPIState.api_key)
    await message.answer("Send API Key. It will be stored privately in the bot database:")


@router.message(AdminAPIState.api_key)
async def api_key(message: Message, state: FSMContext):
    await state.update_data(api_key=(message.text or "").strip())
    with suppress(Exception): await message.delete()
    await state.set_state(AdminAPIState.auth_header)
    await message.answer("Send API Auth Header name (example: X-API-Key):")


@router.message(AdminAPIState.auth_header)
async def api_auth(message: Message, state: FSMContext):
    await state.update_data(auth_header=(message.text or "").strip())
    await state.set_state(AdminAPIState.products_endpoint)
    await message.answer("Send Products endpoint (example: /products):")


@router.message(AdminAPIState.products_endpoint)
async def api_products_ep(message: Message, state: FSMContext):
    await state.update_data(products_endpoint=(message.text or "").strip())
    await state.set_state(AdminAPIState.balance_endpoint)
    await message.answer("Send Balance endpoint (example: /balance):")


@router.message(AdminAPIState.balance_endpoint)
async def api_balance_ep(message: Message, state: FSMContext):
    await state.update_data(balance_endpoint=(message.text or "").strip())
    await state.set_state(AdminAPIState.order_endpoint)
    await message.answer("Send Create Order endpoint (example: /orders):")


@router.message(AdminAPIState.order_endpoint)
async def api_order_ep(message: Message, state: FSMContext):
    await state.update_data(order_endpoint=(message.text or "").strip())
    await state.set_state(AdminAPIState.status_endpoint)
    await message.answer("Send Order Status endpoint (example: /orders/{id}):")


@router.message(AdminAPIState.status_endpoint)
async def api_status_ep(message: Message, state: FSMContext):
    await state.update_data(status_endpoint=(message.text or "").strip())
    data = await state.get_data()
    pid = add_api_provider(data)
    await state.clear()
    await message.answer(
        f"✅ API Provider Saved\n\nID: <code>{pid}</code>\nProvider: <b>{html.escape(data['name'])}</b>\n\n"
        "API credentials are admin-only and are never shown to customers.",
        reply_markup=admin_kb(),
    )


@router.callback_query(F.data == "admin:add_product")
async def admin_add_product(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.set_state(AdminProductState.name)
    await edit(callback, "➕ <b>Add Product</b>\n\n1/8 • Send Product Name:", back_home())


@router.message(AdminProductState.name)
async def ap_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    data = await state.get_data()
    if raw.upper() == "SKIP" and data.get("group_category"):
        await state.clear()
        return await message.answer("✅ <b>Variant Group Finished</b>", reply_markup=admin_kb())
    if not raw:
        return await message.answer("❌ Product name cannot be empty.")
    await state.update_data(name=raw)
    await state.set_state(AdminProductState.price)
    await message.answer(f"2/8 • Send Price in {INVOICE_CURRENCY}:")


@router.message(AdminProductState.price)
async def ap_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        price = Decimal((message.text or "").strip())
        if price <= 0: raise InvalidOperation
    except Exception:
        return await message.answer("❌ Invalid price. Send a positive number.")
    await state.update_data(price=str(price))
    await state.set_state(AdminProductState.validity)
    await message.answer("3/8 • Send Validity (example: 1 Month / 6 Months / 1 Year):")


@router.message(AdminProductState.validity)
async def ap_validity(message: Message, state: FSMContext):
    await state.update_data(validity=(message.text or "").strip())
    await state.set_state(AdminProductState.warranty)
    await message.answer("4/8 • Send Warranty (example: 30 Days / No Warranty):")


@router.message(AdminProductState.warranty)
async def ap_warranty(message: Message, state: FSMContext):
    await state.update_data(warranty=(message.text or "").strip())
    await state.set_state(AdminProductState.product_type)
    await message.answer("5/8 • Send Product Type (Full Account / Invite / Key / Link / Other):")


@router.message(AdminProductState.product_type)
async def ap_type(message: Message, state: FSMContext):
    await state.update_data(product_type=(message.text or "").strip())
    await state.set_state(AdminProductState.category)
    await message.answer("6/8 • Send Plan/Category (example: YouTube Premium — All Plans). Send SKIP for standalone:")


@router.message(AdminProductState.category)
async def ap_category(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    existing_group = (await state.get_data()).get("group_category")
    if existing_group:
        # While adding serial variants, keep every variant in the same group.
        category = existing_group
    else:
        category = "" if raw.upper() == "SKIP" else raw
        await state.update_data(group_category=category)
    await state.update_data(category=category)
    await state.set_state(AdminProductState.note)
    await message.answer("7/8 • Send View Note / Product Instructions:")


@router.message(AdminProductState.note)
async def ap_note(message: Message, state: FSMContext):
    await state.update_data(note=(message.text or "").strip())
    await state.set_state(AdminProductState.stock)
    await message.answer("8/8 • Send Initial Stock Items, one item per line.\nSend SKIP for no stock:")


@router.message(AdminProductState.stock)
async def ap_stock(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    pid = create_product(
        data["name"], Decimal(data["price"]), data["validity"], data["warranty"],
        data["product_type"], data.get("category", ""), data["note"]
    )
    raw = (message.text or "").strip()
    added = 0 if raw.upper() == "SKIP" else add_stock(pid, raw.splitlines())

    category = str(data.get("category") or "").strip()
    if category:
        # Keep the group data in FSM and offer serial variant creation.
        await state.update_data(group_category=category)
        await state.set_state(AdminProductState.name)
        await message.answer(
            "✅ <b>Variant Added</b>\n\n"
            f"🆔 ID: <code>{pid}</code>\n"
            f"📦 {html.escape(data['name'])}\n"
            f"📂 Group: <b>{html.escape(category)}</b>\n"
            f"📊 Stock: <b>{added}</b>\n\n"
            "➕ <b>Add another variant?</b>\n"
            "Send the next variant name to continue, or send <b>SKIP</b> to finish.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Add Another Variant", callback_data="admin:variant_next", style="success")],
                [InlineKeyboardButton(text="✅ Finish", callback_data="admin:variant_finish", style="primary")],
            ]),
        )
        await broadcast_new_product(message.bot, pid)
        await safe_send(message.bot, ADMIN_ALERT_CHANNEL_ID, f"✅ <b>NEW VARIANT CREATED</b>\n\n📦 {html.escape(data['name'])}\n📂 {html.escape(category)}\n📊 Stock: <b>{added}</b>")
        return

    await state.clear()
    await message.answer(
        "✅ <b>Product Added Successfully</b>\n\n"
        f"🆔 ID: <code>{pid}</code>\n"
        f"📦 {html.escape(data['name'])}\n"
        f"💰 {money(data['price'])} {INVOICE_CURRENCY}\n"
        f"📅 Validity: {html.escape(data['validity'])}\n"
        f"🛡 Warranty: {html.escape(data['warranty'])}\n"
        f"🏷 Type: {html.escape(data['product_type'])}\n"
        f"📂 Plan: Standalone\n"
        f"📊 Initial Stock: <b>{added}</b>",
        reply_markup=admin_kb()
    )
    await broadcast_new_product(message.bot, pid)
    await safe_send(message.bot, ADMIN_ALERT_CHANNEL_ID, f"✅ <b>NEW PRODUCT CREATED</b>\n\n📦 {html.escape(data['name'])}\n📊 Stock: <b>{added}</b>")


@router.callback_query(F.data == "admin:variant_next")
async def admin_variant_next(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    category = str(data.get("group_category") or "").strip()
    if not category:
        await state.clear()
        return await edit(callback, "❌ Variant group is missing.", admin_kb())
    await state.set_state(AdminProductState.name)
    await edit(
        callback,
        f"➕ <b>Add Next Variant</b>\n\n📂 Group: <b>{html.escape(category)}</b>\n\n"
        "1/8 • Send Variant Product Name:",
        back_home(),
    )


@router.callback_query(F.data == "admin:variant_finish")
async def admin_variant_finish(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await edit(callback, "✅ <b>Variant Group Saved</b>", admin_kb())




@router.callback_query(F.data == "admin:add_stock")
async def admin_add_stock(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.set_state(AdminStockState.product_id)
    await callback.message.edit_text("Send Product ID:")
    await callback.answer()


@router.message(AdminStockState.product_id)
async def as_pid(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try: pid = int((message.text or "").strip())
    except: return await message.answer("Invalid Product ID")
    if not get_product(pid): return await message.answer("Product not found")
    await state.update_data(pid=pid)
    await state.set_state(AdminStockState.items)
    await message.answer("Send stock items, one per line:")


@router.message(AdminStockState.items)
async def as_items(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data(); pid = int(data["pid"])
    added = add_stock(pid, (message.text or "").splitlines())
    total = stock_count(pid)
    p = get_product(pid)
    await state.clear()
    await message.answer(f"✅ Added <b>{added}</b> stock item(s).\nTotal stock: <b>{total}</b>", reply_markup=admin_kb())
    await safe_send(message.bot, ADMIN_ALERT_CHANNEL_ID, f"✅ <b>STOCK ADDED</b>\n\n📦 {html.escape(p['name'])}\n➕ Added: <b>{added}</b>\n📊 Total Stock: <b>{total}</b>")
    await broadcast_stock_added(message.bot, pid, added)
    await process_waiting_orders(message.bot, pid)


@router.callback_query(F.data == "admin:change_price")
async def admin_change_price(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    available_products = [p for p in list_products() if stock_count(p["id"]) > 0]
    if not available_products:
        await callback.answer("No in-stock products available.", show_alert=True)
        return
    lines = ["💲 <b>Change Product Price</b>", "", "In-stock products:"]
    for p in available_products[:30]:
        lines.append(f"ID <code>{p['id']}</code> • {html.escape(p['name'])} • {money(p['price'])} {INVOICE_CURRENCY} • 📦{stock_count(p['id'])}")
    lines.append("\nSend Product ID:")
    await state.set_state(AdminPriceState.product_id)
    await callback.message.edit_text("\n".join(lines))
    await callback.answer()


@router.message(AdminPriceState.product_id)
async def change_price_product_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        return await message.answer("❌ Send a valid Product ID.")
    pid = int(raw)
    p = get_product(pid)
    if not p:
        return await message.answer("❌ Product not found.")
    current_stock = stock_count(pid)
    if current_stock <= 0:
        return await message.answer("❌ This product has no stock. Price can only be changed for in-stock products.")
    await state.update_data(price_pid=pid, old_price=str(p["price"]))
    await state.set_state(AdminPriceState.new_price)
    await message.answer(
        f"📦 <b>{html.escape(p['name'])}</b>\n"
        f"💵 Current Price: <b>{money(p['price'])} {INVOICE_CURRENCY}</b>\n"
        f"📦 Stock: <b>{current_stock}</b>\n\n"
        f"Send the new price:"
    )


@router.message(AdminPriceState.new_price)
async def change_price_new_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        new_price = Decimal((message.text or "").strip())
        if new_price <= 0:
            raise InvalidOperation
    except Exception:
        return await message.answer("❌ Send a valid positive price.")

    data = await state.get_data()
    pid = int(data["price_pid"])
    p = get_product(pid)
    if not p:
        await state.clear()
        return await message.answer("❌ Product not found.", reply_markup=admin_kb())
    if stock_count(pid) <= 0:
        await state.clear()
        return await message.answer("❌ Stock is now 0, so price was not changed.", reply_markup=admin_kb())

    old_price = Decimal(str(p["price"]))
    update_product_price(pid, new_price)
    await state.clear()

    direction = "📉 Decreased" if new_price < old_price else "📈 Increased" if new_price > old_price else "🔄 Updated"
    await message.answer(
        f"✅ <b>Price Changed</b>\n\n"
        f"📦 {html.escape(p['name'])}\n"
        f"💵 Old: <b>{money(old_price)} {INVOICE_CURRENCY}</b>\n"
        f"✨ New: <b>{money(new_price)} {INVOICE_CURRENCY}</b>\n"
        f"{direction}\n"
        f"📦 Stock: <b>{stock_count(pid)}</b>",
        reply_markup=admin_kb(),
    )
    if new_price != old_price:
        await broadcast_price_update(message.bot, pid, old_price, new_price)


@router.callback_query(F.data == "admin:delete_product")
async def admin_delete_product(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    products = list_products()
    if not products:
        return await callback.answer("No products found.", show_alert=True)
    lines = ["🗑 <b>Delete Product</b>", "", "Send the Product ID you want to delete:"]
    for p in products[:30]:
        lines.append(f"<code>{p['id']}</code> • {html.escape(p['name'])} • 📦{stock_count(p['id'])}")
    await state.set_state(AdminDeleteProductState.product_id)
    await callback.message.edit_text("\n".join(lines))
    await callback.answer()


@router.message(AdminDeleteProductState.product_id)
async def admin_delete_product_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        return await message.answer("❌ Send a valid Product ID.")
    pid = int(raw)
    p = get_product(pid)
    if not p:
        return await message.answer("❌ Product not found.")
    await state.update_data(delete_pid=pid)
    await state.set_state(AdminDeleteProductState.confirm)
    await message.answer(
        "⚠️ <b>Confirm Product Delete</b>\n\n"
        f"📦 {html.escape(p['name'])}\n"
        f"💵 Price: {money(p['price'])} {INVOICE_CURRENCY}\n"
        f"📊 Available Stock: {stock_count(pid)}\n\n"
        "This will hide the product and remove its unsold stock. Existing order history will stay saved.\n"
        "Customers will NOT receive a notification.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Yes, Delete", callback_data="admin:delete_product_confirm", style="danger")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:cancel", style="primary")],
        ]),
    )


@router.callback_query(F.data == "admin:delete_product_confirm")
async def admin_delete_product_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    pid = int(data.get("delete_pid", 0))
    p = get_product(pid)
    if not p:
        await state.clear()
        return await edit(callback, "❌ Product not found or already deleted.", admin_kb())
    removed_stock = soft_delete_product(pid)
    await state.clear()
    await edit(
        callback,
        f"✅ <b>Product Deleted</b>\n\n📦 {html.escape(p['name'])}\n🧹 Unsold stock removed: <b>{removed_stock}</b>\n\n🔕 No customer notification was sent.",
        admin_kb(),
    )


@router.callback_query(F.data == "admin:delete_stock")
async def admin_delete_stock(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()

    products = [p for p in list_products() if stock_count(p["id"]) > 0]
    if not products:
        return await callback.answer("No available stock to delete.", show_alert=True)

    lines = [
        "🧹 <b>Delete Added Stock</b>",
        "",
        "Select a product to view the exact stock items you added:",
        "",
    ]
    rows = []
    for serial, p in enumerate(products[:40], start=1):
        available = stock_count(p["id"])
        lines.append(f"<b>{serial}.</b> {html.escape(p['name'])} — Stock: <b>{available}</b>")
        rows.append([
            InlineKeyboardButton(
                text=f"{serial}. 📦 {p['name']} • Stock: {available}",
                callback_data=f"admin:delete_stock_pick:{p['id']}",
                style="primary",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="◀ Back",
            callback_data="admin:home",
            style="danger",
        )
    ])

    await edit(
        callback,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _stock_preview(value: str, max_len: int = 44) -> str:
    value = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


async def show_exact_stock_items(callback: CallbackQuery, pid: int, page: int = 1):
    p = get_product(pid)
    if not p:
        return await callback.answer("Product not found.", show_alert=True)

    items = list_available_stock_items(pid)
    if not items:
        return await callback.answer("This product has no available stock.", show_alert=True)

    per_page = 10
    pages = max(1, ceil(len(items) / per_page))
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    chunk = items[start:start + per_page]

    lines = [
        f"🧹 <b>Delete Stock — {html.escape(p['name'])}</b>",
        "",
        f"📊 Available Stock: <b>{len(items)}</b>",
        "",
        "Tap the exact stock item you want to delete:",
        "",
    ]

    rows = []
    for absolute_index, item in enumerate(chunk, start=start + 1):
        content = str(item["content"])
        lines.append(
            f"<b>{absolute_index}.</b> <code>{html.escape(content)}</code>"
        )
        rows.append([
            InlineKeyboardButton(
                text=f"🗑 {absolute_index}. {_stock_preview(content)}",
                callback_data=f"admin:delete_exact:{pid}:{item['id']}:{page}",
                style="danger",
            )
        ])

    if pages > 1:
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅ Prev",
                    callback_data=f"admin:delete_stock_page:{pid}:{page-1}",
                    style="primary",
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"📊 {page}/{pages}",
                callback_data="noop",
                style="primary",
            )
        )
        if page < pages:
            nav.append(
                InlineKeyboardButton(
                    text="Next ➡",
                    callback_data=f"admin:delete_stock_page:{pid}:{page+1}",
                    style="primary",
                )
            )
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(
            text="🗑 Delete ALL Stock",
            callback_data=f"admin:delete_exact_all_confirm:{pid}",
            style="danger",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="◀ Back to Products",
            callback_data="admin:delete_stock",
            style="primary",
        )
    ])

    await edit(
        callback,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("admin:delete_stock_pick:"))
async def admin_delete_stock_pick(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    pid = int(callback.data.rsplit(":", 1)[1])
    await show_exact_stock_items(callback, pid, 1)


@router.callback_query(F.data.startswith("admin:delete_stock_page:"))
async def admin_delete_stock_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    _, _, _, pid, page = callback.data.split(":")
    await show_exact_stock_items(callback, int(pid), int(page))


@router.callback_query(F.data.startswith("admin:delete_exact:"))
async def admin_delete_exact(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    _, _, pid_raw, item_id_raw, page_raw = callback.data.split(":")
    pid = int(pid_raw)
    item_id = int(item_id_raw)
    page = int(page_raw)

    p = get_product(pid)
    if not p:
        return await callback.answer("Product not found.", show_alert=True)

    item = None
    for row in list_available_stock_items(pid):
        if int(row["id"]) == item_id:
            item = row
            break

    if not item:
        return await callback.answer("This stock item was already removed.", show_alert=True)

    content = str(item["content"])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Delete This Stock",
                callback_data=f"admin:delete_exact_confirm:{pid}:{item_id}:{page}",
                style="danger",
            )
        ],
        [
            InlineKeyboardButton(
                text="◀ Cancel",
                callback_data=f"admin:delete_stock_page:{pid}:{page}",
                style="primary",
            )
        ],
    ])

    await edit(
        callback,
        "⚠️ <b>Delete This Exact Stock?</b>\n\n"
        f"📦 Product: <b>{html.escape(p['name'])}</b>\n"
        f"🆔 Stock Item ID: <code>{item_id}</code>\n\n"
        f"<code>{html.escape(content)}</code>\n\n"
        "🔕 Customers will NOT receive a notification.",
        kb,
    )


@router.callback_query(F.data.startswith("admin:delete_exact_confirm:"))
async def admin_delete_exact_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    _, _, _, pid_raw, item_id_raw, page_raw = callback.data.split(":")
    pid = int(pid_raw)
    item_id = int(item_id_raw)
    page = int(page_raw)

    deleted = delete_stock_item_by_id(pid, item_id)
    if not deleted:
        return await callback.answer("This stock item was already removed.", show_alert=True)

    remaining = stock_count(pid)
    await callback.answer("✅ Stock item deleted.", show_alert=False)

    if remaining <= 0:
        return await edit(
            callback,
            "✅ <b>Stock Item Deleted</b>\n\n"
            "📦 Remaining Stock: <b>0</b>\n"
            "🔕 No customer notification was sent.",
            InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀ Back to Products",
                        callback_data="admin:delete_stock",
                        style="primary",
                    )
                ]
            ]),
        )

    items = list_available_stock_items(pid)
    pages = max(1, ceil(len(items) / 10))
    page = min(page, pages)
    await show_exact_stock_items(callback, pid, page)


@router.callback_query(F.data.startswith("admin:delete_exact_all_confirm:"))
async def admin_delete_exact_all_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    pid = int(callback.data.rsplit(":", 1)[1])
    p = get_product(pid)
    if not p:
        return await callback.answer("Product not found.", show_alert=True)

    items = list_available_stock_items(pid)
    if not items:
        return await callback.answer("No available stock to delete.", show_alert=True)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"✅ Delete ALL ({len(items)})",
                callback_data=f"admin:delete_exact_all_do:{pid}",
                style="danger",
            )
        ],
        [
            InlineKeyboardButton(
                text="◀ Cancel",
                callback_data=f"admin:delete_stock_pick:{pid}",
                style="primary",
            )
        ],
    ])

    await edit(
        callback,
        "⚠️ <b>Delete ALL Available Stock?</b>\n\n"
        f"📦 Product: <b>{html.escape(p['name'])}</b>\n"
        f"🧹 Stock items to delete: <b>{len(items)}</b>\n\n"
        "This removes only unsold AVAILABLE stock.\n"
        "Existing order history and sold items are not touched.\n\n"
        "🔕 Customers will NOT receive a notification.",
        kb,
    )


@router.callback_query(F.data.startswith("admin:delete_exact_all_do:"))
async def admin_delete_exact_all_do(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    pid = int(callback.data.rsplit(":", 1)[1])
    p = get_product(pid)
    if not p:
        return await callback.answer("Product not found.", show_alert=True)

    deleted = delete_available_stock(pid, None)

    await edit(
        callback,
        "✅ <b>All Available Stock Deleted</b>\n\n"
        f"📦 Product: <b>{html.escape(p['name'])}</b>\n"
        f"🧹 Deleted: <b>{deleted}</b>\n"
        "📊 Remaining: <b>0</b>\n\n"
        "🔕 No customer notification was sent.",
        InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀ Back to Products",
                    callback_data="admin:delete_stock",
                    style="primary",
                )
            ]
        ]),
    )


@router.callback_query(F.data == "admin:edit_product")
async def admin_edit_product(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    products = list_products()
    if not products:
        return await callback.answer("No products found.", show_alert=True)
    lines = ["✏️ <b>Edit Product Information</b>", "", "Send Product ID:"]
    for p in products[:30]:
        lines.append(f"<code>{p['id']}</code> • {html.escape(p['name'])}")
    await state.set_state(AdminEditProductState.product_id)
    await callback.message.edit_text("\n".join(lines))
    await callback.answer()


@router.message(AdminEditProductState.product_id)
async def admin_edit_product_pid(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        return await message.answer("❌ Send a valid Product ID.")
    pid = int(raw)
    p = get_product(pid)
    if not p:
        return await message.answer("❌ Product not found.")
    await state.update_data(edit_pid=pid)
    await state.set_state(AdminEditProductState.field)
    await message.answer(
        f"✏️ <b>Edit Product</b>\n\n"
        f"📦 Name: <b>{html.escape(p['name'])}</b>\n"
        f"💵 Price: <b>{money(p['price'])} {INVOICE_CURRENCY}</b>\n"
        f"🛡 Warranty: <b>{html.escape(p['warranty'])}</b>\n"
        f"📝 Note/Details: {html.escape(p['note'] or 'Not set')}\n\n"
        "Choose what you want to edit:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Name", callback_data="admin:edit_field:name", style="primary"),
                InlineKeyboardButton(text="💵 Price", callback_data="admin:edit_field:price", style="success"),
            ],
            [
                InlineKeyboardButton(text="🛡 Warranty", callback_data="admin:edit_field:warranty", style="primary"),
                InlineKeyboardButton(text="📝 Details / Note", callback_data="admin:edit_field:note", style="success"),
            ],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:cancel", style="danger")],
        ]),
    )


@router.callback_query(F.data.startswith("admin:edit_field:"))
async def admin_edit_field(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    field = callback.data.rsplit(":", 1)[1]
    if field not in {"name", "price", "warranty", "note"}:
        return await callback.answer("Invalid field.", show_alert=True)
    data = await state.get_data()
    pid = int(data.get("edit_pid", 0))
    if not get_product(pid):
        await state.clear()
        return await edit(callback, "❌ Product not found.", admin_kb())
    await state.update_data(edit_field=field)
    await state.set_state(AdminEditProductState.value)
    labels = {
        "name": "Send the new product name:",
        "price": f"Send the new price in {INVOICE_CURRENCY}:",
        "warranty": "Send the new warranty text:",
        "note": "Send the new product details / View Note text:",
    }
    await callback.message.edit_text(labels[field])
    await callback.answer()


@router.message(AdminEditProductState.value)
async def admin_edit_product_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    pid = int(data.get("edit_pid", 0))
    field = data.get("edit_field")
    p = get_product(pid)
    if not p or field not in {"name", "price", "warranty", "note"}:
        await state.clear()
        return await message.answer("❌ Edit session expired.", reply_markup=admin_kb())

    raw = (message.text or "").strip()
    if not raw:
        return await message.answer("❌ Value cannot be empty.")

    if field == "price":
        try:
            new_price = Decimal(raw)
            if new_price <= 0:
                raise InvalidOperation
        except Exception:
            return await message.answer("❌ Send a valid positive price.")
        old_price = Decimal(str(p["price"]))
        update_product_price(pid, new_price)
        await state.clear()
        await message.answer(
            f"✅ <b>Product Price Updated</b>\n\n📦 {html.escape(p['name'])}\n💵 Old: {money(old_price)} {INVOICE_CURRENCY}\n✨ New: {money(new_price)} {INVOICE_CURRENCY}",
            reply_markup=admin_kb(),
        )
        # Keep the earlier requirement: price changes are announced to users + public channel.
        if new_price != old_price and stock_count(pid) > 0:
            await broadcast_price_update(message.bot, pid, old_price, new_price)
        return

    old_value = str(p[field] or "")
    update_product_field(pid, field, raw)
    await state.clear()
    label = {"name": "Name", "warranty": "Warranty", "note": "Details / Note"}[field]
    await message.answer(
        f"✅ <b>{label} Updated</b>\n\n"
        f"📦 Product ID: <code>{pid}</code>\n"
        f"Old: {html.escape(old_value or 'Not set')}\n"
        f"New: <b>{html.escape(raw)}</b>\n\n"
        "🔕 No customer notification was sent for this information edit.",
        reply_markup=admin_kb(),
    )


@router.callback_query(F.data == "admin:add_balance")
async def admin_add_balance(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.set_state(AdminBalanceState.user)
    await callback.message.edit_text("Send user's Telegram ID or @username:")
    await callback.answer()


@router.message(AdminBalanceState.user)
async def ab_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    raw = (message.text or "").strip()
    with db() as con:
        if raw.startswith("@"):
            u = con.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (raw[1:],)).fetchone()
        elif raw.isdigit():
            u = con.execute("SELECT * FROM users WHERE telegram_id=?", (int(raw),)).fetchone()
        else:
            u = None
    if not u: return await message.answer("User not found. The user must start the bot first.")
    await state.update_data(target_uid=u["telegram_id"], target_name=u["full_name"])
    await state.set_state(AdminBalanceState.amount)
    await message.answer(f"User: <b>{html.escape(u['full_name'])}</b>\nCurrent wallet: <b>{money(u['wallet'])}</b>\n\nSend amount to add:")


@router.message(AdminBalanceState.amount)
async def ab_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        amount = Decimal((message.text or "").strip())
        if amount <= 0: raise InvalidOperation
    except Exception:
        return await message.answer("Send a valid positive amount.")
    data = await state.get_data()
    await state.update_data(amount=str(amount))
    await state.set_state(AdminBalanceState.confirm)
    await message.answer(
        f"Confirm balance add?\n\n👤 {html.escape(data['target_name'])}\n💰 +{money(amount)} {INVOICE_CURRENCY}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Confirm", callback_data="admin:balance_confirm", style="danger")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:cancel", style="danger")],
        ]),
    )


@router.callback_query(F.data == "admin:balance_confirm")
async def ab_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    data = await state.get_data()
    uid = int(data["target_uid"]); amount = Decimal(data["amount"])
    balance = change_wallet(uid, amount, "ADMIN_ADD", f"ADMIN:{callback.from_user.id}", "Manual balance add")
    await state.clear()
    await edit(callback, f"✅ Balance added.\n\nAmount: <b>{money(amount)} {INVOICE_CURRENCY}</b>\nNew Balance: <b>{money(balance)} {INVOICE_CURRENCY}</b>", admin_kb())
    await safe_send(callback.bot, uid, f"💰 <b>Wallet Balance Added</b>\n\nAdmin added <b>{money(amount)} {INVOICE_CURRENCY}</b> to your wallet.\nNew Balance: <b>{money(balance)} {INVOICE_CURRENCY}</b>")


def _find_admin_user(raw: str):
    raw = str(raw or "").strip()
    with db() as con:
        if raw.startswith("@"):
            return con.execute(
                "SELECT * FROM users WHERE lower(username)=lower(?)", (raw[1:],)
            ).fetchone()
        if raw.isdigit():
            return con.execute(
                "SELECT * FROM users WHERE telegram_id=?", (int(raw),)
            ).fetchone()
    return None


def admin_balance_manage_kb(uid: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➖ Remove Balance", callback_data=f"admin:balance_remove:{uid}", style="danger"),
            InlineKeyboardButton(text="🧹 Set Balance 0", callback_data=f"admin:balance_zero:{uid}", style="danger"),
        ],
        [InlineKeyboardButton(text="🔄 Refresh Balance", callback_data=f"admin:balance_view:{uid}", style="primary")],
        [InlineKeyboardButton(text="🔎 Find Another User", callback_data="admin:manage_balance", style="success")],
        [InlineKeyboardButton(text="🧹 Reset ALL User Balances", callback_data="admin:balance_reset_all_confirm", style="danger")],
        [InlineKeyboardButton(text="◀ Back", callback_data="admin:home", style="primary")],
    ])


def admin_balance_text(user) -> str:
    username_value = user["username"] if user else None
    username = f"@{html.escape(str(username_value))}" if username_value else "Not set"
    return (
        "💳 <b>Manage User Balance</b>\n\n"
        f"👤 Name: <b>{html.escape(str(user['full_name']))}</b>\n"
        f"🆔 Telegram ID: <code>{user['telegram_id']}</code>\n"
        f"🔗 Username: <b>{username}</b>\n"
        f"💰 Current Balance: <b>{money(user['wallet'])} {INVOICE_CURRENCY}</b>\n\n"
        "Choose what you want to do:"
    )


@router.callback_query(F.data == "admin:manage_balance")
async def admin_manage_balance(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await state.set_state(AdminManageBalanceState.user)
    await edit(
        callback,
        "💳 <b>Manage Balance</b>\n\nSend the user's Telegram ID or @username.\n"
        "You will be able to view, remove or set the balance to 0.",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Cancel", callback_data="admin:home", style="danger")
        ]]),
    )


@router.message(AdminManageBalanceState.user)
async def admin_manage_balance_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    user = _find_admin_user(message.text or "")
    if not user:
        return await message.answer("❌ User not found. The user must start the bot first.")
    uid = int(user["telegram_id"])
    await state.clear()
    await message.answer(admin_balance_text(user), reply_markup=admin_balance_manage_kb(uid))


@router.callback_query(F.data.startswith("admin:balance_view:"))
async def admin_balance_view(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    uid = int(callback.data.rsplit(":", 1)[1])
    user = get_user(uid)
    if not user:
        return await callback.answer("User not found.", show_alert=True)
    await edit(callback, admin_balance_text(user), admin_balance_manage_kb(uid))


@router.callback_query(F.data.startswith("admin:balance_remove:"))
async def admin_balance_remove_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.rsplit(":", 1)[1])
    user = get_user(uid)
    if not user:
        return await callback.answer("User not found.", show_alert=True)
    current = Decimal(str(user["wallet"] or "0"))
    if current <= 0:
        return await callback.answer("This user's balance is already 0.", show_alert=True)
    await state.set_state(AdminManageBalanceState.amount)
    await state.update_data(manage_uid=uid, manage_name=str(user["full_name"]))
    await edit(
        callback,
        "➖ <b>Remove Balance</b>\n\n"
        f"👤 {html.escape(str(user['full_name']))}\n"
        f"💰 Current Balance: <b>{money(current)} {INVOICE_CURRENCY}</b>\n\n"
        "Send the amount you want to remove:",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀ Cancel", callback_data=f"admin:balance_view:{uid}", style="primary")
        ]]),
    )


@router.message(AdminManageBalanceState.amount)
async def admin_balance_remove_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    uid = int(data.get("manage_uid", 0))
    user = get_user(uid)
    if not user:
        await state.clear()
        return await message.answer("❌ User not found.", reply_markup=admin_kb())
    try:
        amount = Decimal((message.text or "").strip())
        current = Decimal(str(user["wallet"] or "0"))
        if amount <= 0 or amount > current:
            raise InvalidOperation
    except Exception:
        return await message.answer(
            f"❌ Send a valid amount from 0.001 to {money(user['wallet'])} {INVOICE_CURRENCY}."
        )
    await state.update_data(manage_amount=str(amount))
    await state.set_state(AdminManageBalanceState.confirm)
    await message.answer(
        "⚠️ <b>Confirm Balance Removal</b>\n\n"
        f"👤 {html.escape(str(user['full_name']))}\n"
        f"💰 Current: <b>{money(user['wallet'])} {INVOICE_CURRENCY}</b>\n"
        f"➖ Remove: <b>{money(amount)} {INVOICE_CURRENCY}</b>\n"
        f"➡️ After: <b>{money(Decimal(str(user['wallet'])) - amount)} {INVOICE_CURRENCY}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Remove Balance", callback_data="admin:balance_remove_confirm", style="danger")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"admin:balance_view:{uid}", style="primary")],
        ]),
    )


@router.callback_query(F.data == "admin:balance_remove_confirm")
async def admin_balance_remove_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    uid = int(data.get("manage_uid", 0))
    amount = Decimal(str(data.get("manage_amount", "0")))
    user = get_user(uid)
    if not user or amount <= 0:
        await state.clear()
        return await edit(callback, "❌ Balance operation expired.", admin_kb())
    current = Decimal(str(user["wallet"] or "0"))
    if amount > current:
        await state.clear()
        return await edit(callback, "❌ Balance changed. Open Manage Balance and try again.", admin_kb())
    try:
        new_balance = change_wallet(
            uid, -amount, "ADMIN_REMOVE", f"ADMIN:{callback.from_user.id}", "Manual balance removal"
        )
    except ValueError:
        await state.clear()
        return await edit(
            callback,
            "❌ Balance changed before confirmation. Open Manage Balance and try again.",
            admin_kb(),
        )
    await state.clear()
    updated = get_user(uid)
    await edit(callback, admin_balance_text(updated), admin_balance_manage_kb(uid))
    await safe_send(
        callback.bot, uid,
        f"💳 <b>Wallet Balance Updated</b>\n\n"
        f"Admin removed <b>{money(amount)} {INVOICE_CURRENCY}</b>.\n"
        f"New Balance: <b>{money(new_balance)} {INVOICE_CURRENCY}</b>"
    )


@router.callback_query(F.data.startswith("admin:balance_zero:"))
async def admin_balance_zero_prompt(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.rsplit(":", 1)[1])
    user = get_user(uid)
    if not user:
        return await callback.answer("User not found.", show_alert=True)
    current = Decimal(str(user["wallet"] or "0"))
    if current <= 0:
        return await callback.answer("This user's balance is already 0.", show_alert=True)
    await edit(
        callback,
        "⚠️ <b>Set User Balance to 0?</b>\n\n"
        f"👤 {html.escape(str(user['full_name']))}\n"
        f"💰 Current Balance: <b>{money(current)} {INVOICE_CURRENCY}</b>\n\n"
        "Only this user's wallet balance will be reset. Orders and account data stay unchanged.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, Set to 0", callback_data=f"admin:balance_zero_do:{uid}", style="danger")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"admin:balance_view:{uid}", style="primary")],
        ]),
    )


@router.callback_query(F.data.startswith("admin:balance_zero_do:"))
async def admin_balance_zero_do(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.rsplit(":", 1)[1])
    user = get_user(uid)
    if not user:
        return await callback.answer("User not found.", show_alert=True)
    current = Decimal(str(user["wallet"] or "0"))
    if current > 0:
        change_wallet(uid, -current, "ADMIN_RESET", f"ADMIN:{callback.from_user.id}", "Balance reset to zero")
    await state.clear()
    updated = get_user(uid)
    await edit(callback, admin_balance_text(updated), admin_balance_manage_kb(uid))
    if current > 0:
        await safe_send(
            callback.bot, uid,
            f"💳 <b>Wallet Balance Reset</b>\n\nYour wallet balance was reset to <b>0 {INVOICE_CURRENCY}</b> by admin."
        )


def reset_all_user_balances(admin_id: int) -> tuple[int, Decimal]:
    """Reset every non-zero wallet in one DB transaction and keep an audit trail."""
    count = 0
    total_removed = Decimal("0")
    with db() as con:
        rows = con.execute("SELECT telegram_id, wallet FROM users").fetchall()
        for row in rows:
            try:
                before = Decimal(str(row["wallet"] or "0"))
            except Exception:
                continue
            if before <= 0:
                continue
            uid = int(row["telegram_id"])
            con.execute("UPDATE users SET wallet=? WHERE telegram_id=?", ("0", uid))
            con.execute(
                """INSERT INTO wallet_transactions(
                    tx_id, telegram_id, type, amount, balance_before, balance_after, reference_id, note
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (f"TX-{uuid4().hex[:14].upper()}", uid, "ADMIN_RESET_ALL", str(-before), str(before), "0", f"ADMIN:{admin_id}", "Global balance reset"),
            )
            count += 1
            total_removed += before
        con.commit()
    return count, total_removed


@router.callback_query(F.data == "admin:balance_reset_all_confirm")
async def admin_balance_reset_all_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await edit(
        callback,
        "🚨 <b>RESET ALL USER BALANCES?</b>\n\n"
        "This will set every user's current wallet balance to <b>0</b>.\n"
        "Accounts, orders, products and transaction history will stay saved.\n\n"
        "This action cannot automatically restore the previous balances.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚨 YES — RESET ALL TO 0", callback_data="admin:balance_reset_all_do", style="danger")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:home", style="primary")],
        ]),
    )


@router.callback_query(F.data == "admin:balance_reset_all_do")
async def admin_balance_reset_all_do(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    count, removed = reset_all_user_balances(callback.from_user.id)
    await state.clear()
    await edit(
        callback,
        "✅ <b>All User Balances Reset</b>\n\n"
        f"👥 Users changed: <b>{count}</b>\n"
        f"💰 Total removed: <b>{money(removed)} {INVOICE_CURRENCY}</b>\n"
        f"👛 Every affected wallet is now <b>0 {INVOICE_CURRENCY}</b>.",
        admin_kb(),
    )


@router.callback_query(F.data == "admin:cancel")
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.clear(); await edit(callback, "❎ Cancelled.", admin_kb())


def admin_reorder_kb():
    products = list_products()
    rows = []
    for idx, p in enumerate(products):
        controls = []
        if idx > 0:
            controls.append(InlineKeyboardButton(text="⬆️", callback_data=f"admin:moveup:{p['id']}", style="primary"))
        if idx < len(products) - 1:
            controls.append(InlineKeyboardButton(text="⬇️", callback_data=f"admin:movedown:{p['id']}", style="primary"))
        if controls:
            rows.append([
                InlineKeyboardButton(
                    text=f"{idx+1}. {str(p['name'])[:35]}",
                    callback_data="noop",
                    style="primary",
                ),
                *controls,
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text=f"{idx+1}. {str(p['name'])[:35]}",
                    callback_data="noop",
                    style="primary",
                )
            ])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data="admin:home", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "admin:plans")
async def admin_plans(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    groups={}
    for p in list_products():
        category=str(p["category"] or "").strip()
        if category:
            k=category.casefold(); groups.setdefault(k,{"name":category,"pid":int(p["id"]),"count":0}); groups[k]["count"]+=1
    rows=[]
    for g in groups.values():
        rows.append([InlineKeyboardButton(text=f"📂 {g['name']} • {g['count']} plans", callback_data=f"admin:planadd:{g['pid']}", style="primary")])
        rows.append([InlineKeyboardButton(text=f"📥 Add Stock • {g['name']}", callback_data=f"admin:planstock:{g['pid']}", style="success")])
    rows.append([InlineKeyboardButton(text="➕ Create New Plan Group", callback_data="admin:add_product", style="success")])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data="admin:home", style="danger")])
    await edit(callback,"📂 <b>Manage Plans</b>\n\nSelect a plan group to add another product inside it.",InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("admin:planadd:"))
async def admin_plan_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    pid=int(callback.data.rsplit(":",1)[1]); p=get_product(pid)
    if not p or not str(p["category"] or "").strip(): return await callback.answer("Plan group not found.",show_alert=True)
    category=str(p["category"]).strip()
    await state.clear(); await state.update_data(group_category=category,category=category); await state.set_state(AdminProductState.name)
    await edit(callback,f"➕ <b>Add Product to Plan</b>\n\n📂 Plan: <b>{html.escape(category)}</b>\n\n1/8 • Send Product Name:",InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀ Cancel",callback_data="admin:plans",style="danger")]]))


@router.callback_query(F.data == "admin:reorder")
async def admin_reorder(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    products = list_products()
    if not products:
        return await edit(callback, "↕️ <b>Reorder Products</b>\n\nNo products found.", admin_kb())
    await edit(
        callback,
        "↕️ <b>Reorder Products</b>\n\n"
        "Use ⬆️ / ⬇️ to move a product.\n"
        "The customer shop follows this order.",
        admin_reorder_kb(),
    )


@router.callback_query(F.data.startswith("admin:moveup:"))
async def admin_moveup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    pid = int(callback.data.rsplit(":", 1)[1])
    move_product(pid, "up")
    await edit(callback, "↕️ <b>Reorder Products</b>\n\nUse ⬆️ / ⬇️ to move a product.", admin_reorder_kb())


@router.callback_query(F.data.startswith("admin:movedown:"))
async def admin_movedown(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    pid = int(callback.data.rsplit(":", 1)[1])
    move_product(pid, "down")
    await edit(callback, "↕️ <b>Reorder Products</b>\n\nUse ⬆️ / ⬇️ to move a product.", admin_reorder_kb())


@router.callback_query(F.data == "admin:products")
async def admin_products(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    lines = ["📦 <b>Products</b>"]
    for p in list_products():
        note_preview = (p["note"] or "").replace("\n", " ").strip()
        if len(note_preview) > 70:
            note_preview = note_preview[:67] + "..."
        lines.append(
            f"\nID: <code>{p['id']}</code>\n"
            f"🔢 Position: {p['display_order']}\n"
            f"📦 {html.escape(p['name'])}\n"
            f"💵 Price: {money(p['price'])} {INVOICE_CURRENCY}\n"
            f"📊 Stock: {stock_count(p['id'])}\n"
            f"🛡 Warranty: {html.escape(p['warranty'])}\n"
            f"📝 Details: {html.escape(note_preview or 'Not set')}"
        )
    await edit(callback, "\n".join(lines), admin_kb())


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


# =========================
# WEBHOOK + PDF HTTP SERVER
# =========================

BOT_INSTANCE: Bot | None = None

async def ipn_handler(request: web.Request):
    secret = request.headers.get("X-Webhook-Secret", "")
    if not PAYMENT_WEBHOOK_SECRET and not ALLOW_INSECURE_WEBHOOKS:
        logging.error("Webhook rejected: PAYMENT_WEBHOOK_SECRET is not configured")
        return web.Response(status=503, text="webhook-secret-not-configured")
    if PAYMENT_WEBHOOK_SECRET and secret != PAYMENT_WEBHOOK_SECRET:
        return web.Response(status=401, text="invalid-secret")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    event = str(payload.get("event") or "").upper()
    if event == "BALANCE_CREDIT":
        try:
            uid = int(payload.get("telegram_id"))
            amount = Decimal(str(payload.get("amount")))
            if amount <= 0:
                return web.Response(status=400, text="bad-amount")
            note = str(payload.get("note") or "manual")
            external_ref = str(payload.get("event_id") or payload.get("reference_id") or payload.get("txid") or "").strip()
            if not external_ref:
                canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
                external_ref = "PAYLOAD-" + hashlib.sha256(canonical).hexdigest()[:24]
            marker_invoice = f"BALANCE_CREDIT:{uid}"
            if not mark_webhook_processed(marker_invoice, external_ref):
                return web.Response(status=200, text="duplicate")
            try:
                balance = change_wallet(uid, amount, "DEPOSIT", f"MANUAL-{external_ref[:40]}", note)
            except Exception:
                # Allow a failed credit attempt to be retried safely.
                with db() as con:
                    con.execute("DELETE FROM processed_webhooks WHERE invoice_id=? AND tx_hash=?", (marker_invoice, external_ref))
                    con.commit()
                raise
            if BOT_INSTANCE:
                await safe_send(
                    BOT_INSTANCE,
                    uid,
                    "✅ <b>Balance Credited</b>\n\n"
                    f"💰 Added: <b>{money(amount)} {payload.get('currency') or INVOICE_CURRENCY}</b>\n"
                    f"👛 New Balance: <b>{money(balance)} {INVOICE_CURRENCY}</b>",
                )
            return web.Response(status=200, text="credited")
        except Exception:
            logging.exception("Manual credit webhook failed")
            return web.Response(status=503, text="credit-failed")
    if event and event != "PAYMENT_PAID":
        return web.Response(status=200, text="ignored")
    invoice_id = str(payload.get("invoice_id") or "")
    if not invoice_id:
        return web.Response(status=200, text="ignored")
    saved = get_saved_invoice(invoice_id)
    if not saved:
        return web.Response(status=200, text="unknown-invoice")
    if str(payload.get("status") or "PAID").upper() != "PAID":
        return web.Response(status=200, text="not-paid")
    await apply_paid_invoice(
        BOT_INSTANCE,
        invoice_id,
        str(payload.get("txid") or payload.get("order_id") or payload.get("tx_id") or invoice_id),
        "BINANCE_PAY",
    )
    return web.Response(status=200, text="ok")


async def pdf_handler(request: web.Request):
    token = request.match_info["token"]
    with db() as con:
        order = con.execute("SELECT * FROM orders WHERE invoice_pdf_token=? AND status='COMPLETED'", (token,)).fetchone()
    if not order:
        raise web.HTTPNotFound(text="Invoice not found")
    user = get_user(order["telegram_id"])
    if not user:
        raise web.HTTPNotFound(text="User not found")
    pdf = invoice_pdf_bytes(order, user)
    headers = {"Content-Disposition": f'attachment; filename="invoice-{order["order_id"]}.pdf"'}
    return web.Response(body=pdf, content_type="application/pdf", headers=headers)


async def health_handler(request: web.Request):
    return web.json_response({"ok": True, "bot": BOT_NAME})


async def email_verify_handler(request: web.Request):
    """Legacy link-based verify (old emails). New flow uses 6-digit codes in the bot."""
    token = request.match_info.get("token", "")
    ok = verify_email_token(token) if token else False
    if ok:
        return web.Response(
            text=f"<html><body style='font-family:Arial;padding:40px;background:#0b1726;color:#e5eef8'>"
                 f"<h2>✅ Email verified</h2><p>You can return to {html.escape(BOT_NAME)} on Telegram.</p>"
                 f"</body></html>",
            content_type="text/html",
        )
    return web.Response(
        text="<html><body style='font-family:Arial;padding:40px'><h2>❌ Invalid or expired link</h2></body></html>",
        content_type="text/html",
        status=400,
    )


async def start_web_server():
    app = web.Application()
    app.router.add_post("/webhook", ipn_handler)
    app.router.add_post("/api/v1/payments/webhook", ipn_handler)
    app.router.add_post("/ipn", ipn_handler)
    app.router.add_get("/verify-email/{token}", email_verify_handler)
    app.router.add_get("/invoice/{token}", pdf_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
    await site.start()
    logging.info("HTTP server listening on %s:%s", WEB_HOST, WEB_PORT)
    return runner


# =========================
# MAIN
# =========================


async def setup_shop_commands(bot: Bot):
    """Force-register Telegram commands on every startup."""
    customer_commands = [
        BotCommand(command="start", description="Open Premium Hub Store"),
        BotCommand(command="shop", description="🛒 Shop"),
        BotCommand(command="topup", description="💰 Topup Wallet"),
        BotCommand(command="balance", description="💳 Balance"),
        BotCommand(command="settings", description="⚙️ Settings"),
        BotCommand(command="refer", description="🎁 Refer & Earn"),
        BotCommand(command="channel", description="📢 Official Channel"),
    ]
    admin_commands = [
        *customer_commands,
        BotCommand(command="admin", description="🛠 Open Admin Panel"),
    ]

    # Clear common old scopes first so stale commands do not remain.
    scopes_to_clear = [BotCommandScopeDefault()]
    if ADMIN_ID:
        scopes_to_clear.append(BotCommandScopeChat(chat_id=int(ADMIN_ID)))

    for scope in scopes_to_clear:
        try:
            await bot.delete_my_commands(scope=scope)
        except Exception as e:
            logging.warning("Could not clear Telegram commands for %s: %s", scope, e)

    # Register customer/default menu.
    await bot.set_my_commands(
        customer_commands,
        scope=BotCommandScopeDefault(),
    )

    # Register admin-only menu.
    if ADMIN_ID:
        await bot.set_my_commands(
            admin_commands,
            scope=BotCommandScopeChat(chat_id=int(ADMIN_ID)),
        )

    try:
        global BOT_USERNAME
        me = await bot.get_me()
        BOT_USERNAME = me.username or ""
        logging.info(
            "Telegram commands registered successfully for @%s (admin_id=%s)",
            me.username,
            ADMIN_ID,
        )
    except Exception as e:
        logging.warning("Command registration verification failed: %s", e)



async def main():
    global BOT_INSTANCE
    init_db()
    migrate_v23_schema()
    refresh_runtime_settings()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await setup_shop_commands(bot)
    BOT_INSTANCE = bot
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    runner = await start_web_server()
    logging.info("%s is running...", BOT_NAME)
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
