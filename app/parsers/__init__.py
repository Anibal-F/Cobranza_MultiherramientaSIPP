from ..models import Movimiento
from . import bajio, banorte, banregio, bbva, santander

PARSERS = {
    "SANTANDER": santander,
    "BANREGIO": banregio,
    "BANORTE": banorte,
    "BANBAJIO": bajio,
    "BBVA": bbva,
}


def detectar_banco(path: str) -> str | None:
    for nombre, modulo in PARSERS.items():
        try:
            if modulo.detect(path):
                return nombre
        except Exception:
            continue
    return None


def parsear_archivo(path: str, banco: str) -> list[Movimiento]:
    modulo = PARSERS[banco]
    return modulo.parse(path)
