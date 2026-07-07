"""Auto-actualización vía git: consulta el repo remoto en GitHub y aplica los
cambios con `git pull`, reiniciando la app.

Requiere que la app corra desde un clon git con upstream configurado (origin/…).
Las llamadas a git usan BatchMode para no colgarse pidiendo credenciales.
"""

import os
import subprocess
import sys

_ENV_GIT = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",           # no pedir usuario/clave por HTTPS
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=15",
}


# En Windows, evita que cada 'git' (app de consola) abra una ventana cmd al
# correr la app con pythonw (sin consola).
_CREATE_NO_WINDOW = 0x08000000


def _git(args: list[str], base_dir: str) -> subprocess.CompletedProcess:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(
        ["git", *args],
        cwd=base_dir,
        capture_output=True,
        text=True,
        timeout=90,
        env=_ENV_GIT,
        **kwargs,
    )


def es_repo_git(base_dir: str) -> bool:
    try:
        r = _git(["rev-parse", "--is-inside-work-tree"], base_dir)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def revisar_actualizaciones(base_dir: str) -> dict:
    """Consulta GitHub y compara la copia local con el upstream.

    Devuelve un dict con:
      - disponible: bool
      - detras: nº de commits por detrás (si disponible)
      - resumen: log corto de los commits nuevos
      - error: mensaje si no se pudo comprobar
    """
    # Distinguir "git no disponible" de "no es un clon git" para un mensaje claro.
    try:
        version = _git(["--version"], base_dir)
        if version.returncode != 0:
            raise FileNotFoundError
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return {
            "disponible": False,
            "error": "git no está instalado o no está en el PATH. Reinstala con instalar_windows.bat.",
        }
    if not es_repo_git(base_dir):
        return {
            "disponible": False,
            "error": "La carpeta no es un clon de git. Instala con 'git clone' (no copiando la carpeta).",
        }
    try:
        fetch = _git(["fetch", "--quiet"], base_dir)
        if fetch.returncode != 0:
            return {"disponible": False, "error": fetch.stderr.strip() or "No se pudo consultar GitHub."}

        local = _git(["rev-parse", "HEAD"], base_dir).stdout.strip()
        upstream = _git(["rev-parse", "@{u}"], base_dir)
        if upstream.returncode != 0:
            return {"disponible": False, "error": "La rama actual no tiene upstream configurado."}
        remoto = upstream.stdout.strip()

        if not local or local == remoto:
            return {"disponible": False, "detras": 0}

        detras = int(_git(["rev-list", "--count", "HEAD..@{u}"], base_dir).stdout.strip() or 0)
        if detras == 0:
            # Local va igual o adelante del remoto (commits propios sin subir):
            # no hay nada que "pull"-ear, así que no hay actualización pendiente.
            return {"disponible": False, "detras": 0}

        resumen = _git(["log", "--oneline", "-5", "HEAD..@{u}"], base_dir).stdout.strip()
        return {"disponible": True, "detras": detras, "resumen": resumen}
    except subprocess.TimeoutExpired:
        return {"disponible": False, "error": "Tiempo de espera agotado al consultar GitHub."}
    except (OSError, subprocess.SubprocessError) as ex:
        return {"disponible": False, "error": str(ex)}


def aplicar_actualizacion(base_dir: str) -> tuple[bool, str]:
    """Aplica los cambios con `git pull --ff-only`. Devuelve (ok, salida)."""
    try:
        r = _git(["pull", "--ff-only"], base_dir)
        salida = (r.stdout + "\n" + r.stderr).strip()
        return (r.returncode == 0, salida)
    except subprocess.TimeoutExpired:
        return (False, "Tiempo de espera agotado al descargar la actualización.")
    except (OSError, subprocess.SubprocessError) as ex:
        return (False, str(ex))


def reiniciar_app(base_dir: str) -> None:
    """Relanza la app para cargar el código nuevo, conservando las variables de
    entorno (ej. SIPP_ENV).

    - Windows: `os.execv` es poco fiable con apps GUI (Flet deja la ventana del
      cliente huérfana). Se lanza una instancia NUEVA desacoplada; el proceso
      actual debe cerrarse después (el llamador cierra la ventana y sale).
    - macOS/Linux: se reemplaza el proceso con execv (no regresa)."""
    main_py = os.path.join(base_dir, "main.py")
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [sys.executable, main_py],
            cwd=base_dir,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return  # el llamador cierra esta instancia (page.window.destroy + salir)
    os.execv(sys.executable, [sys.executable, main_py])
