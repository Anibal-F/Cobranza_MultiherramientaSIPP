"""Motor de conciliación: compara movimientos del banco vs. del sistema.

Flujo: (1) apartar devoluciones de cheque del lado banco; (2) emparejar; (3)
clasificar el resto en solo-banco y solo-sistema.

Regla de emparejamiento (igual para TODOS los bancos): un movimiento del banco
concilia con uno del sistema cuando el IMPORTE coincide y ALGUNO de los textos del
sistema (su REFERENCIA o su CONCEPTO/DESCRIPCIÓN) aparece (normalizado) dentro del
CONCEPTO/DESCRIPCIÓN o la REFERENCIA del banco. Es decir: importe igual + (check por
referencia O check por concepto). El reporte de Ingresos Diversos solo trae
referencia (y razón social); la tabla en la nube trae de_Referencia y de_Concepto,
y ambos se usan como texto a buscar. Cada banco/lado aporta sus columnas al construir
el MovimientoConciliacion; aquí solo se comparan.

La leyenda que identifica una devolución de cheque es CONFIGURABLE (los usuarios
aún no dan la exacta): se ajusta LEYENDA_DEVOLUCION_CHEQUE sin tocar la lógica.
"""

import re
from collections import defaultdict

from ..textutils import normalizar
from .modelo import MovimientoConciliacion, ResultadoConciliacion

# Leyenda (regex) que marca un movimiento como devolución de cheque. Default:
# cualquier variante que contenga "CHEQUE"/"CHEQUES". Ajustar cuando los usuarios
# proporcionen la leyenda exacta (p. ej. r"DEV\.?\s*CHEQUE").
LEYENDA_DEVOLUCION_CHEQUE = re.compile(r"CHEQUE", re.IGNORECASE)


def es_devolucion_cheque(m: MovimientoConciliacion) -> bool:
    return bool(LEYENDA_DEVOLUCION_CHEQUE.search(m.texto))


def conciliar(
    mov_banco: list[MovimientoConciliacion],
    mov_sistema: list[MovimientoConciliacion],
) -> ResultadoConciliacion:
    """Concilia las dos listas y devuelve los 4 grupos del requerimiento."""
    # 1. Apartar devoluciones de cheque del lado banco (antes de comparar).
    devoluciones = [m for m in mov_banco if es_devolucion_cheque(m)]
    banco = [m for m in mov_banco if not es_devolucion_cheque(m)]

    # 2. Agrupar el sistema por importe (2 decimales) para acotar la búsqueda;
    #    cada movimiento del sistema se consume una sola vez. Se guardan sus dos
    #    textos normalizados: referencia y concepto/descripción (agujas a buscar).
    por_importe: dict[float, list] = defaultdict(list)
    for s in mov_sistema:
        por_importe[round(s.importe, 2)].append(
            (s, normalizar(s.referencia), normalizar(s.descripcion))
        )

    conciliados: list[tuple[MovimientoConciliacion, MovimientoConciliacion]] = []
    solo_banco: list[MovimientoConciliacion] = []
    consumidos: set[int] = set()
    for b in banco:
        # Texto del banco donde se busca: su concepto/descripción y su referencia.
        texto_banco = (normalizar(b.descripcion), normalizar(b.referencia))
        elegido = None
        for s, aguja_ref, aguja_con in por_importe.get(round(b.importe, 2), []):
            if id(s) in consumidos:
                continue
            # Alguna aguja del sistema (referencia o concepto) aparece en el banco.
            if any(a and (a in texto_banco[0] or a in texto_banco[1]) for a in (aguja_ref, aguja_con)):
                elegido = s
                break
        if elegido is not None:
            consumidos.add(id(elegido))
            conciliados.append((b, elegido))
        else:
            solo_banco.append(b)

    # 3. Lo que quedó sin consumir en el sistema -> solo sistema.
    solo_sistema = [s for s in mov_sistema if id(s) not in consumidos]

    return ResultadoConciliacion(
        conciliados=conciliados,
        solo_banco=solo_banco,
        solo_sistema=solo_sistema,
        devoluciones_cheque=devoluciones,
        posibles_repetidos_sistema=_posibles_repetidos(mov_sistema),
    )


def _posibles_repetidos(sistema: list[MovimientoConciliacion]) -> list[MovimientoConciliacion]:
    """Movimientos del sistema que se repiten entre sí: misma referencia,
    descripción, importe Y misma fecha (mismo día). Devuelve TODOS los miembros de
    cada grupo con 2+ movimientos (para revisarlos como posibles duplicados)."""
    grupos: dict[tuple, list[MovimientoConciliacion]] = defaultdict(list)
    for s in sistema:
        clave = (normalizar(s.referencia), normalizar(s.descripcion), round(s.importe, 2), s.fecha)
        grupos[clave].append(s)
    repetidos: list[MovimientoConciliacion] = []
    for miembros in grupos.values():
        if len(miembros) >= 2:
            repetidos.extend(miembros)
    return repetidos
