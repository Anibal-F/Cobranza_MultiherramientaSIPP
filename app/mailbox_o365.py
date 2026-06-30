import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv
from O365 import Account, FileSystemTokenBackend

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_dotenv_path = os.path.join(ROOT_DIR, ".env")
load_dotenv(_dotenv_path if os.path.exists(_dotenv_path) else None)

CLIENT_ID = os.getenv("GRAPH_CLIENT_ID")
TENANT_ID = os.getenv("GRAPH_TENANT_ID")
TOKEN_FILENAME = "o365_token.txt"


@dataclass
class CorreoResumen:
    id_correo: str
    asunto: str
    remitente: str
    fecha: Optional[datetime]
    tiene_adjuntos: bool
    mensaje: Any  # O365.message.Message, conservado para descargar adjuntos bajo demanda


def obtener_cuenta() -> Optional[Account]:
    """Devuelve la cuenta autenticada usando el token generado por auth_o365.py.
    Si no existe el token o ya expiró, regresa None: hay que correr
    `python auth_o365.py` en una terminal para (re)generarlo."""
    if not CLIENT_ID or not TENANT_ID:
        return None

    token_backend = FileSystemTokenBackend(token_path=ROOT_DIR, token_filename=TOKEN_FILENAME)
    account = Account(
        (CLIENT_ID, ""),
        auth_flow="authorization",
        tenant_id=TENANT_ID,
        token_backend=token_backend,
    )
    return account if account.is_authenticated else None


def listar_correos(
    account: Account, limite: int = 25, contiene_en_asunto: Optional[str] = None
) -> list[CorreoResumen]:
    """Lista los correos más recientes de la bandeja de entrada (sin descargar
    adjuntos todavía; eso se hace bajo demanda con descargar_adjuntos).

    Si se da contiene_en_asunto, se descartan los correos cuyo asunto no la
    contenga (sin distinguir mayúsculas/minúsculas)."""
    bandeja = account.mailbox().inbox_folder()
    mensajes = bandeja.get_messages(limit=limite)

    filtro = contiene_en_asunto.lower() if contiene_en_asunto else None

    resumenes: list[CorreoResumen] = []
    for mensaje in mensajes:
        asunto = mensaje.subject or "(sin asunto)"
        if filtro and filtro not in asunto.lower():
            continue
        remitente = mensaje.sender.address if mensaje.sender else ""
        resumenes.append(
            CorreoResumen(
                id_correo=mensaje.object_id,
                asunto=asunto,
                remitente=remitente,
                fecha=mensaje.received,
                tiene_adjuntos=mensaje.has_attachments,
                mensaje=mensaje,
            )
        )
    return resumenes


def obtener_cuerpo(mensaje: Any) -> str:
    """Regresa el texto plano del cuerpo del correo (sin tags HTML)."""
    try:
        texto = mensaje.get_body_text()
    except Exception:
        texto = None
    return (texto or mensaje.body_preview or "").strip()


def descargar_adjuntos(mensaje: Any, destino_dir: str) -> list[str]:
    """Descarga todos los adjuntos del correo a destino_dir y regresa las rutas
    guardadas."""
    if not mensaje.has_attachments:
        return []

    mensaje.attachments.download_attachments()

    rutas: list[str] = []
    for adjunto in mensaje.attachments:
        adjunto.save(location=destino_dir)
        rutas.append(os.path.join(destino_dir, adjunto.name))
    return rutas
