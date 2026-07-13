"""Capa de datos de la pestaña RDC (Antigüedad de Saldos): consulta
`Tableros.documentosClientes_AntiguedadSaldosVencidoPorClienteDetalle` y
replica la lógica de las macros `CargarAntiguedadSaldos` /
`CargarAntiguedadAsociados` del Excel de Proyección.

A diferencia de esas macros (que distinguían Distribuidora vs. Asociados según
el reporte de Excel que se hubiera cargado, leyendo "Asociados" en C8), esta
tabla ya trae `nb_TipoDeNegocio` por fila, así que la segmentación se resuelve
en la propia consulta sin necesitar ese archivo.

Requiere BIGQUERY_CREDENTIALS_PATH en .env (ver app/services/bigquery_cliente.py).
"""

import asyncio
from datetime import date

from google.cloud import bigquery

from ..services.bigquery_cliente import cliente_bigquery

TABLA = "sipp-app.Tableros.documentosClientes_AntiguedadSaldosVencidoPorClienteDetalle"

# Único filtro configurado en Config_Filtros > "FILTROS — ANTIGÜEDAD DE SALDOS"
# > columna Factura > "EXCLUIR (empieza con)": los folios que empiezan con FCOR
# se excluyen. Si se agregan más prefijos en esa hoja, se agregan aquí.
PREFIJOS_FACTURA_EXCLUIDOS = ["FCOR"]

# Orden de despliegue de los 3 tipos de negocio que maneja la macro.
SEGMENTOS = ["Distribuidora", "Asociados", "Petroplazas"]

# Petroplazas se separa por nombre de cliente, sin importar nb_TipoDeNegocio —
# así aparecía tanto en el reporte de Distribuidora como en el de Asociados en
# la macro original. El resto de las filas se agrupa por nb_TipoDeNegocio.
# GasPetroil (y tipo nulo) quedan fuera: la macro nunca los tocaba.
_SEGMENTO_POR_FILA = """CASE
        WHEN UPPER(TRIM(nb_Cliente)) = 'PETROPLAZAS' THEN 'Petroplazas'
        WHEN nb_TipoDeNegocio = 'Distribuidora' THEN 'Distribuidora'
        WHEN nb_TipoDeNegocio = 'Asociados' THEN 'Asociados'
    END"""


def obtener_antiguedad_saldos(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Saldo vigente y vencido a 30 días por segmento (Distribuidora, Asociados,
    Petroplazas).

    - Saldo vigente (im_CarteraVigente) solo cuenta si fh_Vencimiento cae en
      [fecha_inicio, fecha_fin] — igual que la columna H del Excel, que la
      macro solo sumaba cuando la fecha de vencimiento caía en el rango
      capturado en la hoja Proyección.
    - Saldo vencido a 30 días (im_Vencido30Dias) se suma completo, SIN filtro
      de fecha — la macro sumaba la columna J de cada fila sin condicionarla a
      la fecha de vencimiento (comportamiento asimétrico, pero fiel al
      original).
    """
    cliente = cliente_bigquery()
    query = f"""
        WITH filas AS (
            SELECT
                {_SEGMENTO_POR_FILA} AS segmento,
                im_CarteraVigente,
                im_Vencido30Dias,
                fh_Vencimiento
            FROM `{TABLA}`
            WHERE nb_Cliente IS NOT NULL AND TRIM(nb_Cliente) != ''
              AND fl_FolioDocumento IS NOT NULL AND TRIM(fl_FolioDocumento) != ''
              AND UPPER(TRIM(nb_Cliente)) != 'ICV'
              AND NOT LOWER(nb_Cliente) LIKE '%totales%'
              AND NOT EXISTS (
                  SELECT 1 FROM UNNEST(@prefijos_excluidos) AS prefijo
                  WHERE STARTS_WITH(UPPER(TRIM(fl_FolioDocumento)), prefijo)
              )
        )
        SELECT
            segmento,
            SUM(CASE WHEN DATE(fh_Vencimiento) BETWEEN @fecha_inicio AND @fecha_fin
                     THEN im_CarteraVigente ELSE 0 END) AS saldo_vigente,
            SUM(im_Vencido30Dias) AS saldo_vencido_30
        FROM filas
        WHERE segmento IS NOT NULL
        GROUP BY segmento
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("prefijos_excluidos", "STRING", PREFIJOS_FACTURA_EXCLUIDOS),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        ]
    )
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


async def consultar_antiguedad_saldos(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    return await asyncio.to_thread(obtener_antiguedad_saldos, fecha_inicio, fecha_fin)


def obtener_detalle_periodo(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Registros crudos de la tabla en el periodo seleccionado, SIN aplicar
    ninguno de los filtros de negocio del concentrado (cliente/factura vacíos,
    'ICV', 'Totales', prefijo FCOR, segmentación) — solo el filtro de fecha
    (fh_Vencimiento dentro del rango), para poder auditar contra el
    concentrado fila por fila."""
    cliente = cliente_bigquery()
    query = f"""
        SELECT *
        FROM `{TABLA}`
        WHERE DATE(fh_Vencimiento) BETWEEN @fecha_inicio AND @fecha_fin
        ORDER BY fh_Vencimiento
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        ]
    )
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


async def consultar_detalle_periodo(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    return await asyncio.to_thread(obtener_detalle_periodo, fecha_inicio, fecha_fin)
