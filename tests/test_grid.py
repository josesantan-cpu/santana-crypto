"""Prueba sin red: simula un mercado lateral y uno que rompe el rango."""
import random

from bot import config as C
from bot import grid


def ohlc_flat(n=60, base=100000.0):
    out = []
    for i in range(n):
        o = base * (1 + 0.004 * ((i % 7) - 3))
        out.append({"time": i, "open": o, "high": o * 1.02, "low": o * 0.98, "close": o, "volume": 1})
    return out


def test_lateral_market(tmp_path, monkeypatch):
    monkeypatch.setattr(grid, "DATA", tmp_path)
    monkeypatch.setattr(grid, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(grid, "FILLS", tmp_path / "fills.jsonl")
    s = grid.State()
    ohlc = ohlc_flat()
    price = 100000.0
    grid.build_range(s, ohlc, price, "2026-11-01")
    assert s.orders and all(o["side"] == "buy" for o in s.orders)
    random.seed(1)
    # oscila ±3% durante 400 ticks
    for k in range(400):
        p = price * (1 + 0.03 * random.uniform(-1, 1))
        grid.process_fills(s, p - 5, p + 5, f"t{k}")
    assert s.round_trips > 0
    assert s.coin * price <= C.CAPITAL_USD * C.MAX_INVENTORY_PCT + 1
    assert s.fees_paid > 0
    eq = grid.equity(s, price)
    print(grid.status_text(s, price))
    assert abs(eq - C.CAPITAL_USD - s.realized_pnl - (s.coin * (price - s.avg_cost))) < 5


def test_range_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(grid, "DATA", tmp_path)
    monkeypatch.setattr(grid, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(grid, "FILLS", tmp_path / "fills.jsonl")
    s = grid.State()
    ohlc = ohlc_flat()
    grid.build_range(s, ohlc, 100000.0, "2026-11-01")
    grid.process_fills(s, 96000, 96010, "t")   # llena varias compras
    assert s.coin > 0
    crash = ohlc + [{"time": 99, "open": 90000, "high": 90000, "low": 85000, "close": 85000, "volume": 1}]
    ev = grid.range_stop_check(s, crash, "2026-11-02")
    assert not s.active and s.coin == 0 and "STOP" in ev[0]
    # dos cierres dentro del rango → reanuda
    inside = crash + [{"time": 100, "open": 99000, "high": 99500, "low": 98500, "close": 99000, "volume": 1}]
    grid.range_stop_check(s, inside, "2026-11-03")
    grid.range_stop_check(s, inside, "2026-11-04")
    assert s.active
