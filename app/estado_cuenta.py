"""Parsea el reporte "Estado de Cuenta" de SIPP (.xlsx) y, a partir del cliente
y el monto del abono, SUGIERE la sucursal a la que aplica.

El reporte es jerárquico:
  - fila encabezado de cliente: col A = nombre del cliente, col B vacía.
  - filas de detalle (una por factura pendiente): col B = sucursal, col C = folio,
    col P = saldo pendiente. La col A es la empresa (no siempre "Abastecedora").
  - filas "Total Sucursal/Empresa/Cliente": col A empieza con "Total" → se ignoran.

El monto NO es llave dura (un abono puede exceder una factura y el resto irse como
anticipo), así que la sucursal es solo una SUGERENCIA: se pre-marca y el usuario la
corrige en SIPP si hace falta.
"""

from dataclasses import dataclass, field
from typing import Optional

from .textutils import normalizar

# Índice de columnas (0-based) en el reporte.
_COL_EMPRESA = 0      # A
_COL_SUCURSAL = 1     # B
_COL_FOLIO = 2        # C
_COL_SALDO = 15       # P (Saldo Pendiente)

# Tolerancia en pesos para considerar que un monto "coincide".
_TOLERANCIA = 1.0


@dataclass
class EstadoCuenta:
    # cliente_normalizado -> { sucursal -> [(folio, saldo), ...] }
    por_cliente: dict[str, dict[str, list[tuple[str, float]]]] = field(default_factory=dict)
    # cliente_normalizado -> nombre original (para mostrar)
    nombre_original: dict[str, str] = field(default_factory=dict)

    @property
    def num_clientes(self) -> int:
        return len(self.por_cliente)


def cargar_estado_cuenta(ruta: str) -> EstadoCuenta:
    """Carga el .xlsx del estado de cuenta a un índice cliente→sucursal→facturas."""
    import openpyxl  # import diferido: dependencia solo de este flujo

    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    estado = EstadoCuenta()
    cliente_norm: Optional[str] = None
    try:
        for fila in ws.iter_rows(min_row=6, values_only=True):
            col_a = fila[_COL_EMPRESA]
            sucursal = fila[_COL_SUCURSAL]
            if isinstance(col_a, str) and col_a.strip().startswith("Total"):
                continue
            if sucursal not in (None, ""):
                # Fila de detalle (factura).
                if cliente_norm is None:
                    continue
                folio = fila[_COL_FOLIO]
                saldo = fila[_COL_SALDO]
                try:
                    saldo_f = float(saldo) if saldo not in (None, "") else 0.0
                except (TypeError, ValueError):
                    saldo_f = 0.0
                suc = str(sucursal).strip()
                estado.por_cliente[cliente_norm].setdefault(suc, []).append(
                    (str(folio or "").strip(), saldo_f)
                )
            elif isinstance(col_a, str) and col_a.strip():
                # Encabezado de cliente.
                nombre = col_a.strip()
                cliente_norm = normalizar(nombre)
                estado.por_cliente.setdefault(cliente_norm, {})
                estado.nombre_original.setdefault(cliente_norm, nombre)
    finally:
        wb.close()

    # Limpiamos clientes sin facturas (encabezados sin detalle).
    estado.por_cliente = {k: v for k, v in estado.por_cliente.items() if v}
    return estado


def _resolver_cliente(estado: EstadoCuenta, cliente: str) -> Optional[str]:
    """Encuentra la clave del cliente en el reporte: exacto normalizado, o por
    contención única (los nombres varían un poco entre catálogo y reporte)."""
    if not cliente:
        return None
    objetivo = normalizar(cliente)
    if objetivo in estado.por_cliente:
        return objetivo
    candidatos = [
        k for k in estado.por_cliente
        if k and (k in objetivo or objetivo in k)
    ]
    if len(candidatos) == 1:
        return candidatos[0]
    return None


def _existe_subconjunto(saldos: list[float], objetivo: float, tol: float, max_n: int = 3) -> bool:
    """¿Hay un subconjunto de hasta max_n facturas que sume ~objetivo?"""
    from itertools import combinations

    for n in range(1, min(max_n, len(saldos)) + 1):
        for combo in combinations(saldos, n):
            if abs(sum(combo) - objetivo) <= tol:
                return True
    return False


def sugerir_sucursal(
    estado: EstadoCuenta, cliente: str, abono: Optional[float]
) -> Optional[tuple[str, str]]:
    """Regresa (sucursal, motivo) sugerida para (cliente, abono), o None si no
    se puede sugerir (cliente no está en el reporte). motivo es informativo:
    'única' | 'factura exacta' | 'suma de facturas' | 'aproximado'."""
    clave = _resolver_cliente(estado, cliente)
    if clave is None:
        return None
    sucursales = estado.por_cliente[clave]
    if not sucursales:
        return None

    # Caso fácil (85%): el cliente solo opera en una sucursal.
    if len(sucursales) == 1:
        return (next(iter(sucursales)), "única")

    if abono is None:
        return None

    # 1) Una factura cuyo saldo coincide con el abono.
    for suc, facturas in sucursales.items():
        if any(abs(saldo - abono) <= _TOLERANCIA for _folio, saldo in facturas):
            return (suc, "factura exacta")

    # 2) Suma de facturas (misma sucursal) que coincide con el abono.
    for suc, facturas in sucursales.items():
        saldos = [saldo for _folio, saldo in facturas]
        if _existe_subconjunto(saldos, abono, _TOLERANCIA):
            return (suc, "suma de facturas")

    # 3) Aproximado: la sucursal con la factura individual más cercana al abono.
    mejor_suc = None
    mejor_dif = None
    for suc, facturas in sucursales.items():
        for _folio, saldo in facturas:
            dif = abs(saldo - abono)
            if mejor_dif is None or dif < mejor_dif:
                mejor_dif = dif
                mejor_suc = suc
    if mejor_suc is not None:
        return (mejor_suc, "aproximado")
    return None
