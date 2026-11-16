@echo off
rem Abre nube2stl (instalado con instalar_windows.bat). Se puede arrastrar un .ply encima de este fichero.
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Primero ejecuta instalar_windows.bat
  pause & exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0nube2stl.py" %*
