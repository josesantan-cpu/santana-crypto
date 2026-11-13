"""Una corrida del grid (cada 15 min por GitHub Actions).

  python -m bot.run            corrida normal (papel)
  python -m bot.run --status   solo imprime el estado (para "cómo va el bot")
  python -m bot.run --dry      valida que el código carga, sin red
  python -m bot.run --balance  fase 2 (solo lectura): lee el saldo de Kraken con las llaves del entorno
                               y lo manda por Telegram. No coloca órdenes.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from . import config as C
from . import grid, kraken, kraken_private, live, notify


def main(argv: list[str]) -> int:
    if "--dry" in argv:
        s = grid.State()
        print("dry run OK —", grid.status_text(s, 100000.0).splitlines()[0])
        return 0
    if "--balance" in argv:
        try:
            text = kraken_private.balance_text()
        except kraken_private.KrakenAuthError as e:
            text = f"Kraken: no pude autenticar ({e}). Revisa los secrets KRAKEN_API_KEY / KRAKEN_API_SECRET."
        print(text)
        notify.send("Santana crypto\n" + text)
        return 0
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    # Fase 2: órdenes reales en Kraken cuando LIVE=true (variable del repo). El papel sigue aparte.
    if kraken_private.live_enabled():
        if "--status" in argv:
            ls = live.load_state()
            print(live.status_text(ls, ls.last_price or 0.0))
            return 0
        ls, price, events = live.tick(now.isoformat(), today)
        status = live.status_text(ls, price)
        print(status)
        if events:
            print("\n".join(events))
        notable = [e for e in events if "vuelta" in e or "STOP" in e or "reanudado" in e or "LIVE activo" in e
                   or "órdenes reales" in e or "ERROR" in e or "adoptada" in e]
        if notable:
            notify.send("Santana crypto LIVE\n" + "\n".join(notable) + "\n\n" + status)
        return 0

    s = grid.load_state()
    if "--status" in argv:
        print(grid.status_text(s, s.last_price or 0.0))
        return 0
    t = kraken.ticker(C.PAIR)
    ohlc = kraken.daily_ohlc(C.PAIR, 60)
    price = t["last"]
    events: list[str] = []

    # 1. stop de rango / reanudación (una vez por día, con el cierre diario)
    if s.range_low and s.range_built != today:
        events += grid.range_stop_check(s, ohlc, today)

    # 2. rango nuevo (primera vez o cambio de día) si el bot está activo
    if s.active and (not s.range_low or s.range_built != today):
        events += grid.build_range(s, ohlc, price, today)

    # 3. llenados simulados
    if s.active:
        events += grid.process_fills(s, t["bid"], t["ask"], now.isoformat())

    s.last_price, s.last_check = price, now.isoformat()
    for e in events:
        s.log.append(f"{now:%m-%d %H:%M} {e}")
    grid.save_state(s)

    status = grid.status_text(s, price)
    print(status)
    if events:
        print("\n".join(events))
    # Telegram solo con eventos (vueltas, stop, reanudación), no cada 15 min
    notable = [e for e in events if "vuelta" in e or "STOP" in e or "reanudado" in e]
    if notable:
        notify.send("Santana crypto (papel)\n" + "\n".join(notable) + "\n\n" + status)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
