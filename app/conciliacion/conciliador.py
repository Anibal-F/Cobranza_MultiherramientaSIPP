"""Motor de conciliación: compara movimientos del banco vs. del sistema.

Flujo: (1) apartar devoluciones de cheque del lado banco; (2) emparejar por llave
(texto normalizado + importe) manejando duplicados con un multiconjunto;
(3) clasificar el resto en solo-banco y solo-sistema.

La leyenda que identifica una devolución de cheque es CONFIGURABLE (los usuarios
aún no dan la exacta): se ajusta LEYENDA_DEVOLUCION_CHEQUE sin tocar la lógica.
"""

import re
from collections import defaultdict
from typing import Callable

from .modelo import MovimientoConciliacion, ResultadoConciliacion

# Leyenda (regex) que marca un movimiento como devolución de cheque. Default:
# cualquier variante que contenga "CHEQUE"/"CHEQUES". Ajustar cuando los usuarios
# proporcionen la leyenda exacta (p. ej. r"DEV\.?\s*CHEQUE").
LEYENDA_DEVOLUCION_CHEQUE = re.compile(r"CHEQUE", re.IGNORECASE)

# Llave de emparejamiento por defecto (decisión del usuario): texto normalizado +
# importe. Es enchufable: pasar otra `clave` a conciliar() permite cambiar el
# criterio sin tocar el motor.
ClaveFn = Callable[[MovimientoConciliacion], tuple]


def _clave_default(m: MovimientoConciliacion) -> tuple:
    return m.clave()


def es_devolucion_cheque(m: MovimientoConciliacion) -> bool:
    return bool(LEYENDA_DEVOLUCION_CHEQUE.search(m.texto))


def conciliar(
    mov_banco: list[MovimientoConciliacion],
    mov_sistema: list[MovimientoConciliacion],
    clave: ClaveFn = _clave_default,
) -> ResultadoConciliacion:
    """Concilia las dos listas y devuelve los 4 grupos del requerimiento."""
    # 1. Apartar devoluciones de cheque del lado banco (antes de comparar).
    devoluciones = [m for m in mov_banco if es_devolucion_cheque(m)]
    banco = [m for m in mov_banco if not es_devolucion_cheque(m)]

    # 2. Indexar el sistema por llave (multiconjunto: soporta importes repetidos).
    indice: dict[tuple, list[MovimientoConciliacion]] = defaultdict(list)
    for s in mov_sistema:
        indice[clave(s)].append(s)

    # 3. Recorrer el banco: si hay match disponible -> conciliado; si no -> solo banco.
    conciliados: list[tuple[MovimientoConciliacion, MovimientoConciliacion]] = []
    solo_banco: list[MovimientoConciliacion] = []
    for b in banco:
        cola = indice.get(clave(b))
        if cola:
            conciliados.append((b, cola.pop(0)))
        else:
            solo_banco.append(b)

    # 4. Lo que quedó sin consumir en el sistema -> solo sistema.
    solo_sistema = [s for cola in indice.values() for s in cola]

    return ResultadoConciliacion(
        conciliados=conciliados,
        solo_banco=solo_banco,
        solo_sistema=solo_sistema,
        devoluciones_cheque=devoluciones,
    )
