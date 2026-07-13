"""Banamex: Fecha | Descripción | Retiros | Depósitos | Saldo.

No trae columna de referencia: se usa la descripción como referencia
(referencia_desde_descripcion). Se distingue de Banorte/BX/Ve por Más — que
comparten Depósitos/Retiros — porque NO tiene columna REFERENCIA (FIRMA_AUSENTE)."""

from .base import EstrategiaBancoExcel


class Banamex(EstrategiaBancoExcel):
    nombre = "BANAMEX"

    FIRMA = {"DEPOSITOS", "RETIROS", "DESCRIPCION"}
    FIRMA_AUSENTE = {"REFERENCIA"}
    COLS_FECHA = {"FECHA"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_ABONO = {"DEPOSITOS"}
    COLS_CARGO = {"RETIROS"}
    COLS_SALDO = {"SALDO"}
    referencia_desde_descripcion = True
