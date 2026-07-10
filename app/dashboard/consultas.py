"""Capa de datos del dashboard: cliente de BigQuery (proyecto sipp-app) y
todas las consultas sobre la tabla Tableros.IgresosClientes — tanto las del
segmento principal (sub-pestaña Segmentado) como las del explorador abierto
(sub-pestañas Timeline y Detalle).

Requiere BIGQUERY_CREDENTIALS_PATH en .env apuntando al JSON de la cuenta de
servicio (ver .env.example). Ese JSON NO se sube al repositorio.
"""

import asyncio
import os
from datetime import date

from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_dotenv_path = os.path.join(ROOT_DIR, ".env")
load_dotenv(_dotenv_path if os.path.exists(_dotenv_path) else None)

CREDENCIALES_PATH = os.getenv("BIGQUERY_CREDENTIALS_PATH", "app/credentials/bq.json")
PROYECTO = "sipp-app"
TABLA = "sipp-app.Tableros.IgresosClientes"

# Empresas y tipos de negocio del segmento principal (vistas Empresa / Tipo de
# negocio / Sucursal). El segmento GasPetroil se consulta aparte: mismas
# empresas, pero nb_TipoDeNegocio = 'GasPetroil' en vez de Asociados/Distribuidora,
# y ahí las sucursales de GAS/Autotanque SÍ cuentan (son el objeto de esa vista).
EMPRESAS_DASHBOARD = ["Abastecedora", "ACP Combustibles", "Petro Smart"]
TIPOS_NEGOCIO_DASHBOARD = ["Asociados", "Distribuidora"]

# Correcciones de negocio que los reportes anteriores aplicaban "al vuelo" (sin
# tocar la tabla en BigQuery, que sigue siendo de solo lectura para este
# tablero): 2 casos puntuales donde el nb_TipoDeNegocio capturado no refleja
# cómo se debe reportar el movimiento. Se resuelve con un CASE en el SELECT/WHERE
# — nunca se escribe de vuelta a BigQuery. Cualquier query que filtre o agrupe
# por tipo de negocio debe usar esta expresión en vez de la columna cruda.
TIPO_NEGOCIO_EFECTIVO = """CASE
        WHEN de_RazonSocial = 'CLIENTES PUBLICO EN GENERAL' AND nb_Empresa = 'Petro Smart' THEN 'GasPetroil'
        WHEN id_Cliente = 4359 THEN 'Distribuidora'
        ELSE nb_TipoDeNegocio
    END"""

# Columnas permitidas para poblar catálogos de filtro vía SELECT DISTINCT. La
# clave es el identificador interno (usado por la UI); el valor es la
# expresión SQL real (tipo_negocio reutiliza el CASE de arriba, nunca la
# columna cruda nb_TipoDeNegocio).
_COLUMNAS_CATALOGO = {
    "empresa": "nb_Empresa",
    "sucursal": "nb_sucursal",
    "tipo_negocio": TIPO_NEGOCIO_EFECTIVO,
}

# Tope de filas del detalle crudo (sin agregar): protege costo/performance de
# BigQuery y el render de la tabla. Se pide limite+1 para saber si se truncó
# sin una query de COUNT(*) aparte.
LIMITE_FILAS_DETALLE = 5000

_cliente: bigquery.Client | None = None


def _cliente_bigquery() -> bigquery.Client:
    """Cliente de BigQuery, autenticado con la cuenta de servicio. Se crea una
    sola vez y se reutiliza (evita releer/reautenticar el JSON en cada consulta)."""
    global _cliente
    if _cliente is None:
        ruta = os.path.join(ROOT_DIR, CREDENCIALES_PATH)
        credenciales = service_account.Credentials.from_service_account_file(ruta)
        _cliente = bigquery.Client(project=PROYECTO, credentials=credenciales)
    return _cliente


# Nombres de columna permitidos para agrupar el segmento principal — el nombre
# de columna no se puede pasar como query parameter de BigQuery (solo valores),
# así que se valida contra esta lista fija antes de interpolarlo en el SQL.
_COLUMNAS_SEGMENTO_PRINCIPAL = {"nb_Empresa", "nb_TipoDeNegocio", "nb_sucursal"}


# --- Segmento principal (sub-pestaña Segmentado) -----------------------------

def obtener_agregado_segmento_principal(fecha_inicio: date, fecha_fin: date, agrupar_por: str) -> list[dict]:
    """Total de im_Movimiento agrupado por `agrupar_por` ("nb_Empresa",
    "nb_TipoDeNegocio" o "nb_sucursal") en [fecha_inicio, fecha_fin] (ambas
    incluidas), solo Asociados/Distribuidora, excluyendo pagos entre filiales y
    sucursales de GAS, Autotanque o sin asignar. El tipo de negocio usado (para
    filtrar y, si aplica, para agrupar) es el "efectivo" — ver
    TIPO_NEGOCIO_EFECTIVO — no la columna cruda."""
    if agrupar_por not in _COLUMNAS_SEGMENTO_PRINCIPAL:
        raise ValueError(f"Columna no permitida para agrupar: {agrupar_por}")
    cliente = _cliente_bigquery()
    columna_agrupacion = TIPO_NEGOCIO_EFECTIVO if agrupar_por == "nb_TipoDeNegocio" else agrupar_por
    query = f"""
        SELECT {columna_agrupacion} AS etiqueta, SUM(im_Movimiento) AS total
        FROM `{TABLA}`
        WHERE nb_Empresa IN UNNEST(@empresas)
          AND ({TIPO_NEGOCIO_EFECTIVO}) IN UNNEST(@tipos_negocio)
          AND sn_PagoFilial = 'NO'
          AND nb_sucursal IS NOT NULL
          AND nb_sucursal != ''
          AND NOT LOWER(nb_sucursal) LIKE '%gas%'
          AND NOT LOWER(nb_sucursal) LIKE '%autotanque%'
          AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        GROUP BY {columna_agrupacion}
        ORDER BY total DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("empresas", "STRING", EMPRESAS_DASHBOARD),
            bigquery.ArrayQueryParameter("tipos_negocio", "STRING", TIPOS_NEGOCIO_DASHBOARD),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        ]
    )
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


def obtener_agregado_sucursal_gas(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Total de im_Movimiento por sucursal para el segmento GasPetroil (las
    mismas 3 empresas, pero tipo de negocio GasPetroil en vez de
    Asociados/Distribuidora — usando el tipo de negocio "efectivo", ver
    TIPO_NEGOCIO_EFECTIVO). Aquí NO se excluyen sucursales de GAS/Autotanque
    — son precisamente el objeto de esta vista. Ordenado de mayor a menor."""
    cliente = _cliente_bigquery()
    query = f"""
        SELECT nb_sucursal AS etiqueta, SUM(im_Movimiento) AS total
        FROM `{TABLA}`
        WHERE nb_Empresa IN UNNEST(@empresas)
          AND ({TIPO_NEGOCIO_EFECTIVO}) = 'GasPetroil'
          AND sn_PagoFilial = 'NO'
          AND nb_sucursal IS NOT NULL
          AND nb_sucursal != ''
          AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        GROUP BY nb_sucursal
        ORDER BY total DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("empresas", "STRING", EMPRESAS_DASHBOARD),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        ]
    )
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


def obtener_agregado_otras_empresas(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Total de im_Movimiento por empresa+sucursal para empresas FUERA de las 3
    principales (Abastecedora/ACP Combustibles/Petro Smart). Sin filtro de tipo
    de negocio ni de sucursal (todos), tal como se pidió. Ordenado de mayor a
    menor."""
    cliente = _cliente_bigquery()
    query = f"""
        SELECT nb_Empresa AS empresa, IFNULL(nb_sucursal, '(Sin sucursal)') AS sucursal, SUM(im_Movimiento) AS total
        FROM `{TABLA}`
        WHERE nb_Empresa NOT IN UNNEST(@empresas_principales)
          AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        GROUP BY empresa, sucursal
        ORDER BY total DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("empresas_principales", "STRING", EMPRESAS_DASHBOARD),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        ]
    )
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


def obtener_total_no_identificado(fecha_inicio: date, fecha_fin: date) -> float:
    """Suma de im_Movimiento con sn_Identificada = 'NO' en el rango, sin más
    filtros (todas las empresas/sucursales/tipos de negocio) — un total global
    de "qué tanto no se ha podido identificar" en el periodo."""
    cliente = _cliente_bigquery()
    query = f"""
        SELECT SUM(im_Movimiento) AS total
        FROM `{TABLA}`
        WHERE sn_Identificada = 'NO'
          AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        ]
    )
    filas = list(cliente.query(query, job_config=job_config).result())
    return (filas[0]["total"] or 0) if filas else 0


# --- Explorador (sub-pestañas Timeline / Detalle) ----------------------------
# A diferencia del segmento principal, el explorador NO restringe por defecto
# empresa/tipo de negocio/sucursal: el catálogo de opciones sale directo de la
# tabla (obtener_valores_distintos) y los filtros son opcionales.

def obtener_valores_distintos(columna: str) -> list[str]:
    """Catálogo completo (sin filtro de fecha) de valores de `columna` en
    _COLUMNAS_CATALOGO, para poblar los selectores del explorador. Se llama
    una sola vez por sesión (el resultado se cachea en el llamador) — sin
    filtro de fecha, escanea la tabla completa."""
    if columna not in _COLUMNAS_CATALOGO:
        raise ValueError(f"Columna de catálogo no permitida: {columna}")
    expr = _COLUMNAS_CATALOGO[columna]
    cliente = _cliente_bigquery()
    query = f"""
        SELECT DISTINCT {expr} AS valor
        FROM `{TABLA}`
        WHERE {expr} IS NOT NULL AND {expr} != ''
        ORDER BY valor
    """
    filas = cliente.query(query).result()
    return [fila["valor"] for fila in filas]


def _condiciones_explorador(
    fecha_inicio: date, fecha_fin: date,
    empresas: list[str], sucursales: list[str], tipos_negocio: list[str], filial: str,
) -> tuple[list[str], list]:
    """Condiciones WHERE + parámetros compartidos por obtener_serie_temporal y
    obtener_detalle_movimientos: cada filtro es opcional — lista vacía == sin
    restricción ("todas"). `filial` es "todos" | "excluir" | "solo", sobre
    sn_PagoFilial (no existe columna nb_Filial en la tabla)."""
    condiciones = ["DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin"]
    parametros: list = [
        bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
        bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
    ]
    if empresas:
        condiciones.append("nb_Empresa IN UNNEST(@empresas)")
        parametros.append(bigquery.ArrayQueryParameter("empresas", "STRING", empresas))
    if sucursales:
        condiciones.append("nb_sucursal IN UNNEST(@sucursales)")
        parametros.append(bigquery.ArrayQueryParameter("sucursales", "STRING", sucursales))
    if tipos_negocio:
        condiciones.append(f"({TIPO_NEGOCIO_EFECTIVO}) IN UNNEST(@tipos_negocio)")
        parametros.append(bigquery.ArrayQueryParameter("tipos_negocio", "STRING", tipos_negocio))
    if filial == "excluir":
        condiciones.append("sn_PagoFilial = 'NO'")
    elif filial == "solo":
        condiciones.append("sn_PagoFilial = 'SI'")
    # filial == "todos" -> sin condición adicional
    return condiciones, parametros


def obtener_serie_temporal(
    fecha_inicio: date, fecha_fin: date, periodo: str,
    empresas: list[str], sucursales: list[str], tipos_negocio: list[str], filial: str,
) -> list[dict]:
    """SUM(im_Movimiento) agrupado por mes o semana en [fecha_inicio, fecha_fin],
    con los filtros opcionales de _condiciones_explorador. `periodo`: "mensual"
    o "semanal". Ordenado ASC por periodo (para graficar la tendencia)."""
    if periodo not in ("mensual", "semanal"):
        raise ValueError(f"Periodo no soportado: {periodo}")
    trunc = "DATE_TRUNC(DATE(fh_Envio), MONTH)" if periodo == "mensual" else "DATE_TRUNC(DATE(fh_Envio), WEEK(MONDAY))"
    condiciones, parametros = _condiciones_explorador(
        fecha_inicio, fecha_fin, empresas, sucursales, tipos_negocio, filial
    )
    query = f"""
        SELECT {trunc} AS periodo, SUM(im_Movimiento) AS total
        FROM `{TABLA}`
        WHERE {' AND '.join(condiciones)}
        GROUP BY periodo
        ORDER BY periodo ASC
    """
    job_config = bigquery.QueryJobConfig(query_parameters=parametros)
    filas = _cliente_bigquery().query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


def obtener_detalle_movimientos(
    fecha_inicio: date, fecha_fin: date,
    empresas: list[str], sucursales: list[str], tipos_negocio: list[str], filial: str,
    limite: int = LIMITE_FILAS_DETALLE,
) -> list[dict]:
    """Detalle fila-por-movimiento (sin agregar) con los mismos filtros que
    obtener_serie_temporal, ordenado por fecha desc. Trae `limite + 1` filas
    para que el llamador detecte truncamiento sin un COUNT(*) aparte."""
    condiciones, parametros = _condiciones_explorador(
        fecha_inicio, fecha_fin, empresas, sucursales, tipos_negocio, filial
    )
    parametros = [*parametros, bigquery.ScalarQueryParameter("limite", "INT64", limite + 1)]
    query = f"""
        SELECT
            fh_Envio, nb_Empresa, nb_sucursal,
            ({TIPO_NEGOCIO_EFECTIVO}) AS tipo_negocio_efectivo,
            im_Movimiento, sn_PagoFilial, sn_Identificada, de_RazonSocial, id_Cliente
        FROM `{TABLA}`
        WHERE {' AND '.join(condiciones)}
        ORDER BY fh_Envio DESC
        LIMIT @limite
    """
    job_config = bigquery.QueryJobConfig(query_parameters=parametros)
    filas = _cliente_bigquery().query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


# --- Envolturas async (las obtener_* son síncronas; la UI las corre en un
# thread para no congelar el event loop de Flet) --------------------------

async def consultar_segmento(fecha_inicio: date, fecha_fin: date, columna: str) -> list[tuple[str, float]]:
    filas = await asyncio.to_thread(obtener_agregado_segmento_principal, fecha_inicio, fecha_fin, columna)
    return [(fila["etiqueta"], fila["total"] or 0) for fila in filas]


async def consultar_sucursal_gas(fecha_inicio: date, fecha_fin: date) -> list[tuple[str, float]]:
    filas = await asyncio.to_thread(obtener_agregado_sucursal_gas, fecha_inicio, fecha_fin)
    return [(fila["etiqueta"], fila["total"] or 0) for fila in filas]


async def consultar_otras_empresas(fecha_inicio: date, fecha_fin: date) -> list[tuple[str, float]]:
    filas = await asyncio.to_thread(obtener_agregado_otras_empresas, fecha_inicio, fecha_fin)
    return [(f"{fila['empresa']} · {fila['sucursal']}", fila["total"] or 0) for fila in filas]


async def consultar_no_identificado(fecha_inicio: date, fecha_fin: date) -> float:
    return await asyncio.to_thread(obtener_total_no_identificado, fecha_inicio, fecha_fin)


async def consultar_catalogo(columna: str) -> list[str]:
    return await asyncio.to_thread(obtener_valores_distintos, columna)


async def consultar_serie_temporal(
    fi: date, ff: date, periodo: str, empresas: list[str], sucursales: list[str], tipos: list[str], filial: str
) -> list[dict]:
    return await asyncio.to_thread(obtener_serie_temporal, fi, ff, periodo, empresas, sucursales, tipos, filial)


async def consultar_detalle_movimientos(
    fi: date, ff: date, empresas: list[str], sucursales: list[str], tipos: list[str], filial: str
) -> tuple[list[dict], bool]:
    filas = await asyncio.to_thread(obtener_detalle_movimientos, fi, ff, empresas, sucursales, tipos, filial)
    truncado = len(filas) > LIMITE_FILAS_DETALLE
    return filas[:LIMITE_FILAS_DETALLE], truncado
