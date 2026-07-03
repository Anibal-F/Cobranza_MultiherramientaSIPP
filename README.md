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

El ícono se toma de **`assets/icon.png`** (ya incluido; idealmente cuadrado y de
alta resolución, p. ej. 512×512 o 1024×1024). En Windows, además, la ventana usa
`assets/Logo_Petroil.ico` en tiempo de ejecución (`page.window.icon`).

```bash
# macOS (.app)
flet build macos --icon assets/icon.png

# Windows (.exe)
flet build windows --icon assets/icon.png
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
