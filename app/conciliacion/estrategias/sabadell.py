"""Sabadell: Fecha Valor | Referencia | Cargo | Abono | Saldo.

No hay columna de descripción propia: la REFERENCIA se usa como descripción y
como referencia (igual que en el SP)."""

from .base import EstrategiaBancoExcel


class Sabadell(EstrategiaBancoExcel):
    nombre = "SABADELL"

    FIRMA = {"FECHA_VALOR", "ABONO", "CARGO"}
    COLS_FECHA = {"FECHA_VALOR"}
    COLS_DESCRIPCION = {"REFERENCIA"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_ABONO = {"ABONO"}
    COLS_CARGO = {"CARGO"}
    COLS_SALDO = {"SALDO"}
