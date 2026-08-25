"""Pruebas del RPA contra un combo `chosen` falso, SIN conectarse a SIPP.

Ejercitan el código real de Playwright de `RPAAutomation._chosen_select` sobre
tests/combo_chosen_falso.html, que reproduce la estructura y el filtrado de la
librería `chosen` que usa SIPP. Así se puede verificar la salvaguarda de clientes
en cualquier máquina, sin credenciales ni VPN.

Requiere el navegador de Playwright:  .venv/bin/python -m playwright install chromium
"""

import os

import pytest

from rpa.automation import ClienteNoConfiable, RPAAutomation

pytest_plugins = ()

RUTA_HTML = "file://" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "combo_chosen_falso.html")


@pytest.fixture
def rpa():
    """Instancia del RPA que no habla con nadie: solo se usan sus métodos de combo."""
    return RPAAutomation(username="prueba", password="prueba", headless=True, log_fn=lambda *a: None)


async def _abrir(playwright, clientes):
    navegador = await playwright.chromium.launch(headless=True)
    pagina = await navegador.new_page()
    await pagina.goto(RUTA_HTML)
    await pagina.evaluate("(c) => window.cargarClientes(c)", clientes)
    return navegador, pagina


async def _seleccionado(pagina):
    return await pagina.evaluate("() => window.seleccionado")


@pytest.mark.asyncio
async def test_selecciona_al_cliente_correcto(rpa):
    from playwright.async_api import async_playwright

    clientes = [
        "01234 - COMERCIAL BETA DEL NORTE SA DE CV",
        "05881 - COMERCIALIZADORA ACME S.A. DE C.V.",
        "07001 - DISTRIBUIDORA DEL VALLE SA DE CV",
    ]
    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, clientes)
        try:
            await rpa._chosen_select(pagina, "ID_CLIENTE", "COMERCIALIZADORA ACME SA DE CV", validar=True)
            assert await _seleccionado(pagina) == "05881 - COMERCIALIZADORA ACME S.A. DE C.V."
        finally:
            await navegador.close()


@pytest.mark.asyncio
async def test_no_selecciona_a_nadie_cuando_el_cliente_no_esta(rpa):
    """EL BUG REPORTADO, de punta a punta.

    'COMERCIALIZADORA ACME' no está en el catálogo. El filtro se queda en cero, el
    RPA borra caracteres hasta que reaparece 'COMERCIAL BETA'… y antes lo elegía.
    Ahora debe lanzar ClienteNoConfiable y dejar el combo intacto.
    """
    from playwright.async_api import async_playwright

    clientes = [
        "01234 - COMERCIAL BETA DEL NORTE SA DE CV",
        "07001 - DISTRIBUIDORA DEL VALLE SA DE CV",
    ]
    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, clientes)
        try:
            with pytest.raises(ClienteNoConfiable):
                await rpa._chosen_select(
                    pagina, "ID_CLIENTE", "COMERCIALIZADORA ACME DEL PACIFICO SA DE CV", validar=True
                )
            assert await _seleccionado(pagina) is None, "no debió seleccionar ningún cliente"
        finally:
            await navegador.close()


@pytest.mark.asyncio
async def test_acepta_el_nombre_recortado_de_sipp(rpa):
    """SIPP guarda el nombre truncado; borrar caracteres SÍ debe funcionar aquí."""
    from playwright.async_api import async_playwright

    clientes = ["00077 - TRANSPORTES DEL NOROESTE 3T", "00078 - TRANSPORTES UNIDOS SA DE CV"]
    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, clientes)
        try:
            await rpa._chosen_select(
                pagina, "ID_CLIENTE", "TRANSPORTES DEL NOROESTE 3T SA DE CV", validar=True
            )
            assert await _seleccionado(pagina) == "00077 - TRANSPORTES DEL NOROESTE 3T"
        finally:
            await navegador.close()


@pytest.mark.asyncio
async def test_no_elige_entre_dos_clientes_casi_iguales(rpa):
    from playwright.async_api import async_playwright

    clientes = [
        "01 - GRUPO GASOLINERO DEL NORTE I SA DE CV",
        "02 - GRUPO GASOLINERO DEL NORTE II SA DE CV",
    ]
    async with async_playwright() as p:
        navegador, pagina = await _abrir(p, clientes)
        try:
            with pytest.raises(ClienteNoConfiable):
                await rpa._chosen_select(pagina, "ID_CLIENTE", "GRUPO GASOLINERO DEL NORTE", validar=True)
            assert await _seleccionado(pagina) is None
        finally:
            await navegador.close()


@pytest.mark.asyncio
async def test_la_incidencia_queda_registrada_para_el_resumen(rpa):
    """Lo que el usuario verá al final en vez de un cliente equivocado."""
    rpa.registrar_incidencia_cliente(
        cliente="COMERCIALIZADORA ACME SA DE CV",
        motivo="la opción más parecida solo coincide 62%",
        fila=3, referencia="REF900", monto=15000.0,
    )
    texto = rpa.resumen_incidencias(total_procesados=10)
    assert "1 de 10" in texto
    assert "COMERCIALIZADORA ACME" in texto
    assert "REF900" in texto
