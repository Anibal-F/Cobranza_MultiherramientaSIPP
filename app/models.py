from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# Opciones del modal "Agregar Movimientos" de SIPP (checkboxes "¿Es ...?").
# El orden refleja el de SIPP. Se pueden combinar varias en un mismo movimiento.
OPCIONES_TIPO_MOVIMIENTO = [
    "Anticipo",
    "Contado",
    "Efectivo",
    "TPV",
    "Factoraje Financiero",
    "Indemnización",
    "Monedero Electrónico",
]


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
    # True si este movimiento ya venía en una extracción previa YA SUBIDA a SIPP
    # (CSV bancario acumulativo). Se marca al cargar el CSV; se muestra en gris y
    # se excluye de la carga a SIPP para no duplicar.
    ya_subido: bool = False
    # Movimiento excluido manualmente del RPA que sube a SIPP (p. ej. "Traspaso a
    # Filiales": no es cobranza de un cliente). Se detecta automáticamente cuando
    # la descripción contiene "TRASPASO" y el usuario puede alternarlo. Se pinta en
    # rojo tenue, no se le asigna cliente y su fila se omite del CSV que se sube.
    excluido: bool = False
    # Tipos de movimiento para la captura en SIPP por el modal "Agregar
    # Movimientos" (bancos que SIPP no importa por Excel, p. ej. BanBajío).
    # Cada elemento es una etiqueta de la lista OPCIONES_TIPO_MOVIMIENTO (ej.
    # "Contado", "Factoraje Financiero"); el RPA marca su checkbox "¿Es ...?".
    # Vacío = Ingreso Diverso normal (ningún check).
    tipos_movimiento: list = field(default_factory=list)
    # Interés de factoraje (BAJA FERRIES) tomado del PDF NAFIN/BBVA y cruzado por
    # folio/monto neto. Se captura en SIPP como "Interés Factoraje".
    factoraje_interes: Optional[float] = None
    factoraje_folio_pdf: Optional[str] = None
    # BBVA: True si el movimiento proviene del archivo de "movimientos externos"
    # (SPEI de otros bancos). Estos son cobros reales a subir a SIPP, así que NO
    # se les aplica la exclusión de portal de clientes (CI/CE) del archivo interno.
    bbva_externo: bool = False

    @property
    def identificado(self) -> bool:
        return self.cliente_match is not None
