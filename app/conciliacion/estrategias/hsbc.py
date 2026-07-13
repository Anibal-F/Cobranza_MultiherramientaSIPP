"""HSBC: Fecha Valor | Descripción | Referencia Bancaria | Importe de Crédito |
Importe del Débito | Saldo."""

from .base import EstrategiaBancoExcel


class HSBC(EstrategiaBancoExcel):
    nombre = "HSBC"

    FIRMA = {"FECHA_VALOR", "IMPORTE_DE_CREDITO"}
    COLS_FECHA = {"FECHA_VALOR"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_REFERENCIA = {"REFERENCIA_BANCARIA", "REFERENCIABANCARIA"}
    COLS_ABONO = {"IMPORTE_DE_CREDITO"}
    COLS_CARGO = {"IMPORTE_DEL_DEBITO"}
    COLS_SALDO = {"SALDO"}
