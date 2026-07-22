"""Repository de BigQuery del reporte 'Cumplimiento de Cobro': consulta
`Tableros.documentosClientes_AntiguedadSaldosVencidoPorClienteDetalle` filtrada
por fecha de vencimiento (fh_Vencimiento), con los mismos filtros de calidad de
datos que ya usa la pestaña Proyección (RDC) sobre esta misma tabla — cliente y
folio no vacíos, cliente distinto de 'ICV', sin filas de subtotal 'Totales' y
sin folios con prefijo FCOR (ver app/services/rdc_repository.py)."""

from datetime import date

from google.cloud import bigquery

from .bigquery_cliente import cliente_bigquery

TABLA = "sipp-app.Tableros.documentosClientes_AntiguedadSaldosVencidoPorClienteDetalle"

# Mismo prefijo excluido que Config_Filtros > "FILTROS — ANTIGÜEDAD DE SALDOS"
# (ver rdc_repository.PREFIJOS_FACTURA_EXCLUIDOS): se comparte la tabla, así
# que se comparte también este filtro de calidad de datos.
PREFIJOS_FACTURA_EXCLUIDOS = ["FCOR"]

# Columnas que consume la UI (tarjetas KPI, mini dashboards por tipo de
# negocio, top de clientes y el Excel de detalle).
#
# OJO: se usa im_Cartera (saldo total original del documento) en vez de
# im_CarteraVigente. im_CarteraVigente es una FOTO DEL DÍA DE HOY (la parte
# del saldo que aún no vence AL MOMENTO DE CONSULTAR), no un valor histórico
# por fecha de vencimiento — para cualquier fila cuyo fh_Vencimiento ya pasó
# (el caso normal de este reporte, que por default mira la semana ANTERIOR),
# im_CarteraVigente sale en 0 porque ese saldo ya se reclasificó a
# im_CarteraVencida / im_Vencido30Dias / etc. im_Cartera, en cambio, es el
# saldo total del documento (im_Cartera = im_CarteraVigente +
# im_CarteraVencida siempre) y sí representa "lo que se esperaba cobrar" sin
# importar el estatus de cobranza a hoy. im_CarteraVencida se trae aparte
# para poder comparar esperado vs. lo que sigue pendiente HOY.
COLUMNAS_DETALLE = [
    "nb_Cliente",
    "nb_Sucursal",
    "fl_FolioDocumento",
    "fh_Vencimiento",
    "im_Cartera",
    "im_CarteraVencida",
    "nb_Empresa",
    "nb_TipoDeNegocio",
    "sn_filial",
]

_FILTROS_CALIDAD = """
        nb_Cliente IS NOT NULL AND TRIM(nb_Cliente) != ''
        AND fl_FolioDocumento IS NOT NULL AND TRIM(fl_FolioDocumento) != ''
        AND UPPER(TRIM(nb_Cliente)) != 'ICV'
        AND NOT LOWER(nb_Cliente) LIKE '%totales%'
        AND NOT EXISTS (
            SELECT 1 FROM UNNEST(@prefijos_excluidos) AS prefijo
            WHERE STARTS_WITH(UPPER(TRIM(fl_FolioDocumento)), prefijo)
        )
"""


class CumplimientoRepository:
    """Punto único de acceso a BigQuery del reporte de Cumplimiento de Cobro.

    `tabla` es inyectable para pruebas o para apuntar a otra fuente sin tocar
    el resto del código.
    """

    def __init__(self, tabla: str = TABLA) -> None:
        self._cliente = cliente_bigquery()  # comparte el singleton del módulo cliente
        self._tabla = tabla

    def detalle_periodo(self, fecha_inicio: date, fecha_fin: date) -> list[dict]:
        """Filas cuya fh_Vencimiento cae en [fecha_inicio, fecha_fin], con los
        filtros de calidad de datos descritos arriba. Los KPIs, los mini
        dashboards por tipo de negocio y el top de empresas se calculan en
        Python sobre este mismo resultado (un solo viaje a BigQuery)."""
        columnas = ", ".join(COLUMNAS_DETALLE)
        query = f"""
            SELECT {columnas}
            FROM `{self._tabla}`
            WHERE DATE(fh_Vencimiento) BETWEEN @fecha_inicio AND @fecha_fin
              AND {_FILTROS_CALIDAD}
            ORDER BY fh_Vencimiento
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("prefijos_excluidos", "STRING", PREFIJOS_FACTURA_EXCLUIDOS),
                bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
                bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            ]
        )
        filas = self._cliente.query(query, job_config=job_config).result()
        return [dict(fila.items()) for fila in filas]
