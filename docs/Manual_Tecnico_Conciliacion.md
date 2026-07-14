# Manual técnico — Módulo de Conciliación Bancaria

> Documento para el equipo de desarrollo. Explica **qué** se construyó, **cómo está
> organizado** y **por qué**, con notas de Python donde hace falta (pensado para
> quien conoce la lógica pero no está muy acostumbrado al lenguaje).

---

## 1. ¿Qué hace este módulo?

Compara los **movimientos de un estado de cuenta bancario** (uno o varios archivos)
contra los **movimientos del sistema** (el reporte de "Ingresos Diversos", o —cuando
esté lista— una tabla en BigQuery) y los clasifica en:

| Grupo | Significado |
|---|---|
| **Movimientos conciliados** | El movimiento del banco cruzó con uno del sistema (mismo importe + referencia coincide). |
| **En banco, no en sistema** | Está en el archivo del banco pero no se encontró en el sistema. |
| **Posibles repetidos en sistema** | Movimientos del sistema duplicados entre sí (misma referencia, descripción, importe **y** fecha). |
| **Devoluciones de cheque** | Movimientos del banco con la leyenda de cheque (se apartan antes de comparar). |

Además: soporta **varios bancos y formatos** (CSV, `.xls`, `.xlsx`), reutiliza los
mismos lectores que la pantalla de "Identificación Bancaria", y permite habilitar o
deshabilitar bancos en el selector.

---

## 2. Mini‑glosario de Python (lo que verás en el código)

| Concepto | Qué es | Ejemplo en el proyecto |
|---|---|---|
| **Módulo** | Un archivo `.py`. Se importa con `from ... import ...`. | `from .modelo import MovimientoConciliacion` |
| **Paquete** | Una carpeta con `__init__.py`. Agrupa módulos. | `app/conciliacion/` |
| **Función** | `def nombre(args) -> tipo:` | `def conciliar(mov_banco, mov_sistema):` |
| **Type hints** | Anotaciones de tipo (`: str`, `-> list`). **No** obligan nada en runtime, solo documentan y ayudan al editor. | `path: str -> str \| None` |
| **`dataclass`** | Clase "de datos": defines los campos y Python genera el constructor. | `MovimientoConciliacion` |
| **`list` / `dict` / `tuple` / `set`** | Lista `[...]`, diccionario `{clave: valor}`, tupla `(...)` (inmutable), conjunto `{...}` (sin repetidos). | `mov_banco: list[...]` |
| **List comprehension** | Crear una lista en una línea: `[f(x) for x in lista if cond]`. | `[_a_conciliacion(m) for m in movimientos]` |
| **f-string** | Texto con variables: `f"Banco: {nombre}"`. | mensajes de la UI |
| **`None`** | "Nada / vacío" (como `null`). | `banco: str \| None = None` |
| **Closure** | Una función definida *dentro* de otra que "recuerda" las variables de afuera. Lo usamos mucho en la UI. | los handlers dentro de `construir_tab_conciliaciones` |
| **Decorador** | `@algo` encima de una función/clase que la "envuelve". | `@dataclass`, `@property`, `@classmethod` |
| **`@property`** | Un método que se usa como si fuera un atributo (sin `()`). | `movimiento.texto` |

---

## 3. Arquitectura y flujo de datos

```
                 ┌───────────────────────── app/parsers/ (lectura de bancos) ───────────────────────────┐
Archivo banco →  │  lectura.py  →  módulo del banco (bbva.py, banorte.py...)  ó  excel_columnas.py      │ → list[Movimiento]
(.csv/.xls/.xlsx)│  (detecta el formato por sus bytes)     (lógica propia)        (mapeo por columnas)  │
                 └──────────────────────────────────────────────┬───────────────────────────────────────┘
                                                                │
                          app/conciliacion/lector_banco.py ─────┤  convierte Movimiento → MovimientoConciliacion
                                                                ▼
Reporte sistema →  ingresos_diversos.py ─┐                 mov_banco  (list[MovimientoConciliacion])
(Ingresos Diversos)                      │                      │
                                         ├──► mov_sistema───────┤
BigQuery (futuro) → services/            │                      ▼
  bigquery_repository.py  ───────────────┘        conciliador.py  →  conciliar(mov_banco, mov_sistema)
                                                                │
                                                                ▼
                                                  ResultadoConciliacion  (los 5 grupos)
                                                                │
                                                                ▼
                                      app/conciliacion/vista.py  →  pinta las tablas en la pestaña Flet
```

**Idea clave:** todo se normaliza a **un solo tipo de dato** (`MovimientoConciliacion`),
venga del banco o del sistema. El motor (`conciliador.py`) solo trabaja con ese tipo,
sin importar de qué banco o formato vino.

---

## 4. Estructura de archivos (lo que creamos/tocamos)

```
app/
├── services/                        ← capa nueva: acceso a BigQuery
│   ├── bigquery_cliente.py          ← cliente + configuración compartidos
│   └── bigquery_repository.py       ← consulta datos "crudos" del sistema (BigQuery)
│
├── conciliacion/                    ← el módulo nuevo
│   ├── modelo.py                    ← MovimientoConciliacion + ResultadoConciliacion
│   ├── conciliador.py               ← el MOTOR de comparación
│   ├── lector_banco.py              ← adaptador: parsers → MovimientoConciliacion
│   ├── ingresos_diversos.py         ← lee el reporte de Ingresos Diversos (sistema)
│   └── vista.py                     ← la interfaz (pestaña Flet)
│
├── parsers/                         ← lectura de bancos (UNIFICADO con identificación)
│   ├── __init__.py                  ← registro central + detectar_banco / parsear_archivo
│   ├── lectura.py                   ← lector universal de tablas (csv/xls/xlsx)
│   ├── excel_columnas.py            ← lector genérico por columnas + configs de bancos
│   ├── base.py                      ← utilidades (clean_text, parse_money...)
│   ├── santander.py, banorte.py, banregio.py, bajio.py, bbva.py   ← módulos por banco
│
├── dashboard_cobranza.py            ← (modificado) ahora usa services/bigquery_cliente
└── main.py                          ← (modificado) agrega la pestaña "Conciliaciones Bancarias"
```

---

## 5. El dato central: `MovimientoConciliacion` (modelo.py)

Es una **dataclass**: solo declaramos los campos y Python genera el constructor.

```python
@dataclass
class MovimientoConciliacion:
    fecha: Optional[date]      # fecha del movimiento (o None)
    descripcion: str           # concepto/descripción (el texto "grande")
    referencia: str            # la referencia/folio
    importe: float             # monto, siempre positivo, 2 decimales
    naturaleza: str = "A"      # 'A' abono / 'C' cargo  (default "A")
    saldo: Optional[float] = None
    origen: str = ""           # "BANCO:BBVA"  ó  "SISTEMA"
    raw: dict = field(default_factory=dict)   # la fila original completa (para auditar)
```

- `origen` distingue de qué lado viene el movimiento (`"BANCO:<nombre>"` o `"SISTEMA"`).
  De ahí sale la columna **Banco** de las tablas.
- `raw` guarda la fila original tal cual (por eso podemos mostrar la columna
  **Conciliación** en "posibles repetidos": está en `raw["CONCILIACION"]`).

También hay:
- `texto` (**@property**): `descripcion + " " + referencia`. Se usa para detectar la
  leyenda de "cheque".
- `desde_sistema(fila)` (**@classmethod**): crea un movimiento del lado sistema a
  partir de una fila de BigQuery.

`ResultadoConciliacion` es otra dataclass que solo **agrupa las 5 listas** de salida
(`conciliados`, `solo_banco`, `solo_sistema`, `devoluciones_cheque`,
`posibles_repetidos_sistema`) y tiene un `resumen` con los conteos.

---

## 6. El motor: `conciliador.py`

Una sola función pública: `conciliar(mov_banco, mov_sistema) -> ResultadoConciliacion`.

### Regla de emparejamiento (igual para todos los bancos)

> Un movimiento del banco **concilia** con uno del sistema cuando:
> 1. el **importe** es el mismo (2 decimales), **y**
> 2. la **referencia del sistema** (texto normalizado) **aparece dentro** del
>    concepto/descripción del banco **o** dentro de la referencia del banco.

Es decir: **importe igual + (check por concepto O check por referencia)**.

`normalizar()` (de `app/textutils.py`) pone en mayúsculas, quita acentos y símbolos
(así `'003871297` y `003871297` quedan iguales, y `112` se encuentra dentro de
`REF 112 CVE...`).

### Cómo está implementado (paso a paso)

```python
def conciliar(mov_banco, mov_sistema):
    # 1. Apartar las devoluciones de cheque del lado banco
    devoluciones = [m for m in mov_banco if es_devolucion_cheque(m)]
    banco = [m for m in mov_banco if not es_devolucion_cheque(m)]

    # 2. Agrupar el sistema por importe -> {importe: [(mov, ref_normalizada), ...]}
    por_importe = defaultdict(list)          # dict que crea listas vacías solo
    for s in mov_sistema:                    #   con pedirlas (no hay que inicializar)
        por_importe[round(s.importe, 2)].append([s, normalizar(s.referencia)])

    conciliados, solo_banco = [], []
    consumidos = set()                       # ids de sistema ya usados (no repetir)
    for b in banco:
        concepto   = normalizar(b.descripcion)
        referencia = normalizar(b.referencia)
        elegido = None
        for s, aguja in por_importe.get(round(b.importe, 2), []):
            if id(s) in consumidos or not aguja:
                continue
            if aguja in concepto or aguja in referencia:   # <- los dos checks
                elegido = s
                break
        if elegido is not None:
            consumidos.add(id(elegido)); conciliados.append((b, elegido))
        else:
            solo_banco.append(b)

    solo_sistema = [s for s in mov_sistema if id(s) not in consumidos]
    return ResultadoConciliacion(conciliados, solo_banco, solo_sistema,
                                 devoluciones, _posibles_repetidos(mov_sistema))
```

Notas de Python:
- `defaultdict(list)`: un diccionario que, si pides una clave que no existe, te da una
  lista vacía automáticamente. Sirve para **agrupar** sin escribir `if clave not in ...`.
- Agrupar por importe primero **acota la búsqueda**: solo comparamos textos entre
  movimientos con el mismo monto (más rápido y menos falsos positivos).
- `id(s)` es la identidad del objeto en memoria; con `consumidos` evitamos usar dos
  veces el mismo movimiento del sistema.
- `aguja in concepto` es "¿la referencia del sistema está contenida en el texto?".

### Posibles repetidos en sistema

```python
def _posibles_repetidos(sistema):
    grupos = defaultdict(list)
    for s in sistema:
        clave = (normalizar(s.referencia), normalizar(s.descripcion),
                 round(s.importe, 2), s.fecha)         # <- incluye la FECHA
        grupos[clave].append(s)
    return [m for miembros in grupos.values() if len(miembros) >= 2 for m in miembros]
```

Agrupa los movimientos del sistema por **(referencia, descripción, importe, fecha)** y
devuelve todos los que caen en un grupo de 2 o más (posibles duplicados capturados el
**mismo día**).

### Devoluciones de cheque

```python
LEYENDA_DEVOLUCION_CHEQUE = re.compile(r"CHEQUE", re.IGNORECASE)
def es_devolucion_cheque(m): return bool(LEYENDA_DEVOLUCION_CHEQUE.search(m.texto))
```

Es una **expresión regular** configurable. Hoy marca cualquier texto que contenga
"CHEQUE". Cuando los usuarios den la leyenda exacta, se cambia solo esa línea (por
ejemplo `r"DEV\.?\s*CHEQUE"`).

---

## 7. Lectura de bancos: `app/parsers/` (unificado)

Antes había dos sistemas separados (identificación vs. conciliación). Ahora hay **uno
solo**. Cada banco se lee a `Movimiento` (el dataclass de `app/models.py`) y
conciliación lo convierte después.

### `lectura.py` — lector universal de tablas

`leer_tabla(path) -> list[list]` abre **cualquier** formato y devuelve las filas como
listas de celdas. **Detecta el formato por los primeros bytes del archivo, no por la
extensión** (porque los portales a veces ponen la extensión equivocada):

| Primeros bytes | Formato | Cómo se lee |
|---|---|---|
| `PK\x03\x04` (ZIP) | `.xlsx`/`.xlsm` | openpyxl (modo normal → read_only → respaldo XML crudo) |
| `\xD0\xCF\x11\xE0` (OLE2) | `.xls` binario antiguo | **No soportado**: avisa "guárdalo como .xlsx o .csv" |
| `<?xml` / `mso-application` | `.xls`/`.xml` SpreadsheetML (Excel 2003) | `xml.etree` |
| texto | `.csv` | módulo `csv` con detección de separador y codificación |

Detalle importante que arreglamos: algunos exportadores (BanBajío) declaran mal la
"dimensión" de la hoja y el modo `read_only` de openpyxl **no ve los datos**. Por eso
`leer_tabla` intenta primero el **modo normal**, y si un modo devuelve vacío, pasa al
siguiente.

### `excel_columnas.py` — lector genérico "por columnas"

Para bancos cuyo `.xlsx` se puede leer solo **mapeando encabezados a campos**. La
clase `BancoColumnasExcel` recibe qué encabezados corresponden a cada campo:

```python
BancoColumnasExcel(
    "BBVA",                                          # nombre del banco
    firma={"CONCEPTO_DE_PAGO", "CLAVE_DE_RASTREO", "CUENTA_ORDENANTE"},  # cómo se reconoce
    fila_encabezado=2,                               # en qué renglón está el encabezado
    cols_fecha={"FECHA"},
    cols_descripcion={"CONCEPTO_DE_PAGO"},           # -> el "concepto" para el match
    cols_referencia={"REFERENCIA"},
    cols_importe={"IMPORTE"},                        # una sola columna con signo
    cols_saldo={"SALDO"},
    abono_es_positivo=True,
    en_conciliacion=True,                            # ¿se lista en el selector?
),
```

- La **firma** (`firma`) es el conjunto de encabezados que **deben estar todos**
  presentes para reconocer el banco. Sirve para distinguir formatos parecidos.
- Los encabezados se comparan **normalizados** (`"Fecha Operación"` → `FECHA_OPERACION`).
- Soporta importe en **dos columnas** (`cols_abono`/`cols_cargo`), **una columna con
  signo** (`cols_importe` + `abono_es_positivo`), o **por una columna TIPO**
  (`naturaleza_por_tipo`, caso Scotiabank).

La lista `BANCOS_COLUMNAS` al final del archivo tiene todas estas configuraciones.

### Módulos por banco (`santander.py`, `banorte.py`, ...)

Los 5 bancos validados tienen **su propio módulo** con lógica a la medida (saltar
comisiones/COMPENSACIÓN, buscar el encabezado dinámicamente, dos layouts de BBVA,
etc.). Cada módulo expone:
- `detect(path) -> bool`: ¿este archivo es de este banco?
- `parse(path) -> list[Movimiento]`: leerlo.
- `BANCO = "..."` y `EN_CONCILIACION = True/False`.

### `__init__.py` — el registro central

Reúne **módulos + configuraciones de columnas** y expone las funciones que usa todo lo
demás:

| Función | Qué hace |
|---|---|
| `detectar_banco(path)` | Recorre todos los lectores y devuelve el nombre del primero que reconozca el archivo (o `None`). |
| `parsear_archivo(path, banco)` | Lee el archivo con el lector de ese banco → `list[Movimiento]`. |
| `bancos_conciliacion()` | Nombres **habilitados** para el selector (respeta el flag `EN_CONCILIACION`). |
| `es_banco_conciliacion(banco)` | ¿Ese banco está habilitado? |

Orden de detección: **primero los módulos** (formatos de identificación), luego las
configuraciones `.xlsx` por columnas. Un mismo banco puede tener varios lectores (BBVA
`.xls` y BBVA `.xlsx`); `parsear_archivo` elige el que reconozca el archivo.

---

## 8. Sistema (lo que se compara contra el banco)

Dos orígenes, seleccionables en la UI:

### `ingresos_diversos.py` — el reporte Excel

`cargar_ingresos_diversos(path) -> list[MovimientoConciliacion]`. Ese reporte también
trae la "dimensión" mal declarada, así que se lee **directo del XML del `.xlsx`** (zip
+ `xml.etree`). Localiza el encabezado por contenido (busca "Referencia" y "Movimiento")
y mapea: `Referencia` → referencia, `Movimiento` → importe, `Fecha Envío` → fecha,
`Razón Social` → descripción, y guarda todo en `raw` (de ahí sale la columna
`Conciliación`).

### `services/bigquery_repository.py` — la nube (pendiente)

`BigQueryRepository.movimientos_crudos(fecha_inicio, fecha_fin)` consulta filas de
BigQuery. **La tabla real todavía no existe**, así que hoy la referencia sale como
literal vacío (`COL_REFERENCIA = "''"`). Cuando entreguen la tabla, se ajustan las
constantes `COL_*` del inicio del archivo (una línea por columna).

`services/bigquery_cliente.py` centraliza el cliente y la configuración de BigQuery,
para que el Dashboard y la Conciliación **compartan** la misma conexión sin depender
uno del otro.

---

## 9. El adaptador: `lector_banco.py`

Es el "puente" entre los parsers (que dan `Movimiento`) y el motor (que quiere
`MovimientoConciliacion`).

- `normalizar_banco(path, banco=None) -> (nombre, movimientos, estado)`
  - Detecta o **fuerza** el banco (si el usuario lo eligió en el selector) y lo lee.
  - `estado` puede ser:
    - `"ok"` — todo bien.
    - `"no_reconocido"` — no se identificó ningún banco.
    - `"no_habilitado"` — se reconoció un banco que **no está habilitado** (la UI
      avisa "comunícate para validar el formato").
- `_a_conciliacion(m)` — convierte un `Movimiento` a `MovimientoConciliacion`:
  `descripcion = concepto + descripción` del banco, `referencia = referencia`,
  `importe = abono`, `origen = "BANCO:<nombre>"`.

---

## 10. Sistema de bancos habilitados (el flag)

Cada banco tiene una variable que decide **si aparece en el selector de conciliaciones**:
- Bancos con módulo: `EN_CONCILIACION = True/False` en su archivo (`santander.py`, etc.).
- Bancos por columnas: `en_conciliacion=True/False` en su config de `excel_columnas.py`.

Hoy **habilitados (5)**: Santander, BanRegio, Banorte, BanBajío, BBVA.
**Deshabilitados (inferidos, sin archivo real):** HSBC, Sabadell, Scotiabank, Bancoppel,
Intercam, Banamex, BX, Ve por Más.

Comportamiento:
- Si el archivo es de un banco habilitado → concilia.
- Si la autodetección da un banco **deshabilitado** → aviso "parece de X, comunícate
  para validar el formato" (y no concilia ese archivo).
- Si no se reconoce → aviso "no se reconoció el formato...".

---

## 11. La interfaz: `vista.py`

Función principal: `construir_tab_conciliaciones(page) -> (ft.Tab, ft.Control)`.
Devuelve la pestaña y su contenido; `main.py` los inserta en las pestañas de la app.

Está construida con **closures**: adentro de `construir_tab_conciliaciones` se definen
todos los handlers (`on_cargar_archivo`, `on_conciliar`, `_render`, ...) que
"recuerdan" las variables locales (la lista de archivos, los controles, etc.). Es el
patrón normal en Flet.

Partes principales:
- **Carga de archivos**: `archivos_banco` es una lista; cada archivo agrega un renglón
  con su nombre, su **selector de banco** (Auto-detectar o forzar) y un botón de quitar.
  Botones "Agregar archivos bancarios" y "Limpiar todos".
- **Origen del sistema**: radio "Excel de Ingresos Diversos" / "Datos en la nube".
- **`on_conciliar`**: normaliza **cada** archivo con su banco, junta todos los
  movimientos, trae el sistema, llama `conciliar(...)` y pinta el resultado. Los
  archivos con problema se listan en un diálogo y se concilian los demás.
- **`_render`**: arma las 4 tablas (cada una es un panel desplegable `ExpansionTile`).
  Las tablas del lado banco llevan columna **Banco**; la de repetidos lleva columna
  **Conciliación**.

Detalles de UI que resolvimos en el camino (por si aparecen de nuevo):
- El `FilePicker` en Flet 0.85 **no** se agrega a `page.overlay` (da "Unknown control").
- `page.open(...)` no existe: se usa un `SnackBar` en overlay con `.open = True`.
- Fechas del calendario en español: `page.locale_configuration` en `main.py`, y el
  `DateRangePicker` en modo `CALENDAR_ONLY`.

---

## 12. Cómo hacer cambios comunes

### Agregar / validar un banco nuevo
1. **Si su `.xlsx` es un mapeo simple de columnas** → agrega una `BancoColumnasExcel`
   en `excel_columnas.py` (firma + columnas) con `en_conciliacion=True`.
2. **Si necesita lógica especial** (saltar comisiones, encabezado dinámico, varios
   layouts) → crea un módulo `app/parsers/<banco>.py` con `detect`, `parse`, `BANCO` y
   `EN_CONCILIACION`, y regístralo en el `PARSERS` de `parsers/__init__.py`.
3. Verifica con un archivo real: `detectar_banco(ruta)` y `parsear_archivo(ruta, "X")`.

### Habilitar un banco que hoy está deshabilitado
Cambia su flag a `True`: `EN_CONCILIACION` (módulo) o `en_conciliacion=True`
(config de columnas).

### Cambiar la leyenda de "devolución de cheque"
Edita `LEYENDA_DEVOLUCION_CHEQUE` en `conciliador.py` (es una expresión regular).

### Conectar la tabla real de BigQuery
Ajusta las constantes `COL_DESCRIPCION`, `COL_REFERENCIA`, `COL_IMPORTE`, `COL_FECHA`
(y el filtro) al inicio de `services/bigquery_repository.py`.

---

## 13. Cómo probar rápido (sin abrir la app)

Desde la raíz del proyecto:

```bash
# ¿Compila todo?
python -m compileall -q app/parsers app/conciliacion

# ¿Detecta y lee un banco?
python -c "from app.parsers import detectar_banco, parsear_archivo; \
p=r'ruta\al\archivo.csv'; print(detectar_banco(p), len(parsear_archivo(p, detectar_banco(p))))"
```

Y para probar el emparejamiento con datos inventados se crean `MovimientoConciliacion`
a mano y se llama `conciliar([...banco...], [...sistema...])` (ver ejemplos en las
verificaciones que hicimos durante el desarrollo).

---

## 14. Resumen de decisiones importantes

- **Un solo tipo de dato** (`MovimientoConciliacion`) para banco y sistema.
- **Un solo sistema de lectura de bancos** (`app/parsers/`), compartido con
  identificación; conciliación solo convierte la salida.
- **Regla de match universal**: importe igual + la referencia del sistema aparece en
  el concepto o en la referencia del banco.
- **Repetidos**: mismos referencia + descripción + importe + fecha.
- **Formatos por bytes, no por extensión**; soporte csv/xls/xlsx con respaldo XML.
- **Flag por banco** para controlar qué aparece en el selector, con aviso de
  "comunícate a validar" para formatos no habilitados.
