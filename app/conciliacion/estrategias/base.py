"""Patrón Strategy para normalizar Excel bancarios a MovimientoConciliacion.

La clase base concentra TODA la lógica de lectura (openpyxl), conversión de fecha
(serial de Excel o texto), limpieza de importes y descarte de filas vacías. Cada
banco es una subclase que solo declara qué ENCABEZADOS mapean a cada campo
canónico. Los encabezados se comparan ya normalizados (mayúsculas, sin acentos,
espacios -> "_"), igual que las llaves del procedimiento almacenado de referencia
("Fecha Operación" -> FECHA_OPERACION), así detección y mapeo son uniformes.

Formatos soportados (extraídos del SP `upR_cont_ConciliacionesBancarias_LeerMovimientos`):
  - Dos columnas separadas de importe (Abono / Cargo). Ej.: BBVA, Banorte.
  - Una sola columna de importe CON SIGNO (naturaleza por el signo). Ej.: Santander,
    Bancoppel (`abono_es_positivo` invierte la regla).
  - Una sola columna de importe + una columna TIPO ('ABONO'/'CARGO'). Ej.: Scotiabank
    (sobrescribe `_importe_y_naturaleza`).
  - Descripción compuesta por varias columnas (se concatenan). Ej.: Scotiabank.
  - Referencia tomada de la descripción cuando no hay columna propia. Ej.: Banamex,
    Sabadell (`referencia_desde_descripcion`).
"""

import re
import unicodedata
from abc import ABC
from datetime import date, datetime, timedelta
from typing import Optional

import openpyxl

from ..modelo import MovimientoConciliacion
from ...parsers.base import clean_text, parse_money

# Epoch base para seriales de Excel: coincide con el DATEADD(DAY, n-1, '1899-12-31')
# del SP (no corrige el bug del año bisiesto 1900 de Excel, a propósito, para que
# la fecha resultante sea idéntica a la del sistema contable de referencia).
_EPOCH_EXCEL = date(1899, 12, 31)


def normalizar_encabezado(texto: object) -> str:
    """'Fecha Operación' -> 'FECHA_OPERACION'. Mayúsculas, sin acentos, y todo lo
    no alfanumérico colapsado a un solo '_'."""
    s = "" if texto is None else str(texto)
    s = s.upper()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^A-Z0-9]+", "_", s)
    return s.strip("_")


def a_fecha(valor: object) -> Optional[date]:
    """Convierte una celda de fecha: datetime/date directo, serial de Excel
    (número) o texto dd/mm/yyyy. Devuelve None si no se puede interpretar."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, (int, float)):
        return _EPOCH_EXCEL + timedelta(days=int(valor) - 1)
    texto = str(valor).strip().strip("'").replace("_", "")
    # Serial de Excel almacenado como texto ("45962").
    if texto.replace(".", "", 1).isdigit() and "/" not in texto and "-" not in texto:
        return _EPOCH_EXCEL + timedelta(days=int(float(texto)) - 1)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


class EstrategiaBancoExcel(ABC):
    """Base de las estrategias. Una subclase declara `nombre`, la fila de
    encabezado, los sets de encabezados por campo y (opcional) su firma de
    detección."""

    nombre: str = ""
    fila_encabezado: int = 1
    solo_abonos: bool = True  # cobranza: solo interesa el dinero que entra (abonos)

    # Encabezados aceptados por campo (ya normalizados con normalizar_encabezado).
    COLS_FECHA: set[str] = set()
    COLS_DESCRIPCION: set[str] = set()   # varias -> se concatenan
    COLS_REFERENCIA: set[str] = set()    # varias -> se concatenan
    COLS_ABONO: set[str] = set()
    COLS_CARGO: set[str] = set()
    COLS_IMPORTE: set[str] = set()       # columna única con signo (alternativa a Abono/Cargo)
    COLS_TIPO: set[str] = set()          # columna 'ABONO'/'CARGO' (p. ej. Scotiabank)
    COLS_SALDO: set[str] = set()

    # Detección: si FIRMA no está vacía, se exige que TODOS sus encabezados estén
    # presentes (y ninguno de FIRMA_AUSENTE) para reconocer el banco. Sirve para
    # desambiguar formatos parecidos (p. ej. CARGO singular vs CARGOS plural).
    FIRMA: set[str] = set()
    FIRMA_AUSENTE: set[str] = set()

    # Reglas para columna única con signo / referencia derivada.
    abono_es_positivo: bool = True       # True: abono si importe >= 0; False: si <= 0
    referencia_desde_descripcion: bool = False

    def detectar(self, headers_norm: set[str]) -> bool:
        """True si los encabezados del archivo corresponden a este banco."""
        if self.FIRMA_AUSENTE & headers_norm:
            return False
        if self.FIRMA:
            return self.FIRMA <= headers_norm
        # Respaldo si no se declaró firma: referencia + alguna columna de importe.
        tiene_ref = bool(self.COLS_REFERENCIA & headers_norm) or (
            self.referencia_desde_descripcion and bool(self.COLS_DESCRIPCION & headers_norm)
        )
        tiene_importe = bool((self.COLS_ABONO | self.COLS_CARGO | self.COLS_IMPORTE) & headers_norm)
        return tiene_ref and tiene_importe

    def normalizar(self, path: str) -> list[MovimientoConciliacion]:
        """Lee el .xlsx y devuelve la lista de movimientos normalizados. Saltos:
        filas vacías y (si `solo_abonos`) movimientos sin abono."""
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            filas = ws.iter_rows(values_only=True)

            header = None
            for i, fila in enumerate(filas, start=1):
                if i == self.fila_encabezado:
                    header = fila
                    break
            if header is None:
                raise ValueError("El archivo no tiene fila de encabezado.")

            headers_norm = [normalizar_encabezado(c) for c in header]
            idx = self._resolver_indices(headers_norm)

            movimientos: list[MovimientoConciliacion] = []
            for fila in filas:  # continúa después del encabezado
                if self._fila_vacia(fila):
                    continue
                mov = self._fila_a_movimiento(fila, idx, header)
                if mov is None:
                    continue
                if self.solo_abonos and mov.naturaleza != "A":
                    continue
                movimientos.append(mov)
            return movimientos
        finally:
            wb.close()

    # --- Helpers internos -------------------------------------------------------

    def _resolver_indices(self, headers_norm: list[str]) -> dict:
        def primero(cols: set[str]) -> Optional[int]:
            for i, h in enumerate(headers_norm):
                if h in cols:
                    return i
            return None

        def todos(cols: set[str]) -> list[int]:
            return [i for i, h in enumerate(headers_norm) if h in cols]

        idx = {
            "fecha": primero(self.COLS_FECHA),
            "descripcion": todos(self.COLS_DESCRIPCION),
            "referencia": todos(self.COLS_REFERENCIA),
            "abono": primero(self.COLS_ABONO),
            "cargo": primero(self.COLS_CARGO),
            "importe": primero(self.COLS_IMPORTE),
            "tipo": primero(self.COLS_TIPO),
            "saldo": primero(self.COLS_SALDO),
        }
        tiene_ref = bool(idx["referencia"]) or (self.referencia_desde_descripcion and bool(idx["descripcion"]))
        tiene_importe = idx["abono"] is not None or idx["cargo"] is not None or idx["importe"] is not None
        if not tiene_ref or not tiene_importe:
            raise ValueError(
                f"El formato del archivo no coincide con el banco {self.nombre}: "
                "faltan columnas de referencia o de importe."
            )
        return idx

    @staticmethod
    def _fila_vacia(fila: tuple) -> bool:
        return all(c is None or (isinstance(c, str) and not c.strip()) for c in fila)

    def _celda(self, fila: tuple, i: Optional[int]) -> object:
        return fila[i] if i is not None and i < len(fila) else None

    def _unir(self, fila: tuple, indices: list[int]) -> str:
        partes = [self._texto(self._celda(fila, i)).strip() for i in indices]
        return " ".join(p for p in partes if p)

    def _importe_y_naturaleza(self, fila: tuple, idx: dict) -> Optional[tuple[float, str]]:
        """Devuelve (importe_abs, naturaleza) o None si la fila no tiene importe.

        Sobrescribible por bancos con reglas especiales (p. ej. Scotiabank usa una
        columna TIPO)."""
        if idx["importe"] is not None:  # columna única con signo
            valor = parse_money(self._texto(self._celda(fila, idx["importe"])))
            if not valor:
                return None
            if self.abono_es_positivo:
                naturaleza = "A" if valor >= 0 else "C"
            else:
                naturaleza = "A" if valor <= 0 else "C"
            return abs(valor), naturaleza

        abono = parse_money(self._texto(self._celda(fila, idx["abono"])))
        cargo = parse_money(self._texto(self._celda(fila, idx["cargo"])))
        importe = abono if abono else cargo
        if not importe:
            return None
        return abs(importe), ("A" if abono else "C")

    def _fila_a_movimiento(self, fila: tuple, idx: dict, header: tuple) -> Optional[MovimientoConciliacion]:
        res = self._importe_y_naturaleza(fila, idx)
        if res is None:
            return None
        importe, naturaleza = res

        descripcion = clean_text(self._unir(fila, idx["descripcion"]))
        if idx["referencia"]:
            referencia = clean_text(self._unir(fila, idx["referencia"]))
        elif self.referencia_desde_descripcion:
            referencia = descripcion
        else:
            referencia = ""

        saldo_txt = self._texto(self._celda(fila, idx["saldo"]))
        return MovimientoConciliacion(
            fecha=a_fecha(self._celda(fila, idx["fecha"])),
            descripcion=descripcion,
            referencia=referencia,
            importe=round(importe, 2),
            naturaleza=naturaleza,
            saldo=parse_money(saldo_txt) if saldo_txt else None,
            origen=f"BANCO:{self.nombre}",
            raw={normalizar_encabezado(h): fila[i] for i, h in enumerate(header) if i < len(fila)},
        )

    @staticmethod
    def _texto(valor: object) -> str:
        """Celda -> texto para parse_money/clean_text (que esperan str|None)."""
        if valor is None:
            return ""
        if isinstance(valor, float) and valor.is_integer():
            return str(int(valor))
        return str(valor)
