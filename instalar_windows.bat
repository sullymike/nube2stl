@echo off
rem Instala nube2stl en este equipo (Windows) para usarlo desde el codigo fuente, sin ejecutable.
rem Requisitos: Python 3.12, 3.11 o 3.10 de python.org (con el lanzador "py").
rem Crea el entorno .venv en esta carpeta (unos 2 GB; tarda unos minutos). Despues: doble clic en abrir_nube2stl.bat
setlocal
cd /d "%~dp0"
set PY=
for %%V in (3.12 3.11 3.10) do (
  if not defined PY ( py -%%V -c "import sys" >nul 2>&1 && set PY=py -%%V )
)
if not defined PY (
  echo No se encuentra Python 3.10-3.12. Instalalo desde https://www.python.org/downloads/windows/
  echo ^(Open3D aun no existe para versiones mas nuevas^)
  pause & exit /b 1
)
echo Usando %PY%
if not exist .venv\Scripts\python.exe (
  %PY% -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install --upgrade pip || goto :error
.venv\Scripts\python.exe -m pip install -r requisitos.txt || goto :error
.venv\Scripts\python.exe consola.py --version || goto :error
echo.
echo Instalado. Para abrirlo: doble clic en abrir_nube2stl.bat
pause
exit /b 0
:error
echo.
echo *** Ha fallado la instalacion. Revisa los mensajes de arriba. ***
pause
exit /b 1
