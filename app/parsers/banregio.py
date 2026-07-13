import csv

from ..models import Movimiento
from .base import clean_text, parse_date_dmy_slash, parse_money

BANCO = "BANREGIO"
# Si True, se lista en el selector de Conciliaciones Bancarias (ver santander.py).
EN_CONCILIACION = True


def _find_header_index(rows: list[list[str]]) -> int:
    for i, row in enumerate(rows):
        if row and clean_text(row[0]) == "Fecha" and len(row) > 1 and "Descrip" in row[1]:
            return i
    raise ValueError("No se encontró el encabezado 'Fecha,Descripción,...' en el archivo BanRegio")


def detect(path: str) -> bool:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    try:
        _find_header_index(rows[:20])
    except ValueError:
        return False
    return True


def parse(path: str) -> list[Movimiento]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    header_idx = _find_header_index(rows)
    header = [clean_text(h) for h in rows[header_idx]]

    movimientos: list[Movimiento] = []
    for raw_row in rows[header_idx + 1 :]:
        if not raw_row or not clean_text(raw_row[0]):
            continue  # filas como "Saldo Inicial" sin fecha
        row = dict(zip(header, raw_row))

        # Solo se procesan abonos por ahora. Si en el futuro se requieren
        # también los cargos, descomentar la siguiente línea y quitar el
        # "continue".
        # cargo = parse_money(row.get("Cargo"))
        abono = parse_money(row.get("Abonos"))
        if abono <= 0:
            continue

        descripcion = clean_text(row.get("Descripción"))
        referencia = clean_text(row.get("Referencia"))

        movimientos.append(
            Movimiento(
                banco=BANCO,
                fecha=parse_date_dmy_slash(row.get("Fecha")),
                descripcion=descripcion,
                referencia=referencia,
                concepto="",
                cargo=0.0,
                abono=abono,
                saldo=parse_money(row.get("Saldo")),
                texto_busqueda=f"{descripcion} {referencia}",
            )
        )
    return movimientos
