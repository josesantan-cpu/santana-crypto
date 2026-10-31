"""Grid adaptativo en papel.

Reglas:
- Rango = [mínimo, máximo] de los últimos RANGE_LOOKBACK_DAYS días; se recalcula una vez al día.
- Niveles de compra bajo el precio y de venta arriba, separados LEVEL_SPACING_PCT.
- Cada compra llena crea una venta un nivel más arriba (y viceversa): eso es una "vuelta".
- Filtro de tendencia: precio bajo la SMA50 → no se colocan compras nuevas (solo se vende inventario).
- Inventario tope: MAX_INVENTORY_PCT del capital en moneda.
- Stop de rango: cierre diario < borde inferior × (1 − RANGE_STOP_PCT) → vende todo, se apaga;
  vuelve tras RESUME_DAYS_INSIDE cierres dentro del rango.
- Comisiones descontadas en cada llenado (maker si USE_MAKER).

El estado vive en data/state.json y cada llenado en data/fills.jsonl.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config as C

DATA = Path(__file__).resolve().parent.parent / "data"
STATE = DATA / "state.json"
FILLS = DATA / "fills.jsonl"


@dataclass
class Order:
    side: str          # buy | sell
    price: float
    qty: float         # en moneda (BTC)
    level: int         # índice de nivel en el grid (0 = centro)


@dataclass
class State:
    active: bool = True
    cash: float = C.CAPITAL_USD
    coin: float = 0.0
    avg_cost: float = 0.0
    range_low: float = 0.0
    range_high: float = 0.0
    range_built: str = ""           # fecha (UTC) de la última reconstrucción
    center: float = 0.0
    orders: list[dict] = field(default_factory=list)
    round_trips: int = 0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    days_inside: int = 0
    stopped_at: str = ""
    last_price: float = 0.0
    last_check: str = ""
    log: list[str] = field(default_factory=list)


def load_state() -> State:
    if STATE.exists():
        d = json.loads(STATE.read_text())
        return State(**d)
    return State()


def save_state(s: State) -> None:
    DATA.mkdir(exist_ok=True)
    s.log = s.log[-50:]
    STATE.write_text(json.dumps(asdict(s), indent=1))


def fee_rate() -> float:
    return C.FEE_MAKER if C.USE_MAKER else C.FEE_TAKER


def sma(closes: list[float], n: int) -> float:
    xs = closes[-n:]
    return sum(xs) / len(xs) if xs else 0.0


def build_range(s: State, ohlc: list[dict], price: float, today: str) -> list[str]:
    """Recalcula rango y niveles. Conserva inventario; reemplaza las órdenes pendientes."""
    recent = ohlc[-C.RANGE_LOOKBACK_DAYS:]
    low = min(c["low"] for c in recent)
    high = max(c["high"] for c in recent)
    s.range_low, s.range_high, s.range_built = low, high, today
    s.center = price
    s.orders = place_grid(s, price, ohlc)
    return [f"rango reconstruido {low:.0f}–{high:.0f}, centro {price:.0f}, {len(s.orders)} órdenes"]


def place_grid(s: State, price: float, ohlc: list[dict]) -> list[dict]:
    closes = [c["close"] for c in ohlc]
    trend_ok = price >= sma(closes, C.TREND_SMA_DAYS)
    per_level_usd = (C.CAPITAL_USD * C.MAX_INVENTORY_PCT) / C.N_LEVELS
    orders: list[Order] = []
    # compras bajo el precio (solo si la tendencia lo permite y hay efectivo)
    if trend_ok:
        for i in range(1, C.N_LEVELS + 1):
            p = price * (1 - C.LEVEL_SPACING_PCT) ** i
            if p < s.range_low:
                break
            orders.append(Order("buy", round(p, 2), round(per_level_usd / p, 8), -i))
    # ventas arriba del precio con el inventario que ya existe
    if s.coin > 0:
        chunk = s.coin / min(C.N_LEVELS, max(1, round(s.coin * price / per_level_usd)))
        remaining = s.coin
        for i in range(1, C.N_LEVELS + 1):
            if remaining <= 1e-9:
                break
            p = price * (1 + C.LEVEL_SPACING_PCT) ** i
            if p > s.range_high:
                break
            q = min(chunk, remaining)
            orders.append(Order("sell", round(p, 2), round(q, 8), i))
            remaining -= q
    return [asdict(o) for o in orders]


def process_fills(s: State, bid: float, ask: float, now: str) -> list[str]:
    """Simula llenados: una compra llena si ask ≤ precio; una venta si bid ≥ precio.
    Cada llenado genera la orden contraria un nivel más allá."""
    events: list[str] = []
    fr = fee_rate()
    new_orders: list[dict] = []
    for od in s.orders:
        o = Order(**od)
        if o.side == "buy" and ask <= o.price:
            cost = o.qty * o.price
            if s.cash < cost or (s.coin * o.price + cost) > C.CAPITAL_USD * C.MAX_INVENTORY_PCT + 1e-6:
                new_orders.append(od)          # sin efectivo o tope de inventario: la orden se queda
                continue
            fee = cost * fr
            s.avg_cost = (s.avg_cost * s.coin + o.price * o.qty) / (s.coin + o.qty)
            s.cash -= cost + fee
            s.coin += o.qty
            s.fees_paid += fee
            _log_fill(now, "buy", o.price, o.qty, fee, 0.0)
            events.append(f"compra {o.qty:.6f} @ {o.price:.0f}")
            sell_p = round(o.price * (1 + C.LEVEL_SPACING_PCT), 2)
            new_orders.append(asdict(Order("sell", sell_p, o.qty, o.level + 1)))
        elif o.side == "sell" and bid >= o.price:
            proceeds = o.qty * o.price
            fee = proceeds * fr
            pnl = (o.price - s.avg_cost) * o.qty - fee - (s.avg_cost * o.qty * fr)
            s.cash += proceeds - fee
            s.coin -= o.qty
            s.fees_paid += fee
            s.realized_pnl += pnl
            s.round_trips += 1
            _log_fill(now, "sell", o.price, o.qty, fee, pnl)
            events.append(f"venta {o.qty:.6f} @ {o.price:.0f} → vuelta #{s.round_trips} {pnl:+.2f}")
            buy_p = round(o.price * (1 - C.LEVEL_SPACING_PCT), 2)
            if buy_p >= s.range_low:
                new_orders.append(asdict(Order("buy", buy_p, o.qty, o.level - 1)))
        else:
            new_orders.append(od)
    if s.coin <= 1e-9:
        s.coin, s.avg_cost = 0.0, 0.0
    s.orders = new_orders
    return events


def range_stop_check(s: State, ohlc: list[dict], today: str) -> list[str]:
    """Con el último cierre diario: apaga si rompió el borde inferior; reanuda tras N cierres dentro."""
    last_close = ohlc[-1]["close"]
    floor = s.range_low * (1 - C.RANGE_STOP_PCT)
    if s.active and last_close < floor:
        proceeds = s.coin * last_close
        fee = proceeds * C.FEE_TAKER
        pnl = (last_close - s.avg_cost) * s.coin - fee
        s.cash += proceeds - fee
        s.realized_pnl += pnl
        s.fees_paid += fee
        if s.coin > 0:
            _log_fill(today, "sell", last_close, s.coin, fee, pnl)
        s.coin, s.avg_cost, s.orders = 0.0, 0.0, []
        s.active, s.stopped_at, s.days_inside = False, today, 0
        return [f"STOP DE RANGO: cierre {last_close:.0f} < {floor:.0f}. Inventario liquidado ({pnl:+.2f}). Bot apagado."]
    if not s.active:
        if s.range_low <= last_close <= s.range_high:
            s.days_inside += 1
        else:
            s.days_inside = 0
        if s.days_inside >= C.RESUME_DAYS_INSIDE:
            s.active, s.stopped_at = True, ""
            return [f"reanudado: {C.RESUME_DAYS_INSIDE} cierres dentro del rango"]
    return []


def equity(s: State, price: float) -> float:
    return s.cash + s.coin * price


def _log_fill(ts: str, side: str, price: float, qty: float, fee: float, pnl: float) -> None:
    DATA.mkdir(exist_ok=True)
    with open(FILLS, "a") as f:
        f.write(json.dumps({"ts": ts, "side": side, "price": price, "qty": qty, "fee": round(fee, 4),
                            "pnl": round(pnl, 4)}) + "\n")


def status_text(s: State, price: float) -> str:
    eq = equity(s, price)
    inv_pct = (s.coin * price) / C.CAPITAL_USD if C.CAPITAL_USD else 0
    dist_low = (price - s.range_low) / price if s.range_low else 0
    dist_high = (s.range_high - price) / price if s.range_high else 0
    buys = sum(1 for o in s.orders if o["side"] == "buy")
    sells = sum(1 for o in s.orders if o["side"] == "sell")
    return (f"{C.PAIR_LABEL} {price:,.0f} · {'ACTIVO' if s.active else 'APAGADO'}\n"
            f"equity ${eq:,.2f} ({eq - C.CAPITAL_USD:+.2f}) · realizado ${s.realized_pnl:+.2f} · comisiones ${s.fees_paid:.2f}\n"
            f"vueltas {s.round_trips} · inventario {s.coin:.6f} BTC ({inv_pct:.0%}) costo prom. {s.avg_cost:,.0f}\n"
            f"rango {s.range_low:,.0f}–{s.range_high:,.0f} (borde bajo a {dist_low:.1%}, alto a {dist_high:.1%})\n"
            f"órdenes: {buys} compras / {sells} ventas")
