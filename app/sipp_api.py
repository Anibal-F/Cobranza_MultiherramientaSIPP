"""Cliente HTTP de la API de la Multiherramienta de Cobranza (SIPP).

TI expuso como API dos pantallas que antes se consultaban con el CSV local de
clientes y con el RPA de folios:
  - GET /api/facturas          -> facturas por empresa + rango de VENCIMIENTO
                                  (folio, cliente, sucursal, saldo, UUID).
  - GET /api/clientes          -> maestro de clientes (id, RFC, razón social).
  - GET /api/clientes/sucursales -> plazas (sucursales) por cliente.

Estado: la API está en PRODUCCIÓN (liberada 2026-07-21). URL y token son
configurables por variable de entorno (SIPP_API_URL / SIPP_API_TOKEN) y caen al
valor productivo si no se definen. Para volver al ambiente de pruebas, exportar:
  SIPP_API_URL=https://us-central1-soluciones-petroil.cloudfunctions.net/billing-toolkit-testing
  SIPP_API_TOKEN=P6sgpjDWQayUBuURQTDQ4JVio2upEfrP4Cqg

Cada función lanza `SippAPIError` ante un fallo (red, token, formato) para que el
llamador pueda caer a la fuente local (CSV / RPA) — ver el diseño "API primero,
respaldo local".
"""

import os
from datetime import date

import requests

# URL y token: configurables por entorno; default = ambiente PRODUCTIVO.
BASE_URL = os.environ.get("SIPP_API_URL", "https://billing-toolkit.petroil.com.mx").rstrip("/")
AUTH_TOKEN = os.environ.get("SIPP_API_TOKEN", "RnTcrAzgAYE2Gc9sRjZHyWGMD8BB6k797GuG")

TIMEOUT = 30          # segundos por petición
PAGE_SIZE = 100       # la API topa el pageSize en 100 (aunque se pida más)
MAX_PAGINAS = 500     # tope de seguridad para no paginar sin fin


class SippAPIError(Exception):
    """Fallo al consultar la API (red, autenticación o respuesta inesperada)."""


def _get(path: str, params: dict) -> dict:
    """GET con el header de auth. Devuelve el JSON o lanza SippAPIError."""
    try:
        resp = requests.get(
            f"{BASE_URL}{path}",
            params=params,
            headers={"x-auth-token": AUTH_TOKEN},
            timeout=TIMEOUT,
        )
    except requests.RequestException as ex:
        raise SippAPIError(f"No se pudo conectar con la API: {ex}") from ex
    if resp.status_code != 200:
        raise SippAPIError(f"La API respondió {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as ex:
        raise SippAPIError("La API devolvió una respuesta no-JSON.") from ex
    # La API usa {"msg": "...", "data": [...]}. Un error viene con data=null y un
    # msg distinto de "Success" (p. ej. "... son requeridos").
    if data.get("data") is None and data.get("msg") not in (None, "Success"):
        raise SippAPIError(str(data.get("msg")))
    return data


def _paginar(path: str, params: dict, log_fn=None) -> list[dict]:
    """Recorre todas las páginas de un endpoint paginado y junta `data`."""
    filas: list[dict] = []
    pagina = 1
    while pagina <= MAX_PAGINAS:
        p = dict(params, page=pagina, pageSize=PAGE_SIZE)
        resp = _get(path, p)
        datos = resp.get("data") or []
        filas.extend(datos)
        meta = resp.get("meta") or {}
        total_paginas = meta.get("totalPages") or 1
        if log_fn and total_paginas > 1:
            log_fn(f"  API {path}: página {pagina}/{total_paginas} ({len(filas)} filas)", "info")
        if pagina >= total_paginas or not datos:
            break
        pagina += 1
    return filas


def disponible() -> bool:
    """True si la API responde (health-check barato). Sirve para decidir entre la
    API y el respaldo local sin propagar excepciones."""
    try:
        _get("/api/clientes", {"page": 1, "pageSize": 1})
        return True
    except SippAPIError:
        return False


def obtener_clientes(solo_activos: bool = True, log_fn=None) -> list[dict]:
    """Maestro de clientes: [{id_Cliente, de_RFC, de_RazonSocial, sn_Activo}]."""
    params: dict = {}
    if solo_activos:
        params["sn_Activo"] = 1
    return _paginar("/api/clientes", params, log_fn)


def sucursales_clientes(id_cliente: int | None = None, solo_activos: bool = True) -> list[dict]:
    """Plazas por cliente: [{de_Cliente, id_Cliente, de_RFC, sn_Activo, plazas:[{id_Plaza, nb_Plaza}]}]."""
    params: dict = {}
    if id_cliente is not None:
        params["id_Cliente"] = id_cliente
    if solo_activos:
        params["sn_Activo"] = 1
    return _get("/api/clientes/sucursales", params).get("data") or []


def _params_facturas(empresa: str, fecha_inicio: date, fecha_fin: date) -> dict:
    return {
        "empresa": empresa,
        "fechaVencimientoInicio": fecha_inicio.isoformat(),
        "fechaVencimientoFin": fecha_fin.isoformat(),
    }


def contar_facturas(empresa: str, fecha_inicio: date, fecha_fin: date) -> int:
    """totalRecords de la ventana SIN traer los datos (page=1, pageSize=1). Sirve
    para decidir si la ventana es manejable antes de paginarla toda."""
    resp = _get("/api/facturas", dict(_params_facturas(empresa, fecha_inicio, fecha_fin), page=1, pageSize=1))
    return int((resp.get("meta") or {}).get("totalRecords") or 0)


def obtener_facturas(
    empresa: str,
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    folio: str | None = None,
    uuid: str | None = None,
    log_fn=None,
) -> list[dict]:
    """Facturas de clientes de `empresa` (razón social). Filtros opcionales:
    - `folio`: folio INTERNO exacto (serie + número, ej. 'FCL183653').
    - `uuid`: folio FISCAL (UUID del CFDI).
    - `fecha_inicio`/`fecha_fin`: rango de VENCIMIENTO (ya no obligatorio en la API).
    Cada fila: fl_FolioDocumento, fh_Documento, fh_Vencimiento, im_Total,
    im_SaldoFactura, de_UUID, de_RazonSocialCliente, nb_Sucursal."""
    params: dict = {"empresa": empresa}
    if fecha_inicio is not None:
        params["fechaVencimientoInicio"] = fecha_inicio.isoformat()
    if fecha_fin is not None:
        params["fechaVencimientoFin"] = fecha_fin.isoformat()
    if folio:
        params["folio"] = folio
    if uuid:
        params["uuid"] = uuid
    return _paginar("/api/facturas", params, log_fn)
