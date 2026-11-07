# Instrucciones para Claude en este repo

- Español en comentarios y mensajes; variables en inglés.
- Fase 2 iniciada el 7 nov 2026 (José: "vamos a enlazar el bot para comenzar la estrategia").
  Paso 1 = solo lectura (`bot/kraken_private.py`, `--balance`). Órdenes reales solo con `LIVE=true`,
  y LIVE solo se activa cuando José confirme por escrito el capital real a usar.
- Rutas de Kraken: solo las documentadas en docs.kraken.com (públicas y privadas listadas en kraken_private.py).
- Llaves: solo en GitHub Secrets (KRAKEN_API_KEY / KRAKEN_API_SECRET). Nunca en el repo, logs ni chat.
- Parámetros solo en `bot/config.py`.
- Antes de subir: `pytest -q` y `python -m bot.run --dry` en verde.
- No borrar `data/state.json` ni `data/fills.jsonl` (son el historial de la fase 1).
- Cuando José pregunte "cómo va el bot de cripto": leer `data/state.json` + `data/fills.jsonl` y responder con
  equity, P&L realizado neto, vueltas, inventario, distancia a bordes, último evento. Sin recomendaciones no pedidas.
