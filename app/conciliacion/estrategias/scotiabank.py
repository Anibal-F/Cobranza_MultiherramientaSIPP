"""Scotiabank: Fecha | Leyenda + LeyendaB | Referencia Numérica | Importe | Tipo | Saldo.

La descripción se compone de dos columnas (LEYENDA + LEYENDAB) y la naturaleza se
toma de la columna TIPO ('ABONO'/'CARGO'), no del signo del importe."""

from typing import Optional

from .base import EstrategiaBancoExcel
from ...parsers.base import parse_money


class Scotiabank(EstrategiaBancoExcel):
    nombre = "SCOTIABANK"

    FIRMA = {"IMPORTE", "TIPO"}
    COLS_FECHA = {"FECHA"}
    COLS_DESCRIPCION = {"LEYENDA", "LEYENDAB", "LEYENDA_B"}
    COLS_REFERENCIA = {"REFERENCIA_NUMERICA", "REFERENCIANUMERICA"}
    COLS_IMPORTE = {"IMPORTE"}
    COLS_TIPO = {"TIPO"}
    COLS_SALDO = {"SALDO"}

    def _importe_y_naturaleza(self, fila: tuple, idx: dict) -> Optional[tuple[float, str]]:
        valor = parse_money(self._texto(self._celda(fila, idx["importe"])))
        if not valor:
            return None
        tipo = self._texto(self._celda(fila, idx["tipo"])).upper()
        return abs(valor), ("A" if "ABONO" in tipo else "C")
