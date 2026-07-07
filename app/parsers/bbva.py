"""Parser del estado de cuenta de BBVA.

El archivo llega con extensión .xls pero NO es un Excel binario (BIFF): es
**SpreadsheetML** (XML de Excel 2003, `<?mso-application progid="Excel.Sheet"?>`).
openpyxl no lo lee; se parsea con xml.etree (nativo).

La hoja trae una fila 'Cuenta | <número>', luego el encabezado:
    Fecha Operación | Concepto | Referencia | Referencia Ampliada | Cargo | Abono | Saldo
y después los movimientos. Se procesan solo los ABONOS (cobros).
"""

import xml.etree.ElementTree as ET
from datetime import date, datetime

from ..models import Movimiento
from ..textutils import normalizar

BANCO = "BBVA"
_NS = "urn:schemas-microsoft-com:office:spreadsheet"

# Marcadores del encabezado (normalizados). Identifican la tabla y distinguen el
# archivo de BBVA de otros .xls/.xml.
_HEADERS_TABLA = {"FECHA OPERACION", "CONCEPTO", "ABONO"}


def _q(tag: str) -> str:
    return f"{{{_NS}}}{tag}"


def _key(valor) -> str:
    return normalizar(str(valor if valor is not None else "")).upper()


def _monto(valor) -> float:
    """Convierte a float. Los importes de BBVA vienen como número (incluye
    notación científica en el saldo, ej. '2.417856118E7')."""
    if valor is None or str(valor).strip() == "":
        return 0.0
    try:
        return float(str(valor).strip())
    except ValueError:
        return 0.0


def _fecha(valor):
    if not valor:
        return None
    texto = str(valor).strip()[:10]
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


def _filas(path: str) -> list[list]:
    """Devuelve las filas de la primera hoja como listas de celdas (str|None),
    respetando ss:Index (celdas omitidas por estar vacías)."""
    tree = ET.parse(path)
    root = tree.getroot()
    tabla = root.find(".//" + _q("Worksheet") + "/" + _q("Table"))
    if tabla is None:
        return []
    filas: list[list] = []
    for row in tabla.findall(_q("Row")):
        vals: list = []
        col = 0
        for celda in row.findall(_q("Cell")):
            idx = celda.get(_q("Index"))
            if idx:
                col = int(idx) - 1  # ss:Index es 1-based
            while len(vals) <= col:
                vals.append(None)
            data = celda.find(_q("Data"))
            vals[col] = data.text if data is not None else None
            col += 1
        filas.append(vals)
    return filas


def _buscar_encabezado(filas: list[list]) -> int | None:
    for i, fila in enumerate(filas):
        claves = {_key(v) for v in fila if v is not None}
        if _HEADERS_TABLA.issubset(claves):
            return i
    return None


def detect(path: str) -> bool:
    if not path.lower().endswith((".xls", ".xml")):
        return False
    try:
        return _buscar_encabezado(_filas(path)) is not None
    except Exception:
        return False


def parse(path: str) -> list[Movimiento]:
    filas = _filas(path)
    enc = _buscar_encabezado(filas)
    if enc is None:
        return []

    encabezados = [_key(v) for v in filas[enc]]

    def col(nombre: str) -> int | None:
        try:
            return encabezados.index(nombre)
        except ValueError:
            return None

    c_fecha = col("FECHA OPERACION")
    c_concepto = col("CONCEPTO")
    c_ref = col("REFERENCIA")
    c_amp = col("REFERENCIA AMPLIADA")
    c_cargo = col("CARGO")
    c_abono = col("ABONO")
    c_saldo = col("SALDO")

    def g(fila: list, i: int | None) -> str:
        if i is None or i >= len(fila) or fila[i] is None:
            return ""
        return str(fila[i]).strip()

    movimientos: list[Movimiento] = []
    for fila in filas[enc + 1:]:
        abono = _monto(g(fila, c_abono))
        if abono <= 0:
            continue  # solo cobros (abonos); cargos/comisiones se ignoran

        concepto = g(fila, c_concepto)
        ampliada = g(fila, c_amp)
        referencia = g(fila, c_ref)
        descripcion = " ".join(x for x in (concepto, ampliada) if x)

        # Compensaciones por desfase de SPEI: ajustes internos, no cobros.
        if "COMPENSACION" in normalizar(descripcion).upper():
            continue

        movimientos.append(
            Movimiento(
                banco=BANCO,
                fecha=_fecha(g(fila, c_fecha)),
                descripcion=descripcion,
                referencia=referencia,
                concepto=concepto,
                cargo=_monto(g(fila, c_cargo)),
                abono=abono,
                saldo=_monto(g(fila, c_saldo)) or None,
                # Concepto + Referencia (cuenta ordenante) + Referencia Ampliada
                # (folios FMZ/FLM, banco, etc.) para el match por cuenta/folio.
                texto_busqueda=" ".join(x for x in (concepto, referencia, ampliada) if x),
            )
        )
    return movimientos
