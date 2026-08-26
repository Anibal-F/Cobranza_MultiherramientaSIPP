"""Pruebas de que los movimientos de un corte anterior NO se re-suben a SIPP.

Este es el eslabón crítico del dedup incremental. Para el flujo H2H de BBVA —el
que se usa con los cortes .xls— el archivo NO se sube: los movimientos los trae
SIPP del buzón y la ÚNICA forma de omitir los repetidos es excluirlos uno por uno
en el modal de previsualización (`_eliminar_filas_preview`). Si ese paso falla, los
movimientos del corte anterior se registran otra vez.

Se ejercita el código real de Playwright contra tests/preview_bancario_falso.html,
que reproduce ese modal. Los datos salen de los cortes reales cuando están
disponibles; si no, de filas sintéticas equivalentes.
"""

import os
from collections import Counter

import pytest

from rpa.automation import RPAAutomation

RUTA_HTML = "file://" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "preview_bancario_falso.html"
)
CARPETA = os.getenv("MH_CORTES_DIR", os.path.expanduser("~/Downloads/MH_Ejemplo/BBVA"))
CORTE_1 = os.path.join(CARPETA, "Corte_1.xls")
CORTE_2 = os.path.join(CARPETA, "Corte_2.xls")


@pytest.fixture
def rpa():
    return RPAAutomation(username="x", password="x", headless=True, log_fn=lambda *a: None)


async def _abrir(playwright, filas):
    navegador = await playwright.chromium.launch(headless=True)
    pagina = await navegador.new_page()
    await pagina.goto(RUTA_HTML)
    await pagina.evaluate("(f) => window.cargarFilas(f)", filas)
    return navegador, pagina


def _filas_de_cortes():
    """(filas del corte 2 para el modal, movimientos repetidos del corte 1)."""
    from app.historial import clave_movimiento, claves_subidas
    from app.parsers import parsear_archivo

    c1 = parsear_archivo(CORTE_1, "BBVA")
    c2 = parsear_archivo(CORTE_2, "BBVA")
    historial = [{
        "id": "previa", "banco": "BBVA", "subido_sipp": True,
        "movimientos": [{"banco": m.banco, "referencia": m.referencia, "abono": m.abono,
                         "fecha": m.fecha.isoformat() if m.fecha else "",
                         "descripcion": m.descripcion} for m in c1],
    }]
    repetidas = claves_subidas(historial, "BBVA")
    repetidos = [m for m in c2 if clave_movimiento(m) in repetidas]
    filas = [{"ref": m.referencia, "importe": float(m.abono or 0)} for m in c2]
    return filas, repetidos


# ──────────────────────────────────────────────────────────
# Con los cortes reales
# ──────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (os.path.exists(CORTE_1) and os.path.exists(CORTE_2)),
    reason="No están los cortes de ejemplo",
)
@pytest.mark.asyncio
async def test_los_80_del_corte_anterior_quedan_excluidos_y_los_92_nuevos_no(rpa):
    """EL ESCENARIO REAL: Corte_1 ya subido, se procesa Corte_2.

    De las 172 filas del modal deben excluirse exactamente las 80 que ya venían
    en el Corte_1, y ninguna de las 92 nuevas.
    """
    from playwright.async_api import async_playwright

    filas, repetidos = _filas_de_cortes()
    assert len(filas) == 172 and len(repetidos) == 80, "los cortes no son los esperados"

    a_eliminar = [(m.referencia, m.abono) for m in repetidos]
    # Se comparan con Counter y no con set: las referencias de BBVA NO son únicas
    # (p. ej. 'REFBNTC00833576' aparece tres veces con importes distintos), así que
    # un set escondería que se excluyó una fila de más y otra de menos.
    esperadas = Counter(m.referencia for m in repetidos)

    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, filas)
        try:
            excluidos = await rpa._eliminar_filas_preview(pagina, a_eliminar)
            obtenidas = Counter(await pagina.evaluate("() => window.excluidas()"))

            assert len(excluidos) == 80, (
                f"se excluyeron {len(excluidos)} filas de 80; "
                f"{80 - len(excluidos)} movimiento(s) se re-subirían a SIPP"
            )
            de_mas = obtenidas - esperadas
            de_menos = esperadas - obtenidas
            assert not de_mas, (
                f"se excluyeron filas NUEVAS (ese cobro se perdería): {list(de_mas)}"
            )
            assert not de_menos, (
                f"quedaron sin excluir filas del corte anterior "
                f"(se re-subirían a SIPP): {list(de_menos)}"
            )
            assert rpa.contadores.get("errores") is None, "hubo fallos al excluir filas"
        finally:
            await navegador.close()


# ──────────────────────────────────────────────────────────
# Casos de borde, sin depender de los cortes
# ──────────────────────────────────────────────────────────


FILAS_DEMO = [
    {"ref": "0195586307 014", "importe": 96602.08},
    {"ref": "3345648", "importe": 955049.84},
    {"ref": "0152804678  072", "importe": 12500.00},
    {"ref": "9988776", "importe": 3400.50},
]


@pytest.mark.asyncio
async def test_no_excluye_nada_cuando_no_hay_repetidos(rpa):
    """Un corte totalmente nuevo no debe perder ninguna fila."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, FILAS_DEMO)
        try:
            excluidos = await rpa._eliminar_filas_preview(pagina, [])
            assert excluidos == set()
            assert await pagina.evaluate("() => window.excluidas()") == []
        finally:
            await navegador.close()


@pytest.mark.asyncio
async def test_excluye_solo_la_fila_que_corresponde(rpa):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, FILAS_DEMO)
        try:
            await rpa._eliminar_filas_preview(pagina, [("3345648", 955049.84)])
            assert await pagina.evaluate("() => window.excluidas()") == ["3345648"]
        finally:
            await navegador.close()


@pytest.mark.asyncio
async def test_empareja_aunque_la_referencia_traiga_espacios_distintos(rpa):
    """SIPP muestra la referencia con distinta separación que nuestro parser
    ('0152804678  072' vs '0152804678 072')."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, FILAS_DEMO)
        try:
            await rpa._eliminar_filas_preview(pagina, [("0152804678 072", 12500.00)])
            assert await pagina.evaluate("() => window.excluidas()") == ["0152804678  072"]
        finally:
            await navegador.close()


@pytest.mark.asyncio
async def test_no_confunde_dos_movimientos_con_el_mismo_importe(rpa):
    """Dos filas distintas con el mismo importe: debe excluirse solo la pedida."""
    from playwright.async_api import async_playwright

    filas = [
        {"ref": "AAA111", "importe": 5000.00},
        {"ref": "BBB222", "importe": 5000.00},
    ]
    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, filas)
        try:
            await rpa._eliminar_filas_preview(pagina, [("BBB222", 5000.00)])
            assert await pagina.evaluate("() => window.excluidas()") == ["BBB222"]
        finally:
            await navegador.close()
