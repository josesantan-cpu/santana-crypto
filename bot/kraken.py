"""Cliente público de Kraken (sin llaves). Fase 1 no necesita autenticación."""
from __future__ import annotations

import time
from typing import Any

import requests

BASE = "https://api.kraken.com/0/public"


def _get(path: str, params: dict[str, Any]) -> Any:
    for attempt in range(4):
        r = requests.get(f"{BASE}/{path}", params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(f"Kraken {path}: {body['error']}")
        return body["result"]
    raise RuntimeError(f"Kraken {path}: sin respuesta tras reintentos")


def ticker(pair: str) -> dict:
    """Devuelve bid, ask, last del par."""
    res = _get("Ticker", {"pair": pair})
    data = next(iter(res.values()))
    return {"bid": float(data["b"][0]), "ask": float(data["a"][0]), "last": float(data["c"][0])}


def daily_ohlc(pair: str, days: int = 60) -> list[dict]:
    """Velas diarias (las últimas `days`). Cada vela: time, open, high, low, close, volume."""
    res = _get("OHLC", {"pair": pair, "interval": 1440})
    rows = next(v for k, v in res.items() if k != "last")
    out = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
            "close": float(r[4]), "volume": float(r[6])} for r in rows]
    return out[-days:]


def pair_info(pair: str) -> dict:
    """Mínimo de orden y decimales del par (ruta pública AssetPairs)."""
    res = _get("AssetPairs", {"pair": pair})
    data = next(iter(res.values()))
    return {"ordermin": float(data.get("ordermin", 0) or 0), "pair_decimals": int(data.get("pair_decimals", 1)),
            "lot_decimals": int(data.get("lot_decimals", 8)), "altname": data.get("altname", pair)}
