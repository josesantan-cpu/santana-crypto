# Instrucciones para Claude en este repo

- Español en comentarios y mensajes; variables en inglés.
- Fase 2 iniciada el 7 nov 2026 (José: "vamos a enlazar el bot para comenzar la estrategia").
  Paso 1 = solo lectura (`bot/kraken_private.py`, `--balance`).
  Paso 2 (12 nov 2026) = órdenes reales en `bot/live.py`, aprobadas por José por escrito:
  "Apruebo LIVE con $50 en BTC/USD". Capital real en `LIVE_CAPITAL_USD`; se activa con la variable del
  repo `LIVE=true`. Subir el capital o cambiar el par requiere nueva aprobación escrita.
  12 nov 2026: José aprobó la opción B (`LIVE_IGNORE_TREND = True`): la prueba de $50 compra aunque BTC esté
  bajo la SMA50. Reactivar el filtro (False) cuando BTC cruce la SMA50 o antes de subir el capital.
  13 nov 2026: José aprobó RESUME_DAYS_INSIDE = 1 (reanudar tras un cierre dentro del rango). Volver a 2 al subir capital.
  14 nov 2026: José aprobó la fase de observación de señales de flujo (`bot/signals.py`): Kraken Trades/Depth +
  Unusual Whales ballenas cripto (opcional, secreto `UW_API_KEY`). SOLO REGISTRO en `data/signals.jsonl`; no afecta
  órdenes. Pasar a filtro activo de compras requiere nueva aprobación escrita tras ~1 semana de datos.
  17 nov 2026: José aprobó dos pares LIVE con el mismo capital, uno a la vez (`LIVE_PAIRS`: BTC/USD y TRX/USD):
  "Configura el bot para que haga trades con TRX o BTC, así si una se sale de rango no tengo que esperar a que vuelva".
  Al apagarse un par por stop, `live.try_switch` pasa al otro si su rango de 14 días mide ≤ 20% y el último cierre
  cayó dentro. Agregar un tercer par requiere nueva aprobación escrita. El par activo vive en `live_state.json["pair"]`.
  21 nov 2026: José aprobó un tercer par alterno: "Añade a eth" (ETH/USD en `LIVE_PAIRS`). Mismo capital, un par a la vez;
  ETH solo entra vía `try_switch` si califica. Un cuarto par requiere nueva aprobación escrita.
  26 nov 2026: José pidió usar el tape como filtro: "Lee el tape antes de cada entrada y salida, utiliza ese indicador para
  que el bot sepa cuando entrar o salir dependiendo del volumen de compra y venta". Implementado solo para ENTRADAS
  (`TAPE_FILTER`, `TAPE_SELL_PRESSURE = 0.40` en config): con ≥60% de ventas en los últimos ~1000 trades no se compra y se
  retiran las compras que esperan. Las salidas no se tocan (los datos 14–26 nov no lo justifican).
  26 nov 2026: José aprobó "Corrige esos últimos dos": `LEVEL_SPACING_PCT = 0.02` (antes 0.01) y comisión real
  `FEE_MAKER = FEE_TAKER = 0.004` (antes 0.0016/0.0026). El filtro de tendencia (`LIVE_IGNORE_TREND`) sigue igual;
  cambiarlo requiere aprobación escrita.
- Estado vivo en `data/live_state.json` / `data/live_fills.jsonl`; el papel sigue en `data/state.json`.
  Al preguntar "cómo va el bot de cripto" con LIVE activo, reportar el estado LIVE.
- Rutas de Kraken: solo las documentadas en docs.kraken.com (públicas y privadas listadas en kraken_private.py).
- Llaves: solo en GitHub Secrets (KRAKEN_API_KEY / KRAKEN_API_SECRET). Nunca en el repo, logs ni chat.
- Parámetros solo en `bot/config.py`.
- Antes de subir: `pytest -q` y `python -m bot.run --dry` en verde.
- No borrar `data/state.json` ni `data/fills.jsonl` (son el historial de la fase 1).
- Cuando José pregunte "cómo va el bot de cripto": leer `data/state.json` + `data/fills.jsonl` y responder con
  equity, P&L realizado neto, vueltas, inventario, distancia a bordes, último evento. Sin recomendaciones no pedidas.
