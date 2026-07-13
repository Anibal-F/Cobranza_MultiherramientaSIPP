import os
import tempfile
from typing import Callable, Optional

from rpa.automation import RPAAutomation

from .empresas import EMPRESA_DEFAULT, Empresa
from .estado_cuenta import sugerir_sucursal
from .models import Movimiento
from .pagos_contado import PagoContadoExtraido

# Bancos cuyo estado de cuenta SIPP no acepta por "Subir Excel": se capturan por
# el modal "Agregar Movimientos" (el '+').
_BANCOS_CAPTURA_MANUAL = {"BANBAJIO"}


def _rpa(usuario, password, empresa: Empresa, headless, log_fn, contador_fn=None) -> RPAAutomation:
    return RPAAutomation(
        usuario,
        password,
        headless=headless,
        log_fn=log_fn,
        empresa_sipp=empresa.sipp_empresa,
        sucursal_sipp=empresa.sipp_sucursal,
        contador_fn=contador_fn,
    )


async def aplicar_factoraje_en_sipp(
    folio_conciliacion: str,
    institucion_value: str,
    items: list[dict],
    usuario: str,
    password: str,
    empresa: Empresa = EMPRESA_DEFAULT,
    headless: bool = False,
    log_fn: Callable = print,
) -> int:
    """Abre la conciliación indicada en SIPP y captura el interés de factoraje
    (BAJA FERRIES) en cada movimiento que empate con un renglón del PDF."""
    automatizacion = _rpa(usuario, password, empresa, headless, log_fn)
    return await automatizacion.aplicar_factoraje(folio_conciliacion, institucion_value, items)


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
    sucursal_resolver: Optional[Callable] = None,
    contador_fn: Optional[Callable] = None,
    rpa_out: Optional[list] = None,
) -> int:
    """Sube ruta_csv a "Ingresos Diversos - Agregar" en SIPP y asigna, en el
    modal de previsualización, el cliente ya identificado en la app a cada
    movimiento. Si se da `estado_cuenta` (reporte de SIPP parseado), sugiere
    además la sucursal por cada movimiento (cliente + monto). Regresa cuántos
    movimientos identificados se enviaron a intentar emparejar. No guarda: deja
    el browser abierto para revisión manual del usuario."""
    # Bancos que SIPP NO importa por "Subir Excel" (ej. BanBajío) se capturan por
    # el modal "Agregar Movimientos" (el '+'), movimiento por movimiento.
    banco = (movimientos[0].banco if movimientos else "").upper()
    captura_manual = banco in _BANCOS_CAPTURA_MANUAL
    es_bbva = banco == "BBVA"

    candidatos = []
    manuales = []
    candidatos_bbva = []  # (ref, abono, cliente, sucursal, forzar, tipos, concepto)
    for m in movimientos:
        # Excluido manualmente (traspaso a filiales, etc.): ni se sube ni se
        # identifica. Su fila también se quita del CSV (ver ruta_csv más abajo).
        if getattr(m, "excluido", False):
            continue
        # No re-subir lo que ya venía en una extracción previa subida a SIPP.
        if getattr(m, "ya_subido", False):
            continue
        identificado = m.identificado
        tipos = list(getattr(m, "tipos_movimiento", []) or [])

        # Prioridad de sucursal (solo aplica si hay cliente identificado):
        #   1) declarada por el usuario (override manual);
        #   2) leída de la propia factura durante la búsqueda por folio;
        #   3) sugerida por el estado de cuenta (heurística), solo rellena vacías.
        sucursal = None
        es_declarada = False
        if identificado:
            sucursal = getattr(m, "sucursal_declarada", None)
            es_declarada = bool(sucursal)
            if not sucursal and getattr(m, "sucursal_por_folio", None):
                sucursal = m.sucursal_por_folio
                es_declarada = True  # fuente confiable: forzar
            if not sucursal and estado_cuenta is not None:
                res = sugerir_sucursal(estado_cuenta, m.cliente_match, m.abono, empresa.nombre_reporte)
                if res:
                    sucursal = res[0]
        # Sucursal WYSIWYG (la que muestra el grid) si se dio el resolver.
        sucursal_manual = (
            sucursal_resolver(m) if (identificado and sucursal_resolver is not None) else sucursal
        )

        # Los flujos por archivo/H2H (candidatos) solo procesan identificados: en la
        # previsualización solo se les puede asignar cliente si ya lo tenemos.
        if identificado:
            candidatos.append((m.referencia, m.abono, m.cliente_match, sucursal, es_declarada, tipos))
            candidatos_bbva.append(
                (m.referencia, m.abono, m.cliente_match, sucursal_manual, True, tipos, m.descripcion or "")
            )

        # Captura manual (BanBajío): se agregan TODOS los movimientos, con o SIN
        # cliente. Sin cliente se captura solo el importe (SIPP lo permite) y el
        # usuario lo identifica después en SIPP. Tupla:
        # (concepto, referencia, monto, cliente|None, sucursal|None, tipos).
        manuales.append(
            (
                m.descripcion or "",
                m.referencia,
                m.abono,
                m.cliente_match if identificado else None,
                sucursal_manual if identificado else None,
                tipos,
            )
        )

    # Movimientos que NO deben llegar a SIPP, por dos motivos distintos:
    #   - excluido:  traspasos a filiales / portal BBVA (no son cobranza).
    #   - ya_subido: ya venían en un corte anterior YA subido; re-subirlos duplica.
    # Antes solo se saltaban de `candidatos` (no se les asignaba cliente), pero el
    # archivo/buzón sí los cargaba a SIPP. Ahora además se quitan del CSV y, si aun
    # así aparecen en la previsualización (siempre en BBVA H2H, que no sube archivo),
    # se eliminan ahí con el botón "Eliminar Movimiento".
    omitidos = [
        m
        for m in movimientos
        if getattr(m, "excluido", False) or getattr(m, "ya_subido", False)
    ]
    a_eliminar = [(m.referencia, m.abono) for m in omitidos]

    automatizacion = _rpa(usuario, password, empresa, headless, log_fn, contador_fn)
    # Se expone la instancia a la UI: estos flujos dejan el navegador abierto para que
    # el usuario adjunte el soporte, y el modal de confirmación ofrece cerrarlo.
    if rpa_out is not None:
        rpa_out.append(automatizacion)

    if es_bbva:
        # BBVA no tiene "Subir Excel": se usa el buzón H2H (que cae en la misma
        # previsualización) + respaldo manual. Rango de fechas = rango del .xls.
        fechas = [m.fecha for m in movimientos if getattr(m, "fecha", None)]
        if fechas:
            fecha_ini = min(fechas).strftime("%d/%m/%Y")
            fecha_fin = max(fechas).strftime("%d/%m/%Y")
        else:
            fecha_ini = fecha_fin = fecha_operacion_ddmmyyyy
        await automatizacion.cargar_ingresos_diversos_bbva_h2h(
            candidatos_bbva, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy, fecha_ini,
            fecha_fin, a_eliminar,
        )
        return len(candidatos_bbva)

    if captura_manual:
        await automatizacion.cargar_ingresos_diversos_manual(
            manuales, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy
        )
        return len(manuales)

    # Los movimientos omitidos (excluidos + ya extraídos en un corte anterior) NO
    # deben ni siquiera importarse en SIPP: se genera un CSV sin esas filas. Si no
    # hay nada que omitir, se usa el archivo original tal cual.
    ruta_a_subir = _csv_sin_omitidos(ruta_csv, omitidos, log_fn)

    try:
        await automatizacion.cargar_ingresos_diversos(
            candidatos, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy, ruta_a_subir,
            a_eliminar,
        )
    finally:
        if ruta_a_subir != ruta_csv:
            try:
                os.unlink(ruta_a_subir)
            except OSError:
                pass
    return len(candidatos)


def _csv_sin_omitidos(ruta_csv: str, omitidos: list, log_fn: Callable) -> str:
    """Devuelve la ruta de un CSV temporal igual al original pero SIN las filas de
    los movimientos omitidos —excluidos (traspasos) y ya extraídos en un corte
    anterior—, que se identifican por su referencia dentro de la línea. Si no hay
    nada que omitir o no se pudo escribir, devuelve la ruta original.

    Lo que no se pueda quitar aquí (sin referencia utilizable, o archivo no-CSV) se
    elimina después en el modal de previsualización (ver `a_eliminar`)."""
    referencias = {
        (getattr(m, "referencia", "") or "").strip()
        for m in omitidos
        if len((getattr(m, "referencia", "") or "").strip()) >= 4
    }
    if not referencias:
        if omitidos:
            log_fn(
                f"Aviso: {len(omitidos)} movimiento(s) a omitir sin referencia utilizable; "
                "no se pudieron quitar del CSV (se intentarán eliminar en la previsualización).",
                "warn",
            )
        return ruta_csv
    # Solo se puede filtrar por líneas de texto un .csv. Los .xlsx (BanBajío) son
    # binarios: no se reescriben aquí (se eliminan en la previsualización).
    if not ruta_csv.lower().endswith(".csv"):
        log_fn(
            "Aviso: el archivo no es .csv; los movimientos omitidos no se quitan del "
            "archivo subido (se intentarán eliminar en la previsualización).",
            "warn",
        )
        return ruta_csv
    try:
        with open(ruta_csv, encoding="utf-8-sig", newline="") as f:
            lineas = f.readlines()
        conservadas, quitadas = [], 0
        for i, linea in enumerate(lineas):
            # La primera línea suele ser encabezado; nunca se descarta.
            if i > 0 and any(ref in linea for ref in referencias):
                quitadas += 1
                continue
            conservadas.append(linea)
        if quitadas == 0:
            return ruta_csv
        fd, ruta_tmp = tempfile.mkstemp(suffix=".csv", prefix="mh_sin_omitidos_")
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            f.writelines(conservadas)
        log_fn(
            f"CSV filtrado: se quitaron {quitadas} fila(s) omitida(s) —excluidas / ya "
            "extraídas— antes de subir a SIPP.",
            "info",
        )
        return ruta_tmp
    except OSError as ex:
        log_fn(f"No se pudo generar el CSV filtrado ({ex}); se sube el original.", "warn")
        return ruta_csv


async def cargar_pagos_contado_en_sipp(
    pagos: list[PagoContadoExtraido],
    fecha_operacion_ddmmyyyy: str,
    usuario: str,
    password: str,
    empresa: Empresa = EMPRESA_DEFAULT,
    headless: bool = False,
    log_fn: Callable = print,
    enviar_automaticamente: bool = False,
    contador_fn: Optional[Callable] = None,
) -> int:
    """Agrega, vía el modal "Agregar Movimientos" de SIPP, cada pago de
    contado ya confirmado (con cliente, plaza y monto) en `pagos`. Agrupa los
    pagos por su cuenta bancaria destino (cada `pago.cuenta_bancaria` es un
    id_sipp de la empresa) y deja que el RPA arme una conciliación por cuenta.
    Regresa cuántos se enviaron a intentar agregar."""
    nombre_cuenta_por_id = {c.id_sipp: c.nombre for c in empresa.cuentas}
    # Agrupar por cuenta destino, preservando el orden de aparición. Se guarda
    # además la fecha MÍNIMA de los pagos de cada cuenta (fecha del correo): la
    # verificación de duplicados busca desde esa fecha (el pago pudo subirse en un
    # día de operación distinto al de hoy) hasta la fecha de operación.
    grupos: dict[str, list[tuple]] = {}
    grupos_fecha_min: dict[str, "date"] = {}
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
        if pago.correo and pago.correo.fecha:
            f = pago.correo.fecha.date()
            actual = grupos_fecha_min.get(nombre_cuenta)
            if actual is None or f < actual:
                grupos_fecha_min[nombre_cuenta] = f

    grupos_lista = [
        (
            nombre,
            datos,
            grupos_fecha_min[nombre].strftime("%d/%m/%Y") if nombre in grupos_fecha_min else None,
        )
        for nombre, datos in grupos.items()
    ]
    total = sum(len(datos) for _, datos, _ in grupos_lista)

    automatizacion = _rpa(usuario, password, empresa, headless, log_fn, contador_fn)
    duplicados = await automatizacion.cargar_pagos_contado(
        grupos_lista, fecha_operacion_ddmmyyyy, enviar_automaticamente
    )
    return total, (duplicados or [])
