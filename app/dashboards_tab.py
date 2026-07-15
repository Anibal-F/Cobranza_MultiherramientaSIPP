"""Pestaña 'Dashboards': agrupa bajo una sola pestaña de nivel superior los
dos dashboards de BigQuery de la app, con un SegmentedButton para alternar
entre sub-pestañas:

- 'Dashboard Ingresos' → ingresos diversos mensuales (app/dashboard).
- 'Proyección' → antigüedad de saldos / RDC (app/rdc).

No se usa un segundo ft.Tabs anidado — main.py ya tiene uno de nivel superior
y anidarlos da problemas de render en Flutter (mismo criterio que
app/dashboard/__init__.py, que resuelve su propia sub-navegación interna
igual)."""

import flet as ft

from .dashboard import construir_tab_dashboard
from .rdc import construir_tab_rdc

__all__ = ["construir_tab_dashboards"]

_VALORES_SUBTAB = ("ingresos", "proyeccion")


def construir_tab_dashboards(page: ft.Page) -> tuple[ft.Tab, ft.Control]:
    """Devuelve (tab, contenido): el `Tab` va en `TabBar.tabs` y `contenido`
    en `TabBarView.controls`, en la misma posición (así arma las pestañas
    main.py). Ambos dashboards se construyen de una vez (disparan sus
    consultas al inicio, como ya hacía cada uno por separado); solo se
    intercambia cuál árbol de controles está montado en `area`."""
    _, contenido_ingresos = construir_tab_dashboard(page)
    _, contenido_rdc = construir_tab_rdc(page)

    area = ft.Column(expand=True, controls=[contenido_ingresos])

    selector = ft.SegmentedButton(
        segments=[
            ft.Segment(value="ingresos", icon=ft.Icons.BAR_CHART, label=ft.Text("Dashboard Ingresos")),
            ft.Segment(value="proyeccion", icon=ft.Icons.HISTORY_EDU, label=ft.Text("Proyección")),
        ],
        selected=["ingresos"],
    )

    def _on_cambiar_subtab(e) -> None:
        valor = e.control.selected[0] if e.control.selected else "ingresos"
        if valor not in _VALORES_SUBTAB:
            valor = "ingresos"
        area.controls = [contenido_ingresos if valor == "ingresos" else contenido_rdc]
        page.update()

    selector.on_change = _on_cambiar_subtab

    contenido = ft.Column(
        expand=True,
        controls=[
            ft.Container(content=selector, padding=ft.Padding(left=20, right=20, top=12, bottom=0)),
            area,
        ],
    )

    tab = ft.Tab(label="Dashboards", icon=ft.Icons.BAR_CHART)
    return tab, contenido
