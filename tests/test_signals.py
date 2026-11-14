"""Pruebas del módulo de señales (sin red): formato de Kraken Trades/Depth y veredicto."""
from __future__ import annotations

import json

from bot import signals


def _fake_get(path, params):
    if path == "Trades":
        # [price, volume, time, side, type, misc, id]
        rows = [["68000.0", "2.0", 1700000000.0, "b", "m", "", 1],
                ["68001.0", "0.5", 1700000060.0, "b", "l", "", 2],
                ["67990.0", "0.3", 1700000120.0, "s", "m", "", 3]]
        return {"XXBTZUSD": rows, "last": "1"}
    if path == "Depth":
        return {"XXBTZUSD": {"bids": [["67900.0", "10.0", 1], ["67000.0", "50.0", 1], ["60000.0", "999", 1]],
                             "asks": [["68100.0", "3.0", 1], ["69000.0", "4.0", 1], ["80000.0", "999", 1]]}}
    raise AssertionError(path)


def test_flow_and_walls(monkeypatch, tmp_path):
    monkeypatch.setattr(signals.kraken, "_get", _fake_get)
    monkeypatch.delenv("UW_API_KEY", raising=False)
    monkeypatch.setattr(signals, "DATA", tmp_path)
    monkeypatch.setattr(signals, "SIGNALS_FILE", tmp_path / "signals.jsonl")

    snap = signals.record("XBTUSD", 68000.0)
    assert snap["flow"]["buy_btc"] == 2.5 and snap["flow"]["sell_btc"] == 0.3
    assert snap["flow"]["big_buy"] == 1 and snap["flow"]["big_sell"] == 0
    assert snap["flow"]["buy_ratio"] > 0.8
    # banda ±2% de 68000 = 66640–69360: el bid de 60000 y el ask de 80000 quedan fuera
    assert snap["walls"]["bid_btc_2pct"] == 60.0 and snap["walls"]["ask_btc_2pct"] == 7.0
    assert snap["walls"]["bid_wall"]["price"] == 67000.0
    assert snap["uw"] == {"available": False, "reason": "sin UW_API_KEY"}
    assert snap["verdict"] == "compradores"
    line = (tmp_path / "signals.jsonl").read_text().strip()
    assert json.loads(line)["price"] == 68000.0
    assert "compradores" in signals.summary_line(snap)


def test_source_error_does_not_break(monkeypatch, tmp_path):
    def boom(path, params):
        raise RuntimeError("Kraken caído")
    monkeypatch.setattr(signals.kraken, "_get", boom)
    monkeypatch.delenv("UW_API_KEY", raising=False)
    monkeypatch.setattr(signals, "DATA", tmp_path)
    monkeypatch.setattr(signals, "SIGNALS_FILE", tmp_path / "signals.jsonl")
    snap = signals.record("XBTUSD", 68000.0)
    assert "error" in snap["flow"] and "error" in snap["walls"]
    assert snap["verdict"] == "neutral"
