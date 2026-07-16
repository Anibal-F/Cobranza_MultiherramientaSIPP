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
# tablero): casos puntuales donde el nb_TipoDeNegocio capturado no refleja
# cómo se debe reportar el movimiento. Se resuelve con un CASE en el SELECT/WHERE
# — nunca se escribe de vuelta a BigQuery. Cualquier query que filtre o agrupe
# por tipo de negocio debe usar esta expresión en vez de la columna cruda.
# La rama de_CuentaBancaria IN ('ABASTECEDORA SF /AENE', 'PETROPLAZAS SF') ->
# 'SF' va primero: reclasifica esas filas antes que las otras reglas puedan
# alcanzarlas.
TIPO_NEGOCIO_EFECTIVO = """CASE
        WHEN de_CuentaBancaria IN ('ABASTECEDORA SF /AENE', 'PETROPLAZAS SF') THEN 'SF'
        WHEN de_RazonSocial = 'CLIENTES PUBLICO EN GENERAL' AND nb_Empresa = 'Petro Smart' THEN 'GasPetroil'
        WHEN id_Cliente = 4359 THEN 'Distribuidora'
        ELSE nb_TipoDeNegocio
    END"""

# de_CuentaBancaria = estos 2 valores se descarta SIEMPRE, en TODAS las
# consultas de este tablero (Segmentado, Timeline, Detalle, catálogos y
# exportaciones) — no son movimientos reales de ingresos, son cuentas de
# control interno. IFNULL evita que NULL (la mayoría de las filas no traen
# de_CuentaBancaria) se cuele fuera del resultado por la propagación de NULL
# de "NOT IN".
FILTRO_CUENTA_BANCARIA_EXCLUIDA = (
    "IFNULL(de_CuentaBancaria, '') NOT IN ('GASTOS NO DEDUCIBLES', 'PETROPLAZAS MONEDEROS')"
)

# 'Dolar (USD)' NUNCA se suma junto con los montos en pesos (mezclar montos de
# distinta moneda en un mismo total no significa nada) — en cada consulta de
# este tablero se separan en dos SUM(): uno para pesos (todo lo que no sea
# USD, incluido NULL) y otro para dólares. IFNULL_MONEDA evita que NULL
# (la mayoría de las filas) se cuele fuera de ambas ramas.
MONEDA_USD = "dolar (usd)"
_IFNULL_MONEDA = "LOWER(IFNULL(nb_Moneda, ''))"


def _expr_suma_mxn(columna: str = "im_Movimiento") -> str:
    return f"SUM(CASE WHEN {_IFNULL_MONEDA} != @moneda_usd THEN {columna} ELSE 0 END)"


def _expr_suma_usd(columna: str = "im_Movimiento") -> str:
    return f"SUM(CASE WHEN {_IFNULL_MONEDA} = @moneda_usd THEN {columna} ELSE 0 END)"


def _param_moneda_usd() -> bigquery.ScalarQueryParameter:
    return bigquery.ScalarQueryParameter("moneda_usd", "STRING", MONEDA_USD)


# Conversión de USD a MXN para las secciones de Segmentado (Empresa, Tipo de
# negocio, Sucursal, Sucursal Gaseras, SF, Otras empresas): el tipo de cambio
# no vive en IgresosClientes, así que se cruza por FECHA contra
# Tableros.DocumentosClientesCobranza.im_TipoCambio. Esa columna trae varios
# valores distintos el mismo día (no es un único "tipo de cambio del día"),
# así que se usa el PROMEDIO diario — confirmado con el usuario. Si el día
# exacto (fh_Envio) no tiene tipo de cambio en Cobranza, se usa el del día
# MÁS CERCANO que sí tenga (antes o después, el de menor diferencia en días)
# — pedido explícito del usuario tras ver que ~74% del USD histórico no
# matcheaba exacto (el hueco es sobre todo anterior a dic-2024, así que el
# "más cercano" con frecuencia cae hacia adelante, no solo hacia atrás).
# `total_usd_sin_tc` solo puede quedar con saldo si Cobranza no tiene NINGÚN
# tipo de cambio registrado en toda su historia — un caso límite que en la
# práctica no debería ocurrir, pero se conserva como red de seguridad: nunca
# se descarta ni se inventa un valor.
TABLA_COBRANZA_FX = "sipp-app.Tableros.DocumentosClientesCobranza"

_CTE_FX_DIARIO = f"""fx_diario AS (
        SELECT DATE(fh_Deposito_Mostrar) AS fecha, AVG(im_TipoCambio) AS tipo_cambio
        FROM `{TABLA_COBRANZA_FX}`
        WHERE nb_TipoMoneda = 'Dolar (USD)'
        GROUP BY fecha
    )"""

# Requiere que `filas` ya esté definida antes en el mismo WITH (usa sus fechas
# en USD para saber qué días necesitan un tipo de cambio "cercano"). Compara
# cada día en USD contra TODOS los días con tipo de cambio (CROSS JOIN) y se
# queda con el de menor diferencia absoluta — BigQuery no soporta subconsultas
# correlacionadas contra otra tabla, así que el "más cercano" se resuelve con
# ROW_NUMBER() en vez de un ORDER BY ... LIMIT 1 correlacionado.
_CTE_FX_CERCANO = f"""fx_cercano AS (
        SELECT
            f.fecha,
            fx.tipo_cambio,
            ROW_NUMBER() OVER (
                PARTITION BY f.fecha ORDER BY ABS(DATE_DIFF(f.fecha, fx.fecha, DAY)) ASC
            ) AS rn
        FROM (SELECT DISTINCT fecha FROM filas WHERE {_IFNULL_MONEDA} = @moneda_usd) AS f
        CROSS JOIN fx_diario AS fx
    )"""


def _expr_suma_usd_convertido() -> str:
    return (
        f"SUM(CASE WHEN {_IFNULL_MONEDA} = @moneda_usd AND fxc.tipo_cambio IS NOT NULL "
        "THEN im_Movimiento * fxc.tipo_cambio ELSE 0 END)"
    )


def _expr_suma_usd_sin_tc() -> str:
    return (
        f"SUM(CASE WHEN {_IFNULL_MONEDA} = @moneda_usd AND fxc.tipo_cambio IS NULL "
        "THEN im_Movimiento ELSE 0 END)"
    )


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
    sucursales de GAS, Autotanque, Corporativo o sin asignar. El tipo de negocio usado (para
    filtrar y, si aplica, para agrupar) es el "efectivo" — ver
    TIPO_NEGOCIO_EFECTIVO — no la columna cruda. `total` es pesos únicamente;
    `total_usd` va aparte (ver MONEDA_USD) — nunca se suman entre sí.
    `total_usd_convertido` es `total_usd` multiplicado por el tipo de cambio
    promedio del día exacto, o si ese día no tiene, el del día más cercano
    que sí tenga (ver _CTE_FX_CERCANO); `total_usd_sin_tc` es la parte de
    `total_usd` para la que no existe NINGÚN tipo de cambio en toda la
    historia de Cobranza — se reporta aparte, nunca se descarta ni se
    convierte con un valor inventado."""
    if agrupar_por not in _COLUMNAS_SEGMENTO_PRINCIPAL:
        raise ValueError(f"Columna no permitida para agrupar: {agrupar_por}")
    cliente = _cliente_bigquery()
    columna_agrupacion = TIPO_NEGOCIO_EFECTIVO if agrupar_por == "nb_TipoDeNegocio" else agrupar_por
    query = f"""
        WITH {_CTE_FX_DIARIO},
        filas AS (
            SELECT
                {columna_agrupacion} AS etiqueta,
                DATE(fh_Envio) AS fecha,
                im_Movimiento,
                nb_Moneda
            FROM `{TABLA}`
            WHERE nb_Empresa IN UNNEST(@empresas)
              AND ({TIPO_NEGOCIO_EFECTIVO}) IN UNNEST(@tipos_negocio)
              AND sn_PagoFilial = 'NO'
              AND nb_sucursal IS NOT NULL
              AND nb_sucursal != ''
              AND NOT LOWER(nb_sucursal) LIKE '%gas%'
              AND NOT LOWER(nb_sucursal) LIKE '%autotanque%'
              AND NOT LOWER(nb_sucursal) LIKE '%corporativo%'
              AND {FILTRO_CUENTA_BANCARIA_EXCLUIDA}
              AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        ),
        {_CTE_FX_CERCANO}
        SELECT
            etiqueta,
            {_expr_suma_mxn()} AS total,
            {_expr_suma_usd()} AS total_usd,
            {_expr_suma_usd_convertido()} AS total_usd_convertido,
            {_expr_suma_usd_sin_tc()} AS total_usd_sin_tc
        FROM filas
        LEFT JOIN fx_cercano fxc ON fxc.fecha = filas.fecha AND fxc.rn = 1
        GROUP BY etiqueta
        ORDER BY total DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("empresas", "STRING", EMPRESAS_DASHBOARD),
            bigquery.ArrayQueryParameter("tipos_negocio", "STRING", TIPOS_NEGOCIO_DASHBOARD),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            _param_moneda_usd(),
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
        WITH {_CTE_FX_DIARIO},
        filas AS (
            SELECT nb_sucursal AS etiqueta, DATE(fh_Envio) AS fecha, im_Movimiento, nb_Moneda
            FROM `{TABLA}`
            WHERE nb_Empresa IN UNNEST(@empresas)
              AND ({TIPO_NEGOCIO_EFECTIVO}) = 'GasPetroil'
              AND sn_PagoFilial = 'NO'
              AND nb_sucursal IS NOT NULL
              AND nb_sucursal != ''
              AND {FILTRO_CUENTA_BANCARIA_EXCLUIDA}
              AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        ),
        {_CTE_FX_CERCANO}
        SELECT
            etiqueta,
            {_expr_suma_mxn()} AS total,
            {_expr_suma_usd()} AS total_usd,
            {_expr_suma_usd_convertido()} AS total_usd_convertido,
            {_expr_suma_usd_sin_tc()} AS total_usd_sin_tc
        FROM filas
        LEFT JOIN fx_cercano fxc ON fxc.fecha = filas.fecha AND fxc.rn = 1
        GROUP BY etiqueta
        ORDER BY total DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("empresas", "STRING", EMPRESAS_DASHBOARD),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            _param_moneda_usd(),
        ]
    )
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


def obtener_agregado_sf(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Total de im_Movimiento por sucursal para el segmento 'SF' (cuentas
    bancarias reclasificadas vía TIPO_NEGOCIO_EFECTIVO desde
    de_CuentaBancaria = 'ABASTECEDORA SF /AENE' o 'PETROPLAZAS SF'), en las 3
    empresas principales, excluyendo pagos entre filiales. A diferencia de la vista
    'Sucursal' del segmento principal, aquí se incluyen TODAS las sucursales
    (no se excluyen GAS/Autotanque/sin asignar) — así se pidió. Ordenado de
    mayor a menor."""
    cliente = _cliente_bigquery()
    query = f"""
        WITH {_CTE_FX_DIARIO},
        filas AS (
            SELECT IFNULL(nb_sucursal, '(Sin sucursal)') AS etiqueta, DATE(fh_Envio) AS fecha, im_Movimiento, nb_Moneda
            FROM `{TABLA}`
            WHERE nb_Empresa IN UNNEST(@empresas)
              AND ({TIPO_NEGOCIO_EFECTIVO}) = 'SF'
              AND sn_PagoFilial = 'NO'
              AND {FILTRO_CUENTA_BANCARIA_EXCLUIDA}
              AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        ),
        {_CTE_FX_CERCANO}
        SELECT
            etiqueta,
            {_expr_suma_mxn()} AS total,
            {_expr_suma_usd()} AS total_usd,
            {_expr_suma_usd_convertido()} AS total_usd_convertido,
            {_expr_suma_usd_sin_tc()} AS total_usd_sin_tc
        FROM filas
        LEFT JOIN fx_cercano fxc ON fxc.fecha = filas.fecha AND fxc.rn = 1
        GROUP BY etiqueta
        ORDER BY total DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("empresas", "STRING", EMPRESAS_DASHBOARD),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            _param_moneda_usd(),
        ]
    )
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


# Reclasificación de empresa para 'Otras empresas': cualquier nb_Empresa que
# CONTENGA 'PETROPLAZAS' se agrupa como una sola 'Petroplazas' (junta todas
# sus variantes: PETROPLAZAS, PETROPLAZAS AEROPUERTO, etc.), y lo mismo para
# 'GC MOTORS' -> 'GC Motors de Occidente' — pedido explícito del usuario en
# vez de dejarlas fragmentadas por cada variante de nombre. El resto de
# empresas conserva su nb_Empresa tal cual.
_EMPRESA_OTRAS_RECLASIFICADA = """CASE
        WHEN UPPER(nb_Empresa) LIKE '%PETROPLAZAS%' THEN 'Petroplazas'
        WHEN UPPER(nb_Empresa) LIKE '%GC MOTORS%' THEN 'GC Motors de Occidente'
        ELSE nb_Empresa
    END"""


def obtener_agregado_otras_empresas(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Total de im_Movimiento por empresa (reclasificada, ver
    _EMPRESA_OTRAS_RECLASIFICADA) + tipo de filial, para empresas FUERA de las
    3 principales (Abastecedora/ACP Combustibles/Petro Smart) — todas las
    variantes que contengan 'Petroplazas' se agrupan juntas, igual que 'GC
    Motors'. Se segmenta por sn_PagoFilial ('NO'/'SI') en la misma tarjeta:
    cada empresa aparece como dos filas, una por tipo de filial. Sin filtro
    de tipo de negocio ni de sucursal (todos). Ordenado de mayor a menor."""
    cliente = _cliente_bigquery()
    query = f"""
        WITH {_CTE_FX_DIARIO},
        filas AS (
            SELECT
                {_EMPRESA_OTRAS_RECLASIFICADA} AS empresa,
                sn_PagoFilial AS filial,
                DATE(fh_Envio) AS fecha,
                im_Movimiento,
                nb_Moneda
            FROM `{TABLA}`
            WHERE nb_Empresa NOT IN UNNEST(@empresas_principales)
              AND {FILTRO_CUENTA_BANCARIA_EXCLUIDA}
              AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        ),
        {_CTE_FX_CERCANO}
        SELECT
            empresa,
            filial,
            {_expr_suma_mxn()} AS total,
            {_expr_suma_usd()} AS total_usd,
            {_expr_suma_usd_convertido()} AS total_usd_convertido,
            {_expr_suma_usd_sin_tc()} AS total_usd_sin_tc
        FROM filas
        LEFT JOIN fx_cercano fxc ON fxc.fecha = filas.fecha AND fxc.rn = 1
        GROUP BY empresa, filial
        ORDER BY total DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("empresas_principales", "STRING", EMPRESAS_DASHBOARD),
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            _param_moneda_usd(),
        ]
    )
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


def obtener_total_no_identificado(fecha_inicio: date, fecha_fin: date) -> dict:
    """Suma de im_Movimiento con sn_Identificada = 'NO' en el rango, sin más
    filtros (todas las empresas/sucursales/tipos de negocio) — un total global
    de "qué tanto no se ha podido identificar" en el periodo. `total` es pesos
    únicamente; `total_usd` va aparte."""
    cliente = _cliente_bigquery()
    query = f"""
        SELECT
            {_expr_suma_mxn()} AS total,
            {_expr_suma_usd()} AS total_usd
        FROM `{TABLA}`
        WHERE sn_Identificada = 'NO'
          AND {FILTRO_CUENTA_BANCARIA_EXCLUIDA}
          AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
            _param_moneda_usd(),
        ]
    )
    filas = list(cliente.query(query, job_config=job_config).result())
    if not filas:
        return {"total": 0, "total_usd": 0}
    return {"total": filas[0]["total"] or 0, "total_usd": filas[0]["total_usd"] or 0}


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
          AND {FILTRO_CUENTA_BANCARIA_EXCLUIDA}
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
    condiciones = ["DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin", FILTRO_CUENTA_BANCARIA_EXCLUIDA]
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
    o "semanal". Ordenado ASC por periodo (para graficar la tendencia). `total`
    es pesos únicamente; `total_usd` va aparte por periodo."""
    if periodo not in ("mensual", "semanal"):
        raise ValueError(f"Periodo no soportado: {periodo}")
    trunc = "DATE_TRUNC(DATE(fh_Envio), MONTH)" if periodo == "mensual" else "DATE_TRUNC(DATE(fh_Envio), WEEK(MONDAY))"
    condiciones, parametros = _condiciones_explorador(
        fecha_inicio, fecha_fin, empresas, sucursales, tipos_negocio, filial
    )
    parametros = [*parametros, _param_moneda_usd()]
    query = f"""
        SELECT {trunc} AS periodo, {_expr_suma_mxn()} AS total, {_expr_suma_usd()} AS total_usd
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
    para que el llamador detecte truncamiento sin un COUNT(*) aparte. Incluye
    nb_Moneda para que cada fila muestre su moneda — a este nivel (fila por
    fila, sin agregar) "separar" pesos de dólares es simplemente hacer visible
    la moneda de cada movimiento, no un total aparte."""
    condiciones, parametros = _condiciones_explorador(
        fecha_inicio, fecha_fin, empresas, sucursales, tipos_negocio, filial
    )
    parametros = [*parametros, bigquery.ScalarQueryParameter("limite", "INT64", limite + 1)]
    query = f"""
        SELECT
            fh_Envio, nb_Empresa, nb_sucursal,
            ({TIPO_NEGOCIO_EFECTIVO}) AS tipo_negocio_efectivo,
            im_Movimiento, nb_Moneda, sn_PagoFilial, sn_Identificada, de_RazonSocial, id_Cliente
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
#
# Cada consulta de sección devuelve una lista de 5-tuplas
# (etiqueta, mxn, usd, usd_convertido, usd_sin_tc):
#   - mxn: pesos únicamente (nunca incluye USD).
#   - usd: dólares crudos, sin convertir.
#   - usd_convertido: `usd` × tipo de cambio promedio del día exacto, o del
#     día más cercano que sí tenga tipo de cambio si el exacto no tiene.
#   - usd_sin_tc: la parte de `usd` para la que no existe NINGÚN tipo de
#     cambio en toda la historia de Cobranza — no se descarta ni se
#     convierte con un valor inventado, se reporta aparte (en la práctica
#     debería ser $0 casi siempre, salvo que Cobranza no tenga datos).
# "Total final" de cada categoría = mxn + usd_convertido (lo calcula la UI).

async def consultar_segmento(
    fecha_inicio: date, fecha_fin: date, columna: str
) -> list[tuple[str, float, float, float, float]]:
    filas = await asyncio.to_thread(obtener_agregado_segmento_principal, fecha_inicio, fecha_fin, columna)
    return [
        (fila["etiqueta"], fila["total"] or 0, fila["total_usd"] or 0,
         fila["total_usd_convertido"] or 0, fila["total_usd_sin_tc"] or 0)
        for fila in filas
    ]


async def consultar_sucursal_gas(fecha_inicio: date, fecha_fin: date) -> list[tuple[str, float, float, float, float]]:
    filas = await asyncio.to_thread(obtener_agregado_sucursal_gas, fecha_inicio, fecha_fin)
    return [
        (fila["etiqueta"], fila["total"] or 0, fila["total_usd"] or 0,
         fila["total_usd_convertido"] or 0, fila["total_usd_sin_tc"] or 0)
        for fila in filas
    ]


async def consultar_sf(fecha_inicio: date, fecha_fin: date) -> list[tuple[str, float, float, float, float]]:
    filas = await asyncio.to_thread(obtener_agregado_sf, fecha_inicio, fecha_fin)
    return [
        (fila["etiqueta"], fila["total"] or 0, fila["total_usd"] or 0,
         fila["total_usd_convertido"] or 0, fila["total_usd_sin_tc"] or 0)
        for fila in filas
    ]


async def consultar_otras_empresas(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Una fila POR EMPRESA (no por empresa+filial): la consulta SQL trae 2
    filas por empresa (una por sn_PagoFilial), y aquí se pivotea en Python
    para que la UI muestre una sola fila por empresa con las columnas
    no_mxn/no_usd_convertido/no_usd_sin_tc y si_mxn/si_usd_convertido/
    si_usd_sin_tc lado a lado — evita la vista anterior, que amontonaba 2
    filas casi idénticas por empresa en el mismo leaderboard."""
    filas = await asyncio.to_thread(obtener_agregado_otras_empresas, fecha_inicio, fecha_fin)
    por_empresa: dict[str, dict] = {}
    for fila in filas:
        entrada = por_empresa.setdefault(fila["empresa"], {
            "empresa": fila["empresa"],
            "no_mxn": 0.0, "no_usd_convertido": 0.0, "no_usd_sin_tc": 0.0,
            "si_mxn": 0.0, "si_usd_convertido": 0.0, "si_usd_sin_tc": 0.0,
        })
        prefijo = "no" if fila["filial"] == "NO" else "si"
        entrada[f"{prefijo}_mxn"] = fila["total"] or 0
        entrada[f"{prefijo}_usd_convertido"] = fila["total_usd_convertido"] or 0
        entrada[f"{prefijo}_usd_sin_tc"] = fila["total_usd_sin_tc"] or 0
    items = list(por_empresa.values())
    items.sort(
        key=lambda it: it["no_mxn"] + it["no_usd_convertido"] + it["si_mxn"] + it["si_usd_convertido"],
        reverse=True,
    )
    return items


async def consultar_no_identificado(fecha_inicio: date, fecha_fin: date) -> tuple[float, float]:
    """(total_mxn, total_usd)."""
    resultado = await asyncio.to_thread(obtener_total_no_identificado, fecha_inicio, fecha_fin)
    return resultado["total"], resultado["total_usd"]


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


# --- Exportación a Excel de la sub-pestaña Detalle ---------------------------
# A diferencia de obtener_detalle_movimientos (que respeta los filtros opcionales
# del panel y tiene un tope de LIMITE_FILAS_DETALLE para no saturar la tabla en
# pantalla), la descarga de Detalle es un volcado completo del periodo: solo el
# filtro de fecha, sin empresa/sucursal/tipo de negocio/filial y sin límite de filas.

def obtener_detalle_completo_periodo(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Todos los movimientos de la tabla en [fecha_inicio, fecha_fin], sin los
    filtros opcionales del explorador — solo el filtro de fecha — para la
    descarga a Excel de la sub-pestaña Detalle."""
    query = f"""
        SELECT *, ({TIPO_NEGOCIO_EFECTIVO}) AS tipo_negocio_efectivo
        FROM `{TABLA}`
        WHERE DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
          AND {FILTRO_CUENTA_BANCARIA_EXCLUIDA}
        ORDER BY fh_Envio DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        ]
    )
    filas = _cliente_bigquery().query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


async def consultar_detalle_completo_periodo(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    return await asyncio.to_thread(obtener_detalle_completo_periodo, fecha_inicio, fecha_fin)
