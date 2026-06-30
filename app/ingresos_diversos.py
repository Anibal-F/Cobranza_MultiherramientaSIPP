from typing import Callable, Optional

from rpa.automation import RPAAutomation

from .empresas import EMPRESA_DEFAULT, Empresa
from .estado_cuenta import sugerir_sucursal
from .models import Movimiento
from .pagos_contado import PagoContadoExtraido


def _rpa(usuario, password, empresa: Empresa, headless, log_fn) -> RPAAutomation:
    return RPAAutomation(
        usuario,
        password,
        headless=headless,
        log_fn=log_fn,
        empresa_sipp=empresa.sipp_empresa,
        sucursal_sipp=empresa.sipp_sucursal,
    )


async def cargar_ingresos_diversos_en_sipp(
    movimientos: list[Movimiento],
    cuenta_bancaria_nombre: str,
    fecha_operacion_ddmmyyyy: str,
    ruta_csv: str,
    usuario: str,
    password: str,
    estado_cuenta=None,
    empresa: Empresa = EMPRESA_DEFAULT,
    headless: bool = False,
    log_fn: Callable = print,
) -> int:
    """Sube ruta_csv a "Ingresos Diversos - Agregar" en SIPP y asigna, en el
    modal de previsualización, el cliente ya identificado en la app a cada
    movimiento. Si se da `estado_cuenta` (reporte de SIPP parseado), sugiere
    además la sucursal por cada movimiento (cliente + monto). Regresa cuántos
    movimientos identificados se enviaron a intentar emparejar. No guarda: deja
    el browser abierto para revisión manual del usuario."""
    candidatos = []
    for m in movimientos:
        if not m.identificado:
            continue
        # La sucursal declarada por el usuario (override) se FUERZA (gana incluso
        # sobre la auto-sugerida de SIPP). La sugerida del estado de cuenta solo
        # rellena las que SIPP deja vacías.
        sucursal = getattr(m, "sucursal_declarada", None)
        es_declarada = bool(sucursal)
        if not sucursal and estado_cuenta is not None:
            res = sugerir_sucursal(estado_cuenta, m.cliente_match, m.abono, empresa.nombre_reporte)
            if res:
                sucursal = res[0]
        candidatos.append((m.referencia, m.abono, m.cliente_match, sucursal, es_declarada))

    automatizacion = _rpa(usuario, password, empresa, headless, log_fn)
    await automatizacion.cargar_ingresos_diversos(
        candidatos, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy, ruta_csv
    )
    return len(candidatos)


async def cargar_pagos_contado_en_sipp(
    pagos: list[PagoContadoExtraido],
    fecha_operacion_ddmmyyyy: str,
    usuario: str,
    password: str,
    empresa: Empresa = EMPRESA_DEFAULT,
    headless: bool = False,
    log_fn: Callable = print,
    enviar_automaticamente: bool = False,
) -> int:
    """Agrega, vía el modal "Agregar Movimientos" de SIPP, cada pago de
    contado ya confirmado (con cliente, plaza y monto) en `pagos`. Agrupa los
    pagos por su cuenta bancaria destino (cada `pago.cuenta_bancaria` es un
    id_sipp de la empresa) y deja que el RPA arme una conciliación por cuenta.
    Regresa cuántos se enviaron a intentar agregar."""
    nombre_cuenta_por_id = {c.id_sipp: c.nombre for c in empresa.cuentas}
    # Agrupar por cuenta destino, preservando el orden de aparición.
    grupos: dict[str, list[tuple]] = {}
    for pago in pagos:
        nombre_cuenta = nombre_cuenta_por_id.get(pago.cuenta_bancaria, "")
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

    automatizacion = _rpa(usuario, password, empresa, headless, log_fn)
    await automatizacion.cargar_pagos_contado(
        grupos_lista, fecha_operacion_ddmmyyyy, enviar_automaticamente
    )
    return total
