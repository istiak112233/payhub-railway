from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.binance import BinanceError, live_balances, verify_any
from app.config import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("payhub")

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))
app = FastAPI(title="PayHub")
s0 = get_settings()
app.add_middleware(SessionMiddleware, secret_key=s0.session_secret)


@app.on_event("startup")
def startup() -> None:
    db.init_db()


def is_gmail(email: str) -> bool:
    e = (email or "").lower().strip()
    return e.endswith("@gmail.com") and e.count("@") == 1


async def tg_notify(text: str) -> None:
    s = get_settings()
    if not s.admin_bot_token or not s.admin_telegram_id:
        return
    url = f"https://api.telegram.org/bot{s.admin_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(url, json={"chat_id": s.admin_telegram_id, "text": text})
    except Exception:
        log.exception("tg notify failed")


async def fire_webhook(bot_row, payload: dict) -> int:
    url = (bot_row["webhook_url"] or "").strip()
    if not url:
        db.log_webhook(bot_row["id"], payload.get("event", ""), json.dumps(payload), 0)
        return 0
    headers = {"Content-Type": "application/json", "X-Webhook-Secret": bot_row["webhook_secret"]}
    last_status = 0
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for attempt in range(3):
                try:
                    r = await client.post(url, json=payload, headers=headers)
                    last_status = r.status_code
                    if 200 <= r.status_code < 300:
                        db.log_webhook(bot_row["id"], payload.get("event", ""), json.dumps(payload), r.status_code)
                        return r.status_code
                except Exception:
                    last_status = 0
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
    except Exception:
        log.exception("webhook failed")
    db.log_webhook(bot_row["id"], payload.get("event", ""), json.dumps(payload), last_status)
    return last_status


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "title": "Create account",
            "subtitle": "Only 1 Gmail = 1 account",
            "action": "/register",
            "button": "Create account",
            "other_href": "/login",
            "other_text": "Already have account? Login",
            "error": "",
        },
    )


@app.post("/register")
async def register_post(request: Request, email: str = Form(...), password: str = Form(...)):
    ctx = {
        "request": request,
        "title": "Create account",
        "subtitle": "Only 1 Gmail = 1 account",
        "action": "/register",
        "button": "Create account",
        "other_href": "/login",
        "other_text": "Already have account? Login",
    }
    if not is_gmail(email):
        ctx["error"] = "Only Gmail address allowed."
        return templates.TemplateResponse("login.html", ctx)
    if len(password) < 6:
        ctx["error"] = "Password minimum 6 characters."
        return templates.TemplateResponse("login.html", ctx)
    if db.get_user_by_email(email):
        ctx["error"] = "This Gmail already has an account."
        return templates.TemplateResponse("login.html", ctx)
    user = db.create_user(email, password)
    s = get_settings()
    approve = f"{s.public_base_url.rstrip('/')}/admin/approve/{user['approve_token']}"
    reject = f"{s.public_base_url.rstrip('/')}/admin/reject/{user['approve_token']}"
    await tg_notify(
        f"New PayHub account\nEmail: {user['email']}\nApprove: {approve}\nReject: {reject}"
    )
    request.session["user_id"] = user["id"]
    return RedirectResponse("/pending", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "title": "Login",
            "subtitle": "Gmail login",
            "action": "/login",
            "button": "Login",
            "other_href": "/register",
            "other_text": "Create account",
            "error": "",
        },
    )


@app.post("/login")
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    row = db.get_user_by_email(email)
    if not row or not db.check_pw(password, row["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "title": "Login",
                "subtitle": "Gmail login",
                "action": "/login",
                "button": "Login",
                "other_href": "/register",
                "other_text": "Create account",
                "error": "Wrong email or password",
            },
        )
    request.session["user_id"] = row["id"]
    if row["status"] != "approved":
        return RedirectResponse("/pending", status_code=302)
    return RedirectResponse("/", status_code=302)


@app.get("/pending", response_class=HTMLResponse)
def pending(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login", status_code=302)
    row = db.get_user(uid)
    if row and row["status"] == "approved":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        "pending.html", {"request": request, "status": row["status"] if row else "unknown"}
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "title": "Admin login",
            "subtitle": "Site owner",
            "action": "/admin/login",
            "button": "Admin login",
            "other_href": "/login",
            "other_text": "User login",
            "error": "",
        },
    )


@app.post("/admin/login")
def admin_login(request: Request, email: str = Form(""), password: str = Form(...)):
    s = get_settings()
    if password != s.admin_password:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "title": "Admin login",
                "subtitle": "Site owner",
                "action": "/admin/login",
                "button": "Admin login",
                "other_href": "/login",
                "other_text": "User login",
                "error": "Wrong admin password",
            },
        )
    request.session["admin"] = True
    return RedirectResponse("/admin", status_code=302)


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request, "users": db.list_users()})


@app.get("/admin/approve/{token}")
async def admin_approve(token: str, request: Request):
    if not request.session.get("admin"):
        # allow approve from telegram link if token valid + optional open
        pass
    row = db.get_user_by_token(token)
    if not row:
        raise HTTPException(404, "token invalid")
    db.set_status(row["id"], "approved")
    await tg_notify(f"Approved: {row['email']}")
    if request.session.get("admin"):
        return RedirectResponse("/admin", status_code=302)
    return HTMLResponse("<h3>Account approved</h3>")


@app.get("/admin/reject/{token}")
async def admin_reject(token: str, request: Request):
    row = db.get_user_by_token(token)
    if not row:
        raise HTTPException(404, "token invalid")
    db.set_status(row["id"], "rejected")
    await tg_notify(f"Rejected: {row['email']}")
    if request.session.get("admin"):
        return RedirectResponse("/admin", status_code=302)
    return HTMLResponse("<h3>Account rejected</h3>")


def current_user(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.get_user(uid)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    row = current_user(request)
    if not row:
        return RedirectResponse("/login", status_code=302)
    if row["status"] != "approved":
        return RedirectResponse("/pending", status_code=302)
    items = []
    usdt = 0.0
    bal_error = ""
    if row["binance_api_key"] and row["binance_api_secret"]:
        try:
            data = live_balances(row["binance_api_key"], row["binance_api_secret"])
            items = data["items"]
            usdt = data["usdt"]
        except BinanceError as exc:
            bal_error = str(exc)
    else:
        bal_error = "Binance key set koro live balance dekhte."
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "email": row["email"],
            "uid": row["binance_uid"],
            "bname": row["binance_name"],
            "api_key": row["binance_api_key"],
            "items": items,
            "usdt": f"{usdt:.4f}",
            "bal_error": bal_error,
            "bots": db.list_bots(row["id"]),
            "invoices": db.list_invoices(row["id"]),
            "msgs": db.get_messages(row["id"]),
        },
    )


@app.post("/settings")
def save_settings(
    request: Request,
    binance_api_key: str = Form(""),
    binance_api_secret: str = Form(""),
    binance_uid: str = Form(""),
    binance_name: str = Form(""),
):
    row = current_user(request)
    if not row or row["status"] != "approved":
        return RedirectResponse("/login", status_code=302)
    secret = binance_api_secret.strip() or row["binance_api_secret"]
    db.save_binance(row["id"], binance_api_key, secret, binance_uid, binance_name)
    return RedirectResponse("/", status_code=302)


@app.post("/messages")
def save_messages(
    request: Request,
    deposit_text: str = Form(""),
    success_text: str = Form(""),
    fail_not_found: str = Form(""),
    fail_mismatch: str = Form(""),
):
    row = current_user(request)
    if not row or row["status"] != "approved":
        return RedirectResponse("/login", status_code=302)
    db.save_messages(row["id"], deposit_text, success_text, fail_not_found, fail_mismatch)
    return RedirectResponse("/", status_code=302)


@app.post("/bots")
def add_bot(request: Request, name: str = Form(...), webhook_url: str = Form("")):
    row = current_user(request)
    if not row or row["status"] != "approved":
        return RedirectResponse("/login", status_code=302)
    db.create_bot(row["id"], name.strip(), webhook_url.strip())
    return RedirectResponse("/", status_code=302)


@app.post("/bots/{bot_id}/webhook")
def update_bot_webhook(bot_id: int, request: Request, webhook_url: str = Form("")):
    row = current_user(request)
    if not row or row["status"] != "approved":
        return RedirectResponse("/login", status_code=302)
    if webhook_url and not (webhook_url.startswith("https://") or webhook_url.startswith("http://")):
        raise HTTPException(400, "Webhook URL must start with http:// or https://")
    db.update_bot_webhook(bot_id, row["id"], webhook_url)
    return RedirectResponse("/", status_code=302)


@app.get("/docs-page", response_class=HTMLResponse)
def docs_page(request: Request):
    return templates.TemplateResponse(
        "docs.html",
        {"request": request, "base": get_settings().public_base_url.rstrip("/")},
    )


def _auth_bot(x_api_key: str | None):
    if not x_api_key:
        raise HTTPException(401, "X-API-Key missing")
    bot = db.get_bot_by_key(x_api_key)
    if not bot:
        raise HTTPException(401, "Invalid API key")
    owner = db.get_user(bot["user_id"])
    if not owner or owner["status"] != "approved":
        raise HTTPException(403, "Merchant account not approved")
    return bot, owner


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/v1/invoice")
def api_invoice(body: dict, x_api_key: str | None = Header(default=None)):
    bot, owner = _auth_bot(x_api_key)
    amount = str(body.get("amount") or "")
    telegram_id = str(body.get("telegram_id") or "")
    currency = str(body.get("currency") or get_settings().default_coin).upper()
    if not amount or not telegram_id:
        raise HTTPException(400, "telegram_id and amount required")
    invoice_id = "INV" + secrets.token_hex(5).upper()
    db.create_invoice(invoice_id, owner["id"], bot["id"], telegram_id, amount, currency)
    return {
        "ok": True,
        "invoice_id": invoice_id,
        "amount": amount,
        "currency": currency,
        "uid": owner["binance_uid"],
        "binance_name": owner["binance_name"],
        "pay_id": owner["binance_uid"],
        "status": "PENDING",
        "screen": {
            "title": "Binance Pay Deposit",
            "pay_id": owner["binance_uid"],
            "binance_name": owner["binance_name"],
            "minutes": get_settings().invoice_expire_minutes,
        },
        "messages": db.get_messages(owner["id"]),
    }


@app.post("/api/v1/verify")
async def api_verify(body: dict, x_api_key: str | None = Header(default=None)):
    bot, owner = _auth_bot(x_api_key)
    invoice_id = str(body.get("invoice_id") or "").strip()
    # New bot sends original Binance Order ID as order_id. Keep txid/tx_id aliases
    # for older bot builds, but use exactly one submitted identifier.
    txid = str(body.get("order_id") or body.get("txid") or body.get("tx_id") or "").strip()
    if not invoice_id or not txid:
        raise HTTPException(400, "invoice_id and order_id required")
    inv = db.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "invoice not found")
    if inv["user_id"] != owner["id"]:
        raise HTTPException(403, "invoice not yours")
    if inv["status"] == "PAID":
        return {"ok": True, "status": "PAID", "txid": inv["txid"], "amount": inv["amount"]}
    msgs = db.get_messages(owner["id"])
    code = str(abs(hash(txid)) % 9000 + 1000)
    if db.tx_used(txid):
        text = msgs["fail_not_found"].format(code=code, order_id=txid, amount=inv["amount"], currency=inv["currency"], pay_id=owner["binance_uid"], binance_name=owner["binance_name"], minutes=30, balance="")
        return JSONResponse({"ok": False, "error": "TX already used", "message": text}, status_code=400)
    # Enforce the invoice payment window. The Binance row timestamp is also checked
    # when Binance includes one in the history response.
    try:
        created = datetime.fromisoformat(str(inv["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        expires = created + timedelta(minutes=get_settings().invoice_expire_minutes)
        if datetime.now(timezone.utc) > expires:
            return JSONResponse({"ok": False, "status": "EXPIRED", "error": "invoice expired"}, status_code=400)
        min_ms = int(created.timestamp() * 1000)
        max_ms = int(expires.timestamp() * 1000)
    except Exception:
        min_ms = max_ms = None

    try:
        verified = await asyncio.to_thread(
            verify_any,
            txid,
            float(inv["amount"]),
            inv["currency"],
            owner["binance_api_key"],
            owner["binance_api_secret"],
            min_ms,
            max_ms,
        )
    except BinanceError as exc:
        raw = str(exc)
        tpl = msgs["fail_mismatch"] if "mismatch" in raw.lower() else msgs["fail_not_found"]
        text = tpl.format(code=code, order_id=txid, amount=inv["amount"], currency=inv["currency"], pay_id=owner["binance_uid"], binance_name=owner["binance_name"], minutes=30, balance="")
        return JSONResponse({"ok": False, "error": raw, "message": text}, status_code=400)
    canonical_txid = str(verified.get("transactionId") or txid).strip()
    applied, reason = db.claim_payment(invoice_id, canonical_txid)
    if not applied:
        if reason == "already_paid_same":
            pass
        elif reason in {"tx_already_used", "already_paid"}:
            return JSONResponse({"ok": False, "error": "Transaction already used", "status": "DUPLICATE"}, status_code=409)
        else:
            return JSONResponse({"ok": False, "error": reason}, status_code=400)
    payload = {
        "event": "PAYMENT_PAID",
        "invoice_id": invoice_id,
        "telegram_id": inv["telegram_id"],
        "amount": inv["amount"],
        "currency": inv["currency"],
        "txid": canonical_txid,
        "order_id": txid,
        "network": verified.get("network"),
        "status": "PAID",
    }
    await fire_webhook(bot, payload)
    success = msgs["success_text"].format(
        code=code,
        order_id=txid,
        amount=inv["amount"],
        currency=inv["currency"],
        pay_id=owner["binance_uid"],
        binance_name=owner["binance_name"],
        minutes=30,
        balance="{balance}",
    )
    return {"ok": True, "message": success, **payload}


@app.get("/api/v1/invoice/{invoice_id}")
def api_get_invoice(invoice_id: str, x_api_key: str | None = Header(default=None)):
    bot, owner = _auth_bot(x_api_key)
    inv = db.get_invoice(invoice_id)
    if not inv or inv["user_id"] != owner["id"]:
        raise HTTPException(404, "invoice not found")
    return {
        "ok": True,
        "invoice_id": inv["invoice_id"],
        "status": inv["status"],
        "amount": inv["amount"],
        "currency": inv["currency"],
        "txid": inv["txid"],
        "telegram_id": inv["telegram_id"],
        "uid": owner["binance_uid"],
        "binance_name": owner["binance_name"],
    }
