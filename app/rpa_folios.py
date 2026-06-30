import re
from typing import Callable

from rpa.automation import RPAAutomation

from .matcher import extraer_cuenta
from .models import ClienteCuenta, Movimiento

FOLIO_REGEX = re.compile(r"Liq de F (\d+)", re.IGNORECASE)


def extraer_folios_pendientes(movimientos: list[Movimiento]) -> list[tuple[Movimiento, str]]:
    """Para los movimientos aún no identificados, busca un folio de factura
    reconocible en el texto (ej. '...Liq de F 122088...'). Si el usuario
    declaró manualmente un folio/texto de búsqueda (mov.folio_manual), este
    tiene prioridad sobre la detección automática."""
    candidatos: list[tuple[Movimiento, str]] = []
    for mov in movimientos:
        if mov.identificado:
            continue
        if mov.folio_manual:
            candidatos.append((mov, mov.folio_manual))
            continue
        coincidencia = FOLIO_REGEX.search(mov.texto_busqueda)
        if coincidencia:
            candidatos.append((mov, coincidencia.group(1)))
    return candidatos


async def buscar_y_aplicar_folios(
    candidatos: list[tuple[Movimiento, str]],
    usuario: str,
    password: str,
    headless: bool = False,
    log_fn: Callable = print,
) -> list[ClienteCuenta]:
    """Busca cada folio candidato en SIPP (Facturas - Listado), aplica el
    cliente encontrado a su movimiento y propone el alta correspondiente en el
    catálogo de cuentas (misma mecánica que el match por nombre).
    """
    if not candidatos:
        return []

    pares_unicos = sorted({(folio, mov.abono) for mov, folio in candidatos})
    automatizacion = RPAAutomation(usuario, password, headless=headless, log_fn=log_fn)
    resultados = await automatizacion.buscar_clientes_por_folio(pares_unicos)

    nuevas_cuentas: list[ClienteCuenta] = []
    cuentas_propuestas: set[str] = set()

    for mov, folio in candidatos:
        cliente = resultados.get((folio, mov.abono))
        if not cliente:
            continue

        mov.cliente_match = cliente
        mov.identificado_por_folio = True

        cuenta_extraida = extraer_cuenta(mov.texto_busqueda)
        if not cuenta_extraida:
            continue
        mov.cuenta_match = cuenta_extraida

        if cuenta_extraida not in cuentas_propuestas:
            cuentas_propuestas.add(cuenta_extraida)
            nuevas_cuentas.append(ClienteCuenta(cuenta=cuenta_extraida, cliente=cliente, banco=mov.banco, plaza=""))

    return nuevas_cuentas
