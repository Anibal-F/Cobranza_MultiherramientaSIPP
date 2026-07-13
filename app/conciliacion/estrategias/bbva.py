"""Estrategia BBVA (formato real ya inspeccionado).

Columnas del Excel (fila 1 = encabezado, datos desde la fila 2):
    Fecha Operación | Concepto | Referencia | Referencia Ampliada | Cargo | Abono | Saldo
La fecha viene como serial de Excel (p. ej. 45962) y hay muchas filas vacías
intercaladas que la base descarta automáticamente.
"""

from .base import EstrategiaBancoExcel


class BBVA(EstrategiaBancoExcel):
    nombre = "BBVA"
    fila_encabezado = 1

    FIRMA = {"FECHA_OPERACION", "CONCEPTO"}
    COLS_FECHA = {"FECHA_OPERACION"}
    COLS_DESCRIPCION = {"CONCEPTO"}
    COLS_REFERENCIA = {"REFERENCIA"}
    COLS_ABONO = {"ABONO"}
    COLS_CARGO = {"CARGO"}
    COLS_SALDO = {"SALDO"}
