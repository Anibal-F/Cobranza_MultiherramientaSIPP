import math
import re

from .models import ClienteCuenta, Movimiento
from .textutils import normalizar

LONGITUD_MINIMA_CUENTA = 6

# Palabras sin valor distintivo dentro de razones sociales: se ignoran al
# evaluar coincidencias por palabras sueltas para evitar falsos positivos.
PALABRAS_VACIAS = {
    "DE", "LA", "EL", "LOS", "LAS", "Y", "SA", "CV", "DEL", "SAPI", "RL",
    "CIA", "SC", "SCP", "S", "A", "C", "V", "EN", "SUS",
}
LONGITUD_MINIMA_PALABRA = 3

BANCOS_CONOCIDOS = [
    "BBVA", "SANTANDER", "BANORTE", "HSBC", "BANAMEX", "CITIBANAMEX",
    "SCOTIABANK", "INBURSA", "BAJIO", "BANREGIO", "AFIRME", "BANJERCITO",
    "MULTIVA", "ACTINVER", "CIBANCO", "INVEX", "COMPARTAMOS", "BANCOPPEL",
    "AZTECA",
]


def match_movimientos(movimientos: list[Movimiento], catalogo: list[ClienteCuenta]) -> None:
    """Marca cada movimiento con el cliente identificado, buscando el número de
    cuenta del catálogo como substring dentro del texto del movimiento.
    Modifica los movimientos in-place.
    """
    cuentas_ordenadas = sorted(
        (c for c in catalogo if len(c.cuenta) >= LONGITUD_MINIMA_CUENTA),
        key=lambda c: len(c.cuenta),
        reverse=True,
    )

    for mov in movimientos:
        for cuenta_cliente in cuentas_ordenadas:
            if cuenta_cliente.cuenta in mov.texto_busqueda:
                mov.cliente_match = cuenta_cliente.cliente
                mov.cuenta_match = cuenta_cliente.cuenta
                mov.banco_match = cuenta_cliente.banco
                break


def _palabras_significativas(nombre_normalizado: str) -> list[str]:
    return [
        palabra
        for palabra in nombre_normalizado.split()
        if len(palabra) >= LONGITUD_MINIMA_PALABRA and palabra not in PALABRAS_VACIAS
    ]


def _match_por_nombre(texto_normalizado: str, clientes_normalizados: list[tuple[str, str]]) -> str | None:
    # 1) Coincidencia exacta de la razón social completa (más confiable).
    for nombre_original, nombre_norm in clientes_normalizados:
        if nombre_norm in texto_normalizado:
            return nombre_original

    # 2) Coincidencia por palabras significativas, para cuando el texto del
    #    banco viene truncado o con acentos distintos (ej. "PEÑA COLORADA"
    #    aparece como "PENA COLO"). Se exigen al menos 2 palabras para evitar
    #    falsos positivos de nombres con una sola palabra distintiva (ej.
    #    "FLETES 3H" no debe matchear solo por contener "FLETES").
    for nombre_original, nombre_norm in clientes_normalizados:
        palabras = _palabras_significativas(nombre_norm)
        if len(palabras) < 2:
            continue
        encontradas = sum(1 for palabra in palabras if palabra in texto_normalizado)
        if encontradas >= max(2, math.ceil(len(palabras) * 0.7)):
            return nombre_original
    return None


def extraer_cuenta(texto: str) -> str | None:
    numeros = re.findall(r"\d{8,18}", texto)
    if not numeros:
        return None
    return max(numeros, key=len)


def _extraer_banco(texto: str, banco_por_defecto: str) -> str:
    texto_norm = normalizar(texto)
    for banco in BANCOS_CONOCIDOS:
        if banco in texto_norm:
            return banco
    return banco_por_defecto


def match_movimientos_por_nombre(
    movimientos: list[Movimiento], clientes_normalizados: list[tuple[str, str]]
) -> list[ClienteCuenta]:
    """Para los movimientos que no calzaron por cuenta, intenta identificar al
    cliente por nombre dentro del texto del movimiento. Cuando lo logra, extrae
    el número de cuenta/CLABE del texto para proponerlo como nuevo registro del
    catálogo de cuentas (para que en próximas ejecuciones ya matchee por cuenta).
    Modifica los movimientos in-place y regresa los nuevos registros propuestos.
    """
    nuevas_cuentas: list[ClienteCuenta] = []
    cuentas_propuestas: set[str] = set()

    for mov in movimientos:
        if mov.identificado:
            continue
        texto_normalizado = normalizar(mov.texto_busqueda)
        cliente = _match_por_nombre(texto_normalizado, clientes_normalizados)
        if not cliente:
            continue

        mov.cliente_match = cliente
        mov.identificado_por_nombre = True

        cuenta_extraida = extraer_cuenta(mov.texto_busqueda)
        if not cuenta_extraida:
            continue
        mov.cuenta_match = cuenta_extraida
        mov.banco_match = _extraer_banco(mov.texto_busqueda, mov.banco)

        if cuenta_extraida not in cuentas_propuestas:
            cuentas_propuestas.add(cuenta_extraida)
            nuevas_cuentas.append(
                ClienteCuenta(cuenta=cuenta_extraida, cliente=cliente, banco=mov.banco_match, plaza="")
            )

    return nuevas_cuentas
