"""Sub-pestaña 'Segmentado': la vista agregada original del dashboard — todas
las vistas del segmento principal visibles a la vez (Empresa, Tipo de negocio,
Sucursal, Sucursal Gasolineras, Otras empresas y la banda hero de KPIs), sobre
un rango de fechas propio, con toggle gráfica/tabla."""

import asyncio
from datetime import date, datetime

import flet as ft

from .componentes import (
    chip_total,
    color_slot,
    construir_donut,
    construir_ranked_list,
    construir_tabla,
    estado_vacio,
    hero_tile,
    mostrar_dialogo,
)
from .consultas import (
    consultar_no_identificado,
    consultar_otras_empresas,
    consultar_segmento,
    consultar_sucursal_gas,
)

# (titulo, subtitulo, consulta, vista) — vista: "donut" (composición parte-todo)
# o "ranked" (leaderboard con muchas categorías).
_SECCIONES = [
    (
        "Empresa",
        "Asociados y Distribuidora · excluye pagos entre filiales, GAS, Autotanque y sucursal sin asignar",
        lambda fi, ff: consultar_segmento(fi, ff, "nb_Empresa"),
        "donut",
    ),
    (
        "Tipo de negocio",
        "Asociados y Distribuidora · excluye pagos entre filiales, GAS, Autotanque y sucursal sin asignar",
        lambda fi, ff: consultar_segmento(fi, ff, "nb_TipoDeNegocio"),
        "donut",
    ),
    (
        "Sucursal",
        "Asociados y Distribuidora · excluye pagos entre filiales, GAS, Autotanque y sucursal sin asignar",
        lambda fi, ff: consultar_segmento(fi, ff, "nb_sucursal"),
        "ranked",
    ),
    (
        "Sucursal (Gasolineras)",
        "Segmento GasPetroil · excluye pagos entre filiales",
        consultar_sucursal_gas,
        "ranked",
    ),
    (
        "Otras empresas",
        "Fuera de Abastecedora / ACP Combustibles / Petro Smart · todos los tipos de negocio y sucursales",
        consultar_otras_empresas,
        "ranked",
    ),
]


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
        cuerpo = estado_vacio()
    else:
        items = resultado
        total = sum(v for _, v in items)
        total_chip = chip_total(total)
        if en_tabla:
            cuerpo = construir_tabla(items, dark, un_solo_color=(vista != "donut"))
        elif vista == "donut":
            cuerpo = construir_donut(items, dark)
        else:
            cuerpo = construir_ranked_list(items, dark)

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


def construir_subtab_segmentado(page: ft.Page) -> ft.Control:
    """Contenido de la sub-pestaña 'Segmentado'. Se construye UNA sola vez al
    armar la pestaña (dispara sus consultas al inicio); sus refrescos internos
    reemplazan `.controls` de los contenedores, igual que siempre."""
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)
    rango_sel: list[tuple[date, date]] = [(primer_dia_mes, hoy)]
    resultados_actuales: list[list] = [[[] for _ in _SECCIONES] + [0]]
    en_tabla = [False]  # False = gráfica, True = tabla, aplica a todas las secciones a la vez

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
            hero_tile("Ingresos identificados", _total_seguro(res_empresa), color_slot(0, dark),
                      ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED,
                      "Asociados y Distribuidora (segmento principal)"),
            hero_tile("Gaseras", _total_seguro(res_gas), color_slot(1, dark),
                      ft.Icons.LOCAL_GAS_STATION_OUTLINED, "Segmento GasPetroil"),
            
            hero_tile("Sin identificar", total_no_identificado, "#e34948",
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
            consultar_no_identificado(fecha_inicio, fecha_fin),
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
        entry_mode=ft.DatePickerEntryMode.INPUT,  # diálogo compacto de texto, no el calendario grande
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

    def _abrir_info(_e) -> None:
        lineas = [
            "Ingresos identificados, Tipo de negocio y Sucursal: solo cuentan "
            "movimientos de Asociados y Distribuidora en las 3 empresas "
            "principales (Abastecedora, ACP Combustibles y Petro Smart). No "
            "incluyen pagos entre filiales ni movimientos de sucursales de GAS, "
            "Autotanque o sin sucursal asignada.",
            "Gaseras (Sucursal Gasolineras): mismas 3 empresas principales, pero "
            "solo el segmento GasPetroil. Aquí sí se incluyen las sucursales de "
            "GAS y Autotanque, porque son precisamente el objeto de esta vista. "
            "Tampoco incluye pagos entre filiales.",
            "Otras empresas: todo lo que NO sea Abastecedora, ACP Combustibles ni "
            "Petro Smart, sin más filtros — se incluyen todos los tipos de "
            "negocio, todas las sucursales y también los pagos entre filiales.",
            "Sin identificar: suma de todos los movimientos marcados como no "
            "identificados en el periodo, sin importar empresa, sucursal o tipo "
            "de negocio.",
            "El tipo de negocio se reclasifica en dos casos antes de agrupar: los "
            "clientes de 'Público en general' de Petro Smart se cuentan como "
            "'GasPetroil', y un cliente específico (id 4359) se cuenta como "
            "'Distribuidora', sin importar cómo esté registrado originalmente.",
            "A diferencia de las sub-pestañas 'Timeline' y 'Detalle', aquí sí se "
            "excluyen por defecto GAS, Autotanque, sucursal sin asignar y pagos "
            "entre filiales — por eso los totales no son directamente "
            "comparables con los de esas vistas.",
        ]
        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Cómo se calculan estos datos"),
            content=ft.Container(
                content=ft.Column(
                    [ft.Text(f"•  {l}", size=12, selectable=True) for l in lineas],
                    spacing=10, scroll=ft.ScrollMode.AUTO,
                ),
                width=520, height=340,
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
        [boton_rango, progress, estado_text, ft.Container(expand=True), boton_info, boton_vista],
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

    return contenido
