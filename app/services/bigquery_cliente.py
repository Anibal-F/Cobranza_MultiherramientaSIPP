"""Cliente y configuración de BigQuery compartidos.

Este módulo centraliza la autenticación y la configuración de BigQuery (proyecto,
tabla, credenciales) para que tanto el Dashboard de Ingresos como el módulo de
Conciliación Bancaria usen el MISMO cliente singleton, sin que ninguno importe del
otro (evita dependencias circulares).

Requiere BIGQUERY_CREDENTIALS_PATH en .env apuntando al JSON de la cuenta de
servicio (ver .env.example). Ese JSON NO se sube al repositorio.
"""

import os

from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account

# Raíz del proyecto: este archivo vive en app/services/, así que subimos dos niveles.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_dotenv_path = os.path.join(ROOT_DIR, ".env")
load_dotenv(_dotenv_path if os.path.exists(_dotenv_path) else None)

CREDENCIALES_PATH = os.getenv("BIGQUERY_CREDENTIALS_PATH", "app/credentials/bq.json")
PROYECTO = "sipp-app"
TABLA = "sipp-app.Tableros.IgresosClientes"

_cliente: bigquery.Client | None = None


def cliente_bigquery() -> bigquery.Client:
    """Cliente de BigQuery, autenticado con la cuenta de servicio. Se crea una
    sola vez y se reutiliza (evita releer/reautenticar el JSON en cada consulta)."""
    global _cliente
    if _cliente is None:
        ruta = os.path.join(ROOT_DIR, CREDENCIALES_PATH)
        credenciales = service_account.Credentials.from_service_account_file(ruta)
        _cliente = bigquery.Client(project=PROYECTO, credentials=credenciales)
    return _cliente
