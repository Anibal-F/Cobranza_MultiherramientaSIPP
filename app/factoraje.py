"""Extrae los montos de interés de un PDF de factoraje (NAFIN/BBVA) de BAJA
FERRIES para capturarlos en SIPP como "Interés Factoraje".

El PDF trae una tabla por documento descontado:
  Cuenta de Depósito | EPO | Número de Documento | Moneda | Monto Documento |
  % Descuento | Monto a Descontar | Monto Intereses | Monto a Recibir

Mapeo relevante:
  - Número de Documento (FLM/FMZ ...) = folio del movimiento.
  - Monto a Recibir                    = abono NETO que llega al banco (CSV).
  - Monto Intereses                    = lo que se captura en SIPP.
"""

import re
from dataclasses import dataclass

# Serie de folio: F + 2 iniciales de sucursal + número (FLM/FMZ/FCL...).
_FOLIO_RE = re.compile(r"F[A-Z]{2}\s*\d{3,}", re.IGNORECASE)
_MONEY_RE = re.compile(r"\d[\d,]*\.\d{2}")


def _norm_folio(texto: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (texto or "").upper())


def _money(texto: str):
    try:
        return float(texto.replace(",", ""))
    except (AttributeError, ValueError):
        return None


@dataclass
class FilaFactoraje:
    folio: str            # normalizado, ej. "FMZ244573"
    folio_texto: str      # original, ej. "FMZ 244573"
    monto_documento: float
    monto_intereses: float
    monto_recibir: float


def _parsear_texto_fila(texto: str) -> FilaFactoraje | None:
    """Parsea una fila (celdas unidas o una línea de texto). Usa la posición de
    los importes: el primero es Monto Documento, el penúltimo Monto Intereses y
    el último Monto a Recibir (robusto ante columnas intermedias)."""
    texto = " ".join((texto or "").split())
    m = _FOLIO_RE.search(texto)
    if not m:
        return None
    montos = [v for x in _MONEY_RE.findall(texto) if (v := _money(x)) is not None]
    if len(montos) < 3:
        return None
    folio_texto = " ".join(m.group(0).split())
    return FilaFactoraje(
        folio=_norm_folio(folio_texto),
        folio_texto=folio_texto,
        monto_documento=montos[0],
        monto_intereses=montos[-2],
        monto_recibir=montos[-1],
    )


def extraer_factoraje(ruta: str) -> list[FilaFactoraje]:
    """Lee el PDF y regresa una fila por documento con su interés. Intenta primero
    la extracción de tablas (el PDF tiene bordes); si no, cae a texto por línea
    y, si el PDF no trae capa de texto, a OCR."""
    import pdfplumber

    filas: list[FilaFactoraje] = []
    vistos: set[str] = set()

    def _agregar(fila: FilaFactoraje | None) -> None:
        if fila and fila.folio not in vistos:
            vistos.add(fila.folio)
            filas.append(fila)

    with pdfplumber.open(ruta) as pdf:
        for pagina in pdf.pages:
            for tabla in pagina.extract_tables() or []:
                for row in tabla:
                    celdas = " ".join((c or "").replace("\n", " ") for c in row)
                    _agregar(_parsear_texto_fila(celdas))
            texto = pagina.extract_text() or ""
            for linea in texto.splitlines():
                _agregar(_parsear_texto_fila(linea))

    if filas:
        return filas

    # PDF sin capa de texto: OCR por página (mismo enfoque que los comprobantes).
    from .extraccion_adjuntos import _extraer_texto_pdf

    texto = _extraer_texto_pdf(ruta)
    for linea in texto.splitlines():
        _agregar(_parsear_texto_fila(linea))
    return filas
