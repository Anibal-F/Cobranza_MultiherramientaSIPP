"""Adaptador: obtiene los movimientos del banco para conciliación reutilizando el
sistema de parsers unificado (app/parsers).

Conciliación ya no tiene sus propias "estrategias": usa los mismos lectores que la
identificación bancaria y convierte cada `Movimiento` al `MovimientoConciliacion`
del módulo. Así hay una sola fuente de formatos de banco (CSV / .xls / .xlsx).
"""

from ..models import Movimiento
from ..parsers import (
    bancos_conciliacion,
    detectar_banco,
    es_banco_conciliacion,
    parsear_archivo,
)
from .modelo import MovimientoConciliacion


def _a_conciliacion(m: Movimiento) -> MovimientoConciliacion:
    return MovimientoConciliacion(
        fecha=m.fecha,
        # "descripcion" = concepto + descripción del banco (respetando sus
        # columnas): es el texto donde el conciliador hace el "check por concepto".
        # La "referencia" queda aparte para el "check por referencia".
        descripcion=" ".join(x for x in (m.concepto, m.descripcion) if x),
        referencia=m.referencia,
        importe=m.abono,
        naturaleza="A",
        saldo=m.saldo,
        origen=f"BANCO:{m.banco}",
    )


def normalizar_banco(
    path: str, banco: str | None = None
) -> tuple[str | None, list[MovimientoConciliacion], str]:
    """Detecta (o fuerza) el banco y normaliza sus movimientos.

    Devuelve (nombre, movimientos, estado):
      - estado "ok"            -> nombre y movimientos válidos.
      - estado "no_reconocido" -> no se identificó ningún banco (nombre=None).
      - estado "no_habilitado" -> se autodetectó un banco que NO está habilitado
        para conciliaciones (nombre con el banco detectado, movimientos vacíos);
        la UI debe avisar que se comuniquen para validar el formato.

    Si `banco` viene dado (el usuario lo forzó en el selector), no se aplica el
    filtro de habilitado — el selector solo ofrece bancos habilitados."""
    forzado = banco is not None
    nombre = banco or detectar_banco(path)
    if nombre is None:
        return None, [], "no_reconocido"
    if not forzado and not es_banco_conciliacion(nombre):
        return nombre, [], "no_habilitado"
    movimientos = parsear_archivo(path, nombre)
    return nombre, [_a_conciliacion(m) for m in movimientos], "ok"


def nombres_bancos() -> list[str]:
    """Bancos habilitados para el selector de conciliaciones."""
    return bancos_conciliacion()


def banco_habilitado(nombre: str) -> bool:
    return es_banco_conciliacion(nombre)
