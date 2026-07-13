"""BanBajío: Fecha Movimiento | Descripción | Recibo | Cargos | Abonos | Saldo.

La referencia es la columna RECIBO."""

from .base import EstrategiaBancoExcel


class BanBajio(EstrategiaBancoExcel):
    nombre = "BANBAJIO"

    FIRMA = {"FECHA_MOVIMIENTO", "RECIBO"}
    COLS_FECHA = {"FECHA_MOVIMIENTO"}
    COLS_DESCRIPCION = {"DESCRIPCION"}
    COLS_REFERENCIA = {"RECIBO"}
    COLS_ABONO = {"ABONOS"}
    COLS_CARGO = {"CARGOS"}
    COLS_SALDO = {"SALDO"}
