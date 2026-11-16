# nube2stl

De la nube de puntos de un relieve (una piedra, una lápida, un friso, un fragmento) a una pieza **imprimible en 3D**.

nube2stl lee nubes PLY, XYZ, PTS, CSV, LAS/LAZ y E57 (y mallas OBJ/STL). Ajusta el plano de la cara y hace la superficie,
como mapa de alturas o con Poisson para objetos enteros. Después la cierra con paredes y base y la deja a la medida y la
resolución de tu impresora. Guarda STL, 3MF, OBJ o PLY, y también una ficha PDF de la pieza.

Tiene ventana (PyQt5) y línea de comandos. La ayuda completa está dentro del programa (tecla **F1**) y en
[`ayuda.html`](ayuda.html).

## Instalar

Hace falta **Python 3.10, 3.11 o 3.12**, porque Open3D aún no existe para versiones más nuevas. También hace falta
**git**, para poder actualizar con un clic.

- Windows: Python de [python.org](https://www.python.org/downloads/windows/) y git de [git-scm.com](https://git-scm.com/).
- Ubuntu: `sudo apt install git python3-venv`.

```sh
git clone https://github.com/sullymike/nube2stl.git
cd nube2stl
./instalar_linux.sh          # Windows: doble clic en instalar_windows.bat
```

La instalación crea `.venv` dentro de la carpeta con las librerías de `requisitos.txt`. Ocupa unos 2 GB y tarda unos
minutos.

Para abrirlo:

- **Linux:** `python3 nube2stl.py`, que usa `.venv` solo.
- **Windows:** doble clic en `abrir_nube2stl.bat`. También puedes arrastrar un `.ply` encima del fichero.

Si no tienes git, descarga la última versión desde **Releases**, en *Source code (zip)*, descomprímela e instálala igual.

## Actualizar

Al abrirse, el programa mira en GitHub si hay una versión nueva, como mucho una vez al día. Si la hay, lo avisa en la
barra de estado. **Ayuda › Buscar actualizaciones…** enseña las novedades y la instala:

- **Instalado con git:** hace `git pull`. Si has tocado a mano algún fichero, no actualiza y te dice cuáles son.
- **Instalado desde un .zip:** descarga la versión nueva y copia sus ficheros encima. Los ficheros de antes se guardan en
  `.anterior/<versión>/`.
- **Si ha cambiado `requisitos.txt`:** en los dos casos anteriores instala también las librerías nuevas.

Desde la consola:

```sh
python3 consola.py --actualizar               # busca e instala
python3 consola.py --actualizar --comprobar   # solo dice si hay una nueva
git pull                                      # lo mismo, a mano, si lo instalaste con git
```

A GitHub solo se le pregunta por la última versión; no se envía nada del usuario.

## Línea de comandos

```sh
python3 nube2stl.py --cli entrada.ply salida.stl --ajustes mis_ajustes.json
python3 consola.py --help
```

## Pruebas

```sh
.venv/bin/python pruebas.py            # todas (un par de minutos)
.venv/bin/python pruebas.py rapidas    # sin las reconstrucciones 3D largas
```

Las pruebas hacen sus propios datos sintéticos, así que no hace falta ninguna nube real. También sirven de ejemplo de
uso de cada parte del programa. En GitHub las pruebas rápidas se lanzan solas en cada envío (`.github/workflows/pruebas.yml`).

## Publicar una versión nueva

1. Sube `__version__` en `version.py` y añade sus novedades al principio de `HISTORY`.
2. Confirma y sube los cambios, y luego la etiqueta:

   ```sh
   git commit -am "Versión 0.21.0"
   git push
   git tag v0.21.0
   git push origin v0.21.0
   ```

3. GitHub publica la versión (*release*) con las novedades de `HISTORY` (`.github/workflows/publicar.yml`). La etiqueta
   tiene que coincidir con `version.py`. Esa versión es la que encuentra «Buscar actualizaciones» en los equipos donde
   está instalado.

## Ejecutable

Para equipos sin Python se puede construir un ejecutable con PyInstaller: `construir_windows.bat` o
`construir_linux.sh`, en el mismo sistema en el que se va a usar. Está explicado en la ayuda, en el apartado
«Ejecutable para Windows (o Linux)». El ejecutable no se actualiza solo; «Buscar actualizaciones» abre la página de
descarga.

---

Creado por Jorge Sánchez · 2026 · Licencia [Apache 2.0](LICENSE)
