"""Utilidades para manejar la interfaz Flet desde Playwright.

Flet dibuja con Flutter sobre un `<canvas>`: no hay HTML con botones que
Playwright pueda clicar directamente. La salida es el **árbol de accesibilidad**
de Flutter, que sí publica nodos `<flt-semantics>` con el texto de cada control.
Se activa con un botón oculto (`flt-semantics-placeholder`).

Dos detalles que cuestan tiempo si no se saben:

1. Flutter **repone** ese placeholder cada vez que reconstruye la interfaz (por
   ejemplo al abrir un diálogo), así que hay que reactivarlo antes de cada lectura.
2. Los nodos son solo un espejo del canvas: hacer `.click()` sobre ellos NO
   acciona el control. Hay que tomar su rectángulo y hacer un clic de ratón real
   en esas coordenadas.
"""

import asyncio
from typing import Optional

# JS que devuelve el texto de cada nodo del árbol de accesibilidad.
_JS_TEXTOS = """() => Array.from(document.querySelectorAll('flt-semantics'))
    .map(n => (n.getAttribute('aria-label') || n.textContent || '').trim())
    .filter(Boolean)"""

# JS que localiza un nodo por su texto y devuelve su rectángulo.
_JS_RECT = """(txt) => {
    for (const n of document.querySelectorAll('flt-semantics')) {
        const t = (n.getAttribute('aria-label') || n.textContent || '').trim();
        if (t === txt || (t.includes(txt) && t.length < txt.length + 12)) {
            const r = n.getBoundingClientRect();
            if (r.width && r.height)
                return {x: r.x, y: r.y, width: r.width, height: r.height};
        }
    }
    return null;
}"""


async def activar_accesibilidad(pagina) -> None:
    """Enciende el árbol de accesibilidad de Flutter (idempotente)."""
    ph = pagina.locator("flt-semantics-placeholder")
    if await ph.count():
        await ph.first.evaluate("el => el.click()")
        await pagina.wait_for_timeout(2000)


async def textos(pagina, reactivar: bool = True) -> list[str]:
    """Todo el texto visible de la interfaz, según el árbol de accesibilidad."""
    if reactivar:
        await activar_accesibilidad(pagina)
    return await pagina.evaluate(_JS_TEXTOS)


async def contiene(pagina, fragmento: str) -> bool:
    return any(fragmento in t for t in await textos(pagina))


async def rectangulo(pagina, texto: str) -> Optional[dict]:
    await activar_accesibilidad(pagina)
    return await pagina.evaluate(_JS_RECT, texto)


async def clic(pagina, texto: str, espera_ms: int = 2500) -> bool:
    """Clic de ratón REAL sobre el control que muestra `texto`.

    Devuelve False si no se encontró (para que la prueba decida si es un fallo).
    """
    caja = await rectangulo(pagina, texto)
    if not caja:
        return False
    await pagina.mouse.click(caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2)
    await pagina.wait_for_timeout(espera_ms)
    return True


async def cargar_archivo(pagina, texto_boton: str, ruta: str, espera_ms: int = 15000) -> None:
    """Pulsa un botón que abre el selector de archivos y le entrega `ruta`."""
    caja = await rectangulo(pagina, texto_boton)
    if not caja:
        raise AssertionError(f"No se encontró el botón '{texto_boton}' en la interfaz")
    async with pagina.expect_file_chooser(timeout=15_000) as info:
        await pagina.mouse.click(
            caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
        )
    chooser = await info.value
    await chooser.set_files(ruta)
    await pagina.wait_for_timeout(espera_ms)


async def esperar_texto(pagina, fragmento: str, timeout_s: float = 30.0) -> bool:
    """Espera a que aparezca un texto en la interfaz."""
    limite = timeout_s
    while limite > 0:
        if await contiene(pagina, fragmento):
            return True
        await asyncio.sleep(1.5)
        limite -= 1.5
    return False
