"""Banco BX: Fecha | Descripción | Referencia | Retiros | Depósitos | Saldo.

Comparte el mismo layout que Banorte y Ve por Más (Depósitos/Retiros +
Referencia), así que la autodetección no los puede diferenciar por encabezados:
usar el selector manual de banco para forzar BX cuando sea necesario."""

from .base import EstrategiaBancoExcel


class BX(EstrategiaBancoExcel):
    nombre = "BX"

    FIRMA = {"REFERENCIA", "DEPOSITOS", "RETIROS"}
    COLS_FECHA = {"FECHA"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_ABONO = {"DEPOSITOS"}
    COLS_CARGO = {"RETIROS"}
    COLS_SALDO = {"SALDO"}
