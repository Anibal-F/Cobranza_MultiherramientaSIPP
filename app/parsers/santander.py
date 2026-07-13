import csv

from ..models import Movimiento
from .base import clean_text, parse_date_ddmmyyyy, parse_money

BANCO = "SANTANDER"
# Si True, este banco se lista en el selector de Conciliaciones Bancarias. Poner en
# False para bancos cuyo formato aún no se ha validado (no aparecerán en el selector
# y, si la autodetección los arroja, se avisa al usuario que se comunique).
EN_CONCILIACION = True
# Modo de emparejamiento en conciliación: "contiene" — la referencia del sistema se
# busca DENTRO del texto del movimiento (la columna Concepto trae el detalle del
# pago con la referencia embebida, p. ej. "PAGO BFERRIES 2100015345 0102944...").
MODO_CONCILIACION = "contiene"

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
