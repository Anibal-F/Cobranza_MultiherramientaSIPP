"""Banorte: Fecha | Descripción | Referencia | Depósitos | Retiros | Saldo."""

from .base import EstrategiaBancoExcel


class Banorte(EstrategiaBancoExcel):
    nombre = "BANORTE"

    FIRMA = {"REFERENCIA", "DEPOSITOS", "RETIROS"}
    COLS_FECHA = {"FECHA"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_ABONO = {"DEPOSITOS"}
    COLS_CARGO = {"RETIROS"}
    COLS_SALDO = {"SALDO"}
