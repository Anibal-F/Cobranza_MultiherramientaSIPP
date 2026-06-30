import csv

from .textutils import normalizar


def _limpiar_razon_social(valor: str) -> str:
    # La columna trae a veces comillas o dos puntos sobrantes como parte del
    # propio dato (no como delimitador CSV), ej. '"GRANEROS..."' o ': SDN...'.
    return valor.strip().strip('"').strip().lstrip(":").strip()


def cargar_filas_clientes(path: str) -> list[dict[str, str]]:
    """Lee el catálogo maestro de clientes completo (todas las columnas), para
    el visor de solo lectura en la pestaña Catálogos."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def cargar_clientes(path: str) -> list[str]:
    """Lee el listado maestro de clientes activos y regresa las razones sociales
    únicas. Nota: la columna CLIENTE del archivo es un ID numérico interno; el
    nombre de la empresa está en RAZÓN SOCIAL.
    """
    nombres: list[str] = []
    vistos: set[str] = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nombre = _limpiar_razon_social(row.get("RAZÓN SOCIAL") or "")
            if nombre and nombre not in vistos:
                vistos.add(nombre)
                nombres.append(nombre)
    return nombres


def preparar_clientes_normalizados(nombres: list[str]) -> list[tuple[str, str]]:
    """Pares (nombre original, nombre normalizado), ordenados del más largo al más corto
    para preferir la coincidencia más específica."""
    pares = [(nombre, normalizar(nombre)) for nombre in nombres]
    pares = [(original, norm) for original, norm in pares if norm]
    pares.sort(key=lambda par: len(par[1]), reverse=True)
    return pares
