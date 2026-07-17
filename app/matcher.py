import math
import re

from .models import ClienteCuenta, Movimiento
from .textutils import normalizar, normalizar_referencia

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


_CUENTA_DIGITOS_RE = re.compile(r"\d{%d,}" % LONGITUD_MINIMA_CUENTA)

# RFC mexicano: 3-4 letras (moral/física) + 6 dígitos (fecha) + 3 de homoclave.
_RFC_RE = re.compile(r"[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}")
# Para extraer del texto del movimiento, preferimos el que sigue a la etiqueta "RFC".
_RFC_ETIQUETADO_RE = re.compile(r"RFC\W{0,5}([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})", re.IGNORECASE)


def _rfc_valido(texto: str) -> str:
    """Devuelve el RFC normalizado (mayúsculas) si `texto` es un RFC válido, o ''."""
    candidato = (texto or "").strip().upper()
    return candidato if _RFC_RE.fullmatch(candidato) else ""


def extraer_rfc(texto: str) -> str | None:
    """Extrae el RFC del texto del movimiento. Prioriza el que aparece tras la
    etiqueta 'RFC'; si no, toma el primer RFC bien formado del texto.

    Antes de buscar se eliminan los identificadores por-transacción (CVE RAST,
    referencia, folio): la clave de rastreo suele empezar con letras+dígitos
    (ej. 'CRED05002607010000001860') que tienen forma de RFC y darían un falso
    positivo. La limpieza se define más abajo en el módulo (`_RE_TOKENS_TRANSACCION`)."""
    limpio = _RE_TOKENS_TRANSACCION.sub(" ", texto or "")
    etiquetado = _RFC_ETIQUETADO_RE.search(limpio)
    if etiquetado:
        return etiquetado.group(1).upper()
    generico = _RFC_RE.search(limpio.upper())
    return generico.group(0) if generico else None


def match_movimientos(movimientos: list[Movimiento], catalogo: list[ClienteCuenta]) -> None:
    """Marca cada movimiento con el cliente identificado, buscando el número de
    cuenta del catálogo como substring dentro del texto del movimiento.
    Modifica los movimientos in-place.

    Solo se consideran CORRIDAS DE DÍGITOS (>= LONGITUD_MINIMA_CUENTA) de cada
    cuenta. El catálogo tiene entradas basura no numéricas (ej. 'TEF RECIBIDO
    BANORTE'), y hacer substring con esas produce falsos positivos contra las
    descripciones bancarias ('SPEI RECIBIDO ... BANORTE'). Una cuenta como
    '146651798/112335751' aporta sus dos números por separado.
    """
    indexado: list[tuple[str, ClienteCuenta]] = []
    rfc_indexado: list[tuple[str, ClienteCuenta]] = []
    for c in catalogo:
        # El catálogo trae cuentas SIN nombre de cliente. Indexarlas no sirve (no
        # identifican a nadie) y además hace daño: ganan el match por cuenta —que
        # corta en la primera coincidencia— dejando el movimiento con cliente ""
        # y bloqueando el match por nombre, que se salta los ya identificados.
        if not (c.cliente or "").strip():
            continue
        for numero in _CUENTA_DIGITOS_RE.findall(c.cuenta or ""):
            indexado.append((numero, c))
        rfc = _rfc_valido(getattr(c, "rfc", ""))
        if rfc:
            rfc_indexado.append((rfc, c))
    indexado.sort(key=lambda par: len(par[0]), reverse=True)

    for mov in movimientos:
        # 1) Match por cuenta/CLABE (identificador más específico).
        for numero, cuenta_cliente in indexado:
            if numero in mov.texto_busqueda:
                mov.cliente_match = cuenta_cliente.cliente
                mov.cuenta_match = numero
                mov.banco_match = cuenta_cliente.banco
                break
        else:
            # 2) Fallback por RFC (cuando la CLABE viene enmascarada).
            texto_upper = mov.texto_busqueda.upper()
            for rfc, cuenta_cliente in rfc_indexado:
                if rfc in texto_upper:
                    mov.cliente_match = cuenta_cliente.cliente
                    mov.cuenta_match = rfc
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


# Identificadores ÚNICOS POR TRANSACCIÓN que NO deben tomarse como cuenta/CLABE
# del cliente: la clave de rastreo (CVE RAST), la referencia numérica y el folio
# (FLM) cambian en cada operación, así que guardarlos en el catálogo generaría
# entradas basura que nunca volverían a coincidir.
_RE_TOKENS_TRANSACCION = re.compile(
    r"(?:CVE\s*RAST|CLAVE\s*DE\s*RASTREO|REFERENCIA(?:\s*NUMERICA)?|REF\.?|"
    r"NO\.?\s*FLM|FLM|FOLIO)\s*:?\s*[A-Z]*\d[\dA-Z]*",
    re.IGNORECASE,
)

# La CLABE interbancaria mexicana tiene exactamente 18 dígitos y suele venir
# precedida de "CLABE".
_RE_CLABE_ETIQUETADA = re.compile(r"CLABE\D{0,12}(\d{18})", re.IGNORECASE)


def extraer_cuenta(texto: str) -> str | None:
    """Extrae la cuenta/CLABE ordenante del cliente desde el texto del
    movimiento, para proponerla como clave del catálogo. Descarta los
    identificadores únicos por transacción (CVE RAST, referencia, folio) que no
    sirven como clave. Devuelve None si no hay un número estable de cuenta."""
    texto = texto or ""

    # 1) CLABE explícita ("... CLABE 0123456789012345 67 ...").
    etiquetada = _RE_CLABE_ETIQUETADA.search(texto)
    if etiquetada:
        return etiquetada.group(1)

    # 2) Buscar en el texto ya sin los tokens por-transacción.
    limpio = _RE_TOKENS_TRANSACCION.sub(" ", texto)
    numeros = re.findall(r"\d{10,18}", limpio)
    if not numeros:
        return None
    # Preferir una CLABE de 18 dígitos si aparece; si no, el número más largo.
    clabes = [n for n in numeros if len(n) == 18]
    if clabes:
        return clabes[0]
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
    claves_propuestas: set[str] = set()

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
        rfc_extraido = extraer_rfc(mov.texto_busqueda)
        if not cuenta_extraida and not rfc_extraido:
            continue

        mov.banco_match = _extraer_banco(mov.texto_busqueda, mov.banco)
        # La cuenta/CLABE es la clave preferida; si no hay, se usa el RFC.
        mov.cuenta_match = cuenta_extraida or rfc_extraido

        clave = cuenta_extraida or f"RFC:{rfc_extraido}"
        if clave not in claves_propuestas:
            claves_propuestas.add(clave)
            nuevas_cuentas.append(
                ClienteCuenta(
                    cuenta=cuenta_extraida or "",
                    cliente=cliente,
                    banco=mov.banco_match,
                    plaza="",
                    rfc=rfc_extraido or "",
                )
            )

    return nuevas_cuentas


def enriquecer_con_spei(movimientos: list[Movimiento], indice_spei: dict[str, dict]) -> None:
    """Enriquece los movimientos interbancarios de BBVA con los datos del ordenante
    que trae el SPEI (razón social y cuenta ordenante), enlazando por REFERENCIA.

    El RSM no trae la cuenta ni la razón social del ordenante; el SPEI sí. Se
    guarda la razón social en el movimiento (pista para el grid) y se anexan la
    cuenta y el nombre a `texto_busqueda`, de modo que el match por CUENTA (contra
    el catálogo) y por NOMBRE funcionen sin cambios. Es idempotente: no re-anexa si
    la cuenta ya está en el texto. Modifica los movimientos in-place."""
    for mov in movimientos:
        datos = indice_spei.get(normalizar_referencia(mov.referencia))
        if not datos:
            continue  # movimiento interno del RSM (no interbancario): no está en el SPEI
        mov.razon_social_ordenante = datos.get("nombre") or mov.razon_social_ordenante
        cuenta = datos.get("cuenta") or ""
        nombre = datos.get("nombre") or ""
        extra = " ".join(x for x in (cuenta, nombre) if x)
        if extra and (not cuenta or cuenta not in mov.texto_busqueda):
            mov.texto_busqueda = f"{mov.texto_busqueda} {extra}".strip()


def match_movimientos_por_spei(
    movimientos: list[Movimiento],
    indice_spei: dict[str, dict],
    clientes_normalizados: list[tuple[str, str]],
) -> list[ClienteCuenta]:
    """Identifica los movimientos interbancarios de BBVA aún NO identificados usando
    el índice del SPEI (referencia → razón social + cuenta ordenante + banco
    ordenante). Matchea la razón social contra el maestro de clientes; al acertar,
    marca el cliente y propone la CUENTA ORDENANTE → cliente (banco = banco
    ordenante del SPEI, plaza vacía) para auto-agregarla al catálogo.

    Se ejecuta DESPUÉS de `match_movimientos` (para que una cuenta ya presente en el
    catálogo gane primero) y salta los ya identificados. Modifica in-place y regresa
    las cuentas propuestas. Los movimientos cuya razón social no coincida con ningún
    cliente quedan sin identificar (con la razón social como pista en el grid)."""
    nuevas_cuentas: list[ClienteCuenta] = []
    claves_propuestas: set[str] = set()

    for mov in movimientos:
        if mov.identificado:
            continue
        datos = indice_spei.get(normalizar_referencia(mov.referencia))
        if not datos:
            continue
        cliente = _match_por_nombre(normalizar(datos.get("nombre") or ""), clientes_normalizados)
        if not cliente:
            continue

        cuenta = datos.get("cuenta") or ""
        banco = datos.get("banco") or mov.banco
        mov.cliente_match = cliente
        mov.identificado_por_nombre = True
        mov.banco_match = banco
        mov.cuenta_match = cuenta or mov.cuenta_match

        if cuenta and cuenta not in claves_propuestas:
            claves_propuestas.add(cuenta)
            nuevas_cuentas.append(
                ClienteCuenta(cuenta=cuenta, cliente=cliente, banco=banco, plaza="")
            )

    return nuevas_cuentas
