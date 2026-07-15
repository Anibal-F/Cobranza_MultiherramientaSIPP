# CLAUDE.md — MultiHerramienta de Cobranza (Grupo Petroil)

Orientación rápida del proyecto. Para detalle del módulo de conciliación ver
[docs/Manual_Tecnico_Conciliacion.md](docs/Manual_Tecnico_Conciliacion.md).

## Qué es
App de escritorio en **Flet 0.85.3** (Python) para cobranza. Entrada:
`main.py` → `app/main.py:main(page)`. La UI son **pestañas** (no rutas).

> Comunicación: el equipo trabaja en **español** y no está muy familiarizado con
> Python; conviene explicar los conceptos del lenguaje cuando aporte.

## Pestañas (todas viven en app/main.py salvo las que retornan un `construir_tab_*`)
1. **Identificación Bancaria** — sube movimientos a SIPP vía RPA. Lee estados de cuenta con `app/parsers/`.
2. **Extracción de Contados** — buzón O365 (`app/mailbox_o365.py`).
3. **Catálogos** — CRUD cliente/cuenta (`app/catalogo.py`).
4. **Dashboard Ingresos** — `app/dashboard_cobranza.py` (BigQuery, solo agregados).
5. **Conciliaciones Bancarias** — `app/conciliacion/vista.py` (módulo nuevo).

## Estructura relevante
- `app/parsers/` — **lectura unificada de bancos** (CSV/.xls/.xlsx) → `Movimiento`. La usan identificación Y conciliación. Registro en `parsers/__init__.py` (`detectar_banco`, `parsear_archivo`).
- `app/services/` — BigQuery: `bigquery_cliente.py` (cliente/config compartidos) y `bigquery_repository.py` (datos crudos).
- `app/conciliacion/` — módulo de conciliación (ver su propio CLAUDE.md).
- `app/models.py` — dataclass `Movimiento` (identificación).

## Convenciones / gotchas de Flet 0.85 (ya nos mordieron)
- Un tab se crea con `construir_tab_*(page) -> (ft.Tab, ft.Control)` y se inserta en `TabBar.tabs` + `TabBarView.controls` en la MISMA posición.
- `ft.FilePicker` **NO** se agrega a `page.overlay` (da "Unknown control"); se crea y se usa directo.
- `page.open(...)` **no existe**: usar un `SnackBar` en overlay con `.open = True` + `page.update()`.
- Diálogos: `page.show_dialog(dlg)` / `page.pop_dialog()`.
- I/O bloqueante (leer Excel, BigQuery) → `await asyncio.to_thread(fn, ...)` para no congelar la UI.
- Fechas en español: `page.locale_configuration` (es-MX) en `app/main.py`; `DateRangePicker` en modo `CALENDAR_ONLY`.

## Git / trabajo
- Rama principal: `main`. No commitear salvo que se pida.
- Probar sin abrir la app: `python -m compileall -q app/...` y `python -c "import app.main"`.
- Windows + PowerShell; hay Bash (git-bash) disponible.
