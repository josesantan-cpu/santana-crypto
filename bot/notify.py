"""Telegram. Sin credenciales, imprime y no falla."""
from __future__ import annotations

import os

import requests


def send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram desactivado]\n" + text)
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text[:3900]}, timeout=20)
    return r.ok
