"""Identificación de folios por la API de SIPP (alternativa al RPA).

/api/facturas ya permite BUSCAR por folio INTERNO exacto (serie + número, ej.
'FCL183653') sin exigir rango de vencimiento (TI lo habilitó 2026-07-17). Por eso
se consulta folio por folio directamente: rápido, exacto y sin descargar ventanas.

Devuelve el mismo shape que el RPA (`{(folio, abono): (cliente, sucursal)}`) para
reutilizar `rpa_folios.aplicar_resultados_folios`. Lo que la API no resuelva
(p. ej. folios sin serie, que la API no matchea) se deja al RPA como respaldo.
"""

import re
from concurrent.futures import ThreadPoolExecutor

from . import sipp_api
from .models import Movimiento

# Interruptor: resolver folios por la API ANTES del RPA. En True: la API ya busca
# por folio interno (fl_FolioDocumento). El RPA queda como respaldo para lo que la
# API no resuelva. Ver memoria [[api-sipp]].
FOLIOS_POR_API = True

# Consultas concurrentes a la API (una por folio). La API responde ~0.5s por folio;
# con paralelismo moderado la búsqueda completa se mantiene ágil.
_MAX_CONCURRENCIA = 8


def _norm_folio(texto: str) -> str:
    """Solo alfanuméricos, en mayúsculas: 'FLM 192206' -> 'FLM192206'. Es el formato
    exacto que espera el parámetro `folio` de la API (concatenado, sin espacios)."""
    return re.sub(r"[^A-Z0-9]", "", (texto or "").upper())


def _tiene_serie(folio_norm: str) -> bool:
    """True si el folio trae serie de sucursal (F + 2 letras + dígitos). La API solo
    matchea el folio COMPLETO (serie+número); un folio solo-número no resuelve."""
    return bool(re.match(r"^F[A-Z]{2}\d+$", folio_norm))


def _elegir(facturas: list[dict], abono: float) -> dict | None:
    """Ante varias facturas con el mismo folio, elige la que corresponde al pago.
    Prioriza: importe (total o saldo) == abono; luego, si todas son del mismo
    cliente, la que tenga saldo. Si hay clientes distintos y ningún importe calza,
    devuelve None (ambiguo) para que lo resuelva el RPA."""
    if len(facturas) == 1:
        return facturas[0]

    objetivo = round(abono, 2)
    por_importe = [
        f for f in facturas
        if round(f.get("im_Total") or 0, 2) == objetivo
        or round(f.get("im_SaldoFactura") or 0, 2) == objetivo
    ]
    if len(por_importe) == 1:
        return por_importe[0]

    clientes = {(f.get("de_RazonSocialCliente") or "").strip() for f in facturas}
    if len(clientes) == 1:
        pendientes = [f for f in facturas if (f.get("im_SaldoFactura") or 0) > 0]
        return (pendientes or facturas)[0]

    return None


def buscar_folios_api(
    candidatos: list[tuple[Movimiento, str]],
    empresa_api: str,
    movimientos: list[Movimiento] | None = None,
    log_fn=print,
) -> dict[tuple[str, float], tuple[str, str]]:
    """Resuelve por API los folios candidatos consultando por folio INTERNO. Devuelve
    {(folio, abono): (cliente, sucursal)} solo para los que resolvió con confianza.
    NO modifica los movimientos (eso lo hace rpa_folios.aplicar_resultados_folios).

    Lanza sipp_api.SippAPIError si la API no responde (el llamador cae al RPA).
    """
    if not candidatos:
        return {}

    # Folios únicos a consultar; los que traen serie (la API los matchea exacto).
    folios_unicos = sorted({_norm_folio(folio) for _, folio in candidatos if _norm_folio(folio)})
    consultables = [f for f in folios_unicos if _tiene_serie(f)]
    sin_serie = len(folios_unicos) - len(consultables)
    if sin_serie:
        log_fn(
            f"API: {sin_serie} folio(s) sin serie (solo número) no los busca la API; "
            "se dejan al RPA.",
            "info",
        )
    if not consultables:
        return {}
    log_fn(f"API: consultando {len(consultables)} folio(s) interno(s)…", "info")

    def _consultar(folio_norm: str) -> tuple[str, list[dict]]:
        return folio_norm, sipp_api.obtener_facturas(empresa_api, folio=folio_norm)

    # Concurrencia moderada; una SippAPIError (API caída) se propaga para caer al RPA.
    por_folio: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENCIA) as pool:
        for folio_norm, facturas in pool.map(_consultar, consultables):
            if facturas:
                por_folio[folio_norm] = facturas

    resultados: dict[tuple[str, float], tuple[str, str]] = {}
    for mov, folio in candidatos:
        candidatas = por_folio.get(_norm_folio(folio))
        if not candidatas:
            continue
        elegida = _elegir(candidatas, mov.abono)
        if elegida is None:
            continue
        cliente = (elegida.get("de_RazonSocialCliente") or "").strip()
        sucursal = (elegida.get("nb_Sucursal") or "").strip()
        if cliente:
            resultados[(folio, mov.abono)] = (cliente, sucursal)
    log_fn(f"API: {len(resultados)} folio(s) resuelto(s) por API.", "ok")
    return resultados
