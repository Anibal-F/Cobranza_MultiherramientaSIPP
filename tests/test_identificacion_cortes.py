"""Pruebas de identificación de clientes con cortes REALES de BBVA.

No se verifica "cuántos" se identifican (eso depende del catálogo, que cambia todo
el tiempo), sino las propiedades que deben cumplirse SIEMPRE:

  - identificar es determinista y no depende del orden ni de repetir el proceso;
  - un movimiento que aparece en dos cortes se identifica igual en ambos;
  - todo movimiento identificado por cuenta apunta a la cuenta que realmente trae
    su texto (no a otro cliente).

Esta última es la versión "de identificación" del mismo problema que reportaron en
SIPP: asignar un cliente que no corresponde.
"""

import os

import pytest

from app.catalogo import cargar_catalogo
from app.clientes import cargar_clientes
from app.historial import clave_movimiento
from app.matcher import match_movimientos, match_movimientos_por_nombre
from app.parsers import parsear_archivo
from app.textutils import normalizar

CARPETA = os.getenv("MH_CORTES_DIR", os.path.expanduser("~/Downloads/MH_Ejemplo/BBVA"))
CORTE_1 = os.path.join(CARPETA, "Corte_1.xls")
CORTE_2 = os.path.join(CARPETA, "Corte_2.xls")
RUTA_CATALOGO = "Catalogos/Cuentas_Clientes/Catalogo_Cuentas_Clientes.csv"
RUTA_CLIENTES = "Catalogos/Cuentas_Clientes/Clientes.csv"

pytestmark = pytest.mark.skipif(
    not all(os.path.exists(p) for p in (CORTE_1, CORTE_2, RUTA_CATALOGO, RUTA_CLIENTES)),
    reason="Faltan los cortes de ejemplo o los catálogos",
)


@pytest.fixture(scope="module")
def catalogo():
    return cargar_catalogo(RUTA_CATALOGO)


@pytest.fixture(scope="module")
def clientes_normalizados():
    return [(normalizar(c), c) for c in cargar_clientes(RUTA_CLIENTES)]


def _identificar(ruta, catalogo, clientes_normalizados):
    """Reproduce el proceso completo de la app: primero por cuenta, luego por nombre."""
    movs = parsear_archivo(ruta, "BBVA")
    match_movimientos(movs, catalogo)
    match_movimientos_por_nombre(movs, clientes_normalizados)
    return movs


# ──────────────────────────────────────────────────────────
# Determinismo
# ──────────────────────────────────────────────────────────


def test_identificar_dos_veces_da_el_mismo_resultado(catalogo, clientes_normalizados):
    a = _identificar(CORTE_2, catalogo, clientes_normalizados)
    b = _identificar(CORTE_2, catalogo, clientes_normalizados)
    assert [m.cliente_match for m in a] == [m.cliente_match for m in b]


def test_volver_a_identificar_no_cambia_lo_ya_identificado(catalogo, clientes_normalizados):
    """El proceso debe ser idempotente: pasarlo otra vez sobre los mismos
    movimientos no debe reasignar clientes."""
    movs = _identificar(CORTE_2, catalogo, clientes_normalizados)
    antes = [m.cliente_match for m in movs]
    match_movimientos(movs, catalogo)
    match_movimientos_por_nombre(movs, clientes_normalizados)
    assert [m.cliente_match for m in movs] == antes


def test_el_orden_de_los_movimientos_no_altera_la_identificacion(catalogo, clientes_normalizados):
    movs = _identificar(CORTE_2, catalogo, clientes_normalizados)
    esperado = {clave_movimiento(m): m.cliente_match for m in movs}

    revueltos = parsear_archivo(CORTE_2, "BBVA")
    revueltos.reverse()
    match_movimientos(revueltos, catalogo)
    match_movimientos_por_nombre(revueltos, clientes_normalizados)
    obtenido = {clave_movimiento(m): m.cliente_match for m in revueltos}
    assert obtenido == esperado


# ──────────────────────────────────────────────────────────
# Consistencia entre cortes
# ──────────────────────────────────────────────────────────


def test_un_movimiento_se_identifica_igual_en_ambos_cortes(catalogo, clientes_normalizados):
    """Los 80 movimientos del Corte_1 reaparecen en el Corte_2: deben quedar con
    el MISMO cliente. Si cambiaran, el usuario vería un cliente distinto según el
    archivo que cargue."""
    c1 = _identificar(CORTE_1, catalogo, clientes_normalizados)
    c2 = _identificar(CORTE_2, catalogo, clientes_normalizados)
    en_c2 = {clave_movimiento(m): m.cliente_match for m in c2}

    discrepancias = [
        (clave_movimiento(m), m.cliente_match, en_c2.get(clave_movimiento(m)))
        for m in c1
        if clave_movimiento(m) in en_c2 and m.cliente_match != en_c2[clave_movimiento(m)]
    ]
    assert not discrepancias, f"{len(discrepancias)} movimiento(s) cambian de cliente entre cortes: {discrepancias[:3]}"


# ──────────────────────────────────────────────────────────
# Que el cliente asignado sea el correcto
# ──────────────────────────────────────────────────────────


def test_el_match_por_cuenta_apunta_a_un_cliente_de_esa_cuenta(catalogo, clientes_normalizados):
    """Todo movimiento identificado por cuenta debe apuntar a un cliente que el
    catálogo asocia REALMENTE a esa cuenta.

    Se compara contra el conjunto de clientes de la cuenta (no contra uno solo)
    porque el catálogo tiene cuentas repetidas con más de un cliente; de eso se
    encarga test_no_crecen_las_cuentas_ambiguas_del_catalogo.
    """
    # El índice se arma igual que en el matcher: por CORRIDAS DE DÍGITOS de la
    # cuenta, no por la cadena cruda (una entrada '146651798/112335751' aporta dos
    # números, y '0113205614' se indexa también como '113205614').
    import re

    por_cuenta: dict[str, set[str]] = {}
    for c in catalogo:
        cliente = (c.cliente or "").strip()
        if not cliente:
            continue
        for numero in re.findall(r"\d{6,}", c.cuenta or ""):
            por_cuenta.setdefault(numero, set()).add(cliente)

    movs = _identificar(CORTE_2, catalogo, clientes_normalizados)
    for m in movs:
        if not (m.identificado and m.cuenta_match):
            continue
        cuenta = (m.cuenta_match or "").strip()
        if cuenta in por_cuenta:
            assert m.cliente_match in por_cuenta[cuenta], (
                f"la cuenta {cuenta} pertenece a {sorted(por_cuenta[cuenta])} "
                f"pero se asignó a '{m.cliente_match}'"
            )


# Cuentas que HOY están repetidas en el catálogo con clientes claramente distintos.
# Son un riesgo real: al identificar por cuenta se elige una de ellas según el orden
# del CSV, así que el pago puede quedar a nombre del cliente equivocado. Hay que
# depurarlas en el catálogo; mientras tanto, este número no debe crecer.
CUENTAS_AMBIGUAS_CONOCIDAS = 10


def test_no_crecen_las_cuentas_ambiguas_del_catalogo(catalogo):
    """Vigila la calidad del catálogo: una misma cuenta con dos clientes distintos
    hace que la identificación dependa del orden del archivo."""
    from app.coincidencia_clientes import parecido

    por_cuenta: dict[str, set[str]] = {}
    for c in catalogo:
        cuenta = (c.cuenta or "").strip()
        # Solo cuentas plausibles: el catálogo trae texto basura en esa columna
        # ("PONE", "SPEI"), que el matcher ya ignora por no ser dígitos.
        if cuenta.isdigit() and len(cuenta) >= 6:
            por_cuenta.setdefault(cuenta, set()).add((c.cliente or "").strip())

    ambiguas = {}
    for cuenta, nombres in por_cuenta.items():
        if len(nombres) < 2:
            continue
        lista = sorted(nombres)
        # Variantes del mismo nombre ("GASUPER" vs "GASUPER SA DE CV") son
        # inofensivas; solo interesan los clientes realmente distintos.
        if not all(parecido(lista[0], otro) >= 0.88 for otro in lista[1:]):
            ambiguas[cuenta] = lista

    assert len(ambiguas) <= CUENTAS_AMBIGUAS_CONOCIDAS, (
        f"aparecieron cuentas ambiguas nuevas en el catálogo "
        f"({len(ambiguas)} > {CUENTAS_AMBIGUAS_CONOCIDAS}): "
        + ", ".join(f"{k} → {v}" for k, v in list(ambiguas.items())[:5])
    )


def test_ningun_identificado_queda_con_cliente_vacio(catalogo, clientes_normalizados):
    movs = _identificar(CORTE_2, catalogo, clientes_normalizados)
    vacios = [m for m in movs if m.identificado and not (m.cliente_match or "").strip()]
    assert not vacios, f"{len(vacios)} movimiento(s) marcados como identificados sin cliente"


def test_los_cargos_no_se_identifican_como_cobranza(catalogo, clientes_normalizados):
    """Solo los abonos (dinero que entra) son cobranza; un cargo identificado
    como pago de cliente sería un ingreso inventado."""
    movs = _identificar(CORTE_2, catalogo, clientes_normalizados)
    cargos_identificados = [
        m for m in movs if m.identificado and (m.cargo or 0) > 0 and not (m.abono or 0)
    ]
    assert not cargos_identificados, (
        f"{len(cargos_identificados)} cargo(s) quedaron identificados como cobranza"
    )
