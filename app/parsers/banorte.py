import csv

from ..models import Movimiento
from ..textutils import normalizar
from .base import clean_text, parse_date_dmy_slash, parse_money

BANCO = "BANORTE"

# Encabezado del estado de cuenta de Banorte (.csv):
# CUENTA, FECHA DE OPERACIÓN, FECHA, DEPÓSITOS, RETIROS, MOVIMIENTO, DESCRIPCIÓN DETALLADA
_COLS_REQUERIDAS = ("CUENTA", "DEPOSITOS", "MOVIMIENTO", "DESCRIPCION DETALLADA")


def _key(texto: str) -> str:
    """Clave de encabezado robusta: sin acentos y en mayúsculas."""
    return normalizar(texto or "").upper()


def _fila_normalizada(row: dict) -> dict:
    return {_key(k): v for k, v in row.items() if k}


def _get(row_norm: dict, nombre: str) -> str:
    return clean_text(row_norm.get(_key(nombre)))


def detect(path: str) -> bool:
    with open(path, newline="", encoding="utf-8-sig") as f:
        primera = _key(f.readline())
    return all(col in primera for col in _COLS_REQUERIDAS)


def parse(path: str) -> list[Movimiento]:
    movimientos: list[Movimiento] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = _fila_normalizada(raw)

            # Solo se procesan abonos (DEPÓSITOS) por ahora; RETIROS se ignora.
            abono = parse_money(row.get(_key("DEPÓSITOS")))
            if abono <= 0:
                continue

            descripcion = _get(row, "DESCRIPCIÓN DETALLADA")

            # Se ignoran las compensaciones por desfase de SPEI: son ajustes
            # internos del banco (centavos), no cobros de clientes.
            if "COMPENSACION" in normalizar(descripcion).upper():
                continue
            movimiento = _get(row, "MOVIMIENTO")  # no. de movimiento (referencia bancaria)

            movimientos.append(
                Movimiento(
                    banco=BANCO,
                    fecha=parse_date_dmy_slash(row.get(_key("FECHA DE OPERACIÓN"))),
                    descripcion=descripcion,
                    referencia=movimiento,
                    concepto="",
                    cargo=0.0,
                    abono=abono,
                    saldo=None,
                    # Solo la descripción: trae CLABE/cuenta del cliente, RFC,
                    # nombre, referencia y CVE RAST. NO incluimos la cuenta propia
                    # de Petroil ni el no. de movimiento (generarían falsos matches).
                    texto_busqueda=descripcion,
                )
            )
    return movimientos
