import re
from typing import Callable

from rpa.automation import RPAAutomation

from .empresas import EMPRESA_DEFAULT, Empresa
from .matcher import extraer_cuenta, extraer_rfc
from .models import ClienteCuenta, Movimiento

# Patrones de folio de documento dentro del concepto del movimiento bancario.
# Se busca el NÚMERO del folio (SIPP lo encuentra por coincidencia parcial en su
# campo de búsqueda; el prefijo de serie solo distingue sucursal/tipo y las
# series no traslapan sus rangos).
#
# La serie de folio es una "F" seguida de 2 iniciales de la sucursal y luego el
# número, ej. FLM (Los Mochis), FHM (Hermosillo), FCL (Culiacán), FMZ
# (Mazatlán), FMY (Monterrey)... por eso el patrón general acepta F + 2 letras.
# Cubre:
#   - "... CONCEPTO: DESC DEL DOCUMENTO NO FLM 192206, ..."
#   - "... ABONO FACTURA FCL 190541"
#   - "... CONCEPTO: FACT 47775, ..."
#   - formato antiguo "... Liq de F 122088 ..."
# Cada patrón captura (serie_opcional, número). La serie (ej. FCL) identifica la
# sucursal y hace ÚNICO al folio: dos facturas pueden compartir número (FCL190541
# y FLM190541), así que conservarla evita ambigüedad al buscar en SIPP.
_FOLIO_PATRONES = (
    # "DOCUMENTO NO <serie?> <num>".
    re.compile(r"DOCUMENTO\s+N[O0]\.?\s*(F[A-Z]{2})?\s*(\d{4,})", re.IGNORECASE),
    # "FACT <num>" / "FACTURA <num>" (sin serie de sucursal).
    re.compile(r"\bFACT(?:URA)?\.?\s*()(\d{4,})", re.IGNORECASE),
    # Serie general de sucursal: F + 2 iniciales + número (FLM/FHM/FCL/FMZ/FMY...).
    # (?<![A-Z]) en vez de \b: permite la serie PEGADA a dígitos (ej. BBVA
    # "0060726FMZ 246981", donde entre el dígito y la F no hay límite de palabra),
    # pero evita capturarla a media palabra (letra-antes queda bloqueada).
    re.compile(r"(?<![A-Z])(F[A-Z]{2})\s*[- ]?\s*(\d{4,})", re.IGNORECASE),
    # Formato antiguo "Liq de F 122088".
    re.compile(r"LIQ\s+DE\s+F\s*()(\d{3,})", re.IGNORECASE),
)


def extraer_folio(texto: str) -> str | None:
    """Extrae el folio del documento referenciado en el concepto (FLM/FMZ/FACT/
    Liq de F), CONSERVANDO la serie de sucursal cuando existe (ej. 'FCL190541').
    Devuelve None si no encuentra ninguno."""
    for patron in _FOLIO_PATRONES:
        coincidencia = patron.search(texto or "")
        if coincidencia:
            serie = (coincidencia.group(1) or "").upper()
            numero = coincidencia.group(2)
            return f"{serie}{numero}"
    return None


def extraer_folios_pendientes(movimientos: list[Movimiento]) -> list[tuple[Movimiento, str]]:
    """Para los movimientos aún no identificados, busca un folio de documento
    reconocible en el concepto (FLM/FMZ/FACT/'Liq de F'). Si el usuario declaró
    manualmente un folio/texto de búsqueda (mov.folio_manual), este tiene
    prioridad sobre la detección automática."""
    candidatos: list[tuple[Movimiento, str]] = []
    for mov in movimientos:
        if mov.identificado:
            continue
        if mov.folio_manual:
            candidatos.append((mov, mov.folio_manual))
            continue
        folio = extraer_folio(mov.texto_busqueda)
        if folio:
            candidatos.append((mov, folio))
    return candidatos


async def buscar_y_aplicar_folios(
    candidatos: list[tuple[Movimiento, str]],
    usuario: str,
    password: str,
    empresa: Empresa = EMPRESA_DEFAULT,
    headless: bool = False,
    log_fn: Callable = print,
    todos_movimientos: list[Movimiento] | None = None,
) -> list[ClienteCuenta]:
    """Busca cada folio candidato en SIPP (Facturas - Listado), aplica el
    cliente encontrado a su movimiento y propone el alta correspondiente en el
    catálogo de cuentas (misma mecánica que el match por nombre).

    Si se pasa `todos_movimientos`, el cliente (y la sucursal) identificado por
    folio se propaga a los demás movimientos AÚN NO identificados que compartan
    la misma cuenta/CLABE o la misma referencia bancaria.
    """
    if not candidatos:
        return []

    pares_unicos = sorted({(folio, mov.abono) for mov, folio in candidatos})
    automatizacion = RPAAutomation(
        usuario,
        password,
        headless=headless,
        log_fn=log_fn,
        empresa_sipp=empresa.sipp_empresa,
        sucursal_sipp=empresa.sipp_sucursal,
    )
    resultados = await automatizacion.buscar_clientes_por_folio(pares_unicos)
    return aplicar_resultados_folios(candidatos, resultados, todos_movimientos, log_fn)


def aplicar_resultados_folios(
    candidatos: list[tuple[Movimiento, str]],
    resultados: dict[tuple[str, float], tuple[str, str]],
    todos_movimientos: list[Movimiento] | None = None,
    log_fn: Callable = print,
) -> list[ClienteCuenta]:
    """Aplica a los movimientos los folios ya resueltos (por API o por RPA) y
    propone las cuentas nuevas para el catálogo. `resultados` mapea
    (folio, abono) -> (cliente, sucursal). Reutilizado por ambos orígenes para que
    la identificación por folio sea idéntica venga de donde venga.

    No sobrescribe movimientos ya identificados (p. ej. resueltos en una pasada
    previa por API). Con `todos_movimientos`, propaga el cliente a los demás
    movimientos no identificados con la misma cuenta/CLABE o referencia."""
    nuevas_cuentas: list[ClienteCuenta] = []
    cuentas_propuestas: set[str] = set()

    for mov, folio in candidatos:
        if mov.identificado:
            continue
        encontrado = resultados.get((folio, mov.abono))
        if not encontrado:
            continue

        cliente, sucursal = encontrado
        if not cliente:
            continue

        mov.cliente_match = cliente
        mov.identificado_por_folio = True
        # La sucursal de la propia factura es la fuente más confiable.
        if sucursal:
            mov.sucursal_por_folio = sucursal

        cuenta_extraida = extraer_cuenta(mov.texto_busqueda)
        rfc_extraido = extraer_rfc(mov.texto_busqueda)
        if not cuenta_extraida and not rfc_extraido:
            continue
        # La cuenta/CLABE es la clave preferida; si no hay, se guarda por RFC.
        mov.cuenta_match = cuenta_extraida or rfc_extraido

        clave = cuenta_extraida or f"RFC:{rfc_extraido}"
        if clave not in cuentas_propuestas:
            cuentas_propuestas.add(clave)
            nuevas_cuentas.append(
                ClienteCuenta(
                    cuenta=cuenta_extraida or "",
                    cliente=cliente,
                    banco=mov.banco,
                    plaza="",
                    rfc=rfc_extraido or "",
                )
            )

    if todos_movimientos:
        propagar_identificacion_por_folio(candidatos, todos_movimientos, log_fn)

    return nuevas_cuentas


def propagar_identificacion_por_folio(
    candidatos: list[tuple[Movimiento, str]],
    todos_movimientos: list[Movimiento],
    log_fn: Callable = print,
) -> int:
    """Propaga el cliente identificado por folio a los demás movimientos AÚN NO
    identificados que compartan la MISMA cuenta/CLABE o la MISMA referencia
    bancaria. Regresa cuántos movimientos se identificaron por propagación.

    La cuenta/CLABE manda cuando existe; si no, se intenta por RFC y por último
    por la referencia bancaria exacta (normalizada). Nunca sobrescribe
    movimientos ya identificados.
    """
    # Movimientos recién identificados por folio (fuente de la propagación).
    # Se precalcula su cuenta/CLABE y RFC (identificadores estables del cliente).
    fuentes = []
    for mov, _folio in candidatos:
        if not (mov.identificado_por_folio and mov.cliente_match):
            continue
        fuentes.append({
            "mov": mov,
            "cuenta": extraer_cuenta(mov.texto_busqueda),
            "rfc": extraer_rfc(mov.texto_busqueda),
            "referencia": (mov.referencia or "").strip(),
        })
    if not fuentes:
        return 0

    propagados = 0
    for otro in todos_movimientos:
        if otro.identificado:
            continue
        texto_otro = otro.texto_busqueda or ""
        texto_otro_upper = texto_otro.upper()
        for f in fuentes:
            fuente = f["mov"]
            misma_cuenta = bool(f["cuenta"]) and f["cuenta"] in texto_otro
            mismo_rfc = bool(f["rfc"]) and f["rfc"] in texto_otro_upper
            misma_ref = bool(f["referencia"]) and f["referencia"] == (otro.referencia or "").strip()
            if not (misma_cuenta or mismo_rfc or misma_ref):
                continue

            otro.cliente_match = fuente.cliente_match
            otro.identificado_por_folio = True
            if fuente.cuenta_match:
                otro.cuenta_match = fuente.cuenta_match
            if fuente.banco_match:
                otro.banco_match = fuente.banco_match
            if fuente.sucursal_por_folio:
                otro.sucursal_por_folio = fuente.sucursal_por_folio
            propagados += 1
            criterio = "cuenta" if misma_cuenta else ("RFC" if mismo_rfc else "referencia")
            log_fn(
                f"  Propagado por {criterio}: {otro.referencia or otro.cuenta_match} "
                f"-> {fuente.cliente_match}",
                "ok",
            )
            break

    if propagados:
        log_fn(f"Propagación por folio: {propagados} movimiento(s) adicional(es) identificado(s).", "ok")
    return propagados
