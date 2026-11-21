"""Parámetros del grid bot (Santana Capital — cripto).

Fase 1 = papel: precios reales de Kraken, órdenes simuladas, comisiones descontadas.
Todo lo que José quiera cambiar vive aquí.
"""

PAIR = "XBTUSD"                 # par en Kraken (BTC/USD). Un solo par en fase 1.
PAIR_LABEL = "BTC/USD"

# Capital y tamaño
CAPITAL_USD = 500.0             # capital asignado al grid (papel)
MAX_INVENTORY_PCT = 0.50        # nunca más del 50% del capital en moneda
N_LEVELS = 10                   # niveles de compra bajo el precio + niveles de venta arriba
LEVEL_SPACING_PCT = 0.01        # 1% entre niveles (vuelta neta ≈ 1% − comisiones)

# Comisiones (Kraken spot, maker/taker nivel base). Se descuentan en papel igual que en vivo.
FEE_MAKER = 0.0016
FEE_TAKER = 0.0026
USE_MAKER = True                # órdenes límite que esperan → maker

# Rango adaptativo
RANGE_LOOKBACK_DAYS = 14        # grid = [mínimo, máximo] de los últimos 14 días
RANGE_REBUILD_DAYS = 1          # se recalcula el rango una vez al día (00:00 UTC)
TREND_SMA_DAYS = 50             # bajo la SMA50 solo vende inventario, no acumula

# Stop de rango (apagado)
RANGE_STOP_PCT = 0.02           # cierre diario 2% por debajo del borde inferior → vende todo y se apaga
RESUME_DAYS_INSIDE = 1    # José, 13 nov 2026 ("vamos por el B"): 1 cierre diario dentro del rango reanuda. Volver a 2 al subir el capital.

# Fase 2 (dinero real). Se activa con la variable de entorno LIVE=true (variable del repo en GitHub).
LIVE_CAPITAL_USD = 50.0         # capital real aprobado por José (12 nov 2026): "Apruebo LIVE con $50 en BTC/USD"
LIVE_USERREF = 20261112         # marca de nuestras órdenes en Kraken (userref); el bot solo toca órdenes con esta marca
LIVE_MIN_ORDER_BTC = 0.00005    # mínimo de Kraken para XBT (se confirma en vivo con AssetPairs)

# Pares LIVE (José, 17 nov 2026: "Configura el bot para que haga trades con TRX o BTC, así si una se sale de rango
# no tengo que esperar a que vuelva ya que continuamos con la otra"). UN par a la vez con los mismos $50:
# cuando el par activo se apaga por stop de rango, el bot pasa a otro si está en rango (el de rango más estrecho).
LIVE_PAIRS = {
    "XBTUSD": {"label": "BTC/USD", "assets": ("XXBT", "XBT"), "names": ("XBTUSD", "XXBTZUSD"), "min_order": 0.00005, "decimals": 1},
    "TRXUSD": {"label": "TRX/USD", "assets": ("TRX",), "names": ("TRXUSD", "TRXZUSD"), "min_order": 0.0, "decimals": 5},
    # José, 21 nov 2026: "Añade a eth". Tercer par alterno; solo entra si califica (rango 14d ≤ 20% y cierre dentro).
    "ETHUSD": {"label": "ETH/USD", "assets": ("XETH", "ETH"), "names": ("ETHUSD", "XETHZUSD"), "min_order": 0.002, "decimals": 2},
}
LIVE_DEFAULT_PAIR = "XBTUSD"
SWITCH_MAX_RANGE_PCT = 0.20     # el par alterno califica si su rango de 14 días mide ≤ 20% (alto−bajo)/bajo
SWITCH_MIN_DAYS_INSIDE = 1      # y su último cierre diario está dentro de ese rango
LIVE_IGNORE_TREND = True        # prueba de tubería con $50 (José, 12 nov 2026: "me gusta la B, apruébalo"):
                                # el modo LIVE coloca compras aunque BTC esté bajo la SMA50. El papel conserva
                                # el filtro. Volver a False cuando BTC cruce la SMA50 o al subir el capital.

# Operación
CHECK_INTERVAL_MIN = 15         # frecuencia de la corrida (GitHub Actions)
MIN_ORDER_USD = 10.0            # Kraken no acepta órdenes menores (~0.0001 BTC)
