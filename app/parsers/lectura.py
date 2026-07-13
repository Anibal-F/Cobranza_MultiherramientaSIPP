"""Lector universal de tablas: abre un estado de cuenta en cualquiera de los
formatos que exportan los portales bancarios y devuelve las filas como listas de
celdas (posicionales), sin importar el formato.

Formatos soportados (se detectan por los bytes del archivo, no por la extensión,
porque los portales a veces ponen extensión equivocada):
  - .xlsx / .xlsm (ZIP) -> openpyxl, con respaldo por XML crudo si openpyxl no
    puede abrirlo (algunos exportadores generan metadatos inválidos).
  - .xls / .xml SpreadsheetML (XML de Excel 2003) -> xml.etree.
  - .csv / texto -> csv con detección de separador y codificación.
  - .xls binario antiguo (BIFF/OLE2) -> no soportado (no hay librería): se avisa
    al usuario que lo reguarde como .xlsx o .csv.
"""

import csv
import os
import re
import warnings
import zipfile
from xml.etree import ElementTree as ET

import openpyxl

_NS_SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
_NS_XLSX = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Extensiones que la UI puede ofrecer para estados de cuenta.
EXTENSIONES = ["xlsx", "xlsm", "xls", "xml", "csv"]


class FormatoNoSoportado(ValueError):
    """El archivo no se puede leer (p. ej. .xls binario antiguo)."""


def _col_indice(ref_celda: str) -> int:
    """'F11' -> 5 (índice 0-based de la columna)."""
    letras = re.match(r"[A-Z]+", ref_celda).group()
    n = 0
    for c in letras:
        n = n * 26 + (ord(c) - ord("A") + 1)
    return n - 1


# Caché pequeño: durante la autodetección se prueban muchos lectores sobre el
# MISMO archivo; se lee una sola vez. Clave: (ruta, mtime, tamaño). Las filas se
# devuelven tal cual (los consumidores solo las leen, no las mutan).
_CACHE: dict[tuple, list[list]] = {}
_CACHE_MAX = 4


def leer_tabla(path: str) -> list[list]:
    """Devuelve las filas de la primera hoja como listas de celdas."""
    try:
        st = os.stat(path)
        clave = (path, st.st_mtime, st.st_size)
    except OSError:
        clave = None
    if clave is not None and clave in _CACHE:
        return _CACHE[clave]

    filas = _leer_tabla(path)

    if clave is not None:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[clave] = filas
    return filas


def _leer_tabla(path: str) -> list[list]:
    with open(path, "rb") as f:
        cabeza = f.read(2048)

    if cabeza[:4] == b"PK\x03\x04":  # ZIP => xlsx/xlsm
        return _leer_xlsx(path)
    if cabeza[:4] == b"\xD0\xCF\x11\xE0":  # OLE2 => .xls binario (BIFF)
        raise FormatoNoSoportado(
            "El archivo es un Excel binario (.xls) antiguo que no se puede leer "
            "directamente. Ábrelo en Excel y guárdalo como .xlsx o .csv."
        )

    muestra = cabeza.lstrip().lower()
    if muestra.startswith(b"<?xml") or muestra.startswith(b"<workbook") or b"mso-application" in cabeza.lower() or b"office:spreadsheet" in cabeza.lower():
        return _leer_spreadsheetml(path)
    return _leer_csv(path)


# --- xlsx / xlsm ----------------------------------------------------------------

def _tiene_contenido(filas: list[list]) -> bool:
    return any(any(c is not None and str(c).strip() for c in f) for f in filas)


def _leer_xlsx(path: str) -> list[list]:
    # Modo NORMAL primero: algunos exportadores de portal (p. ej. BanBajío) declaran
    # mal la "dimensión" de la hoja; el modo read_only la respeta y no ve los datos
    # (devuelve la hoja vacía). El modo normal la recalcula. Si el normal falla y el
    # read_only sí abre, se usa ese; si ambos devuelven vacío o fallan, se cae al XML.
    for read_only in (False, True):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # "Workbook contains no default style"
                wb = openpyxl.load_workbook(path, read_only=read_only, data_only=True)
            try:
                ws = wb.active
                filas = [list(fila) for fila in ws.iter_rows(values_only=True)]
            finally:
                wb.close()
        except Exception:
            continue
        if _tiene_contenido(filas):
            return filas
    # Respaldo: exportadores que generan sheetViews/panes inválidos y rompen openpyxl.
    return _leer_xlsx_zip(path)


def _leer_xlsx_zip(path: str) -> list[list]:
    with zipfile.ZipFile(path) as z:
        sst: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{_NS_XLSX}si"):
                sst.append("".join(t.text or "" for t in si.iter(f"{_NS_XLSX}t")))
        hojas = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n))
        if not hojas:
            return []
        root = ET.fromstring(z.read(hojas[0]))
        data = root.find(f"{_NS_XLSX}sheetData")
        filas: list[list] = []
        for row in data.findall(f"{_NS_XLSX}row"):
            celdas: dict[int, object] = {}
            maxc = -1
            for c in row.findall(f"{_NS_XLSX}c"):
                ref = c.get("r")
                col = _col_indice(ref) if ref else (maxc + 1)
                maxc = max(maxc, col)
                tipo = c.get("t")
                v = c.find(f"{_NS_XLSX}v")
                istr = c.find(f"{_NS_XLSX}is")
                if tipo == "s" and v is not None:
                    valor = sst[int(v.text)]
                elif tipo == "inlineStr" and istr is not None:
                    valor = "".join(x.text or "" for x in istr.iter(f"{_NS_XLSX}t"))
                else:
                    valor = v.text if v is not None else None
                celdas[col] = valor
            filas.append([celdas.get(i) for i in range(maxc + 1)])
        return filas


# --- SpreadsheetML (.xls / .xml de Excel 2003) ----------------------------------

def _leer_spreadsheetml(path: str) -> list[list]:
    tree = ET.parse(path)
    root = tree.getroot()
    tabla = root.find(".//" + _NS_SS + "Worksheet/" + _NS_SS + "Table")
    if tabla is None:
        return []
    filas: list[list] = []
    for row in tabla.findall(_NS_SS + "Row"):
        vals: list = []
        col = 0
        for celda in row.findall(_NS_SS + "Cell"):
            idx = celda.get(_NS_SS + "Index")
            if idx:
                col = int(idx) - 1  # ss:Index es 1-based
            while len(vals) <= col:
                vals.append(None)
            data = celda.find(_NS_SS + "Data")
            vals[col] = data.text if data is not None else None
            col += 1
        filas.append(vals)
    return filas


# --- CSV ------------------------------------------------------------------------

def _leer_csv(path: str) -> list[list]:
    datos = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                datos = f.read()
            break
        except UnicodeDecodeError:
            continue
    if datos is None:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            datos = f.read()

    muestra = datos[:4096]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
    except csv.Error:
        dialecto = csv.excel  # separador por defecto: coma
    import io

    return [list(fila) for fila in csv.reader(io.StringIO(datos), dialect=dialecto)]
