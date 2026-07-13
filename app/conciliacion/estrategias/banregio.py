"""Banregio: Fecha | Descripción | Referencia | Cargo | Abonos | Saldo.

Se distingue de Intercam por el singular CARGO (Intercam usa CARGOS)."""

from .base import EstrategiaBancoExcel


class Banregio(EstrategiaBancoExcel):
    nombre = "BANREGIO"

    FIRMA = {"REFERENCIA", "CARGO", "ABONOS"}
    COLS_FECHA = {"FECHA"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_ABONO = {"ABONOS"}
    COLS_CARGO = {"CARGO"}
    COLS_SALDO = {"SALDO"}
