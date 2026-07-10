# Cobranza Multiherramienta SIPP

Aplicación de escritorio (Flet) para **Grupo Petroil** que automatiza la
conciliación bancaria y la carga de movimientos a **SIPP** mediante RPA
(Playwright), integrando el buzón de **Office 365** para los pagos de contado.

> Estado: en uso interno. El RPA se ha validado de punta a punta contra el
> entorno de pruebas de SIPP (`stage.sipp.petroil.dev`).

## Funcionalidades

La app tiene tres pestañas:

### 1. Conciliación Bancaria (archivo `.csv`)
- Carga un estado de cuenta bancario en CSV (Santander / BanRegio, etc.) y lo
  procesa: detecta banco, normaliza referencias e identifica clientes contra el
  catálogo (`Catalogos/Cuentas_Clientes/`).
- Muestra totales (movimientos, identificados, % identificado, total de abonos).
- **Buscar folios en SIPP**: RPA que busca folios pendientes en SIPP y los
  empareja, agregando cuentas nuevas al catálogo.
- **Cargar a SIPP (Ingresos Diversos)**: RPA que sube el CSV a "Ingresos
  Diversos - Agregar", asigna en la previsualización el cliente identificado a
  cada movimiento (la sucursal la auto-sugiere SIPP) y confirma el alta.

### 2. Contado (buzón O365)
- Lee de la bandeja de entrada los correos con "Contado" en el asunto.
- Filtros por fecha (datepicker) y por concepto/asunto.
- **Extraer pagos de contado**: por cada correo descarga el comprobante
  adjunto, le extrae el texto (PDF nativo u **OCR** si es imagen / PDF
  escaneado) y sugiere Referencia y Cliente (auto-confirmado si el match es
  único contra el catálogo).
- Tabla editable por pago con **Cuenta Bancaria por fila** (buscable).
- **Cargar Pagos de Contado a SIPP**: RPA que agrupa los pagos por cuenta
  bancaria y arma una conciliación por cuenta. Con el switch *Enviar
  automáticamente* hace Guardar + adjuntar comprobante + Guardar y Enviar; sin
  él, deja una pestaña por cuenta abierta para revisión manual.

### 3. Catálogos
- Visualización y filtrado de los catálogos de clientes/cuentas.

## Requisitos

- **Python 3.12+**
- **Tesseract OCR** (para leer comprobantes que vienen como imagen):
  - macOS: `brew install tesseract tesseract-lang`
- Navegador de Playwright (Chromium), que se instala con `playwright install`.
- Una cuenta de Office 365 con una App registrada en Microsoft Graph
  (Client ID + Tenant ID).

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Configuración

Copia `.env.example` a `.env` y rellena tus credenciales de Microsoft Graph:

```bash
cp .env.example .env
```

```dotenv
GRAPH_CLIENT_ID=tu-client-id
GRAPH_TENANT_ID=tu-tenant-id
```

La primera vez se abrirá el flujo de autenticación de O365; el token se cachea
localmente en `o365_token.txt`.

## Uso

```bash
python main.py
```

### Modo de pruebas (SIPP stage)

El RPA apunta a producción por defecto. Para usar el entorno de pruebas:

```bash
SIPP_ENV=test python main.py
```

(`SIPP_ENV` acepta `test`, `stage`, `staging`, `qa`, `pruebas` o `dev`). En modo
pruebas la app muestra un badge rojo **"SIPP PRUEBAS"** en el encabezado.

## Empaquetado (build) e ícono de la app

La app se distribuye por `git` y se **auto-actualiza** desde GitHub al iniciar
(y con el botón de actualización del encabezado). Para generar un ejecutable
nativo con el ícono de Grupo Petroil se usa `flet build`.

### Windows: instalación recomendada (sin build, con auto-actualización)

Para desplegar en varias PCs conservando la **auto-actualización** por git, se
corre la app desde el código (`python main.py`) — sin Flutter/build. Clona el
repo en cada PC y ejecuta desde la raíz:

```bat
instalar_windows.bat
```

El script: instala/valida **Python 3.12**, **git** y **Tesseract OCR** con
`winget`; crea el `.venv`; instala `requirements.txt` y **Chromium** (Playwright);
fija el remoto `origin` a **HTTPS** (el repo es público → `git pull` sin
credenciales); genera el lanzador `iniciar_cobranza.bat` (corre `pythonw main.py`,
sin consola) y un **acceso directo** en el escritorio con `Logo_Petroil.ico`.
**No** descarga Flutter. Cada PC se auto-actualiza desde GitHub al abrir la app.

### Windows: build de un ejecutable (opcional, sin auto-actualización)

Genera un `.exe` autocontenido (requiere descargar Flutter, ~2 GB la primera
vez). El `.exe` **no** se auto-actualiza. Ejecuta desde la raíz:

```bat
empaquetar_windows.bat
```

El script:
- instala/valida Python 3.12 con `winget` si hace falta;
- crea el entorno virtual `.venv`;
- instala `requirements.txt`;
- instala Chromium para Playwright;
- instala/valida Tesseract OCR;
- genera el ejecutable con `flet build windows`;
- crea un acceso directo en el escritorio usando `Logo_Petroil.ico`.

El `.exe` final queda dentro de `build/windows/`.

### macOS: instalación recomendada (sin build, con auto-actualización)

Igual que en Windows, para desplegar en varias Mac conservando la
**auto-actualización** por git, se corre la app desde el código
(`python main.py`). Clona el repo en cada Mac y ejecuta desde la raíz:

```bash
./instalar_mac.command
```

(o doble clic en `instalar_mac.command` desde Finder — se abre una Terminal
con el progreso).

El script: instala/valida **Homebrew**, **Python 3.12**, **git** y
**Tesseract OCR** (`brew`); fija el remoto `origin` a **HTTPS** (el repo es
público → `git pull` sin credenciales); crea el `.venv`; instala
`requirements.txt` y **Chromium** (Playwright); genera un icono `.icns` a
partir de `assets/icon.png`; y genera el lanzador **`iniciar_cobranza.command`**
(corre `.venv/bin/python3 main.py` desacoplado de la Terminal, que se cierra
sola) junto con un **acceso directo** en el Escritorio, aplicándole a ambos
el ícono de Grupo Petroil. Cada Mac se auto-actualiza desde GitHub al abrir
la app.

> Homebrew no se instala automáticamente (pide tu password de Mac); si falta,
> el script te indica instalarlo desde https://brew.sh y volver a correrlo.

> **Por qué `.command` y no un `.app`:** se probó envolver el lanzador en un
> `.app` (como en Windows), pero macOS marca todo ejecutable creado
> localmente con el atributo `com.apple.provenance` — que, a diferencia del
> viejo `com.apple.quarantine`, no se puede quitar — y Gatekeeper lo rechaza
> (`spctl` → `rejected`) aunque se firme ad-hoc, así que Finder no lo abre ni
> con doble clic. Firmarlo de verdad requiere una cuenta de Apple Developer
> (pago). Un `.command` no tiene ese problema: Terminal lo interpreta
> directamente, sin pasar por la verificación de Gatekeeper para apps. El
> ícono personalizado **sí** se puede aplicar sin tocar Gatekeeper — es solo
> metadata de Finder (`NSWorkspace.setIcon`, vía `osascript -l JavaScript`),
> no un ejecutable nuevo.

El ícono se toma **automáticamente** de **`assets/icon.png`** (ya incluido;
idealmente cuadrado y de alta resolución, p. ej. 512×512 o 1024×1024). En
Windows, además, la ventana usa `assets/Logo_Petroil.ico` en tiempo de ejecución
(`page.window.icon`).

> `flet build` (0.85.3) **no** acepta `--icon`; usa `assets/icon.png` por
> convención. No pases ese flag o fallará con `unrecognized arguments: --icon`.

```bash
# macOS (.app)
flet build macos

# Windows (.exe)
flet build windows
```

Notas:
- En **macOS corriendo con `python main.py`** (modo desarrollo) el ícono del dock
  lo trae el cliente de Flet y **no** es configurable; solo se personaliza al
  empaquetar con `flet build`.
- En **Windows** el ícono ya se aplica en desarrollo vía `page.window.icon`.

## Estructura

```
main.py                     # punto de entrada (ft.run)
app/
  main.py                   # UI (Flet): pestañas, tablas, diálogos
  mailbox_o365.py           # integración Microsoft Graph / O365
  extraccion_adjuntos.py    # texto de PDF / OCR de imágenes y PDFs escaneados
  pagos_contado.py          # extracción y sugerencias de pagos de contado
  ingresos_diversos.py      # orquesta la carga a SIPP (CSV y contado)
  matcher.py / textutils.py # identificación de clientes y normalización
  catalogo.py / clientes.py / cuentas_bancarias.py / sucursales.py
rpa/
  automation.py             # RPA de SIPP con Playwright (login, navegación, formularios)
Catalogos/                  # catálogos de clientes, cuentas y sucursales
```

## Notas de seguridad

Los archivos `.env`, `o365_token.txt` y `sipp_credenciales.json` contienen
credenciales/sesiones y están en `.gitignore` — **no se suben al repositorio**.
La opción "Recordar credenciales" guarda usuario/contraseña de SIPP en texto
plano local (`sipp_credenciales.json`); úsala solo en equipos de confianza.
