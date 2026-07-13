"""Intercam: Fecha | Descripción | Referencia | Cargos | Abonos | Saldo.

Se distingue de Banregio por el plural CARGOS (Banregio usa CARGO)."""

from .base import EstrategiaBancoExcel


class Intercam(EstrategiaBancoExcel):
    nombre = "INTERCAM"

    FIRMA = {"REFERENCIA", "CARGOS", "ABONOS"}
    COLS_FECHA = {"FECHA"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_ABONO = {"ABONOS"}
    COLS_CARGO = {"CARGOS"}
    COLS_SALDO = {"SALDO"}
