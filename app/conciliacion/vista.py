"""Pestaña 'Conciliaciones Bancarias' (Flet).

Flujo: elegir rango de fechas -> subir Excel del banco -> normalizar (openpyxl en
hilo) -> traer movimientos del sistema (BigQuery en hilo) -> conciliar -> mostrar
los 4 grupos en tablas. Todo el I/O va en asyncio.to_thread para no bloquear la UI.

Devuelve (tab, contenido) igual que construir_tab_dashboard, para que main.py lo
inserte en TabBar/TabBarView en la misma posición.
"""

import asyncio
import dataclasses
import os
import tempfile
import traceback
from datetime import date, datetime, timedelta

import flet as ft
from flet_datatable2 import DataColumn2, DataColumnSize, DataTable2

from .conciliador import conciliar
from .ingresos_diversos import cargar_ingresos_diversos
from .lector_banco import nombres_bancos, normalizar_banco
from .modelo import MovimientoConciliacion, ResultadoConciliacion
from ..parsers.lectura import EXTENSIONES
from ..services.bigquery_repository import BigQueryRepository

# Color por grupo (acento de la tarjeta/tabla).
_COLOR_CONCILIADOS = "#1baf7a"
_COLOR_SOLO_BANCO = "#eda100"
_COLOR_SOLO_SISTEMA = "#2a78d6"
_COLOR_CHEQUES = "#e34948"


_MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _fmt_fecha(f: date | None) -> str:
    return f.strftime("%d/%m/%Y") if f else ""


def _fmt_fecha_larga(f: date) -> str:
    """01 jul 2026 — con mes en español (strftime %b depende del locale del SO)."""
    return f"{f.day:02d} {_MESES_ES[f.month - 1]} {f.year}"


def _fmt_importe(v: float) -> str:
    return f"${v:,.2f}"


def construir_tab_conciliaciones(page: ft.Page) -> tuple[ft.Tab, ft.Control]:
    # El diálogo compacto (INPUT) del DateRangePicker trae textos (encabezado, ayuda
    # y etiquetas) demasiado grandes para su tamaño ajustado. El estilo del picker
    # solo se configura a nivel tema (no por instancia), así que se afina aquí para
    # claro y oscuro. Esta pestaña se construye después del Dashboard, por lo que su
    # ajuste es el que prevalece (unifica ambos selectores).
    _tema_dp = ft.DatePickerTheme(
        range_picker_header_headline_text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_600),
        range_picker_header_help_text_style=ft.TextStyle(size=11),
        header_headline_text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_600),
        header_help_text_style=ft.TextStyle(size=11),
        weekday_text_style=ft.TextStyle(size=12),
        day_text_style=ft.TextStyle(size=12),
        year_text_style=ft.TextStyle(size=13),
    )
    if page.theme is not None:
        page.theme = dataclasses.replace(page.theme, date_picker_theme=_tema_dp)
    if page.dark_theme is not None:
        page.dark_theme = dataclasses.replace(page.dark_theme, date_picker_theme=_tema_dp)

    hoy = date.today()
    # Rango por defecto: el día anterior (la conciliación normalmente se hace sobre
    # el movimiento del día previo, no el de hoy que aún no cierra).
    ayer = hoy - timedelta(days=1)
    rango_sel: list[tuple[date, date]] = [(ayer, ayer)]
    archivo_sel: list[str | None] = [None]        # ruta temporal del .xlsx del banco
    archivo_sistema: list[str | None] = [None]    # ruta temporal del Excel de Ingresos Diversos
    nombre_archivo = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    nombre_sistema = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

    # El repositorio se crea perezosamente (necesita credenciales de BigQuery); así
    # la pestaña se construye aunque BigQuery no esté configurado en ese momento.
    repo_holder: list[BigQueryRepository | None] = [None]

    def _repo() -> BigQueryRepository:
        if repo_holder[0] is None:
            repo_holder[0] = BigQueryRepository()
        return repo_holder[0]

    # En Flet 0.85 el FilePicker es un servicio: se crea y se usa directamente (NO
    # se agrega a page.overlay; hacerlo provoca "Unknown control: FilePicker").
    file_picker = ft.FilePicker()

    # SnackBar propio para avisos (Flet 0.85 no tiene page.open(); se usa el patrón
    # del resto de la app: control en overlay + .open = True + page.update()).
    snackbar = ft.SnackBar(content=ft.Text(""))
    page.overlay.append(snackbar)

    # --- Encabezado y barra de herramientas -------------------------------------
    titulo = ft.Text("Conciliación Bancaria", size=20, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE)
    subtitulo = ft.Text(
        "Compara el estado de cuenta del banco (.xlsx) contra los movimientos del sistema.",
        size=12,
        color=ft.Colors.ON_SURFACE_VARIANT,
    )
    estado_text = ft.Text("", size=12, color=ft.Colors.RED_600)
    progress = ft.ProgressRing(width=16, height=16, visible=False, stroke_width=2)

    def _texto_rango(inicio: date, fin: date) -> str:
        return f"{_fmt_fecha_larga(inicio)} – {_fmt_fecha_larga(fin)}"

    def on_cambiar_rango(e) -> None:
        picker = e.control
        if not picker.start_value or not picker.end_value:
            return
        rango_sel[0] = (picker.start_value.date(), picker.end_value.date())
        boton_rango.content.controls[1].value = _texto_rango(*rango_sel[0])
        page.update()

    date_range_picker = ft.DateRangePicker(
        first_date=datetime(2020, 1, 1),
        last_date=datetime(2035, 12, 31),
        start_value=datetime.combine(ayer, datetime.min.time()),
        end_value=datetime.combine(ayer, datetime.min.time()),
        # Calendario siempre visible (sin modo escritura): más intuitivo para el
        # usuario — se eligen las fechas tocando los días directamente.
        entry_mode=ft.DatePickerEntryMode.CALENDAR_ONLY,
        on_change=on_cambiar_rango,
    )
    boton_rango = ft.OutlinedButton(
        content=ft.Row(
            [ft.Icon(ft.Icons.DATE_RANGE, size=16), ft.Text(_texto_rango(ayer, ayer), size=13)],
            spacing=8,
            tight=True,
        ),
        style=ft.ButtonStyle(padding=ft.Padding(left=12, right=12, top=6, bottom=6)),
        on_click=lambda _e: page.show_dialog(date_range_picker),
    )

    async def on_cargar_archivo(_e) -> None:
        archivos = await file_picker.pick_files(
            dialog_title="Selecciona el estado de cuenta del banco",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=EXTENSIONES,  # xlsx/xlsm/xls/xml/csv
            allow_multiple=False,
            with_data=True,
        )
        if not archivos:
            return
        archivo = archivos[0]
        # En modo web/escritorio: si viene con bytes, volcarlos a un temporal
        # CONSERVANDO la extensión original (la detección de algunos bancos —p. ej.
        # BBVA .xls SpreadsheetML— depende de ella).
        if archivo.path and os.path.exists(archivo.path):
            archivo_sel[0] = archivo.path
        else:
            sufijo = os.path.splitext(archivo.name)[1] or ".xlsx"
            with tempfile.NamedTemporaryFile(suffix=sufijo, delete=False) as tmp:
                tmp.write(archivo.bytes or b"")
                archivo_sel[0] = tmp.name
        nombre_archivo.value = archivo.name
        boton_conciliar.disabled = False
        page.update()

    boton_cargar = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.UPLOAD_FILE, size=16), ft.Text("Cargar Excel bancario", size=13)], spacing=8, tight=True),
        style=ft.ButtonStyle(padding=ft.Padding(left=12, right=12, top=6, bottom=6)),
        on_click=on_cargar_archivo,
    )
    # Selector de banco: "Auto-detectar" por defecto; forzar un banco es útil cuando
    # dos formatos comparten encabezados (Banorte / BX / Ve por Más).
    banco_dropdown = ft.Dropdown(
        label="Banco",
        value="",
        width=190,
        options=[ft.dropdown.Option(key="", text="Auto-detectar")]
        + [ft.dropdown.Option(key=n, text=n.title()) for n in nombres_bancos()],
    )
    boton_conciliar = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.COMPARE_ARROWS, size=16), ft.Text("Conciliar", size=13)], spacing=8, tight=True),
        disabled=True,
        on_click=lambda e: page.run_task(on_conciliar, e),
    )

    # --- Origen de los datos del "sistema" para comparar ------------------------
    async def on_cargar_sistema(_e) -> None:
        archivos = await file_picker.pick_files(
            dialog_title="Selecciona el reporte de Ingresos Diversos",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx"],
            allow_multiple=False,
            with_data=True,
        )
        if not archivos:
            return
        archivo = archivos[0]
        if archivo.path and os.path.exists(archivo.path):
            archivo_sistema[0] = archivo.path
        else:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(archivo.bytes or b"")
                archivo_sistema[0] = tmp.name
        nombre_sistema.value = archivo.name
        page.update()

    boton_cargar_sistema = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.UPLOAD_FILE, size=16), ft.Text("Cargar Ingresos Diversos", size=13)], spacing=8, tight=True),
        style=ft.ButtonStyle(padding=ft.Padding(left=12, right=12, top=6, bottom=6)),
        on_click=on_cargar_sistema,
    )

    def on_cambiar_origen(_e=None) -> None:
        es_excel = origen_group.value == "excel"
        # En modo Excel se cargan los movimientos del reporte; en modo nube se
        # consultan de BigQuery por rango de fechas.
        boton_cargar_sistema.visible = es_excel
        nombre_sistema.visible = es_excel
        boton_rango.visible = not es_excel
        page.update()

    origen_group = ft.RadioGroup(
        value="excel",  # por defecto el Excel, mientras la tabla en la nube no esté lista
        content=ft.Row(
            [
                ft.Radio(value="excel", label="Excel de Ingresos Diversos"),
                ft.Radio(value="nube", label="Datos en la nube"),
            ],
            spacing=8,
        ),
        on_change=on_cambiar_origen,
    )

    # Fila 1: archivo del banco. Fila 2: contra qué comparar. Fila 3: acción.
    # (El espaciador expand=True va en una fila SIN wrap; dentro de una fila con
    # wrap=True se renderiza como un recuadro gris enorme y descoloca los botones.)
    barra = ft.Column(
        [
            ft.Row(
                [boton_cargar, banco_dropdown, nombre_archivo],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
            ),
            ft.Row(
                [
                    ft.Text("Comparar contra:", size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                    origen_group,
                    boton_rango,
                    boton_cargar_sistema,
                    nombre_sistema,
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
            ),
            ft.Row(
                [progress, estado_text, ft.Container(expand=True), boton_conciliar],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ],
        spacing=10,
    )
    on_cambiar_origen()  # estado inicial de visibilidad (modo Excel)

    # --- Secciones (total + tabla unificados en un panel desplegable) -----------
    secciones = ft.Column(spacing=12)
    cuerpo = ft.Column([secciones], spacing=16)

    def _tabla(columnas: list[tuple[str, DataColumnSize | float | None]]) -> DataTable2:
        cols = []
        for etiqueta, tam in columnas:
            if isinstance(tam, (int, float)):
                cols.append(DataColumn2(ft.Text(etiqueta, weight=ft.FontWeight.BOLD), fixed_width=tam))
            else:
                cols.append(DataColumn2(ft.Text(etiqueta, weight=ft.FontWeight.BOLD), size=tam or DataColumnSize.M))
        return DataTable2(columns=cols, rows=[], min_width=700, fixed_top_rows=1, column_spacing=16, expand=True)

    def seccion_resultado(
        titulo_s: str,
        color: str,
        icono: str,
        columnas: list[tuple[str, object]],
        filas: list[list[str]],
        total_monto: float,
    ) -> ft.Control:
        """Factory reutilizable: un panel (total + tabla) por grupo. El encabezado
        muestra el conteo y el monto total; al hacer clic se despliega la tabla.

        Las 4 secciones (Conciliados, Solo Banco, Solo Sistema, Cheques) usan esta
        misma función; solo cambian columnas, filas y color."""
        # Cuerpo desplegable: la tabla, o un aviso si el grupo está vacío.
        if filas:
            tabla = _tabla(columnas)
            tabla.rows = [
                ft.DataRow(cells=[ft.DataCell(ft.Text(c, color=ft.Colors.ON_SURFACE)) for c in fila])
                for fila in filas
            ]
            interior = ft.Container(content=tabla, height=280, padding=ft.Padding(left=12, right=12, top=0, bottom=12))
        else:
            interior = ft.Container(
                content=ft.Text("Sin movimientos en este grupo.", italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
                padding=ft.Padding(left=16, right=16, top=0, bottom=14),
            )

        # Chip con el conteo (el "total" en el que se hace clic).
        badge = ft.Container(
            content=ft.Text(str(len(filas)), size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
            bgcolor=color, border_radius=12, padding=ft.Padding(left=9, right=9, top=1, bottom=1),
        )
        tile = ft.ExpansionTile(
            leading=ft.Icon(icono, color=color),
            title=ft.Row([ft.Text(titulo_s, size=14, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE), badge], spacing=10),
            subtitle=ft.Text(f"{len(filas)} movimiento(s) · {_fmt_importe(total_monto)}", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            controls=[interior],
            expanded=False,
            collapsed_bgcolor=ft.Colors.with_opacity(0.04, color),
            bgcolor=ft.Colors.with_opacity(0.04, color),
        )
        # Envoltura con borde redondeado para que cada panel se vea como tarjeta.
        return ft.Container(
            content=tile,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

    def _render(res: ResultadoConciliacion) -> None:
        cols_mov = [("Fecha", 90.0), ("Descripción", DataColumnSize.L), ("Referencia", 150.0), ("Importe", 120.0)]
        cols_conc = [("Fecha", 90.0), ("Descripción (banco)", DataColumnSize.L), ("Referencia", 150.0),
                     ("Importe banco", 120.0), ("Importe sistema", 120.0)]

        filas_conc = [
            [_fmt_fecha(b.fecha), b.descripcion, b.referencia, _fmt_importe(b.importe), _fmt_importe(s.importe)]
            for b, s in res.conciliados
        ]
        filas_banco = [[_fmt_fecha(m.fecha), m.descripcion, m.referencia, _fmt_importe(m.importe)] for m in res.solo_banco]
        filas_sistema = [[_fmt_fecha(m.fecha), m.descripcion, m.referencia, _fmt_importe(m.importe)] for m in res.solo_sistema]
        filas_cheques = [[_fmt_fecha(m.fecha), m.descripcion, m.referencia, _fmt_importe(m.importe)] for m in res.devoluciones_cheque]

        total_conc = sum(b.importe for b, _ in res.conciliados)
        secciones.controls = [
            seccion_resultado("Movimientos conciliados", _COLOR_CONCILIADOS, ft.Icons.CHECK_CIRCLE_OUTLINE, cols_conc, filas_conc, total_conc),
            seccion_resultado("En banco, no en sistema", _COLOR_SOLO_BANCO, ft.Icons.ACCOUNT_BALANCE_OUTLINED, cols_mov, filas_banco, sum(m.importe for m in res.solo_banco)),
            seccion_resultado("En sistema, no en banco", _COLOR_SOLO_SISTEMA, ft.Icons.DNS_OUTLINED, cols_mov, filas_sistema, sum(m.importe for m in res.solo_sistema)),
            seccion_resultado("Devoluciones de cheque", _COLOR_CHEQUES, ft.Icons.MONEY_OFF, cols_mov, filas_cheques, sum(m.importe for m in res.devoluciones_cheque)),
        ]
        page.update()

    def _avisar(mensaje: str, error: bool = True) -> None:
        snackbar.content = ft.Text(mensaje)
        snackbar.bgcolor = ft.Colors.RED_700 if error else None
        snackbar.open = True
        page.update()

    def _mostrar_error(mensaje: str) -> None:
        """Muestra el error en un diálogo FIJO y copiable, y además imprime el
        traceback completo en consola (para depurar)."""
        detalle = traceback.format_exc()
        print("\n[Conciliación] " + mensaje + "\n" + detalle)  # a consola
        cuerpo = ft.Column(
            [
                ft.Text(mensaje, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700),
                ft.SelectionArea(  # texto seleccionable/copiable
                    content=ft.Text(detalle, size=12, font_family="monospace", color=ft.Colors.ON_SURFACE),
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            tight=True,
            spacing=10,
        )
        dialogo = ft.AlertDialog(
            title=ft.Text("Error de conciliación"),
            content=ft.Container(content=cuerpo, width=620, height=360),
            actions=[ft.TextButton("Cerrar", on_click=lambda _e: page.pop_dialog())],
        )
        page.show_dialog(dialogo)

    async def on_conciliar(_e=None) -> None:
        if not archivo_sel[0]:
            _avisar("Primero carga el Excel del banco.")
            return
        progress.visible = True
        estado_text.value = ""
        boton_conciliar.disabled = True
        page.update()
        try:
            # 1. Detectar (o forzar) el banco y normalizar sus movimientos. Usa el
            #    sistema de parsers unificado (mismo que identificación bancaria).
            nombre_forzado = banco_dropdown.value or None
            nombre_banco, mov_banco, estado = await asyncio.to_thread(
                normalizar_banco, archivo_sel[0], nombre_forzado
            )
            if estado == "no_reconocido":
                _avisar(
                    "No se reconoció el formato del archivo. Verifica que sea un estado de "
                    "cuenta de un banco soportado; si el banco no está en la lista, comunícate "
                    "con el equipo de sistemas para validar el formato."
                )
                return
            if estado == "no_habilitado":
                _avisar(
                    f"El archivo parece de {nombre_banco}, pero ese banco aún no está "
                    "habilitado para conciliaciones. Comunícate con el equipo de sistemas "
                    "para validar el formato del banco."
                )
                return
            if not mov_banco:
                _avisar(f"El archivo de {nombre_banco} no tiene movimientos (abonos) que conciliar.", error=False)

            # 2. Traer los movimientos del sistema según el origen elegido.
            if origen_group.value == "excel":
                if not archivo_sistema[0]:
                    _avisar("Carga el Excel de Ingresos Diversos para comparar.")
                    return
                mov_sistema = await asyncio.to_thread(cargar_ingresos_diversos, archivo_sistema[0])
                origen_txt = "Ingresos Diversos (Excel)"
            else:
                fi, ff = rango_sel[0]
                crudos = await asyncio.to_thread(_repo().movimientos_crudos, fi, ff)
                mov_sistema = [MovimientoConciliacion.desde_sistema(c) for c in crudos]
                origen_txt = "nube"

            # 3. Conciliar y renderizar.
            resultado = conciliar(mov_banco, mov_sistema)
            _render(resultado)
            estado_text.value = f"Banco: {nombre_banco} · {len(mov_banco)} mov. · Sistema ({origen_txt}): {len(mov_sistema)} mov."
        except FileNotFoundError:
            _avisar("No se encontró el archivo cargado. Vuelve a cargarlo.")
        except Exception as ex:  # noqa: BLE001 — se reporta al usuario, no debe tumbar la UI
            _mostrar_error(f"Error al conciliar: {ex}")
        finally:
            progress.visible = False
            boton_conciliar.disabled = False
            page.update()

    contenido = ft.Container(
        content=ft.Column(
            [ft.Column([titulo, subtitulo], spacing=2), barra, ft.Container(content=cuerpo, expand=True)],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=20,
        expand=True,
    )

    tab = ft.Tab(label="Conciliaciones Bancarias", icon=ft.Icons.COMPARE_ARROWS)
    return tab, contenido
