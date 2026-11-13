"""Grid en vivo sobre Kraken (fase 2). Mismas reglas que el papel (bot/grid.py), pero las órdenes
son reales: límite post-only (maker) con userref = C.LIVE_USERREF.

Estado en data/live_state.json y llenados en data/live_fills.jsonl (el historial de papel no se toca).

Flujo de cada tick:
  1. Saldos reales (Balance) → cash / coin.
  2. Llenados nuevos (TradesHistory desde el último tick) → contabilidad + orden contraria un nivel más allá.
  3. Órdenes abiertas (OpenOrders) → descartar las que ya no existen; cancelar huérfanas con nuestro userref.
  4. Una vez al día: stop de rango (venta a mercado + apagado) o reconstrucción del rango
     (cancelar todas las nuestras y volver a colocar el grid).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from . import config as C
from . import grid, kraken, kraken_private as kp
from .grid import State

DATA = grid.DATA
STATE = DATA / "live_state.json"
FILLS = DATA / "live_fills.jsonl"


# --- estado ----------------------------------------------------------------------

def load_state() -> State:
    if STATE.exists():
        return State(**json.loads(STATE.read_text()))
    s = State()
    s.cash = 0.0
    return s


def save_state(s: State) -> None:
    DATA.mkdir(exist_ok=True)
    s.log = s.log[-80:]
    STATE.write_text(json.dumps(asdict(s), indent=1))


def _log_fill(ts: str, side: str, price: float, qty: float, fee: float, pnl: float, txid: str) -> None:
    DATA.mkdir(exist_ok=True)
    with open(FILLS, "a") as f:
        f.write(json.dumps({"ts": ts, "side": side, "price": price, "qty": qty, "fee": round(fee, 4),
                            "pnl": round(pnl, 4), "txid": txid}) + "\n")


# --- tamaño -----------------------------------------------------------------------

def level_plan(price: float, ordermin: float) -> tuple[int, float]:
    """Cuántos niveles caben con el capital LIVE respetando el mínimo de Kraken.
    Devuelve (niveles, usd_por_nivel)."""
    budget = C.LIVE_CAPITAL_USD * C.MAX_INVENTORY_PCT
    min_usd = max(ordermin * price, C.MIN_ORDER_USD if C.MIN_ORDER_USD < budget else 0) * 1.05  # 5% de holgura
    n = min(C.N_LEVELS, int(budget // min_usd)) if min_usd > 0 else C.N_LEVELS
    n = max(n, 0)
    per = budget / n if n else 0.0
    return n, per


def plan_grid(s: State, price: float, ohlc: list[dict], ordermin: float) -> list[dict]:
    """Niveles a colocar (sin enviar): compras bajo el precio si la tendencia lo permite,
    ventas del inventario existente arriba."""
    closes = [c["close"] for c in ohlc]
    trend_ok = C.LIVE_IGNORE_TREND or price >= grid.sma(closes, C.TREND_SMA_DAYS)
    n, per = level_plan(price, ordermin)
    out: list[dict] = []
    if trend_ok:
        cash_left = s.cash
        for i in range(1, n + 1):
            p = round(price * (1 - C.LEVEL_SPACING_PCT) ** i, 1)
            if p < s.range_low or cash_left < per * (1 + C.FEE_MAKER):
                break
            out.append({"side": "buy", "price": p, "qty": round(per / p, 8), "level": -i, "txid": ""})
            cash_left -= per * (1 + C.FEE_MAKER)
    if s.coin >= ordermin:
        chunks = max(1, min(n or 1, int(s.coin // ordermin)))
        q = round(s.coin / chunks, 8)
        remaining = s.coin
        for i in range(1, chunks + 1):
            if remaining < ordermin:
                break
            p = round(price * (1 + C.LEVEL_SPACING_PCT) ** i, 1)
            if p > s.range_high:
                break
            qty = round(min(q, remaining), 8)
            out.append({"side": "sell", "price": p, "qty": qty, "level": i, "txid": ""})
            remaining -= qty
    return out


# --- Kraken ----------------------------------------------------------------------

def _mine(order: dict) -> bool:
    return int(order.get("userref") or 0) == C.LIVE_USERREF


def sync_balances(s: State) -> None:
    b = kp.balance()
    s.cash = round(b.get("ZUSD", 0.0) + b.get("USD", 0.0), 2)
    s.coin = round(b.get("XXBT", 0.0) + b.get("XBT", 0.0), 8)
    if s.coin <= 1e-9:
        s.coin, s.avg_cost = 0.0, 0.0


def place(s: State, od: dict) -> str:
    res = kp.add_order(C.PAIR, od["side"], od["qty"], od["price"], post_only=True, userref=C.LIVE_USERREF)
    txid = (res.get("txid") or [""])[0]
    od["txid"] = txid
    return txid


def cancel_all_mine(s: State, open_orders: dict | None = None) -> int:
    oo = open_orders if open_orders is not None else kp.open_orders()
    n = 0
    for txid, o in oo.items():
        if _mine(o):
            try:
                kp.cancel_order(txid)
                n += 1
            except Exception as e:  # noqa: BLE001 — seguir con las demás
                print(f"[live] no pude cancelar {txid}: {e}")
    s.orders = []
    return n


def process_fills(s: State, since: int, now: str) -> list[str]:
    """Lee los trades desde `since` (unix) y aplica los que pertenecen a nuestras órdenes."""
    events: list[str] = []
    trades = kp.trades_history(start=since)
    by_order: dict[str, dict] = {}
    for tid, t in trades.items():
        if t.get("pair") not in (C.PAIR, "XXBTZUSD", "XBTUSD"):
            continue
        o = by_order.setdefault(t["ordertxid"], {"vol": 0.0, "cost": 0.0, "fee": 0.0, "type": t["type"], "tid": tid})
        o["vol"] += float(t["vol"])
        o["cost"] += float(t["cost"])
        o["fee"] += float(t["fee"])
    new_orders: list[dict] = []
    for od in s.orders:
        f = by_order.get(od["txid"])
        if not f or f["vol"] < od["qty"] * 0.99:
            if f:  # llenado parcial: ajustar lo que queda
                od["qty"] = round(od["qty"] - f["vol"], 8)
            new_orders.append(od)
            continue
        price = f["cost"] / f["vol"]
        fee = f["fee"]
        if od["side"] == "buy":
            prev_coin = max(s.coin - f["vol"], 0.0)   # Balance ya incluye esta compra
            s.avg_cost = (s.avg_cost * prev_coin + price * f["vol"]) / (prev_coin + f["vol"]) if prev_coin > 0 else price
            s.fees_paid += fee
            _log_fill(now, "buy", round(price, 1), f["vol"], fee, 0.0, od["txid"])
            events.append(f"compra {f['vol']:.6f} @ {price:,.0f}")
            nxt = {"side": "sell", "price": round(od["price"] * (1 + C.LEVEL_SPACING_PCT), 1),
                   "qty": round(f["vol"], 8), "level": od["level"] + 1, "txid": ""}
            place(s, nxt)
            new_orders.append(nxt)
        else:
            pnl = (price - s.avg_cost) * f["vol"] - fee - (s.avg_cost * f["vol"] * C.FEE_MAKER)
            s.fees_paid += fee
            s.realized_pnl += pnl
            s.round_trips += 1
            _log_fill(now, "sell", round(price, 1), f["vol"], fee, pnl, od["txid"])
            events.append(f"venta {f['vol']:.6f} @ {price:,.0f} → vuelta #{s.round_trips} {pnl:+.2f}")
            buy_p = round(od["price"] * (1 - C.LEVEL_SPACING_PCT), 1)
            if buy_p >= s.range_low:
                nxt = {"side": "buy", "price": buy_p, "qty": round(f["vol"], 8), "level": od["level"] - 1, "txid": ""}
                place(s, nxt)
                new_orders.append(nxt)
    s.orders = new_orders
    return events


def reconcile_open(s: State) -> list[str]:
    """Descarta órdenes que Kraken ya no tiene; cancela huérfanas con nuestro userref."""
    events: list[str] = []
    oo = kp.open_orders()
    live_ids = {txid for txid, o in oo.items() if _mine(o)}
    tracked = {od["txid"] for od in s.orders}
    gone = [od for od in s.orders if od["txid"] and od["txid"] not in live_ids]
    if gone:
        s.orders = [od for od in s.orders if od["txid"] in live_ids]
        events.append(f"{len(gone)} orden(es) ya no estaban en Kraken (canceladas o llenadas sin trade visible)")
    for txid in live_ids - tracked:
        o = oo[txid]
        d = o.get("descr", {})
        try:
            od = {"side": d.get("type"), "price": float(d.get("price")), "qty": round(float(o.get("vol", 0)) - float(o.get("vol_exec", 0)), 8),
                  "level": 0, "txid": txid}
            if od["side"] in ("buy", "sell") and od["qty"] > 0:
                s.orders.append(od)
                events.append(f"adoptada orden {od['side']} {od['qty']:.6f} @ {od['price']:,.0f} ({txid})")
        except Exception as e:  # noqa: BLE001
            print(f"[live] no pude adoptar {txid}: {e}")
    return events


def rebuild(s: State, ohlc: list[dict], price: float, today: str, ordermin: float) -> list[str]:
    recent = ohlc[-C.RANGE_LOOKBACK_DAYS:]
    s.range_low = min(c["low"] for c in recent)
    s.range_high = max(c["high"] for c in recent)
    s.range_built, s.center = today, price
    events: list[str] = []
    try:
        cancel_all_mine(s)
    except Exception as e:  # noqa: BLE001
        events.append(f"ERROR cancelando: {e}")
    time.sleep(1)
    try:
        sync_balances(s)
    except Exception as e:  # noqa: BLE001
        events.append(f"ERROR leyendo saldo: {e}")
    plan = plan_grid(s, price, ohlc, ordermin)
    placed = []
    for od in plan:
        try:
            place(s, od)
            placed.append(od)
        except Exception as e:  # noqa: BLE001
            events.append(f"ERROR orden {od['side']} {od['qty']} @ {od['price']}: {e}")
    s.orders = placed
    events.append(f"rango reconstruido {s.range_low:.0f}–{s.range_high:.0f}, centro {price:.0f}, {len(placed)} órdenes reales")
    return events


def last_completed_close(ohlc: list[dict]) -> float:
    """Kraken incluye la vela del día en curso al final; el cierre diario válido es el anterior."""
    import datetime as _dt
    today_start = _dt.datetime.now(_dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    done = [c for c in ohlc if float(c.get("time", 0)) < today_start]
    return (done[-1] if done else ohlc[-1])["close"]


def range_stop(s: State, ohlc: list[dict], today: str, ordermin: float) -> list[str]:
    last_close = last_completed_close(ohlc)
    floor = s.range_low * (1 - C.RANGE_STOP_PCT)
    if s.active and last_close < floor:
        cancel_all_mine(s)
        time.sleep(1)
        sync_balances(s)
        pnl = 0.0
        if s.coin >= ordermin:
            kp.market_sell(C.PAIR, s.coin, userref=C.LIVE_USERREF)
            fee = s.coin * last_close * C.FEE_TAKER
            pnl = (last_close - s.avg_cost) * s.coin - fee
            s.realized_pnl += pnl
            s.fees_paid += fee
            _log_fill(today, "sell", last_close, s.coin, fee, pnl, "market-stop")
        s.active, s.stopped_at, s.days_inside = False, today, 0
        return [f"STOP DE RANGO: cierre {last_close:.0f} < {floor:.0f}. Inventario liquidado a mercado ({pnl:+.2f}). Bot apagado."]
    if not s.active:
        s.days_inside = s.days_inside + 1 if s.range_low <= last_close <= s.range_high else 0
        if s.days_inside >= C.RESUME_DAYS_INSIDE:
            s.active, s.stopped_at = True, ""
            return [f"reanudado: {C.RESUME_DAYS_INSIDE} cierres dentro del rango"]
    return []


# --- tick --------------------------------------------------------------------------

def tick(now_iso: str, today: str) -> tuple[State, float, list[str]]:
    s = load_state()
    t = kraken.ticker(C.PAIR)
    ohlc = kraken.daily_ohlc(C.PAIR, 60)
    info = kraken.pair_info(C.PAIR)
    ordermin = max(info["ordermin"], C.LIVE_MIN_ORDER_BTC)
    price = t["last"]
    events: list[str] = []
    first_run = not s.last_check

    sync_balances(s)
    since = int(_iso_to_unix(s.last_check)) - 120 if s.last_check else int(time.time()) - 86400
    try:
        if s.orders:
            events += process_fills(s, since, now_iso)
        events += reconcile_open(s)
    except Exception as e:  # noqa: BLE001
        events.append(f"ERROR conciliando: {e}")
    try:
        if s.range_low and s.range_built != today:
            events += range_stop(s, ohlc, today, ordermin)
        if s.active and (not s.range_low or s.range_built != today):
            events += rebuild(s, ohlc, price, today, ordermin)
    except Exception as e:  # noqa: BLE001
        events.append(f"ERROR rango/stop: {e}")
    if s.active and s.range_built == today and not s.orders:
        # grid vacío (p. ej. precio pegado al borde inferior en la reconstrucción): reintentar centrado en el precio actual
        plan = plan_grid(s, price, ohlc, ordermin)
        placed = []
        for od in plan:
            try:
                place(s, od)
                placed.append(od)
            except Exception as e:  # noqa: BLE001
                print(f"[live] rechazo {od['side']} {od['qty']} @ {od['price']}: {e}")
        if placed:
            s.center = price
            s.orders = placed
            events.append(f"grid recentrado en {price:.0f}: {len(placed)} órdenes reales")
    if first_run:
        events.insert(0, f"LIVE activo: saldo USD {s.cash:.2f}, BTC {s.coin:.6f}, mínimo de orden {ordermin} BTC")
    s.last_price, s.last_check = price, now_iso
    for e in events:
        s.log.append(f"{now_iso[5:16].replace('T', ' ')} {e}")
    save_state(s)
    return s, price, events


def _iso_to_unix(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


def status_text(s: State, price: float) -> str:
    eq = grid.equity(s, price)
    inv_pct = (s.coin * price) / C.LIVE_CAPITAL_USD if C.LIVE_CAPITAL_USD else 0
    dist_low = (price - s.range_low) / price if s.range_low else 0
    dist_high = (s.range_high - price) / price if s.range_high else 0
    buys = sum(1 for o in s.orders if o["side"] == "buy")
    sells = sum(1 for o in s.orders if o["side"] == "sell")
    return (f"LIVE {C.PAIR_LABEL} {price:,.0f} · {'ACTIVO' if s.active else 'APAGADO'}\n"
            f"equity ${eq:,.2f} ({eq - C.LIVE_CAPITAL_USD:+.2f}) · realizado ${s.realized_pnl:+.2f} · comisiones ${s.fees_paid:.2f}\n"
            f"vueltas {s.round_trips} · inventario {s.coin:.6f} BTC ({inv_pct:.0%}) costo prom. {s.avg_cost:,.0f}\n"
            f"rango {s.range_low:,.0f}–{s.range_high:,.0f} (borde bajo a {dist_low:.1%}, alto a {dist_high:.1%})\n"
            f"órdenes reales: {buys} compras / {sells} ventas")
