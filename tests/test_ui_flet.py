"""Pruebas de la INTERFAZ, manejando la app de verdad.

La app se levanta como servidor web (Flet sirve la misma interfaz que en
escritorio) y se maneja con Playwright a través del árbol de accesibilidad de
Flutter — ver `tests/ui_flet.py` para los detalles de esa mecánica.

Qué se prueba: que la app arranque sin errores, que se pueda navegar entre
pestañas y que cargar un corte real produzca los movimientos correctos en
pantalla. Es la comprobación de que lo que verifican las pruebas de lógica
también funciona cuando el usuario lo hace con el ratón.

Apagadas por defecto (tardan ~1 minuto y levantan un servidor). Para correrlas:

    MH_UI_TESTS=1 python -m pytest tests/test_ui_flet.py -v -s

Nota: en escritorio la app usa el selector de archivos del sistema operativo y
aquí el del navegador. El resto del flujo es el mismo código.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

from tests import ui_flet as ui

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA_CORTES = os.getenv("MH_CORTES_DIR", os.path.expanduser("~/Downloads/MH_Ejemplo/BBVA"))
CORTE_1 = os.path.join(CARPETA_CORTES, "Corte_1.xls")

pytestmark = pytest.mark.skipif(
    os.getenv("MH_UI_TESTS", "").strip() not in ("1", "true", "si", "yes"),
    reason="Pruebas de interfaz apagadas; actívalas con MH_UI_TESTS=1",
)


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor():
    """Levanta la app como servidor web y la apaga al terminar."""
    puerto = _puerto_libre()
    guion = (
        "import os, sys; sys.path.insert(0, %r); os.chdir(%r);"
        "os.environ['FLET_FORCE_WEB_SERVER']='true';"
        "import flet as ft; from app import logs; from app.main import main;"
        "logs.configurar(consola=False);"
        "ft.run(main, port=%d, view=None, assets_dir='assets')"
        % (RAIZ, RAIZ, puerto)
    )
    entorno = {**os.environ, "MH_LOGS_BQ": "0", "MH_LOGS_DIR": "/tmp/mh_ui_tests"}
    proceso = subprocess.Popen(
        [sys.executable, "-c", guion],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=entorno,
    )
    # Esperar a que el puerto responda.
    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", puerto), timeout=1):
                break
        except OSError:
            if proceso.poll() is not None:
                salida = proceso.stdout.read().decode(errors="replace")
                raise RuntimeError(f"La app no arrancó:\n{salida[-2000:]}")
            time.sleep(1)
    else:
        proceso.kill()
        raise RuntimeError("La app no respondió en el puerto a tiempo")

    yield f"http://127.0.0.1:{puerto}/"

    proceso.terminate()
    try:
        proceso.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proceso.kill()


@pytest.fixture
async def pagina(servidor):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        navegador = await p.chromium.launch(headless=True)
        pag = await navegador.new_page(viewport={"width": 1400, "height": 900})
        errores: list[str] = []
        pag.on("pageerror", lambda e: errores.append(str(e)))
        await pag.goto(servidor, wait_until="networkidle", timeout=60_000)
        await pag.wait_for_timeout(8000)   # Flutter tarda en pintar la primera vez
        await ui.activar_accesibilidad(pag)
        pag.errores_js = errores           # para que las pruebas los revisen
        yield pag
        await navegador.close()


@pytest.mark.asyncio
async def test_la_app_arranca_y_muestra_sus_pestanas(pagina):
    """Humo: si esto falla, la app no abre en la máquina del usuario."""
    et = await ui.textos(pagina)
    assert et, "la interfaz no publicó ningún control"
    for pestana in ("Identificación Bancaria", "Extracción de Contados",
                    "Dashboards", "Conciliaciones Bancarias"):
        assert any(pestana in t for t in et), f"falta la pestaña '{pestana}'"
    assert not pagina.errores_js, f"errores de JavaScript al arrancar: {pagina.errores_js[:3]}"


@pytest.mark.asyncio
async def test_el_catalogo_de_clientes_se_carga_al_iniciar(pagina):
    """El encabezado dice cuántas cuentas se cargaron; si es 0, el catálogo falló."""
    et = await ui.textos(pagina)
    encabezado = next((t for t in et if "Catálogo de clientes cargado" in t), "")
    assert encabezado, "el encabezado no reporta el catálogo cargado"
    numero = "".join(c for c in encabezado if c.isdigit())
    assert numero and int(numero) > 0, f"catálogo vacío: {encabezado!r}"


@pytest.mark.asyncio
async def test_se_puede_navegar_entre_pestanas(pagina):
    for pestana in ("Dashboards", "Conciliaciones Bancarias", "Identificación Bancaria"):
        assert await ui.clic(pagina, pestana, espera_ms=4000), f"no se pudo abrir '{pestana}'"
    assert not pagina.errores_js, f"errores al navegar: {pagina.errores_js[:3]}"


@pytest.mark.skipif(not os.path.exists(CORTE_1), reason="No está Corte_1.xls")
@pytest.mark.asyncio
async def test_cargar_un_corte_muestra_sus_movimientos(pagina):
    """El flujo que usa el usuario todos los días, de punta a punta.

    Corte_1.xls tiene 80 movimientos (lo mismo que verifica
    test_duplicidad_cortes.py sin interfaz): aquí se comprueba que la app los
    lea, detecte el banco y los muestre.
    """
    await ui.cargar_archivo(pagina, "Cargar archivo bancario (.csv)", CORTE_1)

    # BBVA abre un diálogo ofreciendo agregar el SPEI; se contesta que no.
    if await ui.contiene(pagina, "Unificar archivos de BBVA"):
        assert await ui.clic(pagina, "No, continuar", espera_ms=4000)

    et = await ui.textos(pagina)
    todo = " | ".join(et)

    assert "Corte_1.xls" in todo, "la app no muestra el archivo cargado"
    assert "BBVA" in todo, "no se detectó el banco"
    assert "80 movimientos leídos" in todo or "80" in et, (
        f"no aparecen los 80 movimientos leídos. Interfaz: {todo[:400]}"
    )
    assert not pagina.errores_js, f"errores al cargar: {pagina.errores_js[:3]}"
