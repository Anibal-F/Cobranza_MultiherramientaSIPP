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
- Los registros en dólares (nb_Moneda = 'Dolar (USD)') se muestran aparte,
  como un total informativo — no se convierten ni se suman a los montos en
  pesos de Distribuidora/Asociados/Petroplazas (a diferencia de la macro de
  Excel original, que los cruzaba contra un Informe de Cobranza aparte para
  convertirlos; por ahora solo se reporta el total en USD).
- La columna Movimiento (im_Movimiento) es la que se suma, agrupada por
  segmento, filtrada por fh_Envio dentro del rango seleccionado.
"""

import asyncio
from datetime import date, datetime, timedelta

import flet as ft
from google.cloud import bigquery

from ..dashboard.componentes import color_slot, formato_compacto, mostrar_dialogo
from ..services.bigquery_cliente import cliente_bigquery

TABLA = "sipp-app.Tableros.IgresosClientes"

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


def obtener_cobranza_por_segmento(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Total cobrado (im_Movimiento) por segmento en [fecha_inicio, fecha_fin]
    (sobre fh_Envio), separando el total en pesos del total en dólares."""
    cliente = cliente_bigquery()
    condiciones_sucursal = " AND ".join(
        f"NOT LOWER(IFNULL(nb_sucursal, '')) LIKE @sucursal_excluida_{i}"
        for i in range(len(SUCURSAL_EXCLUIDA_CONTIENE))
    )
    query = f"""
        WITH filas AS (
            SELECT
                {_SEGMENTO_POR_FILA} AS segmento,
                im_Movimiento,
                nb_Moneda
            FROM `{TABLA}`
            WHERE UPPER(TRIM(de_RazonSocial)) NOT IN UNNEST(@razon_social_excluida)
              AND {condiciones_sucursal}
              AND DATE(fh_Envio) BETWEEN @fecha_inicio AND @fecha_fin
        )
        SELECT
            segmento,
            SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) != @moneda_usd THEN im_Movimiento ELSE 0 END) AS total_mxn,
            SUM(CASE WHEN LOWER(IFNULL(nb_Moneda, '')) = @moneda_usd THEN im_Movimiento ELSE 0 END) AS total_usd
        FROM filas
        WHERE segmento IS NOT NULL
        GROUP BY segmento
    """
    parametros = [
        bigquery.ArrayQueryParameter("razon_social_excluida", "STRING", RAZON_SOCIAL_EXCLUIDA),
        bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
        bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        bigquery.ScalarQueryParameter("moneda_usd", "STRING", MONEDA_USD),
    ]
    for i, palabra in enumerate(SUCURSAL_EXCLUIDA_CONTIENE):
        parametros.append(bigquery.ScalarQueryParameter(f"sucursal_excluida_{i}", "STRING", f"%{palabra}%"))
    job_config = bigquery.QueryJobConfig(query_parameters=parametros)
    filas = cliente.query(query, job_config=job_config).result()
    return [dict(fila.items()) for fila in filas]


async def consultar_cobranza_por_segmento(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    return await asyncio.to_thread(obtener_cobranza_por_segmento, fecha_inicio, fecha_fin)


def _tile_compacta(etiqueta: str, valor: float, color: str, icono, subtexto: str = "") -> ft.Container:
    """Tarjeta KPI compacta — la mitad derecha de la pantalla solo tiene la
    mitad del ancho disponible, así que no cabe el hero_tile de 4-across que
    usa el panel de la izquierda; esta versión es más chica y pensada para
    acomodar 2 por fila."""
    contenido = [
        ft.Row(
            [
                ft.Container(
                    ft.Icon(icono, color=color, size=15),
                    width=28, height=28, border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.14, color),
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Text(etiqueta, size=11, color=ft.Colors.ON_SURFACE_VARIANT, expand=True,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        ft.Text(formato_compacto(valor), size=19, weight=ft.FontWeight.W_700, color=ft.Colors.ON_SURFACE),
    ]
    if subtexto:
        contenido.append(ft.Text(subtexto, size=9.5, color=ft.Colors.ON_SURFACE_VARIANT,
                                  max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
    return ft.Container(
        content=ft.Column(contenido, spacing=5),
        padding=12,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        border=ft.Border(
            top=ft.BorderSide(3, color),
            left=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
            right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
            bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
        ),
        border_radius=10,
        col={"xs": 12, "sm": 6},
    )


def construir_panel_cobranza(page: ft.Page) -> ft.Control:
    """Contenido del panel de Cobranza (sin Tab propio — vive dentro de la
    mitad derecha de la sub-pestaña Proyección, ver app/rdc/vista.py)."""
    hoy = date.today()
    hace_una_semana = hoy - timedelta(days=7)
    rango_sel: list[tuple[date, date]] = [(hace_una_semana, hoy)]

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
    cuerpo = ft.Column([hero_contenedor, seccion_detalle], spacing=16, opacity=1.0, animate_opacity=200)

    def _refrescar(resultado) -> None:
        dark = _dark()
        if isinstance(resultado, Exception):
            hero_contenedor.controls = []
            seccion_detalle.content = ft.Container(
                content=ft.Text(f"No se pudo consultar: {resultado}", size=12, color=ft.Colors.RED_600),
                height=120,
                alignment=ft.Alignment.CENTER,
            )
            return

        por_segmento = {fila["segmento"]: fila for fila in resultado}
        items = [
            (
                segmento,
                (por_segmento.get(segmento, {}).get("total_mxn") or 0),
                (por_segmento.get(segmento, {}).get("total_usd") or 0),
            )
            for segmento in SEGMENTOS
        ]
        total_mxn = sum(v for _s, v, _u in items)
        total_usd = sum(u for _s, _v, u in items)

        hero_contenedor.controls = [
            _tile_compacta("Total cobrado", total_mxn, color_slot(2, dark), ft.Icons.PAYMENTS_OUTLINED,
                           "Distribuidora + Asociados + Petroplazas"),
            _tile_compacta("Distribuidora", items[0][1], color_slot(0, dark), ft.Icons.LOCAL_SHIPPING_OUTLINED),
            _tile_compacta("Asociados", items[1][1], color_slot(1, dark), ft.Icons.HANDSHAKE_OUTLINED),
            _tile_compacta("Petroplazas", items[2][1], color_slot(4, dark), ft.Icons.LOCAL_GAS_STATION_OUTLINED),
        ]

        color_usd = color_slot(3, dark)
        filas_tabla = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(segmento, size=12)),
                ft.DataCell(ft.Text(f"${valor_mxn:,.2f}", size=12)),
                ft.DataCell(ft.Text(f"US${valor_usd:,.2f}" if valor_usd else "—", size=12,
                                     color=color_usd if valor_usd else ft.Colors.ON_SURFACE_VARIANT)),
            ])
            for segmento, valor_mxn, valor_usd in items
        ]
        filas_tabla.append(
            ft.DataRow(cells=[
                ft.DataCell(ft.Text("Total", size=12, weight=ft.FontWeight.W_600)),
                ft.DataCell(ft.Text(f"${total_mxn:,.2f}", size=12, weight=ft.FontWeight.W_600)),
                ft.DataCell(ft.Text(f"US${total_usd:,.2f}", size=12, weight=ft.FontWeight.W_600, color=color_usd)),
            ])
        )
        tabla = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Segmento", size=12)),
                ft.DataColumn(ft.Text("Total MXN", size=12), numeric=True),
                ft.DataColumn(ft.Text("Total USD", size=12), numeric=True),
            ],
            rows=filas_tabla,
            data_row_max_height=34,
            heading_row_height=34,
            column_spacing=24,
        )

        seccion_detalle.content = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Detalle por segmento", size=13, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE),
                    tabla,
                    ft.Text(
                        "Los montos en USD son informativos: se muestran por segmento, pero no se convierten "
                        "ni se suman a los totales en MXN.",
                        size=10.5, color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=10,
            ),
            padding=16,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=12,
        )

    async def cargar(_e=None) -> None:
        cuerpo.opacity = 0.5
        progress.visible = True
        estado_text.value = ""
        boton_rango.disabled = True
        page.update()

        fecha_inicio, fecha_fin = rango_sel[0]
        try:
            resultado = await consultar_cobranza_por_segmento(fecha_inicio, fecha_fin)
        except Exception as error:  # noqa: BLE001 - se muestra en la sección, igual que el resto de la pestaña
            resultado = error

        _refrescar(resultado)

        progress.visible = False
        boton_rango.disabled = False
        cuerpo.opacity = 1.0
        if isinstance(resultado, Exception):
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
            "Los registros en dólares (Moneda = 'Dolar (USD)') se muestran aparte, como referencia — "
            "no se convierten ni se suman al total en pesos de Distribuidora/Asociados/Petroplazas.",
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

    barra_herramientas = ft.Row(
        [boton_rango, progress, estado_text, ft.Container(expand=True), boton_info],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
