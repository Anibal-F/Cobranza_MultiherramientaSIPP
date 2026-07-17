"""Esquema único de la conciliación bancaria.

`MovimientoConciliacion` es la dataclass común a la que se normaliza todo: tanto
cada movimiento del banco (vía los parsers en app/parsers, convertido en
lector_banco.py) como cada movimiento del sistema (Excel de Ingresos Diversos o
BigQueryRepository). Reusa el mismo contrato del procedimiento almacenado de
referencia (fh_Movimiento / de_Descripcion / de_Referencia / im_Importe /
cl_Naturaleza / im_Saldo).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class MovimientoConciliacion:
    fecha: Optional[date]
    descripcion: str                     # de_Descripcion (BBVA: columna "Concepto")
    referencia: str                      # de_Referencia  (BBVA: columna "Referencia")
    importe: float                       # im_Importe, valor absoluto, 2 decimales
    naturaleza: str = "A"                # 'A' = abono / 'C' = cargo
    saldo: Optional[float] = None
    origen: str = ""                     # "BANCO:BBVA" | "SISTEMA"
    raw: dict = field(default_factory=dict)  # fila original (auditoría / detalle en UI)

    @property
    def texto(self) -> str:
        """Descripción + referencia. Se usa para detectar la leyenda de devolución
        de cheque (ver conciliador.es_devolucion_cheque)."""
        return f"{self.descripcion} {self.referencia}".strip()

    @classmethod
    def desde_sistema(cls, fila: dict) -> "MovimientoConciliacion":
        """Construye un movimiento del lado sistema a partir de una fila cruda del
        BigQueryRepository (llaves: descripcion, referencia, importe, fecha)."""
        return cls(
            fecha=fila.get("fecha"),
            descripcion=str(fila.get("descripcion") or ""),
            referencia=str(fila.get("referencia") or ""),
            # abs(): el lado banco maneja abonos positivos; se normaliza el importe del
            # sistema a valor absoluto igual que el lector de Excel (ingresos_diversos),
            # por si algún día im_Movimiento trae signos (hoy es 100% positivo).
            importe=round(abs(float(fila.get("importe") or 0)), 2),
            naturaleza="A",
            origen="SISTEMA",
            raw=dict(fila),
        )


@dataclass
class ResultadoConciliacion:
    # Pares (movimiento banco, movimiento sistema) que cruzaron.
    conciliados: list[tuple[MovimientoConciliacion, MovimientoConciliacion]] = field(default_factory=list)
    # En el archivo del banco pero NO en el sistema (SP: tipos 3/5).
    solo_banco: list[MovimientoConciliacion] = field(default_factory=list)
    # En el sistema pero NO en el archivo del banco (SP: tipos 2/4). Se calcula
    # pero ya no se muestra en la UI (se reemplazó por "posibles repetidos").
    solo_sistema: list[MovimientoConciliacion] = field(default_factory=list)
    # Devoluciones de cheque, apartadas ANTES de comparar (leyenda configurable).
    devoluciones_cheque: list[MovimientoConciliacion] = field(default_factory=list)
    # Movimientos del sistema que se repiten entre sí (misma referencia, descripción
    # e importe): posibles duplicados capturados en el sistema.
    posibles_repetidos_sistema: list[MovimientoConciliacion] = field(default_factory=list)
    # Movimientos (banco y/o sistema) cuya fecha cae FUERA de la ventana común de
    # fechas de ambos archivos: se apartan ANTES de comparar y no se concilian ni
    # cuentan como duplicados. Se distinguen por su `origen` ("BANCO:*" / "SISTEMA").
    fuera_de_rango: list[MovimientoConciliacion] = field(default_factory=list)
    # Ventana común (inicio, fin) usada para filtrar por fecha, o None si no se pudo
    # calcular (algún lado sin fechas) y por tanto no se filtró.
    ventana: Optional[tuple[date, date]] = None

    @property
    def resumen(self) -> dict[str, int]:
        """Conteo por grupo, para KPIs de la UI."""
        return {
            "conciliados": len(self.conciliados),
            "solo_banco": len(self.solo_banco),
            "solo_sistema": len(self.solo_sistema),
            "devoluciones_cheque": len(self.devoluciones_cheque),
            "posibles_repetidos_sistema": len(self.posibles_repetidos_sistema),
            "fuera_de_rango": len(self.fuera_de_rango),
        }
