"""Bancoppel: Fecha | Concepto | Referencia | Importe | Saldo.

Una sola columna IMPORTE con signo. Según el SP: IMPORTE < 0 -> Cargo; en otro
caso -> Abono (abono_es_positivo = True)."""

from .base import EstrategiaBancoExcel


class Bancoppel(EstrategiaBancoExcel):
    nombre = "BANCOPPEL"

    FIRMA = {"CONCEPTO", "IMPORTE"}
    COLS_FECHA = {"FECHA"}
    COLS_DESCRIPCION = {"CONCEPTO"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_IMPORTE = {"IMPORTE"}
    COLS_SALDO = {"SALDO"}
    abono_es_positivo = True
