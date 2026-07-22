"""Pestaña 'Cumplimiento de Cobro' (paquete modular):

- app/services/cumplimiento_repository.py → capa de datos (BigQuery)
- resumen.py → sub-pestaña 'Resumen': KPIs, mini dashboards por tipo de
  negocio y top 15 de clientes.
- detalle.py → sub-pestaña 'Detalle': los mismos registros fila por fila,
  con paginación y filtros multi-selección por columna.

Este módulo solo ensambla las 2 sub-pestañas y expone
`construir_tab_cumplimiento`, la única función pública que consume
app/dashboards_tab.py — mismo criterio que app/dashboard/__init__.py."""

import flet as ft

from ..dashboard.componentes import preparar_tema_date_picker
from .detalle import construir_subtab_detalle
from .resumen import construir_subtab_resumen

__all__ = ["construir_tab_cumplimiento"]

_VALORES_SUBTAB = ("resumen", "detalle")


def construir_tab_cumplimiento(page: ft.Page) -> tuple[ft.Tab, ft.Control]:
    """Pestaña 'Cumplimiento de Cobro' con 2 sub-pestañas: Resumen (KPIs +
    mini dashboards) y Detalle (tabla paginada con filtros multi-selección
    por columna). Devuelve (tab, contenido): el `Tab` va en `TabBar.tabs` y
    `contenido` en `TabBarView.controls`, en la misma posición (así arma las
    pestañas main.py).

    La sub-navegación usa un SegmentedButton — NO un segundo `ft.Tabs`
    anidado (main.py ya tiene uno de nivel superior y anidarlos daba
    problemas de render en Flutter). Ambas sub-pestañas se construyen una
    sola vez (disparan sus consultas al inicio); solo se intercambia cuál
    árbol de controles está montado en `area`."""
    preparar_tema_date_picker(page)

    contenido_resumen = construir_subtab_resumen(page)
    contenido_detalle = construir_subtab_detalle(page)

    area = ft.Column(expand=True, controls=[contenido_resumen])

    selector = ft.SegmentedButton(
        segments=[
            ft.Segment(value="resumen", icon=ft.Icons.DASHBOARD_OUTLINED, label=ft.Text("Resumen")),
            ft.Segment(value="detalle", icon=ft.Icons.TABLE_ROWS_OUTLINED, label=ft.Text("Detalle")),
        ],
        selected=["resumen"],
    )

    def _on_cambiar_subtab(e) -> None:
        valor = e.control.selected[0] if e.control.selected else "resumen"
        if valor not in _VALORES_SUBTAB:
            valor = "resumen"
        area.controls = [contenido_resumen if valor == "resumen" else contenido_detalle]
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

    tab = ft.Tab(label="Cumplimiento de Cobro", icon=ft.Icons.FACT_CHECK_OUTLINED)
    return tab, contenido
