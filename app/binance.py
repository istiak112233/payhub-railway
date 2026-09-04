from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings

BASE = "https://api.binance.com"


class BinanceError(Exception):
    pass


def _keys(api_key: str = "", api_secret: str = "") -> tuple[str, str]:
    """Use merchant/user keys passed by PayHub; env keys are fallback only."""
    s = get_settings()
    key = (api_key or s.binance_api_key or "").strip()
    secret = (api_secret or s.binance_api_secret or "").strip()
    return key, secret


def _server_offset() -> int:
    try:
        r = httpx.get(f"{BASE}/api/v3/time", timeout=10)
        r.raise_for_status()
        return int(r.json()["serverTime"]) - int(time.time() * 1000)
    except Exception:
        return 0


def signed_get(path: str, params: dict | None = None, api_key: str = "", api_secret: str = "") -> Any:
    key, secret = _keys(api_key, api_secret)
    if not key or not secret:
        raise BinanceError("Binance API key/secret set kora hoyni.")
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000) + _server_offset()
    params["recvWindow"] = 60000
    query = urlencode(params)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{query}&signature={sig}"
    last = None
    for _ in range(3):
        try:
            r = httpx.get(url, headers={"X-MBX-APIKEY": key}, timeout=45)
            try:
                data = r.json()
            except Exception:
                data = {"msg": r.text[:300]}
            if r.status_code != 200:
                msg = data.get("msg") if isinstance(data, dict) else data
                raise BinanceError(str(msg))
            return data
        except BinanceError:
            raise
        except Exception as exc:
            last = exc
            time.sleep(1)
    raise BinanceError(f"Binance timeout: {last}")


def live_balances(api_key: str = "", api_secret: str = "", coin_filter: str = "") -> dict[str, Any]:
    spot = signed_get("/api/v3/account", api_key=api_key, api_secret=api_secret)
    items = []
    for b in spot.get("balances") or []:
        free = float(b.get("free") or 0)
        locked = float(b.get("locked") or 0)
        if free + locked <= 0:
            continue
        asset = str(b.get("asset") or "")
        if coin_filter and asset.upper() != coin_filter.upper():
            continue
        items.append({"asset": asset, "free": free, "locked": locked, "total": free + locked, "wallet": "SPOT"})
    try:
        funding = signed_get("/sapi/v1/asset/get-funding-asset", {}, api_key=api_key, api_secret=api_secret)
        if isinstance(funding, list):
            for b in funding:
                free = float(b.get("free") or b.get("available") or 0)
                locked = float(b.get("locked") or 0)
                asset = str(b.get("asset") or "")
                if free + locked <= 0:
                    continue
                if coin_filter and asset.upper() != coin_filter.upper():
                    continue
                items.append({"asset": asset, "free": free, "locked": locked, "total": free + locked, "wallet": "FUNDING"})
    except BinanceError:
        pass
    usdt = sum(i["total"] for i in items if i["asset"] == "USDT")
    return {"ok": True, "usdt": usdt, "items": items}


_ID_KEYS = {
    "transactionid", "transaction_id", "orderid", "order_id", "merchanttradeno",
    "merchant_trade_no", "prepayid", "prepay_id", "txid", "tx_id", "id"
}


def _norm_id(value: Any) -> str:
    return str(value or "").strip()


def _candidate_ids(obj: Any) -> set[str]:
    """Collect identifier values only; never substring-match the entire JSON blob."""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower().replace("-", "_")
            if key in _ID_KEYS and not isinstance(v, (dict, list)):
                val = _norm_id(v)
                if val:
                    out.add(val)
            elif isinstance(v, (dict, list)):
                out.update(_candidate_ids(v))
    elif isinstance(obj, list):
        for v in obj:
            out.update(_candidate_ids(v))
    return out


def _match_row(row: Any, needle: str) -> bool:
    needle = _norm_id(needle)
    if not needle:
        return False
    return needle in _candidate_ids(row)


def _row_time_ms(row: dict) -> int | None:
    for key in ("transactionTime", "createTime", "insertTime", "time", "timestamp"):
        value = row.get(key)
        if value is None:
            continue
        try:
            n = int(value)
            return n if n > 10_000_000_000 else n * 1000
        except Exception:
            pass
    return None


def _check_time_window(row: dict, min_time_ms: int | None, max_time_ms: int | None) -> None:
    if min_time_ms is None and max_time_ms is None:
        return
    t = _row_time_ms(row)
    # Some Binance history responses omit a reliable timestamp; do not reject solely for that.
    if t is None:
        return
    if min_time_ms is not None and t < min_time_ms:
        raise BinanceError("Order was completed before this invoice was created.")
    if max_time_ms is not None and t > max_time_ms:
        raise BinanceError("Order was completed after this invoice expired.")


def verify_any(
    order_id: str,
    expected_amount: float,
    coin: str,
    api_key: str = "",
    api_secret: str = "",
    min_time_ms: int | None = None,
    max_time_ms: int | None = None,
) -> dict:
    """Verify an exact Binance Order/TX ID against Pay history, then deposits.

    The supplied value is matched only against known identifier fields. This avoids the old
    false-positive behavior where a short numeric ID could match unrelated text in a row.
    """
    needle = _norm_id(order_id)
    if not needle:
        raise BinanceError("Order ID required.")

    pay = signed_get("/sapi/v1/pay/transactions", {"limit": 100}, api_key=api_key, api_secret=api_secret)
    rows = pay.get("data") if isinstance(pay, dict) else pay
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or not _match_row(row, needle):
                continue
            _check_time_window(row, min_time_ms, max_time_ms)
            amount = float(row.get("amount") or 0)
            currency = str(row.get("currency") or coin).upper()
            if amount <= 0:
                raise BinanceError("Outgoing transfer — receive hoy nai.")
            if currency != coin.upper():
                raise BinanceError(f"Coin mismatch: {currency}")
            if abs(amount - expected_amount) > max(0.01, expected_amount * 1e-6):
                raise BinanceError(f"Amount mismatch: expected {expected_amount}, got {amount}")
            ids = _candidate_ids(row)
            canonical = str(row.get("transactionId") or row.get("orderId") or row.get("merchantTradeNo") or needle)
            return {
                "transactionId": canonical,
                "submittedOrderId": needle,
                "matchedIds": sorted(ids),
                "amount": amount,
                "coin": currency,
                "network": "BINANCE_PAY",
            }

    dep = signed_get(
        "/sapi/v1/capital/deposit/hisrec",
        {"coin": coin.upper(), "status": 1},
        api_key=api_key,
        api_secret=api_secret,
    )
    if isinstance(dep, list):
        for row in dep:
            if not isinstance(row, dict) or not _match_row(row, needle):
                continue
            _check_time_window(row, min_time_ms, max_time_ms)
            amount = float(row.get("amount") or 0)
            got = str(row.get("coin") or coin).upper()
            if got != coin.upper():
                raise BinanceError(f"Coin mismatch: {got}")
            if abs(amount - expected_amount) > max(0.01, expected_amount * 1e-6):
                raise BinanceError(f"Amount mismatch: expected {expected_amount}, got {amount}")
            canonical = str(row.get("txId") or needle)
            return {
                "transactionId": canonical,
                "submittedOrderId": needle,
                "matchedIds": sorted(_candidate_ids(row)),
                "amount": amount,
                "coin": got,
                "network": str(row.get("network") or "CRYPTO"),
            }
    raise BinanceError("Order/TX ID pai nai. 2-5 min pore abar try korun.")
