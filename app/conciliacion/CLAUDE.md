# CLAUDE.md — Módulo de Conciliación Bancaria

Cheat-sheet. Detalle completo en [../../docs/Manual_Tecnico_Conciliacion.md](../../docs/Manual_Tecnico_Conciliacion.md).

## Qué hace
Compara movimientos de estados de cuenta bancarios (uno o varios archivos) contra los
del sistema (reporte "Ingresos Diversos" o BigQuery) y los clasifica en 4 grupos que se
muestran + 1 interno.

## Archivos
- `modelo.py` — `MovimientoConciliacion` (dato único banco/sistema) + `ResultadoConciliacion`.
- `conciliador.py` — motor: `conciliar(mov_banco, mov_sistema)`.
- `lector_banco.py` — puente: usa `app/parsers` y convierte `Movimiento` → `MovimientoConciliacion` (`normalizar_banco`).
- `ingresos_diversos.py` — lee el reporte del sistema (XML crudo del .xlsx; openpyxl no puede abrirlo).
- `vista.py` — UI Flet: `construir_tab_conciliaciones(page) -> (Tab, Control)`. Permite **varios archivos** (cada uno con su selector de banco), botón "Limpiar todos", origen del sistema (Excel/nube), y muestra 4 paneles desplegables. Las tablas del lado banco llevan columna **Banco**; la de repetidos, columna **Conciliación**.

## Flujo
archivo banco → `app/parsers` (detectar+parsear) → `Movimiento` → `lector_banco._a_conciliacion` → `MovimientoConciliacion`.
sistema → `ingresos_diversos.cargar_ingresos_diversos` o `services/bigquery_repository`.
`conciliador.conciliar(...)` → `ResultadoConciliacion` → `vista._render`.

## Reglas (en conciliador.py)
- **Ventana de fechas (filtro previo)**: antes de comparar se calcula la **intersección** de rangos de fecha de ambos lados (`_ventana_comun`: `[max(mínimos), min(máximos)]`). Los movimientos (banco **o** sistema) cuya fecha caiga fuera se apartan en `ResultadoConciliacion.fuera_de_rango` y NO se concilian ni cuentan como duplicados. Fechas nulas se conservan. Si algún lado no trae fechas → `ventana=None` y no se filtra. La ventana usada queda en `ResultadoConciliacion.ventana`.
- **Match**: importe igual **Y** alguna aguja del sistema (su `referencia` **o** su `descripcion`/concepto, normalizadas) aparece dentro del `descripcion` (concepto) **o** de la `referencia` del banco. El reporte Excel solo trae referencia; la nube trae `de_Referencia` + `de_Concepto` (ambos se usan). Se agrupa por importe; cada sistema se consume 1 vez.
- **Posibles repetidos en sistema**: mismos referencia + descripción + importe + **fecha** (2+). El grid muestra columna **Banco** (de `raw["BANCO"]`, ver abajo) para que el usuario NO confunda un duplicado de otro banco con el archivo que subió.
- **Devolución de cheque**: regex `LEYENDA_DEVOLUCION_CHEQUE` (hoy contiene "CHEQUE"); apartadas antes de comparar.
- `normalizar()` (app/textutils) quita mayúsc/acentos/símbolos → el apóstrofo (`'003…`) y guion bajo (`_SPEI`) no estorban.

## Bancos en el selector (flag `EN_CONCILIACION` / `en_conciliacion`)
- **Habilitados (validados, 5)**: SANTANDER, BANREGIO, BANORTE, BANBAJIO, BBVA (flag en su módulo `app/parsers/<banco>.py`).
- **Deshabilitados (inferidos del SP)**: HSBC, SABADELL, SCOTIABANK, BANCOPPEL, INTERCAM, BANAMEX, BX, VE POR MAS (flag en `app/parsers/excel_columnas.py`).
- Autodetectar un banco deshabilitado → UI avisa "comunícate a validar" y no concilia ese archivo.

## Cómo extender
- Banco `.xlsx` simple → agregar `BancoColumnasExcel(...)` en `excel_columnas.py` (firma + columnas + `en_conciliacion=True`).
- Banco con lógica especial → módulo `app/parsers/<banco>.py` (`detect`, `parse`, `BANCO`, `EN_CONCILIACION`) y registrarlo en `PARSERS`.
- Habilitar uno existente → poner su flag en `True`.
- Nube: `services/bigquery_repository.py` mapea `de_Concepto`→descripcion, `de_Referencia`→referencia, `im_Movimiento`→importe, `fh_Envio`→fecha, `de_CuentaBancaria`→cuenta (en `raw`). Ajustar `COL_*` si cambian los nombres.

## Exportar a Excel (vista.py)
- Botón "Exportar a Excel" (habilitado tras conciliar). `exportar_excel` toma `ultimo_resultado[0]` y llama `_construir_workbook(res, secs, generado)` (nivel módulo). Genera hoja **Resumen** (ventana de fechas + conteo/importe por sección) + una hoja por sección con sus movimientos, con los MISMOS columnas/filas que la UI (fuente única: `_secciones_datos`). Armado y `wb.save` van en `asyncio.to_thread`.

## Notas
- Reader de tablas: `app/parsers/lectura.py` detecta formato por **bytes** (no extensión); archivos de portal declaran mal la "dimensión" → usa modo normal antes que read_only.
- El reporte de Ingresos Diversos **sí** trae columna **Banco** y **Cuenta Bancaria** (encabezado en fila 8; el lector lo localiza por contenido y guarda TODAS las columnas en `raw`). Se leen como `raw["BANCO"]` / `raw["CUENTA_BANCARIA"]`; la "Conciliación" (folio) sale de `raw["CONCILIACION"]`.
- `_secciones_datos(res)` (en vista.py) es la fuente única de columnas+filas+totales por grupo; la consumen tanto `_render` (UI) como `exportar_excel` (Excel). Al agregar/cambiar una sección, tocar solo ahí.

## Estado / pendientes (actualizar aquí lo que quede abierto)
- **Nube sin probar**: el origen "Datos en la nube" (BigQuery) NO se ha podido validar porque la tabla con `de_Referencia`/`de_Concepto`/`de_CuentaBancaria` aún no está publicada. El código ya está listo (`services/bigquery_repository.py`); falta correr contra la tabla real y confirmar nombres de columna.
- **8 bancos inferidos** (HSBC, Sabadell, Scotiabank, Bancoppel, Intercam, Banamex, BX, Ve por Más): están en `excel_columnas.py` con `en_conciliacion=False` (deducidos del SP, sin archivo real). Validar con archivo real y poner el flag en True cuando se confirmen.
- **Leyenda de devolución de cheque**: `LEYENDA_DEVOLUCION_CHEQUE` en `conciliador.py` es provisional (hoy "CHEQUE"); ajustar cuando den la leyenda exacta.
- **BBVA** tiene 3 lectores: módulo `.xls` (identificación) + 2 `.xlsx` (RSM y SPEI, ambos con encabezado en fila 2).
  - **Conciliación usa los `.xlsx`** (`excel_columnas.py`). Descripción/concepto usada como texto de match:
    - **RSM**: `Referencia Ampliada`; si esa celda viene vacía en la fila → `Concepto` (fallback por fila vía `descripcion_orden`). La `referencia` sale de la columna `Referencia`.
    - **SPEI**: `Concepto de pago`.
