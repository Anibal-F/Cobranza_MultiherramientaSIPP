"""Lector genérico de estados de cuenta en Excel (.xlsx) por mapeo de columnas.

Unifica en el sistema de parsers los formatos que antes vivían aparte como
"estrategias" de conciliación. Cada banco es una instancia de BancoColumnasExcel
que declara qué ENCABEZADOS mapean a cada campo; el lector produce `Movimiento`
(mismo dataclass que los demás parsers), así identificación y conciliación
comparten una sola fuente.

Los encabezados se comparan normalizados (mayúsculas, sin acentos, espacios->"_"),
igual que las llaves del procedimiento almacenado de referencia
("Fecha Operación" -> FECHA_OPERACION). Formatos soportados por banco:
  - Dos columnas de importe (Abono / Cargo). Ej.: BBVA, Banorte.
  - Una columna de importe CON SIGNO (naturaleza por el signo). Ej.: Santander,
    Bancoppel (`abono_es_positivo` invierte la regla).
  - Una columna de importe + columna TIPO ('ABONO'/'CARGO'). Ej.: Scotiabank
    (`naturaleza_por_tipo`).
  - Descripción o referencia compuestas por varias columnas (se concatenan).
  - Referencia tomada de la descripción cuando no hay columna propia
    (`referencia_desde_descripcion`). Ej.: Banamex, Sabadell.
"""

import re
import unicodedata
from datetime import date, datetime, timedelta

from ..models import Movimiento
from .base import clean_text, parse_money
from .lectura import EXTENSIONES, leer_tabla

# Epoch base para seriales de Excel (coincide con DATEADD(DAY, n-1, '1899-12-31')).
_EPOCH_EXCEL = date(1899, 12, 31)


def normalizar_encabezado(texto: object) -> str:
    """'Fecha Operación' -> 'FECHA_OPERACION'."""
    s = "" if texto is None else str(texto)
    s = s.upper()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^A-Z0-9]+", "_", s)
    return s.strip("_")


def a_fecha(valor: object) -> date | None:
    """datetime/date directo, serial de Excel (número) o texto dd/mm/yyyy."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, (int, float)):
        return _EPOCH_EXCEL + timedelta(days=int(valor) - 1)
    texto = str(valor).strip().strip("'").replace("_", "")
    if texto.replace(".", "", 1).isdigit() and "/" not in texto and "-" not in texto:
        return _EPOCH_EXCEL + timedelta(days=int(float(texto)) - 1)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y-%m-%d",
                "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    # Último recurso: fecha con hora u otro sufijo -> tomar los primeros 10 chars.
    cabeza = texto[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(cabeza, fmt).date()
        except ValueError:
            continue
    return None


def _texto(valor: object) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


class BancoColumnasExcel:
    """Configuración + lectura de un banco cuyo estado de cuenta es .xlsx.

    `firma` es el conjunto de encabezados (normalizados) que deben estar TODOS
    presentes para reconocer el banco (y ninguno de `firma_ausente`). Las `cols_*`
    son los encabezados aceptados por campo canónico.
    """

    def __init__(
        self,
        nombre: str,
        *,
        firma: set[str],
        cols_fecha: set[str],
        cols_descripcion: set[str],
        descripcion_orden: tuple[str, ...] = (),
        cols_referencia: set[str] = frozenset(),
        cols_abono: set[str] = frozenset(),
        cols_cargo: set[str] = frozenset(),
        cols_importe: set[str] = frozenset(),
        cols_tipo: set[str] = frozenset(),
        cols_saldo: set[str] = frozenset(),
        firma_ausente: set[str] = frozenset(),
        fila_encabezado: int = 1,
        solo_abonos: bool = True,
        abono_es_positivo: bool = True,
        naturaleza_por_tipo: bool = False,
        referencia_desde_descripcion: bool = False,
        en_conciliacion: bool = True,
    ) -> None:
        self.nombre = nombre
        self.firma = set(firma)
        self.firma_ausente = set(firma_ausente)
        self.cols_fecha = set(cols_fecha)
        self.cols_descripcion = set(cols_descripcion)
        # Orden de PREFERENCIA (no unión): si se define, la descripción es la PRIMERA
        # de estas columnas cuya celda NO venga vacía en la fila (fallback por fila).
        # Ej. BBVA RSM: ("REFERENCIA_AMPLIADA", "CONCEPTO"). Vacío = comportamiento
        # normal (concatenar todas las cols_descripcion).
        self.descripcion_orden = tuple(descripcion_orden)
        self.cols_referencia = set(cols_referencia)
        self.cols_abono = set(cols_abono)
        self.cols_cargo = set(cols_cargo)
        self.cols_importe = set(cols_importe)
        self.cols_tipo = set(cols_tipo)
        self.cols_saldo = set(cols_saldo)
        self.fila_encabezado = fila_encabezado
        self.solo_abonos = solo_abonos
        self.abono_es_positivo = abono_es_positivo
        self.naturaleza_por_tipo = naturaleza_por_tipo
        self.referencia_desde_descripcion = referencia_desde_descripcion
        # Metadata (punto #2): si se lista o no en el selector de conciliaciones.
        self.en_conciliacion = en_conciliacion

    # --- Detección --------------------------------------------------------------

    def _encabezados(self, path: str) -> set[str]:
        filas = leer_tabla(path)
        headers: set[str] = set()
        for fila in filas[: max(5, self.fila_encabezado)]:
            for celda in fila:
                h = normalizar_encabezado(celda)
                if h:
                    headers.add(h)
        return headers

    def detect(self, path: str) -> bool:
        if not path.lower().endswith(tuple("." + e for e in EXTENSIONES)):
            return False
        try:
            headers = self._encabezados(path)
        except Exception:
            return False
        if self.firma_ausente & headers:
            return False
        return bool(self.firma) and self.firma <= headers

    # --- Lectura ----------------------------------------------------------------

    def parse(self, path: str) -> list[Movimiento]:
        filas = leer_tabla(path)
        if len(filas) < self.fila_encabezado:
            return []
        header = filas[self.fila_encabezado - 1]
        headers_norm = [normalizar_encabezado(c) for c in header]
        idx = self._indices(headers_norm)

        movimientos: list[Movimiento] = []
        for fila in filas[self.fila_encabezado:]:
            if all(c is None or (isinstance(c, str) and not c.strip()) for c in fila):
                continue
            mov = self._fila_a_movimiento(fila, idx)
            if mov is None:
                continue
            movimientos.append(mov)
        return movimientos

    def _indices(self, headers_norm: list[str]) -> dict:
        def primero(cols: set[str]):
            for i, h in enumerate(headers_norm):
                if h in cols:
                    return i
            return None

        def todos(cols: set[str]):
            return [i for i, h in enumerate(headers_norm) if h in cols]

        return {
            "fecha": primero(self.cols_fecha),
            "descripcion": todos(self.cols_descripcion),
            # Índices en el ORDEN de preferencia declarado (para el fallback por fila).
            "descripcion_orden": [
                headers_norm.index(n) for n in self.descripcion_orden if n in headers_norm
            ],
            "referencia": todos(self.cols_referencia),
            "abono": primero(self.cols_abono),
            "cargo": primero(self.cols_cargo),
            "importe": primero(self.cols_importe),
            "tipo": primero(self.cols_tipo),
            "saldo": primero(self.cols_saldo),
        }

    def _celda(self, fila: tuple, i):
        return fila[i] if i is not None and i < len(fila) else None

    def _unir(self, fila: tuple, indices: list[int]) -> str:
        partes = [_texto(self._celda(fila, i)).strip() for i in indices]
        return " ".join(p for p in partes if p)

    def _descripcion(self, fila: tuple, idx: dict) -> str:
        """Descripción de la fila. Con `descripcion_orden` toma la PRIMERA columna no
        vacía (fallback por fila, ej. BBVA RSM: Referencia Ampliada -> Concepto); si
        no, concatena todas las cols_descripcion."""
        for i in idx["descripcion_orden"]:
            val = _texto(self._celda(fila, i)).strip()
            if val:
                return clean_text(val)
        return clean_text(self._unir(fila, idx["descripcion"]))

    def _importe_naturaleza(self, fila: tuple, idx: dict):
        if self.naturaleza_por_tipo and idx["tipo"] is not None:
            valor = parse_money(_texto(self._celda(fila, idx["importe"])))
            if not valor:
                return None
            tipo = _texto(self._celda(fila, idx["tipo"])).upper()
            return abs(valor), ("A" if "ABONO" in tipo else "C")
        if idx["importe"] is not None:  # columna única con signo
            valor = parse_money(_texto(self._celda(fila, idx["importe"])))
            if not valor:
                return None
            if self.abono_es_positivo:
                naturaleza = "A" if valor >= 0 else "C"
            else:
                naturaleza = "A" if valor <= 0 else "C"
            return abs(valor), naturaleza
        abono = parse_money(_texto(self._celda(fila, idx["abono"])))
        cargo = parse_money(_texto(self._celda(fila, idx["cargo"])))
        importe = abono if abono else cargo
        if not importe:
            return None
        return abs(importe), ("A" if abono else "C")

    def _fila_a_movimiento(self, fila: tuple, idx: dict) -> Movimiento | None:
        res = self._importe_naturaleza(fila, idx)
        if res is None:
            return None
        importe, naturaleza = res
        if self.solo_abonos and naturaleza != "A":
            return None

        descripcion = self._descripcion(fila, idx)
        if idx["referencia"]:
            referencia = clean_text(self._unir(fila, idx["referencia"]))
        elif self.referencia_desde_descripcion:
            referencia = descripcion
        else:
            referencia = ""

        saldo_txt = _texto(self._celda(fila, idx["saldo"]))
        return Movimiento(
            banco=self.nombre,
            fecha=a_fecha(self._celda(fila, idx["fecha"])),
            descripcion=descripcion,
            referencia=referencia,
            concepto="",
            cargo=importe if naturaleza == "C" else 0.0,
            abono=importe if naturaleza == "A" else 0.0,
            saldo=parse_money(saldo_txt) if saldo_txt else None,
            texto_busqueda=" ".join(x for x in (descripcion, referencia) if x),
        )


# --- Configuraciones por banco (formatos .xlsx de portal/conciliación) ----------
# BanBajío NO está aquí: su módulo parsers/bajio.py ya lee su .xlsx (con lógica
# propia: salta COMPENSACION, etc.), y esa lógica se prefiere.
BANCOS_COLUMNAS: list[BancoColumnasExcel] = [
    # BBVA RSM/COBRANZA: encabezado en la fila 2 (la 1 trae "Cuenta | <clabe>").
    # Columnas: Fecha Operación | Concepto | Referencia | Referencia Ampliada | Cargo
    # | Abono | Saldo. Match por Referencia Ampliada y, si esa celda viene vacía en la
    # fila, por Concepto (fallback). Importe en Cargo/Abono (solo abonos).
    BancoColumnasExcel(
        "BBVA",
        firma={"FECHA_OPERACION", "CONCEPTO"},
        fila_encabezado=2,
        cols_fecha={"FECHA_OPERACION"},
        cols_descripcion={"REFERENCIA_AMPLIADA", "CONCEPTO"},
        descripcion_orden=("REFERENCIA_AMPLIADA", "CONCEPTO"),
        cols_referencia={"REFERENCIA"}, cols_abono={"ABONO"}, cols_cargo={"CARGO"}, cols_saldo={"SALDO"},
    ),
    # BBVA SPEI/COBRANZA (movimientos recibidos de otros bancos): encabezado en la
    # fila 2 (la 1 trae "Cuenta | <clabe>"). Importe siempre positivo = abono. El
    # texto de match es "Concepto de pago" (lo que teclea el cliente: folios F/CLN);
    # "Referencia" queda como respaldo.
    BancoColumnasExcel(
        "BBVA",
        firma={"CONCEPTO_DE_PAGO", "CLAVE_DE_RASTREO", "CUENTA_ORDENANTE"},
        fila_encabezado=2,
        cols_fecha={"FECHA"},
        cols_descripcion={"CONCEPTO_DE_PAGO"},
        cols_referencia={"REFERENCIA"},
        cols_importe={"IMPORTE"},
        cols_saldo={"SALDO"},
        abono_es_positivo=True,
    ),
    # --- Bancos INFERIDOS del SP, aún NO validados con archivo real -----------------
    # Van con en_conciliacion=False: NO se listan en el selector, pero la
    # autodetección SÍ los reconoce para avisar al usuario que se comunique a validar
    # el formato. Al confirmar un formato con archivo real, poner en_conciliacion=True
    # (o migrarlo a su módulo, como BBVA/Banorte/Santander/BanRegio/BanBajío).
    BancoColumnasExcel(
        "HSBC",
        firma={"FECHA_VALOR", "IMPORTE_DE_CREDITO"},
        cols_fecha={"FECHA_VALOR"}, cols_descripcion={"DESCRIPCION"},
        cols_referencia={"REFERENCIA_BANCARIA", "REFERENCIABANCARIA"},
        cols_abono={"IMPORTE_DE_CREDITO"}, cols_cargo={"IMPORTE_DEL_DEBITO"}, cols_saldo={"SALDO"},
        en_conciliacion=False,
    ),
    BancoColumnasExcel(
        "SABADELL",
        firma={"FECHA_VALOR", "ABONO", "CARGO"},
        cols_fecha={"FECHA_VALOR"}, cols_descripcion={"REFERENCIA"}, cols_referencia={"REFERENCIA"},
        cols_abono={"ABONO"}, cols_cargo={"CARGO"}, cols_saldo={"SALDO"},
        en_conciliacion=False,
    ),
    BancoColumnasExcel(
        "SCOTIABANK",
        firma={"IMPORTE", "TIPO"},
        cols_fecha={"FECHA"}, cols_descripcion={"LEYENDA", "LEYENDAB", "LEYENDA_B"},
        cols_referencia={"REFERENCIA_NUMERICA", "REFERENCIANUMERICA"},
        cols_importe={"IMPORTE"}, cols_tipo={"TIPO"}, cols_saldo={"SALDO"}, naturaleza_por_tipo=True,
        en_conciliacion=False,
    ),
    BancoColumnasExcel(
        "BANCOPPEL",
        firma={"CONCEPTO", "IMPORTE"},
        cols_fecha={"FECHA"}, cols_descripcion={"CONCEPTO"}, cols_referencia={"REFERENCIA"},
        cols_importe={"IMPORTE"}, cols_saldo={"SALDO"}, abono_es_positivo=True,
        en_conciliacion=False,
    ),
    BancoColumnasExcel(
        "INTERCAM",
        firma={"REFERENCIA", "CARGOS", "ABONOS"},
        cols_fecha={"FECHA"}, cols_descripcion={"DESCRIPCION"}, cols_referencia={"REFERENCIA"},
        cols_abono={"ABONOS"}, cols_cargo={"CARGOS"}, cols_saldo={"SALDO"},
        en_conciliacion=False,
    ),
    BancoColumnasExcel(
        "BANAMEX",
        firma={"DEPOSITOS", "RETIROS", "DESCRIPCION"}, firma_ausente={"REFERENCIA"},
        cols_fecha={"FECHA"}, cols_descripcion={"DESCRIPCION"},
        cols_abono={"DEPOSITOS"}, cols_cargo={"RETIROS"}, cols_saldo={"SALDO"},
        referencia_desde_descripcion=True,
        en_conciliacion=False,
    ),
    # BX / Ve por Más comparten layout (Depósitos/Retiros + Referencia) inferido del SP.
    BancoColumnasExcel(
        "BX",
        firma={"REFERENCIA", "DEPOSITOS", "RETIROS"},
        cols_fecha={"FECHA"}, cols_descripcion={"DESCRIPCION"}, cols_referencia={"REFERENCIA"},
        cols_abono={"DEPOSITOS"}, cols_cargo={"RETIROS"}, cols_saldo={"SALDO"},
        en_conciliacion=False,
    ),
    BancoColumnasExcel(
        "VE POR MAS",
        firma={"REFERENCIA", "DEPOSITOS", "RETIROS"},
        cols_fecha={"FECHA"}, cols_descripcion={"DESCRIPCION"}, cols_referencia={"REFERENCIA"},
        cols_abono={"DEPOSITOS"}, cols_cargo={"RETIROS"}, cols_saldo={"SALDO"},
        en_conciliacion=False,
    ),
]
