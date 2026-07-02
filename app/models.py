from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class ClienteCuenta:
    cuenta: str
    cliente: str
    banco: str
    plaza: str
    # RFC del cliente: clave alterna estable para identificar cuando la CLABE
    # viene enmascarada pero el RFC no. Opcional (retrocompatible con catálogos
    # sin esta columna).
    rfc: str = ""


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
    # Sucursal declarada por el usuario (override de la sugerida del estado de cuenta).
    sucursal_declarada: Optional[str] = None
    # Sucursal leída directamente de la factura en SIPP cuando el movimiento se
    # identificó por folio (fuente confiable, gana sobre la sugerida del estado
    # de cuenta pero no sobre la declarada por el usuario).
    sucursal_por_folio: Optional[str] = None
    # Sugerencia de sucursal por estado de cuenta, CONGELADA al guardar el
    # snapshot del historial, para poder mostrarla al restaurar sin recargar el
    # .xlsx. No es fuente de verdad: si el estado de cuenta está cargado se
    # recalcula en vivo.
    sucursal_sugerida: Optional[str] = None
    sucursal_sugerida_motivo: Optional[str] = None

    @property
    def identificado(self) -> bool:
        return self.cliente_match is not None
