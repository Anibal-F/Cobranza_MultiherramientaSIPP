import asyncio
import os
import platform
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from typing import Optional

import flet as ft
from flet_datatable2 import DataColumn2, DataColumnSize, DataTable2

from .catalogo import cargar_catalogo, guardar_catalogo_completo, guardar_nuevas_cuentas
from .clientes import cargar_clientes, preparar_clientes_normalizados
from .credenciales import borrar_credenciales, cargar_credenciales, guardar_credenciales
from .estado_cuenta import EstadoCuenta, cargar_estado_cuenta, sugerir_sucursal_detalle
from rpa.automation import es_modo_test
from .sucursales import cargar_sucursales
from .empresas import EMPRESAS, EMPRESA_DEFAULT, EMPRESA_POR_CLAVE
from .matcher import extraer_cuenta, match_movimientos, match_movimientos_por_nombre
from .models import ClienteCuenta, Movimiento
from .ingresos_diversos import cargar_ingresos_diversos_en_sipp, cargar_pagos_contado_en_sipp
from .mailbox_o365 import CorreoResumen, descargar_adjuntos, listar_correos, obtener_cuenta, obtener_cuerpo
from .pagos_contado import PagoContadoExtraido, completar_con_adjunto, extraer_pago_contado
from .parsers import detectar_banco, parsear_archivo
from .rpa_folios import buscar_y_aplicar_folios, extraer_folios_pendientes

EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _parsear_monto_pago(texto: str) -> Optional[float]:
    limpio = (texto or "").replace("$", "").replace(",", "").strip()
    if not limpio:
        return None
    try:
        return float(limpio)
    except ValueError:
        return None


def abrir_archivo_con_app_predeterminada(ruta: str) -> None:
    sistema = platform.system()
    if sistema == "Darwin":
        subprocess.run(["open", ruta], check=False)
    elif sistema == "Windows":
        os.startfile(ruta)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", ruta], check=False)


def revelar_en_explorador(ruta: str) -> None:
    """Abre el explorador de archivos resaltando `ruta`."""
    sistema = platform.system()
    try:
        if sistema == "Darwin":
            subprocess.run(["open", "-R", ruta], check=False)
        elif sistema == "Windows":
            subprocess.run(["explorer", "/select,", ruta], check=False)
        else:
            subprocess.run(["xdg-open", os.path.dirname(ruta)], check=False)
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGO_PATH = os.path.join(BASE_DIR, "Catalogos", "Cuentas_Clientes", "Catalogo_Cuentas_Clientes.csv")
CLIENTES_PATH = os.path.join(BASE_DIR, "Catalogos", "Cuentas_Clientes", "Clientes.csv")

FILTRO_TODOS = "Todos"
FILTRO_IDENTIFICADOS = "Identificados"
FILTRO_NO_IDENTIFICADOS = "No identificados"

# Branding Grupo Petroil
NAVY = "#003C74"
GOLD = "#FBB812"
ORANGE = "#F59D00"


def main(page: ft.Page) -> None:
    page.title = "Conciliación Bancaria · Grupo Petroil"
    page.window.width = 1280
    page.window.height = 860
    page.window.icon = "Logo_Petroil.ico"
    page.padding = 0
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=NAVY, use_material3=True)
    page.dark_theme = ft.Theme(color_scheme_seed=NAVY, use_material3=True)

    def mostrar_dialogo(dialogo: ft.AlertDialog) -> None:
        """Muestra el diálogo de forma idempotente y a prueba de estados colgados.

        pop_dialog() marca open=False pero NO retira el diálogo de la pila
        interna de Flet hasta que Flutter reporta el evento de dismiss. Tras un
        RPA pesado (Playwright) ese evento puede no llegar limpio y el diálogo
        queda colgado: en la pila pero con open=False. Entonces el guard
        `if not dialogo.open` pasa, show_dialog() lo encuentra todavía en la
        pila y truena con 'Dialog is already opened' (excepción tragada en el
        on_click) → no aparece ningún modal. Lo limpiamos a mano antes de
        reabrir."""
        pila = page._dialogs.controls
        if dialogo in pila:
            if dialogo.open:
                return  # ya está realmente abierto
            pila.remove(dialogo)  # colgado: retirarlo para poder reabrir
        page.show_dialog(dialogo)

    catalogo = cargar_catalogo(CATALOGO_PATH)
    clientes_normalizados = preparar_clientes_normalizados(cargar_clientes(CLIENTES_PATH))
    sucursales_catalogo = cargar_sucursales()
    nombres_clientes = sorted({original for original, _ in clientes_normalizados})
    sugerencias_clientes = [ft.AutoCompleteSuggestion(key=nombre, value=nombre) for nombre in nombres_clientes]
    movimientos: list[Movimiento] = []

    # --- Controles de estado / resumen ---
    archivo_nombre_text = ft.Text("Ningún archivo cargado", italic=True, color=ft.Colors.ON_SURFACE_VARIANT)
    banco_detectado_text = ft.Text("")
    catalogo_info_text = ft.Text(
        f"Catálogo de clientes cargado: {len(catalogo)} cuentas",
        color=ft.Colors.WHITE_70,
        size=12,
    )

    card_total = ft.Text("0", size=28, weight=ft.FontWeight.BOLD, color=NAVY)
    card_identificados = ft.Text("0", size=28, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_700)
    card_no_identificados = ft.Text("0", size=28, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700)
    card_porcentaje = ft.Text("0%", size=28, weight=ft.FontWeight.BOLD, color=ORANGE)
    # Cargos fuera de alcance por ahora (solo se procesan abonos). Se deja
    # comentado por si en el futuro se requiere mostrar también los cargos.
    # card_total_cargos = ft.Text("$0.00", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700)
    card_total_abonos = ft.Text("$0.00", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_700)

    def resumen_card(titulo: str, valor_control: ft.Text) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [ft.Text(titulo, size=12, color=ft.Colors.ON_SURFACE_VARIANT), valor_control],
                spacing=4,
            ),
            padding=16,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border(
                top=ft.BorderSide(3, GOLD),
                left=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
                right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
                bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
            ),
            border_radius=8,
            expand=True,
        )

    resumen_row = ft.Row(
        [
            resumen_card("Movimientos totales", card_total),
            resumen_card("Identificados", card_identificados),
            resumen_card("No identificados", card_no_identificados),
            resumen_card("% Identificado", card_porcentaje),
            # resumen_card("Total cargos", card_total_cargos),
            resumen_card("Total abonos", card_total_abonos),
        ],
        spacing=12,
    )

    def on_filtro_cambio(_e) -> None:
        refrescar_tabla()

    # --- Filtros ---
    filtro_estado = ft.Dropdown(
        label="Estado",
        value=FILTRO_TODOS,
        options=[ft.dropdown.Option(o) for o in (FILTRO_TODOS, FILTRO_IDENTIFICADOS, FILTRO_NO_IDENTIFICADOS)],
        width=200,
        color=ft.Colors.ON_SURFACE,
        on_select=on_filtro_cambio,
    )
    filtro_texto = ft.TextField(
        label="Buscar cliente, cuenta o descripción",
        width=400,
        color=ft.Colors.ON_SURFACE,
        on_change=on_filtro_cambio,
        on_submit=on_filtro_cambio,
        on_blur=on_filtro_cambio,
    )

    def columna(
        texto: str,
        numeric: bool = False,
        fixed_width: Optional[float] = None,
        size: Optional[DataColumnSize] = None,
    ) -> DataColumn2:
        return DataColumn2(
            ft.Text(texto, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
            numeric=numeric,
            fixed_width=fixed_width,
            size=size,
        )

    # Columnas angostas con ancho fijo; "Descripción" y "Cliente identificado"
    # son flexibles (size=L) y se reparten el espacio restante de la ventana.
    tabla = DataTable2(
        columns=[
            columna("Fecha", fixed_width=90),
            columna("Banco", fixed_width=90),
            columna("Descripción", size=DataColumnSize.L),
            columna("Referencia", fixed_width=150),
            columna("Abono", numeric=True, fixed_width=110),
            columna("Cliente identificado", size=DataColumnSize.L),
            columna("Cuenta", fixed_width=110),
            columna("Sucursal sugerida", fixed_width=180),
            columna("Estado", fixed_width=150),
            columna("Acciones", fixed_width=90),
        ],
        rows=[],
        min_width=1050,
        fixed_top_rows=1,
        column_spacing=16,
        expand=True,
    )

    tabla_contenedor = ft.Container(content=tabla, expand=True)

    def estado_badge(m: Movimiento) -> ft.Container:
        if m.identificado_manual:
            return ft.Container(
                content=ft.Text("Identificado (manual)", color=ft.Colors.WHITE, size=11, no_wrap=True),
                bgcolor=ft.Colors.TEAL_600,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=12,
            )
        if m.identificado_por_folio:
            return ft.Container(
                content=ft.Text("Identificado (folio SIPP)", color=ft.Colors.WHITE, size=11, no_wrap=True),
                bgcolor=ft.Colors.PURPLE_600,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=12,
            )
        if m.identificado_por_nombre:
            return ft.Container(
                content=ft.Text("Identificado (nombre)", color=ft.Colors.WHITE, size=11, no_wrap=True),
                bgcolor=ft.Colors.BLUE_600,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=12,
            )
        if m.identificado:
            return ft.Container(
                content=ft.Text("Identificado", color=ft.Colors.WHITE, size=11, no_wrap=True),
                bgcolor=ft.Colors.GREEN_600,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=12,
            )
        return ft.Container(
            content=ft.Text("No identificado", color=ft.Colors.WHITE, size=11, no_wrap=True),
            bgcolor=ft.Colors.RED_600,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=12,
        )

    # --- Identificación manual ---
    movimiento_a_identificar: list[Movimiento | None] = [None]

    manual_info_text = ft.Text("")
    manual_autocomplete = ft.AutoComplete(suggestions=sugerencias_clientes)

    def on_select_cliente_manual(e: ft.AutoCompleteSelectEvent) -> None:
        mov = movimiento_a_identificar[0]
        if mov is None:
            return
        cliente = e.selection.value

        mov.cliente_match = cliente
        mov.identificado_manual = True

        cuenta_extraida = extraer_cuenta(mov.texto_busqueda)
        agregadas = []
        if cuenta_extraida:
            mov.cuenta_match = cuenta_extraida
            propuesta = [ClienteCuenta(cuenta=cuenta_extraida, cliente=cliente, banco=mov.banco, plaza="")]
            agregadas = guardar_nuevas_cuentas(CATALOGO_PATH, catalogo, propuesta)
            catalogo.extend(agregadas)
            catalogo_info_text.value = f"Catálogo de clientes cargado: {len(catalogo)} cuentas"

        mensaje = f"'{cliente}' asignado manualmente al movimiento."
        if agregadas:
            mensaje += " Se agregó la cuenta al catálogo."
        estado_text.value = mensaje

        page.pop_dialog()
        refrescar_resumen()
        refrescar_tabla()

    manual_autocomplete.on_select = on_select_cliente_manual

    def on_cancelar_manual(_e) -> None:
        page.pop_dialog()

    dialogo_manual = ft.AlertDialog(
        modal=True,
        title=ft.Text("Identificar cliente manualmente"),
        content=ft.Column(
            [manual_info_text, manual_autocomplete],
            tight=True,
            spacing=12,
            width=400,
        ),
        actions=[ft.TextButton("Cancelar", on_click=on_cancelar_manual)],
    )

    def abrir_dialogo_manual(mov: Movimiento) -> None:
        movimiento_a_identificar[0] = mov
        manual_info_text.value = f"{mov.descripcion[:90]}\nReferencia: {mov.referencia} · Abono: ${mov.abono:,.2f}"
        manual_autocomplete.value = ""
        mostrar_dialogo(dialogo_manual)

    def boton_identificar_manual(m: Movimiento) -> ft.Control:
        if m.identificado:
            return ft.Text("-", color=ft.Colors.ON_SURFACE_VARIANT)
        return ft.IconButton(
            icon=ft.Icons.PERSON_ADD,
            tooltip="Identificar cliente manualmente",
            icon_color=NAVY,
            on_click=lambda _e, mov=m: abrir_dialogo_manual(mov),
        )

    # --- Declaración manual de folio/texto a buscar en SIPP ---
    movimiento_a_declarar_folio: list[Movimiento | None] = [None]

    folio_manual_info_text = ft.Text("")
    folio_manual_field = ft.TextField(label="Folio o texto a buscar en SIPP")

    def on_confirmar_folio_manual(_e) -> None:
        mov = movimiento_a_declarar_folio[0]
        if mov is None:
            return
        mov.folio_manual = (folio_manual_field.value or "").strip() or None
        page.pop_dialog()
        refrescar_tabla()

    def on_cancelar_folio_manual(_e) -> None:
        page.pop_dialog()

    dialogo_folio_manual = ft.AlertDialog(
        modal=True,
        title=ft.Text("Declarar folio/texto para SIPP"),
        content=ft.Column(
            [folio_manual_info_text, folio_manual_field],
            tight=True,
            spacing=12,
            width=400,
        ),
        actions=[
            ft.TextButton("Cancelar", on_click=on_cancelar_folio_manual),
            ft.Button("Guardar", on_click=on_confirmar_folio_manual, bgcolor=NAVY, color=ft.Colors.WHITE),
        ],
    )

    def abrir_dialogo_folio_manual(mov: Movimiento) -> None:
        movimiento_a_declarar_folio[0] = mov
        folio_manual_info_text.value = f"{mov.descripcion[:90]}\nReferencia: {mov.referencia} · Abono: ${mov.abono:,.2f}"
        folio_manual_field.value = mov.folio_manual or ""
        mostrar_dialogo(dialogo_folio_manual)

    def boton_declarar_folio(m: Movimiento) -> ft.Control:
        if m.identificado:
            return ft.Container(width=0)
        return ft.IconButton(
            icon=ft.Icons.TRAVEL_EXPLORE,
            tooltip=f"Folio declarado: {m.folio_manual}" if m.folio_manual else "Declarar folio/texto a buscar en SIPP",
            icon_color=ORANGE if m.folio_manual else ft.Colors.ON_SURFACE_VARIANT,
            on_click=lambda _e, mov=m: abrir_dialogo_folio_manual(mov),
        )

    def aplicar_filtros() -> list[Movimiento]:
        resultado = movimientos
        if filtro_estado.value == FILTRO_IDENTIFICADOS:
            resultado = [m for m in resultado if m.identificado]
        elif filtro_estado.value == FILTRO_NO_IDENTIFICADOS:
            resultado = [m for m in resultado if not m.identificado]

        texto = (filtro_texto.value or "").strip().lower()
        if texto:
            resultado = [
                m
                for m in resultado
                if texto in (m.cliente_match or "").lower()
                or texto in (m.cuenta_match or "").lower()
                or texto in m.descripcion.lower()
                or texto in m.referencia.lower()
            ]
        return resultado

    def celda(texto: str) -> ft.DataCell:
        return ft.DataCell(ft.Text(texto, color=ft.Colors.ON_SURFACE))

    # Cache de la sugerencia por (movimiento, cliente, abono): refrescar_tabla se
    # llama en cada tecla de los filtros y no queremos recalcular el subset-sum.
    sucursal_cache: dict = {}

    def _boton_editar_sucursal(m: Movimiento) -> ft.Control:
        return ft.IconButton(
            icon=ft.Icons.EDIT,
            tooltip="Declarar/cambiar sucursal",
            icon_color=NAVY,
            icon_size=16,
            on_click=lambda _e, mov=m: abrir_dialogo_sucursal(mov),
        )

    def celda_sucursal_sugerida(m: Movimiento) -> ft.DataCell:
        """Celda de sucursal: declarada por el usuario (override) o sugerida por
        el estado de cuenta. Vacía si no se ha cargado el estado de cuenta o el
        movimiento no está identificado."""
        estado = estado_cuenta_ref[0]
        if estado is None or not m.identificado:
            return ft.DataCell(ft.Text("", color=ft.Colors.ON_SURFACE_VARIANT))

        # 1) Override declarado por el usuario tiene prioridad.
        if m.sucursal_declarada:
            texto = m.sucursal_declarada
            return ft.DataCell(
                ft.Row(
                    [
                        ft.Text(texto, color=ft.Colors.BLUE_700, weight=ft.FontWeight.BOLD,
                                tooltip="Declarada por el usuario"),
                        _boton_editar_sucursal(m),
                    ],
                    spacing=0,
                    tight=True,
                )
            )

        # 2) Sugerida por el estado de cuenta (cacheada), filtrada por empresa.
        empresa = empresa_ref[0]
        clave = (id(m), m.cliente_match, m.abono, empresa.clave)
        if clave in sucursal_cache:
            detalle = sucursal_cache[clave]
        else:
            detalle = sugerir_sucursal_detalle(estado, m.cliente_match, m.abono, empresa.nombre_reporte)
            sucursal_cache[clave] = detalle
        if detalle is None:
            # Cliente sin facturas de esta empresa en el estado de cuenta.
            return ft.DataCell(
                ft.Row(
                    [
                        ft.Text("— (no en edo. cuenta)", italic=True,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                        _boton_editar_sucursal(m),
                    ],
                    spacing=0,
                    tight=True,
                )
            )
        sucursal, motivo, todas = detalle
        if sucursal is None:
            texto = "?"
        elif motivo == "única":
            texto = sucursal
        else:
            texto = f"{sucursal} ({motivo})"
        tooltip = None
        color = ft.Colors.ON_SURFACE
        if len(todas) > 1:
            tooltip = "Sucursales del cliente: " + ", ".join(todas)
            if motivo in ("aproximado", None):
                color = ft.Colors.ORANGE_700  # match débil: revisar
        return ft.DataCell(
            ft.Row(
                [ft.Text(texto, color=color, tooltip=tooltip), _boton_editar_sucursal(m)],
                spacing=0,
                tight=True,
            )
        )

    def refrescar_tabla() -> None:
        filas = aplicar_filtros()
        tabla.rows = [
            ft.DataRow(
                cells=[
                    celda(m.fecha.strftime("%d/%m/%Y") if m.fecha else "-"),
                    celda(m.banco),
                    celda(m.descripcion[:60]),
                    celda(m.referencia[:30]),
                    # celda(f"${m.cargo:,.2f}" if m.cargo else ""),
                    celda(f"${m.abono:,.2f}" if m.abono else ""),
                    celda(m.cliente_match or "-"),
                    celda(m.cuenta_match or "-"),
                    celda_sucursal_sugerida(m),
                    ft.DataCell(estado_badge(m)),
                    ft.DataCell(ft.Row([boton_identificar_manual(m), boton_declarar_folio(m)], spacing=0)),
                ],
            )
            for m in filas
        ]
        page.update()

    def refrescar_resumen() -> None:
        total = len(movimientos)
        identificados = sum(1 for m in movimientos if m.identificado)
        no_identificados = total - identificados
        porcentaje = (identificados / total * 100) if total else 0
        # total_cargos = sum(m.cargo for m in movimientos)
        total_abonos = sum(m.abono for m in movimientos)

        card_total.value = str(total)
        card_identificados.value = str(identificados)
        card_no_identificados.value = str(no_identificados)
        card_porcentaje.value = f"{porcentaje:.1f}%"
        # card_total_cargos.value = f"${total_cargos:,.2f}"
        card_total_abonos.value = f"${total_abonos:,.2f}"
        page.update()


    estado_text = ft.Text("")
    ultima_ruta_csv: list[Optional[str]] = [None]

    def procesar_csv_path(ruta_temporal: str) -> None:
        """Detecta el banco, parsea y matchea el CSV en ruta_temporal, actualizando
        movimientos/catálogo/estado. Reutilizado por la carga manual de archivo y
        por la descarga de adjuntos del buzón O365. Si el archivo es válido, se
        conserva su ruta (ultima_ruta_csv) para poder volver a subirlo en SIPP
        (Ingresos Diversos) sin que el usuario tenga que volver a elegirlo."""
        nonlocal movimientos
        estado_text.value = "Procesando..."
        page.update()
        try:
            banco = detectar_banco(ruta_temporal)
            if banco is None:
                estado_text.value = "No se reconoció el formato del archivo (se esperaba Santander o BanRegio)."
                banco_detectado_text.value = ""
                movimientos = []
                refrescar_resumen()
                refrescar_tabla()
                page.update()
                return

            banco_detectado_text.value = f"Banco detectado: {banco}"
            movimientos = parsear_archivo(ruta_temporal, banco)
            match_movimientos(movimientos, catalogo)

            propuestas = match_movimientos_por_nombre(movimientos, clientes_normalizados)
            agregadas = guardar_nuevas_cuentas(CATALOGO_PATH, catalogo, propuestas)
            catalogo.extend(agregadas)
            catalogo_info_text.value = f"Catálogo de clientes cargado: {len(catalogo)} cuentas"

            identificados_por_nombre = sum(1 for m in movimientos if m.identificado_por_nombre)
            mensaje = f"Archivo procesado correctamente. {len(movimientos)} movimientos leídos."
            if identificados_por_nombre:
                mensaje += f" {identificados_por_nombre} se identificaron por nombre."
            if agregadas:
                mensaje += f" Se agregaron {len(agregadas)} cuenta(s) nueva(s) al catálogo."
            estado_text.value = mensaje
            ultima_ruta_csv[0] = ruta_temporal
        except Exception as ex:
            estado_text.value = f"Error al procesar el archivo: {ex}"
            movimientos = []

        # El estado de cuenta solo tiene sentido con un CSV ya cargado.
        boton_cargar_estado_cuenta.disabled = not movimientos
        refrescar_resumen()
        refrescar_tabla()

    file_picker = ft.FilePicker()

    async def on_click_cargar(_e) -> None:
        archivos = await file_picker.pick_files(
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["csv"],
            allow_multiple=False,
            with_data=True,
        )
        if not archivos:
            return
        archivo = archivos[0]
        archivo_nombre_text.value = archivo.name
        archivo_nombre_text.italic = False
        archivo_nombre_text.color = ft.Colors.ON_SURFACE
        page.update()

        # En modo web la API solo entrega los bytes del archivo (path es None);
        # se vuelca a un temporal para reutilizar los parsers basados en path.
        # No se borra al terminar: se conserva para poder subirlo también a
        # "Ingresos Diversos" en SIPP (ver ultima_ruta_csv).
        if ultima_ruta_csv[0] and os.path.exists(ultima_ruta_csv[0]):
            os.unlink(ultima_ruta_csv[0])
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp.write(archivo.bytes or b"")
            ruta_temporal = tmp.name
        procesar_csv_path(ruta_temporal)

    boton_cargar = ft.Button(
        "Cargar archivo bancario (.csv)",
        icon=ft.Icons.UPLOAD_FILE,
        on_click=on_click_cargar,
        bgcolor=NAVY,
        color=ft.Colors.WHITE,
    )

    # --- Estado de cuenta (.xlsx): índice cliente→sucursal para sugerir sucursal ---
    estado_cuenta_ref: list[Optional[EstadoCuenta]] = [None]
    estado_cuenta_text = ft.Text(
        "Estado de cuenta: no cargado", italic=True, color=ft.Colors.ON_SURFACE_VARIANT
    )

    async def on_click_cargar_estado_cuenta(_e) -> None:
        archivos = await file_picker.pick_files(
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx"],
            allow_multiple=False,
            with_data=True,
        )
        if not archivos:
            return
        archivo = archivos[0]
        estado_cuenta_text.value = f"Cargando estado de cuenta '{archivo.name}'..."
        estado_cuenta_text.italic = True
        page.update()
        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(archivo.bytes or b"")
                ruta_xlsx = tmp.name
            estado = await asyncio.to_thread(cargar_estado_cuenta, ruta_xlsx)
            os.unlink(ruta_xlsx)
            estado_cuenta_ref[0] = estado
            sucursal_cache.clear()
            identificados = sum(1 for m in movimientos if m.identificado)
            estado_cuenta_text.value = (
                f"Estado de cuenta cargado: {estado.num_clientes} cliente(s). "
                f"Sucursal sugerida para {identificados} movimiento(s) identificado(s)."
            )
            estado_cuenta_text.italic = False
            estado_cuenta_text.color = ft.Colors.ON_SURFACE
            refrescar_tabla()  # puebla la columna "Sucursal sugerida"
        except Exception as ex:
            estado_cuenta_ref[0] = None
            estado_cuenta_text.value = f"Error al cargar estado de cuenta: {ex}"
            estado_cuenta_text.color = ft.Colors.RED_600
        page.update()

    boton_cargar_estado_cuenta = ft.Button(
        "Cargar estado de cuenta (.xlsx)",
        icon=ft.Icons.TABLE_VIEW,
        on_click=lambda e: page.run_task(on_click_cargar_estado_cuenta, e),
        bgcolor=NAVY,
        color=ft.Colors.WHITE,
        disabled=True,  # se habilita al cargar un archivo bancario (.csv)
    )

    # --- Declaración manual de sucursal (override de la sugerida) ---
    movimiento_sucursal_edit: list[Movimiento | None] = [None]
    sucursal_edit_info = ft.Text("")
    sucursal_edit_dropdown = ft.Dropdown(
        label="Sucursal",
        editable=True,
        enable_filter=True,
        menu_height=300,
        width=360,
    )

    def on_confirmar_sucursal(_e) -> None:
        mov = movimiento_sucursal_edit[0]
        if mov is not None:
            mov.sucursal_declarada = sucursal_edit_dropdown.value or None
        page.pop_dialog()
        refrescar_tabla()

    def on_limpiar_sucursal(_e) -> None:
        mov = movimiento_sucursal_edit[0]
        if mov is not None:
            mov.sucursal_declarada = None
        page.pop_dialog()
        refrescar_tabla()

    dialogo_sucursal = ft.AlertDialog(
        modal=True,
        title=ft.Text("Declarar sucursal"),
        content=ft.Column(
            [sucursal_edit_info, sucursal_edit_dropdown],
            tight=True,
            spacing=12,
            width=400,
        ),
        actions=[
            ft.TextButton("Usar sugerida", on_click=on_limpiar_sucursal),
            ft.TextButton("Cancelar", on_click=lambda _e: page.pop_dialog()),
            ft.Button("Aplicar", on_click=on_confirmar_sucursal, bgcolor=NAVY, color=ft.Colors.WHITE),
        ],
    )

    def abrir_dialogo_sucursal(mov: Movimiento) -> None:
        estado = estado_cuenta_ref[0]
        if estado is None:
            return
        empresa = empresa_ref[0]
        # Opciones: las sucursales del cliente en la empresa; si no está, todas
        # las de la empresa.
        opciones = estado.sucursales_de_cliente(mov.cliente_match or "", empresa.nombre_reporte)
        if not opciones:
            opciones = estado.sucursales(empresa.nombre_reporte)
        sucursal_edit_dropdown.options = [ft.dropdown.Option(s) for s in opciones]
        # Valor inicial: la declarada, o la sugerida.
        detalle = sugerir_sucursal_detalle(estado, mov.cliente_match, mov.abono, empresa.nombre_reporte)
        sugerida = detalle[0] if detalle else None
        sucursal_edit_dropdown.value = mov.sucursal_declarada or sugerida
        sucursal_edit_info.value = (
            f"{(mov.cliente_match or '')} · Abono ${mov.abono:,.2f}\n"
            f"Sugerida: {sugerida or '—'}"
        )
        movimiento_sucursal_edit[0] = mov
        mostrar_dialogo(dialogo_sucursal)

    # --- Búsqueda de folios pendientes en SIPP (RPA) ---
    sipp_usuario_field = ft.TextField(label="Usuario SIPP", autofocus=True)
    sipp_password_field = ft.TextField(label="Contraseña SIPP", password=True, can_reveal_password=True)
    sipp_recordar_check = ft.Checkbox(label="Recordar credenciales en este equipo")
    sipp_progreso_text = ft.Text("")

    async def on_cancelar_sipp(_e) -> None:
        page.pop_dialog()

    async def on_confirmar_sipp(_e) -> None:
        usuario = (sipp_usuario_field.value or "").strip()
        password = sipp_password_field.value or ""
        if not usuario or not password:
            sipp_progreso_text.value = "Usuario y contraseña son obligatorios."
            page.update()
            return

        if sipp_recordar_check.value:
            guardar_credenciales(usuario, password)
        else:
            borrar_credenciales()

        boton_sipp_buscar.disabled = True
        boton_sipp_cancelar.disabled = True
        page.update()

        def log_fn(mensaje: str, _nivel: str = "info") -> None:
            sipp_progreso_text.value = mensaje
            page.update()

        try:
            candidatos = extraer_folios_pendientes(movimientos)
            propuestas = await buscar_y_aplicar_folios(
                candidatos, usuario, password, empresa=empresa_ref[0], headless=False, log_fn=log_fn
            )
            agregadas = guardar_nuevas_cuentas(CATALOGO_PATH, catalogo, propuestas)
            catalogo.extend(agregadas)
            catalogo_info_text.value = f"Catálogo de clientes cargado: {len(catalogo)} cuentas"

            identificados_por_folio = sum(1 for m in movimientos if m.identificado_por_folio)
            mensaje = f"Búsqueda en SIPP terminada. {identificados_por_folio} folio(s) identificado(s)."
            if agregadas:
                mensaje += f" Se agregaron {len(agregadas)} cuenta(s) nueva(s) al catálogo."
            estado_text.value = mensaje
        except Exception as ex:
            estado_text.value = f"Error durante la búsqueda en SIPP: {ex}"
        finally:
            boton_sipp_buscar.disabled = False
            boton_sipp_cancelar.disabled = False
            sipp_password_field.value = ""
            page.pop_dialog()
            refrescar_resumen()
            refrescar_tabla()

    boton_sipp_cancelar = ft.TextButton("Cancelar", on_click=on_cancelar_sipp)
    boton_sipp_buscar = ft.Button("Buscar", on_click=on_confirmar_sipp, bgcolor=NAVY, color=ft.Colors.WHITE)

    dialogo_sipp = ft.AlertDialog(
        modal=True,
        title=ft.Text("Buscar folios pendientes en SIPP"),
        content=ft.Column(
            [
                sipp_progreso_text,
                sipp_usuario_field,
                sipp_password_field,
                sipp_recordar_check,
            ],
            tight=True,
            spacing=12,
            width=350,
        ),
        actions=[boton_sipp_cancelar, boton_sipp_buscar],
    )

    def on_click_buscar_sipp(_e) -> None:
        if not movimientos:
            estado_text.value = "Primero carga un archivo bancario."
            page.update()
            return

        candidatos = extraer_folios_pendientes(movimientos)
        if not candidatos:
            estado_text.value = "No hay folios candidatos pendientes de buscar en SIPP."
            page.update()
            return

        usuario_guardado, password_guardado = cargar_credenciales()
        sipp_usuario_field.value = usuario_guardado
        sipp_password_field.value = password_guardado
        sipp_recordar_check.value = bool(usuario_guardado)
        sipp_progreso_text.value = f"{len(candidatos)} folio(s) candidato(s) detectado(s) en la descripción."
        mostrar_dialogo(dialogo_sipp)

    boton_buscar_sipp = ft.Button(
        "Buscar folios en SIPP",
        icon=ft.Icons.TRAVEL_EXPLORE,
        on_click=on_click_buscar_sipp,
        bgcolor=ORANGE,
        color=ft.Colors.WHITE,
    )

    # ──────────────────────────────────────────────────────
    # Carga a "Ingresos Diversos - Agregar" en SIPP
    # ──────────────────────────────────────────────────────
    fecha_operacion_field = ft.TextField(
        label="Fecha de Operación",
        value=date.today().strftime("%d/%m/%Y"),
        width=160,
        color=ft.Colors.ON_SURFACE,
    )
    # Empresa seleccionada: define el login de SIPP, el catálogo de cuentas y
    # las sucursales (estado de cuenta). Es global a toda la app.
    empresa_ref = [EMPRESA_DEFAULT]

    def _opciones_cuenta() -> list:
        return [ft.dropdown.Option(key=c.id_sipp, text=c.nombre) for c in empresa_ref[0].cuentas]

    def nombre_cuenta_bancaria(id_sipp: str) -> str:
        return {c.id_sipp: c.nombre for c in empresa_ref[0].cuentas}.get(id_sipp or "", "")

    cuenta_bancaria_dropdown = ft.Dropdown(
        label="Cuenta Bancaria (SIPP)",
        width=420,
        color=ft.Colors.ON_SURFACE,
        editable=True,        # campo escribible
        enable_filter=True,   # filtra la lista mientras escribes
        menu_height=300,      # tope de altura: el resto hace scroll
        options=_opciones_cuenta(),
    )

    def on_cambiar_empresa(e) -> None:
        empresa_ref[0] = EMPRESA_POR_CLAVE.get(e.control.value, EMPRESA_DEFAULT)
        # Las cuentas y sucursales del flujo CSV cambian con la empresa.
        cuenta_bancaria_dropdown.options = _opciones_cuenta()
        cuenta_bancaria_dropdown.value = None
        sucursal_cache.clear()
        refrescar_tabla()
        page.update()

    empresa_dropdown = ft.Dropdown(
        label="Empresa (SIPP)",
        width=220,
        color=ft.Colors.ON_SURFACE,
        value=EMPRESA_DEFAULT.clave,
        options=[ft.dropdown.Option(key=e.clave, text=e.nombre) for e in EMPRESAS],
        on_select=on_cambiar_empresa,
    )

    # Empresa y cuenta para el flujo de Contado, INDEPENDIENTES del CSV (puedes
    # trabajar el CSV con una empresa y Contado con otra sin regresar a cambiar).
    empresa_contado_ref = [EMPRESA_DEFAULT]

    def _opciones_cuenta_contado() -> list:
        return [ft.dropdown.Option(key=c.id_sipp, text=c.nombre) for c in empresa_contado_ref[0].cuentas]

    cuenta_contado_dropdown = ft.Dropdown(
        label="Cuenta Bancaria por default (SIPP)",
        width=420,
        color=ft.Colors.ON_SURFACE,
        editable=True,
        enable_filter=True,
        menu_height=300,
        options=_opciones_cuenta_contado(),
    )

    def on_cambiar_empresa_contado(e) -> None:
        empresa_contado_ref[0] = EMPRESA_POR_CLAVE.get(e.control.value, EMPRESA_DEFAULT)
        cuenta_contado_dropdown.options = _opciones_cuenta_contado()
        cuenta_contado_dropdown.value = None
        # Los ids de cuenta cambian por empresa: limpiamos la asignación previa.
        for p in pagos_contado:
            p.cuenta_bancaria = ""
        refrescar_tabla_pagos_contado()
        page.update()

    empresa_contado_dropdown = ft.Dropdown(
        label="Empresa (SIPP)",
        width=220,
        color=ft.Colors.ON_SURFACE,
        value=EMPRESA_DEFAULT.clave,
        options=[ft.dropdown.Option(key=e.clave, text=e.nombre) for e in EMPRESAS],
        on_select=on_cambiar_empresa_contado,
    )

    ingresos_div_usuario_field = ft.TextField(label="Usuario SIPP", autofocus=True)
    ingresos_div_password_field = ft.TextField(label="Contraseña SIPP", password=True, can_reveal_password=True)
    ingresos_div_recordar_check = ft.Checkbox(label="Recordar credenciales en este equipo")
    ingresos_div_progreso_text = ft.Text("")

    def on_cancelar_ingresos_div(_e) -> None:
        page.pop_dialog()

    async def on_confirmar_ingresos_div(_e) -> None:
        usuario = (ingresos_div_usuario_field.value or "").strip()
        password = ingresos_div_password_field.value or ""
        if not usuario or not password:
            ingresos_div_progreso_text.value = "Usuario y contraseña son obligatorios."
            page.update()
            return

        if ingresos_div_recordar_check.value:
            guardar_credenciales(usuario, password)
        else:
            borrar_credenciales()

        boton_ingresos_div_confirmar.disabled = True
        boton_ingresos_div_cancelar.disabled = True
        page.update()

        def log_fn(mensaje: str, _nivel: str = "info") -> None:
            ingresos_div_progreso_text.value = mensaje
            page.update()

        cuenta_nombre = nombre_cuenta_bancaria(cuenta_bancaria_dropdown.value or "")
        try:
            enviados = await cargar_ingresos_diversos_en_sipp(
                movimientos,
                cuenta_nombre,
                fecha_operacion_field.value or "",
                ultima_ruta_csv[0],
                usuario,
                password,
                estado_cuenta=estado_cuenta_ref[0],
                empresa=empresa_ref[0],
                headless=False,
                log_fn=log_fn,
            )
            estado_text.value = (
                f"Carga a SIPP (Ingresos Diversos) lista: {enviados} movimiento(s) identificado(s) "
                "enviados a emparejar. Revisa la previsualización y guarda manualmente en SIPP."
            )
        except Exception as ex:
            estado_text.value = f"Error al cargar Ingresos Diversos en SIPP: {ex}"
        finally:
            boton_ingresos_div_confirmar.disabled = False
            boton_ingresos_div_cancelar.disabled = False
            ingresos_div_password_field.value = ""
            page.pop_dialog()
            page.update()

    boton_ingresos_div_cancelar = ft.TextButton("Cancelar", on_click=on_cancelar_ingresos_div)
    boton_ingresos_div_confirmar = ft.Button(
        "Cargar", on_click=on_confirmar_ingresos_div, bgcolor=NAVY, color=ft.Colors.WHITE
    )

    dialogo_ingresos_div = ft.AlertDialog(
        modal=True,
        title=ft.Text("Cargar a SIPP: Ingresos Diversos"),
        content=ft.Column(
            [
                ingresos_div_progreso_text,
                ingresos_div_usuario_field,
                ingresos_div_password_field,
                ingresos_div_recordar_check,
            ],
            tight=True,
            spacing=12,
            width=350,
        ),
        actions=[boton_ingresos_div_cancelar, boton_ingresos_div_confirmar],
    )

    def on_click_cargar_ingresos_div(_e) -> None:
        if not movimientos:
            estado_text.value = "Primero carga un archivo bancario."
            page.update()
            return
        if not ultima_ruta_csv[0] or not os.path.exists(ultima_ruta_csv[0]):
            estado_text.value = "No hay un archivo bancario disponible para volver a subir; carga uno de nuevo."
            page.update()
            return
        if not cuenta_bancaria_dropdown.value:
            estado_text.value = "Selecciona la Cuenta Bancaria (SIPP) antes de continuar."
            page.update()
            return
        if not (fecha_operacion_field.value or "").strip():
            estado_text.value = "Indica la Fecha de Operación antes de continuar."
            page.update()
            return
        if estado_cuenta_ref[0] is None:
            estado_text.value = (
                "Carga primero el Estado de Cuenta (.xlsx) — se usa para sugerir la sucursal de cada movimiento."
            )
            page.update()
            return
        identificados = sum(1 for m in movimientos if m.identificado)
        if identificados == 0:
            estado_text.value = "No hay movimientos identificados (con cliente asignado) para enviar."
            page.update()
            return

        usuario_guardado, password_guardado = cargar_credenciales()
        ingresos_div_usuario_field.value = usuario_guardado
        ingresos_div_password_field.value = password_guardado
        ingresos_div_recordar_check.value = bool(usuario_guardado)
        ingresos_div_progreso_text.value = f"{identificados} movimiento(s) identificado(s) se intentarán emparejar."
        mostrar_dialogo(dialogo_ingresos_div)

    boton_cargar_ingresos_div = ft.Button(
        "Cargar a SIPP (Ingresos Diversos)",
        icon=ft.Icons.ACCOUNT_BALANCE,
        on_click=on_click_cargar_ingresos_div,
        bgcolor=NAVY,
        color=ft.Colors.WHITE,
    )

    # ──────────────────────────────────────────────────────
    # Pestaña: Buzón O365 (Microsoft Graph)
    # ──────────────────────────────────────────────────────
    correos_o365: list[CorreoResumen] = []
    estado_o365_text = ft.Text("")

    def columna_o365(
        texto: str,
        fixed_width: Optional[float] = None,
        size: Optional[DataColumnSize] = None,
    ) -> DataColumn2:
        return DataColumn2(
            ft.Text(texto, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
            fixed_width=fixed_width,
            size=size,
        )

    tabla_o365 = DataTable2(
        columns=[
            columna_o365("Fecha", fixed_width=150),
            columna_o365("Remitente", fixed_width=280),
            columna_o365("Asunto", size=DataColumnSize.L),
            columna_o365("Adjuntos", fixed_width=90),
            columna_o365("Acciones", fixed_width=110),
        ],
        rows=[],
        min_width=1100,
        fixed_top_rows=1,
        column_spacing=16,
        expand=True,
    )

    async def on_descargar_adjunto(_e, correo: CorreoResumen) -> None:
        estado_o365_text.value = f"Descargando adjuntos de '{correo.asunto}'..."
        page.update()
        destino_dir = tempfile.mkdtemp(prefix="mh_cobranza_o365_")
        try:
            rutas = await asyncio.to_thread(descargar_adjuntos, correo.mensaje, destino_dir)
            if not rutas:
                estado_o365_text.value = f"'{correo.asunto}' no tiene adjuntos."
                page.update()
                return

            # Si hay un CSV, es el archivo bancario: se carga en Conciliación.
            csv_rutas = [r for r in rutas if r.lower().endswith(".csv")]
            if csv_rutas:
                archivo_nombre_text.value = os.path.basename(csv_rutas[0])
                archivo_nombre_text.italic = False
                archivo_nombre_text.color = ft.Colors.ON_SURFACE
                procesar_csv_path(csv_rutas[0])
                estado_o365_text.value = f"Adjunto de '{correo.asunto}' cargado en Conciliación Bancaria."
                tabs.selected_index = 0
                page.update()
                return

            # Cualquier otro adjunto (PDF, imagen, etc.): se copia a Descargas y
            # se revela en el explorador para que el usuario lo guarde/abra.
            descargas_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            os.makedirs(descargas_dir, exist_ok=True)
            guardados = []
            for ruta in rutas:
                destino = os.path.join(descargas_dir, os.path.basename(ruta))
                await asyncio.to_thread(shutil.copy2, ruta, destino)
                guardados.append(destino)
            if guardados:
                revelar_en_explorador(guardados[0])
            nombres = ", ".join(os.path.basename(g) for g in guardados)
            estado_o365_text.value = f"{len(guardados)} adjunto(s) descargado(s) a Descargas: {nombres}"
            page.update()
        except Exception as ex:
            estado_o365_text.value = f"Error al descargar adjuntos: {ex}"
            page.update()

    def celda_o365(texto: str) -> ft.DataCell:
        return ft.DataCell(ft.Text(texto, color=ft.Colors.ON_SURFACE))

    def boton_descargar_o365(correo: CorreoResumen) -> ft.Control:
        if not correo.tiene_adjuntos:
            return ft.Text("-", color=ft.Colors.ON_SURFACE_VARIANT)
        return ft.IconButton(
            icon=ft.Icons.DOWNLOAD,
            tooltip="Descargar adjunto(s) (CSV se carga en Conciliación; otros se guardan en Descargas)",
            icon_color=NAVY,
            on_click=lambda e, c=correo: page.run_task(on_descargar_adjunto, e, c),
        )

    # --- Detalle de correo (cuerpo completo + visor de adjuntos) ---
    detalle_titulo_text = ft.Text("", weight=ft.FontWeight.BOLD, size=16)
    detalle_meta_text = ft.Text("", color=ft.Colors.ON_SURFACE_VARIANT, size=12)
    detalle_cuerpo_text = ft.Text("", selectable=True)
    detalle_adjuntos_column = ft.Column([], spacing=8)

    def on_cerrar_detalle(_e) -> None:
        page.pop_dialog()

    dialogo_detalle = ft.AlertDialog(
        modal=True,
        title=detalle_titulo_text,
        content=ft.Column(
            [
                detalle_meta_text,
                ft.Divider(),
                detalle_cuerpo_text,
                ft.Divider(),
                ft.Text("Adjuntos:", weight=ft.FontWeight.BOLD),
                detalle_adjuntos_column,
            ],
            tight=True,
            spacing=10,
            width=560,
            height=520,
            scroll=ft.ScrollMode.ALWAYS,
        ),
        actions=[ft.TextButton("Cerrar", on_click=on_cerrar_detalle)],
    )

    def control_adjunto(ruta: str) -> ft.Control:
        nombre = os.path.basename(ruta)
        extension = os.path.splitext(nombre)[1].lower()
        if extension in EXTENSIONES_IMAGEN:
            with open(ruta, "rb") as f:
                datos = f.read()
            return ft.Column(
                [
                    ft.Text(nombre, size=12),
                    ft.Image(src=datos, width=480, fit=ft.BoxFit.CONTAIN),
                ],
                spacing=4,
            )
        return ft.Row(
            [
                ft.Text(nombre, expand=True),
                ft.IconButton(
                    icon=ft.Icons.OPEN_IN_NEW,
                    tooltip="Abrir con la aplicación predeterminada del sistema",
                    icon_color=NAVY,
                    on_click=lambda _e, r=ruta: abrir_archivo_con_app_predeterminada(r),
                ),
            ],
        )

    async def on_ver_detalle(_e, correo: CorreoResumen) -> None:
        detalle_titulo_text.value = correo.asunto
        fecha_texto = correo.fecha.strftime("%d/%m/%Y %H:%M") if correo.fecha else "-"
        detalle_meta_text.value = f"De: {correo.remitente} · {fecha_texto}"
        detalle_cuerpo_text.value = "Cargando..."
        detalle_adjuntos_column.controls = []
        mostrar_dialogo(dialogo_detalle)
        page.update()

        try:
            cuerpo = await asyncio.to_thread(obtener_cuerpo, correo.mensaje)
            detalle_cuerpo_text.value = cuerpo or "(sin contenido)"

            if correo.tiene_adjuntos:
                destino_dir = tempfile.mkdtemp(prefix="mh_cobranza_o365_detalle_")
                rutas = await asyncio.to_thread(descargar_adjuntos, correo.mensaje, destino_dir)
                detalle_adjuntos_column.controls = [control_adjunto(ruta) for ruta in rutas]
            else:
                detalle_adjuntos_column.controls = [ft.Text("(sin adjuntos)", color=ft.Colors.ON_SURFACE_VARIANT)]
        except Exception as ex:
            detalle_cuerpo_text.value = f"Error al cargar el correo: {ex}"
        page.update()

    def boton_ver_detalle(correo: CorreoResumen) -> ft.Control:
        return ft.IconButton(
            icon=ft.Icons.VISIBILITY,
            tooltip="Ver mensaje completo",
            icon_color=NAVY,
            on_click=lambda e, c=correo: page.run_task(on_ver_detalle, e, c),
        )

    filtro_o365_concepto = ft.TextField(
        label="Filtrar por concepto/asunto",
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        width=320,
        on_change=lambda _e: refrescar_tabla_o365(),
    )
    # Filtro de fecha vía DatePicker (en vez de teclear la fecha completa).
    fecha_filtro_sel: list[Optional[date]] = [None]
    texto_fecha_filtro = ft.Text("", color=ft.Colors.ON_SURFACE)

    def on_cambiar_fecha_filtro(e) -> None:
        val = e.control.value  # datetime seleccionado (o None)
        fecha_filtro_sel[0] = val.date() if val else None
        texto_fecha_filtro.value = val.strftime("%d/%m/%Y") if val else ""
        refrescar_tabla_o365()

    date_picker_o365 = ft.DatePicker(
        first_date=datetime(2020, 1, 1),
        last_date=datetime(2035, 12, 31),
        on_change=on_cambiar_fecha_filtro,
    )

    boton_filtro_fecha = ft.OutlinedButton(
        "Filtrar por fecha",
        icon=ft.Icons.CALENDAR_MONTH,
        on_click=lambda _e: page.show_dialog(date_picker_o365),
    )

    def limpiar_filtro_fecha(_e) -> None:
        fecha_filtro_sel[0] = None
        date_picker_o365.value = None
        texto_fecha_filtro.value = ""
        refrescar_tabla_o365()

    boton_limpiar_fecha = ft.IconButton(
        icon=ft.Icons.CLOSE,
        tooltip="Quitar filtro de fecha",
        on_click=limpiar_filtro_fecha,
    )

    def refrescar_tabla_o365() -> None:
        f_concepto = (filtro_o365_concepto.value or "").strip().lower()
        f_fecha = fecha_filtro_sel[0]

        def pasa(correo: CorreoResumen) -> bool:
            if f_concepto and f_concepto not in (correo.asunto or "").lower():
                return False
            if f_fecha is not None:
                if not correo.fecha or correo.fecha.date() != f_fecha:
                    return False
            return True

        visibles = [c for c in correos_o365 if pasa(c)]
        tabla_o365.rows = [
            ft.DataRow(
                cells=[
                    celda_o365(correo.fecha.strftime("%d/%m/%Y %H:%M") if correo.fecha else "-"),
                    celda_o365(correo.remitente),
                    celda_o365(correo.asunto[:80]),
                    celda_o365("Sí" if correo.tiene_adjuntos else "No"),
                    ft.DataCell(
                        ft.Row([boton_ver_detalle(correo), boton_descargar_o365(correo)], spacing=0)
                    ),
                ],
            )
            for correo in visibles
        ]
        page.update()

    correos_vistos_ids: set[str] = set()
    primera_carga_o365 = [True]

    def notificar(mensaje: str) -> None:
        notificacion_snackbar.content = ft.Text(mensaje)
        notificacion_snackbar.open = True
        page.update()

    async def actualizar_bandeja_o365(_e=None) -> None:
        nonlocal correos_o365
        estado_o365_text.value = "Conectando con el buzón..."
        page.update()
        try:
            cuenta = await asyncio.to_thread(obtener_cuenta)
            if cuenta is None:
                estado_o365_text.value = (
                    "No autenticado. Corre `python auth_o365.py` en una terminal, en la "
                    "raíz del proyecto, para generar el token y vuelve a intentar."
                )
                correos_o365 = []
                refrescar_tabla_o365()
                page.update()
                return

            correos_o365 = await asyncio.to_thread(listar_correos, cuenta, 150, "Contado")
            estado_o365_text.value = (
                f"{len(correos_o365)} correo(s) con 'Contado' en el asunto cargado(s) de la bandeja de entrada."
            )
            refrescar_tabla_o365()

            if primera_carga_o365[0]:
                # Primera carga: solo establece la línea base, sin disparar
                # extracción/notificación para correos que ya estaban ahí.
                correos_vistos_ids.update(c.id_correo for c in correos_o365)
                primera_carga_o365[0] = False
            else:
                nuevos = [c for c in correos_o365 if c.id_correo not in correos_vistos_ids]
                for correo in nuevos:
                    correos_vistos_ids.add(correo.id_correo)
                    pago = await extraer_un_pago_contado(correo)
                    pagos_contado.append(pago)
                    notificar(f"Pago de contado detectado y extraído: {correo.asunto[:80]}")
                if nuevos:
                    refrescar_tabla_pagos_contado()
        except Exception as ex:
            estado_o365_text.value = f"Error al consultar el buzón: {ex}"
        page.update()

    boton_actualizar_o365 = ft.Button(
        "Actualizar bandeja",
        icon=ft.Icons.REFRESH,
        on_click=actualizar_bandeja_o365,
        bgcolor=NAVY,
        color=ft.Colors.WHITE,
    )

    # ──────────────────────────────────────────────────────
    # Extracción de "Pagos de Contado" desde los correos filtrados
    # (Fecha de Operación y Cuenta Bancaria se declaran una sola vez,
    # arriba, en la pestaña Conciliación Bancaria — igual que en SIPP)
    # ──────────────────────────────────────────────────────
    pagos_contado: list[PagoContadoExtraido] = []
    pagos_progreso_text = ft.Text("")

    pago_a_identificar: list[Optional[PagoContadoExtraido]] = [None]
    pago_cliente_info_text = ft.Text("")
    pago_cliente_autocomplete = ft.AutoComplete(suggestions=sugerencias_clientes)

    def on_select_cliente_pago(e: ft.AutoCompleteSelectEvent) -> None:
        pago = pago_a_identificar[0]
        if pago is None:
            return
        pago.cliente_match = e.selection.value
        page.pop_dialog()
        refrescar_tabla_pagos_contado()

    pago_cliente_autocomplete.on_select = on_select_cliente_pago

    def on_cancelar_pago_cliente(_e) -> None:
        page.pop_dialog()

    dialogo_pago_cliente = ft.AlertDialog(
        modal=True,
        title=ft.Text("Seleccionar cliente"),
        content=ft.Column(
            [pago_cliente_info_text, pago_cliente_autocomplete],
            tight=True,
            spacing=12,
            width=400,
        ),
        actions=[ft.TextButton("Cancelar", on_click=on_cancelar_pago_cliente)],
    )

    def abrir_dialogo_pago_cliente(pago: PagoContadoExtraido) -> None:
        pago_a_identificar[0] = pago
        pago_cliente_info_text.value = f"{pago.concepto[:90]}\nSugerencia detectada: {pago.cliente_texto or '(ninguna)'}"
        pago_cliente_autocomplete.value = pago.cliente_texto or ""
        mostrar_dialogo(dialogo_pago_cliente)

    texto_adjunto_pago_text = ft.Text("", selectable=True)
    dialogo_texto_adjunto = ft.AlertDialog(
        modal=True,
        title=ft.Text("Texto extraído del comprobante"),
        content=ft.Column(
            [texto_adjunto_pago_text],
            tight=True,
            spacing=12,
            width=560,
            height=420,
            scroll=ft.ScrollMode.ALWAYS,
        ),
        actions=[ft.TextButton("Cerrar", on_click=lambda _e: page.pop_dialog())],
    )

    def abrir_dialogo_texto_adjunto(pago: PagoContadoExtraido) -> None:
        if pago.error and not pago.texto_adjunto:
            texto_adjunto_pago_text.value = pago.error
        else:
            texto_adjunto_pago_text.value = pago.texto_adjunto or "(sin texto extraído)"
        mostrar_dialogo(dialogo_texto_adjunto)

    def quitar_pago_contado(pago: PagoContadoExtraido) -> None:
        if pago in pagos_contado:
            pagos_contado.remove(pago)
        refrescar_tabla_pagos_contado()

    def celda_campo_pago(valor: str, on_change) -> ft.DataCell:
        return ft.DataCell(
            ft.TextField(value=valor, dense=True, content_padding=6, on_change=on_change)
        )

    tabla_pagos_contado = DataTable2(
        columns=[
            columna_o365("Fecha"),
            columna_o365("Concepto"),
            columna_o365("Tipo"),
            columna_o365("Banco"),
            columna_o365("Plaza"),
            columna_o365("Monto"),
            columna_o365("Cliente"),
            columna_o365("Referencia"),
            columna_o365("Cuenta Bancaria"),
            columna_o365("Acciones"),
        ],
        rows=[],
        min_width=1500,
        fixed_top_rows=1,
        column_spacing=12,
        expand=True,
    )

    def refrescar_tabla_pagos_contado() -> None:
        filas = []
        for pago in pagos_contado:
            fecha_texto = pago.correo.fecha.strftime("%d/%m/%Y") if pago.correo.fecha else "-"

            def on_change_concepto(e, p=pago):
                p.concepto = e.control.value

            def on_change_plaza(e, p=pago):
                p.plaza = e.control.value

            def on_change_monto(e, p=pago):
                p.monto = _parsear_monto_pago(e.control.value)

            def on_change_referencia(e, p=pago):
                p.referencia = e.control.value

            def on_change_tipo(e, p=pago):
                p.tipo_movimiento = e.control.value

            def on_change_cuenta(e, p=pago):
                p.cuenta_bancaria = e.control.value or ""

            # Hereda la cuenta del encabezado (Conciliación Bancaria) si la fila
            # aún no tiene una; el usuario puede sobrescribirla por fila.
            if not pago.cuenta_bancaria and cuenta_contado_dropdown.value:
                pago.cuenta_bancaria = cuenta_contado_dropdown.value

            cuenta_dropdown = ft.DataCell(
                ft.Dropdown(
                    value=pago.cuenta_bancaria or None,
                    dense=True,
                    editable=True,        # campo escribible
                    enable_filter=True,   # filtra la lista mientras escribes
                    menu_height=300,      # tope de altura: el resto hace scroll
                    options=[
                        ft.dropdown.Option(key=c.id_sipp, text=c.nombre)
                        for c in empresa_contado_ref[0].cuentas
                    ],
                    on_select=on_change_cuenta,
                )
            )

            tipo_dropdown = ft.DataCell(
                ft.Dropdown(
                    value=pago.tipo_movimiento or None,
                    dense=True,
                    options=[
                        ft.dropdown.Option(""),
                        ft.dropdown.Option("Anticipo"),
                        ft.dropdown.Option("Contado"),
                    ],
                    on_select=on_change_tipo,
                )
            )

            acciones = ft.DataCell(
                ft.Row(
                    [
                        ft.IconButton(
                            icon=ft.Icons.PERSON_SEARCH,
                            tooltip="Seleccionar cliente",
                            icon_color=NAVY,
                            on_click=lambda _e, p=pago: abrir_dialogo_pago_cliente(p),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.ARTICLE,
                            tooltip="Ver texto extraído del comprobante",
                            icon_color=NAVY,
                            on_click=lambda _e, p=pago: abrir_dialogo_texto_adjunto(p),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            tooltip="Quitar de la lista",
                            icon_color=ft.Colors.RED_600,
                            on_click=lambda _e, p=pago: quitar_pago_contado(p),
                        ),
                    ],
                    spacing=0,
                )
            )

            filas.append(
                ft.DataRow(
                    cells=[
                        celda_o365(fecha_texto),
                        celda_campo_pago(pago.concepto, on_change_concepto),
                        tipo_dropdown,
                        celda_o365(pago.banco_detectado or "-"),
                        celda_campo_pago(pago.plaza, on_change_plaza),
                        celda_campo_pago(f"{pago.monto:.2f}" if pago.monto is not None else "", on_change_monto),
                        celda_o365(pago.cliente_match or f"(sugerido: {pago.cliente_texto})" if pago.cliente_texto else (pago.cliente_match or "-")),
                        celda_campo_pago(pago.referencia, on_change_referencia),
                        cuenta_dropdown,
                        acciones,
                    ],
                )
            )
        tabla_pagos_contado.rows = filas
        page.update()

    async def extraer_un_pago_contado(correo: CorreoResumen) -> PagoContadoExtraido:
        try:
            cuerpo = await asyncio.to_thread(obtener_cuerpo, correo.mensaje)
            pago = extraer_pago_contado(correo, cuerpo, clientes_normalizados, sucursales_catalogo)
            destino_dir = tempfile.mkdtemp(prefix="mh_cobranza_pago_contado_")
            await asyncio.to_thread(completar_con_adjunto, pago, destino_dir)
        except Exception as ex:
            pago = PagoContadoExtraido(correo=correo, concepto=correo.asunto, error=f"Error al extraer: {ex}")
        return pago

    async def on_click_extraer_pagos_contado(_e) -> None:
        ids_extraidos = {p.correo.id_correo for p in pagos_contado}
        nuevos = [c for c in correos_o365 if c.id_correo not in ids_extraidos]
        if not nuevos:
            pagos_progreso_text.value = "No hay correos nuevos por extraer (actualiza la bandeja o ya se extrajeron todos)."
            page.update()
            return

        for i, correo in enumerate(nuevos):
            pagos_progreso_text.value = f"Extrayendo {i + 1}/{len(nuevos)}: {correo.asunto[:60]}..."
            page.update()
            pagos_contado.append(await extraer_un_pago_contado(correo))
            refrescar_tabla_pagos_contado()

        pagos_progreso_text.value = f"Extracción completa: {len(pagos_contado)} pago(s) de contado en revisión."
        page.update()

    boton_extraer_pagos_contado = ft.Button(
        "Extraer pagos de contado",
        icon=ft.Icons.FIND_IN_PAGE,
        on_click=lambda e: page.run_task(on_click_extraer_pagos_contado, e),
        bgcolor=ORANGE,
        color=ft.Colors.WHITE,
    )

    # --- Carga de Pagos de Contado a SIPP (reutiliza Fecha/Cuenta Bancaria
    # declarados arriba, en la pestaña Conciliación Bancaria) ---
    enviar_automaticamente_switch = ft.Switch(
        label="Enviar automáticamente (Guardar y Enviar) sin pausa para revisión",
        value=False,
    )
    pagos_sipp_usuario_field = ft.TextField(label="Usuario SIPP", autofocus=True)
    pagos_sipp_password_field = ft.TextField(label="Contraseña SIPP", password=True, can_reveal_password=True)
    pagos_sipp_recordar_check = ft.Checkbox(label="Recordar credenciales en este equipo")
    pagos_sipp_progreso_text = ft.Text("")

    def on_cancelar_pagos_sipp(_e) -> None:
        page.pop_dialog()

    async def on_confirmar_pagos_sipp(_e) -> None:
        usuario = (pagos_sipp_usuario_field.value or "").strip()
        password = pagos_sipp_password_field.value or ""
        if not usuario or not password:
            pagos_sipp_progreso_text.value = "Usuario y contraseña son obligatorios."
            page.update()
            return

        if pagos_sipp_recordar_check.value:
            guardar_credenciales(usuario, password)
        else:
            borrar_credenciales()

        boton_pagos_sipp_confirmar.disabled = True
        boton_pagos_sipp_cancelar.disabled = True
        page.update()

        def log_fn(mensaje: str, _nivel: str = "info") -> None:
            pagos_sipp_progreso_text.value = mensaje
            page.update()

        confirmados = [p for p in pagos_contado if p.cliente_match and p.plaza and p.monto is not None]
        try:
            enviados = await cargar_pagos_contado_en_sipp(
                confirmados,
                fecha_operacion_field.value or "",
                usuario,
                password,
                empresa=empresa_contado_ref[0],
                headless=False,
                log_fn=log_fn,
                enviar_automaticamente=enviar_automaticamente_switch.value,
            )
            estado_o365_text.value = f"Carga a SIPP (Pagos de Contado) lista: {enviados} movimiento(s) enviados."
        except Exception as ex:
            estado_o365_text.value = f"Error al cargar Pagos de Contado en SIPP: {ex}"
        finally:
            boton_pagos_sipp_confirmar.disabled = False
            boton_pagos_sipp_cancelar.disabled = False
            pagos_sipp_password_field.value = ""
            page.pop_dialog()
            page.update()

    boton_pagos_sipp_cancelar = ft.TextButton("Cancelar", on_click=on_cancelar_pagos_sipp)
    boton_pagos_sipp_confirmar = ft.Button(
        "Cargar", on_click=on_confirmar_pagos_sipp, bgcolor=NAVY, color=ft.Colors.WHITE
    )

    dialogo_pagos_sipp = ft.AlertDialog(
        modal=True,
        title=ft.Text("Cargar Pagos de Contado a SIPP"),
        content=ft.Column(
            [
                pagos_sipp_progreso_text,
                pagos_sipp_usuario_field,
                pagos_sipp_password_field,
                pagos_sipp_recordar_check,
            ],
            tight=True,
            spacing=12,
            width=350,
        ),
        actions=[boton_pagos_sipp_cancelar, boton_pagos_sipp_confirmar],
    )

    def on_click_cargar_pagos_sipp(_e) -> None:
        if not pagos_contado:
            estado_o365_text.value = "Primero extrae los pagos de contado."
            page.update()
            return
        if not (fecha_operacion_field.value or "").strip():
            estado_o365_text.value = "Indica la Fecha de Operación, arriba en Conciliación Bancaria."
            page.update()
            return
        confirmados = [p for p in pagos_contado if p.cliente_match and p.plaza and p.monto is not None]
        if not confirmados:
            estado_o365_text.value = (
                "Ningún pago tiene Cliente + Plaza + Monto confirmados todavía; revísalos en la tabla."
            )
            page.update()
            return
        if any(not p.cuenta_bancaria for p in confirmados):
            estado_o365_text.value = (
                "Asigna la Cuenta Bancaria a cada pago confirmado en la columna 'Cuenta Bancaria' de la tabla."
            )
            page.update()
            return

        usuario_guardado, password_guardado = cargar_credenciales()
        pagos_sipp_usuario_field.value = usuario_guardado
        pagos_sipp_password_field.value = password_guardado
        pagos_sipp_recordar_check.value = bool(usuario_guardado)
        pagos_sipp_progreso_text.value = f"{len(confirmados)}/{len(pagos_contado)} pago(s) listo(s) para enviar."
        mostrar_dialogo(dialogo_pagos_sipp)

    boton_cargar_pagos_sipp = ft.Button(
        "Cargar Pagos de Contado a SIPP",
        icon=ft.Icons.ACCOUNT_BALANCE,
        on_click=on_click_cargar_pagos_sipp,
        bgcolor=NAVY,
        color=ft.Colors.WHITE,
    )

    contenido_o365 = ft.Container(
        content=ft.Column(
            [
                ft.Row([boton_actualizar_o365], spacing=16),
                estado_o365_text,
                ft.Row(
                    [
                        boton_filtro_fecha,
                        texto_fecha_filtro,
                        boton_limpiar_fecha,
                        filtro_o365_concepto,
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(),
                ft.Container(content=tabla_o365, expand=True),
                ft.Divider(),
                ft.Text("Pagos de Contado (revisar antes de cargar a SIPP)", weight=ft.FontWeight.BOLD, size=16),
                ft.Row(
                    [empresa_contado_dropdown, cuenta_contado_dropdown],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row([boton_extraer_pagos_contado], spacing=16),
                pagos_progreso_text,
                ft.Container(content=tabla_pagos_contado, expand=True),
                ft.Row([enviar_automaticamente_switch, boton_cargar_pagos_sipp], spacing=16),
            ],
            spacing=12,
            scroll=ft.ScrollMode.ALWAYS,
        ),
        padding=20,
        expand=True,
    )

    # ──────────────────────────────────────────────────────
    # Pestaña: Catálogos
    # ──────────────────────────────────────────────────────
    # --- Catálogo Cuentas-Clientes (CRUD completo) ---
    filtro_catalogo_token = [0]

    async def _filtrar_catalogo_con_debounce(token: int) -> None:
        await asyncio.sleep(0.3)
        if token == filtro_catalogo_token[0]:
            refrescar_tabla_catalogo()

    def on_cambio_filtro_catalogo(_e) -> None:
        filtro_catalogo_token[0] += 1
        page.run_task(_filtrar_catalogo_con_debounce, filtro_catalogo_token[0])

    filtro_catalogo_texto = ft.TextField(
        label="Buscar cuenta, cliente, banco o plaza",
        width=400,
        color=ft.Colors.ON_SURFACE,
        on_change=on_cambio_filtro_catalogo,
        on_submit=lambda _e: refrescar_tabla_catalogo(),
    )
    catalogo_estado_text = ft.Text("")
    LIMITE_RESULTADOS_CATALOGO = 200

    def columna_catalogos(texto: str) -> DataColumn2:
        return DataColumn2(ft.Text(texto, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE))

    tabla_catalogo = DataTable2(
        columns=[
            columna_catalogos("Cuenta"),
            columna_catalogos("Cliente"),
            columna_catalogos("Banco"),
            columna_catalogos("Plaza"),
            columna_catalogos("Acciones"),
        ],
        rows=[],
        min_width=800,
        fixed_top_rows=1,
        column_spacing=16,
        expand=True,
    )

    item_catalogo_editando: list[Optional[ClienteCuenta]] = [None]
    campo_catalogo_cuenta = ft.TextField(label="Cuenta")
    campo_catalogo_cliente = ft.TextField(label="Cliente")
    campo_catalogo_banco = ft.TextField(label="Banco")
    campo_catalogo_plaza = ft.TextField(label="Plaza")

    def aplicar_filtro_catalogo() -> list[ClienteCuenta]:
        texto = (filtro_catalogo_texto.value or "").strip().lower()
        if not texto:
            return catalogo
        return [
            c for c in catalogo
            if texto in c.cuenta.lower()
            or texto in c.cliente.lower()
            or texto in c.banco.lower()
            or texto in c.plaza.lower()
        ]

    def on_cerrar_dialogo_catalogo(_e) -> None:
        page.pop_dialog()

    def on_guardar_item_catalogo(_e) -> None:
        cuenta = (campo_catalogo_cuenta.value or "").strip()
        cliente = (campo_catalogo_cliente.value or "").strip()
        banco = (campo_catalogo_banco.value or "").strip()
        plaza = (campo_catalogo_plaza.value or "").strip()
        if not cuenta or not cliente:
            catalogo_estado_text.value = "Cuenta y Cliente son obligatorios."
            page.update()
            return

        item = item_catalogo_editando[0]
        if item is None:
            if any(c.cuenta == cuenta for c in catalogo):
                catalogo_estado_text.value = f"Ya existe una cuenta '{cuenta}' en el catálogo."
                page.update()
                return
            catalogo.append(ClienteCuenta(cuenta=cuenta, cliente=cliente, banco=banco, plaza=plaza))
        else:
            item.cuenta, item.cliente, item.banco, item.plaza = cuenta, cliente, banco, plaza

        guardar_catalogo_completo(CATALOGO_PATH, catalogo)
        catalogo_info_text.value = f"Catálogo de clientes cargado: {len(catalogo)} cuentas"
        catalogo_estado_text.value = "Catálogo actualizado y guardado."
        page.pop_dialog()
        refrescar_tabla_catalogo()

    dialogo_catalogo = ft.AlertDialog(
        modal=True,
        title=ft.Text("Cuenta del catálogo"),
        content=ft.Column(
            [campo_catalogo_cuenta, campo_catalogo_cliente, campo_catalogo_banco, campo_catalogo_plaza],
            tight=True,
            spacing=12,
            width=350,
        ),
        actions=[
            ft.TextButton("Cancelar", on_click=on_cerrar_dialogo_catalogo),
            ft.Button("Guardar", on_click=on_guardar_item_catalogo, bgcolor=NAVY, color=ft.Colors.WHITE),
        ],
    )

    def abrir_dialogo_catalogo(item: Optional[ClienteCuenta]) -> None:
        item_catalogo_editando[0] = item
        campo_catalogo_cuenta.value = item.cuenta if item else ""
        campo_catalogo_cliente.value = item.cliente if item else ""
        campo_catalogo_banco.value = item.banco if item else ""
        campo_catalogo_plaza.value = item.plaza if item else ""
        dialogo_catalogo.title = ft.Text("Editar cuenta" if item else "Agregar cuenta")
        mostrar_dialogo(dialogo_catalogo)

    def eliminar_item_catalogo(item: ClienteCuenta) -> None:
        catalogo.remove(item)
        guardar_catalogo_completo(CATALOGO_PATH, catalogo)
        catalogo_info_text.value = f"Catálogo de clientes cargado: {len(catalogo)} cuentas"
        catalogo_estado_text.value = f"Cuenta '{item.cuenta}' eliminada."
        refrescar_tabla_catalogo()

    def refrescar_tabla_catalogo() -> None:
        todas = aplicar_filtro_catalogo()
        truncado = len(todas) > LIMITE_RESULTADOS_CATALOGO
        filas = todas[:LIMITE_RESULTADOS_CATALOGO]
        catalogo_estado_text.value = (
            f"Mostrando los primeros {LIMITE_RESULTADOS_CATALOGO} de {len(todas)} "
            "resultados; refina la búsqueda para ver otros." if truncado else f"{len(filas)} resultado(s)."
        )
        tabla_catalogo.rows = [
            ft.DataRow(
                cells=[
                    celda_o365(item.cuenta),
                    celda_o365(item.cliente),
                    celda_o365(item.banco),
                    celda_o365(item.plaza),
                    ft.DataCell(
                        ft.Row(
                            [
                                ft.IconButton(
                                    icon=ft.Icons.EDIT,
                                    tooltip="Editar",
                                    icon_color=NAVY,
                                    on_click=lambda _e, i=item: abrir_dialogo_catalogo(i),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE,
                                    tooltip="Eliminar",
                                    icon_color=ft.Colors.RED_600,
                                    on_click=lambda _e, i=item: eliminar_item_catalogo(i),
                                ),
                            ],
                            spacing=0,
                        )
                    ),
                ],
            )
            for item in filas
        ]
        page.update()

    boton_agregar_catalogo = ft.Button(
        "Agregar cuenta",
        icon=ft.Icons.ADD,
        on_click=lambda _e: abrir_dialogo_catalogo(None),
        bgcolor=NAVY,
        color=ft.Colors.WHITE,
    )

    refrescar_tabla_catalogo()

    contenido_catalogos = ft.Container(
        content=ft.Column(
            [
                ft.Text("Catálogo Cuentas-Clientes", weight=ft.FontWeight.BOLD, size=16),
                ft.Row([filtro_catalogo_texto, boton_agregar_catalogo], spacing=16),
                catalogo_estado_text,
                ft.Container(content=tabla_catalogo, expand=True),
            ],
            spacing=12,
        ),
        padding=20,
        expand=True,
    )

    def on_click_tema(_e) -> None:
        page.theme_mode = (
            ft.ThemeMode.DARK if page.theme_mode == ft.ThemeMode.LIGHT else ft.ThemeMode.LIGHT
        )
        boton_tema.icon = (
            ft.Icons.LIGHT_MODE if page.theme_mode == ft.ThemeMode.DARK else ft.Icons.DARK_MODE
        )
        page.update()

    boton_tema = ft.IconButton(
        icon=ft.Icons.DARK_MODE,
        icon_color=ft.Colors.WHITE,
        tooltip="Cambiar tema claro/oscuro",
        on_click=on_click_tema,
    )

    encabezado = ft.Container(
        content=ft.Row(
            [
                ft.Container(
                    content=ft.Image(src="grupopetroil.png", height=36, fit=ft.BoxFit.CONTAIN),
                    bgcolor=ft.Colors.WHITE,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    border_radius=8,
                ),
                ft.Column(
                    [
                        ft.Text("Conciliación Bancaria", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        catalogo_info_text,
                    ],
                    spacing=0,
                ),
                ft.Container(expand=True),
                *(
                    [
                        ft.Container(
                            content=ft.Text(
                                "● SIPP PRUEBAS",
                                size=12,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.WHITE,
                            ),
                            bgcolor=ft.Colors.RED_700,
                            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                            border_radius=20,
                            tooltip="El RPA apunta al entorno de pruebas (stage.sipp.petroil.dev)",
                        )
                    ]
                    if es_modo_test()
                    else []
                ),
                boton_tema,
            ],
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=NAVY,
        padding=ft.Padding.symmetric(horizontal=24, vertical=14),
        border=ft.Border(bottom=ft.BorderSide(4, GOLD)),
    )

    def paso_badge(n: int) -> ft.Control:
        return ft.Container(
            content=ft.Text(str(n), color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=14),
            width=26,
            height=26,
            bgcolor=NAVY,
            border_radius=13,
            alignment=ft.Alignment.CENTER,
        )

    _pasos_ayuda = [
        (1, "Carga el archivo bancario (.csv) del banco (Santander / BanRegio)."),
        (2, "Opcional: si quedan movimientos sin cliente, usa 'Buscar folios en SIPP' para identificarlos."),
        (3, "Carga el Estado de Cuenta (.xlsx): sugiere la sucursal de cada movimiento."),
        (4, "Elige Empresa, Cuenta Bancaria (SIPP) y Fecha de Operación."),
        (5, "Presiona 'Cargar a SIPP (Ingresos Diversos)' para enviarlo al RPA."),
    ]
    dialogo_ayuda_csv = ft.AlertDialog(
        modal=True,
        title=ft.Text("¿Cómo funciona la Conciliación Bancaria?"),
        content=ft.Column(
            [
                ft.Row(
                    [paso_badge(n), ft.Text(txt, expand=True)],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
                for n, txt in _pasos_ayuda
            ],
            tight=True,
            spacing=14,
            width=540,
        ),
        actions=[ft.TextButton("Entendido", on_click=lambda _e: page.pop_dialog())],
    )
    boton_ayuda_csv = ft.OutlinedButton(
        "¿Cómo funciona?",
        icon=ft.Icons.HELP_OUTLINE,
        on_click=lambda _e: mostrar_dialogo(dialogo_ayuda_csv),
    )

    def fila_paso(n: int, fila: ft.Control) -> ft.Control:
        return ft.Row(
            [paso_badge(n), fila],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    contenido_conciliacion = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        paso_badge(1),
                        boton_cargar,
                        boton_buscar_sipp,
                        archivo_nombre_text,
                        banco_detectado_text,
                        ft.Container(expand=True),
                        boton_ayuda_csv,
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                fila_paso(
                    2,
                    ft.Row(
                        [boton_cargar_estado_cuenta, estado_cuenta_text],
                        spacing=16,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                ft.Divider(),
                fila_paso(
                    3,
                    ft.Row(
                        [fecha_operacion_field, empresa_dropdown, cuenta_bancaria_dropdown, boton_cargar_ingresos_div],
                        spacing=16,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                estado_text,
                ft.Divider(),
                resumen_row,
                ft.Divider(),
                ft.Row([filtro_estado, filtro_texto], spacing=16),
                ft.Container(content=tabla_contenedor, expand=True),
            ],
            spacing=12,
        ),
        padding=20,
        expand=True,
    )

    def on_tabs_change(e) -> None:
        if tabs.selected_index == 1:
            page.run_task(actualizar_bandeja_o365)

    tabs = ft.Tabs(
        length=3,
        selected_index=0,
        expand=True,
        on_change=on_tabs_change,
        content=ft.Column(
            expand=True,
            controls=[
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="Conciliación Bancaria", icon=ft.Icons.ACCOUNT_BALANCE),
                        ft.Tab(label="Contado", icon=ft.Icons.MAIL_OUTLINE),
                        ft.Tab(label="Catálogos", icon=ft.Icons.FOLDER_OPEN),
                    ],
                ),
                ft.TabBarView(
                    expand=True,
                    controls=[contenido_conciliacion, contenido_o365, contenido_catalogos],
                ),
            ],
        ),
    )

    notificacion_snackbar = ft.SnackBar(content=ft.Text(""))
    page.overlay.append(notificacion_snackbar)

    async def bucle_refresco_o365() -> None:
        while True:
            await asyncio.sleep(300)
            await actualizar_bandeja_o365()

    page.add(
        ft.Column(
            [
                encabezado,
                tabs,
            ],
            expand=True,
            spacing=0,
        )
    )

    page.run_task(bucle_refresco_o365)


if __name__ == "__main__":
    ft.run(main)
