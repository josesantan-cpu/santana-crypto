"""Firma de Kraken: vector de prueba oficial de docs.kraken.com (Authentication → Example)."""
import os

import pytest

from bot import kraken_private as kp


def test_signature_official_vector():
    secret = ("kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==")
    data = {"nonce": "1616492376594", "ordertype": "limit", "pair": "XBTUSD", "price": 37500,
            "type": "buy", "volume": 1.25}
    sig = kp._sign("/0/private/AddOrder", data, secret)
    assert sig == ("4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ==")


def test_orders_blocked_without_live(monkeypatch):
    monkeypatch.delenv("LIVE", raising=False)
    monkeypatch.setenv("KRAKEN_API_KEY", "x")
    monkeypatch.setenv("KRAKEN_API_SECRET", "eA==")
    with pytest.raises(PermissionError):
        kp.add_order("XBTUSD", "buy", 0.0002, 70000.0)
    with pytest.raises(PermissionError):
        kp.cancel_order("ABC")


def test_missing_keys(monkeypatch):
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    with pytest.raises(kp.KrakenAuthError):
        kp.balance()
