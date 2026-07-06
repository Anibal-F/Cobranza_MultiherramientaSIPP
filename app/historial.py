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
    subido_sipp: bool = False,
) -> dict:
    return {
        "id": registro_id,
        "fecha": fecha,
        "hora": hora,
        "banco": banco,
        "archivo": archivo,
        "ruta_csv": ruta_csv,
        "empresa_clave": empresa_clave,
        "subido_sipp": subido_sipp,
        "total_abonado": round(sum(m.abono for m in movimientos), 2),
        "num_movimientos": len(movimientos),
        "num_identificados": sum(1 for m in movimientos if m.identificado),
        "movimientos": [movimiento_a_dict(m) for m in movimientos],
    }


# ── Deduplicación incremental de extracciones ─────────────────────────────
# Los CSV del banco son acumulativos durante el día: cada corte trae lo anterior
# + lo nuevo. Para no re-subir a SIPP lo ya cargado, se identifica cada
# movimiento por una clave estable y se comparan contra los bloques ya marcados
# como subidos a SIPP.

def clave_dedup(banco: str, referencia: str, abono, fecha_iso: str = "", descripcion: str = "") -> str:
    # Llave: banco + referencia + descripción + monto. NO se incluye la fecha: el
    # parser de algunos bancos (Banorte "Cuentas de Cheques") la extrae de forma
    # inconsistente entre cortes, y el mismo movimiento generaría llaves distintas.
    # La referencia (nº de movimiento) ya es única; la descripción + monto
    # desambiguan cuando no hay referencia.
    ref = (referencia or "").strip()
    desc = " ".join((descripcion or "").split())[:80]  # normaliza espacios
    try:
        monto = float(abono or 0)
    except (TypeError, ValueError):
        monto = 0.0
    return f"{(banco or '').upper()}|{ref}|{desc}|{monto:.2f}"


def clave_movimiento(m: Movimiento) -> str:
    return clave_dedup(
        m.banco, m.referencia, m.abono, m.fecha.isoformat() if m.fecha else "", m.descripcion
    )


def clave_movimiento_dict(d: dict) -> str:
    return clave_dedup(
        d.get("banco", ""), d.get("referencia", ""), d.get("abono"), d.get("fecha") or "", d.get("descripcion", "")
    )


_clave_movimiento_dict = clave_movimiento_dict  # alias interno


def claves_subidas(
    registros: list[dict], banco: str, excluir_id: str | None = None, solo_subidos: bool = False
) -> set[str]:
    """Conjunto de claves de movimientos ya vistos en extracciones PREVIAS del
    mismo banco (unión de sus movimientos), para detectar duplicados en cortes
    acumulativos. Con `solo_subidos=True` considera únicamente los bloques
    marcados como subidos a SIPP. `excluir_id` omite un registro (p. ej. el que
    se está creando/actualizando)."""
    banco_u = (banco or "").upper()
    claves: set[str] = set()
    for r in registros:
        if solo_subidos and not r.get("subido_sipp"):
            continue
        if r.get("id") == excluir_id:
            continue
        if (r.get("banco", "") or "").upper() != banco_u:
            continue
        for d in r.get("movimientos", []):
            claves.add(_clave_movimiento_dict(d))
    return claves
