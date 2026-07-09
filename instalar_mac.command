#!/bin/bash
# Instalacion (sin build) - MultiHerramienta de Cobranza - macOS
#
# Corre la app desde el codigo (python main.py) y crea un lanzador
# (.command) + acceso directo en el Escritorio. NO usa flet build y
# CONSERVA la auto-actualizacion por git (ver app/updater.py).
#
# Equivalente en macOS de instalar_windows.bat.

set -u
cd "$(dirname "$0")"
BASE_DIR="$(pwd)"

APP_NAME="MultiHerramienta de Cobranza"
LAUNCHER="$BASE_DIR/iniciar_cobranza.command"
ICON_PNG="$BASE_DIR/assets/icon.png"
ICON_ICNS="$BASE_DIR/assets/icon.icns"

fail() {
    echo
    echo "============================================================"
    echo " La instalacion no se completo. Revisa el error anterior."
    echo "============================================================"
    echo
    read -r -p "Presiona Enter para cerrar..." _
    exit 1
}

echo
echo "============================================================"
echo " Instalacion macOS - $APP_NAME"
echo "============================================================"
echo " Corre la app desde el codigo (python main.py) y crea un"
echo " lanzador (.command) + acceso directo en el Escritorio."
echo " CONSERVA la auto-actualizacion por git."
echo "============================================================"
echo

# --- [0/6] Homebrew (necesario para Python 3.12 / git / Tesseract) ---
echo "[0/6] Verificando Homebrew..."
if ! command -v brew >/dev/null 2>&1; then
    echo "[ERROR] Homebrew no esta instalado."
    echo "        Instalalo desde https://brew.sh (requiere tu password de Mac)"
    echo "        y vuelve a ejecutar este archivo."
    fail
fi
brew --version | head -n1

# --- [1/6] Python 3.12 ---
echo "[1/6] Verificando Python 3.12..."
if ! command -v python3.12 >/dev/null 2>&1; then
    echo "[INFO] Python 3.12 no esta instalado. Instalando con Homebrew..."
    brew install python@3.12 || fail
    if ! command -v python3.12 >/dev/null 2>&1; then
        echo "[ERROR] Python se instalo, pero 'python3.12' no quedo en el PATH."
        echo "        Cierra esta ventana, abre una Terminal nueva y ejecuta de nuevo este archivo."
        fail
    fi
fi
python3.12 --version

# --- [2/6] git (necesario para la auto-actualizacion) ---
echo "[2/6] Verificando git..."
if ! command -v git >/dev/null 2>&1; then
    echo "[INFO] git no esta instalado. Instalando con Homebrew..."
    brew install git || fail
fi
git --version

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "[INFO] Fijando remoto origin a HTTPS para auto-actualizacion..."
    git remote set-url origin https://github.com/Anibal-F/Cobranza_MultiherramientaSIPP.git
fi

# --- [3/6] Entorno virtual + dependencias + Playwright ---
if [ ! -x ".venv/bin/python3" ]; then
    echo "[3/6] Creando entorno virtual .venv..."
    python3.12 -m venv .venv || fail
else
    echo "[3/6] Entorno virtual .venv encontrado."
fi

echo "      Actualizando pip y herramientas base..."
.venv/bin/python3 -m pip install --upgrade pip setuptools wheel || fail

echo "      Instalando dependencias de la aplicacion..."
.venv/bin/python3 -m pip install -r requirements.txt || fail

echo "      Instalando navegador Chromium para Playwright..."
.venv/bin/python3 -m playwright install chromium || fail

# --- [4/6] Tesseract OCR ---
echo "[4/6] Verificando Tesseract OCR..."
if ! command -v tesseract >/dev/null 2>&1; then
    echo "[INFO] Tesseract OCR no esta instalado. Instalando con Homebrew..."
    brew install tesseract tesseract-lang || fail
fi
tesseract --version | head -n1

# --- [5/6] Icono .icns para el lanzador ---
echo "[5/6] Generando icono de la app (.icns)..."
if [ ! -f "$ICON_PNG" ]; then
    echo "[AVISO] No se encontro $ICON_PNG. El lanzador usara el icono generico."
else
    ICONSET="$BASE_DIR/.build_icon.iconset"
    rm -rf "$ICONSET"
    mkdir -p "$ICONSET"
    sips -z 16 16 "$ICON_PNG" --out "$ICONSET/icon_16x16.png" >/dev/null
    sips -z 32 32 "$ICON_PNG" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
    sips -z 32 32 "$ICON_PNG" --out "$ICONSET/icon_32x32.png" >/dev/null
    sips -z 64 64 "$ICON_PNG" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
    sips -z 128 128 "$ICON_PNG" --out "$ICONSET/icon_128x128.png" >/dev/null
    sips -z 256 256 "$ICON_PNG" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
    sips -z 256 256 "$ICON_PNG" --out "$ICONSET/icon_256x256.png" >/dev/null
    sips -z 512 512 "$ICON_PNG" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
    iconutil -c icns "$ICONSET" -o "$ICON_ICNS" || echo "[AVISO] No se pudo generar el .icns; se usara el icono generico."
    rm -rf "$ICONSET"
fi

# --- [6/6] Lanzador (.command) + acceso directo en el Escritorio ---
#
# NOTA: se descarto envolver el lanzador en un .app (bundle con icono propio)
# porque macOS marca cualquier ejecutable creado localmente con el atributo
# com.apple.provenance -- que no se puede quitar (a diferencia del viejo
# com.apple.quarantine) -- y Gatekeeper lo rechaza (spctl: "rejected") aunque
# se firme ad-hoc, asi que Finder no lo abre ni con doble clic. Un .command
# no tiene ese problema: Terminal lo interpreta directamente, sin pasar por
# la verificacion de Gatekeeper para "apps". El icono SI se puede personalizar
# (es solo metadata de Finder, no pasa por Gatekeeper) via NSWorkspace/JXA.
echo "[6/6] Creando lanzador y acceso directo..."

cat > "$LAUNCHER" <<LAUNCHERSCRIPT
#!/bin/bash
# Corre la app totalmente desacoplada de esta Terminal (doble fork: el
# subshell "( ... & )" termina en cuanto lanza python3, que asi queda
# reparentado a launchd y deja de figurar como proceso de esta ventana;
# sin esto, Terminal ve a python3 como hijo de la ventana y pregunta
# "Quieres finalizar los procesos..." al cerrarla).
#
# El cierre de la ventana usa el MISMO truco: si el osascript de cierre
# corriera dentro de esta ventana, Terminal se veria a si misma (bash +
# osascript) como "proceso corriendo" justo al pedir el cierre y mostraria
# el mismo dialogo. Por eso tambien va doblemente desacoplado, en su propio
# subshell en segundo plano.
cd "$BASE_DIR"
( nohup "$BASE_DIR/.venv/bin/python3" "$BASE_DIR/main.py" >/dev/null 2>&1 & )
( sleep 1; osascript -e 'tell application "Terminal" to close (first window whose name contains "iniciar_cobranza") saving no' >/dev/null 2>&1 & ) &
exit 0
LAUNCHERSCRIPT
chmod +x "$LAUNCHER"

DESKTOP_LINK="$HOME/Desktop/$APP_NAME.command"
rm -f "$DESKTOP_LINK"
ln -s "$LAUNCHER" "$DESKTOP_LINK"

if [ -f "$ICON_ICNS" ]; then
    osascript -l JavaScript -e '
        function run(argv) {
            ObjC.import("AppKit")
            var image = $.NSImage.alloc.initWithContentsOfFile(argv[0])
            $.NSWorkspace.sharedWorkspace.setIconForFileOptions(image, argv[1], 0)
            $.NSWorkspace.sharedWorkspace.setIconForFileOptions(image, argv[2], 0)
        }
    ' "$ICON_ICNS" "$LAUNCHER" "$DESKTOP_LINK" >/dev/null 2>&1 \
        && echo "      Icono aplicado al lanzador y al acceso directo." \
        || echo "[AVISO] No se pudo aplicar el icono; el lanzador usara el icono generico."
fi

echo
echo "============================================================"
echo " Listo."
echo " Lanzador:       $LAUNCHER"
echo " Acceso directo: ~/Desktop/$APP_NAME.command"
echo
echo " Doble clic en el acceso directo del Escritorio para abrir la app"
echo " (se abre una Terminal un instante y se cierra sola)."
echo " La app se auto-actualiza desde GitHub al iniciar."
echo "============================================================"
echo
read -r -p "Presiona Enter para cerrar..." _
exit 0
