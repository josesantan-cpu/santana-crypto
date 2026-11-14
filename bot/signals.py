"""Señales de flujo para BTC/USD — SOLO REGISTRO (fase de observación, aprobada por José 14 nov 2026).

Este módulo no toca órdenes. En cada tick toma una foto de tres fuentes y la guarda en
`data/signals.jsonl` junto con el precio, para medir después si la señal anticipó el movimiento.

Fuentes (todas rutas documentadas):
  1. Kraken público `Trades`  — últimos ~1000 trades con lado agresor (b = compra, s = venta).
  2. Kraken público `Depth`   — libro de órdenes: muros de compra/venta cerca del precio.
  3. Unusual Whales `/api/crypto/whales/recent` — ballenas cripto; se filtra BTC. Opcional:
     solo si existe UW_API_KEY en el entorno; si no, se registra como "no disponible".

Cuando José apruebe pasar a fase activa, `verdict()` será el filtro de compras del grid.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import kraken

DATA = Path(__file__).resolve().parent.parent / "data"
SIGNALS_FILE = DATA / "signals.jsonl"
UW_BASE = "https://api.unusualwhales.com"

BIG_TRADE_BTC = 1.0        # trade "grande" en Kraken: ≥ 1 BTC
WALL_BAND_PCT = 0.02       # banda ±2% para medir muros del libro


# --- 1. Kraken Trades: desbalance compra/venta -----------------------------------------
def kraken_trade_flow(pair: str) -> dict[str, Any]:
    """Volumen comprado vs vendido en los últimos ~1000 trades y conteo de trades grandes."""
    res = kraken._get("Trades", {"pair": pair})
    rows = next(v for k, v in res.items() if k != "last")
    buy = sell = 0.0
    big_buy = big_sell = 0
    t0 = t1 = None
    for r in rows:
        # formato documentado: [price, volume, time, buy/sell, market/limit, misc, trade_id]
        vol, ts, side = float(r[1]), float(r[2]), r[3]
        t0 = ts if t0 is None else min(t0, ts)
        t1 = ts if t1 is None else max(t1, ts)
        if side == "b":
            buy += vol
            big_buy += vol >= BIG_TRADE_BTC
        else:
            sell += vol
            big_sell += vol >= BIG_TRADE_BTC
    total = buy + sell
    return {
        "trades": len(rows),
        "minutes": round((t1 - t0) / 60, 1) if t0 is not None and t1 is not None else 0,
        "buy_btc": round(buy, 4),
        "sell_btc": round(sell, 4),
        "buy_ratio": round(buy / total, 3) if total else 0.5,   # 0.5 = equilibrio
        "big_buy": big_buy,
        "big_sell": big_sell,
    }


# --- 2. Kraken Depth: muros del libro ----------------------------------------------------
def kraken_walls(pair: str, price: float) -> dict[str, Any]:
    """Volumen de órdenes de compra y venta dentro de ±2% del precio y el nivel con más volumen de cada lado."""
    res = kraken._get("Depth", {"pair": pair, "count": 500})
    book = next(iter(res.values()))
    lo, hi = price * (1 - WALL_BAND_PCT), price * (1 + WALL_BAND_PCT)
    bids = [(float(p), float(v)) for p, v, *_ in book["bids"] if float(p) >= lo]
    asks = [(float(p), float(v)) for p, v, *_ in book["asks"] if float(p) <= hi]
    bid_vol = sum(v for _, v in bids)
    ask_vol = sum(v for _, v in asks)
    bid_wall = max(bids, key=lambda x: x[1], default=(0.0, 0.0))
    ask_wall = max(asks, key=lambda x: x[1], default=(0.0, 0.0))
    return {
        "bid_btc_2pct": round(bid_vol, 3),
        "ask_btc_2pct": round(ask_vol, 3),
        "bid_share": round(bid_vol / (bid_vol + ask_vol), 3) if bid_vol + ask_vol else 0.5,
        "bid_wall": {"price": bid_wall[0], "btc": round(bid_wall[1], 3)},
        "ask_wall": {"price": ask_wall[0], "btc": round(ask_wall[1], 3)},
    }


# --- 3. Unusual Whales: ballenas cripto (opcional) -------------------------------------
def uw_whales_btc() -> dict[str, Any]:
    """Compras vs ventas de ballenas en pares de BTC según UW. Requiere UW_API_KEY; si falta, lo dice."""
    token = os.environ.get("UW_API_KEY", "")
    if not token:
        return {"available": False, "reason": "sin UW_API_KEY"}
    r = requests.get(f"{UW_BASE}/api/crypto/whales/recent", params={"limit": 500},
                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    body = r.json()
    rows = body.get("data", body) if isinstance(body, dict) else body
    buy_usd = sell_usd = 0.0
    n = 0
    for row in rows:
        pair = str(row.get("pair", "")).upper()
        if not pair.startswith("BTC-") and not pair.startswith("WBTC-") and not pair.startswith("XBT-"):
            continue
        usd = float(row.get("total") or 0)
        if usd <= 0:
            continue
        n += 1
        if row.get("side") == "buy":
            buy_usd += usd
        else:
            sell_usd += usd
    total = buy_usd + sell_usd
    return {
        "available": True,
        "rows_total": len(rows),
        "btc_trades": n,
        "buy_usd": round(buy_usd),
        "sell_usd": round(sell_usd),
        "buy_ratio": round(buy_usd / total, 3) if total else None,
    }


# --- Foto completa + veredicto ------------------------------------------------------------
def snapshot(pair: str, price: float) -> dict[str, Any]:
    """Toma las tres fuentes; cada una protegida: un fallo se registra como error, no detiene nada."""
    out: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "price": price}
    for name, fn in (("flow", lambda: kraken_trade_flow(pair)),
                     ("walls", lambda: kraken_walls(pair, price)),
                     ("uw", uw_whales_btc)):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — registro, nunca bloquea el bot
            out[name] = {"error": str(e)[:200]}
    out["verdict"] = verdict(out)
    return out


def verdict(snap: dict[str, Any]) -> str:
    """Lectura simple: 'compradores', 'vendedores' o 'neutral'. Por ahora solo informativa."""
    flow = snap.get("flow", {})
    walls = snap.get("walls", {})
    score = 0
    br = flow.get("buy_ratio")
    if br is not None:
        score += 1 if br >= 0.58 else -1 if br <= 0.42 else 0
    bs = walls.get("bid_share")
    if bs is not None:
        score += 1 if bs >= 0.6 else -1 if bs <= 0.4 else 0
    uw = snap.get("uw", {})
    ur = uw.get("buy_ratio")
    if ur is not None:
        score += 1 if ur >= 0.6 else -1 if ur <= 0.4 else 0
    return "compradores" if score >= 2 else "vendedores" if score <= -2 else "neutral"


def record(pair: str, price: float) -> dict[str, Any]:
    """Toma la foto y la agrega a data/signals.jsonl. Devuelve la foto."""
    snap = snapshot(pair, price)
    DATA.mkdir(exist_ok=True)
    with SIGNALS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return snap


def summary_line(snap: dict[str, Any]) -> str:
    """Una línea para el log/estado."""
    flow, walls, uw = snap.get("flow", {}), snap.get("walls", {}), snap.get("uw", {})
    parts = [f"señal: {snap.get('verdict', '?')}"]
    if "buy_ratio" in flow:
        parts.append(f"trades {flow['buy_ratio']:.0%} compra ({flow['big_buy']}▲/{flow['big_sell']}▼ grandes)")
    if "bid_share" in walls:
        parts.append(f"libro {walls['bid_share']:.0%} bids, muro compra {walls['bid_wall']['price']:,.0f}")
    if uw.get("available"):
        parts.append(f"UW ballenas BTC {uw['btc_trades']} ({uw['buy_ratio'] if uw['buy_ratio'] is not None else 's/d'})")
    elif uw.get("reason"):
        parts.append(f"UW {uw['reason']}")
    return " · ".join(parts)
