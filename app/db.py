from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from app.config import get_settings

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_pw(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${digest}"


def check_pw(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    test = hashlib.sha256((salt + password).encode()).hexdigest()
    return hmac.compare_digest(test, digest)


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            s = get_settings()
            if not s.database_url:
                raise RuntimeError("DATABASE_URL is required for the PostgreSQL build")
            _pool = ThreadedConnectionPool(
                minconn=max(1, s.db_pool_min),
                maxconn=max(s.db_pool_min, s.db_pool_max),
                dsn=s.database_url,
                connect_timeout=10,
                application_name="payhub",
            )
    return _pool


@contextmanager
def connect():
    pool = _get_pool()
    con = pool.getconn()
    try:
        if con.closed:
            pool.putconn(con, close=True)
            con = pool.getconn()
        with con.cursor(cursor_factory=RealDictCursor) as cur:
            yield con, cur
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        pool.putconn(con)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def init_db() -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        approve_token TEXT UNIQUE,
        binance_api_key TEXT NOT NULL DEFAULT '',
        binance_api_secret TEXT NOT NULL DEFAULT '',
        binance_uid TEXT NOT NULL DEFAULT '',
        binance_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bots (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        name TEXT NOT NULL,
        api_key TEXT UNIQUE NOT NULL,
        webhook_url TEXT NOT NULL DEFAULT '',
        webhook_secret TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS invoices (
        invoice_id TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        bot_id BIGINT,
        telegram_id TEXT,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        txid TEXT,
        created_at TEXT NOT NULL,
        paid_at TEXT
    );
    CREATE TABLE IF NOT EXISTS used_tx (
        txid TEXT PRIMARY KEY,
        invoice_id TEXT NOT NULL,
        used_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS webhook_logs (
        id BIGSERIAL PRIMARY KEY,
        bot_id BIGINT,
        event TEXT,
        payload TEXT,
        status_code INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        user_id BIGINT PRIMARY KEY,
        deposit_text TEXT NOT NULL DEFAULT '',
        success_text TEXT NOT NULL DEFAULT '',
        fail_not_found TEXT NOT NULL DEFAULT '',
        fail_mismatch TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_bots_user_id ON bots(user_id);
    CREATE INDEX IF NOT EXISTS idx_invoices_user_created ON invoices(user_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_invoices_bot_id ON invoices(bot_id);
    """
    with connect() as (_, cur):
        cur.execute(ddl)


DEFAULT_DEPOSIT = """🟡 Binance Pay Deposit

Pay ID: {pay_id}
Binance Name: {binance_name}

Amount: {amount} {currency}

✅ Send any exact amount to the Pay ID above
📝 Paste your Order ID below

⏰ Only payments started after opening this screen and completed within {minutes} minutes will be credited.

Please send your Order ID below:"""

DEFAULT_SUCCESS = """✅ Success — Done

Credited: {amount} {currency}
New balance: {balance} {currency}"""

DEFAULT_FAIL_NOT_FOUND = """❌ Disapproved (#{code}).

Order ID: {order_id}

We couldn't find that Order ID in our Binance Pay history. Make sure you copied the full ID from the receipt and that the payment completed within the 30-minute window.

This order did not match our records. If you believe this is a mistake, contact support."""

DEFAULT_FAIL_MISMATCH = """❌ Disapproved (#{code}).

Order ID: {order_id}

This order did not match our records. The paid amount is not the same as this invoice."""


def get_messages(user_id: int) -> dict:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM messages WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
    data = dict(row) if row else {}
    return {
        "deposit_text": data.get("deposit_text") or DEFAULT_DEPOSIT,
        "success_text": data.get("success_text") or DEFAULT_SUCCESS,
        "fail_not_found": data.get("fail_not_found") or DEFAULT_FAIL_NOT_FOUND,
        "fail_mismatch": data.get("fail_mismatch") or DEFAULT_FAIL_MISMATCH,
    }


def save_messages(user_id: int, deposit_text: str, success_text: str, fail_not_found: str, fail_mismatch: str) -> None:
    with connect() as (_, cur):
        cur.execute(
            """INSERT INTO messages(user_id,deposit_text,success_text,fail_not_found,fail_mismatch)
               VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT(user_id) DO UPDATE SET
                 deposit_text=EXCLUDED.deposit_text,
                 success_text=EXCLUDED.success_text,
                 fail_not_found=EXCLUDED.fail_not_found,
                 fail_mismatch=EXCLUDED.fail_mismatch""",
            (user_id, deposit_text, success_text, fail_not_found, fail_mismatch),
        )


def create_user(email: str, password: str) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    email_clean = email.lower().strip()
    with connect() as (_, cur):
        cur.execute(
            "INSERT INTO users(email,password_hash,status,approve_token,created_at) VALUES(%s,%s,%s,%s,%s) RETURNING id",
            (email_clean, hash_pw(password), "pending", token, utcnow()),
        )
        row = cur.fetchone()
        return {"id": row["id"], "email": email_clean, "approve_token": token}


def get_user_by_email(email: str) -> Optional[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM users WHERE email=%s", (email.lower().strip(),))
        return cur.fetchone()


def get_user(user_id: int) -> Optional[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
        return cur.fetchone()


def get_user_by_token(token: str) -> Optional[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM users WHERE approve_token=%s", (token,))
        return cur.fetchone()


def set_status(user_id: int, status: str) -> None:
    with connect() as (_, cur):
        cur.execute("UPDATE users SET status=%s WHERE id=%s", (status, user_id))


def list_users() -> list[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM users ORDER BY id DESC")
        return cur.fetchall()


def pending_users() -> list[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM users WHERE status='pending' ORDER BY id DESC")
        return cur.fetchall()


def save_binance(user_id: int, key: str, secret: str, uid: str, name: str) -> None:
    with connect() as (_, cur):
        cur.execute(
            "UPDATE users SET binance_api_key=%s, binance_api_secret=%s, binance_uid=%s, binance_name=%s WHERE id=%s",
            (key.strip(), secret.strip(), uid.strip(), name.strip(), user_id),
        )


def create_bot(user_id: int, name: str, webhook_url: str = "") -> dict[str, Any]:
    api_key = "pk_live_" + secrets.token_hex(16)
    secret = "whsec_" + secrets.token_hex(16)
    with connect() as (_, cur):
        cur.execute(
            """INSERT INTO bots(user_id,name,api_key,webhook_url,webhook_secret,created_at)
               VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
            (user_id, name, api_key, webhook_url, secret, utcnow()),
        )
        row = cur.fetchone()
        return {"id": row["id"], "api_key": api_key, "webhook_secret": secret, "webhook_url": webhook_url, "name": name}


def list_bots(user_id: int) -> list[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM bots WHERE user_id=%s ORDER BY id DESC", (user_id,))
        return cur.fetchall()


def get_bot(bot_id: int) -> Optional[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM bots WHERE id=%s", (bot_id,))
        return cur.fetchone()


def get_bot_by_key(api_key: str) -> Optional[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM bots WHERE api_key=%s AND active=1", (api_key,))
        return cur.fetchone()


def update_bot_webhook(bot_id: int, user_id: int, webhook_url: str) -> None:
    with connect() as (_, cur):
        cur.execute("UPDATE bots SET webhook_url=%s WHERE id=%s AND user_id=%s", (webhook_url.strip(), bot_id, user_id))


def create_invoice(invoice_id: str, user_id: int, bot_id: int, telegram_id: str, amount: str, currency: str) -> None:
    with connect() as (_, cur):
        cur.execute(
            """INSERT INTO invoices(invoice_id,user_id,bot_id,telegram_id,amount,currency,status,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,'PENDING',%s)""",
            (invoice_id, user_id, bot_id, telegram_id, amount, currency, utcnow()),
        )


def get_invoice(invoice_id: str) -> Optional[dict]:
    with connect() as (_, cur):
        cur.execute("SELECT * FROM invoices WHERE invoice_id=%s", (invoice_id,))
        return cur.fetchone()


def mark_paid(invoice_id: str, txid: str) -> None:
    with connect() as (_, cur):
        cur.execute(
            "UPDATE invoices SET status='PAID', txid=%s, paid_at=%s WHERE invoice_id=%s AND status!='PAID'",
            (txid, utcnow(), invoice_id),
        )
        cur.execute(
            "INSERT INTO used_tx(txid,invoice_id,used_at) VALUES(%s,%s,%s) ON CONFLICT(txid) DO NOTHING",
            (txid, invoice_id, utcnow()),
        )


def tx_used(txid: str) -> bool:
    with connect() as (_, cur):
        cur.execute("SELECT 1 FROM used_tx WHERE txid=%s", (txid,))
        return cur.fetchone() is not None


def claim_payment(invoice_id: str, txid: str) -> tuple[bool, str]:
    """Atomically reserve a transaction ID and mark an invoice paid."""
    txid = (txid or "").strip()
    if not txid:
        return False, "missing_txid"

    pool = _get_pool()
    con = pool.getconn()
    try:
        con.autocommit = False
        with con.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM invoices WHERE invoice_id=%s FOR UPDATE", (invoice_id,))
            inv = cur.fetchone()
            if not inv:
                con.rollback()
                return False, "invoice_not_found"
            if inv["status"] == "PAID":
                same = str(inv.get("txid") or "").strip() == txid
                con.commit()
                return (same, "already_paid_same" if same else "already_paid")

            cur.execute("SELECT invoice_id FROM used_tx WHERE txid=%s FOR UPDATE", (txid,))
            used = cur.fetchone()
            if used:
                con.rollback()
                return False, "tx_already_used"

            try:
                cur.execute("INSERT INTO used_tx(txid,invoice_id,used_at) VALUES(%s,%s,%s)", (txid, invoice_id, utcnow()))
            except psycopg2.errors.UniqueViolation:
                con.rollback()
                return False, "tx_already_used"

            cur.execute(
                "UPDATE invoices SET status='PAID', txid=%s, paid_at=%s WHERE invoice_id=%s AND status='PENDING'",
                (txid, utcnow(), invoice_id),
            )
            if cur.rowcount != 1:
                con.rollback()
                return False, "invoice_not_pending"
            con.commit()
            return True, "paid"
    except Exception:
        con.rollback()
        raise
    finally:
        con.autocommit = False
        pool.putconn(con)


def list_invoices(user_id: int, limit: int = 50) -> list[dict]:
    with connect() as (_, cur):
        cur.execute(
            "SELECT * FROM invoices WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )
        return cur.fetchall()


def log_webhook(bot_id: int, event: str, payload: str, status_code: int) -> None:
    with connect() as (_, cur):
        cur.execute(
            "INSERT INTO webhook_logs(bot_id,event,payload,status_code,created_at) VALUES(%s,%s,%s,%s,%s)",
            (bot_id, event, payload, status_code, utcnow()),
        )
