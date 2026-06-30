from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class ClienteCuenta:
    cuenta: str
    cliente: str
    banco: str
    plaza: str


@dataclass
class Movimiento:
    banco: str
    fecha: Optional[date]
    descripcion: str
    referencia: str
    concepto: str
    cargo: float
    abono: float
    saldo: Optional[float]
    texto_busqueda: str
    cliente_match: Optional[str] = None
    cuenta_match: Optional[str] = None
    banco_match: Optional[str] = None
    identificado_por_nombre: bool = False
    identificado_por_folio: bool = False
    identificado_manual: bool = False
    folio_manual: Optional[str] = None

    @property
    def identificado(self) -> bool:
        return self.cliente_match is not None
