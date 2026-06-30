import csv

from ..models import Movimiento
from .base import clean_text, parse_date_ddmmyyyy, parse_money

BANCO = "SANTANDER"

EXPECTED_HEADER = "Cuenta"


def detect(path: str) -> bool:
    with open(path, newline="", encoding="utf-8-sig") as f:
        primera_linea = f.readline()
    return primera_linea.startswith(EXPECTED_HEADER) and "Hora" in primera_linea


def parse(path: str) -> list[Movimiento]:
    movimientos: list[Movimiento] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            signo = clean_text(row.get("Cargo/Abono"))
            importe = parse_money(row.get("Importe"))

            # Solo se procesan abonos por ahora. Si en el futuro se requieren
            # también los cargos, descomentar la siguiente línea y quitar el
            # "continue".
            # cargo = importe if signo == "-" else 0.0
            cargo = 0.0
            if signo != "+":
                continue
            abono = importe if signo == "+" else 0.0

            descripcion = clean_text(row.get("Descripcion"))
            referencia = clean_text(row.get("Referencia"))
            concepto = clean_text(row.get("Concepto"))

            campos_busqueda = [
                referencia,
                concepto,
                descripcion,
                clean_text(row.get("Cta Ordenante")),
                clean_text(row.get("Clabe Beneficiario")),
                clean_text(row.get("Nombre Ordenante")),
                clean_text(row.get("Nombre Beneficiario")),
            ]

            movimientos.append(
                Movimiento(
                    banco=BANCO,
                    fecha=parse_date_ddmmyyyy(row.get("Fecha")),
                    descripcion=descripcion,
                    referencia=referencia,
                    concepto=concepto,
                    cargo=cargo,
                    abono=abono,
                    saldo=parse_money(row.get("Saldo")),
                    texto_busqueda=" ".join(campos_busqueda),
                )
            )
    return movimientos
