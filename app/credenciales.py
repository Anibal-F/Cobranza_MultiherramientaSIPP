"""Persistencia simple de las credenciales de SIPP para no re-teclearlas.

Se guardan en un archivo local (mismo patrón que o365_token.txt), ignorado por
git. NO es almacenamiento cifrado: la contraseña queda legible en disco, así que
solo debe usarse en equipos de confianza."""

import json
import os

_RUTA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sipp_credenciales.json"
)


def guardar_credenciales(usuario: str, password: str) -> None:
    try:
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump({"usuario": usuario, "password": password}, f)
    except OSError:
        pass


def cargar_credenciales() -> tuple[str, str]:
    """Regresa (usuario, password) guardados, o ("", "") si no hay nada."""
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            datos = json.load(f)
        return datos.get("usuario", ""), datos.get("password", "")
    except (OSError, json.JSONDecodeError, ValueError):
        return "", ""


def borrar_credenciales() -> None:
    try:
        os.remove(_RUTA)
    except OSError:
        pass
