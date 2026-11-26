"""Grid en vivo contra un Kraken simulado (sin red)."""
from bot import config as C
from bot import kraken, kraken_private as kp, live, signals


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

    def add_order(self, pair, side, volume, price, post_only=True, userref=None, price_decimals=1):
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
    monkeypatch.setattr(signals, "kraken_trade_flow", lambda pair: {"buy_ratio": 0.5})   # tape neutral por defecto
    ohlc = [{"time": i, "open": price, "high": price * 1.03, "low": price * 0.95, "close": price * 0.99, "volume": 1}
            for i in range(60)]
    monkeypatch.setattr(kraken, "daily_ohlc", lambda pair, days=60: ohlc)
    monkeypatch.setattr(kraken, "pair_info", lambda pair: {"ordermin": 0.00005, "pair_decimals": 1, "lot_decimals": 8})
    live.set_pair("XBTUSD", 1)
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
    assert len(sells) == 1 and abs(sells[0]["price"] - round(first["price"] * (1 + C.LEVEL_SPACING_PCT), 1)) < 0.2
    assert s.coin > 0 and s.avg_cost > 0
    # llena la venta → vuelta completa con P&L positivo neto de comisiones
    fk.fill(sells[0]["txid"], sells[0]["price"])
    s, price, ev = live.tick("2026-11-12T17:30:00+00:00", "2026-11-12")
    assert s.round_trips == 1 and s.realized_pnl > 0
    assert any("vuelta #1" in e for e in ev)


def test_orders_blocked_when_live_false(monkeypatch):
    monkeypatch.setenv("LIVE", "false")
    assert kp.live_enabled() is False


def test_stop_switches_to_trx_when_in_range(tmp_path, monkeypatch):
    """BTC cierra bajo el stop → liquida, se apaga y pasa a TRX si TRX está en rango (José, 17 nov 2026)."""
    fk = FakeKraken(usd=50.0)
    _patch(monkeypatch, fk, tmp_path=tmp_path)
    btc_ohlc = [{"time": i, "open": 70000, "high": 72000, "low": 68000, "close": 70000, "volume": 1} for i in range(60)]
    btc_ohlc[-1]["close"] = 60000      # último cierre completo muy por debajo del borde inferior
    trx_ohlc = [{"time": i, "open": 0.31, "high": 0.32, "low": 0.30, "close": 0.31, "volume": 1} for i in range(60)]
    data = {"XBTUSD": btc_ohlc, "TRXUSD": trx_ohlc}
    monkeypatch.setattr(kraken, "daily_ohlc", lambda pair, days=60: data[pair])
    monkeypatch.setattr(kraken, "ticker", lambda pair: {"bid": 0.3099, "ask": 0.3101, "last": 0.31} if pair == "TRXUSD"
                        else {"bid": 59995.0, "ask": 60005.0, "last": 60000.0})
    monkeypatch.setattr(kraken, "pair_info", lambda pair: {"ordermin": 20.0, "pair_decimals": 5, "lot_decimals": 8}
                        if pair == "TRXUSD" else {"ordermin": 0.00005, "pair_decimals": 1, "lot_decimals": 8})
    # estado previo: grid de BTC construido ayer, activo
    s = live.State(active=True, cash=50.0, coin=0.0, range_low=68000.0, range_high=72000.0, range_built="2026-11-16",
                   last_check="2026-11-16T23:45:00+00:00", pair="XBTUSD")
    live.save_state(s)
    s, price, ev = live.tick("2026-11-17T00:05:00+00:00", "2026-11-17")
    assert any("STOP DE RANGO BTC/USD" in e for e in ev)
    assert any("CAMBIO DE PAR: BTC/USD → TRX/USD" in e for e in ev)
    assert s.pair == "TRXUSD" and s.active
    assert s.range_low == 0.30 and s.range_high == 0.32
    buys = [o for o in s.orders if o["side"] == "buy"]
    assert buys and all(len(str(o["price"]).split(".")[-1]) <= 5 for o in buys)   # precios con decimales de TRX
    assert all(o["price"] < 0.31 for o in buys)


def test_no_switch_when_other_pair_out_of_range(tmp_path, monkeypatch):
    fk = FakeKraken(usd=50.0)
    _patch(monkeypatch, fk, tmp_path=tmp_path)
    btc_ohlc = [{"time": i, "open": 70000, "high": 72000, "low": 68000, "close": 70000, "volume": 1} for i in range(60)]
    btc_ohlc[-1]["close"] = 60000
    trx_ohlc = [{"time": i, "open": 0.31, "high": 0.40, "low": 0.30, "close": 0.29, "volume": 1} for i in range(60)]  # ancho 33%, cierre fuera
    data = {"XBTUSD": btc_ohlc, "TRXUSD": trx_ohlc}
    monkeypatch.setattr(kraken, "daily_ohlc", lambda pair, days=60: data[pair])
    s = live.State(active=True, cash=50.0, coin=0.0, range_low=68000.0, range_high=72000.0, range_built="2026-11-16",
                   last_check="2026-11-16T23:45:00+00:00", pair="XBTUSD")
    live.save_state(s)
    s, price, ev = live.tick("2026-11-17T00:05:00+00:00", "2026-11-17")
    assert any("sin par alterno" in e for e in ev)
    assert s.pair == "XBTUSD" and not s.active


def test_trx_stop_switches_to_eth_when_eth_in_range(tmp_path, monkeypatch):
    """Tercer par (José, 21 nov 2026: "Añade a eth"): TRX se apaga, BTC fuera de rango, ETH en rango → pasa a ETH."""
    fk = FakeKraken(usd=50.0)
    _patch(monkeypatch, fk, tmp_path=tmp_path)
    trx_ohlc = [{"time": i, "open": 0.31, "high": 0.32, "low": 0.30, "close": 0.31, "volume": 1} for i in range(60)]
    trx_ohlc[-1]["close"] = 0.27
    btc_ohlc = [{"time": i, "open": 70000, "high": 90000, "low": 60000, "close": 70000, "volume": 1} for i in range(60)]
    eth_ohlc = [{"time": i, "open": 1700, "high": 1800, "low": 1600, "close": 1700, "volume": 1} for i in range(60)]
    data = {"XBTUSD": btc_ohlc, "TRXUSD": trx_ohlc, "ETHUSD": eth_ohlc}
    monkeypatch.setattr(kraken, "daily_ohlc", lambda pair, days=60: data[pair])
    monkeypatch.setattr(kraken, "ticker", lambda pair: {"bid": 1699.5, "ask": 1700.5, "last": 1700.0} if pair == "ETHUSD"
                        else {"bid": 0.2699, "ask": 0.2701, "last": 0.27})
    monkeypatch.setattr(kraken, "pair_info", lambda pair: {"ordermin": 0.002, "pair_decimals": 2, "lot_decimals": 8}
                        if pair == "ETHUSD" else {"ordermin": 20.0, "pair_decimals": 5, "lot_decimals": 8})
    s = live.State(active=True, cash=50.0, coin=0.0, range_low=0.30, range_high=0.32, range_built="2026-11-20",
                   last_check="2026-11-20T23:45:00+00:00", pair="TRXUSD")
    live.save_state(s)
    s, price, ev = live.tick("2026-11-21T00:05:00+00:00", "2026-11-21")
    assert any("CAMBIO DE PAR: TRX/USD → ETH/USD" in e for e in ev)
    assert s.pair == "ETHUSD" and s.active
    buys = [o for o in s.orders if o["side"] == "buy"]
    assert buys and all(o["price"] < 1700 for o in buys)
    assert all(len(str(o["price"]).split(".")[-1]) <= 2 for o in buys)


def test_second_sell_after_inventory_empty_uses_avg_cost(tmp_path, monkeypatch):
    """Dos ventas llenadas en el mismo paso dejan el inventario en 0: la segunda no debe contabilizarse con costo 0."""
    fk = FakeKraken(usd=35.0)
    _patch(monkeypatch, fk, tmp_path=tmp_path)
    live.set_pair("TRXUSD", 5)
    s = live.State(active=True, cash=35.0, coin=0.0, avg_cost=0.3, pair="TRXUSD",
                   orders=[{"side": "sell", "price": 0.31, "qty": 20.0, "level": 1, "txid": "S1"}])
    monkeypatch.setattr(live.kp, "trades_history", lambda start=None: {
        "T1": {"pair": "TRXUSD", "ordertxid": "S1", "vol": "20.0", "cost": "6.2", "fee": "0.02", "type": "sell"}})
    monkeypatch.setattr(live, "place", lambda st, od: "X")
    monkeypatch.setattr(live.kp, "balance", lambda: {"ZUSD": 41.18, "TRX": 0.0})
    live.sync_balances(s)                            # el saldo ya refleja la venta: inventario 0
    assert s.avg_cost == 0.3                         # el costo promedio sigue disponible para contabilizar
    ev = live.process_fills(s, 0, "2026-11-23T16:00:00+00:00")
    assert s.realized_pnl < 0.5                     # (0.31-0.30)*20 = 0.20 menos comisiones, no 6.2
    assert s.avg_cost == 0.0                         # se reinicia después de contabilizar


def test_tape_sell_pressure_blocks_and_resumes_buys(tmp_path, monkeypatch):
    """José, 26 nov 2026: con ≥60% de ventas en el tape no se compra; al normalizarse se reanuda."""
    fk = FakeKraken(usd=50.0)
    _patch(monkeypatch, fk, tmp_path=tmp_path)
    tape = {"buy_ratio": 0.30}
    monkeypatch.setattr(signals, "kraken_trade_flow", lambda pair: dict(tape))
    s, price, ev = live.tick("2026-11-12T17:00:00+00:00", "2026-11-12")
    assert not [o for o in s.orders if o["side"] == "buy"] and not fk.open
    assert s.tape_paused and any("tape vendedor (30% compra)" in e for e in ev)
    # sigue vendedor: no repite el aviso
    s, price, ev = live.tick("2026-11-12T17:15:00+00:00", "2026-11-12")
    assert not any("tape" in e for e in ev)
    # el tape se normaliza: vuelven las compras
    tape["buy_ratio"] = 0.55
    s, price, ev = live.tick("2026-11-12T17:30:00+00:00", "2026-11-12")
    buys = [o for o in s.orders if o["side"] == "buy"]
    assert buys and len(fk.open) == len(buys) and not s.tape_paused
    assert any("compras reanudadas" in e for e in ev)


def test_tape_pause_keeps_sells_and_cancels_waiting_buys(tmp_path, monkeypatch):
    fk = FakeKraken(usd=50.0)
    _patch(monkeypatch, fk, tmp_path=tmp_path)
    s, price, ev = live.tick("2026-11-12T17:00:00+00:00", "2026-11-12")
    buys = [o for o in s.orders if o["side"] == "buy"]
    fk.fill(buys[0]["txid"], buys[0]["price"])          # una compra se llena → aparece su venta
    monkeypatch.setattr(signals, "kraken_trade_flow", lambda pair: {"buy_ratio": 0.35})
    s, price, ev = live.tick("2026-11-12T17:15:00+00:00", "2026-11-12")
    assert [o["side"] for o in s.orders] == ["sell"]
    assert all(o["descr"]["type"] == "sell" for o in fk.open.values())
    # la venta se llena con el tape vendedor: se contabiliza, pero no se recoloca la compra
    sell = s.orders[0]
    fk.fill(sell["txid"], sell["price"])
    s, price, ev = live.tick("2026-11-12T17:30:00+00:00", "2026-11-12")
    assert s.round_trips == 1 and not s.orders and not fk.open


def test_tape_error_does_not_block(tmp_path, monkeypatch):
    fk = FakeKraken(usd=50.0)
    _patch(monkeypatch, fk, tmp_path=tmp_path)
    def boom(pair):
        raise RuntimeError("sin red")
    monkeypatch.setattr(signals, "kraken_trade_flow", boom)
    s, price, ev = live.tick("2026-11-12T17:00:00+00:00", "2026-11-12")
    assert [o for o in s.orders if o["side"] == "buy"]


def test_all_inventory_gets_a_sell_order_when_range_is_narrow():
    """Con 2% de distancia y rango estrecho (TRX), todo el inventario debe quedar a la venta."""
    live.set_pair("TRXUSD", 6)
    s = live.State(active=True, cash=35.48, coin=41.03513592, avg_cost=0.304617,
                   range_low=0.298277, range_high=0.315421, pair="TRXUSD")
    ohlc = [{"time": i, "open": 0.30, "high": 0.31, "low": 0.298, "close": 0.30, "volume": 1} for i in range(60)]
    plan = live.plan_grid(s, 0.303762, ohlc, ordermin=10.0)
    sells = [o for o in plan if o["side"] == "sell"]
    assert sells and abs(sum(o["qty"] for o in sells) - s.coin) < 1e-6
    assert all(o["price"] >= round(0.303762 * (1 + C.LEVEL_SPACING_PCT), 6) for o in sells)
    live.set_pair("XBTUSD", 1)
