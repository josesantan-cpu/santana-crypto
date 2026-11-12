"""Grid en vivo contra un Kraken simulado (sin red)."""
from bot import config as C
from bot import kraken, kraken_private as kp, live


class FakeKraken:
    def __init__(self, usd=50.0, btc=0.0):
        self.bal = {"ZUSD": usd, "XXBT": btc}
        self.open = {}
        self.trades = {}
        self.n = 0

    def balance(self):
        return {k: v for k, v in self.bal.items() if v}

    def open_orders(self):
        return dict(self.open)

    def trades_history(self, start=None):
        return dict(self.trades)

    def add_order(self, pair, side, volume, price, post_only=True, userref=None):
        self.n += 1
        txid = f"T{self.n}"
        self.open[txid] = {"userref": userref, "descr": {"type": side, "price": str(price)}, "vol": str(volume)}
        return {"txid": [txid]}

    def cancel_order(self, txid):
        self.open.pop(txid, None)
        return {"count": 1}

    def market_sell(self, pair, volume, userref=None):
        self.bal["XXBT"] -= volume
        self.bal["ZUSD"] += volume * 70000
        return {"txid": ["M1"]}

    def fill(self, txid, price):
        o = self.open.pop(txid)
        vol = float(o["vol"])
        cost = vol * price
        fee = cost * C.FEE_MAKER
        if o["descr"]["type"] == "buy":
            self.bal["ZUSD"] -= cost + fee
            self.bal["XXBT"] += vol
        else:
            self.bal["ZUSD"] += cost - fee
            self.bal["XXBT"] -= vol
        self.trades[f"TR{txid}"] = {"ordertxid": txid, "pair": "XXBTZUSD", "type": o["descr"]["type"],
                                     "vol": str(vol), "cost": str(cost), "fee": str(fee)}


def _patch(monkeypatch, fk, price=73000.0, tmp_path=None):
    monkeypatch.setenv("LIVE", "true")
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "eA==")
    for name in ("balance", "open_orders", "trades_history", "add_order", "cancel_order", "market_sell"):
        monkeypatch.setattr(kp, name, getattr(fk, name))
    monkeypatch.setattr(kraken, "ticker", lambda pair: {"bid": price - 5, "ask": price + 5, "last": price})
    ohlc = [{"time": i, "open": price, "high": price * 1.03, "low": price * 0.95, "close": price * 0.99, "volume": 1}
            for i in range(60)]
    monkeypatch.setattr(kraken, "daily_ohlc", lambda pair, days=60: ohlc)
    monkeypatch.setattr(kraken, "pair_info", lambda pair: {"ordermin": 0.00005, "pair_decimals": 1, "lot_decimals": 8})
    monkeypatch.setattr(live, "DATA", tmp_path)
    monkeypatch.setattr(live, "STATE", tmp_path / "live_state.json")
    monkeypatch.setattr(live, "FILLS", tmp_path / "live_fills.jsonl")


def test_level_plan_respects_min_order():
    n, per = live.level_plan(73000.0, 0.00005)
    assert 1 <= n <= C.N_LEVELS
    assert per >= 0.00005 * 73000.0


def test_first_tick_places_buys_and_round_trip(tmp_path, monkeypatch):
    fk = FakeKraken(usd=50.0)
    _patch(monkeypatch, fk, tmp_path=tmp_path)
    s, price, ev = live.tick("2026-11-12T17:00:00+00:00", "2026-11-12")
    assert any("LIVE activo" in e for e in ev)
    buys = [o for o in s.orders if o["side"] == "buy"]
    assert buys and all(o["txid"] for o in buys)
    assert len(fk.open) == len(buys)
    # llena la primera compra → debe aparecer una venta un nivel arriba
    first = buys[0]
    fk.fill(first["txid"], first["price"])
    s, price, ev = live.tick("2026-11-12T17:15:00+00:00", "2026-11-12")
    sells = [o for o in s.orders if o["side"] == "sell"]
    assert len(sells) == 1 and abs(sells[0]["price"] - round(first["price"] * 1.01, 1)) < 0.2
    assert s.coin > 0 and s.avg_cost > 0
    # llena la venta → vuelta completa con P&L positivo neto de comisiones
    fk.fill(sells[0]["txid"], sells[0]["price"])
    s, price, ev = live.tick("2026-11-12T17:30:00+00:00", "2026-11-12")
    assert s.round_trips == 1 and s.realized_pnl > 0
    assert any("vuelta #1" in e for e in ev)


def test_orders_blocked_when_live_false(monkeypatch):
    monkeypatch.setenv("LIVE", "false")
    assert kp.live_enabled() is False
