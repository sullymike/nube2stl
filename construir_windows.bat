@echo off
rem Construye el ejecutable de nube2stl para Windows.
rem Requisitos: Python 3.11 o 3.12 de python.org (con el lanzador "py"). Ejecutar con doble clic o desde cmd.
rem Resultado: dist\nube2stl\nube2stl.exe  (copia la carpeta dist\nube2stl entera; el .exe solo no funciona)
setlocal
cd /d "%~dp0"

set PY=
for %%V in (3.12 3.11 3.10) do (
  if not defined PY ( py -%%V -c "import sys" >nul 2>&1 && set PY=py -%%V )
)
if not defined PY (
  echo No se encuentra Python 3.10-3.12. Instalalo desde https://www.python.org/downloads/windows/
  pause & exit /b 1
)
echo Usando %PY%

if not exist venv-build (
  %PY% -m venv venv-build || goto :error
)
call venv-build\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requisitos.txt pyinstaller pillow || goto :error

rem icono .ico (por si se ha regenerado el logo)
python -c "from PIL import Image; Image.open('iconos/logo.png').save('iconos/logo.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])" || goto :error

pyinstaller --noconfirm --clean nube2stl.spec || goto :error

echo.
echo Comprobando el ejecutable...
dist\nube2stl\nube2stl-consola.exe --version || goto :error
echo.
echo Listo: dist\nube2stl\nube2stl.exe
echo Reparte la carpeta dist\nube2stl completa (por ejemplo, comprimida en .zip).
pause
exit /b 0

:error
echo.
echo *** Ha fallado la construccion. Revisa los mensajes de arriba. ***
pause
exit /b 1
