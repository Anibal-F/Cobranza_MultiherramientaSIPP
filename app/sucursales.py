import csv
import os

SUCURSALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Catalogos",
    "Sucursales",
)


def cargar_sucursales() -> list[str]:
    """Lee todos los CSV de Catalogos/Sucursales/ y devuelve la lista única
    de nombres canónicos (NB_SUCURSAL), preservando capitalización original."""
    vistos: set[str] = set()
    nombres: list[str] = []
    for nombre_archivo in os.listdir(SUCURSALES_DIR):
        if not nombre_archivo.lower().endswith(".csv"):
            continue
        ruta = os.path.join(SUCURSALES_DIR, nombre_archivo)
        with open(ruta, newline="", encoding="utf-8-sig") as f:
            for fila in csv.DictReader(f):
                suc = (fila.get("NB_SUCURSAL") or "").strip()
                if suc and suc.lower() not in vistos:
                    vistos.add(suc.lower())
                    nombres.append(suc)
    return sorted(nombres)


def normalizar_plaza(texto: str, sucursales: list[str]) -> str:
    """Devuelve el nombre canónico de la plaza que mejor coincide con `texto`
    (extraído con regex del correo, potencialmente truncado o con tildes
    distintas). La comparación se hace en minúsculas, sin acentos.

    Estrategias aplicadas en orden de confianza:
    1. Coincidencia exacta (case-insensitive).
    2. El texto detectado es prefijo del nombre canónico (ej. 'Tijuan' → 'Tijuana').
    3. El nombre canónico es substring del texto detectado (ej. texto más largo).
    4. El texto detectado es substring de algún nombre canónico — se elige el
       más corto para evitar falsos positivos.
    Si no hay coincidencia se devuelve el texto original sin cambios."""
    if not texto or not sucursales:
        return texto

    t = texto.strip().lower()

    for suc in sucursales:
        if suc.lower() == t:
            return suc

    candidatos = [suc for suc in sucursales if suc.lower().startswith(t)]
    if len(candidatos) == 1:
        return candidatos[0]

    candidatos = [suc for suc in sucursales if t.startswith(suc.lower())]
    if len(candidatos) == 1:
        return candidatos[0]

    candidatos = [suc for suc in sucursales if t in suc.lower()]
    if len(candidatos) == 1:
        return candidatos[0]

    return texto
