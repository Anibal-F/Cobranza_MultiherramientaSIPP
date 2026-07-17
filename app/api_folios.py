"""Identificación de folios por la API de SIPP (alternativa al RPA).

El param `folio` de /api/facturas solo acepta el UUID de la factura (que no
tenemos: los movimientos bancarios traen el NÚMERO de folio, ej. FLM192206). Por
eso se traen las facturas de la empresa por un rango de VENCIMIENTO acotado a las
fechas de la extracción y se cruza `fl_FolioDocumento` del lado nuestro.

Devuelve el mismo shape que el RPA (`{(folio, abono): (cliente, sucursal)}`) para
reutilizar `rpa_folios.aplicar_resultados_folios`. Lo que la API no resuelva se
deja al RPA como respaldo.
"""

import re
from datetime import date, timedelta

from . import sipp_api
from .models import Movimiento

# Interruptor: resolver folios por la API ANTES del RPA. HOY EN False: la API de
# /api/facturas aún no permite BUSCAR por folio interno (fl_FolioDocumento) —solo
# filtra por el UUID fiscal—, así que se sigue con el RPA. TI ya entendió la
# solicitud y la trabajará; poner en True cuando habiliten el filtro por folio
# interno (idealmente sin exigir rango de vencimiento). Ver memoria [[api-sipp]].
FOLIOS_POR_API = False


def _norm_folio(texto: str) -> str:
    """Solo alfanuméricos, en mayúsculas: 'FLM 192206' -> 'FLM192206'."""
    return re.sub(r"[^A-Z0-9]", "", (texto or "").upper())


def _solo_numero(folio_norm: str) -> str:
    """Quita la serie de sucursal (F + 2 letras) y los ceros a la izquierda:
    'FMT034107' -> '34107'. Para cruzar candidatos que vienen SIN serie."""
    sin_serie = re.sub(r"^F[A-Z]{2}", "", folio_norm)
    return sin_serie.lstrip("0") or "0"


# Máximo de facturas que se acepta paginar para una ventana. Si la ventana trae
# más (el volumen de vencimientos es muy dispar entre meses), se cae al RPA en vez
# de paginar cientos de veces. Ajustable si el desempeño lo permite.
LIMITE_FACTURAS = 5000


def ventana_vencimiento(
    movimientos: list[Movimiento], meses_atras: int = 3, dias_adelante: int = 20
) -> tuple[date, date]:
    """Rango de vencimiento a consultar, derivado de las fechas de la extracción:
    un pago normalmente salda facturas vencidas en los meses previos (a veces se
    paga por anticipado, de ahí el margen hacia adelante). Las facturas más viejas
    que este rango (o si la ventana es enorme) se resuelven con el RPA de respaldo."""
    fechas = [m.fecha for m in movimientos if m.fecha]
    base_min = min(fechas) if fechas else date.today()
    base_max = max(fechas) if fechas else date.today()
    return (base_min - timedelta(days=meses_atras * 30), base_max + timedelta(days=dias_adelante))


def _elegir(facturas: list[dict], abono: float) -> dict | None:
    """Ante varias facturas con el mismo número de folio, elige la que corresponde
    al pago. Prioriza: importe (total o saldo) == abono; luego saldo pendiente;
    si todas son del mismo cliente, la primera. Si no hay forma de decidir con
    confianza (clientes distintos y sin match de importe), devuelve None para que
    lo resuelva el RPA."""
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
        # Mismo cliente en todas: da igual cuál factura; prefiere una con saldo.
        pendientes = [f for f in facturas if (f.get("im_SaldoFactura") or 0) > 0]
        return (pendientes or facturas)[0]

    # Clientes distintos y sin match de importe: ambiguo -> respaldo RPA.
    return None


def buscar_folios_api(
    candidatos: list[tuple[Movimiento, str]],
    empresa_api: str,
    movimientos: list[Movimiento],
    log_fn=print,
) -> dict[tuple[str, float], tuple[str, str]]:
    """Resuelve por API los folios candidatos. Devuelve
    {(folio, abono): (cliente, sucursal)} solo para los que resolvió con confianza.
    NO modifica los movimientos (eso lo hace rpa_folios.aplicar_resultados_folios).

    Lanza sipp_api.SippAPIError si la API no está disponible (el llamador cae al RPA).
    """
    if not candidatos:
        return {}

    inicio, fin = ventana_vencimiento(movimientos)
    # Sondeo: si la ventana trae demasiadas facturas, no se pagina (sería lento);
    # esos folios se resuelven por RPA.
    total = sipp_api.contar_facturas(empresa_api, inicio, fin)
    log_fn(
        f"API: {total} factura(s) con vencimiento {inicio.isoformat()} … {fin.isoformat()} "
        f"para {empresa_api}.",
        "info",
    )
    if total > LIMITE_FACTURAS:
        log_fn(
            f"API: ventana con {total} facturas (> {LIMITE_FACTURAS}); se omite la API "
            "y se usará el RPA para estos folios.",
            "warn",
        )
        return {}
    facturas = sipp_api.obtener_facturas(empresa_api, inicio, fin, log_fn=log_fn)
    log_fn(f"API: {len(facturas)} factura(s) traídas; cruzando folios…", "info")

    por_folio: dict[str, list[dict]] = {}
    por_numero: dict[str, list[dict]] = {}
    for f in facturas:
        norm = _norm_folio(f.get("fl_FolioDocumento"))
        if not norm:
            continue
        por_folio.setdefault(norm, []).append(f)
        por_numero.setdefault(_solo_numero(norm), []).append(f)

    resultados: dict[tuple[str, float], tuple[str, str]] = {}
    for mov, folio in candidatos:
        norm = _norm_folio(folio)
        candidatas = por_folio.get(norm)
        if not candidatas:
            # Candidato sin serie de sucursal: cruzar por número.
            candidatas = por_numero.get(_solo_numero(norm))
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
