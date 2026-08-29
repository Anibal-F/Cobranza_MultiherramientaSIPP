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

Las leyendas que identifican una devolución de cheque son CONFIGURABLES (los
usuarios aún no dan las exactas): se editan desde la UI y se guardan en un JSON
(ver leyendas_cheque.py) sin tocar esta lógica.
"""

from collections import defaultdict
from datetime import date

from ..textutils import normalizar
from .leyendas_cheque import cargar_leyendas, es_devolucion
from .modelo import MovimientoConciliacion, ResultadoConciliacion


# Largo mínimo de la referencia del banco para buscarla DENTRO del texto del
# sistema (2ª pasada del emparejamiento). Con menos caracteres un número suelto
# puede aparecer por casualidad en un texto largo y conciliar lo que no es.
LONGITUD_MINIMA_AGUJA_BANCO = 6


def es_devolucion_cheque(m: MovimientoConciliacion, leyendas: list[str]) -> bool:
    return es_devolucion(m.texto, leyendas)


def _rango(movimientos: list[MovimientoConciliacion]) -> tuple[date, date] | None:
    """Primer y último día con datos de un lado; None si ninguno trae fecha."""
    fechas = [m.fecha for m in movimientos if m.fecha]
    return (min(fechas), max(fechas)) if fechas else None


def _ventana_comun(
    mov_banco: list[MovimientoConciliacion],
    mov_sistema: list[MovimientoConciliacion],
) -> tuple[date, date] | None:
    """Rango de fechas presente en AMBOS archivos: [max(mínimos), min(máximos)].

    Los archivos del banco a veces cubren un rango mayor o menor que el reporte de
    Ingresos Diversos; solo tiene sentido conciliar el tramo que ambos comparten.
    Devuelve None si algún lado no tiene ninguna fecha (no se puede acotar → no se
    filtra). Si los rangos no se traslapan, inicio > fin y todo queda fuera."""
    fechas_banco = [m.fecha for m in mov_banco if m.fecha]
    fechas_sistema = [m.fecha for m in mov_sistema if m.fecha]
    if not fechas_banco or not fechas_sistema:
        return None
    return (max(min(fechas_banco), min(fechas_sistema)),
            min(max(fechas_banco), max(fechas_sistema)))


def conciliar(
    mov_banco: list[MovimientoConciliacion],
    mov_sistema: list[MovimientoConciliacion],
    leyendas: list[str] | None = None,
) -> ResultadoConciliacion:
    """Concilia las dos listas y devuelve los grupos del requerimiento.

    `leyendas` son las leyendas de devolución de cheque; si es None se cargan del
    JSON configurable (leyendas_cheque.cargar_leyendas)."""
    if leyendas is None:
        leyendas = cargar_leyendas()
    # Rango propio de cada lado, ANTES de filtrar: más abajo `mov_banco` y
    # `mov_sistema` se reasignan con lo que cae dentro de la ventana, y si los
    # archivos no se traslapan quedarían vacíos.
    rango_banco = _rango(mov_banco)
    rango_sistema = _rango(mov_sistema)
    # 0. Acotar por la ventana común de fechas: los movimientos (de cualquier lado)
    #    cuya fecha caiga fuera se apartan y NO se consideran para conciliar ni para
    #    detectar duplicados. Las fechas nulas no se pueden ubicar → se conservan.
    ventana = _ventana_comun(mov_banco, mov_sistema)
    fuera_de_rango: list[MovimientoConciliacion] = []
    if ventana is not None:
        inicio, fin = ventana

        def _dentro(m: MovimientoConciliacion) -> bool:
            return m.fecha is None or inicio <= m.fecha <= fin

        fuera_de_rango = [m for m in mov_banco + mov_sistema if not _dentro(m)]
        mov_banco = [m for m in mov_banco if _dentro(m)]
        mov_sistema = [m for m in mov_sistema if _dentro(m)]

    # 1. Apartar devoluciones de cheque del lado banco (antes de comparar).
    devoluciones = [m for m in mov_banco if es_devolucion_cheque(m, leyendas)]
    banco = [m for m in mov_banco if not es_devolucion_cheque(m, leyendas)]

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
        candidatos = [
            (s, ar, ac)
            for s, ar, ac in por_importe.get(round(b.importe, 2), [])
            if id(s) not in consumidos
        ]
        elegido = None
        # 1ª pasada: alguna aguja del SISTEMA aparece en el texto del banco.
        for s, aguja_ref, aguja_con in candidatos:
            if any(a and (a in texto_banco[0] or a in texto_banco[1]) for a in (aguja_ref, aguja_con)):
                elegido = s
                break
        # 2ª pasada (solo si la anterior no encontró nada): al revés, la referencia
        # del BANCO aparece dentro del texto del sistema. Hace falta porque SIPP
        # guarda la referencia concatenada con más datos y entonces no cabe dentro
        # del texto del banco: p. ej. banco '0034131073' contra sistema
        # 'PAGO CUENTA DE TERCERO / 0034131073 BNET 0476697690'.
        # Se exige una aguja larga (LONGITUD_MINIMA_AGUJA_BANCO) para que un número
        # corto no coincida por casualidad dentro de un texto largo.
        if elegido is None:
            for s, aguja_ref, aguja_con in candidatos:
                if any(
                    a and len(a) >= LONGITUD_MINIMA_AGUJA_BANCO
                    and (a in aguja_ref or a in aguja_con)
                    for a in texto_banco
                ):
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
        fuera_de_rango=fuera_de_rango,
        ventana=ventana,
        rango_banco=rango_banco,
        rango_sistema=rango_sistema,
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
