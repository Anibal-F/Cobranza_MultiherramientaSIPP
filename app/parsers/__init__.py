from ..models import Movimiento
from . import bajio, banorte, banregio, bbva, santander
from .excel_columnas import BANCOS_COLUMNAS

# Módulos de identificación: cada uno con su lógica propia y sus formatos
# (Santander/BanRegio/Banorte = CSV, BanBajío = .xlsx, BBVA = .xls SpreadsheetML).
PARSERS = {
    "SANTANDER": santander,
    "BANREGIO": banregio,
    "BANORTE": banorte,
    "BANBAJIO": bajio,
    "BBVA": bbva,
}

def _lectores():
    """Todos los lectores en orden de detección: primero los módulos (formatos de
    identificación), luego los .xlsx por columnas. Cada elemento es (nombre, lector),
    donde `lector` expone .detect(path) y .parse(path) -> list[Movimiento].

    Un mismo banco puede aparecer con varios lectores (formatos distintos): p. ej.
    BBVA como .xls (identificación) y como .xlsx (portal/conciliación)."""
    for nombre, modulo in PARSERS.items():
        yield nombre, modulo
    for banco in BANCOS_COLUMNAS:
        yield banco.nombre, banco


def detectar_banco(path: str) -> str | None:
    for nombre, lector in _lectores():
        try:
            if lector.detect(path):
                return nombre
        except Exception:
            continue
    return None


def parsear_archivo(path: str, banco: str) -> list[Movimiento]:
    lectores = [lec for nom, lec in _lectores() if nom == banco]
    if not lectores:
        raise KeyError(banco)
    # Preferir el lector que reconoce ESTE archivo (el banco puede tener varios
    # formatos). Si ninguno detecta —caso "banco forzado" desde el selector— se usa
    # el primero de todos modos.
    for lec in lectores:
        try:
            if lec.detect(path):
                return lec.parse(path)
        except Exception:
            continue
    return lectores[0].parse(path)


def bancos_conciliacion() -> list[str]:
    """Nombres de banco habilitados para el selector de conciliaciones (sin
    duplicar). El flag vive en cada archivo de banco: `EN_CONCILIACION` en los
    módulos (santander.py, bbva.py, ...) y `en_conciliacion` en las
    configuraciones .xlsx de excel_columnas.py.

    Para un banco que tiene módulo (BBVA, Santander, Banorte, Banregio, BanBajío)
    MANDA el flag del módulo; su lector .xlsx por columnas es solo un formato más.
    Los lectores de columnas solo aportan bancos que NO tienen módulo."""
    nombres: list[str] = []
    for nombre, modulo in PARSERS.items():
        if getattr(modulo, "EN_CONCILIACION", True):
            nombres.append(nombre)
    for banco in BANCOS_COLUMNAS:
        if banco.nombre in PARSERS:
            continue  # gobernado por su módulo
        if banco.en_conciliacion and banco.nombre not in nombres:
            nombres.append(banco.nombre)
    return nombres


def es_banco_conciliacion(banco: str) -> bool:
    """True si el banco está habilitado para conciliaciones (para avisar cuando la
    autodetección arroja un banco no habilitado)."""
    return banco in bancos_conciliacion()
