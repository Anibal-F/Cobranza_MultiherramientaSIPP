"""Trazabilidad centralizada de la MultiHerramienta de Cobranza.

Motivo: la app se instala en la computadora de cada usuario y hasta ahora todo el
diagnóstico eran `print()` que morían al cerrar la ventana. Cuando alguien reporta
"me puso otro cliente" no había forma de saber qué pasó realmente en su máquina.

Este módulo da tres cosas:

1. **Archivo local rotativo** en formato JSON-lines (una línea = un evento), con
   usuario, equipo, versión del código y un id de sesión. Vive en la carpeta de
   logs del sistema operativo y sobrevive al cierre de la app.
2. **Captura global de errores**: excepciones no atrapadas del hilo principal, de
   otros hilos y de asyncio quedan registradas en vez de perderse.
3. **Envío a BigQuery** en segundo plano y por lotes, para poder consultar con SQL
   lo que pasó en CUALQUIER computadora sin depender de que el usuario nos mande
   nada. Si no hay red o credenciales, el archivo local sigue funcionando igual.

Uso típico desde otro módulo::

    from app import logs

    log = logs.obtener_logger(__name__)
    log.info("Inicia carga de movimientos")
    logs.evento(log, "cliente_rechazado", buscado=nombre, candidato=texto, score=0.42)

`evento()` es la forma preferida: además del mensaje deja campos estructurados que
luego se pueden filtrar en BigQuery (`WHERE accion = 'cliente_rechazado'`).

Notas de Python para el equipo:
- `logging` es la librería estándar; un "logger" es un canal con nombre y un
  "handler" es un destino (archivo, consola, BigQuery). Un mismo mensaje puede ir a
  varios destinos a la vez.
- Un hilo `daemon` es un hilo que no impide que el programa termine.
"""

from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import os
import platform
import queue
import re
import socket
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# ──────────────────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────────────────

NOMBRE_APP = "MH_Cobranza"

# Tabla destino en BigQuery. Se reutiliza el proyecto/credenciales que ya usan los
# repositorios de app/services/.
TABLA_LOGS = os.getenv("MH_LOGS_TABLA", "sipp-app.Tableros.logs_app")

# Cada cuántos segundos, o cada cuántos registros, se vacía la cola hacia BigQuery.
INTERVALO_ENVIO_SEG = 15.0
TAMANO_LOTE = 50

# Tope de la cola en memoria: si el envío remoto se atora, preferimos descartar los
# eventos más viejos antes que consumir memoria sin límite. El archivo local nunca
# pierde nada.
MAX_COLA = 5_000

# Rotación del archivo local: 5 archivos de 5 MB ≈ 25 MB por equipo.
MAX_BYTES_ARCHIVO = 5 * 1024 * 1024
ARCHIVOS_RESPALDO = 5

ID_SESION = uuid.uuid4().hex[:16]

_configurado = False
_emisor_bq: Optional["EmisorBigQuery"] = None
_lock = threading.Lock()


# ──────────────────────────────────────────────────────────
# Contexto del equipo (se calcula una sola vez)
# ──────────────────────────────────────────────────────────


def _version_codigo() -> str:
    """Commit corto del repositorio, para saber qué versión corría el usuario.

    Si la app va empaquetada (sin git) se cae a la variable MH_VERSION o a
    'desconocida'; nunca revienta.
    """
    env = os.getenv("MH_VERSION")
    if env:
        return env.strip()
    try:
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return "desconocida"


def _usuario() -> str:
    for var in ("USERNAME", "USER", "LOGNAME"):
        v = os.getenv(var)
        if v:
            return v
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return "desconocido"


def _nombre_equipo() -> str:
    """Nombre del equipo. En macOS `gethostname()` a veces devuelve la IP, así que
    se prefiere `platform.node()` cuando ese es el caso."""
    def _es_ip(t: str) -> bool:
        return bool(re.fullmatch(r"[\d.]+", t))

    for candidato in (platform.node(), socket.gethostname()):
        candidato = (candidato or "").strip()
        if not candidato:
            continue
        if _es_ip(candidato):
            # Es una IP, no un nombre: se conserva completa (partirla por "." daría
            # basura como "192") y se sigue buscando un nombre de verdad.
            respaldo = candidato
            continue
        return candidato.split(".")[0]  # quita el dominio de un FQDN
    return locals().get("respaldo") or "desconocido"


_CONTEXTO: dict[str, str] = {}


def contexto() -> dict[str, str]:
    """Datos fijos que acompañan a TODOS los eventos de esta sesión."""
    global _CONTEXTO
    if not _CONTEXTO:
        _CONTEXTO = {
            "sesion": ID_SESION,
            "usuario": _usuario(),
            "equipo": _nombre_equipo(),
            "so": f"{platform.system()} {platform.release()}",
            "version": _version_codigo(),
            "entorno": os.getenv("SIPP_ENV", "prod").strip().lower() or "prod",
        }
    return _CONTEXTO


# ──────────────────────────────────────────────────────────
# Dónde se guarda el archivo
# ──────────────────────────────────────────────────────────


def directorio_logs() -> str:
    """Carpeta de logs propia del sistema operativo (se crea si no existe).

    Windows: %LOCALAPPDATA%\\MH_Cobranza\\logs
    macOS:   ~/Library/Logs/MH_Cobranza
    Otros:   ~/.local/state/MH_Cobranza/logs
    """
    forzado = os.getenv("MH_LOGS_DIR")
    if forzado:
        ruta = forzado
    elif sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        ruta = os.path.join(base, NOMBRE_APP, "logs")
    elif sys.platform == "darwin":
        ruta = os.path.expanduser(f"~/Library/Logs/{NOMBRE_APP}")
    else:
        ruta = os.path.expanduser(f"~/.local/state/{NOMBRE_APP}/logs")
    os.makedirs(ruta, exist_ok=True)
    return ruta


def ruta_archivo_log() -> str:
    return os.path.join(directorio_logs(), "mh_cobranza.jsonl")


# ──────────────────────────────────────────────────────────
# Formato JSON-lines
# ──────────────────────────────────────────────────────────

# Atributos internos de logging.LogRecord: todo lo que NO esté aquí es un campo
# extra que puso quien llamó (vía evento(...)) y sí queremos conservar.
_ATRIBUTOS_ESTANDAR = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}


def registro_a_dict(record: logging.LogRecord) -> dict[str, Any]:
    """Convierte un LogRecord en el diccionario que se escribe/envía."""
    datos: dict[str, Any] = {
        "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        "nivel": record.levelname,
        "modulo": record.name,
        "mensaje": record.getMessage(),
        "linea": f"{record.filename}:{record.lineno}",
        **contexto(),
    }
    extras = {
        k: v for k, v in record.__dict__.items()
        if k not in _ATRIBUTOS_ESTANDAR and not k.startswith("_")
    }
    accion = extras.pop("accion", None)
    if accion:
        datos["accion"] = str(accion)
    if record.exc_info:
        datos["error"] = "".join(traceback.format_exception(*record.exc_info)).strip()
    if extras:
        # Los campos libres van serializados en una sola columna JSON: así la tabla
        # de BigQuery no necesita cambiar de esquema cada vez que agregamos un dato.
        try:
            datos["datos"] = json.dumps(extras, default=str, ensure_ascii=False)
        except Exception:
            datos["datos"] = str(extras)
    return datos


class FormateadorJSON(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(registro_a_dict(record), ensure_ascii=False, default=str)


# ──────────────────────────────────────────────────────────
# Envío a BigQuery (segundo plano, por lotes)
# ──────────────────────────────────────────────────────────

ESQUEMA_LOGS = [
    ("ts", "TIMESTAMP"),
    ("nivel", "STRING"),
    ("modulo", "STRING"),
    ("mensaje", "STRING"),
    ("linea", "STRING"),
    ("accion", "STRING"),
    ("datos", "JSON"),
    ("error", "STRING"),
    ("sesion", "STRING"),
    ("usuario", "STRING"),
    ("equipo", "STRING"),
    ("so", "STRING"),
    ("version", "STRING"),
    ("entorno", "STRING"),
]


class EmisorBigQuery(logging.Handler):
    """Handler que encola los registros y los sube a BigQuery en un hilo aparte.

    Nunca bloquea a la interfaz: `emit()` solo mete el registro en una cola. Si
    BigQuery no está disponible (sin red, sin credenciales, sin permisos) el
    emisor se apaga solo y deja constancia en el archivo local; la app sigue
    funcionando igual.
    """

    def __init__(self, tabla: str = TABLA_LOGS, nivel: int = logging.INFO):
        super().__init__(level=nivel)
        self.tabla = tabla
        self._cola: queue.Queue = queue.Queue(maxsize=MAX_COLA)
        self._activo = True
        self._cliente = None
        self._detener = threading.Event()
        self._hilo = threading.Thread(
            target=self._bucle, name="logs-bigquery", daemon=True
        )
        self._hilo.start()

    # -- productor ------------------------------------------------------
    def emit(self, record: logging.LogRecord) -> None:
        if not self._activo:
            return
        try:
            fila = registro_a_dict(record)
        except Exception:
            return
        try:
            self._cola.put_nowait(fila)
        except queue.Full:
            # Cola saturada: tiramos el evento más viejo para dar espacio al nuevo.
            try:
                self._cola.get_nowait()
                self._cola.put_nowait(fila)
            except Exception:
                pass

    # -- consumidor -----------------------------------------------------
    def _obtener_cliente(self):
        if self._cliente is None:
            from app.services.bigquery_cliente import cliente_bigquery

            self._cliente = cliente_bigquery()
        return self._cliente

    def _asegurar_tabla(self) -> None:
        """Crea la tabla de logs si no existe (particionada por día).

        Particionar por `ts` hace que consultar "lo que pasó ayer" no escanee el
        histórico completo, que es lo que cuesta dinero en BigQuery.
        """
        from google.cloud import bigquery

        cliente = self._obtener_cliente()
        try:
            cliente.get_table(self.tabla)
            return
        except Exception:
            pass
        esquema = [bigquery.SchemaField(n, t) for n, t in ESQUEMA_LOGS]
        tabla = bigquery.Table(self.tabla, schema=esquema)
        tabla.time_partitioning = bigquery.TimePartitioning(field="ts")
        cliente.create_table(tabla, exists_ok=True)

    def _bucle(self) -> None:
        preparado = False
        lote: list[dict] = []
        while not self._detener.is_set() or not self._cola.empty():
            try:
                try:
                    fila = self._cola.get(timeout=INTERVALO_ENVIO_SEG)
                    lote.append(fila)
                    # Aprovechamos para vaciar lo que ya esté en la cola.
                    while len(lote) < TAMANO_LOTE:
                        try:
                            lote.append(self._cola.get_nowait())
                        except queue.Empty:
                            break
                except queue.Empty:
                    pass

                if not lote:
                    continue

                if not preparado:
                    self._asegurar_tabla()
                    preparado = True

                errores = self._obtener_cliente().insert_rows_json(self.tabla, lote)
                if errores:
                    self._apagar(f"BigQuery rechazó filas de log: {errores[:2]}")
                lote = []
            except Exception as e:  # sin red, sin credenciales, sin permisos…
                self._apagar(f"{type(e).__name__}: {e}")
                lote = []
                return

    def _apagar(self, motivo: str) -> None:
        if not self._activo:
            return
        self._activo = False
        try:
            # Se escribe SOLO al archivo local (este handler ya está apagado, así
            # que no hay riesgo de recursión).
            logging.getLogger("app.logs").warning(
                "Envío de logs a BigQuery desactivado en esta sesión: %s", motivo,
                extra={"accion": "logs_bq_desactivado"},
            )
        except Exception:
            pass

    def close(self) -> None:
        self._detener.set()
        try:
            self._hilo.join(timeout=5)
        except Exception:
            pass
        super().close()


# ──────────────────────────────────────────────────────────
# Captura global de errores
# ──────────────────────────────────────────────────────────


def instalar_captura_global() -> None:
    """Hace que ninguna excepción no atrapada se pierda en silencio."""
    log = logging.getLogger("app.no_capturado")

    anterior = sys.excepthook

    def _hook(tipo, valor, tb):
        if issubclass(tipo, KeyboardInterrupt):
            anterior(tipo, valor, tb)
            return
        log.critical(
            "Excepción no capturada: %s", valor,
            exc_info=(tipo, valor, tb),
            extra={"accion": "excepcion_no_capturada"},
        )
        anterior(tipo, valor, tb)

    sys.excepthook = _hook

    # Excepciones en hilos (Python 3.8+).
    def _hook_hilo(args):
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        log.critical(
            "Excepción no capturada en el hilo %s: %s",
            args.thread.name if args.thread else "?", args.exc_value,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            extra={"accion": "excepcion_hilo"},
        )

    threading.excepthook = _hook_hilo


def instalar_captura_asyncio(loop) -> None:
    """Registra los errores del bucle de asyncio (tareas que fallan sin await).

    Se llama aparte porque necesita el loop ya creado; la UI de Flet lo tiene
    disponible una vez arrancada.
    """
    log = logging.getLogger("app.asyncio")

    def _manejador(bucle, contexto_err: dict):
        exc = contexto_err.get("exception")
        log.error(
            "Error en asyncio: %s", contexto_err.get("message"),
            exc_info=exc if exc else None,
            extra={"accion": "error_asyncio"},
        )

    try:
        loop.set_exception_handler(_manejador)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
# Arranque
# ──────────────────────────────────────────────────────────


def configurar(
    nivel: int = logging.INFO,
    a_bigquery: Optional[bool] = None,
    consola: bool = True,
) -> str:
    """Deja el logging listo. Idempotente: llamarlo dos veces no duplica destinos.

    Devuelve la ruta del archivo de log local.

    `a_bigquery=None` (por defecto) lo activa salvo que MH_LOGS_BQ=0.
    """
    global _configurado, _emisor_bq
    with _lock:
        ruta = ruta_archivo_log()
        if _configurado:
            return ruta

        raiz = logging.getLogger()
        raiz.setLevel(min(nivel, logging.DEBUG))

        archivo = logging.handlers.RotatingFileHandler(
            ruta, maxBytes=MAX_BYTES_ARCHIVO, backupCount=ARCHIVOS_RESPALDO,
            encoding="utf-8",
        )
        archivo.setFormatter(FormateadorJSON())
        archivo.setLevel(nivel)
        raiz.addHandler(archivo)

        if consola:
            terminal = logging.StreamHandler(sys.stdout)
            terminal.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                  datefmt="%H:%M:%S")
            )
            terminal.setLevel(nivel)
            raiz.addHandler(terminal)

        if a_bigquery is None:
            a_bigquery = os.getenv("MH_LOGS_BQ", "1").strip() not in ("0", "false", "no")
        if a_bigquery:
            try:
                _emisor_bq = EmisorBigQuery()
                raiz.addHandler(_emisor_bq)
            except Exception as e:
                logging.getLogger("app.logs").warning(
                    "No se pudo iniciar el envío de logs a BigQuery: %s", e
                )

        # Las librerías de terceros son muy ruidosas en DEBUG/INFO.
        for ruidoso in ("google", "google.auth", "google.cloud",
                        "asyncio", "flet", "flet_core", "PIL"):
            logging.getLogger(ruidoso).setLevel(logging.WARNING)
        # urllib3 avisa "Connection pool is full" en cada consulta concurrente a
        # BigQuery; es normal (el pool reabre la conexión) y ahogaba el log con
        # decenas de líneas por sesión. Solo nos interesan sus errores de verdad.
        logging.getLogger("urllib3").setLevel(logging.ERROR)

        instalar_captura_global()
        atexit.register(cerrar)
        _configurado = True

        logging.getLogger("app.logs").info(
            "Sesión iniciada", extra={"accion": "sesion_inicio", "archivo": ruta}
        )
        return ruta


def cerrar() -> None:
    """Vacía la cola pendiente antes de que el proceso termine."""
    global _emisor_bq
    if _emisor_bq is not None:
        try:
            _emisor_bq.close()
        except Exception:
            pass
        _emisor_bq = None


def obtener_logger(nombre: str) -> logging.Logger:
    return logging.getLogger(nombre)


def evento(log: logging.Logger, accion: str, nivel: int = logging.INFO, **campos: Any) -> None:
    """Registra un evento estructurado.

    `accion` es la etiqueta con la que luego se filtra en BigQuery, y `campos` son
    datos libres que se guardan en la columna JSON `datos`. Ejemplo::

        evento(log, "cliente_rechazado", buscado="ACME SA DE CV", score=0.41)
    """
    mensaje = campos.pop("mensaje", accion)
    # stacklevel=2 hace que el log apunte a QUIEN llamó a evento(), no a esta línea.
    log.log(nivel, mensaje, extra={"accion": accion, **campos}, stacklevel=2)
