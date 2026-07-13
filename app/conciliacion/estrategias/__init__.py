"""Registro y despacho de estrategias de banco (espejo de app/parsers/__init__.py).

Agregar un banco = crear su subclase de EstrategiaBancoExcel y registrarla en
ESTRATEGIAS. Los mapeos provienen del SP de referencia
(upR_cont_ConciliacionesBancarias_LeerMovimientos).

Orden de detección: primero los formatos con firma distintiva; al final los que
comparten layout (Banorte / BX / Ve por Más traen las mismas columnas
Depósitos/Retiros + Referencia y NO se pueden diferenciar por encabezados). Para
esos casos, usar el selector manual (ESTRATEGIAS_POR_NOMBRE) que ofrece la vista.
"""

import openpyxl

from .banamex import Banamex
from .banbajio import BanBajio
from .bancoppel import Bancoppel
from .banorte import Banorte
from .banregio import Banregio
from .base import EstrategiaBancoExcel, normalizar_encabezado
from .bbva import BBVA
from .bx import BX
from .hsbc import HSBC
from .intercam import Intercam
from .sabadell import Sabadell
from .santander import Santander
from .scotiabank import Scotiabank
from .veporma import VePorMas

# Orden importa: firmas distintivas primero; layouts compartidos (Banorte/BX/
# VePorMas) al final — la autodetección devuelve el primero que coincida.
ESTRATEGIAS: list[EstrategiaBancoExcel] = [
    BBVA(),
    HSBC(),
    Sabadell(),
    BanBajio(),
    Santander(),
    Scotiabank(),
    Bancoppel(),
    Banregio(),
    Intercam(),
    Banamex(),
    Banorte(),
    BX(),
    VePorMas(),
]

# Acceso por nombre para forzar un banco (selector manual de la UI), útil cuando
# dos formatos comparten encabezados.
ESTRATEGIAS_POR_NOMBRE: dict[str, EstrategiaBancoExcel] = {e.nombre: e for e in ESTRATEGIAS}


def _headers_normalizados(path: str) -> set[str]:
    """Lee las primeras filas del .xlsx y devuelve sus encabezados normalizados.
    Se revisan varias filas por si algún banco mete títulos antes del encabezado."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        filas = list(ws.iter_rows(min_row=1, max_row=5, values_only=True))
    finally:
        wb.close()
    encabezados: set[str] = set()
    for fila in filas:
        for celda in fila:
            h = normalizar_encabezado(celda)
            if h:
                encabezados.add(h)
    return encabezados


def detectar_estrategia(path: str) -> EstrategiaBancoExcel | None:
    """Devuelve la estrategia cuyo formato coincide con el archivo, o None."""
    headers = _headers_normalizados(path)
    for estrategia in ESTRATEGIAS:
        try:
            if estrategia.detectar(headers):
                return estrategia
        except Exception:
            continue
    return None
