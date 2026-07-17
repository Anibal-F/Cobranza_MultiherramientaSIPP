"""Leyendas configurables para identificar DEVOLUCIONES DE CHEQUE.

Un movimiento del banco se aparta como devolución de cheque (antes de conciliar)
si su texto CONTIENE alguna de estas leyendas. Los usuarios aún no dan la lista
exacta, así que se guarda en un JSON editable desde la UI (pestaña Conciliaciones)
en lugar de estar hardcodeada.

El match es por SUBSTRING NORMALIZADO (mayúsculas, sin acentos, solo alfanumérico),
igual que el resto de la conciliación: la leyedna "DEV CHEQUE" apartaría un
movimiento cuyo texto contenga "…dev. cheque…".
"""

import json
import os

from ..textutils import normalizar

# El JSON vive en la raíz del proyecto (como historial_extracciones.json). Se ignora
# en git: es configuración por máquina, no código (ver .gitignore).
_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUTA_DEFECTO = os.path.join(_RAIZ, "leyendas_cheque.json")

# Semilla usada cuando aún no existe el JSON (equivale a la regex vieja "CHEQUE").
LEYENDAS_DEFECTO: list[str] = ["CHEQUE"]


def _limpiar(leyendas: list[str]) -> list[str]:
    """Quita vacías/espacios y elimina duplicados por forma normalizada,
    conservando el texto original de la PRIMERA aparición (para mostrarlo tal cual)."""
    vistas: set[str] = set()
    salida: list[str] = []
    for ley in leyendas:
        texto = (ley or "").strip()
        clave = normalizar(texto)
        if not clave or clave in vistas:
            continue
        vistas.add(clave)
        salida.append(texto)
    return salida


def cargar_leyendas(path: str = RUTA_DEFECTO) -> list[str]:
    """Lee la lista de leyendas del JSON. Si no existe o está dañado, devuelve la
    semilla por defecto (no lanza)."""
    if not os.path.exists(path):
        return list(LEYENDAS_DEFECTO)
    try:
        with open(path, encoding="utf-8") as f:
            datos = json.load(f)
        if isinstance(datos, list):
            return _limpiar([str(x) for x in datos])
    except (json.JSONDecodeError, OSError):
        pass
    return list(LEYENDAS_DEFECTO)


def guardar_leyendas(leyendas: list[str], path: str = RUTA_DEFECTO) -> None:
    """Persiste la lista (limpiada) en el JSON."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_limpiar(leyendas), f, ensure_ascii=False, indent=2)


def es_devolucion(texto: str, leyendas: list[str]) -> bool:
    """True si el texto CONTIENE (normalizado) alguna de las leyendas. Lista vacía
    -> nunca aparta nada."""
    t = normalizar(texto)
    return any(n and n in t for n in (normalizar(l) for l in leyendas))
