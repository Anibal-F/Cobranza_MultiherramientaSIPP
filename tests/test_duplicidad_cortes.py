"""Pruebas de duplicidad con cortes REALES de BBVA.

Los archivos del banco son acumulativos: cada corte del día trae todo lo anterior
más lo nuevo. Si el dedup falla, el usuario re-sube a SIPP movimientos ya cargados
(pagos duplicados). Estas pruebas usan dos cortes de verdad del mismo día:

    Corte_1.xls →  80 movimientos
    Corte_2.xls → 172 movimientos = los mismos 80 + 92 nuevos

Los archivos NO están en el repositorio (traen datos de clientes). Se toman de la
carpeta indicada en MH_CORTES_DIR, y si no están las pruebas se saltan.
"""

import os

import pytest

from app.historial import clave_movimiento, clave_movimiento_dict, claves_subidas
from app.parsers import detectar_banco, parsear_archivo
from app.parsers.bbva import recortar_acumulado

CARPETA = os.getenv("MH_CORTES_DIR", os.path.expanduser("~/Downloads/MH_Ejemplo/BBVA"))
CORTE_1 = os.path.join(CARPETA, "Corte_1.xls")
CORTE_2 = os.path.join(CARPETA, "Corte_2.xls")

pytestmark = pytest.mark.skipif(
    not (os.path.exists(CORTE_1) and os.path.exists(CORTE_2)),
    reason=f"No se encontraron los cortes de ejemplo en {CARPETA} (define MH_CORTES_DIR)",
)


@pytest.fixture(scope="module")
def corte_1():
    return parsear_archivo(CORTE_1, "BBVA")


@pytest.fixture(scope="module")
def corte_2():
    return parsear_archivo(CORTE_2, "BBVA")


# ──────────────────────────────────────────────────────────
# Lectura del archivo
# ──────────────────────────────────────────────────────────


def test_reconoce_el_formato_de_bbva():
    assert detectar_banco(CORTE_1) == "BBVA"
    assert detectar_banco(CORTE_2) == "BBVA"


def test_lee_todos_los_movimientos(corte_1, corte_2):
    assert len(corte_1) == 80
    assert len(corte_2) == 172


def test_ningun_movimiento_queda_sin_datos_basicos(corte_2):
    """Un movimiento sin monto ni referencia no se puede identificar ni deduplicar."""
    sin_monto = [m for m in corte_2 if not (m.abono or m.cargo)]
    assert not sin_monto, f"{len(sin_monto)} movimiento(s) sin cargo ni abono"


# ──────────────────────────────────────────────────────────
# La llave de deduplicación
# ──────────────────────────────────────────────────────────


def test_la_llave_no_se_repite_dentro_de_un_mismo_corte(corte_2):
    """Si dos movimientos distintos compartieran llave, uno se perdería al subir."""
    llaves = [clave_movimiento(m) for m in corte_2]
    assert len(set(llaves)) == len(llaves), "hay llaves repetidas dentro del mismo archivo"


def test_el_mismo_movimiento_conserva_su_llave_entre_cortes(corte_1, corte_2):
    """Lo esencial del dedup: un movimiento leído en dos cortes distintos debe
    producir EXACTAMENTE la misma llave, o se re-subiría a SIPP."""
    llaves_1 = {clave_movimiento(m) for m in corte_1}
    llaves_2 = {clave_movimiento(m) for m in corte_2}
    assert llaves_1 <= llaves_2, (
        f"{len(llaves_1 - llaves_2)} movimiento(s) del Corte_1 cambiaron de llave "
        "en el Corte_2: se volverían a subir a SIPP"
    )


# ──────────────────────────────────────────────────────────
# El escenario real: subir Corte_1 y luego Corte_2
# ──────────────────────────────────────────────────────────


def _historial_con(movimientos, banco="BBVA", subido=True):
    """Simula el historial que guarda la app tras subir una extracción."""
    return [{
        "id": "extraccion-previa",
        "banco": banco,
        "subido_sipp": subido,
        "movimientos": [{
            "banco": m.banco, "referencia": m.referencia, "abono": m.abono,
            "fecha": m.fecha.isoformat() if m.fecha else "",
            "descripcion": m.descripcion,
        } for m in movimientos],
    }]


def test_tras_subir_el_primer_corte_solo_quedan_los_nuevos(corte_1, corte_2):
    """EL CASO QUE IMPORTA: con Corte_1 ya en SIPP, el Corte_2 debe aportar
    únicamente los 92 movimientos nuevos, no los 172."""
    ya_subidas = claves_subidas(_historial_con(corte_1), "BBVA", solo_subidos=True)
    nuevos = [m for m in corte_2 if clave_movimiento(m) not in ya_subidas]

    assert len(nuevos) == 92, f"se subirían {len(nuevos)} movimiento(s) en vez de 92"
    repetidos = len(corte_2) - len(nuevos)
    assert repetidos == 80, f"se detectaron {repetidos} duplicados en vez de 80"


def test_volver_a_subir_el_mismo_corte_no_aporta_nada(corte_2):
    """Si el usuario carga dos veces el MISMO archivo, no debe subirse nada."""
    ya_subidas = claves_subidas(_historial_con(corte_2), "BBVA", solo_subidos=True)
    nuevos = [m for m in corte_2 if clave_movimiento(m) not in ya_subidas]
    assert nuevos == [], f"{len(nuevos)} movimiento(s) se re-subirían a SIPP"


def test_una_extraccion_no_subida_no_bloquea_movimientos(corte_1, corte_2):
    """Solo lo marcado como subido a SIPP cuenta como duplicado: si la extracción
    previa quedó sin subir, sus movimientos deben seguir disponibles."""
    historial = _historial_con(corte_1, subido=False)
    ya_subidas = claves_subidas(historial, "BBVA", solo_subidos=True)
    nuevos = [m for m in corte_2 if clave_movimiento(m) not in ya_subidas]
    assert len(nuevos) == len(corte_2)


def test_el_historial_de_otro_banco_no_afecta(corte_1, corte_2):
    """Un movimiento de Santander con la misma referencia no debe ocultar uno
    de BBVA."""
    historial = _historial_con(corte_1, banco="SANTANDER")
    ya_subidas = claves_subidas(historial, "BBVA", solo_subidos=True)
    nuevos = [m for m in corte_2 if clave_movimiento(m) not in ya_subidas]
    assert len(nuevos) == len(corte_2)


def test_la_llave_del_movimiento_y_la_del_historial_coinciden(corte_1):
    """El movimiento recién leído y el mismo movimiento guardado en el historial
    deben dar la misma llave (si no, el dedup nunca encontraría nada)."""
    guardado = _historial_con(corte_1)[0]["movimientos"]
    for m, d in zip(corte_1, guardado):
        assert clave_movimiento(m) == clave_movimiento_dict(d)


# ──────────────────────────────────────────────────────────
# Recorte de archivos acumulados
# ──────────────────────────────────────────────────────────


def test_un_corte_de_un_solo_dia_no_se_recorta(corte_2):
    """Ambos cortes son del mismo día: deben pasar intactos, sin fecha de corte."""
    conservados, omitidos, corte = recortar_acumulado(corte_2)
    assert len(conservados) == len(corte_2)
    assert omitidos == []
    assert corte is None
