"""Cliente privado de Kraken (rutas autenticadas, docs.kraken.com).

Fase 2. Las llaves llegan SOLO por variables de entorno (GitHub Secrets):
  KRAKEN_API_KEY, KRAKEN_API_SECRET
Nunca se escriben en el repo ni en los logs.

Rutas usadas (todas documentadas en https://docs.kraken.com/api/docs/rest-api/):
  /0/private/Balance        saldo por moneda
  /0/private/OpenOrders     órdenes abiertas
  /0/private/TradesHistory  llenados
  /0/private/AddOrder       colocar orden (solo si LIVE=true)
  /0/private/CancelOrder    cancelar orden (solo si LIVE=true)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse
from typing import Any

import requests

API = "https://api.kraken.com"


class KrakenAuthError(RuntimeError):
    pass


def _sign(path: str, data: dict[str, Any], secret: str) -> str:
    """Firma según Kraken: HMAC-SHA512(path + SHA256(nonce + postdata), base64decode(secret))."""
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def _keys() -> tuple[str, str]:
    key = os.environ.get("KRAKEN_API_KEY", "")
    secret = os.environ.get("KRAKEN_API_SECRET", "")
    if not key or not secret:
        raise KrakenAuthError("faltan KRAKEN_API_KEY / KRAKEN_API_SECRET en el entorno")
    return key, secret


def _post(endpoint: str, data: dict[str, Any] | None = None) -> Any:
    key, secret = _keys()
    path = f"/0/private/{endpoint}"
    data = dict(data or {})
    for attempt in range(4):
        data["nonce"] = int(time.time() * 1000)
        headers = {"API-Key": key, "API-Sign": _sign(path, data, secret)}
        r = requests.post(API + path, headers=headers, data=data, timeout=20)
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            errs = body["error"]
            if any("Permission denied" in e or "Invalid key" in e or "Invalid signature" in e for e in errs):
                raise KrakenAuthError(f"Kraken {endpoint}: {errs}")
            if any("Rate limit" in e for e in errs):
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"Kraken {endpoint}: {errs}")
        return body["result"]
    raise RuntimeError(f"Kraken {endpoint}: sin respuesta tras reintentos")


# --- Solo lectura -------------------------------------------------------------

def balance() -> dict[str, float]:
    """Saldos > 0 por moneda. Kraken usa códigos propios: ZUSD = USD, XXBT = BTC."""
    res = _post("Balance")
    return {k: float(v) for k, v in res.items() if float(v) != 0}


def open_orders() -> dict[str, dict]:
    return _post("OpenOrders").get("open", {})


def trades_history(start: int | None = None) -> dict[str, dict]:
    data = {"start": start} if start else {}
    return _post("TradesHistory", data).get("trades", {})


# --- Escritura (bloqueada salvo LIVE=true) ------------------------------------

def live_enabled() -> bool:
    return os.environ.get("LIVE", "false").lower() == "true"


def add_order(pair: str, side: str, volume: float, price: float, post_only: bool = True,
              userref: int | None = None) -> dict:
    """Orden límite. `side` = buy | sell. post_only garantiza comisión maker (si cruzaría, se rechaza)."""
    if not live_enabled():
        raise PermissionError("LIVE=false: orden bloqueada (fase papel)")
    data: dict[str, Any] = {
        "pair": pair, "type": side, "ordertype": "limit",
        "volume": f"{volume:.8f}", "price": f"{price:.1f}",
    }
    if post_only:
        data["oflags"] = "post"
    if userref is not None:
        data["userref"] = userref
    return _post("AddOrder", data)


def cancel_order(txid: str) -> dict:
    if not live_enabled():
        raise PermissionError("LIVE=false: cancelación bloqueada (fase papel)")
    return _post("CancelOrder", {"txid": txid})


def balance_text() -> str:
    """Texto para Telegram / consola. No incluye llaves."""
    b = balance()
    usd = b.get("ZUSD", 0.0) + b.get("USD", 0.0)
    btc = b.get("XXBT", 0.0) + b.get("XBT", 0.0)
    extra = {k: v for k, v in b.items() if k not in ("ZUSD", "USD", "XXBT", "XBT")}
    lines = [f"Kraken conectado (solo lectura). USD {usd:,.2f} · BTC {btc:.6f}"]
    if extra:
        lines.append("Otros: " + ", ".join(f"{k} {v}" for k, v in extra.items()))
    oo = open_orders()
    lines.append(f"Órdenes abiertas: {len(oo)}")
    return "\n".join(lines)
