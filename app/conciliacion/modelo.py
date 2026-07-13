"""Esquema único de la conciliación bancaria.

`MovimientoConciliacion` es la dataclass común a la que se normaliza 
TODO: tanto cada fila del Excel del banco (vía las estrategias) como cada movimiento del
sistema (vía el BigQueryRepository). Reusa el mismo contrato del procedimiento
almacenado de referencia (fh_Movimiento / de_Descripcion / de_Referencia /
im_Importe / cl_Naturaleza / im_Saldo).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..textutils import normalizar


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
        """Texto base del emparejamiento: descripción + referencia."""
        return f"{self.descripcion} {self.referencia}".strip()

    def texto_clave(self) -> str:
        """Texto base del emparejamiento, ASIMÉTRICO según el origen:
        - Banco: se prioriza el Concepto/Descripción (donde el banco pone la
        referencia del pago); si viene vacío, se usa la Referencia.
        - Sistema (Ingresos Diversos / BigQuery): se usa la Referencia.
        """
        if self.origen.startswith("BANCO"):
            return self.descripcion or self.referencia
        return self.referencia

    def clave(self) -> tuple[str, float]:
        """Llave de conciliación: texto_clave normalizado (mayúsculas, sin acentos,
        solo alfanumérico) + importe redondeado a 2 decimales."""
        return (normalizar(self.texto_clave()), round(self.importe, 2))

    @classmethod
    def desde_sistema(cls, fila: dict) -> "MovimientoConciliacion":
        """Construye un movimiento del lado sistema a partir de una fila cruda del
        BigQueryRepository (llaves: descripcion, referencia, importe, fecha)."""
        return cls(
            fecha=fila.get("fecha"),
            descripcion=str(fila.get("descripcion") or ""),
            referencia=str(fila.get("referencia") or ""),
            importe=round(float(fila.get("importe") or 0), 2),
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
    # En el sistema pero NO en el archivo del banco (SP: tipos 2/4).
    solo_sistema: list[MovimientoConciliacion] = field(default_factory=list)
    # Devoluciones de cheque, apartadas ANTES de comparar (leyenda configurable).
    devoluciones_cheque: list[MovimientoConciliacion] = field(default_factory=list)

    @property
    def resumen(self) -> dict[str, int]:
        """Conteo por grupo, para KPIs de la UI."""
        return {
            "conciliados": len(self.conciliados),
            "solo_banco": len(self.solo_banco),
            "solo_sistema": len(self.solo_sistema),
            "devoluciones_cheque": len(self.devoluciones_cheque),
        }
