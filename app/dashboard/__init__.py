"""Pestaña 'Dashboard Ingresos' (paquete modular):

- app/services/dashboard_repository.py → capa de datos (BigQuery: DashboardRepository)
- componentes.py  → piezas visuales compartidas (paleta, KPIs, dona, ranking, timeline)
- segmentado.py   → sub-pestaña 'Segmentado' (la vista agregada original)
- explorador.py   → sub-pestañas 'Timeline' y 'Detalle' (explorador abierto con filtros)

Este módulo solo ensambla las 3 sub-pestañas y expone `construir_tab_dashboard`,
la única función pública que consume app/main.py.
"""

import flet as ft

from .componentes import preparar_tema_date_picker
from .explorador import Explorador
from .segmentado import construir_subtab_segmentado

__all__ = ["construir_tab_dashboard"]

_VALORES_SUBTAB = ("segmentado", "timeline", "detalle")


def construir_tab_dashboard(page: ft.Page) -> tuple[ft.Tab, ft.Control]:
    """Pestaña 'Dashboard Ingresos' con 3 sub-pestañas: Segmentado (vista
    agregada del segmento principal), Timeline (serie temporal mensual/semanal
    del explorador abierto) y Detalle (tabla fila-por-movimiento del mismo
    explorador, filtros compartidos con Timeline).

    Devuelve (tab, contenido): el `Tab` va en `TabBar.tabs` y `contenido` en
    `TabBarView.controls`, en la misma posición (así arma las pestañas main.py).

    La sub-navegación usa un SegmentedButton — NO un segundo `ft.Tabs` anidado
    (main.py ya tiene uno de nivel superior y anidarlos daba problemas de
    render en Flutter). Segmentado se construye una sola vez (sus consultas
    disparan al inicio, como siempre); Timeline/Detalle se RECONSTRUYEN desde
    cero en cada entrada (ver docstring de explorador.py)."""
    preparar_tema_date_picker(page)

    contenido_segmentado = construir_subtab_segmentado(page)
    explorador = Explorador(page)

    area = ft.Column(expand=True, controls=[contenido_segmentado])

    selector = ft.SegmentedButton(
        segments=[
            ft.Segment(value="segmentado", icon=ft.Icons.DONUT_SMALL, label=ft.Text("Segmentado")),
            ft.Segment(value="timeline", icon=ft.Icons.SHOW_CHART, label=ft.Text("Timeline")),
            ft.Segment(value="detalle", icon=ft.Icons.TABLE_ROWS_OUTLINED, label=ft.Text("Detalle")),
        ],
        selected=["segmentado"],
    )

    def _on_cambiar_subtab(e) -> None:
        valor = e.control.selected[0] if e.control.selected else "segmentado"
        if valor not in _VALORES_SUBTAB:
            valor = "segmentado"
        if valor == "segmentado":
            area.controls = [contenido_segmentado]
        else:
            area.controls = [explorador.construir(valor)]
        page.update()

    selector.on_change = _on_cambiar_subtab

    contenido = ft.Column(
        expand=True,
        spacing=0,
        controls=[
            # Borde inferior: separa visualmente el selector del contenido de
            # abajo (antes flotaba sin ninguna división).
            ft.Container(
                content=selector,
                padding=ft.Padding(left=20, right=20, top=14, bottom=14),
                border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            ),
            area,
        ],
    )

    tab = ft.Tab(label="Dashboard Ingresos", icon=ft.Icons.BAR_CHART)
    return tab, contenido
