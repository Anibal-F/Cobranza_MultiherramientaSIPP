"""Repository de BigQuery de la pestaña RDC (Antigüedad de Saldos): consulta
`Tableros.documentosClientes_AntiguedadSaldosVencidoPorClienteDetalle` y
replica la lógica de las macros `CargarAntiguedadSaldos` /
`CargarAntiguedadAsociados` del Excel de Proyección.

A diferencia de esas macros (que distinguían Distribuidora vs. Asociados según
el reporte de Excel que se hubiera cargado, leyendo "Asociados" en C8), esta
tabla ya trae `nb_TipoDeNegocio` por fila, así que la segmentación se resuelve
en la propia consulta sin necesitar ese archivo.
"""

from datetime import date

from google.cloud import bigquery

from .bigquery_cliente import cliente_bigquery

TABLA = "sipp-app.Tableros.documentosClientes_AntiguedadSaldosVencidoPorClienteDetalle"

# Único filtro configurado en Config_Filtros > "FILTROS — ANTIGÜEDAD DE SALDOS"
# > columna Factura > "EXCLUIR (empieza con)": los folios que empiezan con FCOR
# se excluyen. Si se agregan más prefijos en esa hoja, se agregan aquí.
PREFIJOS_FACTURA_EXCLUIDOS = ["FCOR"]

# Pedido directo del usuario (2026-08-01): estos 3 no son clientes reales para
# efectos de Proyección — son las mismas 3 razones sociales que ya excluyen
# Cobranza Semanal (cobranza_semanal_repository.RAZON_SOCIAL_EXCLUIDA) y
# Dashboard Ingresos, pero ese filtro nunca se había replicado aquí. Coincidencia
# EXACTA (no por prefijo/LIKE) — nombres parecidos de clientes reales (ej.
# "Abastecedora de Combustible Estacion Dimas") deben seguir contando.
CLIENTES_EXCLUIDOS = [
    "ABASTECEDORA DE COMBUSTIBLES DEL PACIFICO",
    "ACP COMBUSTIBLES",
    "PETRO SMART COMBUSTIBLES",
]

# Orden de despliegue: los 3 tipos de negocio que maneja la macro + 'Sin
# identificar' (pedido directo del usuario, 2026-08-01: ya no se descartan del
# concentrado las filas sin cliente/folio o con un tipo de negocio que no cae
# en ninguno de los 3 segmentos — se suman aparte en vez de desaparecer).
SEGMENTOS = ["Distribuidora", "Asociados", "Petroplazas", "Sin identificar"]

# Petroplazas se separa por nombre de cliente, sin importar nb_TipoDeNegocio —
# así aparecía tanto en el reporte de Distribuidora como en el de Asociados en
# la macro original. El resto de las filas se agrupa por nb_TipoDeNegocio;
# GasPetroil, tipo nulo, o cliente/folio vacíos caen en 'Sin identificar' —
# la macro original las descartaba, pero el usuario pidió que ya no se pierdan
# del concentrado.
_SEGMENTO_POR_FILA = """CASE
        WHEN UPPER(TRIM(nb_Cliente)) = 'PETROPLAZAS' THEN 'Petroplazas'
        WHEN nb_TipoDeNegocio = 'Distribuidora' THEN 'Distribuidora'
        WHEN nb_TipoDeNegocio = 'Asociados' THEN 'Asociados'
        ELSE 'Sin identificar'
    END"""


class RdcRepository:
    """Punto único de acceso a BigQuery para la pestaña RDC (Antigüedad de
    Saldos).

    `tabla` es inyectable para pruebas o para apuntar a otra fuente sin tocar el
    resto del código.
    """

    def __init__(self, tabla: str = TABLA) -> None:
        self._cliente = cliente_bigquery()  # comparte el singleton del módulo cliente
        self._tabla = tabla

    def antiguedad_saldos(self, fecha_inicio: date, fecha_fin: date) -> list[dict]:
        """Saldo vigente y vencido a 30 días por segmento (Distribuidora, Asociados,
        Petroplazas, Sin identificar).

        - Saldo vigente (im_CarteraVigente) solo cuenta si fh_Vencimiento cae en
          [fecha_inicio, fecha_fin] — igual que la columna H del Excel, que la
          macro solo sumaba cuando la fecha de vencimiento caía en el rango
          capturado en la hoja Proyección.
        - Saldo vencido a 30 días (im_Vencido30Dias) se suma completo, SIN filtro
          de fecha — la macro sumaba la columna J de cada fila sin condicionarla a
          la fecha de vencimiento (comportamiento asimétrico, pero fiel al
          original).
        - Se excluyen por completo (coincidencia exacta, no se cuentan ni como
          'Sin identificar') los clientes de CLIENTES_EXCLUIDOS, las filas
          'ICV'/'Totales' y los folios con prefijo excluido (FCOR) — son basura
          o entidades deliberadamente fuera del concentrado, no clientes sin
          identificar.
        - Cliente vacío, folio vacío, o tipo de negocio que no cae en Distribuidora/
          Asociados/Petroplazas: en vez de descartarse (como hacía la macro
          original), se agrupan en el segmento 'Sin identificar' — nada se pierde
          del total.
        """
        query = f"""
            WITH filas AS (
                SELECT
                    {_SEGMENTO_POR_FILA} AS segmento,
                    im_CarteraVigente,
                    im_Vencido30Dias,
                    fh_Vencimiento
                FROM `{self._tabla}`
                WHERE IFNULL(UPPER(TRIM(nb_Cliente)), '') != 'ICV'
                  AND NOT LOWER(IFNULL(nb_Cliente, '')) LIKE '%totales%'
                  AND IFNULL(UPPER(TRIM(nb_Cliente)), '') NOT IN UNNEST(@clientes_excluidos)
                  AND NOT EXISTS (
                      SELECT 1 FROM UNNEST(@prefijos_excluidos) AS prefijo
                      WHERE STARTS_WITH(UPPER(TRIM(IFNULL(fl_FolioDocumento, ''))), prefijo)
                  )
            )
            SELECT
                segmento,
                SUM(CASE WHEN DATE(fh_Vencimiento) BETWEEN @fecha_inicio AND @fecha_fin
                         THEN im_CarteraVigente ELSE 0 END) AS saldo_vigente,
                SUM(im_Vencido30Dias) AS saldo_vencido_30
            FROM filas
            GROUP BY segmento
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("prefijos_excluidos", "STRING", PREFIJOS_FACTURA_EXCLUIDOS),
                bigquery.ArrayQueryParameter("clientes_excluidos", "STRING", CLIENTES_EXCLUIDOS),
                bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
                bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            ]
        )
        filas = self._cliente.query(query, job_config=job_config).result()
        return [dict(fila.items()) for fila in filas]

    def detalle_periodo(self, fecha_inicio: date, fecha_fin: date) -> list[dict]:
        """Registros crudos de la tabla en el periodo seleccionado, SIN aplicar
        ninguno de los filtros de negocio del concentrado (cliente/factura vacíos,
        'ICV', 'Totales', prefijo FCOR, segmentación) — solo el filtro de fecha
        (fh_Vencimiento dentro del rango), para poder auditar contra el
        concentrado fila por fila."""
        query = f"""
            SELECT *
            FROM `{self._tabla}`
            WHERE DATE(fh_Vencimiento) BETWEEN @fecha_inicio AND @fecha_fin
            ORDER BY fh_Vencimiento
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
                bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            ]
        )
        filas = self._cliente.query(query, job_config=job_config).result()
        return [dict(fila.items()) for fila in filas]
