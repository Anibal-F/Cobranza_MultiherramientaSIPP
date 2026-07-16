"""Panel 'Cobranza' (mitad derecha de la sub-pestaña Proyección): lo
efectivamente cobrado en el periodo seleccionado (por defecto, la semana
anterior a hoy), sobre `Tableros.IgresosClientes`.

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

import asyncio
from datetime import date, datetime, timedelta

import flet as ft
from google.cloud import bigquery

from ..dashboard.componentes import (
    color_slot,
    encabezado_seccion,
    escribir_hoja_excel,
    guardar_workbook,
    mostrar_dialogo,
    nombre_hoja_valido,
    sombra_tarjeta,
    tile_compacta,
)
from ..services.bigquery_cliente import cliente_bigquery

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


def _parametros_cobranza(fecha_inicio: date, fecha_fin: date) -> list:
    parametros = [
        bigquery.ArrayQueryParameter("razon_social_excluida", "STRING", RAZON_SOCIAL_EXCLUIDA),
        bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
        bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        bigquery.ScalarQueryParameter("moneda_usd", "STRING", MONEDA_USD),
    ]
    for i, palabra in enumerate(SUCURSAL_EXCLUIDA_CONTIENE):
        parametros.append(bigquery.ScalarQueryParameter(f"sucursal_excluida_{i}", "STRING", f"%{palabra}%"))
    return parametros


def _condiciones_sucursal() -> str:
    return " AND ".join(
        f"NOT LOWER(IFNULL(nb_sucursal, '')) LIKE @sucursal_excluida_{i}"
        for i in range(len(SUCURSAL_EXCLUIDA_CONTIENE))
    )


def obtener_cobranza_por_segmento(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Total cobrado (im_Movimiento) por segmento en [fecha_inicio, fecha_fin]
    (sobre fh_Envio), separando USD, su conversión y el total final en MXN."""
    cliente = cliente_bigquery()
    query = f"""
        WITH {_CTE_FX_DIARIO},
        filas AS (
            SELECT
                {_SEGMENTO_POR_FILA} AS segmento,
                DATE(fh_Envio) AS fecha,
                im_Movimiento,
                nb_Moneda
            FROM `{TABLA}`
            WHERE UPPER(TRIM(de_RazonSocial)) NOT IN UNNEST(@razon_social_excluida)
              AND {_condiciones_sucursal()}
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
    job_config = bigquery.QueryJobConfig(query_parameters=_parametros_cobranza(fecha_inicio, fecha_fin))
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


async def consultar_cobranza_por_segmento(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    return await asyncio.to_thread(obtener_cobranza_por_segmento, fecha_inicio, fecha_fin)


def obtener_ingresos_significativos(
    fecha_inicio: date, fecha_fin: date, segmento: str | None = None
) -> list[dict]:
    """Top 20 de ingresos agregados por razón social y ordenados por su total
    final en MXN. `segmento=None` incluye los tres segmentos."""
    cliente = cliente_bigquery()
    query = f"""
        WITH {_CTE_FX_DIARIO},
        filas AS (
            SELECT
                {_SEGMENTO_POR_FILA} AS segmento,
                TRIM(de_RazonSocial) AS razon_social,
                DATE(fh_Envio) AS fecha,
                im_Movimiento,
                nb_Moneda
            FROM `{TABLA}`
            WHERE UPPER(TRIM(de_RazonSocial)) NOT IN UNNEST(@razon_social_excluida)
              AND {_condiciones_sucursal()}
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
              AND (@segmento IS NULL OR segmento = @segmento)
              AND razon_social IS NOT NULL
              AND razon_social != ''
            GROUP BY razon_social
        )
        SELECT *, total_mxn + total_usd_convertido AS total_final
        FROM agregados
        ORDER BY total_final DESC
        LIMIT 20
    """
    parametros = _parametros_cobranza(fecha_inicio, fecha_fin)
    parametros.append(bigquery.ScalarQueryParameter("segmento", "STRING", segmento))
    filas = cliente.query(
        query,
        job_config=bigquery.QueryJobConfig(query_parameters=parametros),
    ).result()
    return [dict(fila.items()) for fila in filas]


async def consultar_ingresos_significativos(
    fecha_inicio: date, fecha_fin: date, segmento: str | None = None
) -> list[dict]:
    return await asyncio.to_thread(obtener_ingresos_significativos, fecha_inicio, fecha_fin, segmento)


def obtener_ingresos_por_dia(
    fecha_inicio: date, fecha_fin: date, segmento: str | None = None
) -> list[dict]:
    """Ingresos agregados por día dentro del periodo y segmento seleccionados,
    con el mismo esquema monetario del concentrado y del Top 20."""
    cliente = cliente_bigquery()
    query = f"""
        WITH {_CTE_FX_DIARIO},
        filas AS (
            SELECT
                {_SEGMENTO_POR_FILA} AS segmento,
                DATE(fh_Envio) AS fecha,
                im_Movimiento,
                nb_Moneda
            FROM `{TABLA}`
            WHERE UPPER(TRIM(de_RazonSocial)) NOT IN UNNEST(@razon_social_excluida)
              AND {_condiciones_sucursal()}
              AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        ),
        {_CTE_FX_CERCANO}
        SELECT
            filas.fecha,
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
          AND (@segmento IS NULL OR segmento = @segmento)
        GROUP BY filas.fecha
        ORDER BY filas.fecha
    """
    parametros = _parametros_cobranza(fecha_inicio, fecha_fin)
    parametros.append(bigquery.ScalarQueryParameter("segmento", "STRING", segmento))
    filas = cliente.query(
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


async def consultar_ingresos_por_dia(
    fecha_inicio: date, fecha_fin: date, segmento: str | None = None
) -> list[dict]:
    return await asyncio.to_thread(obtener_ingresos_por_dia, fecha_inicio, fecha_fin, segmento)


def construir_panel_cobranza(page: ft.Page) -> ft.Control:
    """Contenido del panel de Cobranza (sin Tab propio — vive dentro de la
    mitad derecha de la sub-pestaña Proyección, ver app/rdc/vista.py)."""
    hoy = date.today()
    hace_una_semana = hoy - timedelta(days=7)
    rango_sel: list[tuple[date, date]] = [(hace_una_semana, hoy)]
    segmento_significativos: list[str | None] = [None]

    def _dark() -> bool:
        return page.theme_mode == ft.ThemeMode.DARK

    def _texto_rango(inicio: date, fin: date) -> str:
        return f"{inicio.strftime('%d %b %Y')} – {fin.strftime('%d %b %Y')}"

    titulo = ft.Text("Cobranza", size=20, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE)
    subtitulo = ft.Text(
        "Distribuidora, Asociados y Petroplazas · lo efectivamente cobrado (im_Movimiento) "
        "en el periodo seleccionado.",
        size=12,
        color=ft.Colors.ON_SURFACE_VARIANT,
    )

    estado_text = ft.Text("", size=12, color=ft.Colors.RED_600)
    progress = ft.ProgressRing(width=16, height=16, visible=False, stroke_width=2)

    hero_contenedor = ft.ResponsiveRow(spacing=10, run_spacing=10)
    seccion_detalle = ft.Container()
    seccion_significativos = ft.Container()
    cuerpo = ft.Column(
        [hero_contenedor, seccion_detalle, seccion_significativos],
        spacing=16,
        opacity=1.0,
        animate_opacity=200,
    )

    # En Flet 0.85 el FilePicker es un servicio: se crea y se usa directamente
    # (NO se agrega a page.overlay; hacerlo provoca "Unknown control: FilePicker").
    file_picker = ft.FilePicker()

    # Datos de la última consulta exitosa, para exportar sin recalcular. Se
    # habilita el botón de descarga solo si las 3 consultas (concentrado,
    # significativos, por día) tuvieron éxito a la vez.
    ultimo_datos: dict = {"items": None, "significativos": None, "por_dia": None}

    def _refrescar(resultado, significativos, por_dia) -> None:
        dark = _dark()
        if isinstance(resultado, Exception):
            ultimo_datos["items"] = None
            hero_contenedor.controls = []
            seccion_detalle.content = ft.Container(
                content=ft.Text(f"No se pudo consultar: {resultado}", size=12, color=ft.Colors.RED_600),
                height=120,
                alignment=ft.Alignment.CENTER,
            )
        else:
            por_segmento = {fila["segmento"]: fila for fila in resultado}
            items = [
                (
                    segmento,
                    (por_segmento.get(segmento, {}).get("total_mxn") or 0),
                    (por_segmento.get(segmento, {}).get("total_usd") or 0),
                    (por_segmento.get(segmento, {}).get("total_usd_convertido") or 0),
                    (por_segmento.get(segmento, {}).get("total_usd_sin_tc") or 0),
                )
                for segmento in SEGMENTOS
            ]
            ultimo_datos["items"] = items
            total_mxn = sum(v for _s, v, _u, _c, _st in items)
            total_usd = sum(u for _s, _v, u, _c, _st in items)
            total_convertido = sum(c for _s, _v, _u, c, _st in items)
            total_sin_tc = sum(st for _s, _v, _u, _c, st in items)
            total_final = total_mxn + total_convertido

            hero_contenedor.controls = [
                tile_compacta("Total cobrado", total_final, color_slot(2, dark), ft.Icons.PAYMENTS_OUTLINED,
                               "MXN + USD convertido"),
                tile_compacta("Distribuidora", items[0][1] + items[0][3], color_slot(0, dark),
                               ft.Icons.LOCAL_SHIPPING_OUTLINED),
                tile_compacta("Asociados", items[1][1] + items[1][3], color_slot(1, dark),
                               ft.Icons.HANDSHAKE_OUTLINED),
                tile_compacta("Petroplazas", items[2][1] + items[2][3], color_slot(4, dark),
                               ft.Icons.LOCAL_GAS_STATION_OUTLINED),
            ]

            filas_tabla = []
            for segmento, valor_mxn, valor_usd, convertido, sin_tc in items:
                filas_tabla.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(segmento, size=11)),
                    ft.DataCell(ft.Text(f"${valor_mxn:,.2f}", size=11)),
                    ft.DataCell(ft.Text(f"US${valor_usd:,.2f}" if valor_usd else "—", size=11)),
                    ft.DataCell(ft.Text(f"${convertido:,.2f}" if convertido else "—", size=11)),
                    ft.DataCell(ft.Text(f"${valor_mxn + convertido:,.2f}", size=11,
                                        weight=ft.FontWeight.W_600)),
                    ft.DataCell(ft.Text(f"US${sin_tc:,.2f}" if sin_tc else "—", size=11)),
                ]))
            filas_tabla.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text("Total", size=11, weight=ft.FontWeight.W_700)),
                ft.DataCell(ft.Text(f"${total_mxn:,.2f}", size=11, weight=ft.FontWeight.W_700)),
                ft.DataCell(ft.Text(f"US${total_usd:,.2f}" if total_usd else "—", size=11,
                                    weight=ft.FontWeight.W_700)),
                ft.DataCell(ft.Text(f"${total_convertido:,.2f}" if total_convertido else "—", size=11,
                                    weight=ft.FontWeight.W_700)),
                ft.DataCell(ft.Text(f"${total_final:,.2f}", size=11, weight=ft.FontWeight.W_700)),
                ft.DataCell(ft.Text(f"US${total_sin_tc:,.2f}" if total_sin_tc else "—", size=11,
                                    weight=ft.FontWeight.W_700)),
            ]))
            tabla = ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Text("Segmento", size=11)),
                    ft.DataColumn(ft.Text("MXN", size=11), numeric=True),
                    ft.DataColumn(ft.Text("USD", size=11), numeric=True),
                    ft.DataColumn(ft.Text("MXN convertido", size=11), numeric=True),
                    ft.DataColumn(ft.Text("Total final", size=11), numeric=True),
                    ft.DataColumn(ft.Text("USD sin TC", size=11), numeric=True),
                ],
                rows=filas_tabla,
                data_row_max_height=34,
                heading_row_height=34,
                column_spacing=16,
            )
            seccion_detalle.content = ft.Container(
                content=ft.Column(
                    [
                        encabezado_seccion(ft.Icons.TABLE_CHART_OUTLINED, color_slot(3, dark),
                                           "Detalle por segmento", "MXN, USD y su conversión por segmento"),
                        ft.Row([tabla], scroll=ft.ScrollMode.AUTO),
                    ],
                    spacing=10,
                ),
                padding=16,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                shadow=sombra_tarjeta(),
            )

        if isinstance(significativos, Exception) or isinstance(por_dia, Exception):
            ultimo_datos["significativos"] = None
            ultimo_datos["por_dia"] = None
            errores = [
                str(error)
                for error in (significativos, por_dia)
                if isinstance(error, Exception)
            ]
            seccion_significativos.content = ft.Container(
                content=ft.Text(f"No se pudo consultar Ingresos Significativos: {' · '.join(errores)}",
                                size=12, color=ft.Colors.RED_600),
                height=120,
                alignment=ft.Alignment.CENTER,
            )
        else:
            ultimo_datos["significativos"] = significativos
            ultimo_datos["por_dia"] = por_dia
            filas_top = []
            for puesto, fila in enumerate(significativos, start=1):
                sin_tc = fila.get("total_usd_sin_tc") or 0
                filas_top.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(str(puesto), size=10)),
                    ft.DataCell(ft.Container(
                        ft.Text(fila["razon_social"], size=10, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS, tooltip=fila["razon_social"]),
                        width=210,
                    )),
                    ft.DataCell(ft.Text(fila.get("segmento") or "—", size=10)),
                    ft.DataCell(ft.Text(f"${(fila.get('total_mxn') or 0):,.2f}", size=10)),
                    ft.DataCell(ft.Text(
                        f"US${(fila.get('total_usd') or 0):,.2f}" if fila.get("total_usd") else "—", size=10
                    )),
                    ft.DataCell(ft.Text(
                        f"${(fila.get('total_usd_convertido') or 0):,.2f}"
                        if fila.get("total_usd_convertido") else "—", size=10
                    )),
                    ft.DataCell(ft.Text(f"${(fila.get('total_final') or 0):,.2f}", size=10,
                                        weight=ft.FontWeight.W_600)),
                    ft.DataCell(ft.Text(f"US${sin_tc:,.2f}" if sin_tc else "—", size=10,
                                        color=ft.Colors.RED_600 if sin_tc else ft.Colors.ON_SURFACE_VARIANT)),
                ]))
            tabla_top = ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Text("#", size=10), numeric=True),
                    ft.DataColumn(ft.Text("Razón social", size=10)),
                    ft.DataColumn(ft.Text("Tipo de negocio", size=10)),
                    ft.DataColumn(ft.Text("MXN", size=10), numeric=True),
                    ft.DataColumn(ft.Text("USD", size=10), numeric=True),
                    ft.DataColumn(ft.Text("MXN convertido", size=10), numeric=True),
                    ft.DataColumn(ft.Text("Total final", size=10), numeric=True),
                    ft.DataColumn(ft.Text("USD sin TC", size=10), numeric=True),
                ],
                rows=filas_top,
                data_row_max_height=32,
                heading_row_height=34,
                column_spacing=14,
            )

            filas_dia = []
            for fila in por_dia:
                sin_tc = fila.get("total_usd_sin_tc") or 0
                filas_dia.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(fila["fecha"].strftime("%d/%m/%Y"), size=10)),
                    ft.DataCell(ft.Text(f"${(fila.get('total_mxn') or 0):,.2f}", size=10)),
                    ft.DataCell(ft.Text(
                        f"US${(fila.get('total_usd') or 0):,.2f}" if fila.get("total_usd") else "—", size=10
                    )),
                    ft.DataCell(ft.Text(
                        f"${(fila.get('total_usd_convertido') or 0):,.2f}"
                        if fila.get("total_usd_convertido") else "—", size=10
                    )),
                    ft.DataCell(ft.Text(f"${(fila.get('total_final') or 0):,.2f}", size=10,
                                        weight=ft.FontWeight.W_600)),
                    ft.DataCell(ft.Text(f"US${sin_tc:,.2f}" if sin_tc else "—", size=10,
                                        color=ft.Colors.RED_600 if sin_tc else ft.Colors.ON_SURFACE_VARIANT)),
                ]))
            tabla_dia = ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Text("Fecha", size=10)),
                    ft.DataColumn(ft.Text("MXN", size=10), numeric=True),
                    ft.DataColumn(ft.Text("USD", size=10), numeric=True),
                    ft.DataColumn(ft.Text("MXN convertido", size=10), numeric=True),
                    ft.DataColumn(ft.Text("Total final", size=10), numeric=True),
                    ft.DataColumn(ft.Text("USD sin TC", size=10), numeric=True),
                ],
                rows=filas_dia,
                data_row_max_height=32,
                heading_row_height=34,
                column_spacing=14,
            )
            seccion_significativos.content = ft.Container(
                content=ft.Column(
                    [
                        encabezado_seccion(
                            ft.Icons.STAR_OUTLINE, color_slot(4, dark),
                            "Ingresos Significativos", "Top 20 agregado por razón social · ordenado por total final en MXN",
                            [filtro_segmento],
                        ),
                        ft.Divider(height=1),
                        ft.Row([tabla_top], scroll=ft.ScrollMode.AUTO),
                        ft.Divider(height=1),
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.CALENDAR_VIEW_DAY_OUTLINED, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                                ft.Text("Ingresos por día", size=13, weight=ft.FontWeight.W_600,
                                        color=ft.Colors.ON_SURFACE),
                            ],
                            spacing=6,
                        ),
                        ft.Text(
                            "Desglose diario de todos los ingresos del periodo con el filtro seleccionado.",
                            size=10,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Row([tabla_dia], scroll=ft.ScrollMode.AUTO),
                    ],
                    spacing=10,
                ),
                padding=16,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=12,
                shadow=sombra_tarjeta(),
            )

        boton_exportar.disabled = any(valor is None for valor in ultimo_datos.values())

    async def cargar(_e=None) -> None:
        cuerpo.opacity = 0.5
        progress.visible = True
        estado_text.value = ""
        boton_rango.disabled = True
        page.update()

        fecha_inicio, fecha_fin = rango_sel[0]
        resultado, significativos, por_dia = await asyncio.gather(
            consultar_cobranza_por_segmento(fecha_inicio, fecha_fin),
            consultar_ingresos_significativos(fecha_inicio, fecha_fin, segmento_significativos[0]),
            consultar_ingresos_por_dia(fecha_inicio, fecha_fin, segmento_significativos[0]),
            return_exceptions=True,
        )

        _refrescar(resultado, significativos, por_dia)

        progress.visible = False
        boton_rango.disabled = False
        cuerpo.opacity = 1.0
        if any(isinstance(valor, Exception) for valor in (resultado, significativos, por_dia)):
            estado_text.value = "No se pudo consultar BigQuery (ver detalle abajo)."
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
        start_value=datetime.combine(hace_una_semana, datetime.min.time()),
        end_value=datetime.combine(hoy, datetime.min.time()),
        entry_mode=ft.DatePickerEntryMode.CALENDAR_ONLY,
        on_change=on_cambiar_rango,
    )

    boton_rango = ft.OutlinedButton(
        content=ft.Row(
            [ft.Icon(ft.Icons.DATE_RANGE, size=16), ft.Text(_texto_rango(hace_una_semana, hoy), size=13)],
            spacing=8,
            tight=True,
        ),
        style=ft.ButtonStyle(padding=ft.Padding(left=12, right=12, top=6, bottom=6)),
        on_click=lambda _e: page.show_dialog(date_range_picker),
    )

    def on_cambiar_segmento(e) -> None:
        segmento_significativos[0] = None if e.control.value == "Todos" else e.control.value
        page.run_task(cargar)

    filtro_segmento = ft.Dropdown(
        label="Tipo de negocio",
        value="Todos",
        width=170,
        dense=True,
        options=[ft.dropdown.Option("Todos")] + [ft.dropdown.Option(segmento) for segmento in SEGMENTOS],
        on_select=on_cambiar_segmento,
    )

    def _abrir_info(_e) -> None:
        lineas = [
            "Se excluyen registros cuya Razón Social sea exactamente 'Abastecedora de Combustibles "
            "del Pacifico', 'ACP Combustibles' o 'Petro Smart Combustibles'.",
            "Los registros cuya Razón Social empiece con 'Petroplazas' (incluye variantes como "
            "PETROPLAZAS AEROPUERTO o PETROPLAZAS ESTACIONES) se cuentan como segmento 'Petroplazas', "
            "sin importar su tipo de negocio original.",
            "Se excluye el tipo de negocio 'GasPetroil' — salvo que ya se haya reclasificado como "
            "Petroplazas por el punto anterior.",
            "Se excluyen las sucursales cuyo nombre contenga 'GAS', 'AUTOTANQUE', 'GC' o 'Corporativo'.",
            "Los registros en dólares se muestran separados y se convierten a MXN con el promedio "
            "diario de im_TipoCambio en DocumentosClientesCobranza. Si no existe tipo de cambio en "
            "la fecha exacta, se usa la fecha disponible más cercana.",
            "Ingresos Significativos muestra los 20 mayores ingresos agregados por Razón Social, "
            "ordenados por Total final = MXN + USD convertido. Puede filtrarse por Distribuidora, "
            "Asociados, Petroplazas o Todos.",
            "Dentro de Ingresos Significativos, el apartado Ingresos por día agrega todos los "
            "movimientos de cada fecha del periodo y respeta el mismo filtro de tipo de negocio "
            "y el mismo tratamiento de MXN, USD, conversión y USD sin tipo de cambio.",
            "La fecha usada para filtrar es fh_Envio; por defecto se muestra la semana anterior a hoy "
            "(espejo de la semana a futuro que muestra el panel de Proyección, a la izquierda).",
        ]
        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Cómo se calculan estos datos"),
            content=ft.Container(
                content=ft.Column(
                    [ft.Text(f"•  {l}", size=12, selectable=True) for l in lineas],
                    spacing=10, scroll=ft.ScrollMode.AUTO,
                ),
                width=480, height=340,
            ),
            actions=[ft.TextButton("Cerrar", on_click=lambda _e: page.pop_dialog())],
        )
        mostrar_dialogo(page, dialogo)

    boton_info = ft.IconButton(
        icon=ft.Icons.INFO_OUTLINE,
        icon_size=18,
        tooltip="Ver cómo se calculan estos datos",
        on_click=_abrir_info,
    )

    async def exportar_excel(_e) -> None:
        """Descarga un Excel con las 3 vistas del panel (mismos datos que ya
        están en pantalla, sin volver a consultar BigQuery): concentrado por
        segmento, Top 20 de Ingresos Significativos e Ingresos por día."""
        items = ultimo_datos["items"]
        significativos = ultimo_datos["significativos"]
        por_dia = ultimo_datos["por_dia"]
        if items is None or significativos is None or por_dia is None:
            return
        boton_exportar.disabled = True
        estado_text.value = "Generando Excel…"
        page.update()

        import openpyxl

        wb = openpyxl.Workbook()
        nombres_usados: set = set()

        ws_concentrado = wb.active
        ws_concentrado.title = nombre_hoja_valido("Concentrado", nombres_usados)
        total_mxn = sum(mxn for _s, mxn, _u, _c, _st in items)
        total_usd = sum(usd for _s, _m, usd, _c, _st in items)
        total_convertido = sum(c for _s, _m, _u, c, _st in items)
        total_sin_tc = sum(st for _s, _m, _u, _c, st in items)
        filas_concentrado = [
            [segmento, round(mxn, 2), round(usd, 2), round(convertido, 2),
             round(mxn + convertido, 2), round(sin_tc, 2)]
            for segmento, mxn, usd, convertido, sin_tc in items
        ]
        filas_concentrado.append([
            "Total", round(total_mxn, 2), round(total_usd, 2), round(total_convertido, 2),
            round(total_mxn + total_convertido, 2), round(total_sin_tc, 2),
        ])
        escribir_hoja_excel(
            ws_concentrado,
            ["Segmento", "MXN", "USD", "MXN convertido", "Total final", "USD sin TC"],
            filas_concentrado,
        )
        for fila_celdas in ws_concentrado.iter_rows(min_row=2, min_col=2, max_col=6):
            for celda in fila_celdas:
                celda.number_format = "#,##0.00"

        ws_top = wb.create_sheet(nombre_hoja_valido("Ingresos Significativos", nombres_usados))
        escribir_hoja_excel(
            ws_top,
            ["#", "Razón social", "Tipo de negocio", "MXN", "USD", "MXN convertido", "Total final", "USD sin TC"],
            [
                [
                    puesto,
                    fila["razon_social"],
                    fila.get("segmento") or "",
                    round(fila.get("total_mxn") or 0, 2),
                    round(fila.get("total_usd") or 0, 2),
                    round(fila.get("total_usd_convertido") or 0, 2),
                    round(fila.get("total_final") or 0, 2),
                    round(fila.get("total_usd_sin_tc") or 0, 2),
                ]
                for puesto, fila in enumerate(significativos, start=1)
            ],
        )
        for fila_celdas in ws_top.iter_rows(min_row=2, min_col=4, max_col=8):
            for celda in fila_celdas:
                celda.number_format = "#,##0.00"

        ws_dia = wb.create_sheet(nombre_hoja_valido("Ingresos por dia", nombres_usados))
        escribir_hoja_excel(
            ws_dia,
            ["Fecha", "MXN", "USD", "MXN convertido", "Total final", "USD sin TC"],
            [
                [
                    fila["fecha"],
                    round(fila.get("total_mxn") or 0, 2),
                    round(fila.get("total_usd") or 0, 2),
                    round(fila.get("total_usd_convertido") or 0, 2),
                    round(fila.get("total_final") or 0, 2),
                    round(fila.get("total_usd_sin_tc") or 0, 2),
                ]
                for fila in por_dia
            ],
        )
        for fila_celdas in ws_dia.iter_rows(min_row=2, min_col=1, max_col=1):
            for celda in fila_celdas:
                celda.number_format = "dd/mm/yyyy"
        for fila_celdas in ws_dia.iter_rows(min_row=2, min_col=2, max_col=6):
            for celda in fila_celdas:
                celda.number_format = "#,##0.00"

        fecha_inicio, fecha_fin = rango_sel[0]
        nombre_def = f"rdc_cobranza_{fecha_inicio:%Y%m%d}_{fecha_fin:%Y%m%d}.xlsx"
        ok, mensaje = await guardar_workbook(page, file_picker, wb, nombre_def)
        boton_exportar.disabled = False
        estado_text.value = mensaje
        page.update()

    boton_exportar = ft.IconButton(
        icon=ft.Icons.DOWNLOAD,
        icon_size=18,
        tooltip="Descargar Excel (concentrado + ingresos significativos + por día)",
        disabled=True,
        on_click=lambda e: page.run_task(exportar_excel, e),
    )

    barra_herramientas = ft.Container(
        content=ft.Row(
            [boton_rango, progress, estado_text, ft.Container(expand=True), boton_exportar, boton_info],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding(left=14, right=14, top=10, bottom=10),
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border_radius=12,
    )

    contenido = ft.Column(
        [
            ft.Column([titulo, subtitulo], spacing=2),
            barra_herramientas,
            ft.Container(content=cuerpo, expand=True),
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    page.run_task(cargar)

    return contenido
