from typing import Callable

from rpa.automation import RPAAutomation

from .cuentas_bancarias import CUENTAS_BANCARIAS
from .models import Movimiento
from .pagos_contado import PagoContadoExtraido


async def cargar_ingresos_diversos_en_sipp(
    movimientos: list[Movimiento],
    cuenta_bancaria_nombre: str,
    fecha_operacion_ddmmyyyy: str,
    ruta_csv: str,
    usuario: str,
    password: str,
    headless: bool = False,
    log_fn: Callable = print,
) -> int:
    """Sube ruta_csv a "Ingresos Diversos - Agregar" en SIPP y asigna, en el
    modal de previsualización, el cliente ya identificado en la app a cada
    movimiento. Regresa cuántos movimientos identificados se enviaron a
    intentar emparejar. No guarda: deja el browser abierto para revisión
    manual del usuario."""
    candidatos = [(m.referencia, m.abono, m.cliente_match) for m in movimientos if m.identificado]

    automatizacion = RPAAutomation(usuario, password, headless=headless, log_fn=log_fn)
    await automatizacion.cargar_ingresos_diversos(
        candidatos, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy, ruta_csv
    )
    return len(candidatos)


_NOMBRE_CUENTA_POR_ID = {c.id_sipp: c.nombre for c in CUENTAS_BANCARIAS}


async def cargar_pagos_contado_en_sipp(
    pagos: list[PagoContadoExtraido],
    fecha_operacion_ddmmyyyy: str,
    usuario: str,
    password: str,
    headless: bool = False,
    log_fn: Callable = print,
    enviar_automaticamente: bool = False,
) -> int:
    """Agrega, vía el modal "Agregar Movimientos" de SIPP, cada pago de
    contado ya confirmado (con cliente, plaza y monto) en `pagos`. Agrupa los
    pagos por su cuenta bancaria destino (cada `pago.cuenta_bancaria` es un
    id_sipp) y deja que el RPA arme una conciliación por cuenta. Regresa
    cuántos se enviaron a intentar agregar."""
    # Agrupar por cuenta destino, preservando el orden de aparición.
    grupos: dict[str, list[tuple]] = {}
    for pago in pagos:
        nombre_cuenta = _NOMBRE_CUENTA_POR_ID.get(pago.cuenta_bancaria, "")
        grupos.setdefault(nombre_cuenta, []).append(
            (
                pago.concepto,
                pago.referencia,
                pago.tipo_movimiento,
                pago.cliente_match,
                pago.plaza,
                pago.monto,
                pago.ruta_adjunto,
            )
        )

    grupos_lista = [(nombre, datos) for nombre, datos in grupos.items()]
    total = sum(len(datos) for _, datos in grupos_lista)

    automatizacion = RPAAutomation(usuario, password, headless=headless, log_fn=log_fn)
    await automatizacion.cargar_pagos_contado(
        grupos_lista, fecha_operacion_ddmmyyyy, enviar_automaticamente
    )
    return total
