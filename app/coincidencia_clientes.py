"""Decide si el cliente que ofrece SIPP es REALMENTE el que buscamos.

El problema que resuelve: los combos de SIPP usan la librería `chosen`, que filtra
la lista conforme se teclea. Como el nombre en SIPP casi nunca es idéntico al
nuestro ("S.A. DE C.V." vs "SA DE CV", acentos, nombres recortados), el RPA borra
caracteres del final hasta que vuelve a aparecer alguna opción. Hasta ahora, si el
texto recortado ya no correspondía al cliente, el RPA **elegía la primera opción de
todos modos** y terminaba capturando el pago a un cliente equivocado.

La regla nueva es simple: *ante la duda, no se elige*. La fila se deja sin asignar
y se acumula la incidencia para mostrársela al usuario al final.

Nota de Python para el equipo: `SequenceMatcher` (librería estándar) compara dos
cadenas y devuelve un parecido de 0 a 1; `@dataclass` es una clase de puros datos.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Optional, Sequence

# Qué tan parecidos deben ser el nombre buscado y el de SIPP para aceptarlo.
# 0.88 acepta diferencias de razón social y acentos, pero rechaza clientes que solo
# comparten el inicio del nombre (que es justo el error que reportaron los usuarios).
UMBRAL_ACEPTACION = 0.88

# Cuando hay varias opciones buenas, la mejor debe superar a la segunda por este
# margen; si van casi empatadas es ambiguo y preferimos no adivinar.
MARGEN_MINIMO = 0.05

# Terminaciones de razón social: no aportan nada para distinguir un cliente de otro
# ("ACME SA DE CV" y "ACME S.A. DE C.V." son el mismo) y por eso se descartan al
# comparar. Se quitan SOLO del final del nombre.
SUFIJOS_SOCIETARIOS = {
    "SA", "S", "A", "DE", "CV", "C", "V", "SAPI", "SAB", "SC", "SRL", "RL",
    "SOFOM", "ENR", "SPR", "MI", "SAS", "S EN NC", "COOP", "SDE",
}


def normalizar(texto) -> str:
    """Mayúsculas, sin acentos y solo alfanumérico, para comparar nombres."""
    t = unicodedata.normalize("NFKD", str(texto or "").upper())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def sin_codigo(texto) -> str:
    """Quita el prefijo de código con que SIPP muestra a sus clientes.

    Cubre '05881 - LOGISTICA TEROMO' y también variantes con letra ('C05881 - …').
    """
    return re.sub(r"^\s*[A-Za-z]{0,3}\d+\s*-\s*", "", str(texto or "")).strip()


def nucleo(texto) -> str:
    """Nombre comparable: sin código, normalizado y sin la razón social final.

    'C05881 - Comercializadora Ácme, S.A. de C.V.' → 'COMERCIALIZADORA ACME'
    """
    palabras = normalizar(sin_codigo(texto)).split()
    # Se recortan desde el final: los sufijos conocidos y las letras sueltas que
    # deja la puntuación al normalizar ("S.A.P.I." → "S A P I"). Solo aplica al
    # final del nombre, así que la parte distintiva nunca se toca.
    while palabras and (palabras[-1] in SUFIJOS_SOCIETARIOS or len(palabras[-1]) == 1):
        palabras.pop()
    return " ".join(palabras)


def parecido(buscado: str, candidato: str) -> float:
    """Qué tanto se parecen dos nombres de cliente, de 0 (nada) a 1 (idéntico).

    Se toma el mejor de tres criterios, porque los nombres difieren de formas
    distintas según el caso:

    1. Igualdad del núcleo → 1.0 (mismo cliente escrito distinto).
    2. Uno es el nombre completo del otro recortado: SIPP trunca nombres largos,
       así que 'ACME TRANSPORTES' vs 'ACME TRANSPORTES DEL NOROESTE' es válido…
       pero solo si lo compartido ya es sustancial, para que 'COMER' no case con
       'COMERCIALIZADORA ACME'.
    3. Parecido carácter por carácter (tolera erratas y palabras abreviadas).
    """
    a, b = nucleo(buscado), nucleo(candidato)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    puntajes = [SequenceMatcher(None, a, b).ratio()]

    # Criterio de prefijo/contención, exigiendo que la parte común sea la mayor
    # parte del nombre más largo (evita el falso positivo por inicio compartido).
    if a.startswith(b) or b.startswith(a) or a in b or b in a:
        corto, largo = (a, b) if len(a) <= len(b) else (b, a)
        puntajes.append(len(corto) / len(largo))

    # Criterio por palabras: cuántas palabras significativas del nombre corto
    # aparecen completas en el otro. Cubre orden distinto y palabras intercaladas.
    pal_a = [p for p in a.split() if len(p) > 2]
    pal_b = [p for p in b.split() if len(p) > 2]
    if pal_a and pal_b:
        corto, largo = (pal_a, pal_b) if len(pal_a) <= len(pal_b) else (pal_b, pal_a)
        comunes = sum(1 for p in corto if p in largo)
        # Se pondera por el tamaño relativo: coincidir 1 de 1 palabra contra un
        # nombre de 5 no debe valer 1.0.
        puntajes.append((comunes / len(corto)) * (len(corto) / len(largo)))

    return max(puntajes)


@dataclass
class Resultado:
    """Veredicto sobre qué opción del combo (si alguna) corresponde al cliente."""

    indice: Optional[int]          # posición en la lista de opciones; None = ninguna
    texto: Optional[str]           # texto de la opción elegida, tal cual en SIPP
    score: float                   # parecido de la mejor opción
    motivo: str                    # por qué se aceptó o se rechazó
    alternativa: Optional[str] = None  # la opción rival, cuando fue ambiguo

    @property
    def aceptado(self) -> bool:
        return self.indice is not None


def elegir(
    buscado: str,
    opciones: Sequence[str],
    umbral: float = UMBRAL_ACEPTACION,
    margen: float = MARGEN_MINIMO,
) -> Resultado:
    """Elige la opción que corresponde a `buscado`, o ninguna si no hay certeza.

    Se rechaza (indice=None) cuando:
      - la lista viene vacía;
      - la mejor opción no llega al umbral de parecido;
      - las dos mejores están casi empatadas (ambiguo).
    """
    if not opciones:
        return Resultado(None, None, 0.0, "el combo no mostró ninguna opción")

    puntuadas = sorted(
        ((parecido(buscado, o), i, o) for i, o in enumerate(opciones)),
        key=lambda t: (-t[0], t[1]),
    )
    mejor_score, mejor_i, mejor_txt = puntuadas[0]

    if mejor_score < umbral:
        return Resultado(
            None, None, mejor_score,
            f"la opción más parecida ('{sin_codigo(mejor_txt)}') solo coincide "
            f"{mejor_score:.0%} con '{buscado}'; se requiere {umbral:.0%}",
        )

    if len(puntuadas) > 1:
        segundo_score, _, segundo_txt = puntuadas[1]
        if mejor_score - segundo_score < margen:
            return Resultado(
                None, None, mejor_score,
                f"hay dos clientes igual de parecidos a '{buscado}' "
                f"('{sin_codigo(mejor_txt)}' y '{sin_codigo(segundo_txt)}'); "
                f"no se asigna para evitar equivocarse",
                alternativa=segundo_txt,
            )

    return Resultado(mejor_i, mejor_txt, mejor_score, f"coincidencia {mejor_score:.0%}")


# ──────────────────────────────────────────────────────────
# Resumen de incidencias para el usuario
# ──────────────────────────────────────────────────────────


@dataclass
class Incidencia:
    """Una fila que el RPA NO pudo asignar con seguridad."""

    fila: Optional[int]
    cliente_buscado: str
    motivo: str
    referencia: str = ""
    monto: Optional[float] = None

    def como_linea(self) -> str:
        partes = []
        if self.fila is not None:
            partes.append(f"Fila {self.fila}")
        if self.referencia:
            partes.append(f"ref. {self.referencia}")
        if self.monto is not None:
            partes.append(f"${self.monto:,.2f}")
        encabezado = " · ".join(partes)
        prefijo = f"{encabezado} — " if encabezado else ""
        return f"{prefijo}«{self.cliente_buscado}»: {self.motivo}"


def resumen(incidencias: Iterable[Incidencia], total_procesadas: int = 0) -> str:
    """Texto que se le muestra al usuario al terminar la corrida."""
    lista = list(incidencias)
    if not lista:
        return (
            f"Todos los clientes se asignaron correctamente"
            + (f" ({total_procesadas} movimiento(s))." if total_procesadas else ".")
        )
    lineas = [
        f"⚠️  {len(lista)} de {total_procesadas or len(lista)} movimiento(s) quedaron "
        f"SIN cliente asignado en SIPP.",
        "",
        "El RPA no los capturó a propósito: el nombre no coincidía con ninguna",
        "opción del catálogo de SIPP y asignar el equivocado sería peor. Hay que",
        "revisarlos y capturarlos a mano:",
        "",
    ]
    lineas += [f"  • {inc.como_linea()}" for inc in lista]
    return "\n".join(lineas)
