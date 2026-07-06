@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "APP_NAME=Cobranza Multiherramienta SIPP"
set "ICON_FILE=%CD%\Logo_Petroil.ico"
set "BUILD_ICON=assets\icon.png"

echo.
echo ============================================================
echo  Empaquetado Windows - %APP_NAME%
echo ============================================================
echo.

where winget >nul 2>&1
if errorlevel 1 (
    echo [AVISO] winget no esta disponible. Si falta Python o Tesseract,
    echo         instalalos manualmente y vuelve a ejecutar este archivo.
    set "HAS_WINGET=0"
) else (
    set "HAS_WINGET=1"
)

call :ensure_python
if errorlevel 1 goto :fail

if not exist ".venv\Scripts\python.exe" (
    echo [1/6] Creando entorno virtual .venv...
    py -3.12 -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo [1/6] Entorno virtual .venv encontrado.
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :fail

echo [2/6] Actualizando pip y herramientas base...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :fail

echo [3/6] Instalando dependencias de la aplicacion...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [4/6] Instalando navegador Chromium para Playwright...
python -m playwright install chromium
if errorlevel 1 goto :fail

call :ensure_tesseract
if errorlevel 1 goto :fail

if not exist "%BUILD_ICON%" (
    echo [ERROR] No se encontro el icono de build: %BUILD_ICON%
    goto :fail
)

echo [5/6] Generando ejecutable de Windows con Flet...
flet build windows --icon "%BUILD_ICON%"
if errorlevel 1 goto :fail

set "EXE_PATH="
for /f "usebackq delims=" %%E in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$exe = Get-ChildItem -Path 'build\windows' -Filter '*.exe' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(flet|flutter|dart)\.exe$' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($exe) { $exe.FullName }"`) do set "EXE_PATH=%%E"

if not defined EXE_PATH (
    echo [ERROR] No se encontro el .exe dentro de build\windows.
    goto :fail
)

if not exist "%ICON_FILE%" (
    echo [AVISO] No se encontro %ICON_FILE%. El acceso directo usara el icono del ejecutable.
)

echo [6/6] Creando acceso directo en el escritorio...
set "SHORTCUT_NAME=%APP_NAME%.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$desktop = [Environment]::GetFolderPath('Desktop'); $lnk = Join-Path $desktop $env:SHORTCUT_NAME; $shell = New-Object -ComObject WScript.Shell; $sc = $shell.CreateShortcut($lnk); $sc.TargetPath = $env:EXE_PATH; $sc.WorkingDirectory = Split-Path $env:EXE_PATH; if (Test-Path $env:ICON_FILE) { $sc.IconLocation = $env:ICON_FILE }; $sc.Save(); Write-Host $lnk"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  Listo.
echo  Ejecutable: %EXE_PATH%
echo  Acceso directo: Escritorio\%SHORTCUT_NAME%
echo ============================================================
echo.
pause
exit /b 0

:ensure_python
echo [0/6] Verificando Python 3.12...
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

:ensure_tesseract
echo [4.5/6] Verificando Tesseract OCR...
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
echo  El empaquetado no se completo. Revisa el error anterior.
echo ============================================================
echo.
pause
exit /b 1
