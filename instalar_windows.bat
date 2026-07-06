@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "APP_NAME=MultiHerramienta de Cobranza"
set "ICON_FILE=%CD%\Logo_Petroil.ico"
set "LAUNCHER=%CD%\iniciar_cobranza.bat"

echo.
echo ============================================================
echo  Instalacion (sin build) - %APP_NAME%
echo ============================================================
echo  Corre la app desde el codigo (python main.py) y crea un
echo  acceso directo. NO usa Flutter/build y CONSERVA la
echo  auto-actualizacion por git.
echo ============================================================
echo.

where winget >nul 2>&1
if errorlevel 1 (
    echo [AVISO] winget no esta disponible. Si falta Python, git o Tesseract,
    echo         instalalos manualmente y vuelve a ejecutar este archivo.
    set "HAS_WINGET=0"
) else (
    set "HAS_WINGET=1"
)

call :ensure_python
if errorlevel 1 goto :fail

call :ensure_git

rem Repo publico: fijar el remoto a HTTPS para que la auto-actualizacion
rem (git pull) funcione sin SSH ni credenciales en cualquier PC.
git rev-parse --is-inside-work-tree >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Fijando remoto origin a HTTPS para auto-actualizacion...
    git remote set-url origin https://github.com/Anibal-F/Cobranza_MultiherramientaSIPP.git
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creando entorno virtual .venv...
    py -3.12 -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo [1/5] Entorno virtual .venv encontrado.
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :fail

echo [2/5] Actualizando pip y herramientas base...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :fail

echo [3/5] Instalando dependencias de la aplicacion...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [4/5] Instalando navegador Chromium para Playwright...
python -m playwright install chromium
if errorlevel 1 goto :fail

call :ensure_tesseract
if errorlevel 1 goto :fail

echo [5/5] Creando lanzador y acceso directo...

rem --- Lanzador: corre la app SIN consola (pythonw) desde su carpeta ---
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo start "" ".venv\Scripts\pythonw.exe" main.py
) > "%LAUNCHER%"

if not exist "%ICON_FILE%" (
    echo [AVISO] No se encontro %ICON_FILE%. El acceso directo usara el icono por defecto.
)

set "SHORTCUT_NAME=%APP_NAME%.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "$lnk = Join-Path $desktop $env:SHORTCUT_NAME;" ^
  "$shell = New-Object -ComObject WScript.Shell;" ^
  "$sc = $shell.CreateShortcut($lnk);" ^
  "$sc.TargetPath = $env:LAUNCHER;" ^
  "$sc.WorkingDirectory = Split-Path $env:LAUNCHER;" ^
  "$sc.WindowStyle = 7;" ^
  "if (Test-Path $env:ICON_FILE) { $sc.IconLocation = $env:ICON_FILE };" ^
  "$sc.Save(); Write-Host $lnk"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  Listo.
echo  Lanzador:        %LAUNCHER%
echo  Acceso directo:  Escritorio\%SHORTCUT_NAME%
echo.
echo  Doble clic en el acceso directo para abrir la app.
echo  La app se auto-actualiza desde GitHub al iniciar.
echo ============================================================
echo.
pause
exit /b 0

:ensure_python
echo [0/5] Verificando Python 3.12...
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    py -3.12 --version
    exit /b 0
)
echo [INFO] Python 3.12 no esta instalado.
if "%HAS_WINGET%"=="1" (
    echo [INFO] Instalando Python 3.12 con winget...
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
    if errorlevel 1 exit /b 1
    py -3.12 --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python se instalo, pero no quedo disponible en esta consola.
        echo         Cierra esta ventana, abre una nueva y ejecuta de nuevo este .bat.
        exit /b 1
    )
    exit /b 0
)
echo [ERROR] Instala Python 3.12 desde https://www.python.org/downloads/windows/
echo         Marca la opcion "Add python.exe to PATH" durante la instalacion.
exit /b 1

:ensure_git
echo [0.5/5] Verificando git (necesario para la auto-actualizacion)...
where git >nul 2>&1
if not errorlevel 1 (
    exit /b 0
)
echo [INFO] git no esta instalado.
if "%HAS_WINGET%"=="1" (
    echo [INFO] Instalando Git con winget...
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    echo [INFO] Si git no queda en el PATH, cierra y reabre la consola.
    exit /b 0
)
echo [AVISO] Sin git, la app funciona pero NO se auto-actualizara.
echo         Instala Git desde https://git-scm.com/download/win
exit /b 0

:ensure_tesseract
echo [4.5/5] Verificando Tesseract OCR...
where tesseract >nul 2>&1
if not errorlevel 1 (
    tesseract --version
    exit /b 0
)
echo [INFO] Tesseract OCR no esta instalado. Es necesario para OCR de comprobantes.
if "%HAS_WINGET%"=="1" (
    echo [INFO] Instalando Tesseract OCR con winget...
    winget install --id UB-Mannheim.TesseractOCR -e --source winget --accept-package-agreements --accept-source-agreements
    if errorlevel 1 exit /b 1
    echo [INFO] Si OCR falla al abrir la app, reinicia Windows para refrescar PATH.
    exit /b 0
)
echo [ERROR] Instala Tesseract OCR manualmente y vuelve a ejecutar este .bat:
echo         https://github.com/UB-Mannheim/tesseract/wiki
exit /b 1

:fail
echo.
echo ============================================================
echo  La instalacion no se completo. Revisa el error anterior.
echo ============================================================
echo.
pause
exit /b 1
