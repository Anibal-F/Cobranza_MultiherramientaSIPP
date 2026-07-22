"""Repository de BigQuery del panel 'Cobranza' (mitad derecha de la
sub-pestaña Proyección): lo efectivamente cobrado en el periodo seleccionado,
sobre `Tableros.IgresosClientes`.

Reglas de negocio (pedidas directamente, no vía el Config_Filtros de Excel
que usa el resto del Dashboard Ingresos):

- Se excluyen registros cuya Razón Social (de_RazonSocial) sea exactamente
  'Abastecedora de Combustibles del Pacifico', 'ACP Combustibles' o
  'Petro Smart Combustibles'.
- Los registros cuya Razón Social EMPIECE CON 'Petroplazas' (en los datos
  aparecen variantes: PETROPLAZAS, PETROPLAZAS AEROPUERTO, PETROPLAZAS
  ESTACIONES) se reclasifican como segmento 'Petroplazas', sin importar su
  nb_TipoDeNegocio original.
- Se excluye nb_TipoDeNegocio = 'GasPetroil' exactamente — pero DESPUÉS de
  la reclasificación de Petroplazas, así que un registro de Petroplazas que
  originalmente traía GasPetroil sigue contando como Petroplazas, no se
  descarta.
- Se excluyen registros cuya Sucursal (nb_sucursal) CONTENGA 'GAS',
  'AUTOTANQUE', 'GC' o 'Corporativo' (insensible a mayúsculas).
- Los registros en dólares (nb_Moneda = 'Dolar (USD)') se mantienen separados
  de los pesos originales y se convierten a MXN con el promedio diario de
  `DocumentosClientesCobranza.im_TipoCambio`. Si la fecha exacta no tiene tipo
  de cambio, se usa la fecha disponible más cercana, igual que en Dashboard
  Ingresos.
- La columna Movimiento (im_Movimiento) es la que se suma, agrupada por
  segmento, filtrada por fh_Envio dentro del rango seleccionado.
"""

from datetime import date

from google.cloud import bigquery

from .bigquery_cliente import cliente_bigquery

TABLA = "sipp-app.Tableros.IgresosClientes"
TABLA_COBRANZA_FX = "sipp-app.Tableros.DocumentosClientesCobranza"

RAZON_SOCIAL_EXCLUIDA = [
    "ABASTECEDORA DE COMBUSTIBLES DEL PACIFICO",
    "ACP COMBUSTIBLES",
    "PETRO SMART COMBUSTIBLES",
]
SUCURSAL_EXCLUIDA_CONTIENE = ["gas", "autotanque", "gc", "corporativo"]
MONEDA_USD = "dolar (usd)"

SEGMENTOS = ["Distribuidora", "Asociados", "Petroplazas"]

_SEGMENTO_POR_FILA = """CASE
        WHEN STARTS_WITH(UPPER(TRIM(de_RazonSocial)), 'PETROPLAZAS') THEN 'Petroplazas'
        WHEN nb_TipoDeNegocio = 'Distribuidora' THEN 'Distribuidora'
        WHEN nb_TipoDeNegocio = 'Asociados' THEN 'Asociados'
    END"""

_CTE_FX_DIARIO = f"""fx_diario AS (
        SELECT DATE(fh_Deposito_Mostrar) AS fecha, AVG(im_TipoCambio) AS tipo_cambio
        FROM `{TABLA_COBRANZA_FX}`
        WHERE nb_TipoMoneda = 'Dolar (USD)'
        GROUP BY fecha
    )"""

_CTE_FX_CERCANO = """fx_cercano AS (
        SELECT
            f.fecha,
            fx.tipo_cambio,
            ROW_NUMBER() OVER (
                PARTITION BY f.fecha ORDER BY ABS(DATE_DIFF(f.fecha, fx.fecha, DAY)) ASC
            ) AS rn
        FROM (
            SELECT DISTINCT fecha
            FROM filas
            WHERE LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd
        ) AS f
        CROSS JOIN fx_diario AS fx
    )"""


class CobranzaSemanalRepository:
    """Punto único de acceso a BigQuery para el panel 'Cobranza' de la
    sub-pestaña Proyección.

    `tabla` es inyectable para pruebas o para apuntar a otra fuente sin tocar el
    resto del código.
    """

    def __init__(self, tabla: str = TABLA) -> None:
        self._cliente = cliente_bigquery()  # comparte el singleton del módulo cliente
        self._tabla = tabla

    def _parametros(self, fecha_inicio: date, fecha_fin: date) -> list:
        parametros = [
            bigquery.ArrayQueryParameter("razon_social_excluida", "STRING", RAZON_SOCIAL_EXCLUIDA),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            bigquery.ScalarQueryParameter("moneda_usd", "STRING", MONEDA_USD),
        ]
        for i, palabra in enumerate(SUCURSAL_EXCLUIDA_CONTIENE):
            parametros.append(bigquery.ScalarQueryParameter(f"sucursal_excluida_{i}", "STRING", f"%{palabra}%"))
        return parametros

    def _condiciones_sucursal(self) -> str:
        return " AND ".join(
            f"NOT LOWER(IFNULL(nb_sucursal, '')) LIKE @sucursal_excluida_{i}"
            for i in range(len(SUCURSAL_EXCLUIDA_CONTIENE))
        )

    def _parametros_segmentos(self, segmentos: list[str] | None) -> list:
        """`segmentos` vacío o None = sin filtro (los tres segmentos). Se manda
        siempre un booleano + un array (en vez de un solo STRING nullable) para
        soportar selección múltiple sin tener que armar el SQL condicionalmente."""
        valores = segmentos or []
        return [
            bigquery.ScalarQueryParameter("filtrar_segmento", "BOOL", bool(valores)),
            bigquery.ArrayQueryParameter("segmentos", "STRING", valores),
        ]

    def cobranza_por_segmento(self, fecha_inicio: date, fecha_fin: date) -> list[dict]:
        """Total cobrado (im_Movimiento) por segmento en [fecha_inicio, fecha_fin]
        (sobre fh_Envio), separando USD, su conversión y el total final en MXN."""
        query = f"""
            WITH {_CTE_FX_DIARIO},
            filas AS (
                SELECT
                    {_SEGMENTO_POR_FILA} AS segmento,
                    DATE(fh_Envio) AS fecha,
                    im_Movimiento,
                    nb_Moneda
                FROM `{self._tabla}`
                WHERE UPPER(TRIM(de_RazonSocial)) NOT IN UNNEST(@razon_social_excluida)
                  AND {self._condiciones_sucursal()}
                  AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
            ),
            {_CTE_FX_CERCANO}
            SELECT
                segmento,
                SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) != @moneda_usd THEN im_Movimiento ELSE 0 END) AS total_mxn,
                SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd THEN im_Movimiento ELSE 0 END) AS total_usd,
                SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd AND fxc.tipo_cambio IS NOT NULL
                         THEN im_Movimiento * fxc.tipo_cambio ELSE 0 END) AS total_usd_convertido,
                SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd AND fxc.tipo_cambio IS NULL
                         THEN im_Movimiento ELSE 0 END) AS total_usd_sin_tc
            FROM filas
            LEFT JOIN fx_cercano fxc ON fxc.fecha = filas.fecha AND fxc.rn = 1
            WHERE segmento IS NOT NULL
            GROUP BY segmento
        """
        job_config = bigquery.QueryJobConfig(query_parameters=self._parametros(fecha_inicio, fecha_fin))
        filas = self._cliente.query(query, job_config=job_config).result()
        return [dict(fila.items()) for fila in filas]

    def ingresos_significativos(
        self, fecha_inicio: date, fecha_fin: date, segmentos: list[str] | None = None
    ) -> list[dict]:
        """Top 20 de ingresos agregados por razón social y ordenados por su total
        final en MXN. `segmentos` vacío o None incluye los tres segmentos; con uno
        o varios valores de SEGMENTOS, solo agrega los de esos tipos de negocio."""
        query = f"""
            WITH {_CTE_FX_DIARIO},
            filas AS (
                SELECT
                    {_SEGMENTO_POR_FILA} AS segmento,
                    TRIM(de_RazonSocial) AS razon_social,
                    DATE(fh_Envio) AS fecha,
                    im_Movimiento,
                    nb_Moneda
                FROM `{self._tabla}`
                WHERE UPPER(TRIM(de_RazonSocial)) NOT IN UNNEST(@razon_social_excluida)
                  AND {self._condiciones_sucursal()}
                  AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
            ),
            {_CTE_FX_CERCANO},
            agregados AS (
                SELECT
                    razon_social,
                    STRING_AGG(DISTINCT segmento, ', ' ORDER BY segmento) AS segmento,
                    SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) != @moneda_usd
                             THEN im_Movimiento ELSE 0 END) AS total_mxn,
                    SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd
                             THEN im_Movimiento ELSE 0 END) AS total_usd,
                    SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd
                                  AND fxc.tipo_cambio IS NOT NULL
                             THEN im_Movimiento * fxc.tipo_cambio ELSE 0 END) AS total_usd_convertido,
                    SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd
                                  AND fxc.tipo_cambio IS NULL
                             THEN im_Movimiento ELSE 0 END) AS total_usd_sin_tc
                FROM filas
                LEFT JOIN fx_cercano fxc ON fxc.fecha = filas.fecha AND fxc.rn = 1
                WHERE segmento IS NOT NULL
                  AND (NOT @filtrar_segmento OR segmento IN UNNEST(@segmentos))
                  AND razon_social IS NOT NULL
                  AND razon_social != ''
                GROUP BY razon_social
            )
            SELECT *, total_mxn + total_usd_convertido AS total_final
            FROM agregados
            ORDER BY total_final DESC
            LIMIT 20
        """
        parametros = self._parametros(fecha_inicio, fecha_fin) + self._parametros_segmentos(segmentos)
        filas = self._cliente.query(
            query,
            job_config=bigquery.QueryJobConfig(query_parameters=parametros),
        ).result()
        return [dict(fila.items()) for fila in filas]

    def ingresos_por_dia(
        self, fecha_inicio: date, fecha_fin: date, segmentos: list[str] | None = None
    ) -> list[dict]:
        """Ingresos agregados por día Y por tipo de negocio dentro del periodo y
        segmento(s) seleccionados (una fila por combinación fecha/segmento, no un
        total combinado por día), con el mismo esquema monetario del concentrado
        y del Top 20."""
        query = f"""
            WITH {_CTE_FX_DIARIO},
            filas AS (
                SELECT
                    {_SEGMENTO_POR_FILA} AS segmento,
                    DATE(fh_Envio) AS fecha,
                    im_Movimiento,
                    nb_Moneda
                FROM `{self._tabla}`
                WHERE UPPER(TRIM(de_RazonSocial)) NOT IN UNNEST(@razon_social_excluida)
                  AND {self._condiciones_sucursal()}
                  AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
            ),
            {_CTE_FX_CERCANO}
            SELECT
                filas.fecha,
                filas.segmento,
                SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) != @moneda_usd
                         THEN im_Movimiento ELSE 0 END) AS total_mxn,
                SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd
                         THEN im_Movimiento ELSE 0 END) AS total_usd,
                SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd
                              AND fxc.tipo_cambio IS NOT NULL
                         THEN im_Movimiento * fxc.tipo_cambio ELSE 0 END) AS total_usd_convertido,
                SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd
                              AND fxc.tipo_cambio IS NULL
                         THEN im_Movimiento ELSE 0 END) AS total_usd_sin_tc
            FROM filas
            LEFT JOIN fx_cercano fxc ON fxc.fecha = filas.fecha AND fxc.rn = 1
            WHERE segmento IS NOT NULL
              AND (NOT @filtrar_segmento OR segmento IN UNNEST(@segmentos))
            GROUP BY filas.fecha, filas.segmento
            ORDER BY filas.fecha, filas.segmento
        """
        parametros = self._parametros(fecha_inicio, fecha_fin) + self._parametros_segmentos(segmentos)
        filas = self._cliente.query(
            query,
            job_config=bigquery.QueryJobConfig(query_parameters=parametros),
        ).result()
        return [
            {
                **dict(fila.items()),
                "total_final": (fila["total_mxn"] or 0) + (fila["total_usd_convertido"] or 0),
            }
            for fila in filas
        ]
