import flet as ft

from app.main import main


def _fijar_appusermodelid() -> None:
    """En Windows, asignar un AppUserModelID propio hace que la barra de tareas
    agrupe la app aparte del cliente de Flet y use el ícono de la ventana
    (page.window.icon = Logo_Petroil.ico) en vez del ícono por defecto de Flet.
    En otros sistemas operativos no aplica (se ignora silenciosamente)."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "GrupoPetroil.MultiHerramientaCobranza"
        )
    except Exception:
        pass


if __name__ == "__main__":
    _fijar_appusermodelid()
    ft.run(main)
