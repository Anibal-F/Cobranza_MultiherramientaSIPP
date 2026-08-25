"""Pruebas de la salvaguarda que evita capturar pagos al cliente equivocado.

Cada caso representa una situación real del combo de clientes de SIPP. La regla
que se verifica es siempre la misma: **si no hay certeza, no se asigna**.

Para correrlas:  .venv/bin/python -m pytest tests/ -v
"""

import pytest

from app.coincidencia_clientes import (
    Incidencia,
    elegir,
    nucleo,
    parecido,
    resumen,
)


# ──────────────────────────────────────────────────────────
# Normalización del nombre
# ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("05881 - LOGISTICA TEROMO", "LOGISTICA TEROMO"),
        ("C05881 - Comercializadora Ácme, S.A. de C.V.", "COMERCIALIZADORA ACME"),
        ("ACME S.A. DE C.V.", "ACME"),
        ("ACME SA DE CV", "ACME"),
        ("ACME, S.A.P.I. de C.V.", "ACME"),
        ("  transportes   del   norte  ", "TRANSPORTES DEL NORTE"),
        ("", ""),
        (None, ""),
    ],
)
def test_nucleo_normaliza_codigo_acentos_y_razon_social(entrada, esperado):
    assert nucleo(entrada) == esperado


def test_mismo_cliente_escrito_distinto_es_identico():
    """El caso más común: la app y SIPP escriben la razón social diferente."""
    assert parecido("ACME S.A. DE C.V.", "01234 - Acme, SA de CV") == 1.0


# ──────────────────────────────────────────────────────────
# Casos que SÍ se deben asignar
# ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "buscado, opciones, esperado_contiene",
    [
        # Idéntico salvo formato de razón social.
        (
            "COMERCIALIZADORA ACME SA DE CV",
            ["05881 - COMERCIALIZADORA ACME S.A. DE C.V."],
            "COMERCIALIZADORA ACME",
        ),
        # SIPP tiene el nombre recortado (el caso que motivó el borrado de caracteres).
        (
            "TRANSPORTES DEL NOROESTE 3T SA DE CV",
            ["77 - TRANSPORTES DEL NOROESTE 3T"],
            "3T",
        ),
        # Acentos y puntuación distintos.
        (
            "GASOLINERA SAN JOSÉ, S.A. DE C.V.",
            ["0912 - GASOLINERA SAN JOSE SA DE CV"],
            "SAN JOSE",
        ),
        # La correcta no es la primera de la lista.
        (
            "DISTRIBUIDORA DEL VALLE SA DE CV",
            [
                "01 - DISTRIBUIDORA DEL BAJIO SA DE CV",
                "02 - DISTRIBUIDORA DEL VALLE S.A. DE C.V.",
            ],
            "DEL VALLE",
        ),
    ],
)
def test_asigna_cuando_es_el_mismo_cliente(buscado, opciones, esperado_contiene):
    r = elegir(buscado, opciones)
    assert r.aceptado, f"debió asignar, pero: {r.motivo}"
    assert esperado_contiene in r.texto


# ──────────────────────────────────────────────────────────
# Casos que NO se deben asignar (el bug reportado)
# ──────────────────────────────────────────────────────────


def test_no_asigna_por_inicio_de_nombre_compartido():
    """EL BUG: al borrar caracteres quedaba 'COMER' y SIPP mostraba otra empresa.

    Antes se tomaba la primera opción visible y el pago se capturaba al cliente
    equivocado. Ahora se rechaza.
    """
    r = elegir(
        "COMERCIALIZADORA ACME DEL PACIFICO SA DE CV",
        ["03 - COMERCIAL BETA DEL NORTE SA DE CV"],
    )
    assert not r.aceptado
    assert r.indice is None


def test_no_asigna_cuando_el_combo_quedo_vacio():
    r = elegir("CUALQUIER CLIENTE SA DE CV", [])
    assert not r.aceptado
    assert "ninguna opción" in r.motivo


def test_no_asigna_cuando_hay_dos_candidatos_igual_de_parecidos():
    """Ambigüedad: dos clientes casi iguales. Adivinar sería una moneda al aire."""
    r = elegir(
        "GRUPO GASOLINERO DEL NORTE",
        [
            "01 - GRUPO GASOLINERO DEL NORTE I SA DE CV",
            "02 - GRUPO GASOLINERO DEL NORTE II SA DE CV",
        ],
    )
    assert not r.aceptado
    assert "igual de parecidos" in r.motivo


def test_no_asigna_cuando_el_texto_quedo_demasiado_corto():
    """Tras borrar mucho queda un fragmento que casa con decenas de clientes."""
    r = elegir("ACME", ["01 - ACME CORPORATIVO SA DE CV", "02 - ACME LOGISTICA SA DE CV"])
    assert not r.aceptado


def test_nunca_devuelve_la_primera_opcion_por_descarte():
    """Prueba de regresión directa del comportamiento anterior.

    Con una lista larga de clientes que no tienen nada que ver, el resultado debe
    ser 'ninguno', no 'el primero de la lista'.
    """
    ajenos = [f"{i:02d} - EMPRESA AJENA {i} SA DE CV" for i in range(1, 21)]
    r = elegir("PETROLERA DEL PACIFICO SA DE CV", ajenos)
    assert r.indice is None, f"eligió '{r.texto}' cuando no debía elegir nada"


# ──────────────────────────────────────────────────────────
# Resumen para el usuario
# ──────────────────────────────────────────────────────────


def test_resumen_sin_incidencias_es_tranquilizador():
    texto = resumen([], total_procesadas=12)
    assert "correctamente" in texto
    assert "12" in texto


def test_resumen_lista_cada_movimiento_pendiente():
    incidencias = [
        Incidencia(fila=3, cliente_buscado="ACME SA DE CV",
                   motivo="no coincide con ninguna opción", referencia="REF123", monto=1500.5),
        Incidencia(fila=7, cliente_buscado="BETA SA DE CV",
                   motivo="hay dos clientes igual de parecidos"),
    ]
    texto = resumen(incidencias, total_procesadas=10)
    assert "2 de 10" in texto
    assert "Fila 3" in texto and "REF123" in texto and "1,500.50" in texto
    assert "ACME SA DE CV" in texto and "BETA SA DE CV" in texto


def test_recortar_razon_social_no_destruye_el_nombre_distintivo():
    """Salvaguarda del recorte: solo se quitan sufijos del FINAL."""
    assert nucleo("SA DE CV GASOLINERA CENTRO") == "SA DE CV GASOLINERA CENTRO"
    assert nucleo("TRANSPORTES DEL NOROESTE 3T") == "TRANSPORTES DEL NOROESTE 3T"
    # Un nombre que es SOLO razón social queda vacío y por lo tanto nunca casa.
    assert nucleo("S.A. DE C.V.") == ""
    assert not elegir("S.A. DE C.V.", ["01 - ACME SA DE CV"]).aceptado
