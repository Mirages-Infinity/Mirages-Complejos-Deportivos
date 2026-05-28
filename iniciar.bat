@echo off
cd /d "%~dp0"

if not exist "venv\" (
    echo Entorno virtual no encontrado. Creando...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: No se pudo crear el entorno virtual. Verifica que Python este instalado.
        pause
        exit /b 1
    )
    call venv\Scripts\activate.bat
    echo Instalando dependencias desde requirements.txt...
    pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
) else (
    call venv\Scripts\activate.bat
)

echo Iniciando Tailwind CSS (watch)...
start "Tailwind CSS" cmd /k "tailwindcss -i static/css/input.css -o static/css/output.css --watch"

timeout /t 2 /nobreak >nul

echo Iniciando servidor Django...
start "Django Server" cmd /k "%~dp0venv\Scripts\python.exe manage.py runserver"

echo.
echo Ambos procesos iniciados.
echo   - Tailwind CSS: compilando en tiempo real
echo   - Django: http://127.0.0.1:8000
echo.
echo Cierra las ventanas de consola para detener los servidores.
