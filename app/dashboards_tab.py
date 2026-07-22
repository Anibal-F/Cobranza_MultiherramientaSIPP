"""Pestaña 'Dashboards': agrupa bajo una sola pestaña de nivel superior los
dashboards de BigQuery de la app, con un SegmentedButton para alternar entre
sub-pestañas:

- 'Ingresos Mensuales' → ingresos diversos mensuales (app/dashboard).
- 'Proyección y Cobranza Semanal' → antigüedad de saldos / RDC (app/rdc).
- 'Cumplimiento de Cobro' → facturas por fecha de vencimiento, KPIs y top de
  empresas (app/cumplimiento).

No se usa un segundo ft.Tabs anidado — main.py ya tiene uno de nivel superior
y anidarlos da problemas de render en Flutter (mismo criterio que
app/dashboard/__init__.py, que resuelve su propia sub-navegación interna
igual)."""

import flet as ft

from .cumplimiento import construir_tab_cumplimiento
from .dashboard import construir_tab_dashboard
from .rdc import construir_tab_rdc

__all__ = ["construir_tab_dashboards"]

_VALORES_SUBTAB = ("ingresos", "proyeccion", "cumplimiento")


def construir_tab_dashboards(page: ft.Page) -> tuple[ft.Tab, ft.Control]:
    """Devuelve (tab, contenido): el `Tab` va en `TabBar.tabs` y `contenido`
    en `TabBarView.controls`, en la misma posición (así arma las pestañas
    main.py). Ambos dashboards se construyen de una vez (disparan sus
    consultas al inicio, como ya hacía cada uno por separado); solo se
    intercambia cuál árbol de controles está montado en `area`."""
    _, contenido_ingresos = construir_tab_dashboard(page)
    _, contenido_rdc = construir_tab_rdc(page)
    _, contenido_cumplimiento = construir_tab_cumplimiento(page)

    area = ft.Column(expand=True, controls=[contenido_ingresos])

    selector = ft.SegmentedButton(
        segments=[
            ft.Segment(value="ingresos", icon=ft.Icons.BAR_CHART, label=ft.Text("Ingresos Mensuales")),
            ft.Segment(value="proyeccion", icon=ft.Icons.HISTORY_EDU, label=ft.Text("Proyección y Cobranza Semanal")),
            ft.Segment(value="cumplimiento", icon=ft.Icons.FACT_CHECK_OUTLINED, label=ft.Text("Cumplimiento de Cobro")),
        ],
        selected=["ingresos"],
    )

    _CONTENIDO_POR_VALOR = {
        "ingresos": contenido_ingresos,
        "proyeccion": contenido_rdc,
        "cumplimiento": contenido_cumplimiento,
    }

    def _on_cambiar_subtab(e) -> None:
        valor = e.control.selected[0] if e.control.selected else "ingresos"
        if valor not in _VALORES_SUBTAB:
            valor = "ingresos"
        area.controls = [_CONTENIDO_POR_VALOR[valor]]
        page.update()

    selector.on_change = _on_cambiar_subtab

    contenido = ft.Column(
        expand=True,
        spacing=0,
        controls=[
            # Barra de sub-navegación con borde inferior: separa visualmente el
            # selector del contenido de abajo (antes flotaba sin ninguna
            # división, se sentía pegado al primer panel de cada dashboard).
            ft.Container(
                content=selector,
                padding=ft.Padding(left=20, right=20, top=14, bottom=14),
                border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            ),
            area,
        ],
    )

    tab = ft.Tab(label="Dashboards", icon=ft.Icons.BAR_CHART)
    return tab, contenido
