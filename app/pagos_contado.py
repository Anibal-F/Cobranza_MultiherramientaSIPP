import re
from dataclasses import dataclass
from typing import Optional

from .extraccion_adjuntos import extraer_texto_adjunto
from .mailbox_o365 import CorreoResumen, descargar_adjuntos
from .matcher import PALABRAS_VACIAS
from .sucursales import normalizar_plaza
from .textutils import normalizar

LONGITUD_MINIMA_PALABRA_DISTINTIVA = 6

BANCOS_CONOCIDOS = [
    "AMERICAN EXPRESS",
    "BAJIO", "BAJÍO",
    "BANAMEX",
    "BANCOPPEL",
    "BANORTE",
    "BANREGIO",
    "BBVA BANCOMER", "BANCOMER", "BBVA",
    "BMONEX", "MONEX",
    "BMULTIVA", "MULTIVA",
    "HSBC",
    "INBURSA",
    "INTERCAM",
    "KAPITAL",
    "SABADELL",
    "SANTANDER",
    "SCOTIABANK",
    "VE POR MAS", "VEPORMAS",
]

_RE_MONTO = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")
_RE_PLAZA = re.compile(r"PLAZA\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ ]*)", re.IGNORECASE)
_RE_CLIENTE = re.compile(r"CLIENTE\s+(.+?)(?:\s*/|$)", re.IGNORECASE)


@dataclass
class PagoContadoExtraido:
    correo: CorreoResumen
    concepto: str = ""
    tipo_movimiento: str = ""  # "Anticipo" | "Contado" | ""
    banco_detectado: str = ""
    plaza: str = ""
    monto: Optional[float] = None
    cliente_texto: str = ""
    cliente_match: Optional[str] = None
    referencia: str = ""
    cuenta_bancaria: str = ""  # id_sipp de la cuenta destino (ver CUENTAS_BANCARIAS)
    ruta_adjunto: Optional[str] = None
    texto_adjunto: str = ""
    error: str = ""
    # Cruce con bloques bancarios: si este pago ya viene en un movimiento de una
    # extracción bancaria, NO se sube por el RPA de contado (se identifica en el
    # bloque). Se llena en la app al cruzar contra el historial.
    en_bloque_bancario: bool = False
    bloque_ref: str = ""       # descripción legible del bloque/movimiento
    bloque_id: str = ""        # id de la extracción en el historial
    bloque_clave: str = ""     # clave del movimiento bancario coincidente


def _detectar_tipo_movimiento(texto: str) -> str:
    """Para este flujo (correos ya filtrados por 'Contado' en el asunto),
    Contado siempre aplica; lo único que distingue un correo de otro es si
    ADEMÁS es un Anticipo. 'Contado' es el valor por default."""
    mayus = texto.upper()
    if "ANTICIPO" in mayus:
        return "Anticipo"
    return "Contado"


def _detectar_banco(texto: str) -> str:
    mayus = texto.upper()
    for banco in BANCOS_CONOCIDOS:
        if banco in mayus:
            return banco
    return ""


def _detectar_plaza(texto: str) -> str:
    coincidencia = _RE_PLAZA.search(texto)
    if not coincidencia:
        return ""
    return coincidencia.group(1).strip(" -/").title()


def _detectar_monto(texto: str) -> Optional[float]:
    coincidencia = _RE_MONTO.search(texto)
    if not coincidencia:
        return None
    limpio = coincidencia.group(1).replace(",", "")
    try:
        return float(limpio)
    except ValueError:
        return None


def _detectar_cliente_texto(texto: str) -> str:
    coincidencia = _RE_CLIENTE.search(texto)
    if not coincidencia:
        return ""
    return coincidencia.group(1).strip(" -/")


PALABRAS_TRANSACCIONALES = {
    "CONTADO", "ANTICIPO", "TRANSFERENCIA", "TRANSFERENCIAS", "CLIENTE",
    "PLAZA", "PAGO", "DEPOSITO", "REFERENCIA", "FAVOR", "APOYO", "APOYAR",
    "SALDO", "COMENTARIOS", "PENDIENTE",
}


def _sugerir_cliente_por_catalogo(
    texto: str,
    clientes_normalizados: list[tuple[str, str]],
    excluir: frozenset[str] = frozenset(),
) -> str:
    """Cuando el correo no trae la etiqueta 'CLIENTE X', intenta reconocer al
    cliente contra el catálogo (igual que match_movimientos_por_nombre para el
    CSV bancario): por nombre completo, o por una palabra larga y distintiva
    que solo aparezca en el nombre de un único cliente del catálogo.

    `excluir` debe incluir las palabras del banco/plaza ya detectados (ej.
    "TIJUANA", "BANORTE"), que de otro modo generan coincidencias falsas con
    clientes cuya razón social menciona esa misma ciudad o banco."""
    texto_norm = normalizar(texto)
    if not texto_norm:
        return ""

    coincidencias_exactas = {
        nombre_original
        for nombre_original, nombre_norm in clientes_normalizados
        if nombre_norm and nombre_norm in texto_norm
    }
    if len(coincidencias_exactas) == 1:
        return next(iter(coincidencias_exactas))

    palabras_texto = {
        palabra
        for palabra in texto_norm.split()
        if len(palabra) >= LONGITUD_MINIMA_PALABRA_DISTINTIVA
        and palabra not in PALABRAS_VACIAS
        and palabra not in PALABRAS_TRANSACCIONALES
        and palabra not in excluir
    }
    if not palabras_texto:
        return ""

    coincidencias: set[str] = set()
    for nombre_original, nombre_norm in clientes_normalizados:
        if palabras_texto & set(nombre_norm.split()):
            coincidencias.add(nombre_original)
            if len(coincidencias) > 1:
                return ""
    if len(coincidencias) == 1:
        return next(iter(coincidencias))
    return ""


def _sugerir_referencia(texto_adjunto: str) -> str:
    """Busca, en el texto extraído del comprobante, una etiqueta común de
    referencia/folio/clave de rastreo seguida de un valor. No es exhaustivo
    (los formatos de comprobante varían mucho entre bancos): es solo una
    sugerencia de partida; el usuario siempre confirma/corrige en la app."""
    # En comprobantes SPEI la "Clave de Rastreo" es el identificador
    # autoritativo, por eso va primero; "Referencia numérica" es el respaldo.
    # Capturas específicas de dígitos para evitar enganchar el siguiente label
    # (p. ej. "Confirmación") cuando el valor no quedó en la misma línea.
    # El orden importa: los labels compuestos ("referencia del lote",
    # "referencia numérica") van antes del genérico "referencia", que si no
    # capturaría la palabra siguiente ("del", "numérica") como valor.
    patrones = [
        r"clave\s+de\s+rastreo[:\s]+(\d+)",
        r"referencia\s+del\s+lote[:\s]+([A-Za-z0-9]+)",
        r"referencia\s+num[eé]rica[:\s]+(\d+)",
        r"ref\.?\s+de\s+operaci[oó]n[:\s]+([A-Za-z0-9]+)",
        r"referencia[:\s]+([A-Za-z0-9-]+)",
        r"folio[:\s]+([A-Za-z0-9-]+)",
        r"autorizaci[oó]n[:\s]+([A-Za-z0-9-]+)",
    ]
    for patron in patrones:
        coincidencia = re.search(patron, texto_adjunto, re.IGNORECASE)
        if coincidencia:
            return coincidencia.group(1).strip()
    return ""


def extraer_pago_contado(
    correo: CorreoResumen,
    cuerpo: str,
    clientes_normalizados: Optional[list[tuple[str, str]]] = None,
    sucursales: Optional[list[str]] = None,
) -> PagoContadoExtraido:
    """Construye una propuesta de "Pago de Contado" a partir del asunto/cuerpo
    de un correo ya filtrado por 'Contado'. No descarga el adjunto (eso se
    hace aparte, vía completar_con_adjunto, para no bloquear el listado
    inicial con OCR/PDF de todos los correos a la vez).

    Si se da clientes_normalizados intenta sugerir el cliente desde el catálogo.
    Si se da sucursales normaliza la plaza detectada contra el catálogo de
    sucursales (ej. 'Tijuan' → 'Tijuana')."""
    texto_completo = f"{correo.asunto}\n{cuerpo}"
    banco_detectado = _detectar_banco(texto_completo)
    plaza_raw = _detectar_plaza(texto_completo)
    plaza = normalizar_plaza(plaza_raw, sucursales) if sucursales else plaza_raw

    cliente_texto = _detectar_cliente_texto(texto_completo)
    cliente_match: Optional[str] = None
    if clientes_normalizados:
        if cliente_texto:
            # Hay etiqueta "CLIENTE X": resolver ese texto contra el catálogo.
            # Si cae de forma única, auto-confirmamos el cliente (el usuario ya
            # lo verifica visualmente; puede cambiarlo con el ícono de acción).
            cliente_match = _sugerir_cliente_por_catalogo(cliente_texto, clientes_normalizados) or None
        else:
            # Solo el asunto: el cuerpo suele traer firma/disclaimer largo cuyas
            # palabras generan coincidencias falsas con otros clientes del
            # catálogo y anulan la sugerencia (ambigüedad espuria).
            excluir = frozenset(normalizar(banco_detectado).split()) | frozenset(normalizar(plaza).split())
            # _sugerir_cliente_por_catalogo ya regresa el nombre del catálogo,
            # así que sirve como texto sugerido y como match confirmado.
            cliente_texto = _sugerir_cliente_por_catalogo(correo.asunto, clientes_normalizados, excluir)
            cliente_match = cliente_texto or None

    pago = PagoContadoExtraido(
        correo=correo,
        concepto=correo.asunto,
        tipo_movimiento=_detectar_tipo_movimiento(texto_completo),
        banco_detectado=banco_detectado,
        plaza=plaza,
        monto=_detectar_monto(texto_completo),
        cliente_texto=cliente_texto,
        cliente_match=cliente_match,
    )
    return pago


def completar_con_adjunto(pago: PagoContadoExtraido, destino_dir: str) -> None:
    """Descarga el primer adjunto del correo, le extrae texto (PDF real u OCR
    si es imagen) y sugiere una Referencia. Modifica `pago` en sitio."""
    try:
        rutas = descargar_adjuntos(pago.correo.mensaje, destino_dir)
    except Exception as ex:
        pago.error = f"No se pudo descargar el adjunto: {ex}"
        return

    if not rutas:
        pago.error = "El correo no tiene adjuntos."
        return

    pago.ruta_adjunto = rutas[0]
    pago.texto_adjunto = extraer_texto_adjunto(rutas[0])
    if pago.texto_adjunto:
        pago.referencia = _sugerir_referencia(pago.texto_adjunto)
    else:
        pago.error = "No se pudo extraer texto del adjunto; revisa la Referencia manualmente."
