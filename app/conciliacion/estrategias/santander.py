"""Santander: Fecha y Hora Contable | Descripción | Referencia | Importe | Saldo.

Una sola columna IMPORTE con signo. Según el SP: IMPORTE > 0 -> Cargo; en otro
caso -> Abono (por eso abono_es_positivo = False)."""

from .base import EstrategiaBancoExcel


class Santander(EstrategiaBancoExcel):
    nombre = "SANTANDER"

    FIRMA = {"FECHA_Y_HORA_CONTABLE"}
    COLS_FECHA = {"FECHA_Y_HORA_CONTABLE"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_IMPORTE = {"IMPORTE"}
    COLS_SALDO = {"SALDO"}
    abono_es_positivo = False
