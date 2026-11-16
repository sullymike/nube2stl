#!/bin/sh
# Instala nube2stl en este equipo (Linux) para usarlo desde el código fuente, sin ejecutable.
# Crea el entorno .venv en esta carpeta con las librerías de requisitos.txt (unos 2 GB; tarda unos minutos).
# Después:  python3 nube2stl.py   (usa .venv solo)
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if ! "$PY" -c 'import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 12)))'; then
    echo "Hace falta Python 3.10, 3.11 o 3.12 (Open3D aún no existe para versiones más nuevas)."
    echo "Indica otro con:  PYTHON=python3.12 ./instalar_linux.sh"
    exit 1
fi
if ! "$PY" -m venv .venv; then
    echo "No se pudo crear el entorno. En Ubuntu/Debian:  sudo apt install python3-venv"
    exit 1
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requisitos.txt
.venv/bin/python consola.py --version
echo
echo "Instalado. Para abrirlo:  python3 $(pwd)/nube2stl.py"
echo "Si al abrir dice «Could not load the Qt platform plugin xcb»:"
echo "    sudo apt install libxcb-xinerama0 libxcb-cursor0 libxkbcommon-x11-0"
