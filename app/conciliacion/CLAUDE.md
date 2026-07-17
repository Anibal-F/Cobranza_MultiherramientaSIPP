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
- `leyendas_cheque.py` — carga/guarda/matchea las leyendas de devolución de cheque (JSON `leyendas_cheque.json`).
- `vista.py` — UI Flet: `construir_tab_conciliaciones(page) -> (Tab, Control)`. Permite **varios archivos** (cada uno con su selector de banco), botón "Limpiar todos", origen del sistema (Excel/nube), y muestra 4 paneles desplegables. Las tablas del lado banco llevan columna **Banco**; la de repetidos, columna **Conciliación**.

## Flujo
archivo banco → `app/parsers` (detectar+parsear) → `Movimiento` → `lector_banco._a_conciliacion` → `MovimientoConciliacion`.
sistema → `ingresos_diversos.cargar_ingresos_diversos` o `services/bigquery_repository`.
`conciliador.conciliar(...)` → `ResultadoConciliacion` → `vista._render`.

## Reglas (en conciliador.py)
- **Match**: importe igual **Y** alguna aguja del sistema (su `referencia` **o** su `descripcion`/concepto, normalizadas) aparece dentro del `descripcion` (concepto) **o** de la `referencia` del banco. Excel: agujas = referencia + razón social (ambas). **Nube: aguja = SOLO `de_Referencia`** (decisión 2026-07-17; `de_Concepto` viene vacío 73% y difiere de la referencia cuando existe → no se cruza, `descripcion` se emite vacía). Se agrupa por importe; cada sistema se consume 1 vez.
- **Posibles repetidos en sistema**: mismos referencia + descripción + importe + **fecha** (2+).
- **Devolución de cheque**: leyendas CONFIGURABLES desde la UI (botón ícono en la barra) y persistidas en `leyendas_cheque.json` (raíz, gitignored). Un movimiento del banco se aparta antes de comparar si su `texto` CONTIENE (substring normalizado) alguna leyenda. `conciliar(..., leyendas=None)` las carga del JSON si no se pasan. Ver `leyendas_cheque.py`. Semilla por defecto: `["CHEQUE"]`.
- `normalizar()` (app/textutils) quita mayúsc/acentos/símbolos → el apóstrofo (`'003…`) y guion bajo (`_SPEI`) no estorban.

## Bancos en el selector (flag `EN_CONCILIACION` / `en_conciliacion`)
- **Habilitados (validados, 5)**: SANTANDER, BANREGIO, BANORTE, BANBAJIO, BBVA (flag en su módulo `app/parsers/<banco>.py`).
- **Deshabilitados (inferidos del SP)**: HSBC, SABADELL, SCOTIABANK, BANCOPPEL, INTERCAM, BANAMEX, BX, VE POR MAS (flag en `app/parsers/excel_columnas.py`).
- Autodetectar un banco deshabilitado → UI avisa "comunícate a validar" y no concilia ese archivo.

## Cómo extender
- Banco `.xlsx` simple → agregar `BancoColumnasExcel(...)` en `excel_columnas.py` (firma + columnas + `en_conciliacion=True`).
- Banco con lógica especial → módulo `app/parsers/<banco>.py` (`detect`, `parse`, `BANCO`, `EN_CONCILIACION`) y registrarlo en `PARSERS`.
- Habilitar uno existente → poner su flag en `True`.
- Nube: `services/bigquery_repository.py` (tabla `sipp-app.Tableros.IgresosClientes`) mapea `de_Referencia`→referencia (única aguja), `im_Movimiento`→importe, `fh_Envio`→fecha; `de_Concepto`→`raw['concepto']` y `de_CuentaBancaria`→`raw['cuenta']` (solo display, no se cruzan); `descripcion` sale como literal vacío `''`. Trae TODO el universo del rango (sin filtro de tipo). Ajustar `COL_*` si cambian los nombres.

## Notas
- Reader de tablas: `app/parsers/lectura.py` detecta formato por **bytes** (no extensión); archivos de portal declaran mal la "dimensión" → usa modo normal antes que read_only.
- La columna "Conciliación" de repetidos sale de `MovimientoConciliacion.raw["CONCILIACION"]`.

## Estado / pendientes (actualizar aquí lo que quede abierto)
- **Nube sin probar**: el origen "Datos en la nube" (BigQuery) NO se ha podido validar porque la tabla con `de_Referencia`/`de_Concepto`/`de_CuentaBancaria` aún no está publicada. El código ya está listo (`services/bigquery_repository.py`); falta correr contra la tabla real y confirmar nombres de columna.
- **8 bancos inferidos** (HSBC, Sabadell, Scotiabank, Bancoppel, Intercam, Banamex, BX, Ve por Más): están en `excel_columnas.py` con `en_conciliacion=False` (deducidos del SP, sin archivo real). Validar con archivo real y poner el flag en True cuando se confirmen.
- **Leyendas de devolución de cheque**: ya no están hardcodeadas — se editan desde la UI y viven en `leyendas_cheque.json` (semilla `["CHEQUE"]`). Falta que los usuarios pasen las leyendas reales.
  - **OJO (capa previa)**: el conciliador aparta devoluciones sobre `mov_banco`, que ya pasó por el parser. El parser descarta filas SIN importe y, con `solo_abonos=True`, los CARGOS. Por eso una devolución solo se detecta por leyenda si sobrevive como **abono con importe**. Si las devoluciones reales son cargos, o vienen señaladas por columnas SPEI ("Estado del pago"=DEVUELTA / "Motivo de devolución") en vez de texto, habrá que ajustar el parser. Pendiente de un ejemplo real para decidir.
- **BBVA** tiene 3 lectores: módulo `.xls` (identificación) + 2 `.xlsx` (RSM y SPEI, ambos con encabezado en fila 2).
  - **Conciliación usa los `.xlsx`** (`excel_columnas.py`). Descripción/concepto usada como texto de match:
    - **RSM**: `Referencia Ampliada`; si esa celda viene vacía en la fila → `Concepto` (fallback por fila vía `descripcion_orden`). La `referencia` sale de la columna `Referencia`.
    - **SPEI**: `Concepto de pago`.
