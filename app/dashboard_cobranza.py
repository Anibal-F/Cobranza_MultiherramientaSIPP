"""Dashboard de cobranza mensual: conecta a BigQuery (proyecto sipp-app) y
agrega los datos de la tabla Tableros.IgresosClientes bajo varias vistas, todas
visibles a la vez (sin cambiar de pestaña): Empresa, Tipo de negocio, Sucursal
(segmento principal), Sucursal (Gasolineras), Otras empresas, y una tarjeta de
movimientos sin identificar.

Requiere BIGQUERY_CREDENTIALS_PATH en .env apuntando al JSON de la cuenta de
servicio (ver .env.example). Ese JSON NO se sube al repositorio.
"""

import asyncio
import dataclasses
import math
from datetime import date, datetime

import flet as ft
import flet_charts as fc
from google.cloud import bigquery

# Cliente y configuración de BigQuery viven en la capa de servicios compartida, para
# reutilizar el mismo cliente singleton con el módulo de Conciliación Bancaria.
from .services.bigquery_cliente import PROYECTO, TABLA, cliente_bigquery

# Empresas y tipos de negocio del segmento principal (vistas Empresa / Tipo de
# negocio / Sucursal). El segmento GasPetroil se consulta aparte: mismas
# empresas, pero nb_TipoDeNegocio = 'GasPetroil' en vez de Asociados/Distribuidora,
# y ahí las sucursales de GAS/Autotanque SÍ cuentan (son el objeto de esa vista).
EMPRESAS_DASHBOARD = ["Abastecedora", "ACP Combustibles", "Petro Smart"]
TIPOS_NEGOCIO_DASHBOARD = ["Asociados", "Distribuidora"]

# Umbral: con más categorías que esto, una tarjeta KPI y un color por barra ya
# no escala (ruido / pasa el tope de 8 colores CVD-safe) — se muestra un solo
# total agregado y una barra de un solo color por categoría.
UMBRAL_MUCHAS_CATEGORIAS = 6

# Paleta categórica validada (orden fijo, CVD-safe): slot 0 = azul, 1 = aqua,
# 2 = amarillo, ... Un juego de tonos para tema claro y otro para oscuro. Nunca
# se asignan más de ~6-8 slots a la vez (ver UMBRAL_MUCHAS_CATEGORIAS).
PALETA_CATEGORICA_LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
PALETA_CATEGORICA_DARK = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"]


def _color_slot(indice: int, dark: bool) -> str:
    paleta = PALETA_CATEGORICA_DARK if dark else PALETA_CATEGORICA_LIGHT
    return paleta[indice % len(paleta)]


def _formato_compacto(valor: float) -> str:
    """$580,497,376.78 -> $580.5M (cifra escaneable en una tarjeta KPI)."""
    signo = "-" if valor < 0 else ""
    valor = abs(valor)
    if valor >= 1_000_000_000:
        return f"{signo}${valor / 1_000_000_000:,.1f}B"
    if valor >= 1_000_000:
        return f"{signo}${valor / 1_000_000:,.1f}M"
    if valor >= 1_000:
        return f"{signo}${valor / 1_000:,.1f}K"
    return f"{signo}${valor:,.2f}"


def _redondear_max_y(valor: float) -> float:
    """Sube `valor` al siguiente escalón "limpio" (medio orden de magnitud:
    ...50, 100, 500, 1000...) en vez de un *1.15 crudo — evita que el tope del
    eje Y quede pegado/encimado con el último tick "redondo" que dibuja la
    gráfica, sobre todo en las versiones compactas de poca altura."""
    if valor <= 0:
        return 10
    magnitud = 10 ** math.floor(math.log10(valor))
    paso = magnitud / 2
    return math.ceil(valor * 1.05 / paso) * paso


# Nombres de columna permitidos para agrupar el segmento principal — el nombre
# de columna no se puede pasar como query parameter de BigQuery (solo valores),
# así que se valida contra esta lista fija antes de interpolarlo en el SQL.
_COLUMNAS_SEGMENTO_PRINCIPAL = {"nb_Empresa", "nb_TipoDeNegocio", "nb_sucursal"}

# Correcciones de negocio que los reportes anteriores aplicaban "al vuelo" (sin
# tocar la tabla en BigQuery, que sigue siendo de solo lectura para este
# tablero): 2 casos puntuales donde el nb_TipoDeNegocio capturado no refleja
# cómo se debe reportar el movimiento. Se resuelve con un CASE en el SELECT/WHERE
# — nunca se escribe de vuelta a BigQuery. Cualquier query que filtre o agrupe
# por tipo de negocio debe usar esta expresión en vez de la columna cruda.
_TIPO_NEGOCIO_EFECTIVO = """CASE
        WHEN de_RazonSocial = 'CLIENTES PUBLICO EN GENERAL' AND nb_Empresa = 'Petro Smart' THEN 'GasPetroil'
        WHEN id_Cliente = 4359 THEN 'Distribuidora'
        ELSE nb_TipoDeNegocio
    END"""


def obtener_agregado_segmento_principal(fecha_inicio: date, fecha_fin: date, agrupar_por: str) -> list[dict]:
    """Total de im_Movimiento agrupado por `agrupar_por` ("nb_Empresa",
    "nb_TipoDeNegocio" o "nb_sucursal") en [fecha_inicio, fecha_fin] (ambas
    incluidas), solo Asociados/Distribuidora, excluyendo pagos entre filiales y
    sucursales de GAS, Autotanque o sin asignar. El tipo de negocio usado (para
    filtrar y, si aplica, para agrupar) es el "efectivo" — ver
    _TIPO_NEGOCIO_EFECTIVO — no la columna cruda."""
    if agrupar_por not in _COLUMNAS_SEGMENTO_PRINCIPAL:
        raise ValueError(f"Columna no permitida para agrupar: {agrupar_por}")
    cliente = cliente_bigquery()
    columna_agrupacion = _TIPO_NEGOCIO_EFECTIVO if agrupar_por == "nb_TipoDeNegocio" else agrupar_por
    query = f"""
        SELECT 
            {columna_agrupacion} AS etiqueta, SUM(im_Movimiento) AS total
        FROM 
            `{TABLA}`
        WHERE 
            nb_Empresa IN UNNEST(@empresas)
        AND ({_TIPO_NEGOCIO_EFECTIVO}) IN UNNEST(@tipos_negocio)
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
    _TIPO_NEGOCIO_EFECTIVO). Aquí NO se excluyen sucursales de GAS/Autotanque
    — son precisamente el objeto de esta vista. Ordenado de mayor a menor."""
    cliente = cliente_bigquery()
    query = f"""
        SELECT 
            nb_sucursal AS etiqueta, SUM(im_Movimiento) AS total
        FROM 
            `{TABLA}`
        WHERE 
            nb_Empresa IN UNNEST(@empresas)
        AND ({_TIPO_NEGOCIO_EFECTIVO}) = 'GasPetroil'
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
    cliente = cliente_bigquery()
    query = f"""
        SELECT 
            nb_Empresa AS empresa, IFNULL(nb_sucursal, '(Sin sucursal)') AS sucursal, SUM(im_Movimiento) AS total
        FROM 
            `{TABLA}`
        WHERE 
            nb_Empresa NOT IN UNNEST(@empresas_principales)
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
    cliente = cliente_bigquery()
    query = f"""
        SELECT 
            SUM(im_Movimiento) AS total
        FROM `{TABLA}`
        WHERE 
            sn_Identificada = 'NO'
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


def _tarjeta_total(etiqueta: str, total: float, color: str, es_total: bool = False) -> ft.Container:
    """Stat tile: label (sentence case, sin dos puntos) + valor compacto. El
    color de la entidad vive en el acento, nunca en el texto (el texto siempre
    usa tinta neutra, no el color de la marca de datos). `es_total=True` marca
    visualmente el resumen de la sección (borde completo + fondo teñido) para
    que no se confunda con una categoría más."""
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    etiqueta,
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    tooltip=etiqueta,
                ),
                ft.Text(_formato_compacto(total), size=20, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE),
            ],
            spacing=2,
        ),
        padding=ft.Padding(left=12, right=12, top=8, bottom=10),
        bgcolor=ft.Colors.with_opacity(0.08, color) if es_total else ft.Colors.SURFACE_CONTAINER_LOW,
        border=(
            ft.Border.all(1.5, color)
            if es_total
            else ft.Border(
                top=ft.BorderSide(3, color),
                left=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
                right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
                bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
            )
        ),
        border_radius=8,
        width=150,
    )


def _chip_total(total: float) -> ft.Container:
    """Pastilla discreta 'Total · $X' para la cabecera de cada sección."""
    return ft.Container(
        content=ft.Row(
            [
                ft.Text("Total", size=10, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(_formato_compacto(total), size=12, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE),
            ],
            spacing=6,
            tight=True,
        ),
        padding=ft.Padding(left=10, right=10, top=4, bottom=4),
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
        border_radius=20,
    )


def _hero_tile(etiqueta: str, valor, color: str, icono, subtexto: str = "") -> ft.Container:
    """Tarjeta grande de la banda superior (hero): ícono en acento + valor
    grande + etiqueta. `valor` puede ser una Exception (consulta fallida)."""
    if isinstance(valor, Exception):
        valor_texto, valor_color = "—", ft.Colors.ON_SURFACE_VARIANT
    else:
        valor_texto, valor_color = _formato_compacto(valor), ft.Colors.ON_SURFACE
    contenido = [
        ft.Row(
            [
                ft.Container(
                    ft.Icon(icono, color=color, size=18),
                    width=34, height=34, border_radius=10,
                    bgcolor=ft.Colors.with_opacity(0.14, color),
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Text(etiqueta, size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        ft.Text(valor_texto, size=26, weight=ft.FontWeight.W_700, color=valor_color),
    ]
    if subtexto:
        contenido.append(ft.Text(subtexto, size=10, color=ft.Colors.ON_SURFACE_VARIANT))
    return ft.Container(
        content=ft.Column(contenido, spacing=6),
        padding=16,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        border=ft.Border(
            top=ft.BorderSide(3, color),
            left=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
            right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
            bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
        ),
        border_radius=12,
        height=124,  # alto fijo → las 4 tarjetas del hero quedan uniformes
        col={"xs": 12, "sm": 6, "lg": 3},  # ancho responsivo: 4 por fila en pantallas anchas
    )


def _leyenda_fila(color: str, etiqueta: str, valor: float, total: float) -> ft.Row:
    pct = (valor / total * 100) if total else 0
    return ft.Row(
        [
            ft.Container(width=10, height=10, bgcolor=color, border_radius=5),
            ft.Text(etiqueta, size=11, color=ft.Colors.ON_SURFACE, expand=True,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=etiqueta),
            ft.Text(_formato_compacto(valor), size=11, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE),
            ft.Container(
                ft.Text(f"{pct:.0f}%", size=10, color=ft.Colors.ON_SURFACE_VARIANT),
                width=36, alignment=ft.Alignment.CENTER_RIGHT,
            ),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _construir_donut(items: list[tuple[str, float]], dark: bool) -> ft.Control:
    """Composición parte-todo (Empresa, Tipo de negocio): gráfica de DONA con el
    total en el centro y una leyenda (color · nombre · monto · %) al lado."""
    total = sum(v for _, v in items) or 1
    secciones = [
        fc.PieChartSection(
            value=float(valor) if valor > 0 else 0.0001,
            color=_color_slot(i, dark),
            radius=26,
            title="",
        )
        for i, (_etiqueta, valor) in enumerate(items)
    ]
    pie = fc.PieChart(sections=secciones, center_space_radius=44, sections_space=2, expand=True)
    centro = ft.Container(
        content=ft.Column(
            [
                ft.Text("Total", size=9, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(_formato_compacto(total), size=15, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE),
            ],
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        alignment=ft.Alignment.CENTER,
    )
    grafica = ft.Container(ft.Stack([pie, centro]), width=150, height=150)
    leyenda = ft.Column(
        [_leyenda_fila(_color_slot(i, dark), et, val, total) for i, (et, val) in enumerate(items)],
        spacing=10,
        expand=True,
    )
    return ft.Row(
        [grafica, ft.Container(leyenda, expand=True, padding=ft.Padding(left=8, right=0, top=6, bottom=6))],
        spacing=14,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        height=180,
    )


_ANCHO_TRACK = 190  # px del carril del ranking, a valor máximo (versión compacta)


def _construir_ranked_list(items: list[tuple[str, float]], dark: bool) -> ft.Control:
    """Ranking tipo leaderboard (muchas categorías: sucursales / empresa+sucursal):
    posición + nombre + barra sobre un carril tenue + monto. El carril de fondo da
    la escala visual sin necesidad de ejes; orden descendente (viene de la query).
    Column con scroll propio: nada se trunca."""
    color = _color_slot(0, dark)
    track_bg = ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE)
    max_total = max((v for _, v in items), default=0) or 1
    filas = []
    for i, (etiqueta, valor) in enumerate(items):
        ancho = max(3, round(_ANCHO_TRACK * (valor / max_total)))
        filas.append(
            ft.Row(
                [
                    ft.Container(
                        ft.Text(f"{i + 1}", size=10, color=ft.Colors.ON_SURFACE_VARIANT),
                        width=16, alignment=ft.Alignment.CENTER_RIGHT,
                    ),
                    ft.Container(
                        ft.Text(etiqueta, size=10, color=ft.Colors.ON_SURFACE,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=etiqueta),
                        width=104,
                    ),
                    ft.Container(
                        content=ft.Stack(
                            [
                                ft.Container(width=_ANCHO_TRACK, height=12, bgcolor=track_bg, border_radius=6),
                                ft.Container(width=ancho, height=12, bgcolor=color, border_radius=6,
                                             tooltip=_formato_compacto(valor)),
                            ]
                        ),
                        width=_ANCHO_TRACK, height=12,
                    ),
                    ft.Text(_formato_compacto(valor), size=10, weight=ft.FontWeight.W_500,
                            color=ft.Colors.ON_SURFACE),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
    return ft.Column(filas, spacing=8, scroll=ft.ScrollMode.AUTO, height=200)


def _estado_vacio() -> ft.Control:
    """Sin esto, un rango sin movimientos se veía como una pantalla en blanco
    (¿falló? ¿sigue cargando?) — un empty state explícito es parte del
    contrato de cualquier vista con datos, no un caso aparte."""
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.INBOX_OUTLINED, size=24, color=ft.Colors.OUTLINE_VARIANT),
                ft.Text("Sin movimientos en este rango", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.Alignment.CENTER,
        height=120,
    )


def _construir_tabla(items: list[tuple[str, float]], dark: bool, un_solo_color: bool) -> ft.Control:
    filas = []
    for i, (etiqueta, valor) in enumerate(items):
        color = _color_slot(0, dark) if un_solo_color else _color_slot(i, dark)
        filas.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(
                        ft.Row(
                            [
                                ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
                                ft.Text(etiqueta, size=11),
                            ],
                            spacing=6,
                        )
                    ),
                    ft.DataCell(ft.Text(f"${valor:,.2f}", size=11)),
                ]
            )
        )
    tabla = ft.DataTable(
        columns=[ft.DataColumn(ft.Text("Categoría", size=11)), ft.DataColumn(ft.Text("Total", size=11), numeric=True)],
        rows=filas,
        data_row_max_height=32,
        heading_row_height=32,
        column_spacing=16,
    )
    return ft.Column([tabla], scroll=ft.ScrollMode.AUTO, height=200)


# --- Definición de las secciones del dashboard ------------------------------
# (titulo, subtitulo, awaitable que regresa list[(etiqueta, total)])

async def _consultar_segmento(fecha_inicio: date, fecha_fin: date, columna: str) -> list[tuple[str, float]]:
    filas = await asyncio.to_thread(obtener_agregado_segmento_principal, fecha_inicio, fecha_fin, columna)
    return [(fila["etiqueta"], fila["total"] or 0) for fila in filas]


async def _consultar_sucursal_gas(fecha_inicio: date, fecha_fin: date) -> list[tuple[str, float]]:
    filas = await asyncio.to_thread(obtener_agregado_sucursal_gas, fecha_inicio, fecha_fin)
    return [(fila["etiqueta"], fila["total"] or 0) for fila in filas]


async def _consultar_otras_empresas(fecha_inicio: date, fecha_fin: date) -> list[tuple[str, float]]:
    filas = await asyncio.to_thread(obtener_agregado_otras_empresas, fecha_inicio, fecha_fin)
    return [(f"{fila['empresa']} · {fila['sucursal']}", fila["total"] or 0) for fila in filas]


async def _consultar_no_identificado(fecha_inicio: date, fecha_fin: date) -> float:
    return await asyncio.to_thread(obtener_total_no_identificado, fecha_inicio, fecha_fin)


# (titulo, subtitulo, consulta, vista) — vista: "donut" (composición parte-todo)
# o "ranked" (leaderboard con muchas categorías).
_SECCIONES = [
    (
        "Empresa",
        "Asociados y Distribuidora · excluye pagos entre filiales, GAS, Autotanque y sucursal sin asignar",
        lambda fi, ff: _consultar_segmento(fi, ff, "nb_Empresa"),
        "donut",
    ),
    (
        "Tipo de negocio",
        "Asociados y Distribuidora · excluye pagos entre filiales, GAS, Autotanque y sucursal sin asignar",
        lambda fi, ff: _consultar_segmento(fi, ff, "nb_TipoDeNegocio"),
        "donut",
    ),
    (
        "Sucursal",
        "Asociados y Distribuidora · excluye pagos entre filiales, GAS, Autotanque y sucursal sin asignar",
        lambda fi, ff: _consultar_segmento(fi, ff, "nb_sucursal"),
        "ranked",
    ),
    (
        "Sucursal (Gasolineras)",
        "Segmento GasPetroil · excluye pagos entre filiales",
        _consultar_sucursal_gas,
        "ranked",
    ),
    (
        "Otras empresas",
        "Fuera de Abastecedora / ACP Combustibles / Petro Smart · todos los tipos de negocio y sucursales",
        _consultar_otras_empresas,
        "ranked",
    ),
]

_ANCHO_SECCION = 640


def _construir_seccion(
    titulo: str, subtitulo: str, resultado, dark: bool, en_tabla: bool, vista: str = "ranked"
) -> ft.Container:
    """Una sección = cabecera (título + subtítulo + pastilla Total) + cuerpo
    (dona / ranking / tabla). `resultado` puede ser una lista de items, vacía, o
    una Exception (si esa consulta falló) — cada sección se degrada de forma
    independiente, un fallo no tira el resto del dashboard."""
    total_chip: ft.Control = ft.Container()
    if isinstance(resultado, Exception):
        cuerpo: ft.Control = ft.Container(
            content=ft.Text(f"No se pudo consultar: {resultado}", size=11, color=ft.Colors.RED_600),
            height=120,
            alignment=ft.Alignment.CENTER,
        )
    elif not resultado:
        cuerpo = _estado_vacio()
    else:
        items = resultado
        total = sum(v for _, v in items)
        total_chip = _chip_total(total)
        if en_tabla:
            cuerpo = _construir_tabla(items, dark, un_solo_color=(vista != "donut"))
        elif vista == "donut":
            cuerpo = _construir_donut(items, dark)
        else:
            cuerpo = _construir_ranked_list(items, dark)

    cabecera = ft.Row(
        [
            ft.Column(
                [
                    ft.Text(titulo, size=14, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE),
                    ft.Text(subtitulo, size=10, color=ft.Colors.ON_SURFACE_VARIANT,
                            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=2,
                expand=True,
            ),
            total_chip,
        ],
        vertical_alignment=ft.CrossAxisAlignment.START,
    )
    return ft.Container(
        content=ft.Column([cabecera, ft.Divider(height=1), cuerpo], spacing=10),
        padding=16,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=12,
        col={"xs": 12, "lg": 6},  # ancho responsivo: 2 secciones por fila en pantallas anchas
    )


def construir_tab_dashboard(page: ft.Page) -> tuple[ft.Tab, ft.Control]:
    """Pestaña 'Dashboard' de cobranza mensual: todas las vistas visibles a la
    vez (sin selector que oculte unas para ver otras), sobre un rango de
    fechas compartido.

    Devuelve (tab, contenido): el `Tab` va en `TabBar.tabs` y `contenido` en
    `TabBarView.controls`, en la misma posición (así arma las pestañas main.py)."""
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)
    rango_sel: list[tuple[date, date]] = [(primer_dia_mes, hoy)]
    resultados_actuales: list[list] = [[[] for _ in _SECCIONES] + [0]]
    en_tabla = [False]  # False = gráfica, True = tabla, aplica a todas las secciones a la vez

    # El diálogo compacto (entry_mode=INPUT) del DateRangePicker es angosto y el
    # texto grande del rango ("Jul 1 – Jul 7") se corta a 2 líneas con el tamaño
    # de fuente por defecto. No hay una prop por instancia para esto en
    # DateRangePicker — solo a nivel tema, así que se ajusta aquí (sin tocar
    # main.py) para claro y oscuro. (Nota: en modo INPUT este token de Flutter
    # no parece tener efecto — se deja por si acaso, es inofensivo.)
    _tema_date_picker = ft.DatePickerTheme(
        range_picker_header_headline_text_style=ft.TextStyle(size=18, weight=ft.FontWeight.W_600)
    )
    if page.theme is not None:
        page.theme = dataclasses.replace(page.theme, date_picker_theme=_tema_date_picker)
    if page.dark_theme is not None:
        page.dark_theme = dataclasses.replace(page.dark_theme, date_picker_theme=_tema_date_picker)

    def _dark() -> bool:
        return page.theme_mode == ft.ThemeMode.DARK

    def _texto_rango(inicio: date, fin: date) -> str:
        return f"{inicio.strftime('%d %b %Y')} – {fin.strftime('%d %b %Y')}"

    titulo = ft.Text("Ingresos Diversos Mensuales", size=20, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE)
    subtitulo = ft.Text(
        "Todas las vistas usan el mismo rango de fechas seleccionado abajo.",
        size=12,
        color=ft.Colors.ON_SURFACE_VARIANT,
    )

    estado_text = ft.Text("", size=12, color=ft.Colors.RED_600)
    progress = ft.ProgressRing(width=16, height=16, visible=False, stroke_width=2)

    # Banda superior de KPIs (hero) y grid de secciones. ResponsiveRow reparte el
    # ancho disponible en columnas (col por hijo) → los componentes se
    # redimensionan para ocupar TODO el ancho de la ventana.
    hero_contenedor = ft.ResponsiveRow(spacing=16, run_spacing=16)
    secciones_contenedor = ft.ResponsiveRow(
        spacing=16,
        run_spacing=16,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )
    cuerpo = ft.Column([hero_contenedor, secciones_contenedor], spacing=20, opacity=1.0, animate_opacity=200)

    def _total_seguro(resultado) -> float:
        return sum(v for _, v in resultado) if isinstance(resultado, list) else 0

    def _refrescar_todo() -> None:
        dark = _dark()
        *resultados_secciones, total_no_identificado = resultados_actuales[0]

        # Banda hero: los grandes indicadores del periodo.
        res_empresa = resultados_secciones[0] if resultados_secciones else []
        res_gas = resultados_secciones[3] if len(resultados_secciones) > 3 else []
        res_otras = resultados_secciones[4] if len(resultados_secciones) > 4 else []
        hero_contenedor.controls = [
            _hero_tile("Ingresos identificados", _total_seguro(res_empresa), _color_slot(0, dark),
                       ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED,
                       "Asociados y Distribuidora (segmento principal)"),
            _hero_tile("Gasolineras", _total_seguro(res_gas), _color_slot(1, dark),
                       ft.Icons.LOCAL_GAS_STATION_OUTLINED, "Segmento GasPetroil"),
            _hero_tile("Otras empresas", _total_seguro(res_otras), _color_slot(2, dark),
                       ft.Icons.BUSINESS_OUTLINED, "Fuera del segmento principal"),
            _hero_tile("Sin identificar", total_no_identificado, "#e34948",
                       ft.Icons.HELP_OUTLINE, "sn_Identificada = NO en el rango"),
        ]

        secciones_contenedor.controls = [
            _construir_seccion(titulo_s, subtitulo_s, resultado, dark, en_tabla[0], vista)
            for (titulo_s, subtitulo_s, _consulta, vista), resultado in zip(_SECCIONES, resultados_secciones)
        ]

    async def cargar(_e=None) -> None:
        cuerpo.opacity = 0.5  # mantiene el render anterior visible (sin salto de layout) mientras carga
        progress.visible = True
        estado_text.value = ""
        boton_rango.disabled = True
        page.update()

        fecha_inicio, fecha_fin = rango_sel[0]
        resultados = await asyncio.gather(
            *(consulta(fecha_inicio, fecha_fin) for _titulo, _subtitulo, consulta, _vista in _SECCIONES),
            _consultar_no_identificado(fecha_inicio, fecha_fin),
            return_exceptions=True,
        )
        resultados_actuales[0] = resultados
        _refrescar_todo()

        progress.visible = False
        boton_rango.disabled = False
        cuerpo.opacity = 1.0
        if any(isinstance(r, Exception) for r in resultados):
            estado_text.value = "Algunas secciones no se pudieron consultar (ver detalle en cada tarjeta)."
        page.update()

    def on_cambiar_rango(e) -> None:
        picker = e.control
        if not picker.start_value or not picker.end_value:
            return
        inicio = picker.start_value.date()
        fin = picker.end_value.date()
        rango_sel[0] = (inicio, fin)
        boton_rango.content.controls[1].value = _texto_rango(inicio, fin)
        page.update()
        page.run_task(cargar)

    date_range_picker = ft.DateRangePicker(
        first_date=datetime(2020, 1, 1),
        last_date=datetime(2035, 12, 31),
        start_value=datetime.combine(primer_dia_mes, datetime.min.time()),
        end_value=datetime.combine(hoy, datetime.min.time()),
        # Calendario siempre visible (sin modo escritura): más intuitivo para el
        # usuario — se eligen las fechas tocando los días directamente.
        entry_mode=ft.DatePickerEntryMode.CALENDAR_ONLY,
        on_change=on_cambiar_rango,
    )

    # Botón-chip discreto: icono + rango actual, en vez de un botón + texto suelto.
    boton_rango = ft.OutlinedButton(
        content=ft.Row(
            [ft.Icon(ft.Icons.DATE_RANGE, size=16), ft.Text(_texto_rango(primer_dia_mes, hoy), size=13)],
            spacing=8,
            tight=True,
        ),
        style=ft.ButtonStyle(padding=ft.Padding(left=12, right=12, top=6, bottom=6)),
        on_click=lambda _e: page.show_dialog(date_range_picker),
    )

    def _alternar_vista_render(_e) -> None:
        en_tabla[0] = not en_tabla[0]
        boton_vista.icon = ft.Icons.BAR_CHART_OUTLINED if en_tabla[0] else ft.Icons.TABLE_CHART_OUTLINED
        boton_vista.tooltip = "Ver todo como gráfica" if en_tabla[0] else "Ver todo como tabla"
        _refrescar_todo()
        page.update()

    boton_vista = ft.IconButton(
        icon=ft.Icons.TABLE_CHART_OUTLINED,
        icon_size=18,
        tooltip="Ver todo como tabla",
        on_click=_alternar_vista_render,
    )

    barra_herramientas = ft.Row(
        [boton_rango, progress, estado_text, ft.Container(expand=True), boton_vista],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    contenido = ft.Container(
        content=ft.Column(
            [
                ft.Column([titulo, subtitulo], spacing=2),
                barra_herramientas,
                ft.Container(content=cuerpo, expand=True),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=20,
        expand=True,
    )

    page.run_task(cargar)

    tab = ft.Tab(label="Dashboard Ingresos", icon=ft.Icons.BAR_CHART)
    return tab, contenido
