"""Ve por Más: Fecha | Descripción | Referencia | Retiros | Depósitos | Saldo.

Comparte el mismo layout que Banorte y BX; usar el selector manual de banco para
forzarlo cuando la autodetección no baste."""

from .base import EstrategiaBancoExcel


class VePorMas(EstrategiaBancoExcel):
    nombre = "VE POR MAS"

    FIRMA = {"REFERENCIA", "DEPOSITOS", "RETIROS"}
    COLS_FECHA = {"FECHA"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_ABONO = {"DEPOSITOS"}
    COLS_CARGO = {"RETIROS"}
    COLS_SALDO = {"SALDO"}
