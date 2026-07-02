"""Historial local de extracciones (conciliación bancaria).

Persiste en un JSON las últimas extracciones cargadas: metadatos (fecha/hora,
banco, total abonado, conteo de movimientos e identificados) y un snapshot
completo de los movimientos CON sus últimos cambios (cliente, cuenta, sucursal
declarada/por folio, folios manuales, etc.), para poder reabrir una extracción
tal como quedó.
"""

import json
import os
from dataclasses import asdict, fields
from datetime import date

from .models import Movimiento

_CAMPOS_MOVIMIENTO = {f.name for f in fields(Movimiento)}

# Cuántas extracciones se conservan (las más recientes primero).
MAX_REGISTROS = 50


def movimiento_a_dict(m: Movimiento) -> dict:
    d = asdict(m)
    d["fecha"] = m.fecha.isoformat() if m.fecha else None
    return d


def movimiento_desde_dict(d: dict) -> Movimiento:
    datos = {k: v for k, v in d.items() if k in _CAMPOS_MOVIMIENTO}
    fecha = datos.get("fecha")
    datos["fecha"] = date.fromisoformat(fecha) if fecha else None
    return Movimiento(**datos)


def cargar_historial(path: str) -> list[dict]:
    """Lee la lista de registros del JSON. Devuelve [] si no existe o está dañado."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def guardar_historial(path: str, registros: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registros[:MAX_REGISTROS], f, ensure_ascii=False, indent=2)


def construir_registro(
    registro_id: str,
    fecha: str,
    hora: str,
    banco: str,
    archivo: str,
    ruta_csv: str,
    empresa_clave: str,
    movimientos: list[Movimiento],
) -> dict:
    return {
        "id": registro_id,
        "fecha": fecha,
        "hora": hora,
        "banco": banco,
        "archivo": archivo,
        "ruta_csv": ruta_csv,
        "empresa_clave": empresa_clave,
        "total_abonado": round(sum(m.abono for m in movimientos), 2),
        "num_movimientos": len(movimientos),
        "num_identificados": sum(1 for m in movimientos if m.identificado),
        "movimientos": [movimiento_a_dict(m) for m in movimientos],
    }
