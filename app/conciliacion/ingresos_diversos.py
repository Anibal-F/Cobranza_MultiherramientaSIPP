"""Lector del reporte "Ingresos Diversos" (lado sistema, alternativo a BigQuery).

El reporte lo exporta el sistema con metadatos de hoja malformados (p. ej.
`xSplit="undefined"` en el sheetView) que hacen que openpyxl falle al abrirlo. Por
eso se lee el XML crudo del .xlsx (zip) directamente, resolviendo shared strings.

Encabezado en la fila 10 (se localiza por contenido, no por número fijo). Columnas
de interés: Referencia, Movimiento (importe), Fecha Envío, Razón Social. El
emparejamiento con el banco es por Referencia + Importe (ver MovimientoConciliacion.clave).
"""

import re
import zipfile
from xml.etree import ElementTree as ET

from .estrategias.base import a_fecha, normalizar_encabezado
from .modelo import MovimientoConciliacion
from ..parsers.base import clean_text, parse_money

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Campo canónico -> encabezados aceptados (ya normalizados con normalizar_encabezado).
_COL_REFERENCIA = {"REFERENCIA"}
_COL_MOVIMIENTO = {"MOVIMIENTO"}
_COL_FECHA = {"FECHA_ENVIO"}
_COL_DESCRIPCION = {"RAZON_SOCIAL", "CLIENTE"}


def _letra_columna(ref_celda: str) -> str:
    """'F11' -> 'F'."""
    return re.match(r"[A-Z]+", ref_celda).group()


def _leer_filas(path: str) -> list[dict[str, object]]:
    """Devuelve las filas de la primera hoja como lista de dicts {columna: valor}
    leyendo el XML directo (evita openpyxl, que no puede abrir este archivo)."""
    with zipfile.ZipFile(path) as z:
        sst: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{_NS}si"):
                sst.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))

        hojas = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n))
        if not hojas:
            raise ValueError("El archivo no contiene hojas de cálculo.")
        root = ET.fromstring(z.read(hojas[0]))
        data = root.find(f"{_NS}sheetData")

        filas: list[dict[str, object]] = []
        for row in data.findall(f"{_NS}row"):
            celdas: dict[str, object] = {}
            for c in row.findall(f"{_NS}c"):
                ref = c.get("r")
                if not ref:
                    continue
                tipo = c.get("t")
                v = c.find(f"{_NS}v")
                istr = c.find(f"{_NS}is")
                if tipo == "s" and v is not None:
                    valor = sst[int(v.text)]
                elif tipo == "inlineStr" and istr is not None:
                    valor = "".join(x.text or "" for x in istr.iter(f"{_NS}t"))
                else:
                    valor = v.text if v is not None else None
                celdas[_letra_columna(ref)] = valor
            filas.append(celdas)
        return filas


def cargar_ingresos_diversos(path: str) -> list[MovimientoConciliacion]:
    """Lee el reporte y devuelve los movimientos del sistema normalizados."""
    filas = _leer_filas(path)

    # Localizar la fila de encabezado por contenido (Referencia + Movimiento) y
    # construir el mapa encabezado_normalizado -> letra de columna.
    header_idx = None
    mapa: dict[str, str] = {}
    for i, celdas in enumerate(filas):
        norm = {L: normalizar_encabezado(v) for L, v in celdas.items() if v}
        vals = set(norm.values())
        if _COL_REFERENCIA & vals and _COL_MOVIMIENTO & vals:
            header_idx = i
            for L, h in norm.items():
                mapa.setdefault(h, L)
            break
    if header_idx is None:
        raise ValueError(
            "No se encontró el encabezado con las columnas Referencia y Movimiento "
            "en el archivo de Ingresos Diversos."
        )

    def _letra(cols: set[str]) -> str | None:
        for h in cols:
            if h in mapa:
                return mapa[h]
        return None

    L_ref = _letra(_COL_REFERENCIA)
    L_mov = _letra(_COL_MOVIMIENTO)
    L_fec = _letra(_COL_FECHA)
    L_desc = _letra(_COL_DESCRIPCION)

    def _txt(v: object) -> str:
        return "" if v is None else str(v)

    movimientos: list[MovimientoConciliacion] = []
    for celdas in filas[header_idx + 1:]:
        referencia = clean_text(_txt(celdas.get(L_ref)))
        importe = parse_money(_txt(celdas.get(L_mov)))
        if not importe:  # descarta filas vacías, totales o sin movimiento
            continue
        movimientos.append(
            MovimientoConciliacion(
                fecha=a_fecha(celdas.get(L_fec)) if L_fec else None,
                descripcion=clean_text(_txt(celdas.get(L_desc))) if L_desc else "",
                referencia=referencia,
                importe=round(abs(importe), 2),
                naturaleza="A",
                origen="SISTEMA",
                raw={h: celdas.get(L) for h, L in mapa.items()},
            )
        )
    return movimientos
