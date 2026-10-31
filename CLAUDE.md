# Instrucciones para Claude en este repo

- Español en comentarios y mensajes; variables en inglés.
- Fase 1 = papel. No agregar llaves ni órdenes reales hasta que José apruebe la fase 2 por escrito.
- Rutas de Kraken: solo las públicas documentadas (docs.kraken.com). Las privadas llegan en fase 2.
- Parámetros solo en `bot/config.py`.
- Antes de subir: `pytest -q` y `python -m bot.run --dry` en verde.
- No borrar `data/state.json` ni `data/fills.jsonl` (son el historial de la fase 1).
- Cuando José pregunte "cómo va el bot de cripto": leer `data/state.json` + `data/fills.jsonl` y responder con
  equity, P&L realizado neto, vueltas, inventario, distancia a bordes, último evento. Sin recomendaciones no pedidas.
