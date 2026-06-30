import re
import unicodedata


def normalizar(texto: str) -> str:
    """Mayúsculas, sin acentos y solo alfanuméricos, para comparar texto libre."""
    texto = texto.upper()
    texto = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    texto = re.sub(r"[^A-Z0-9 ]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()
