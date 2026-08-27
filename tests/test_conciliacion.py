"""Pruebas del módulo de conciliación bancaria.

Comparan un estado de cuenta de BBVA contra el reporte de "Ingresos Diversos" de
SIPP, ambos del 21/08/2026. Los archivos NO están en el repositorio (traen datos
financieros); se buscan en la carpeta de ejemplos y las pruebas se saltan si no
están.

Lo que se verifica es sobre todo que **no se pierda ni se invente nada**: todo
movimiento del banco cae en exactamente un grupo, y ningún movimiento del sistema
se usa dos veces.
"""

import os

import pytest

from app.conciliacion.conciliador import LONGITUD_MINIMA_AGUJA_BANCO, conciliar
from app.conciliacion.ingresos_diversos import cargar_ingresos_diversos
from app.conciliacion.lector_banco import normalizar_banco
from app.conciliacion.modelo import MovimientoConciliacion

EJEMPLOS = os.getenv("MH_EJEMPLOS_DIR", os.path.expanduser("~/Downloads/MH_Ejemplo"))
CORTE = os.path.join(EJEMPLOS, "BBVA", "Corte_2.xls")
REPORTE = os.path.join(EJEMPLOS, "ReporteIngresosDiversos27_8_2026 .xlsx")
CUENTA = "0100647012"   # la cuenta del corte, para comparar manzanas con manzanas

pytestmark = pytest.mark.skipif(
    not (os.path.exists(CORTE) and os.path.exists(REPORTE)),
    reason=f"Faltan los archivos de ejemplo en {EJEMPLOS}",
)


@pytest.fixture(scope="module")
def lados():
    """(movimientos del banco, movimientos del sistema de esa misma cuenta)."""
    banco_detectado, mov_banco, msg = normalizar_banco(CORTE)
    assert banco_detectado == "BBVA" and msg == "ok", f"no se pudo leer el corte: {msg}"
    sistema = [
        m for m in cargar_ingresos_diversos(REPORTE)
        if CUENTA in ((m.raw or {}).get("CUENTA_BANCARIA") or "")
    ]
    return mov_banco, sistema


@pytest.fixture(scope="module")
def resultado(lados):
    return conciliar(*lados)


# ──────────────────────────────────────────────────────────
# Lectura de ambos lados
# ──────────────────────────────────────────────────────────


def test_lee_los_dos_archivos(lados):
    banco, sistema = lados
    assert len(banco) == 172, f"el corte trae {len(banco)} movimientos"
    assert len(sistema) == 324, f"el reporte trae {len(sistema)} de la cuenta {CUENTA}"


def test_el_reporte_trae_banco_y_cuenta_en_cada_movimiento(lados):
    """La UI los usa para que el usuario no confunda cuentas distintas."""
    _, sistema = lados
    sin_datos = [m for m in sistema if not (m.raw or {}).get("BANCO")]
    assert not sin_datos, f"{len(sin_datos)} movimiento(s) del reporte sin banco"


# ──────────────────────────────────────────────────────────
# Que no se pierda ni se invente nada
# ──────────────────────────────────────────────────────────


def test_cada_movimiento_del_banco_cae_en_un_solo_grupo(lados, resultado):
    """La suma de los grupos debe dar exactamente el total del banco."""
    banco, _ = lados
    suma = (
        len(resultado.conciliados)
        + len(resultado.solo_banco)
        + len(resultado.devoluciones_cheque)
        + len([m for m in resultado.fuera_de_rango if m.origen == "banco"])
    )
    assert suma == len(banco), (
        f"los grupos suman {suma} pero el banco trae {len(banco)}: "
        "hay movimientos perdidos o duplicados"
    )


def test_cada_movimiento_del_sistema_se_usa_una_sola_vez(resultado):
    usados = [id(s) for _, s in resultado.conciliados]
    assert len(usados) == len(set(usados)), (
        "un mismo movimiento del sistema se concilió con dos del banco"
    )


def test_lo_conciliado_coincide_en_importe(resultado):
    """Nunca se cruzan dos movimientos de importe distinto."""
    for b, s in resultado.conciliados:
        assert round(b.importe, 2) == round(s.importe, 2), (
            f"conciliados con importes distintos: {b.importe} vs {s.importe}"
        )


def test_la_ventana_de_fechas_es_la_del_dia(resultado):
    import datetime

    assert resultado.ventana == (datetime.date(2026, 8, 21), datetime.date(2026, 8, 21))
    assert not resultado.fuera_de_rango


# ──────────────────────────────────────────────────────────
# Cuánto concilia
# ──────────────────────────────────────────────────────────


def test_concilia_la_mayor_parte_del_corte(resultado, lados):
    """Regresión sobre el porcentaje: si baja, algo se rompió en el cruce."""
    banco, _ = lados
    porcentaje = len(resultado.conciliados) / len(banco)
    assert porcentaje >= 0.88, (
        f"solo se concilió el {porcentaje:.0%} del corte "
        f"({len(resultado.conciliados)}/{len(banco)})"
    )


def test_encuentra_los_pagos_con_referencia_concatenada(resultado):
    """Los que SIPP guarda como 'PAGO CUENTA DE TERCERO / <referencia> ...'.

    La referencia del sistema es más larga que la del banco, así que no cabe
    dentro del texto bancario: solo se encuentran buscando en sentido inverso.
    """
    importes = {round(b.importe, 2) for b, _ in resultado.conciliados}
    for esperado in (132300.00, 6725.00, 14795.00, 136100.00):
        assert esperado in importes, (
            f"el movimiento de ${esperado:,.2f} debería conciliar "
            "(su referencia aparece dentro de la del sistema)"
        )


# ──────────────────────────────────────────────────────────
# La regla de emparejamiento, aislada
# ──────────────────────────────────────────────────────────


def _mov(referencia, descripcion, importe, origen):
    import datetime

    return MovimientoConciliacion(
        fecha=datetime.date(2026, 8, 21), descripcion=descripcion,
        referencia=referencia, importe=importe, origen=origen,
    )


def test_concilia_cuando_la_referencia_del_banco_esta_dentro_de_la_del_sistema():
    banco = [_mov("0034131073", "PAGO CUENTA DE TERCERO BNET", 6725.0, "banco")]
    sistema = [_mov("PAGO CUENTA DE TERCERO / 0034131073 BNET 0476697690", "", 6725.0, "sistema")]
    res = conciliar(banco, sistema, leyendas=[])
    assert len(res.conciliados) == 1


def test_concilia_cuando_la_referencia_del_sistema_esta_dentro_de_la_del_banco():
    """El caso de siempre: no debe romperse al agregar el sentido inverso."""
    banco = [_mov("REF123456789", "SPEI RECIBIDO REF123456789 ACME", 1000.0, "banco")]
    sistema = [_mov("REF123456789", "ACME SA DE CV", 1000.0, "sistema")]
    res = conciliar(banco, sistema, leyendas=[])
    assert len(res.conciliados) == 1


def test_no_concilia_con_importes_distintos():
    banco = [_mov("0034131073", "PAGO", 6725.0, "banco")]
    sistema = [_mov("PAGO CUENTA DE TERCERO / 0034131073", "", 6725.99, "sistema")]
    res = conciliar(banco, sistema, leyendas=[])
    assert not res.conciliados
    assert len(res.solo_banco) == 1


def test_una_referencia_muy_corta_no_concilia_por_casualidad():
    """Un número de pocos dígitos aparece en cualquier texto largo: con importe
    igual bastaría para cruzar movimientos ajenos, así que se exige longitud."""
    corta = "1" * (LONGITUD_MINIMA_AGUJA_BANCO - 1)
    banco = [_mov(corta, "DEPOSITO", 500.0, "banco")]
    sistema = [_mov(f"OTRA COSA {corta} 999", "CLIENTE AJENO", 500.0, "sistema")]
    res = conciliar(banco, sistema, leyendas=[])
    assert not res.conciliados, "concilió por una coincidencia de pocos dígitos"


def test_un_movimiento_del_sistema_no_se_reparte_entre_dos_del_banco():
    banco = [
        _mov("0034131073", "PAGO", 100.0, "banco"),
        _mov("0034131073", "PAGO", 100.0, "banco"),
    ]
    sistema = [_mov("PAGO / 0034131073 BNET", "", 100.0, "sistema")]
    res = conciliar(banco, sistema, leyendas=[])
    assert len(res.conciliados) == 1
    assert len(res.solo_banco) == 1
