import re
import unicodedata


def normalizar(texto: str) -> str:
    """Mayúsculas, sin acentos y solo alfanuméricos, para comparar texto libre."""
    texto = texto.upper()
    texto = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    texto = re.sub(r"[^A-Z0-9 ]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_referencia(texto: str) -> str:
    """Colapsa espacios de una REFERENCIA para usarla como llave de enlace.

    La referencia de un movimiento interbancario de BBVA (ej. '0152804678  072',
    número de operación + código de banco) es idéntica en el archivo RSM y en la
    columna REFERENCIA del SPEI, salvo por espacios internos: sirve para emparejar
    un movimiento del RSM con su fila del SPEI (que trae la razón social y la cuenta
    ordenante). Se conserva tal cual (dígitos y separación), solo se colapsan
    espacios; NO se aplica `normalizar` porque no es texto libre."""
    return re.sub(r"\s+", " ", (texto or "").strip())
