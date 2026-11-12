# santana-crypto

Grid bot de cripto para Santana Capital. **Fase 1: papel** (precios reales de Kraken, órdenes simuladas,
comisiones descontadas). No necesita cuenta ni llaves; corre en GitHub Actions.

**Hosting:** GitHub no respeta un cron de 15 min en repo privado (corrió 3–4 veces/día). El workflow ahora
corre sesiones encadenadas (`LOOP_MINUTES`, `POLL_SECONDS` como variables del repo). Con `LOOP_MINUTES=0`
(valor inicial) es un tick por corrida; con 350 es ~6 h continuas que se relanzan solas. Repo privado =
2,000 min/mes (≈33 h de bot); repo público = ilimitado. Para dinero real: VPS.

## Qué hace

- Par: BTC/USD (Kraken). Capital simulado: $500.
- **Rango adaptativo**: mínimo–máximo de los últimos 14 días, recalculado cada día.
- **Grid**: 10 niveles de compra bajo el precio separados 1%; cada compra llena crea una venta 1% más
  arriba (una "vuelta" = compra + venta = ~1% menos comisiones 0.32%).
- **Filtro de tendencia**: bajo la SMA de 50 días no acumula (solo vende lo que tiene).
- **Inventario tope**: nunca más del 50% del capital en BTC.
- **Stop de rango**: cierre diario 2% bajo el borde inferior → liquida y se apaga; reanuda tras 2 cierres
  dentro del rango.
- Telegram solo con eventos: vuelta completada, stop, reanudación.

Estado en `data/state.json`, cada llenado en `data/fills.jsonl`. Para preguntar "cómo va": Claude lee esos
archivos y responde con equity, vueltas, P&L neto, inventario y distancia a los bordes.

## Fase 2 (en curso)

1. **Conexión solo lectura** (hecho en código): llaves en Secrets, `python -m bot.run --balance` o workflow
   `balance` (manual) lee saldo y órdenes abiertas y lo manda por Telegram. Sin órdenes.
2. **Órdenes reales** (`bot/live.py`): variable del repo `LIVE=true`. Capital aprobado: US$50 (12 nov 2026).
   Órdenes límite `post_only` (maker) marcadas con `userref`; el bot solo toca las suyas. Niveles ajustados al
   mínimo de Kraken (0.00005 BTC). Stop de rango = cancelar todo + venta a mercado. Estado en
   `data/live_state.json`, llenados en `data/live_fills.jsonl`. El papel sigue corriendo si LIVE no está.

## Fases

1. Papel 3 semanas → medir: vueltas/semana, P&L neto de comisiones, drawdown, cuántas veces saltó el stop.
2. Si es positivo: cuenta Kraken, llaves API **sin permiso de retiro**, $300–500 reales, VPS.
3. Escalar solo con resultados de la fase 2.

## Parámetros

Todos en `bot/config.py`.

## Pruebas

`pytest -q` (sin red). `python -m bot.run --dry` valida que carga.
