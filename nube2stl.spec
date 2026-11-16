# -*- mode: python -*-
# PyInstaller: genera la carpeta dist/nube2stl con dos ejecutables que comparten las librerías:
#   nube2stl          la aplicación (ventana, sin consola)
#   nube2stl-consola  línea de comandos (--cli …) y procesos auxiliares de la generación (--proceso, --decimar)
# Son dos ejecutables porque PyMeshLab trae su propio Qt, incompatible con el de PyQt5, y PyInstaller carga PyQt5
# al arrancar cualquier ejecutable que lo incluya: el de consola se construye sin PyQt5.
# Uso:  pyinstaller --noconfirm nube2stl.spec      (Windows: construir_windows.bat · Linux: construir_linux.sh)
import sys
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

datas = [('ayuda.html', '.'), ('ayuda_img', 'ayuda_img'), ('iconos', 'iconos')]
binaries, hidden = [], []
for pkg in ('pymeshlab',):                      # PyMeshLab carga sus filtros como plugins: hay que llevarlos todos
    d, b, h = collect_all(pkg)
    datas, binaries, hidden = datas + d, binaries + b, hidden + h
# Open3D (modo 3D y medida del error): sin las extensiones de PyTorch/TensorFlow ni los ejemplos
binaries += [x for x in collect_dynamic_libs('open3d') if not any(k in x[0] for k in ('torch_ops', 'tf_ops'))]
hidden += collect_submodules('open3d', filter=lambda n: not n.startswith(('open3d.examples', 'open3d._ml3d')))
hidden += ['proceso', 'decimar', 'recon3d', 'objeto3d', 'bloque3d', 'entrada', 'acabado', 'ficha', 'version', 'psutil',
           'desviacion', 'registro', 'corte', 'superficie', 'unir', 'revolucion', 'comparar', 'interior', 'ejemplos', 'esquina',
           'laspy', 'lazrs', 'pye57', 'actualizar']
common_excludes = ['tkinter', 'IPython', 'jupyter', 'notebook', 'PyQt6', 'PySide2', 'PySide6', 'torch',
                   'tensorflow', 'pandas', 'sklearn', 'pkg_resources']

# ---- consola: sin PyQt5 ni nada que lo cargue
con_a = Analysis(['consola.py'], pathex=['.'], datas=datas, binaries=binaries, hiddenimports=hidden,
                 excludes=common_excludes + ['PyQt5', 'pyqtgraph', 'OpenGL', 'matplotlib.backends.backend_qtagg',
                                             'matplotlib.backends.backend_qt', 'gi'])
# ---- ventana
d, b, h = collect_all('pyqtgraph')
gui_a = Analysis(['nube2stl.py'], pathex=['.'], datas=datas + d, binaries=binaries + b,
                 hiddenimports=hidden + h + collect_submodules('OpenGL') +
                 ['consola', 'asistente', 'memoria', 'orientar', 'visor', 'impresoras', 'recetas', 'ventana_registro'],
                 excludes=common_excludes)

icon = 'iconos/logo.ico' if sys.platform == 'win32' else None
gui_pyz, con_pyz = PYZ(gui_a.pure), PYZ(con_a.pure)
gui = EXE(gui_pyz, gui_a.scripts, [], exclude_binaries=True, name='nube2stl', console=False, icon=icon, upx=False)
con = EXE(con_pyz, con_a.scripts, [], exclude_binaries=True, name='nube2stl-consola', console=True, icon=icon,
          upx=False)
coll = COLLECT(gui, gui_a.binaries, gui_a.datas, con, con_a.binaries, con_a.datas, name='nube2stl', upx=False)
