import csv
import os

from .models import ClienteCuenta


def cargar_catalogo(path: str) -> list[ClienteCuenta]:
    catalogo: list[ClienteCuenta] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cliente = (row.get("CLIENTE") or "").strip()
            banco = (row.get("BANCO") or "").strip()
            plaza = (row.get("PLAZA") or "").strip()
            rfc = (row.get("RFC") or "").strip().upper()
            cuentas = (row.get("CUENTA") or "").split()
            if cuentas:
                # Algunas filas traen varias cuentas separadas por espacios para un mismo cliente.
                for cuenta in cuentas:
                    catalogo.append(ClienteCuenta(cuenta=cuenta, cliente=cliente, banco=banco, plaza=plaza, rfc=rfc))
            elif rfc:
                # Entrada identificada solo por RFC (sin cuenta/CLABE conocida).
                catalogo.append(ClienteCuenta(cuenta="", cliente=cliente, banco=banco, plaza=plaza, rfc=rfc))
    return catalogo


def guardar_catalogo_completo(path: str, catalogo: list[ClienteCuenta]) -> None:
    """Reescribe el CSV completo del catálogo a partir de la lista en memoria
    (usado por el editor de Catálogos: agregar/editar/eliminar cuentas)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["CUENTA", "CLIENTE", "BANCO", "PLAZA", "RFC"])
        for item in catalogo:
            writer.writerow([item.cuenta, item.cliente, item.banco, item.plaza, getattr(item, "rfc", "")])


def guardar_nuevas_cuentas(path: str, catalogo_actual: list[ClienteCuenta], propuestas: list[ClienteCuenta]) -> list[ClienteCuenta]:
    """Agrega al final del CSV del catálogo las cuentas propuestas que todavía
    no existan (por número de cuenta). Regresa las que realmente se agregaron.
    """
    cuentas_existentes = {c.cuenta for c in catalogo_actual if c.cuenta}
    rfcs_existentes = {getattr(c, "rfc", "") for c in catalogo_actual if getattr(c, "rfc", "")}

    def _es_nueva(c: ClienteCuenta) -> bool:
        if c.cuenta:
            return c.cuenta not in cuentas_existentes
        rfc = getattr(c, "rfc", "")
        return bool(rfc) and rfc not in rfcs_existentes

    nuevas = [c for c in propuestas if _es_nueva(c)]
    if not nuevas:
        return []

    # El archivo puede no terminar en salto de línea (común en exports de Excel);
    # si no se corrige, la primera fila nueva quedaría pegada a la última línea.
    necesita_salto_previo = False
    if os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            necesita_salto_previo = f.read(1) not in (b"\n", b"\r")

    with open(path, "a", newline="", encoding="utf-8") as f:
        if necesita_salto_previo:
            f.write("\n")
        writer = csv.writer(f)
        for nueva in nuevas:
            writer.writerow([nueva.cuenta, nueva.cliente, nueva.banco, nueva.plaza, getattr(nueva, "rfc", "")])

    return nuevas
