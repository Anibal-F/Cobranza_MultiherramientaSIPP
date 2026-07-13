"""Parser del estado de cuenta de BBVA.

BBVA descarga los movimientos en DOS archivos distintos (banca en línea), ambos
en formato **SpreadsheetML** (XML de Excel 2003, `<?mso-application
progid="Excel.Sheet"?>`) con extensión .xls. openpyxl no los lee; se parsean con
xml.etree (nativo).

1. Layout INTERNO ("movimientos del mismo banco"): pagos/transferencias hechas
   desde otra cuenta BBVA. Encabezado:
       Fecha Operación | Concepto | Referencia | Referencia Ampliada | Cargo | Abono | Saldo

2. Layout EXTERNO ("movimientos de otros bancos"): SPEI recibidos de bancos que
   NO son BBVA. Encabezado (columnas SPEI):
       Fecha | Referencia numerica | Concepto de codigo de leyenda | Referencia |
       Concepto de pago | Importe | Saldo | Banco ordenante | Nombre ordenante |
       Cuenta ordenante | Banco beneficiario | Nombre beneficiario |
       Cuenta beneficiario | Clave de rastreo | Estado del pago | Motivo de devolucion

En ambos se procesan solo los ABONOS/importes positivos (cobros); cargos,
comisiones y SPEI enviados (importe negativo) se ignoran.
"""

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from ..models import Movimiento
from ..textutils import normalizar

BANCO = "BBVA"
# Si True, se lista en el selector de Conciliaciones Bancarias (ver santander.py).
EN_CONCILIACION = True
_NS = "urn:schemas-microsoft-com:office:spreadsheet"

# Etiquetas de los dos layouts, para que la UI pueda ofrecer subir el archivo
# complementario ("se detectó el interno, ¿desea subir el externo?").
LAYOUT_INTERNO = "interno"
LAYOUT_EXTERNO = "externo"

# Marcadores del encabezado (normalizados) que identifican la tabla y distinguen
# cada layout de BBVA de otros .xls/.xml. Los dos conjuntos son disjuntos: el
# interno tiene 'FECHA OPERACION'/'ABONO'; el externo tiene 'IMPORTE'/'CUENTA
# ORDENANTE', que el interno no trae.
_HEADERS_INTERNO = {"FECHA OPERACION", "CONCEPTO", "ABONO"}
_HEADERS_EXTERNO = {"FECHA", "IMPORTE", "CUENTA ORDENANTE"}


def _q(tag: str) -> str:
    return f"{{{_NS}}}{tag}"


def _key(valor) -> str:
    return normalizar(str(valor if valor is not None else "")).upper()


def _monto(valor) -> float:
    """Convierte a float. Los importes de BBVA vienen como número (incluye
    notación científica en el saldo, ej. '2.417856118E7')."""
    if valor is None or str(valor).strip() == "":
        return 0.0
    try:
        return float(str(valor).strip())
    except ValueError:
        return 0.0


def _fecha(valor):
    """Ambos layouts traen la fecha como 'YYYY-MM-DD' (el externo con hora:
    'YYYY-MM-DD HH:MM:SS'); se toman los primeros 10 caracteres."""
    if not valor:
        return None
    texto = str(valor).strip()[:10]
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


# BBVA emite XML inválido: los '&' de las razones sociales van SIN escapar
# (ej. "A&G TRUCKING GROUP") en vez de '&amp;', y ET revienta con "not well-formed".
# Se re-escapan los '&' que no formen ya una entidad válida antes de parsear.
_AMP_SUELTO_RE = re.compile(rb"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#[xX][0-9a-fA-F]+);)")


def _leer_xml(path: str):
    """Parsea el SpreadsheetML del archivo, saneando los '&' sueltos que BBVA deja
    sin escapar. Se trabaja sobre bytes para no chocar con la declaración de
    encoding del documento."""
    with open(path, "rb") as f:
        crudo = f.read()
    return ET.fromstring(_AMP_SUELTO_RE.sub(b"&amp;", crudo))


def _filas(path: str) -> list[list]:
    """Devuelve las filas de la primera hoja como listas de celdas (str|None),
    respetando ss:Index (celdas omitidas por estar vacías)."""
    root = _leer_xml(path)
    tabla = root.find(".//" + _q("Worksheet") + "/" + _q("Table"))
    if tabla is None:
        return []
    filas: list[list] = []
    for row in tabla.findall(_q("Row")):
        vals: list = []
        col = 0
        for celda in row.findall(_q("Cell")):
            idx = celda.get(_q("Index"))
            if idx:
                col = int(idx) - 1  # ss:Index es 1-based
            while len(vals) <= col:
                vals.append(None)
            data = celda.find(_q("Data"))
            vals[col] = data.text if data is not None else None
            col += 1
        filas.append(vals)
    return filas


def _detectar_layout(filas: list[list]) -> tuple[str | None, int | None]:
    """Devuelve (layout, índice de la fila de encabezado) o (None, None)."""
    for i, fila in enumerate(filas):
        claves = {_key(v) for v in fila if v is not None}
        if _HEADERS_INTERNO.issubset(claves):
            return LAYOUT_INTERNO, i
        if _HEADERS_EXTERNO.issubset(claves):
            return LAYOUT_EXTERNO, i
    return None, None


def layout(path: str) -> str | None:
    """Etiqueta del layout de BBVA del archivo (LAYOUT_INTERNO/LAYOUT_EXTERNO) o
    None si no es un archivo de BBVA. Lo usa la UI para ofrecer el complemento."""
    try:
        return _detectar_layout(_filas(path))[0]
    except Exception:
        return None


def detect(path: str) -> bool:
    if not path.lower().endswith((".xls", ".xml")):
        return False
    try:
        return _detectar_layout(_filas(path))[0] is not None
    except Exception:
        return False


def _columna(encabezados: list[str]):
    def col(nombre: str) -> int | None:
        try:
            return encabezados.index(nombre)
        except ValueError:
            return None

    return col


def _celda(fila: list, i: int | None) -> str:
    if i is None or i >= len(fila) or fila[i] is None:
        return ""
    return str(fila[i]).strip()


def _parse_interno(filas: list[list], enc: int) -> list[Movimiento]:
    encabezados = [_key(v) for v in filas[enc]]
    col = _columna(encabezados)
    c_fecha = col("FECHA OPERACION")
    c_concepto = col("CONCEPTO")
    c_ref = col("REFERENCIA")
    c_amp = col("REFERENCIA AMPLIADA")
    c_cargo = col("CARGO")
    c_abono = col("ABONO")
    c_saldo = col("SALDO")

    movimientos: list[Movimiento] = []
    for fila in filas[enc + 1:]:
        abono = _monto(_celda(fila, c_abono))
        if abono <= 0:
            continue  # solo cobros (abonos); cargos/comisiones se ignoran

        concepto = _celda(fila, c_concepto)
        ampliada = _celda(fila, c_amp)
        referencia = _celda(fila, c_ref)
        descripcion = " ".join(x for x in (concepto, ampliada) if x)

        # Compensaciones por desfase de SPEI: ajustes internos de Banxico (centavos),
        # no cobros. Se busca en TODA la fila, no solo en la descripción: BBVA a veces
        # parte la leyenda entre columnas y deja "COMPENSACION DE" en Referencia con
        # "MORA SPEI NORMA BANXICO" en Concepto.
        fila_texto = " ".join(x for x in (concepto, referencia, ampliada) if x)
        if "COMPENSACION" in normalizar(fila_texto).upper():
            continue

        movimientos.append(
            Movimiento(
                banco=BANCO,
                fecha=_fecha(_celda(fila, c_fecha)),
                descripcion=descripcion,
                referencia=referencia,
                concepto=concepto,
                cargo=_monto(_celda(fila, c_cargo)),
                abono=abono,
                saldo=_monto(_celda(fila, c_saldo)) or None,
                # Concepto + Referencia (cuenta ordenante) + Referencia Ampliada
                # (folios FMZ/FLM, banco, etc.) para el match por cuenta/folio.
                texto_busqueda=" ".join(x for x in (concepto, referencia, ampliada) if x),
            )
        )
    return movimientos


def _parse_externo(filas: list[list], enc: int) -> list[Movimiento]:
    encabezados = [_key(v) for v in filas[enc]]
    col = _columna(encabezados)
    c_fecha = col("FECHA")
    c_leyenda = col("CONCEPTO DE CODIGO DE LEYENDA")
    c_ref = col("REFERENCIA")
    c_conc_pago = col("CONCEPTO DE PAGO")
    c_importe = col("IMPORTE")
    c_saldo = col("SALDO")
    c_banco_ord = col("BANCO ORDENANTE")
    c_nombre_ord = col("NOMBRE ORDENANTE")
    c_cuenta_ord = col("CUENTA ORDENANTE")
    c_clave = col("CLAVE DE RASTREO")

    movimientos: list[Movimiento] = []
    for fila in filas[enc + 1:]:
        # Solo abonos: el importe negativo son SPEI ENVIADOS (traspasos de salida).
        importe = _monto(_celda(fila, c_importe))
        if importe <= 0:
            continue

        leyenda = _celda(fila, c_leyenda)
        concepto_pago = _celda(fila, c_conc_pago)
        nombre_ord = _celda(fila, c_nombre_ord)
        cuenta_ord = _celda(fila, c_cuenta_ord)
        banco_ord = _celda(fila, c_banco_ord)
        clave = _celda(fila, c_clave)

        # Concepto = leyenda ("SPEI RECIBIDO BANORTE") + concepto de pago (lo que
        # teclea el cliente: "F 125539", "CLN-009 La Paz"), para que la
        # identificación por folio (FLM/FMZ) tenga todo el texto disponible.
        concepto = " ".join(x for x in (leyenda, concepto_pago) if x)
        descripcion = " ".join(x for x in (leyenda, nombre_ord, concepto_pago) if x)

        movimientos.append(
            Movimiento(
                banco=BANCO,
                fecha=_fecha(_celda(fila, c_fecha)),
                # La clave de rastreo es el identificador SPEI estable (único por
                # transacción); es lo que el buzón H2H de SIPP muestra para los
                # movimientos interbancarios, así que se usa como referencia para
                # emparejar en la previsualización.
                referencia=clave,
                descripcion=descripcion,
                concepto=concepto,
                cargo=0.0,
                abono=importe,
                saldo=_monto(_celda(fila, c_saldo)) or None,
                # Cuenta ordenante (CLABE 18) + nombre + banco ordenante + folios
                # + clave de rastreo → match por cuenta/folio/nombre.
                texto_busqueda=" ".join(
                    x for x in (concepto, cuenta_ord, nombre_ord, banco_ord, clave) if x
                ),
                bbva_externo=True,
            )
        )
    return movimientos


def parse(path: str) -> list[Movimiento]:
    filas = _filas(path)
    tipo, enc = _detectar_layout(filas)
    if tipo == LAYOUT_INTERNO:
        return _parse_interno(filas, enc)
    if tipo == LAYOUT_EXTERNO:
        return _parse_externo(filas, enc)
    return []


# ── Formato ACUMULADO (RSMACUM / SPEIACUM) ────────────────────────────────
# Además del corte DIARIO, BBVA ofrece un archivo ACUMULADO con varios días. Se usa
# para capturar el dinero que cayó fuera de horario laboral (17:30 → 08:30 del día
# siguiente), así que en la práctica solo interesan HOY y el día hábil anterior; lo
# más viejo ya se concilió en cortes previos. La estructura es idéntica a la diaria
# (mismos encabezados), por eso no hay un layout nuevo: se distingue por abarcar más
# de una fecha.


def dia_habil_anterior(hoy: date) -> date:
    """Día hábil inmediatamente anterior a `hoy`.

    En LUNES el día hábil anterior es el VIERNES: la ventana de fuera de horario
    abarca viernes 17:30 → lunes 08:30, así que el fin de semana (sábado y domingo)
    queda dentro del rango a conservar. Si se tomara "ayer" de calendario, en lunes
    se perderían los movimientos del viernes por la tarde y del sábado."""
    dow = hoy.weekday()  # lunes=0 ... domingo=6
    if dow == 0:  # lunes → viernes
        dias = 3
    elif dow == 6:  # domingo → viernes
        dias = 2
    else:  # sábado → viernes; martes-viernes → ayer
        dias = 1
    return hoy - timedelta(days=dias)


def recortar_acumulado(
    movimientos: list[Movimiento], hoy: date | None = None
) -> tuple[list[Movimiento], list[Movimiento], date | None]:
    """Si el archivo abarca MÁS DE UN DÍA (formato acumulado), conserva solo los
    movimientos desde el día hábil anterior a `hoy` en adelante. Devuelve
    (conservados, omitidos, fecha_de_corte).

    Un archivo de un solo día (formato diario) se devuelve intacto y con corte None:
    así, cargar un diario viejo a propósito sigue funcionando.

    Los movimientos sin fecha legible NO se descartan (se conservan): es preferible
    revisarlos de más a perder un cobro por una fecha que no se pudo parsear."""
    fechas = {m.fecha for m in movimientos if m.fecha}
    if len(fechas) <= 1:
        return list(movimientos), [], None

    corte = dia_habil_anterior(hoy or date.today())
    conservados = [m for m in movimientos if m.fecha is None or m.fecha >= corte]
    omitidos = [m for m in movimientos if m.fecha is not None and m.fecha < corte]
    return conservados, omitidos, corte
