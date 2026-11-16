#!/bin/sh
# Construye el ejecutable de nube2stl para Linux (opcional: el programa funciona igual desde el código fuente).
# Resultado: dist/nube2stl/nube2stl  (copia la carpeta dist/nube2stl entera)
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
[ -d venv-build ] || "$PY" -m venv venv-build
. venv-build/bin/activate
pip install --upgrade pip
pip install -r requisitos.txt pyinstaller
pyinstaller --noconfirm --clean nube2stl.spec
dist/nube2stl/nube2stl-consola --version
echo "Listo: dist/nube2stl/nube2stl"
