#!/usr/bin/env python3
"""nube2stl: carga una nube de puntos PLY de un relieve y la convierte en una malla cerrada imprimible.

Uso:  python3 nube2stl.py [fichero.ply]              (interfaz gráfica)
      python3 nube2stl.py --cli entrada.ply salida.stl [--param valor ...]   (línea de comandos, ver consola.py)
"""
import json
import os
import sys
import time
import traceback

import numpy as np

FROZEN = bool(getattr(sys, 'frozen', False))      # ejecutable hecho con PyInstaller (Windows o Linux)
if FROZEN:
    HERE = sys._MEIPASS                           # ayuda, imágenes e iconos van dentro del paquete
    for _s in ('stdout', 'stderr'):               # versión de ventana sin consola: que print no falle
        if getattr(sys, _s) is None:
            setattr(sys, _s, open(os.devnull, 'w'))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    # si hay un entorno .venv junto al programa, usarlo (Linux/macOS: bin/python; Windows: Scripts/python.exe)
    _VENV = os.path.join(HERE, '.venv')
    _VENV_PY = os.path.join(_VENV, 'Scripts', 'python.exe') if sys.platform == 'win32' \
        else os.path.join(_VENV, 'bin', 'python')
    if os.path.exists(_VENV_PY) and os.path.realpath(sys.prefix) != os.path.realpath(_VENV) \
            and not os.environ.get('NUBE2STL_NO_VENV'):
        if sys.platform == 'win32':               # en Windows execv no sustituye el proceso de verdad
            import subprocess
            sys.exit(subprocess.call([_VENV_PY, os.path.abspath(__file__)] + sys.argv[1:]))
        os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])
    sys.path.insert(0, HERE)

# línea de comandos y procesos auxiliares: sin PyQt5 (no puede convivir con PyMeshLab en el mismo proceso)
CONSOLE_ARGS = ('--cli', '--proceso', '--decimar', '--version', '-V', '--actualizar')
if len(sys.argv) > 1 and sys.argv[1] in CONSOLE_ARGS:
    if FROZEN:
        # el ejecutable de ventana ya ha cargado Qt al arrancar: se pasa la orden al ejecutable de consola
        import subprocess
        exe = os.path.join(os.path.dirname(sys.executable),
                           'nube2stl-consola' + ('.exe' if sys.platform == 'win32' else ''))
        sys.exit(subprocess.call([exe] + sys.argv[1:]))
    import consola
    sys.exit(consola.main(sys.argv[1:]))
import desviacion  # noqa: E402
import recetas  # noqa: E402
import relief  # noqa: E402
from relief import Params  # noqa: E402
from version import AUTHOR, HISTORY, YEAR, __version__  # noqa: E402

HELP_FILE = os.path.join(HERE, 'ayuda.html')
LOGO = os.path.join(HERE, 'iconos', 'logo.svg')
PREVIEW_PTS = 1_000_000          # puntos de la nube que se dibujan
MESH_PREVIEW_TRIS = 1_500_000    # por encima, la vista previa usa una malla aligerada
OOM_MSG = ('El sistema ha cerrado el proceso de reconstrucción, casi seguro por falta de memoria. '
           'Cierra otros programas, aumenta la resolución o baja la profundidad de Poisson.')
OOM_KEY = 'por falta de memoria'  # está en los dos avisos (el del sistema y el de la vigilancia de run_external)
MEM_GUARD_GB = 1.5                # memoria libre mínima: por debajo, se para la reconstrucción antes de que el
                                  # sistema llene el swap (el equipo se queda atascado) y la mate él

# ====================================================================== interfaz gráfica
from PyQt5.QtCore import QElapsedTimer, QRectF, Qt, QThread, QTimer, QUrl, pyqtSignal  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap  # noqa: E402
from PyQt5.QtSvg import QSvgRenderer  # noqa: E402
from PyQt5.QtWidgets import (QAction, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,  # noqa: E402
                             QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                             QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy,
                             QSpinBox, QSplashScreen, QStackedWidget, QStyle, QTabWidget, QTextBrowser,
                             QLineEdit, QScrollArea, QToolButton, QButtonGroup, QDialogButtonBox,
                             QVBoxLayout, QWidget)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT  # noqa: E402
from matplotlib.colors import LightSource, LinearSegmentedColormap  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

try:
    import pyqtgraph.opengl as gl
    HAVE_GL = True
except Exception:
    HAVE_GL = False


# --- especificación de los parámetros en la interfaz: (nombre, etiqueta, tipo, opciones, tooltip)
# ---------------------------------------------------------------- tema visual
C = dict(bg='#eef1f4', card='#ffffff', line='#d9dee3', ink='#24323d', muted='#6b7b87', navy='#1f3b4d',
         teal='#2a7f9e', accent='#e07a3f', accent_dk='#c9642b', ok='#2e8b57', bad='#c0392b', view='#e6eaee')
STONE = LinearSegmentedColormap.from_list('piedra', ['#1d2126', '#5f584f', '#b3a690', '#efe6d6', '#fffaf2'])

ICONS = os.path.join(HERE, 'iconos').replace('\\', '/')

STYLE = f"""
QMainWindow, #central {{ background: {C['bg']}; }}
QWidget {{ font-size: 10pt; color: {C['ink']}; }}
QMenuBar {{ background: {C['navy']}; color: #e8f1f4; padding: 2px; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: #2c6e7f; border-radius: 4px; }}
QMenu {{ background: white; border: 1px solid {C['line']}; }}
QMenu::item:selected {{ background: {C['teal']}; color: white; }}

#header {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {C['navy']}, stop:1 #2c6e7f); }}
#header QLabel {{ color: white; background: transparent; }}
#appTitle {{ font-size: 16pt; font-weight: 800; }}
#appSub {{ color: #b9d3dc; font-size: 9pt; }}
#fileTag {{ color: #ffd9c2; font-weight: 600; }}
#header QPushButton {{ background: rgba(255,255,255,0.12); color: white; border: 1px solid rgba(255,255,255,0.28);
                       border-radius: 7px; padding: 7px 14px; }}
#header QPushButton:hover {{ background: rgba(255,255,255,0.24); }}
#header QPushButton:disabled {{ color: rgba(255,255,255,0.35); background: rgba(255,255,255,0.05);
                                border-color: rgba(255,255,255,0.10); }}
#header QPushButton#primary, QPushButton#primary {{ background: {C['accent']}; color: white; border: none;
                       font-weight: 700; border-radius: 7px; padding: 8px 18px; }}
#header QPushButton#primary:hover, QPushButton#primary:hover {{ background: {C['accent_dk']}; }}
#header QPushButton#primary:disabled {{ background: rgba(224,122,63,0.35); color: rgba(255,255,255,0.55); }}
QPushButton#primary:disabled {{ background: #e9c3aa; color: #fff6ef; }}

QPushButton {{ background: white; border: 1px solid #c9d1d8; border-radius: 6px; padding: 5px 10px; }}
QPushButton:hover {{ border-color: {C['teal']}; background: #f1f8fa; }}
QPushButton:disabled {{ color: #9aa5ae; background: #f1f3f5; border-color: #dde2e6; }}
QPushButton#kind {{ padding: 3px 4px; }}
QPushButton#kind:checked {{ background: {C['teal']}; color: white; border-color: {C['teal']}; font-weight: 700; }}
#steps {{ background: white; border: 1px solid {C['line']}; border-radius: 8px; }}
QToolButton#step {{ border: none; border-radius: 6px; padding: 4px 9px; color: {C['muted']}; background: transparent; }}
QToolButton#step:hover {{ background: #eef5f8; color: {C['navy']}; }}
QToolButton#step[state="done"] {{ color: {C['teal']}; }}
QToolButton#step[state="now"] {{ background: {C['accent']}; color: white; font-weight: 700; }}
QToolButton#step:disabled {{ color: #b8c1c8; }}
QLabel#stepArrow {{ color: #b8c1c8; padding: 0 1px; }}
QPushButton#link {{ border: none; background: transparent; color: {C['teal']}; text-decoration: underline; padding: 2px; }}

QToolBar#cabecera {{ background: {C['navy']}; border: none; padding: 0; spacing: 0; }}
QDockWidget {{ color: {C['teal']}; font-weight: 700; font-size: 8.5pt; }}
QDockWidget::title {{ background: {C['bg']}; padding: 8px 12px 4px 12px; text-align: left; }}
QMainWindow::separator {{ background: {C['bg']}; width: 6px; height: 6px; }}
QMainWindow::separator:hover {{ background: {C['line']}; }}
QFrame#card {{ background: {C['card']}; border: 1px solid {C['line']}; border-radius: 10px; }}
QLabel#cardTitle {{ color: {C['teal']}; font-weight: 800; font-size: 8.5pt; letter-spacing: 1px; }}
QFrame#tile {{ background: #f4f6f8; border: none; border-radius: 8px; }}
QLabel#tileVal {{ font-size: 11.5pt; font-weight: 700; color: {C['navy']}; background: transparent; }}
QLabel#tileCap {{ font-size: 8pt; color: {C['muted']}; background: transparent; }}
QLabel#muted {{ color: {C['muted']}; }}
QLabel#plan {{ background: #f2f7f9; border: 1px solid #d3e4ea; border-left: 3px solid {C['teal']};
              border-radius: 6px; padding: 7px 9px; color: #2d4250; }}

QTabWidget::pane {{ border: 1px solid {C['line']}; border-radius: 8px; background: white; top: -1px; }}
QTabBar::tab {{ background: #e1e6eb; color: #4a5a66; padding: 7px 8px; margin-right: 2px;
               border-top-left-radius: 7px; border-top-right-radius: 7px; }}
QTabBar::tab:selected {{ background: white; color: {C['teal']};
                        border: 1px solid {C['line']}; border-bottom: 3px solid {C['accent']}; }}
QTabBar::tab:hover:!selected {{ background: #d3dbe1; }}

QDoubleSpinBox, QSpinBox, QComboBox {{ background: #f8fafb; border: 1px solid #cdd5dc; border-radius: 5px;
                                       padding: 3px 6px; min-height: 22px; }}
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {C['teal']}; background: white; }}
QComboBox QAbstractItemView {{ selection-background-color: {C['teal']}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 18px;
                                                             border: none; background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: #e3f0f4; border-radius: 3px; }}
QAbstractSpinBox::up-arrow {{ image: url({ICONS}/up.svg); width: 10px; height: 6px; }}
QAbstractSpinBox::down-arrow {{ image: url({ICONS}/down.svg); width: 10px; height: 6px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: url({ICONS}/up_off.svg); }}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: url({ICONS}/down_off.svg); }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({ICONS}/down.svg); width: 10px; height: 6px; }}
QCheckBox {{ spacing: 7px; }}
QDoubleSpinBox:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: #a3adb5; background: #eef1f3;
                                                               border-color: #dde2e6; }}
QCheckBox:disabled {{ color: #a3adb5; }}
QToolButton#help {{ border: 1px solid #b8c6cf; border-radius: 10px; min-width: 20px; max-width: 20px;
                    min-height: 20px; max-height: 20px; color: {C['teal']}; font-weight: 800; background: white; }}
QToolButton#help:hover {{ background: {C['teal']}; color: white; border-color: {C['teal']}; }}

QPlainTextEdit#log {{ background: #1c252c; color: #cfd8de; border: none; border-radius: 8px;
                      font-family: 'DejaVu Sans Mono', monospace; font-size: 8.5pt; padding: 6px; }}
QStatusBar {{ background: #dde3e8; color: #3a4a55; }}
QStatusBar QLabel {{ color: #3a4a55; padding: 0 6px; }}
QProgressBar {{ border: none; background: #c8d2da; border-radius: 4px; max-height: 8px; min-width: 170px; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 4px; }}
QSplitter::handle {{ background: {C['bg']}; }}
QToolTip {{ background: {C['navy']}; color: white; border: none; padding: 5px; }}
"""

# pestañas de parámetros: (título corto, descripción, grupos de SPEC). «Tipo de pieza» no va en ninguna: es el
# selector de arriba (KIND), que pone el método y «Abierto por abajo».
PARAM_TABS = [('Tamaño', 'La nube y el tamaño de la pieza', ['La nube', 'Tamaño']),
              ('Pieza', 'Forma, base, marco y molde', ['Pieza final']),
              ('Orientar', 'Desde dónde se mira, qué va arriba y el giro', ['Orientación']),
              ('Acabados', 'Pieza hueca, agujeros de drenaje y rótulo', ['Acabados']),
              ('Avanzado', 'Impresora, malla y suavizados: lo normal es no tocarlos', ['Impresora', 'Malla',
                                                                                        'Limpieza y suavizado'])]
TAB_ORIENT = 2
# texto de una pestaña cuando el tipo de pieza deja todos sus campos ocultos
TAB_EMPTY = {'Pieza': 'En un bloque, la forma y el grosor los dan el escaneo y la «Profundidad máx.» (pestaña '
                      'Orientar).'}
ADVANCED_NOTE = ('Lo normal es no tocar nada de aquí: la resolución y la simplificación se calculan solas a partir '
                 'de la impresora y del escaneo, y el asistente ajusta los suavizados según el estado del escaneo.')

# (nombre, etiqueta, tipo, opciones, ayuda emergente); el ancla de ayuda es p-<nombre> salvo HELP_ANCHOR
HELP_ANCHOR = {'bed_y': 'p-bed_x'}
SPEC = [
    ('Tipo de pieza', [
        ('mode', 'Método', 'c', dict(items=['alturas', '3d', 'bloque', 'objeto']),
         'alturas: mapa de alturas (rápido, siempre cerrado). 3d: Poisson, conserva socavados (lento). '
         'bloque: la cara y los laterales escaneados del bloque. '
         'objeto: objeto completo escaneado por todos los lados (estatua, vasija, capitel).'),
        ('ground', 'Abierto por abajo', 'b', {},
         'Solo modo objeto. Para escaneos que solo tienen la superficie de arriba: un terreno, una ruina, un\n'
         'edificio o un acueducto escaneado desde el aire. En vez de cortar la base, se cierra con paredes\n'
         'verticales en todo el contorno hasta una base plana («Grosor base» bajo el punto más bajo).\n'
         'Sin esto, Poisson inventa una «burbuja» para cerrar lo que no está escaneado.'),
        ('surface', 'Superficie', 'c', dict(items=['plano', 'cilindro', 'cono', 'revolucion']),
         'Forma de la superficie sobre la que está el relieve.\n'
         'plano: lo normal. cilindro: miliarios, columnas, aras; se ajusta un cilindro y la cara se desenrolla\n'
         'como una placa plana (se mira siempre desde fuera, sin «Mirar desde»). cono: piezas troncocónicas, se\n'
         'desarrolla sin deformar. revolucion: vasijas abombadas, fustes con éntasis; se desenrolla con su perfil.\n'
         'En el modo objeto, revolucion = vasija entera a partir de un fragmento (tipo «Fragmento»).'),
    ]),
    ('La nube', [
        ('units', 'Unidades de la nube', 'c', dict(items=['auto', 'mm', 'cm', 'm', 'pulgadas']),
         'En qué unidades está la nube. Automático: metros si mide menos de 20 de lado, si no milímetros.\n'
         'Todo se pasa a mm al abrirla (al cambiarlo se vuelve a leer la nube).'),
        ('clean', 'Quitar grupos sueltos', 'b', {},
         'Quita los grupos de puntos separados de la pieza (ruido, trozos de suelo o de soportes) antes de nada.\n'
         'Al abrir se lee la nube entera y esos grupos se ven en rojo en la vista de la nube, se quiten o no;\n'
         'desmárcalo si alguno es parte de la pieza.'),
    ]),
    ('Tamaño', [
        ('target_len', 'Lado mayor final', 'f', dict(lo=0, hi=100000, step=10, dec=1, suf='mm', special='tamaño real'),
         'Tamaño al que se imprimirá: el lado mayor de la pieza medirá esto. No cuenta si fijas la escala.'),
        ('ratio', 'Escala 1:', 'f', dict(lo=0, hi=100000, step=1, dec=2, special='según lado mayor'),
         'Escala exacta de la réplica (1:5, 1:10…; 1 = tamaño real). Si la fijas, manda sobre el lado mayor.'),
    ]),
    ('Pieza final', [
        ('interior', 'Interior', 'c', dict(items=['no', 'corte', 'negativo']),
         'Escaneos desde dentro (sala, cueva, cisterna), con el tipo «Interior»:\n'
         'corte: maqueta sin el techo, con la sala como un hueco dentro de un bloque (casa de muñecas).\n'
         'negativo: el volumen del hueco como un sólido.'),
        ('ceiling', 'Altura del corte', 'f', dict(lo=0, hi=1_000_000, step=100, dec=0, suf='mm', special='¾ del alto'),
         'Interior en corte: a qué altura sobre el suelo se quita el techo (mm reales de la nube).'),
        ('corner', 'Esquina (dos caras)', 'b', {},
         'Tipo Bloque: dos caras con relieve que se juntan en una arista (esquina de un friso, sillar de esquina,\n'
         'ara con dos caras labradas). Cada cara tiene su propio mapa de alturas, con el mismo detalle, y la pieza\n'
         'es el bloque que queda detrás de las dos. Sin esto, la segunda cara solo pasa por Poisson.\n'
         'La cara que queda arriba es la mayor; «Darle la vuelta» pone la otra.'),
        ('shape', 'Forma', 'c', dict(items=['contorno', 'rectangulo']), 'Silueta real o placa rectangular.'),
        ('z_scale', 'Exageración relieve', 'f', dict(lo=0.1, hi=20, step=0.25, dec=2, suf='×'),
         'Multiplica las alturas del relieve (relieves gastados o piezas pequeñas).'),
        ('boost', 'Realzar lo pequeño', 'f', dict(lo=1, hi=10, step=0.5, dec=1, suf='×', special='no'),
         'Realza solo los detalles de poco relieve (letras gastadas, incisiones finas), hacia fuera y hacia dentro,\n'
         'sin tocar las formas grandes. Lo que sobresale o se hunde respecto a su entorno «Desde» mm se multiplica\n'
         'por esto; según el detalle es más alto el aumento es menor, y en «Hasta» mm ya no se añade nada.\n'
         'Por debajo de la mitad de «Desde» no se toca (es ruido). Todo en mm de la piedra (lo que mides en la nube),\n'
         'así no cambia al cambiar el tamaño de la pieza; debajo se ve cuánto es en la pieza. Vale para todos los tipos\n'
         'de pieza (en los hechos con Poisson se aplica a la malla y no se ve en la vista previa).\n'
         'El mapa de desviación muestra lo que se ha añadido.'),
        ('boost_lo', 'Realzar desde', 'f', dict(lo=0, hi=1000, step=0.1, dec=2, suf='mm', special='auto'),
         'Profundidad del detalle EN LA PIEDRA (mm de la nube: lo que mides en ella) que recibe el realce entero.\n'
         'La mitad es el umbral: por debajo, ruido del escaneo que no se toca. No cambia al cambiar el tamaño de la\n'
         'pieza. Auto: lo que en la pieza son 0,1 mm. Debajo se ve cuánto es en la pieza.'),
        ('boost_hi', 'Realzar hasta', 'f', dict(lo=0, hi=1000, step=0.5, dec=2, suf='mm', special='auto'),
         'Profundidad del detalle en la piedra (mm) a la que el realce ya es nulo: lo que pasa de esto queda como está.\n'
         'Auto: lo que en la pieza son 0,5 mm (y al menos el triple de «desde»).'),
        ('boost_size', 'Anchura del detalle', 'f', dict(lo=0, hi=10000, step=5, dec=1, suf='mm', special='auto'),
         'Radio del entorno con el que se mide cada detalle, en mm de la piedra: algo mayor que el trazo más ancho\n'
         'que se quiere realzar. Si es pequeño, los trazos anchos solo se realzan por los bordes; si es muy grande,\n'
         'las ondulaciones suaves cuentan como detalle. Auto: lo que en la pieza son 3 mm.'),
        ('detrend', 'Quitar forma de la roca', 'f', dict(lo=0, hi=100000, step=10, dec=0, suf='mm', special='no'),
         'Grabados sobre roca irregular: quita la forma general de la superficie (las jorobas y la pendiente de\n'
         'la roca) y deja solo los detalles más estrechos que esto (mm reales). Debe ser bastante mayor que el\n'
         'trazo más ancho del grabado: si es pequeño, también aplana el grabado. Solo modo alturas.'),
        ('negative', 'Molde (negativo)', 'b', {}, 'Invierte alturas para hacer un molde o sello.'),
        ('base', 'Grosor base', 'f', dict(lo=0.2, hi=1000, step=0.5, dec=1, suf='mm'),
         'Base maciza bajo el punto más bajo (mm de la pieza). En modo objeto, solo con «Abierto por abajo».'),
        ('frame', 'Marco', 'f', dict(lo=0, hi=1000, step=1, dec=1, suf='mm', special='sin marco'),
         'Ancho de un marco perimetral (mm de la pieza).'),
        ('cut', 'Corte de la base', 'f', dict(lo=0, hi=1000, step=0.5, dec=1, suf='mm', special='no cortar'),
         'Solo modo objeto. Corta el objeto por abajo a esta altura (mm de la pieza) para que apoye en una base '
         'plana. El fondo de un objeto casi nunca está escaneado.'),
    ]),
    ('Orientación', [
        ('up', 'Arriba (objeto)', 'c', dict(items=['z', '-z', 'y', '-y', 'x', '-x']),
         'Solo modo objeto. Eje de la nube que quedará hacia arriba al imprimir (mira la vista «Orientación»).'),
        ('view', 'Mirar desde', 'c', dict(items=['auto', 'arriba', 'frente', 'detrás', 'izquierda', 'derecha', 'abajo']),
         'Desde dónde se mira la cara del relieve (modos alturas, 3D y bloque).\n'
         'Auto: perpendicular al plano principal de la nube (lo normal en una lápida o un relieve).\n'
         'Las demás, desde ese lado según los ejes de la nube (frente = desde −Y, arriba = desde +Z):\n'
         'p. ej. «frente» para sacar la fachada de un edificio o un acueducto escaneado desde el aire.\n'
         'Mira el resultado en la pestaña «Orientación».'),
        ('plane', 'Plano de la cara', 'c', dict(items=['nube', 'cara', 'puntos']),
         'Con «Mirar desde» auto, cómo se busca el plano de la cara (la pieza se mira perpendicular a él):\n'
         'nube: el plano medio de toda la nube (lo normal).\n'
         'cara: solo la cara frontal, sin dejarse llevar por los laterales, una rotura grande o una peana.\n'
         'puntos: el plano por tres puntos que marcas con clic en la vista de la nube (enlace de debajo).'),
        ('behind', 'Profundidad máx.', 'f', dict(lo=1, hi=100000, step=10, dec=0, suf='mm'),
         'Descarta puntos más atrás que esto respecto al plano medio (caras laterales del bloque). '
         'En modo bloque, hasta dónde llega el bloque por detrás de la cara.'),
        ('flip', 'Invertir cara frontal', 'b', {}, 'Si el relieve sale hundido o con un reborde alto alrededor.'),
        ('mirror', 'Espejar', 'b', {}, 'Invierte izquierda/derecha (letras al revés, sellos, moldes).'),
        ('auto_straighten', 'Enderezar automáticamente', 'b', {}, 'Gira la pieza para alinear su contorno con los ejes.'),
        ('angle', 'Giro', 'f', dict(lo=-360, hi=360, step=0.5, dec=2, suf='°'),
         'Giro en el plano, en sentido antihorario (180 = darle la vuelta). Se suma al automático si está activo.'),
    ]),
    ('Acabados', [
        ('wall', 'Hueca: pared', 'f', dict(lo=0, hi=100, step=0.5, dec=1, suf='mm', special='maciza'),
         'Vacía la pieza por dentro dejando una pared de este grosor (mm de la pieza). Útil en resina o para\n'
         'piezas grandes. Las piezas más finas que dos paredes se dejan macizas.'),
        ('drain', 'Agujeros de drenaje', 'f', dict(lo=0, hi=30, step=0.5, dec=1, suf='mm', special='sin agujeros'),
         'Diámetro de dos agujeros en la base que comunican el hueco con el exterior (resina: para vaciar el\n'
         'líquido y que no haga ventosa). Solo si la pieza es hueca.'),
        ('label', 'Rótulo en la base', 's', {},
         'Texto grabado en la cara de apoyo (sigla de inventario, «Réplica 1:5», fecha…). Se lee mirando la\n'
         'base por debajo. Usa | para partir líneas. Vacío = sin rótulo.'),
        ('label_size', 'Altura del texto', 'f', dict(lo=0, hi=50, step=0.5, dec=1, suf='mm', special='automática'),
         'Altura de las letras del rótulo (mm). Automática: la mayor que quepa en la base, hasta 8 mm.'),
    ]),
    ('Impresora', [
        ('nozzle', 'Boquilla', 'f', dict(lo=0.02, hi=2, step=0.05, dec=2, suf='mm'),
         'Diámetro de la boquilla. Fija el detalle más fino que se puede imprimir (≈ la mitad).'),
        ('layer', 'Altura de capa', 'f', dict(lo=0.02, hi=1, step=0.02, dec=2, suf='mm'),
         'Altura de capa prevista en el laminador. Sirve para la tolerancia y para contar las capas del relieve.'),
        ('bed_x', 'Cama: ancho', 'f', dict(lo=0, hi=2000, step=10, dec=0, suf='mm', special='no comprobar'),
         'Ancho de la cama de impresión (para avisar si la pieza no cabe).'),
        ('bed_y', 'Cama: fondo', 'f', dict(lo=0, hi=2000, step=10, dec=0, suf='mm', special='no comprobar'),
         'Fondo de la cama de impresión.'),
        ('quality', 'Calidad', 'c', dict(items=['normal', 'alta', 'maxima']),
         'Tolerancia de la simplificación automática: normal = media boquilla/capa; alta = la mitad; máxima = un cuarto.'),
    ]),
    ('Malla', [
        ('res', 'Resolución', 'f', dict(lo=0, hi=100, step=0.5, dec=2, suf='mm', special='automática'),
         'Tamaño de celda en la nube. Automática: la que pide la boquilla o la que permite el escaneo (la mayor).'),
        ('simplify', 'Simplificar', 'f', dict(lo=0, hi=100, step=5, dec=1, suf='%', special='automático'),
         'Automático: todo lo que la impresora no notaría (según Calidad). O un % fijo de triángulos (100 = no).'),
        ('poisson_depth', 'Profundidad Poisson', 'i', dict(lo=0, hi=13, special='automática'),
         'Solo modo 3D. Detalle del octree: +1 = doble de detalle y ~4× memoria.'),
        ('front_pct', 'Percentil frente', 'f', dict(lo=0, hi=100, step=5, dec=0, suf='%'),
         'Qué punto de cada celda se toma: 100 = el más saliente, 90 = robusto a puntos sueltos.'),
        ('min_pts', 'Puntos mín./celda', 'i', dict(lo=1, hi=1000), 'Puntos necesarios para que una celda sea parte de la pieza.'),
        ('fill', 'Relleno de huecos', 'c', dict(items=['vecino', 'interpolado', 'suave']),
         'Cómo rellenar las celdas sin datos dentro de la pieza:\n'
         'vecino: la altura del punto conocido más cercano (rápido; deja escalones en huecos grandes).\n'
         'interpolado: planos entre los bordes del hueco.\n'
         'suave: la superficie más suave que empalma con el borde en altura y pendiente (biarmónica): continúa\n'
         'las curvas de una moldura o de una roca. Algo más lento en huecos grandes.'),
        ('workers', 'Procesadores', 'i', dict(lo=0, hi=relief.NCPU, special=f'automático ({relief.NCPU})'),
         'Procesadores a usar. Automático: todos para ordenar y simplificar; los núcleos físicos para Poisson.'),
    ]),
    ('Limpieza y suavizado', [
        ('despike', 'Quitar picos', 'f', dict(lo=0, hi=1000, step=1, dec=1, suf='mm', special='no'),
         'Celdas que se separan de su entorno más que esto se tratan como huecos.'),
        ('smooth', 'Suavizado mediana', 'i', dict(lo=0, hi=15), 'Filtro de mediana NxN celdas (0/1 = no).'),
        ('gauss', 'Suavizado gaussiano', 'f', dict(lo=0, hi=100, step=0.5, dec=1, suf='mm', special='no'),
         'Sigma del suavizado gaussiano (redondea aristas, salvo con «Respetar las aristas»).'),
        ('edges', 'Respetar las aristas', 'b', {},
         'El suavizado (el de arriba) solo alisa las zonas lisas: quita el grano del escáner sin redondear\n'
         'los cantos de las letras ni las molduras (filtro guiado). Mide el ruido en la propia nube.'),
        ('denoise', 'Alisado (3D)', 'f', dict(lo=0, hi=10, step=0.5, dec=1, suf='× esc.', special='no'),
         'Modos bloque y objeto: quita el ruido de medida del escáner antes de Poisson (sin él la superficie\n'
         'sale granulada). Radio en separaciones entre puntos del escaneo: 2–3 normal, 4–6 escaneos ruidosos\n'
         '(redondea más los detalles finos), no = calcar los puntos tal cual.'),
        ('floor_pct', 'Recorte del fondo', 'f', dict(lo=0, hi=50, step=1, dec=1, suf='%'),
         'Aplana las alturas por debajo de este percentil: quita rampas del borde y adelgaza la base.'),
        ('erode', 'Recortar borde', 'f', dict(lo=0, hi=1000, step=5, dec=1, suf='mm', special='no'),
         'Encoge el contorno hacia dentro esta distancia.'),
    ]),
]


def make_widget(kind, opt, val, label):
    """Control de un parámetro según su tipo en SPEC (f/i/b/c/s)."""
    if kind == 'f':
        w = QDoubleSpinBox()
        w.setRange(opt['lo'], opt['hi'])
        w.setSingleStep(opt['step'])
        w.setDecimals(opt['dec'])
        if opt.get('suf'):
            w.setSuffix(' ' + opt['suf'])
        if opt.get('special'):
            w.setSpecialValueText(opt['special'])
        w.setValue(val)
    elif kind == 'i':
        w = QSpinBox()
        w.setRange(opt['lo'], opt['hi'])
        if opt.get('special'):
            w.setSpecialValueText(opt['special'])
        w.setValue(val)
    elif kind == 'b':
        w = QCheckBox(label)
        w.setChecked(val)
    elif kind == 's':
        w = QLineEdit(val)
        w.setClearButtonEnabled(True)
    else:
        w = QComboBox()
        w.addItems(opt['items'])
        w.setCurrentText(val)
    return w


def plan_html(st, prm):
    """Resumen del cálculo de impresión (lo usan la pestaña Tamaño y el asistente)."""
    pl = relief.print_plan(st, prm)
    if pl.get('object'):
        return plan_html_object(pl, prm)
    sx, sy = pl['size']
    if pl['fits'] is None:
        bed = ''
    elif pl['fits']:
        bed = f' <span style="color:{C["ok"]}">✔ cabe en la cama</span>'
    else:
        bed = f' <span style="color:{C["bad"]}"><b>✘ no cabe</b> en {prm.bed_x:.0f} × {prm.bed_y:.0f}</span>'
    esc = f'1:{1 / pl["scale"]:.1f}' if pl['scale'] < 1 else f'{pl["scale"]:.2f}:1'
    why = {'impresora': 'limita la boquilla', 'escaneo': 'limita el escaneo', 'manual': 'manual'}[pl['why']]
    cells = pl['cells']
    lay_col = C['bad'] if pl['layers'] < 10 else C['ink']
    warn = ''
    if pl['layers'] < 10:
        warn += (f'<br><span style="color:{C["bad"]}">⚠ El relieve ocuparía pocas capas: se verá plano. Usa capa '
                 f'más fina, pieza mayor o exageración del relieve.</span>')
    if cells > 8e6:
        warn += (f'<br><span style="color:{C["bad"]}">⚠ {cells / 1e6:.0f} M celdas: fichero y memoria grandes. '
                 f'Considera una pieza más pequeña o una resolución mayor.</span>')
    return (f'<b>Pieza:</b> {sx:.0f} × {sy:.0f} × ≈{pl["height"]:.0f} mm · {esc}{bed}<br>'
            f'<b>Relieve:</b> <span style="color:{lay_col}">≈ {pl["depth"]:.1f} mm · {pl["layers"]:.0f} capas'
            f'</span><br>'
            f'<b>Detalle en la nube:</b> impresora {pl["res_print"]:.2f} (½ boquilla) · escaneo {pl["res_scan"]:.2f}<br>'
            f'<b>Resolución:</b> {pl["res"]:.2f} ({why}) → celda de {pl["cell_print"]:.3f} mm · '
            f'{cells / 1e6:.1f} M celdas<br>'
            f'<b>Tolerancia al simplificar:</b> {pl["tol"]:.3f} mm' + warn)


def plan_html_object(pl, prm):
    """plan_html del modo objeto."""
    sx, sy = pl['size']
    if pl['fits'] is None:
        bed = ''
    elif pl['fits']:
        bed = f' <span style="color:{C["ok"]}">✔ cabe en la cama</span>'
    else:
        bed = f' <span style="color:{C["bad"]}"><b>✘ no cabe</b> en {prm.bed_x:.0f} × {prm.bed_y:.0f}</span>'
    esc = f'1:{1 / pl["scale"]:.1f}' if pl['scale'] < 1 else f'{pl["scale"]:.2f}:1'
    why = {'impresora': 'limita la boquilla', 'escaneo': 'limita el escaneo', 'manual': 'manual'}[pl['why']]
    cells = pl['cells']
    warn = ''
    if cells > 8e6:
        warn += (f'<br><span style="color:{C["bad"]}">⚠ {cells / 1e6:.0f} M celdas de superficie: fichero y memoria '
                 f'grandes. Considera una pieza más pequeña o una resolución mayor.</span>')
    return (f'<b>Objeto:</b> {sx:.0f} × {sy:.0f} × {pl["height"]:.0f} mm (alto) · {esc}{bed}<br>'
            f'<b>Altura:</b> {pl["height"]:.0f} mm · {pl["layers"]:.0f} capas<br>'
            f'<b>Detalle en la nube:</b> impresora {pl["res_print"]:.2f} (½ boquilla) · escaneo {pl["res_scan"]:.2f}<br>'
            f'<b>Resolución:</b> {pl["res"]:.2f} ({why}) → celda de {pl["cell_print"]:.3f} mm · '
            f'{cells / 1e6:.1f} M celdas de superficie<br>'
            f'<b>Tolerancia al simplificar:</b> {pl["tol"]:.3f} mm' + warn)


SPEC_BY_NAME = {row[0]: row for _, rows in SPEC for row in rows}   # nombre -> (nombre, etiqueta, tipo, opc, ayuda)

# parámetros que solo cuentan en unos métodos (los demás se desactivan al elegir el método)
ONLY_RELIEF = ('front_pct', 'min_pts', 'fill', 'despike', 'smooth', 'gauss', 'floor_pct', 'erode', 'shape',
               'z_scale', 'negative', 'base', 'frame', 'behind', 'flip', 'auto_straighten', 'view', 'plane',
               'edges')
ONLY_OBJECT = ('up', 'cut', 'ground', 'interior', 'ceiling')
ONLY_POISSON = ('poisson_depth',)
ONLY_POINTS = ('denoise',)             # modos que trabajan con los puntos en 3D (no con el mapa de alturas)
ONLY_HEIGHTMAP = ('detrend',)
DISABLED = {'alturas': ONLY_OBJECT + ONLY_POISSON + ONLY_POINTS,
            '3d': ONLY_OBJECT + ONLY_POINTS + ONLY_HEIGHTMAP,
            'bloque': ONLY_OBJECT + ONLY_HEIGHTMAP + ('z_scale', 'negative', 'base', 'frame', 'shape'),
            'objeto': tuple(n for n in ONLY_RELIEF if n != 'base') + ONLY_HEIGHTMAP}
METHOD_NAME = {'alturas': 'mapa de alturas', '3d': '3D (Poisson)', 'bloque': 'cara y laterales del bloque (Poisson)',
               'objeto': 'objeto completo (Poisson)'}


EXTRA_LABELS = {'keep_groups': 'Grupos conservados', 'crop': 'Recortes', 'merge': 'Nubes unidas',
                'reverse': 'Reverso',
                'classes': 'Clases del LiDAR',
                'plane_pts': 'Puntos del plano'}   # ajustes sin campo propio


def param_label(name):
    return SPEC_BY_NAME[name][1] if name in SPEC_BY_NAME else EXTRA_LABELS.get(name, name)


def logo_pixmap(size):
    """El logo (iconos/logo.svg) a cualquier tamaño, nítido también en pantallas de alta densidad."""
    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(LOGO).render(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def logo_label(size):
    lb = QLabel()
    lb.setPixmap(logo_pixmap(size))
    lb.setFixedSize(size, size)
    lb.setStyleSheet('background: transparent;')
    return lb


def splash_pixmap():
    """Portada de arranque: logo, nombre, versión y autor."""
    W, H = 600, 330
    pm = QPixmap(W, H)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    g = QLinearGradient(0, 0, W, H)
    g.setColorAt(0, QColor(C['navy']))
    g.setColorAt(1, QColor('#2c6e7f'))
    p.fillRect(pm.rect(), g)
    p.fillRect(0, H - 6, W, 6, QColor(C['accent']))
    QSvgRenderer(LOGO).render(p, QRectF(34, 62, 180, 180))

    def text(x, y, txt, pt, color, bold=False, w=QFont.Normal):
        f = QFont()
        f.setPointSizeF(pt)
        f.setWeight(QFont.Black if bold else w)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(x, y, txt)
    text(244, 128, 'nube2stl', 34, 'white', bold=True)
    text(247, 160, 'De la nube de puntos a la pieza impresa', 11, '#b9d3dc')
    text(247, 204, f'Versión {__version__}', 12.5, '#ffd9c2', w=QFont.DemiBold)
    text(247, 230, f'Creado por {AUTHOR} · {YEAR}', 11, 'white')
    p.end()
    return pm


class Worker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.done.emit(self.fn(*self.args, log=self.log.emit))
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))


def prep_state(prm):
    """Lo que cambia la nube preparada (desenrollada, vasija girada): si cambia, hay que volver a prepararla."""
    import entrada
    return (entrada.unrolls(prm), entrada.is_vessel(prm), prm.surface if entrada.unrolls(prm) else '')


def load_task(path, prm, log):
    """Lee la nube entera y marca los grupos sueltos, sin quitar nada todavía (la ventana los enseña en rojo)."""
    import entrada
    t = time.time()
    log(f'Leyendo {os.path.basename(path)}…')
    xyz, rgb, _, info = entrada.load_points(path, prm, log, mark_only=True)
    log(f'{len(xyz):,} puntos leídos en {time.time() - t:.1f} s' + (' (con color)' if rgb is not None else ''))
    return prepare_task(path, xyz, rgb, info, prm, log, guess=True)


def save_merged_task(path, prm, out, log):
    """Escribe la nube con las unidas (encajadas) en un PLY binario en mm. -> (out, puntos)"""
    import entrada
    import ejemplos
    from dataclasses import replace
    q = replace(prm, clean=False, crop=[], keep_groups=[])
    xyz, rgb, nrm, info = entrada.load_points(path, q, log, mark_only=True)
    o = info.get('origin_mm') or [0, 0, 0]
    note = 'nube2stl unidades: mm' + (f' (origen restado: {o[0]:.0f} {o[1]:.0f} {o[2]:.0f} mm)' if any(o) else '')
    tmp = out + '.tmp'
    ejemplos.write_ply(tmp, xyz, rgb, nrm, comment=note)
    os.replace(tmp, out)
    return out, len(xyz)


def prepare_task(path, xyz_all, rgb_all, info, prm, log, guess=False):
    """Aplica (o no) la limpieza ya calculada, con los grupos sueltos que se conservan (prm.keep_groups: sus
    centros), y los recortes (prm.crop); prepara medidas y vista de orientación."""
    import entrada
    keep = info.get('keep')
    info = dict(info, removed=0, kept=None, crop=None, cropped=0, guess_new=False)
    use = None                                   # máscara final sobre la nube entera (None = todos)
    if keep is not None:
        m = len(info['groups'])
        kept = np.zeros(m, bool)
        if len(prm.keep_groups):
            from scipy.spatial import cKDTree
            d, _ = cKDTree(np.asarray(prm.keep_groups, np.float64).reshape(-1, 3)).query(info['groups'])
            kept = d <= info['group_tol']
        info['kept'] = kept
        n_all = int(info['group_n'].sum())
        if prm.clean:
            use = keep.copy()
            if kept.any():
                use[info['loose_idx'][kept[info['loose_grp']]]] = True
            n = len(xyz_all) - int(use.sum())
            info['removed'] = n
            nk = int(kept.sum())
            log(f'Limpieza: se quitan {n:,} puntos sueltos (en rojo en la vista de la nube)'
                + (f'; se conserva{"n" if nk > 1 else ""} {nk} grupo{"s" if nk > 1 else ""} '
                   f'elegido{"s" if nk > 1 else ""} ({n_all - n:,} puntos, en verde)' if nk else ''))
        else:
            log(f'Limpieza desactivada: se usan también los {n_all:,} puntos sueltos (en rojo en la vista de la nube)')
    if prm.crop:
        cm = entrada.crop_mask(xyz_all, prm.crop)
        info['crop'] = cm
        before = len(xyz_all) if use is None else int(use.sum())
        use = cm if use is None else use & cm
        info['cropped'] = before - int(use.sum())
        log(f'Recorte: se quitan {info["cropped"]:,} puntos ({len(prm.crop)} recorte{"s" if len(prm.crop) > 1 else ""}; '
            f'en gris claro en la vista de la nube)')
    xyz, rgb = xyz_all, rgb_all
    if use is not None:
        if not use.any():
            raise ValueError('El recorte no deja ningún punto: deshazlo y prueba con otro rectángulo')
        xyz = xyz_all[use]
        rgb = rgb_all[use] if rgb_all is not None else None
    if guess:                                    # qué parece la nube (solo al abrirla): se propone en el selector
        import superficie
        f = 1000.0 if 'metros' in entrada.units_hint(info, xyz.max(0) - xyz.min(0)) else 1.0
        gk = superficie.guess_kind(xyz, factor=f)
        info['guess'] = gk[:2]
        info['guess_surface'] = gk[2].get('surface')       # p. ej. «revolución» para una vasija decorada
        info['guess_corner'] = gk[2].get('corner')         # ángulo entre dos caras grandes (esquina) o None
        info['guess_new'] = True
    info.pop('vessel', None)
    if entrada.unrolls(prm):
        import superficie
        xyz = superficie.unroll_surface(xyz, prm.surface, log=log)
    elif entrada.is_vessel(prm):                 # vasija a partir del fragmento: la sección girada
        import revolucion
        xyz, _, info['vessel'] = revolucion.vessel(xyz, log=log)
        rgb = None
    info['prep'] = prep_state(prm)
    stats = relief.face_stats(xyz, relief.Params(view=prm.view, flip=prm.flip, mirror=prm.mirror,
                                                  behind=prm.behind, surface=prm.surface,
                                                  plane=prm.plane, plane_pts=prm.plane_pts))
    log(f'Cara del relieve: {stats["length"]:.0f} × {stats["width"]:.0f}; el escaneo permite celdas de '
        f'{stats["res_scan"]:.2f}')
    ex = stats['obj']['ext']
    log(f'Como objeto completo (método «objeto»): {ex[0]:.0f} × {ex[1]:.0f} × {ex[2]:.0f} en x, y, z')
    orient = relief.OrientPreview(xyz, prm=prm)      # vista de orientación en tiempo real (muestra gruesa)
    return path, xyz, rgb, stats, orient, info, xyz_all, rgb_all


class Job:
    """Proceso externo en curso (para poder cancelarlo)."""
    proc = None
    cancelled = False

    def cancel(self):
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()


def run_external(path, prm, log, job):
    """Reconstruye en un proceso aparte: PyMeshLab y PyQt5 no pueden convivir en el mismo proceso."""
    import subprocess
    import tempfile
    import proceso
    with tempfile.TemporaryDirectory(prefix='nube2stl_') as td:
        pj, out = os.path.join(td, 'ajustes.json'), os.path.join(td, 'resultado.npz')
        with open(pj, 'w', encoding='utf-8') as f:
            f.write(prm.to_json())
        job.proc = subprocess.Popen(relief.helper_cmd('proceso', path, pj, out), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                    bufsize=1, creationflags=relief.NO_WINDOW)
        tail, said = [], []
        mem = _watch_memory(job)
        for line in job.proc.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-15:]
            if line and not line.startswith(('Traceback', '  File', '    ')):
                log(line)
                said.append(line)
                mem['step'] = line
        rc = job.proc.wait()
        job.proc = None
        mem['done'] = True
        if job.cancelled and not mem.get('stopped'):
            raise RuntimeError('Cancelado por el usuario')
        if rc != 0:
            kept = _keep_failure(pj, said, mem)
            used = (f'El proceso llegaba a {mem["peak"]:.1f} GB' + (f' (estimado: {mem["est"]:.1f} GB)'
                                                                     if mem.get('est') else ''))
            where = f' Iba por: «{mem["step"][:160]}».' if mem.get('step') else ''
            if mem.get('stopped'):
                raise RuntimeError(f'Reconstrucción parada {OOM_KEY}: {used} y en el equipo quedaban '
                                   f'{mem["stopped"]:.1f} GB libres.{where} Ajustes del intento: {kept}')
            if rc in (-9, 137):
                raise RuntimeError(f'{OOM_MSG} {used}.{where} Ajustes del intento: {kept}')
            raise RuntimeError('Error en la reconstrucción:\n' + '\n'.join(tail[-6:]))
        return proceso.load_result(out)


def _watch_memory(job):
    """Vigila en segundo plano la memoria del proceso de reconstrucción (y de los que lance él): apunta el pico y,
    si la memoria libre del equipo baja de MEM_GUARD_GB, lo para (mejor un aviso claro que el equipo atascado
    llenando el swap hasta que el sistema lo mate)."""
    import threading
    mem = {'peak': 0.0, 'stopped': None, 'step': '', 'done': False, 'est': getattr(job, 'est', None)}
    try:
        import psutil
        root = psutil.Process(job.proc.pid)
    except Exception:
        return mem

    def loop():
        while not mem['done'] and job.proc is not None and job.proc.poll() is None:
            try:
                procs = [root] + root.children(recursive=True)
                rss = sum(q.memory_info().rss for q in procs) / 2**30
            except psutil.Error:
                rss, procs = mem['peak'], []
            mem['peak'] = max(mem['peak'], rss)
            av, _ = relief.memory_available()
            if av is not None and av < MEM_GUARD_GB and rss > 1.0:
                mem['stopped'] = av
                job.cancelled = True
                for q in reversed(procs):
                    try:
                        q.kill()
                    except psutil.Error:
                        pass
                return
            time.sleep(0.25)
    threading.Thread(target=loop, daemon=True).start()
    return mem


def _keep_failure(pj, said, mem):
    """Guarda los ajustes y el registro de una reconstrucción fallida (para repetirla o enviarla)."""
    import shutil
    import registro
    folder = os.path.dirname(registro.config_file())
    try:
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, 'ultimo_fallo.json')
        shutil.copy(pj, dst)
        with open(os.path.join(folder, 'ultimo_fallo.txt'), 'w', encoding='utf-8') as f:
            f.write(f'pico {mem["peak"]:.2f} GB · estimado {mem.get("est")} · parado con {mem.get("stopped")} GB libres\n')
            f.write('\n'.join(said[-60:]) + '\n')
        return dst
    except OSError:
        return '(no se han podido guardar)'


def recon_task(path, prm, job, log):
    t = time.time()
    r = run_external(path, prm, log, job)
    log(f'Reconstrucción en {time.time() - t:.1f} s')
    return r


def save_task(path, res, kind, reg, log):
    """reg: None (sin registro) o dict(prm, source, eid): anotar en el registro de piezas (eid si ya lo está)."""
    log(f'Guardando {path}…')
    eid = None
    if reg is None:
        relief.export(path, res, kind)
    else:
        import registro
        eid = registro.export_registered(path, res, reg['prm'], reg['source'], kind, reg.get('eid'), log=log)
    log(f'Guardado ({os.path.getsize(path) / 1e6:.1f} MB)')
    return path, eid



def batch_task(paths, outdir, prm, kind, job, pdf, reg, log):
    """Lote: cada nube con los mismos ajustes (en el proceso aparte, como «Generar»), su malla, su ficha PDF si se
    pide y una fila en lote_resumen.csv (se reescribe tras cada fichero: si se corta, lo hecho queda anotado)."""
    import csv
    import entrada
    ext = relief.EXT[kind]
    done, rows = [], []
    head = ['nube', 'estado', 'malla', 'ancho_mm', 'fondo_mm', 'alto_mm', 'volumen_cm3', 'triangulos', 'cerrada',
            'registro', 'segundos', 'error']
    if reg:
        import registro
    csv_path = os.path.join(outdir, 'lote_resumen.csv')
    for i, p in enumerate(paths, 1):
        log(f'[{i}/{len(paths)}] {os.path.basename(p)}')
        if job.cancelled:
            log('Lote cancelado')
            break
        t = time.time()
        lines = []
        row = dict(nube=p, estado='error')
        try:
            r = run_external(p, prm, lambda s: (lines.append(s), log('    ' + s)), job)
            out = os.path.join(outdir, os.path.splitext(os.path.basename(p))[0] + ext)
            if os.path.abspath(out) == os.path.abspath(p):
                out = out[:-len(ext)] + '_malla' + ext
            src = dict(path=p)
            for ln in lines:                           # lo que dijo el proceso al leer la nube
                if ln.startswith('Unidades de la nube:'):
                    src['units'] = ln.split(':', 1)[1].split('(')[0].strip()
            eid = None
            if reg:
                eid = registro.export_registered(out, r, prm, src, kind, log=lambda s: log('    ' + s))
                row['registro'] = eid or ''
            else:
                relief.export(out, r, kind)
            log(f'    -> {out}')
            if pdf:
                import ficha
                pdf_path = os.path.splitext(out)[0] + '_ficha.pdf'
                ficha.make_pdf(pdf_path, r, prm, dict(src, id=eid) if eid else src, '\n'.join(lines), param_label)
                log(f'    -> {pdf_path}')
                if eid:
                    registro.update(eid, ficha=os.path.abspath(pdf_path))
            sx, sy, sz = r.size
            row.update(estado='ok', malla=out, ancho_mm=f'{sx:.1f}', fondo_mm=f'{sy:.1f}', alto_mm=f'{sz:.1f}',
                       volumen_cm3=f'{r.volume / 1000:.1f}', triangulos=len(r.tris),
                       cerrada='sí' if r.bad_edges == 0 else 'no')
            done.append(out)
        except Exception as e:
            log(f'    ERROR: {e}')
            row['error'] = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
        row['segundos'] = f'{time.time() - t:.0f}'
        rows.append(row)
        try:
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:   # con BOM: Excel lo abre bien
                w = csv.DictWriter(f, head, delimiter=';')
                w.writeheader()
                w.writerows(rows)
        except OSError as e:
            log(f'AVISO: no se pudo escribir {csv_path}: {e}')
    log(f'Resumen del lote: {csv_path}')
    return done


class BatchDialog(QDialog):
    """Lote: ficheros (o carpeta), carpeta de salida, formato y ficha PDF."""
    def __init__(self, parent):
        import entrada
        super().__init__(parent)
        self.setWindowTitle('Procesar varios ficheros')
        self.setMinimumWidth(560)
        self.exts = entrada.EXTS
        lay = QVBoxLayout(self)
        top = QLabel('Se generan todas con los <b>parámetros actuales</b> (como al pulsar «Generar»), una tras '
                     'otra.')
        top.setWordWrap(True)
        lay.addWidget(top)
        row = QHBoxLayout()
        self.lbl = QLabel('Ningún fichero elegido')
        self.lbl.setObjectName('muted')
        self.lbl.setWordWrap(True)
        b1 = QPushButton('Ficheros…')
        b1.clicked.connect(self.pick_files)
        b2 = QPushButton('Carpeta…')
        b2.setToolTip('Todas las nubes y mallas admitidas de una carpeta')
        b2.clicked.connect(self.pick_dir)
        row.addWidget(self.lbl, 1)
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)
        form = QFormLayout()
        orow = QHBoxLayout()
        self.out = QLineEdit()
        bo = QPushButton('…')
        bo.clicked.connect(self.pick_out)
        orow.addWidget(self.out, 1)
        orow.addWidget(bo)
        form.addRow('Carpeta de salida', orow)
        self.fmt = QComboBox()
        self.fmt.addItems(list(relief.FORMATS))
        form.addRow('Formato', self.fmt)
        self.pdf = QCheckBox('Ficha PDF de cada pieza')
        self.pdf.setChecked(True)
        form.addRow('', self.pdf)
        import registro
        self.reg = QCheckBox('Anotar cada pieza en el registro de piezas')
        self.reg.setChecked(registro.auto_enabled())
        self.reg.setToolTip(f'Carpeta del registro: {registro.registry_dir()}\n(se cambia en Archivo → Registro de piezas)')
        form.addRow('', self.reg)
        lay.addLayout(form)
        note = QLabel('Al final queda un resumen <b>lote_resumen.csv</b> en la carpeta de salida (medidas, volumen, '
                      'si la malla está cerrada, tiempo y errores de cada fichero).')
        note.setObjectName('muted')
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QHBoxLayout()
        bb.addStretch(1)
        cancel = QPushButton('Cancelar')
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton('Procesar')
        self.ok.setObjectName('primary')
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        bb.addWidget(cancel)
        bb.addWidget(self.ok)
        lay.addLayout(bb)
        self.paths = []
        self.out.textChanged.connect(self._check)

    def _set(self, paths):
        self.paths = sorted(paths)
        n = len(self.paths)
        self.lbl.setText(f'<b>{n} fichero{"s" if n != 1 else ""}</b>: ' +
                         ', '.join(os.path.basename(p) for p in self.paths[:6]) + (' …' if n > 6 else ''))
        if self.paths and not self.out.text():
            self.out.setText(os.path.join(os.path.dirname(self.paths[0]), 'mallas'))
        self._check()

    def pick_files(self):
        import entrada
        paths, _ = QFileDialog.getOpenFileNames(self, 'Nubes a procesar', os.getcwd(), entrada.OPEN_FILTER)
        if paths:
            self._set(paths)

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, 'Carpeta con nubes', os.getcwd())
        if d:
            self._set([os.path.join(d, f) for f in os.listdir(d) if os.path.splitext(f)[1].lower() in self.exts])

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, 'Carpeta de salida', self.out.text() or os.getcwd())
        if d:
            self.out.setText(d)

    def _check(self, *_):
        self.ok.setEnabled(bool(self.paths) and bool(self.out.text().strip()))


class MplView(QWidget):
    def __init__(self):
        super().__init__()
        self.fig = Figure(tight_layout=True, facecolor=C['view'])
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(NavigationToolbar2QT(self.canvas, self))
        lay.addWidget(self.canvas)

    def clear(self, msg):
        self.fig.clear()
        self.fig.text(0.5, 0.5, msg, ha='center', va='center', color=C['muted'], fontsize=11)
        self.canvas.draw_idle()


class HelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('Ayuda de nube2stl')
        self.resize(820, 760)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view)
        if os.path.exists(HELP_FILE):
            html = open(HELP_FILE, encoding='utf-8').read()
            import recetas
            import actualizar
            for k, v in [('{{VERSION}}', __version__), ('{{AUTHOR}}', AUTHOR), ('{{YEAR}}', YEAR),
                         ('{{REPO}}', actualizar.page_url() or 'https://github.com/USUARIO/nube2stl'),
                         ('{{RECETAS}}', recetas.help_table(param_label)),
                         ('{{IMPRESORAS}}', recetas.printers_text())]:
                html = html.replace(k, v)
            self.view.document().setBaseUrl(QUrl.fromLocalFile(HERE + '/'))   # para las imágenes de ayuda_img/
            self.view.setHtml(html)
        else:
            self.view.setHtml(f'<p>No se encuentra {HELP_FILE}</p>')

    def show_at(self, anchor=None):
        self.show()
        self.raise_()
        if anchor:
            self.view.scrollToAnchor(anchor)
        else:
            self.view.verticalScrollBar().setValue(0)


def _lib_versions():
    from importlib import metadata
    from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    out = [('Python', sys.version.split()[0]), ('Qt / PyQt5', f'{QT_VERSION_STR} / {PYQT_VERSION_STR}')]
    for name in ['numpy', 'scipy', 'matplotlib', 'pyqtgraph', 'open3d', 'pymeshlab']:
        try:                       # sin importar: pymeshlab no puede cargarse junto a PyQt5
            out.append((name, metadata.version(name)))
        except metadata.PackageNotFoundError:
            out.append((name, 'no instalado'))
    return out


class AboutDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('Acerca de nube2stl')
        self.resize(560, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(0)
        head = QFrame()
        head.setObjectName('header')
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(22, 18, 22, 16)
        hrow.setSpacing(18)
        hrow.addWidget(logo_label(84))
        hl = QVBoxLayout()
        hrow.addLayout(hl, 1)
        hl.setSpacing(2)
        t = QLabel(f'nube2stl <span style="font-size:11pt; font-weight:600; color:#ffd9c2">v{__version__}</span>')
        t.setObjectName('appTitle')
        hl.addWidget(t)
        sub = QLabel('De la nube de puntos de un relieve a una pieza imprimible en 3D')
        sub.setObjectName('appSub')
        hl.addWidget(sub)
        who = QLabel(f'Creado por <b>{AUTHOR}</b> · {YEAR}')
        who.setStyleSheet('color: white; font-size: 10.5pt; margin-top: 8px;')
        hl.addWidget(who)
        lay.addWidget(head)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        hist = QTextBrowser()
        html = []
        for i, (v, date, items) in enumerate(HISTORY):
            badge = (f' <span style="background:{C["accent"]}; color:white; font-size:8pt;">&nbsp;actual&nbsp;</span>'
                     if i == 0 else '')
            html.append(f'<p style="margin:10px 0 2px 0"><b style="color:{C["navy"]}; font-size:11pt">{v}</b>'
                        f'{badge} <span style="color:{C["muted"]}">· {date}</span></p><ul style="margin-top:0">'
                        + ''.join(f'<li>{x}</li>' for x in items) + '</ul>')
        hist.setHtml(''.join(html))
        tabs.addTab(hist, 'Versiones')
        info = QTextBrowser()
        info.setOpenExternalLinks(True)
        rows = ''.join(f'<tr><td style="padding:2px 14px 2px 0; color:{C["muted"]}">{n}</td><td>{v}</td></tr>'
                       for n, v in _lib_versions())
        info.setHtml(
            f'<p>nube2stl ajusta el plano de la cara escaneada, convierte los puntos en un mapa de alturas (o en una '
            f'superficie de Poisson en modo 3D), la cierra con paredes y base, y calcula la resolución y la '
            f'simplificación óptimas para la boquilla y el tamaño de impresión.</p>'
            f'<p><b>Procesadores:</b> {relief.NCPU} lógicos · {relief.NCORES} núcleos</p>'
            f'<p><b>Componentes</b></p><table>{rows}</table>')
        tabs.addTab(info, 'Sistema')
        body = QVBoxLayout()
        body.setContentsMargins(14, 10, 14, 0)
        body.addWidget(tabs)
        btn = QPushButton('Cerrar')
        btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(btn)
        body.addLayout(row)
        lay.addLayout(body)


def check_task(log):
    import actualizar
    return actualizar.check()


def update_task(info, log):
    import actualizar
    return actualizar.update(info, log)


_RUNNING = set()       # búsquedas abandonadas al cerrar el diálogo, hasta que su hilo termine


class UpdateDialog(QDialog):
    """Ayuda › Buscar actualizaciones: busca en GitHub la última versión, enseña sus novedades y la instala (git pull
    o copiando la carpeta; el ejecutable solo abre la página de descarga). Ver actualizar.py."""

    def __init__(self, parent, info=None):
        super().__init__(parent)
        import actualizar
        import registro
        self.mod, self.info, self.worker, self.changed = actualizar, info, None, False
        self.setWindowTitle('Buscar actualizaciones')
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        self.head.setTextFormat(Qt.RichText)
        self.head.setOpenExternalLinks(True)
        lay.addWidget(self.head)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        lay.addWidget(self.text, 1)
        self.chk = QCheckBox('Buscar al abrir el programa (como mucho una vez al día)')
        self.chk.setChecked(bool(registro.load_config().get('buscar_actualizaciones', True)))
        self.chk.toggled.connect(lambda on: registro.save_config(buscar_actualizaciones=on))
        lay.addWidget(self.chk)
        row = QHBoxLayout()
        self.b_page = QPushButton('Ver en GitHub')
        self.b_page.clicked.connect(self.open_page)
        self.b_go = QPushButton('Actualizar ahora')
        self.b_go.setObjectName('primary')
        self.b_go.clicked.connect(self.go)
        self.b_restart = QPushButton('Reiniciar nube2stl')
        self.b_restart.clicked.connect(self.restart)
        b_close = self.b_close = QPushButton('Cerrar')
        b_close.clicked.connect(self.reject)
        for b in (self.b_page, self.b_go, self.b_restart):
            b.hide()
            row.addWidget(b)
        row.addStretch()
        row.addWidget(b_close)
        lay.addLayout(row)
        if not actualizar.repo():
            self.head.setText(f'<b>nube2stl {__version__}</b><br>Esta copia aún no sabe en qué repositorio de GitHub '
                              f'se publican las versiones (GITHUB_REPO en version.py).')
            self.chk.setEnabled(False)
        elif info:
            self.show_info(info)
        else:
            self.head.setText(f'<b>nube2stl {__version__}</b><br>Buscando la última versión en '
                              f'{actualizar.page_url()}…')
            self.run(check_task, (), self.show_info)

    def run(self, fn, args, done):
        self.worker = Worker(fn, *args)
        self.worker.done.connect(done)
        self.worker.failed.connect(self.failed)
        self.worker.log.connect(self.text.append)
        self.worker.start()

    def done(self, r):
        """Cerrar (botón, Esc o la X): sin más. Una búsqueda en marcha se abandona (el hilo acaba solo y nadie
        recoge su respuesta); mientras se instala no se cierra."""
        w = self.worker
        if w is not None and w.isRunning():
            if not self.b_close.isEnabled():
                return
            for sig in (w.done, w.failed, w.log):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            _RUNNING.add(w)                   # que el hilo no se destruya con el diálogo (Qt abortaría)
            w.finished.connect(lambda: _RUNNING.discard(w))
        super().done(r)

    def failed(self, msg):
        self.b_close.setEnabled(True)
        self.b_close.setText('Cerrar')
        self.b_go.setEnabled(True)
        self.head.setText(f'<b>nube2stl {__version__}</b><br><span style="color:{C["bad"]}">{msg}</span>')

    def show_info(self, d):
        import registro
        registro.save_config(ultima_busqueda=time.time())
        self.info = d
        when = f' · {d["date"]}' if d.get('date') else ''
        if not d['new']:
            self.head.setText(f'<b>nube2stl {__version__}</b><br>Tienes la última versión publicada '
                              f'({d["version"]}{when}).')
        else:
            self.head.setText(f'<b>Hay una versión nueva: {d["version"]}</b>{when} (tienes la {__version__})<br>'
                              f'<span style="color:{C["muted"]}">Instalación: {self.mod.KIND_TEXT[d["kind"]]}</span>')
            self.b_go.setText('Descargarla' if d['kind'] == 'ejecutable' else 'Actualizar ahora')
            self.b_go.show()
        self.b_page.show()
        notes = d.get('notes') or ''
        self.text.setMarkdown(f'**Novedades de la {d["version"]}**\n\n{notes}' if notes and d['new'] else notes)

    def open_page(self):
        from PyQt5.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl((self.info or {}).get('url') or self.mod.page_url()))

    def go(self):
        if self.info and self.info['kind'] == 'ejecutable':
            return self.open_page()
        self.b_go.setEnabled(False)
        self.text.clear()
        self.head.setText(f'<b>Actualizando a la {self.info["version"]}…</b>')
        self.b_close.setEnabled(False)       # a medias el programa quedaría mezclado: se espera a que acabe
        self.b_close.setText('Actualizando…')
        self.run(update_task, (self.info,), self.updated)

    def updated(self, changed):           # no «done»: es el método de QDialog que cierra la ventana
        self.b_close.setEnabled(True)
        self.b_close.setText('Cerrar')
        self.changed = changed
        self.b_go.hide()
        if changed:
            self.head.setText(f'<b>Actualizado a la {self.info["version"]}.</b><br>Los cambios se usan al volver a '
                              f'abrir nube2stl.')
            self.b_restart.show()
        else:
            self.head.setText('<b>No había nada que cambiar.</b>')

    def restart(self):
        """Cierra la ventana (con sus preguntas de siempre) y, si se cierra, abre el programa de nuevo."""
        from PyQt5.QtCore import QProcess
        win = self.parent()
        self.accept()                     # (aquí no hay hilo en marcha: la instalación ya ha acabado)
        if win is not None and not win.close():
            return
        QProcess.startDetached(sys.executable, [os.path.join(HERE, 'nube2stl.py')])
        QApplication.quit()


class RowForm(QGridLayout):
    """Como QFormLayout (etiqueta | campo, o una fila entera), pero sobre una rejilla: una fila con sus widgets
    ocultos no ocupa nada, ni siquiera el espacio entre filas (QFormLayout de Qt 5 sí lo deja y quedan huecos).
    Al terminar, finish() empuja todo hacia arriba."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnStretch(1, 1)
        self.setHorizontalSpacing(8)

    def addRow(self, a, b=None):
        r = self.rowCount()
        if b is None:
            if isinstance(a, QWidget):
                self.addWidget(a, r, 0, 1, 2)
            else:
                self.addLayout(a, r, 0, 1, 2)
        else:
            if isinstance(a, str):
                a = QLabel(a)
            self.addWidget(a, r, 0)
            self.addWidget(b, r, 1)

    def finish(self):
        self.setRowStretch(self.rowCount(), 1)


class StepBar(QFrame):
    """Barra de pasos encima de las vistas: dónde estás (naranja), lo hecho (✔) y lo que falta. Cada paso es un
    botón que lleva a su sitio o hace la acción. Si se cambia algún parámetro después de generar, la vista previa,
    la pieza y el guardado vuelven a quedar pendientes."""
    STEPS = [('abrir', 'Abrir', 'Abrir una nube de puntos o una malla (Ctrl+O)'),
             ('nube', 'Revisar la nube', 'Ver la nube: limpiar grupos sueltos y recortar lo que sobra (opcional)'),
             ('tipo', 'Tipo y tamaño', '¿Qué has escaneado? y el tamaño de la pieza (pestaña Tamaño)'),
             ('orientar', 'Orientar', 'Cómo saldrá la pieza: desde dónde se mira, qué va arriba y el giro'),
             ('previa', 'Vista previa', 'Generar deprisa, con menos resolución, para comprobar (Ctrl+P)'),
             ('generar', 'Generar', 'Generar la pieza definitiva (Ctrl+G)'),
             ('guardar', 'Guardar', 'Guardar la malla en STL, 3MF, OBJ o PLY (Ctrl+S)')]

    def __init__(self, on_click):
        super().__init__()
        self.setObjectName('steps')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(0)
        self.btns = {}
        for i, (key, text, tip) in enumerate(self.STEPS):
            if i:
                a = QLabel('›')
                a.setObjectName('stepArrow')
                lay.addWidget(a)
            b = QToolButton()
            b.setObjectName('step')
            b.setText(f'{i + 1}  {text}')
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.clicked.connect(lambda _=False, k=key: on_click(k))
            lay.addWidget(b)
            self.btns[key] = b
        lay.addStretch(1)

    def set_state(self, done, enabled, stale=False):
        """done: pasos hechos; el actual es el primero sin hacer. enabled: pasos que se pueden pulsar."""
        now = next((k for k, _, _ in self.STEPS if k not in done), None)
        for i, (key, text, _) in enumerate(self.STEPS):
            b = self.btns[key]
            st = 'now' if key == now else 'done' if key in done else ''
            b.setText(f'{"✔" if st == "done" else i + 1}  {text}')
            b.setProperty('state', st)
            b.setEnabled(key in enabled)
            b.style().unpolish(b)
            b.style().polish(b)
        g = self.btns['generar']
        g.setToolTip(self.STEPS[5][2] + ('\nHas cambiado parámetros desde la última pieza: vuelve a generar.'
                                         if stale else ''))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'nube2stl {__version__} — nube de puntos a superficie cerrada')
        self.resize(1350, 860)
        self.path = self.xyz = self.rgb = self.result = self.orient = None
        self.worker = None
        self.keep_groups = []             # centros de los grupos sueltos que se conservan (clic en la nube)
        self.crops = []                   # recortes hechos en la vista de la nube (Params.crop)
        self.merges = []                  # otras nubes unidas a la abierta (Params.merge, ver unir.py)
        self.plane_pts = []               # puntos marcados para el plano de la cara (Params.plane_pts)
        self.classes = []                 # LiDAR: clases que se usan (Params.classes; vacío = todas menos el ruido)
        self.reverse = []                 # pieza de dos caras: el reverso (Params.reverse)
        self._plane_pick = None           # marcando el plano: los puntos de esta vez
        self.help = None
        self.widgets = {}
        self._menus()

        self.job = None
        self.setStyleSheet(STYLE)
        # cabecera de lado a lado (por encima de los paneles), en una barra fija
        from PyQt5.QtWidgets import QToolBar
        hb = QToolBar('Cabecera')
        hb.setObjectName('cabecera')
        hb.setMovable(False)
        hb.setFloatable(False)
        hb.setContextMenuPolicy(Qt.PreventContextMenu)
        hb.toggleViewAction().setVisible(False)
        hb.addWidget(self._header())
        self.addToolBar(Qt.TopToolBarArea, hb)
        central = QWidget()
        central.setObjectName('central')
        wl = QVBoxLayout(central)
        wl.setContentsMargins(4, 10, 4, 6)
        wl.addWidget(self._center_panel())
        self.setCentralWidget(central)
        self._docks()
        self._statusbar()
        self.update_buttons()
        QTimer.singleShot(4000, self.auto_update_check)

    # ---------- paneles acoplables (menú Ver)
    VIEW_VERSION = 1                      # súbelo si cambian los paneles: la disposición guardada deja de valer

    def _docks(self):
        """Parámetros a la izquierda; Fichero, Resultado y Registro a la derecha. Cada uno se puede arrastrar por
        su título a cualquier lado (o encima de otro, en pestañas), sacar como ventana o cerrar; Ver › los vuelve
        a poner. La disposición se guarda al salir."""
        from PyQt5.QtWidgets import QDockWidget
        self.setDockNestingEnabled(True)
        self.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.RightDockWidgetArea)
        fcard, rcard, gcard = self._right_cards()
        self.docks = {}
        for key, title, w, area in [('parametros', 'Parámetros', self._left_panel(), Qt.LeftDockWidgetArea),
                                    ('fichero', 'Fichero', fcard, Qt.RightDockWidgetArea),
                                    ('resultado', 'Resultado', rcard, Qt.RightDockWidgetArea),
                                    ('registro', 'Registro', gcard, Qt.RightDockWidgetArea)]:
            d = QDockWidget(title, self)
            d.setObjectName(f'panel_{key}')
            d.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea)
            hold = QWidget()                      # un poco de aire alrededor de la tarjeta
            hl = QVBoxLayout(hold)
            hl.setContentsMargins(6, 0, 6, 8)
            if key in ('fichero', 'resultado'):   # si no cabe entero, que se pueda desplazar (no se pierde nada)
                sc = QScrollArea()
                sc.setWidget(w)
                sc.setWidgetResizable(True)
                sc.setFrameShape(QFrame.NoFrame)
                sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                hl.addWidget(sc)
            else:
                hl.addWidget(w)
            d.content = w
            d.setWidget(hold)
            self.addDockWidget(area, d)
            self.docks[key] = d
        self.resizeDocks([self.docks['parametros'], self.docks['registro']], [410, 330], Qt.Horizontal)
        self.resizeDocks([self.docks['fichero'], self.docks['resultado'], self.docks['registro']], [130, 110, 600],
                         Qt.Vertical)
        self._view_default = self.saveState(self.VIEW_VERSION)
        self._view_menu()
        import registro
        st = registro.load_config().get('vista')
        if st:
            from PyQt5.QtCore import QByteArray
            self.restoreState(QByteArray.fromBase64(st.encode()), self.VIEW_VERSION)

    def _view_menu(self):
        m = self.view_menu
        m.clear()
        for d in self.docks.values():
            a = d.toggleViewAction()
            a.setText(f'Panel {d.windowTitle()}')
            m.addAction(a)
        m.addSeparator()
        a = QAction('Todos los paneles a la derecha', self)
        a.triggered.connect(lambda: self.view_side(Qt.RightDockWidgetArea))
        m.addAction(a)
        a = QAction('Todos los paneles a la izquierda', self)
        a.triggered.connect(lambda: self.view_side(Qt.LeftDockWidgetArea))
        m.addAction(a)
        a = QAction('Restablecer la vista', self)
        a.triggered.connect(self.view_reset)
        m.addAction(a)
        m.addSeparator()
        a = QAction('Arrastra un panel por su título para cambiarlo de lado, o suéltalo encima de otro para ponerlos '
                    'en pestañas', self)
        a.setEnabled(False)
        m.addAction(a)

    def fit_dock(self, key, again=True):
        """Que un panel pequeño (Fichero, Resultado) enseñe todo lo que tiene al cambiar su contenido: su alto
        mínimo pasa a ser el de su contenido (como mucho el 45 % de la ventana) y crece a costa de los de su columna."""
        d = getattr(self, 'docks', {}).get(key)
        if d is None:
            return
        w, card = d.widget(), d.content
        lay = card.layout()
        lay.invalidate()
        lay.activate()
        need = max(lay.totalHeightForWidth(card.width()) if lay.hasHeightForWidth() else 0,
                   lay.totalSizeHint().height()) + 10            # + el margen de abajo
        w.setMinimumHeight(min(need, int(self.height() * 0.45)))
        if not d.isFloating() and d.isVisible() and w.height() < need:
            self.resizeDocks([d], [need + d.height() - w.height()], Qt.Vertical)
        if again:                                 # al cambiar el alto, el texto se reparte en otras líneas
            QTimer.singleShot(80, lambda: self.fit_dock(key, again=False))

    def view_side(self, area):
        """Todos los paneles a un lado, uno debajo de otro (Parámetros arriba, el más alto)."""
        ds = list(self.docks.values())
        for d in ds:
            d.setFloating(False)
            d.show()
            self.addDockWidget(area, d, Qt.Vertical)
        self.resizeDocks(ds, [560, 90, 110, 240], Qt.Vertical)
        self.resizeDocks([ds[0]], [410], Qt.Horizontal)

    def view_reset(self):
        for d in self.docks.values():
            d.setFloating(False)
            d.show()
        self.restoreState(self._view_default, self.VIEW_VERSION)

    def save_view(self):
        import registro
        try:
            registro.save_config(vista=bytes(self.saveState(self.VIEW_VERSION).toBase64()).decode())
        except OSError:
            pass

    # ---------- piezas de la ventana
    @staticmethod
    def _card(title=None):
        f = QFrame()
        f.setObjectName('card')
        lay = QVBoxLayout(f)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        if title:
            t = QLabel(title.upper())
            t.setObjectName('cardTitle')
            lay.addWidget(t)
        return f, lay

    def _header(self):
        h = QFrame()
        h.setObjectName('header')
        lay = QHBoxLayout(h)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(10)
        lay.addWidget(logo_label(44))
        tb = QVBoxLayout()
        tb.setSpacing(0)
        t = QLabel('nube2stl')
        t.setObjectName('appTitle')
        sub = QLabel(f'Nube de puntos  →  pieza imprimible   ·   v{__version__}   ·   {AUTHOR}')
        sub.setObjectName('appSub')
        tb.addWidget(t)
        tb.addWidget(sub)
        lay.addLayout(tb)
        lay.addSpacing(18)
        st = self.style()
        self.btn_open = QPushButton(st.standardIcon(QStyle.SP_DialogOpenButton), ' Abrir')
        self.btn_open.setToolTip('Abrir una nube de puntos o una malla: PLY, XYZ, PTS, CSV, LAS, LAZ, E57, OBJ, STL '
                                 '(Ctrl+O)')
        self.btn_open.clicked.connect(self.open_file)
        self.btn_run = QPushButton(st.standardIcon(QStyle.SP_MediaPlay), ' Generar')
        self.btn_run.setObjectName('primary')
        self.btn_run.setToolTip('Generar la superficie cerrada con los parámetros actuales (Ctrl+G)')
        self.btn_run.setShortcut('Ctrl+G')
        self.btn_run.clicked.connect(lambda: self.run_recon())
        self.btn_prev = QPushButton(st.standardIcon(QStyle.SP_FileDialogDetailedView), ' Vista previa')
        self.btn_prev.setToolTip(f'Generar deprisa con una resolución {relief.PREVIEW_FACTOR:g} veces más gruesa, '
                                 f'para comprobar\norientación, tamaño, corte o grosor antes de la definitiva '
                                 f'(no se puede guardar) (Ctrl+P)')
        self.btn_prev.setShortcut('Ctrl+P')
        self.btn_prev.clicked.connect(lambda: self.run_recon(preview=True))
        self.btn_cancel = QPushButton(st.standardIcon(QStyle.SP_BrowserStop), ' Cancelar')
        self.btn_cancel.clicked.connect(self.cancel_job)
        self.btn_save = QPushButton(st.standardIcon(QStyle.SP_DialogSaveButton), ' Guardar')
        self.btn_save.setToolTip('Guardar la malla en STL, OBJ o PLY (Ctrl+S)')
        self.btn_save.clicked.connect(self.save_mesh)
        self.btn_wiz = QPushButton(st.standardIcon(QStyle.SP_FileDialogContentsView), ' Asistente')
        self.btn_wiz.setToolTip('Rellenar los parámetros respondiendo unas preguntas (opcional, Ctrl+W)')
        self.btn_wiz.clicked.connect(self.open_wizard)
        for b in (self.btn_open, self.btn_wiz, self.btn_prev, self.btn_run, self.btn_cancel, self.btn_save):
            b.setCursor(Qt.PointingHandCursor)
            lay.addWidget(b)
        lay.addStretch(1)
        self.lbl_tag = QLabel('')
        self.lbl_tag.setObjectName('fileTag')
        lay.addWidget(self.lbl_tag)
        lay.addSpacing(10)
        bh = QPushButton(st.standardIcon(QStyle.SP_DialogHelpButton), ' Ayuda')
        bh.setCursor(Qt.PointingHandCursor)
        bh.clicked.connect(lambda: self.show_help('pasos'))
        lay.addWidget(bh)
        return h

    def _left_panel(self):
        card, lay = self._card()                  # el título lo pone el panel acoplable (ver _docks)
        b = self.btn_settings = QToolButton()     # cargar/guardar/restablecer ajustes: junto a «¿Qué has escaneado?»
        b.setText('Ajustes ')
        b.setCursor(Qt.PointingHandCursor)
        b.setPopupMode(QToolButton.InstantPopup)
        b.setToolTip('Cargar o guardar los ajustes en un fichero .json, o volver a los de por defecto '
                     '(también en el menú Archivo)')
        from PyQt5.QtWidgets import QMenu
        m = QMenu(b)
        for text, fn in [('Cargar ajustes…', self.load_params), ('Guardar ajustes…', self.save_params),
                         (None, None), ('Restablecer valores por defecto', self.reset_params)]:
            if text is None:
                m.addSeparator()
            else:
                m.addAction(text, fn)
        b.setMenu(m)
        lay.addWidget(self._kind_selector())
        self.ptabs = QTabWidget()
        self.ptabs.setUsesScrollButtons(False)
        self.ptabs.tabBar().setExpanding(True)
        groups = dict(SPEC)
        defaults = Params()
        self.param_rows = {}                      # nombre -> widgets de su fila (para ocultarla)
        self.sections = []                        # (título, nombres) de cada apartado con título
        self.tab_empty = {}                       # pestaña -> (nota «no hay nada», nombres)
        for name, label, kind, opt, tip in groups['Tipo de pieza']:      # el selector de arriba los maneja
            w = self._make_widget(kind, opt, getattr(defaults, name), label)
            w.setParent(card)
            w.hide()
            self.widgets[name] = w
        for short, desc, tab_groups in PARAM_TABS:
            page = QWidget()
            form = RowForm(page)
            form.setContentsMargins(8, 6, 4, 6)
            form.setVerticalSpacing(7)
            if short == 'Avanzado':
                n = QLabel(ADVANCED_NOTE)
                n.setObjectName('muted')
                n.setWordWrap(True)
                form.addRow(n)
            names_tab = []
            for group in tab_groups:
                head = None
                if len(tab_groups) > 1:
                    head = QLabel(f'<b>{group}</b>')
                    head.setStyleSheet(f'color: {C["teal"]}; margin-top: 4px;')
                    form.addRow(head)
                names = []
                for name, label, kind, opt, tip in groups[group]:
                    w = self._make_widget(kind, opt, getattr(defaults, name), label)
                    w.setToolTip(tip)
                    self.widgets[name] = w
                    holder = QWidget()
                    row = QHBoxLayout(holder)
                    row.setContentsMargins(0, 0, 0, 0)
                    row.setSpacing(6)
                    row.addWidget(w, 1)
                    q = QToolButton()
                    q.setObjectName('help')
                    q.setText('?')
                    q.setCursor(Qt.PointingHandCursor)
                    q.setToolTip('Ayuda sobre este parámetro')
                    q.clicked.connect(lambda _=False, an=HELP_ANCHOR.get(name, f'p-{name}'): self.show_help(an))
                    row.addWidget(q)
                    if kind == 'b':
                        form.addRow(holder)
                        self.param_rows[name] = (holder,)
                    else:
                        lb = QLabel(label)
                        form.addRow(lb, holder)
                        self.param_rows[name] = (lb, holder)
                    names.append(name)
                    if name == 'clean':           # unir otras nubes a la abierta
                        self.merge_lbl = QLabel('')
                        self.merge_lbl.setObjectName('muted')
                        self.merge_lbl.setWordWrap(True)
                        self.merge_lbl.setTextFormat(Qt.RichText)
                        self.merge_lbl.linkActivated.connect(self._merge_link)
                        form.addRow(self.merge_lbl)
                        self.rev_lbl = QLabel('')     # pieza de dos caras: el reverso
                        self.rev_lbl.setObjectName('muted')
                        self.rev_lbl.setWordWrap(True)
                        self.rev_lbl.setTextFormat(Qt.RichText)
                        self.rev_lbl.linkActivated.connect(self._rev_link)
                        self.rev_lbl.hide()
                        form.addRow(self.rev_lbl)
                        self.las_lbl = QLabel('')     # LiDAR: qué clases se usan
                        self.las_lbl.setObjectName('plan')
                        self.las_lbl.setWordWrap(True)
                        self.las_lbl.setTextFormat(Qt.RichText)
                        self.las_lbl.linkActivated.connect(self._las_link)
                        self.las_lbl.hide()
                        form.addRow(self.las_lbl)
                    if name == 'plane':           # marcar tres puntos de la cara en la vista de la nube
                        self.plane_lbl = QLabel('')
                        self.plane_lbl.setObjectName('muted')
                        self.plane_lbl.setWordWrap(True)
                        self.plane_lbl.setTextFormat(Qt.RichText)
                        self.plane_lbl.linkActivated.connect(self._plane_link)
                        form.addRow(self.plane_lbl)
                        self.param_rows[name] = self.param_rows[name] + (self.plane_lbl,)
                    if name == 'boost_size':      # el realce en la pieza (los umbrales van en mm de la piedra)
                        self.boost_lbl = QLabel('')
                        self.boost_lbl.setObjectName('muted')
                        self.boost_lbl.setWordWrap(True)
                        self.boost_lbl.setTextFormat(Qt.RichText)
                        form.addRow(self.boost_lbl)
                        self.param_rows[name] = self.param_rows[name] + (self.boost_lbl,)
                    if name == 'behind':          # medidas de la nube para poder elegir el valor
                        self.behind_lbl = QLabel('')
                        self.behind_lbl.setObjectName('plan')
                        self.behind_lbl.setWordWrap(True)
                        self.behind_lbl.setTextFormat(Qt.RichText)
                        self.behind_lbl.hide()
                        form.addRow(self.behind_lbl)
                if head is not None:
                    self.sections.append((head, names))
                names_tab += names
                if group == 'Tamaño':
                    self.plan_lbl = QLabel('Abre una nube para ver el cálculo.')
                    self.plan_lbl.setObjectName('plan')
                    self.plan_lbl.setWordWrap(True)
                    self.plan_lbl.setTextFormat(Qt.RichText)
                    form.addRow(self.plan_lbl)
                    self.printer_lbl = QLabel('')
                    self.printer_lbl.setObjectName('muted')
                    self.printer_lbl.setWordWrap(True)
                    self.printer_lbl.setTextFormat(Qt.RichText)
                    self.printer_lbl.linkActivated.connect(lambda *_: self.ptabs.setCurrentIndex(len(PARAM_TABS) - 1))
                    form.addRow(self.printer_lbl)
            if short in TAB_EMPTY:
                e = QLabel(TAB_EMPTY[short])
                e.setObjectName('muted')
                e.setWordWrap(True)
                form.addRow(e)
                self.tab_empty[short] = (e, names_tab)
            if short == 'Orientar':
                self._orient_tools(form)
            form.finish()
            sc = QScrollArea()                      # pestañas largas (Orientación): que no se corte nada
            sc.setWidget(page)
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.ptabs.addTab(sc, short)
            self.ptabs.setTabToolTip(self.ptabs.count() - 1, desc)
        lay.addWidget(self.ptabs)
        for w in self.widgets.values():           # el cálculo de impresión se actualiza al cambiar cualquier valor
            sig = getattr(w, 'valueChanged', None) or getattr(w, 'currentTextChanged', None) \
                or getattr(w, 'toggled', None) or getattr(w, 'editingFinished', None)
            sig.connect(self.update_plan)
            sig.connect(self.orient_changed)
        self.ptabs.currentChanged.connect(self._ptab_changed)
        self.widgets['mode'].currentTextChanged.connect(self._mode_enable)
        self.widgets['ratio'].valueChanged.connect(self._mode_enable)
        self.widgets['wall'].valueChanged.connect(self._mode_enable)
        self.widgets['units'].currentTextChanged.connect(self._reload_cloud)
        self.widgets['clean'].toggled.connect(self._toggle_clean)
        self.widgets['ground'].toggled.connect(self._mode_enable)
        self.widgets['detrend'].valueChanged.connect(self._mode_enable)
        self.widgets['boost'].valueChanged.connect(self._mode_enable)
        self.widgets['interior'].currentTextChanged.connect(self._mode_enable)
        self.widgets['corner'].toggled.connect(self._mode_enable)
        self.widgets['surface'].currentTextChanged.connect(self._mode_enable)
        self.widgets['view'].currentTextChanged.connect(lambda *_: self._toggle_clean(keep_view=True))
        self.widgets['view'].currentTextChanged.connect(self._mode_enable)
        self.widgets['plane'].currentTextChanged.connect(self._plane_changed)
        # desenrollar o no la nube cambia las medidas y la vista de orientación
        self.widgets['surface'].currentTextChanged.connect(self._prep_later)
        self.widgets['mode'].currentTextChanged.connect(self._prep_later)
        self.prep_timer = QTimer(self)
        self.prep_timer.setSingleShot(True)
        self.prep_timer.setInterval(60)
        self.prep_timer.timeout.connect(self._check_prep)
        self._mode_enable()
        self._plane_note()
        self.adv_lbl = QLabel('')                 # valores de Avanzado distintos de los de por defecto
        self.adv_lbl.setObjectName('plan')
        self.adv_lbl.setWordWrap(True)
        self.adv_lbl.setTextFormat(Qt.RichText)
        self.adv_lbl.linkActivated.connect(self._adv_link)
        self.adv_lbl.hide()
        lay.addWidget(self.adv_lbl)
        card.setMinimumWidth(380)
        return card

    def _center_panel(self):
        self.center = QStackedWidget()
        # portada mientras no hay nube
        welcome = QWidget()
        wl = QVBoxLayout(welcome)
        wl.addStretch(1)
        card, lay = self._card()
        card.setMaximumWidth(520)
        t = QLabel('<span style="font-size:17pt; font-weight:800; color:%s">Empieza abriendo una nube de puntos</span>'
                   % C['navy'])
        t.setWordWrap(True)
        d = QLabel('Nube de puntos o malla de una cara en relieve (plana o sobre una columna), grabados sobre una '
                   'roca, un bloque, un objeto completo o un terreno o monumento: <b>PLY</b>, <b>XYZ / PTS / CSV</b>, <b>LAS / LAZ</b>, <b>E57</b>, <b>OBJ</b> o '
                   '<b>STL</b>.<br><br>'
                   '<b>1.</b> Abre la nube&nbsp;&nbsp;<b>2.</b> Di qué has escaneado (botones de la izquierda)'
                   '&nbsp;&nbsp;<b>3.</b> Elige el tamaño en <i>Tamaño</i>&nbsp;&nbsp;<b>4.</b> Pulsa <b>Generar</b>'
                   '&nbsp;&nbsp;<b>5.</b> Revisa y <b>Guarda</b>')
        d.setWordWrap(True)
        d.setObjectName('muted')
        b = QPushButton(self.style().standardIcon(QStyle.SP_DialogOpenButton), '  Abrir nube de puntos…')
        b.setObjectName('primary')
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self.open_file)
        g = QPushButton('Ver la guía paso a paso')
        g.setObjectName('link')
        g.setCursor(Qt.PointingHandCursor)
        g.clicked.connect(lambda: self.show_help('pasos'))
        wz = QPushButton('Usar el asistente')
        wz.setObjectName('link')
        wz.setCursor(Qt.PointingHandCursor)
        wz.setToolTip('Opcional: rellena los parámetros respondiendo unas preguntas')
        wz.clicked.connect(self.open_wizard)
        exb = QPushButton('Abrir un ejemplo')
        exb.setObjectName('link')
        exb.setCursor(Qt.PointingHandCursor)
        exb.setToolTip('Nubes de ejemplo de cada tipo (lápida, bloque, columna, roca, vasija, terreno) para '
                       'aprender o enseñar')
        exb.clicked.connect(lambda: self.example_menu.exec_(exb.mapToGlobal(exb.rect().bottomLeft())))
        lay.addWidget(t)
        lay.addWidget(d)
        lay.addSpacing(6)
        br = QHBoxLayout()
        br.addWidget(b)
        br.addStretch(1)
        lay.addLayout(br)
        br = QHBoxLayout()
        br.addWidget(g)
        br.addWidget(wz)
        br.addWidget(exb)
        br.addStretch(1)
        lay.addLayout(br)
        lay.addSpacing(8)
        sig = QLabel(f'nube2stl v{__version__} · creado por {AUTHOR}')
        sig.setStyleSheet(f"color: {C['muted']}; font-size: 8.5pt; border-top: 1px solid {C['line']}; padding-top: 6px;")
        lay.addWidget(sig)
        hl = QHBoxLayout()
        hl.addStretch(1)
        hl.addWidget(card)
        hl.addStretch(1)
        wl.addLayout(hl)
        wl.addStretch(2)
        self.center.addWidget(welcome)

        self.tabs = QTabWidget()
        self.cloud_view = self._make_3d('nube') if HAVE_GL else MplView()
        if HAVE_GL:
            self.cloud_view.extra_items = self._plane_markers
        if HAVE_GL:
            self.cloud_view.group_clicked.connect(self._group_clicked)
            self.cloud_view.crop_drawn.connect(self._crop_drawn)
            self.cloud_view.crop_undo.connect(lambda: self._set_crops(self.crops[:-1], 'Recorte deshecho'))
            self.cloud_view.crop_clear.connect(lambda: self._set_crops([], 'Sin recortes: vuelve la nube entera'))
            self.cloud_view.set_crop_state(0)
        self.relief_view = MplView()
        self.mesh_view = self._make_3d('malla') if HAVE_GL else None
        import orientar
        self.orient_view = orientar.OrientPanel(C, STONE)
        self.orient_view.view.lineDrawn.connect(self.orient_line)
        self.orient_view.detailChanged.connect(self.update_orient)
        self.tabs.addTab(self.cloud_view, 'Nube de puntos')
        self.tabs.addTab(self.orient_view, 'Orientación')
        self.tabs.addTab(self.relief_view, 'Relieve sombreado')
        self.tabs.currentChanged.connect(lambda _: self.update_orient() if getattr(self, '_orient_dirty', False) else None)
        if self.mesh_view is not None:
            self.tabs.addTab(self.mesh_view, 'Malla 3D')
        self.relief_view.clear('Pulsa «Generar» para ver la vista previa')
        self.center.addWidget(self.tabs)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        self.steps = StepBar(self._step_clicked)
        bl.addWidget(self.steps)
        bl.addWidget(self.center, 1)
        self.tabs.currentChanged.connect(lambda *_: self.update_steps())
        return box

    # ---------- barra de pasos
    def update_steps(self, *_):
        if not hasattr(self, 'steps'):
            return
        b, loaded = self.busy(), getattr(self, 'xyz', None) is not None
        seen = self._seen = getattr(self, '_seen', set())
        if loaded:
            if self.tabs.currentWidget() is self.cloud_view:
                seen.add('nube')
            if self.tabs.currentWidget() is self.orient_view:
                seen.add('orientar')
            if getattr(self, '_kind_chosen', False):
                seen.add('tipo')
        r = getattr(self, 'result', None)
        stale = r is not None and getattr(self, 'last_prm', None) is not None and self._params_changed()
        done = set()
        if loaded:
            done |= {'abrir'} | seen
        if r is not None and not stale:
            done |= {'nube', 'tipo', 'orientar', 'previa'}
            if not getattr(self, 'result_preview', False):
                done.add('generar')
                if getattr(self, '_saved_result', None) is r:
                    done.add('guardar')
        final = r is not None and not getattr(self, 'result_preview', False)
        enabled = set() if b else ({'abrir'} | ({'nube', 'tipo', 'orientar', 'previa', 'generar'} if loaded else set())
                                   | ({'guardar'} if final else set()))
        self.steps.set_state(done, enabled, stale)

    def _params_changed(self):
        """¿Algún parámetro distinto de los de la última pieza generada? (sin contar los de la vista previa)"""
        a, b = self.params(), self.last_prm
        return any(getattr(a, f) != getattr(b, f) for f in Params.__dataclass_fields__)

    def _step_clicked(self, key):
        if key in ('nube', 'tipo', 'orientar'):
            self._seen = getattr(self, '_seen', set()) | {key}
        if key == 'abrir':
            self.open_file()
        elif key == 'nube':
            self.tabs.setCurrentWidget(self.cloud_view)
        elif key == 'tipo':
            self.ptabs.setCurrentIndex(0)
            self.say('Elige qué has escaneado (botones de arriba de los parámetros) y el tamaño (pestaña Tamaño)')
        elif key == 'orientar':
            self.ptabs.setCurrentIndex(TAB_ORIENT)
            self.tabs.setCurrentWidget(self.orient_view)
        elif key == 'previa':
            self.run_recon(preview=True)
        elif key == 'generar':
            self.run_recon()
        elif key == 'guardar':
            self.save_mesh()
        self.update_steps()

    def _right_cards(self):
        """Fichero, Resultado y Registro: cada uno va en su panel acoplable (ver _docks)."""
        fcard, flay = self._card()
        self.lbl_file = QLabel('Ningún fichero cargado')
        self.lbl_file.setObjectName('muted')
        self.lbl_file.setWordWrap(True)
        flay.addWidget(self.lbl_file)
        flay.addStretch(1)
        card, lay = self._card()
        self.res_empty = QLabel('Aún no se ha generado ninguna pieza.')
        self.res_empty.setObjectName('muted')
        self.res_empty.setWordWrap(True)
        lay.addWidget(self.res_empty)
        self.res_box = QWidget()
        grid = QGridLayout(self.res_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.tiles, self.tile_caps = {}, {}
        for i, (key, cap) in enumerate([('dims', 'Dimensiones (mm)'), ('state', 'Malla'),
                                        ('tris', 'Triángulos'), ('stl', 'Fichero STL'),
                                        ('layers', 'Relieve'), ('pla', 'PLA (relleno 15 %)')]):
            t = QFrame()
            t.setObjectName('tile')
            tl = QVBoxLayout(t)
            tl.setContentsMargins(9, 6, 9, 6)
            tl.setSpacing(0)
            val = QLabel('—')
            val.setObjectName('tileVal')
            c = QLabel(cap)
            c.setObjectName('tileCap')
            tl.addWidget(val)
            tl.addWidget(c)
            grid.addWidget(t, i // 2, i % 2)
            self.tiles[key] = val
            self.tile_caps[key] = c
        self.res_extra = QLabel('')
        self.res_extra.setObjectName('muted')
        self.res_extra.setWordWrap(True)
        self.res_extra.linkActivated.connect(self._link)
        grid.addWidget(self.res_extra, 3, 0, 1, 2)
        self.res_box.hide()
        lay.addWidget(self.res_box)
        lay.addStretch(1)
        rcard = card

        card, lay = self._card()
        self.log = QPlainTextEdit()
        self.log.setObjectName('log')
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        lay.addWidget(self.log, 1)
        head = QHBoxLayout()
        head.addStretch(1)
        b = QToolButton()
        b.setText('Limpiar')
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip('Vaciar el registro (no cambia nada más)')
        b.clicked.connect(lambda: self.log.clear())
        head.addWidget(b)
        lay.addLayout(head)
        for c in (fcard, rcard, card):
            c.setMinimumWidth(260)
        return fcard, rcard, card

    def _statusbar(self):
        sb = self.statusBar()
        self.st_msg = QLabel('Listo')
        self.st_time = QLabel('')
        self.st_prog = QProgressBar()
        self.st_prog.setRange(0, 0)
        self.st_prog.setTextVisible(False)
        self.st_prog.hide()
        sb.addWidget(self.st_msg, 1)
        sb.addPermanentWidget(self.st_time)
        sb.addPermanentWidget(self.st_prog)
        cpu = QLabel(f'{relief.NCPU} procesadores · {relief.NCORES} núcleos')
        sb.addPermanentWidget(cpu)
        self.st_update = QPushButton()          # «Versión nueva…»: solo si la búsqueda al abrir la encuentra
        self.st_update.setFlat(True)
        self.st_update.setCursor(Qt.PointingHandCursor)
        self.st_update.setStyleSheet(f'color: {C["accent"]}; font-weight: 600; padding: 0 8px;')
        self.st_update.hide()
        sb.addPermanentWidget(self.st_update)
        self.clock = QElapsedTimer()
        self.tick = QTimer(self)
        self.tick.timeout.connect(lambda: self.st_time.setText(f'{self.clock.elapsed() / 1000:.0f} s'))

    # ---------- construcción
    def _menus(self):
        mb = self.menuBar()
        m = mb.addMenu('&Archivo')
        self.act = {}
        for key, text, fn, sc in [('open', 'Abrir nube de puntos…', self.open_file, 'Ctrl+O'),
                                  ('merge', 'Unir otra nube…', self.merge_cloud, None),
                                  ('unmerge', 'Quitar las nubes unidas', lambda: self.unmerge(all_=True), None),
                                  ('savecloud', 'Guardar la nube unida…', self.save_merged, None),
                                  ('reverse', 'Añadir el reverso (pieza de dos caras)…', self.add_reverse, None),
                                  ('compare', 'Comparar con otro escaneo (deterioro)…', self.compare_scan, None),
                                  ('wizard', 'Asistente…', self.open_wizard, 'Ctrl+W'),
                                  ('save', 'Guardar malla…', self.save_mesh, 'Ctrl+S'),
                                  ('pdf', 'Ficha PDF de la pieza…', self.save_pdf, None),
                                  ('profile', 'Guardar el perfil de la vasija (SVG)…', self.save_profile, None),
                                  ('batch', 'Procesar varios ficheros…', self.batch, None),
                                  ('registry', 'Registro de piezas…', self.open_registry, 'Ctrl+R'),
                                  (None, None, None, None),
                                  ('loadp', 'Cargar ajustes…', self.load_params, None),
                                  ('savep', 'Guardar ajustes…', self.save_params, None),
                                  ('reset', 'Restablecer valores por defecto', self.reset_params, None),
                                  (None, None, None, None),
                                  ('quit', 'Salir', self.close, 'Ctrl+Q')]:
            if key is None:
                m.addSeparator()
                continue
            a = QAction(text, self)
            a.triggered.connect(fn)
            if sc:
                a.setShortcut(sc)
            m.addAction(a)
            self.act[key] = a
        self.view_menu = mb.addMenu('&Ver')    # se rellena al crear los paneles (_docks)
        h = mb.addMenu('A&yuda')
        for text, anchor, sc in [('Pasos a seguir: de la nube al STL', 'pasos', 'F1'),
                                 ('Parámetros', 'parametros', None), ('Formatos y archivos', 'salida', None),
                                 ('Qué se pierde en la transformación', 'perdidas', None),
                                 ('Cómo se podría mejorar', 'mejoras', None),
                                 ('Programas similares', 'similares', None), ('Línea de comandos', 'cli', None)]:
            a = QAction(text, self)
            a.triggered.connect(lambda _=False, an=anchor: self.show_help(an))
            if sc:
                a.setShortcut(sc)
            h.addAction(a)
        for text, anchor, pos in [('Ejemplos: cómo cambia la pieza', 'ejemplos', 1),
                                  ('Valores de ejemplo para casos habituales', 'recetas', 2)]:
            a = QAction(text, self)
            a.triggered.connect(lambda _=False, an=anchor: self.show_help(an))
            h.insertAction(h.actions()[pos], a)
        h.addSeparator()
        ex = self.example_menu = h.addMenu('Abrir un ejemplo')
        import ejemplos
        for key, _, title, kind, desc, _ in ejemplos.EXAMPLES:
            a = QAction(f'{title}  ({next(k for k in recetas.KINDS if k["key"] == kind)["short"]})', self)
            a.setToolTip(desc)
            a.triggered.connect(lambda _=False, k=key: self.open_example(k))
            ex.addAction(a)
        ex.setToolTipsVisible(True)
        h.addSeparator()
        a = QAction('Buscar actualizaciones…', self)
        a.triggered.connect(lambda: UpdateDialog(self).exec_())
        h.addAction(a)
        a = QAction('Acerca de nube2stl', self)
        a.triggered.connect(lambda: AboutDialog(self).exec_())
        h.addAction(a)

    def auto_update_check(self):
        """Al abrir, como mucho una vez al día y en segundo plano: si hay versión nueva, un aviso en la barra de
        estado; si no hay red o falla, nada."""
        import actualizar
        import registro
        if os.environ.get('NUBE2STL_NO_UPDATE') or not actualizar.due(registro.load_config()):
            return
        registro.save_config(ultima_busqueda=time.time())
        self._upd = Worker(check_task)
        self._upd.done.connect(self._update_found)
        self._upd.start()

    def _update_found(self, d):
        if not d.get('new'):
            return
        self.st_update.setText(f'Versión nueva: {d["version"]}…')
        self.st_update.setToolTip('Ver las novedades y actualizar')
        try:
            self.st_update.clicked.disconnect()
        except TypeError:
            pass
        self.st_update.clicked.connect(lambda: UpdateDialog(self, d).exec_())
        self.st_update.show()

    def _make_widget(self, kind, opt, val, label):
        return make_widget(kind, opt, val, label)

    def _make_3d(self, kind):
        import visor
        return visor.Viewer3D(C['view'], kind, C)

    # ---------- parámetros
    def params(self):
        p = Params()
        for name, w in self.widgets.items():
            if isinstance(w, QCheckBox):
                setattr(p, name, w.isChecked())
            elif isinstance(w, QLineEdit):
                setattr(p, name, w.text().strip())
            elif isinstance(w, QComboBox):
                setattr(p, name, w.currentText())
            else:
                setattr(p, name, type(getattr(p, name))(w.value()))
        p.keep_groups = [list(c) for c in self.keep_groups]
        p.crop = [dict(c) for c in self.crops]
        p.merge = [dict(m) for m in self.merges]
        p.plane_pts = [list(q) for q in self.plane_pts]
        p.classes = list(self.classes)
        p.reverse = [dict(e) for e in self.reverse]
        return p

    def set_params(self, p):
        old = self.params()
        self._setting = True                  # sin releer ni relimpiar la nube por cada parámetro
        self.keep_groups = [list(c) for c in (p.keep_groups or [])]
        self.crops = [dict(c) for c in (p.crop or [])]
        self.merges = [dict(m) for m in (p.merge or [])]
        self.plane_pts = [list(q) for q in (p.plane_pts or [])]
        self.classes = [int(c) for c in (p.classes or [])]
        self.reverse = [dict(e) for e in (p.reverse or [])]
        if HAVE_GL and hasattr(self, 'cloud_view'):
            self.cloud_view.set_crop_state(len(self.crops))
        try:
            self._set_params(p)
        finally:
            self._setting = False
        self._kind_chosen = True              # ajustes cargados, asistente o registro: el tipo ya está elegido
        self._merge_note()
        self._rev_note()
        if old.units != p.units:                  # otras unidades: todas las medidas cambian (sin releer, la ventana
            # medía la nube con las de antes y la estimación de memoria salía mil veces corta, ej. «auto» = mm y «m»)
            self._reload_cloud(keep_sel=True, why=f'--- Releyendo la nube en {p.units} (unidades de los ajustes)')
        elif old.merge != p.merge or old.classes != p.classes:     # otras nubes o clases: hay que volver a leer
            self._reload_cloud(keep_sel=True, why='--- Releyendo la nube con las nubes unidas de los ajustes')
        elif prep_state(old) != prep_state(p) or relief.plane_key(old) != relief.plane_key(p):
            self._toggle_clean(keep_view=True)    # nube desenrollada o vista de otro lado: medidas y orientación

    def _set_params(self, p):
        for name, w in self.widgets.items():
            v = getattr(p, name)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QLineEdit):
                w.setText(str(v))
            elif isinstance(w, QComboBox):
                w.setCurrentText(str(v))
            else:
                w.setValue(v)
        self._mode_enable()

    def _mode_enable(self, *_):
        """Muestra solo los parámetros que cuentan con el tipo de pieza (método) elegido."""
        mode = self.widgets['mode'].currentText()
        ground = self.widgets['ground'].isChecked()
        off = set(DISABLED.get(mode, ()))
        if mode == 'objeto':                       # el grosor de base solo cuenta con «Abierto por abajo»
            off.add('cut' if ground else 'base')
        kind = recetas.kind_of_params(self._kind_params())
        if kind not in ('grabado', 'curvo'):       # quitar la forma: solo para rocas (y, si hace falta, cilindros)
            off.add('detrend')
        if kind == 'curvo':                        # la cara desenrollada se mira siempre desde fuera
            off.add('view')
        if self.widgets['boost'].value() <= 1:
            off.update(('boost_lo', 'boost_hi', 'boost_size'))
        if mode != 'bloque':
            off.add('corner')
        elif self.widgets['corner'].isChecked():
            off.add('behind')                      # la esquina llega hasta donde llega la otra cara
        if kind != 'interior':
            off.update(('interior', 'ceiling'))
        elif self.widgets['interior'].currentText() != 'corte':
            off.add('ceiling')
        if kind == 'curvo' or self.widgets['view'].currentText() != 'auto':
            off.add('plane')                       # el plano solo cuenta mirando perpendicular a la cara
        self._off = off
        for name, ws in getattr(self, 'param_rows', {}).items():
            for w in ws:
                w.setVisible(name not in off)
        for head, names in getattr(self, 'sections', []):
            head.setVisible(any(n not in off for n in names))
        for e, names in getattr(self, 'tab_empty', {}).values():
            e.setVisible(all(n in off for n in names))
        self.widgets['target_len'].setEnabled(self.widgets['ratio'].value() <= 0)
        self.widgets['drain'].setEnabled(self.widgets['wall'].value() > 0)
        self._sync_kind()
        if hasattr(self, 'tile_caps'):
            self.tile_caps['layers'].setText('Altura' if mode == 'objeto' else 'Relieve')
        if hasattr(self, 'orient_btns'):
            self._orient_enable()
        self._adv_note()

    # ---------- valores de Avanzado cambiados (cuentan, pero no se ven si no se abre la pestaña)
    ADV_GROUPS = ('Malla', 'Limpieza y suavizado')     # «Impresora» no: es la de cada uno, se resume arriba

    def _adv_changed(self):
        if not hasattr(self, 'widgets'):
            return []
        p, d = self.params(), Params()
        off = getattr(self, '_off', set())
        return [row for g, rows in SPEC if g in self.ADV_GROUPS for row in rows
                if row[0] not in off and getattr(p, row[0]) != getattr(d, row[0])]

    def _adv_note(self):
        if not hasattr(self, 'adv_lbl'):
            return
        rows = self._adv_changed()
        if not rows:
            self.adv_lbl.hide()
            return
        p = self.params()

        def val(row):
            name, _, _, opt, _ = row
            v = getattr(p, name)
            if isinstance(v, bool):
                return 'sí' if v else 'no'
            if isinstance(v, (int, float)):
                if v == 0 and opt.get('special'):
                    return opt['special']
                return f'{v:g}'.replace('.', ',') + (' ' + opt['suf'] if opt.get('suf') else '')
            return str(v)
        a = f'style="color:{C["teal"]}"'
        items = ', '.join(f'{row[1]} ({val(row)})' for row in rows[:4]) + (' …' if len(rows) > 4 else '')
        self.adv_lbl.setText(f'⚙ <b>Avanzado</b> con valores cambiados: {items} · <a href="ver" {a}>ver</a> · '
                             f'<a href="reset" {a}>dejarlos por defecto</a>')
        self.adv_lbl.show()

    def _adv_link(self, what):
        if what == 'ver':
            self.ptabs.setCurrentIndex(len(PARAM_TABS) - 1)
            return
        rows = self._adv_changed()
        d = Params()
        p = self.params()
        for row in rows:
            setattr(p, row[0], getattr(d, row[0]))
        self.set_params(p)
        self.say('Avanzado: por defecto ' + ', '.join(row[1] for row in rows))
        self._adv_note()

    # ---------- tipo de pieza (selector de arriba: pone el método y «Abierto por abajo»)
    def _kind_params(self):
        """Lo que decide el tipo de pieza (método, abierto por abajo, superficie, quitar forma), de los campos."""
        w = self.widgets
        return relief.Params(mode=w['mode'].currentText(), ground=w['ground'].isChecked(),
                             surface=w['surface'].currentText(), detrend=w['detrend'].value(),
                             interior=w['interior'].currentText())

    def _kind_selector(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 4)
        v.setSpacing(4)
        t = QLabel('<b>¿Qué has escaneado?</b>')
        head = QHBoxLayout()
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.btn_settings)
        v.addLayout(head)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.kind_btns = {}
        grp = QButtonGroup(box)
        for i, it in enumerate(recetas.KINDS):
            b = QPushButton(it['short'])
            b.setObjectName('kind')
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f'<b>{it["title"]}</b><br>{it["desc"]}')
            b.clicked.connect(lambda _=False, k=it['key']: self._kind_clicked(k))
            grp.addButton(b)
            grid.addWidget(b, i // 4, i % 4)
            self.kind_btns[it['key']] = b
        v.addLayout(grid)
        self.curve_row = QWidget()                  # tipo Curvo: qué superficie
        cr = QHBoxLayout(self.curve_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(QLabel('Superficie'))
        self.curve_combo = QComboBox()
        for txt, key in (('cilindro (miliario, columna)', 'cilindro'), ('cono (pieza troncocónica)', 'cono'),
                         ('revolución, con su perfil (vasija abombada)', 'revolucion')):
            self.curve_combo.addItem(txt, key)
        self.curve_combo.setToolTip('Sobre qué superficie está la decoración. El cono se desarrolla sin deformar; la '
                                    'superficie de revolución se desenrolla con su perfil (cada altura con su '
                                    'circunferencia).')
        self.curve_combo.currentIndexChanged.connect(
            lambda i: self.widgets['surface'].setCurrentText(self.curve_combo.itemData(i)))
        cr.addWidget(self.curve_combo, 1)
        self.curve_row.hide()
        v.addWidget(self.curve_row)
        self.kind_desc = QLabel('')
        self.kind_desc.setObjectName('muted')
        self.kind_desc.setWordWrap(True)
        self.kind_desc.setTextFormat(Qt.RichText)
        v.addWidget(self.kind_desc)
        self.kind_guess = QLabel('')                # lo que parece la nube al abrirla
        self.kind_guess.setObjectName('plan')
        self.kind_guess.setWordWrap(True)
        self.kind_guess.setTextFormat(Qt.RichText)
        self.kind_guess.linkActivated.connect(lambda k: self._kind_clicked(k))
        self.kind_guess.hide()
        v.addWidget(self.kind_guess)
        self.chk_under = QCheckBox('Tiene partes que vuelan (socavados)')
        self.chk_under.setToolTip('Altorrelieve con figuras despegadas del fondo: usa el método 3D (Poisson), que '
                                  'conserva los huecos\npor debajo, pero tarda minutos y necesita más memoria. Sin '
                                  'marcar: mapa de alturas (segundos).')
        self.chk_under.toggled.connect(self._under_toggled)
        v.addWidget(self.chk_under)
        return box

    def _kind_clicked(self, key, auto=False):
        """Botón de tipo de pieza (o enlace de la propuesta): pone el método, «Abierto por abajo», la superficie
        y, en los grabados, cuánta forma de la roca se quita (según el tamaño de la nube)."""
        it = next(k for k in recetas.KINDS if k['key'] == key)
        prm = dict(it['params'])
        for name in it.get('auto', []):
            prm[name] = recetas.auto_value(name, getattr(self, 'stats', None))
        if key in ('relieve', 'curvo') and self.chk_under.isChecked():
            prm['mode'] = '3d'
        if not auto:
            self._kind_chosen = True
        w = self.widgets
        w['ground'].setChecked(prm['ground'])
        w['detrend'].setValue(prm['detrend'])
        if not (key == 'interior' and w['interior'].currentText() != 'no'):
            w['interior'].setCurrentText(prm['interior'])     # en Interior se queda corte o negativo
        w['mode'].setCurrentText(prm['mode'])
        if not (key == 'curvo' and w['surface'].currentText() in ('cilindro', 'cono', 'revolucion')):
            w['surface'].setCurrentText(prm['surface'])     # en Curvo se queda la forma ya elegida
        self._mode_enable()
        self._show_guess()
        self.say(f'Tipo de pieza: {it["title"].lower()} (método {METHOD_NAME[prm["mode"]]})'
                 + (' — propuesto por la forma de la nube' if auto else ''))

    def _prep_later(self, *_):
        """Superficie o método cambiados (quizá varios a la vez, desde el selector): preparar otra vez la nube si
        cambia cómo se desenrolla o si pasa a ser una vasija."""
        if not getattr(self, '_setting', False):
            self.prep_timer.start()

    def _check_prep(self):
        li = getattr(self, 'load_info', None)
        if li is None or getattr(self, 'xyz_all', None) is None:
            return
        if self.busy():
            self.prep_timer.start(300)
            return
        if li.get('prep') != prep_state(self.params()):
            self._toggle_clean(keep_view=True)

    def _under_toggled(self, on):
        if self.widgets['mode'].currentText() in ('alturas', '3d'):
            self.widgets['mode'].setCurrentText('3d' if on else 'alturas')

    def _sync_kind(self):
        """El selector refleja el método, «Abierto por abajo», la superficie y quitar la forma (que también ponen
        el asistente y los ajustes)."""
        if not hasattr(self, 'kind_btns'):
            return
        mode = self.widgets['mode'].currentText()
        key = recetas.kind_of_params(self._kind_params())
        self.kind_btns[key].setChecked(True)
        it = next(k for k in recetas.KINDS if k['key'] == key)
        self._kind_line = it['line'] + (f' <i>{it["hint"]}</i>' if it.get('hint') else '')
        self.kind_desc.setText(self._kind_line)
        self.kind_desc.setToolTip(it['desc'])
        self.chk_under.setVisible(key in ('relieve', 'curvo'))
        if hasattr(self, 'curve_row'):
            self.curve_row.setVisible(key == 'curvo')
            i = self.curve_combo.findData(self.widgets['surface'].currentText())
            if i >= 0 and i != self.curve_combo.currentIndex():
                self.curve_combo.blockSignals(True)
                self.curve_combo.setCurrentIndex(i)
                self.curve_combo.blockSignals(False)
        self.chk_under.blockSignals(True)
        self.chk_under.setChecked(mode == '3d')
        self.chk_under.blockSignals(False)
        self._show_guess()

    def _show_guess(self):
        """Propuesta del tipo según la forma de la nube: si coincide con el elegido, el motivo; si no, un enlace
        para usarla."""
        g = (getattr(self, 'load_info', None) or {}).get('guess') if getattr(self, 'xyz', None) is not None else None
        if not hasattr(self, 'kind_guess') or not g:
            if hasattr(self, 'kind_guess'):
                self.kind_guess.hide()
                self.kind_desc.show()
            return
        key, why = g
        it = next(k for k in recetas.KINDS if k['key'] == key)
        now = recetas.kind_of_params(self._kind_params())
        # un solo texto bajo los botones (el sitio es para los parámetros): si coincide, la descripción con una
        # marca y el motivo en la burbuja; si no, solo la propuesta, que es lo que hay que leer
        if now == key:
            self.kind_guess.hide()
            self.kind_desc.setText(f'<span style="color:{C["ok"]}"><b>✔</b></span> {getattr(self, "_kind_line", "")}')
            self.kind_desc.setToolTip(f'<b>{it["short"]}</b>, por la forma de la nube: {why}.<br>'
                                      f'Si no es así, elige otro botón.<br><br>{it["desc"]}')
            self.kind_desc.show()
            return
        self.kind_guess.setText(f'La nube parece <b>{it["short"].lower()}</b>: {why}. '
                                f'<a href="{key}" style="color:{C["teal"]}"><b>Usar «{it["short"]}»</b></a>')
        self.kind_desc.hide()
        self.kind_guess.show()

    def _apply_guess(self, tries=20):
        """Al abrir una nube, si el tipo no lo ha elegido nadie, poner el que parece (cuando acabe la carga)."""
        g = (getattr(self, 'load_info', None) or {}).get('guess')
        if not g or getattr(self, '_kind_chosen', False):
            return
        if self.busy():
            if tries:
                QTimer.singleShot(100, lambda: self._apply_guess(tries - 1))
            return
        if recetas.kind_of_params(self._kind_params()) != g[0]:
            self._kind_clicked(g[0], auto=True)
        gs = self.load_info.get('guess_surface')
        if gs and g[0] == 'curvo' and self.widgets['surface'].currentText() != gs:
            self.widgets['surface'].setCurrentText(gs)
        if g[0] == 'bloque' and self.load_info.get('guess_corner') and not self.widgets['corner'].isChecked():
            self.widgets['corner'].setChecked(True)

    def load_params(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Cargar ajustes', os.getcwd(), 'Ajustes (*.json)')
        if path:
            self.load_params_file(path)

    def load_params_file(self, path):
        """Ajustes guardados. Si traen la nube con la que se guardaron (y no es la abierta), se abre esa nube ya
        con todo: las uniones (con su encaje, sin volver a alinear), recortes, grupos, reverso y parámetros."""
        if self.busy():
            return
        try:
            text = open(path, encoding='utf-8').read()
            p = Params.from_json(text)
            cloud = json.loads(text).get('_cloud')
        except Exception as e:
            self.on_fail(f'No se pudieron cargar los ajustes: {e}')
            return
        if cloud and not os.path.isabs(cloud):
            cloud = os.path.join(os.path.dirname(os.path.abspath(path)), cloud)
        cur = getattr(self, 'path', None)
        if cloud and os.path.exists(cloud) and (not cur or os.path.abspath(cur) != os.path.abspath(cloud)):
            self.job = None
            self.xyz = self.xyz_all = None            # la nube abierta ya no cuenta: no se relee al poner los ajustes
            self.set_params(p)
            self.say(f'Ajustes cargados de {path}: se abre su nube, {os.path.basename(cloud)}'
                     + (f', con {len(p.merge)} nube{"s" if len(p.merge) > 1 else ""} unida{"s" if len(p.merge) > 1 else ""} '
                        f'ya encajada{"s" if len(p.merge) > 1 else ""}' if p.merge else ''))
            self.start(load_task, (cloud, self.params()), self.on_loaded)
            return
        if cloud and not os.path.exists(cloud):
            self.say(f'AVISO: no se encuentra la nube de estos ajustes ({cloud}): se aplican a la abierta')
        self.set_params(p)
        self.say(f'Ajustes cargados de {path}')

    def save_params(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Guardar ajustes', 'ajustes.json', 'Ajustes (*.json)')
        if path:
            d = json.loads(self.params().to_json())
            if getattr(self, 'path', None):
                d['_cloud'] = os.path.abspath(self.path)     # para abrirla con ellos (Cargar ajustes)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(json.dumps(d, indent=2, ensure_ascii=False))
            self.say(f'Ajustes guardados en {path}' + (' (con la nube y las uniones: «Cargar ajustes» lo abre todo)'
                                                       if getattr(self, 'path', None) else ''))

    def save_merged(self):
        """La nube principal con las unidas, ya encajadas, en un solo PLY (mm, con color y normales si las hay): se
        abre como cualquier nube, sin volver a unir. Sin limpiar ni recortar (eso queda en los ajustes)."""
        if self.busy() or not self.merges or getattr(self, 'path', None) is None:
            return
        base = os.path.splitext(os.path.basename(self.path))[0]
        out, _ = QFileDialog.getSaveFileName(self, 'Guardar la nube unida',
                                             os.path.join(os.path.dirname(self.path), base + '_unida.ply'),
                                             'PLY (*.ply)')
        if not out:
            return
        if not out.lower().endswith('.ply'):
            out += '.ply'
        self.say('--- Guardando la nube unida')
        self.start(save_merged_task, (self.path, self.params(), out), self._merged_saved)

    def _merged_saved(self, out):
        path, n = out
        self.say(f'Nube unida guardada: {path} ({n:,} puntos, en mm). Ábrela la próxima vez: no hace falta volver a '
                 f'unir. Los recortes, los grupos conservados y los demás ajustes van aparte (Guardar ajustes).')

    def reset_params(self):
        self.set_params(Params())
        self._kind_chosen = False
        self.say('Parámetros restablecidos')

    # ---------- utilidades
    def show_help(self, anchor=None):
        if self.help is None:
            self.help = HelpDialog(self)
        self.help.show_at(anchor)

    def say(self, s):
        self.log.appendPlainText(s)
        if s.strip():
            self.st_msg.setText(s.strip()[:140])

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def update_buttons(self):
        b = self.busy()
        loaded = self.xyz is not None
        self.btn_open.setEnabled(not b)
        self.btn_run.setEnabled(not b and loaded)
        self.btn_prev.setEnabled(not b and loaded)
        self.btn_cancel.setEnabled(b and self.job is not None)
        final = self.result is not None and not getattr(self, 'result_preview', False)
        self.btn_save.setEnabled(not b and final)
        self.act['open'].setEnabled(not b)
        self.act['save'].setEnabled(not b and final)
        if 'pdf' in self.act:
            self.act['pdf'].setEnabled(not b and final)
        self.act['batch'].setEnabled(not b)
        self.act['merge'].setEnabled(not b and loaded)
        self.act['reverse'].setEnabled(not b and loaded)
        self.act['compare'].setEnabled(not b and loaded and HAVE_GL)
        self.act['profile'].setEnabled(not b and loaded and bool((self.load_info or {}).get('vessel')))
        self.act['unmerge'].setEnabled(not b and loaded and bool(self.merges))
        self.act['savecloud'].setEnabled(not b and loaded and bool(self.merges))
        self.update_steps()

    def start(self, fn, args, on_done):
        self.worker = Worker(fn, *args)
        self.worker.log.connect(self.say)
        self.worker.done.connect(on_done)
        self.worker.failed.connect(self.on_fail)
        self.worker.finished.connect(self.update_buttons)
        self.worker.finished.connect(QApplication.restoreOverrideCursor)
        self.worker.finished.connect(self._job_finished)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self.st_prog.show()
        self.clock.start()
        self.tick.start(500)
        self.st_time.setText('0 s')
        self.worker.start()
        self.update_buttons()

    def _job_finished(self):
        self.tick.stop()
        self.st_prog.hide()
        self.st_time.setText(f'{self.clock.elapsed() / 1000:.1f} s')

    def closeEvent(self, ev):
        if hasattr(self, 'docks'):
            self.save_view()
        if self.job is not None:          # no dejar un proceso de reconstrucción huérfano
            self.job.cancel()
        if self.worker is not None:
            self.worker.wait(5000)
        super().closeEvent(ev)

    def cancel_job(self):
        if self.job is not None:
            self.say('Cancelando…')
            self.job.cancel()

    def on_fail(self, msg):
        if msg.startswith('Cancelado'):
            self.say(msg)
            return
        self.say('ERROR: ' + msg)
        if OOM_KEY in msg and getattr(self, 'stats', None) is not None:
            # corte por falta de memoria: la misma ventana, con los parámetros que bajan el consumo
            if self.check_memory(self.params(), failed=True):
                QTimer.singleShot(0, self.run_recon)
            return
        QMessageBox.critical(self, 'Error', msg)

    # ---------- acciones
    def open_file(self):
        import entrada
        path, _ = QFileDialog.getOpenFileName(self, 'Abrir nube de puntos', os.getcwd(), entrada.OPEN_FILTER)
        if path:
            self.job = None
            self.keep_groups = []                 # los grupos elegidos y los recortes eran de la nube anterior
            self._set_crops_quiet([])
            self.merges = []
            self._merge_note()
            self._plane_reset()
            self.classes = []
            self.reverse = []
            self._rev_note()
            self.start(load_task, (path, self.params()), self.on_loaded)

    # ---------- comparar dos escaneos (deterioro)
    def compare_scan(self):
        """El escaneo abierto es el antiguo; se elige el nuevo, se alinea (ventana de unir) y se mide la diferencia."""
        import entrada
        import ventana_unir
        if self.busy() or getattr(self, 'xyz_all', None) is None:
            self.say('Abre primero el escaneo antiguo; después compáralo con el nuevo.')
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Escaneo nuevo de la misma pieza', os.path.dirname(self.path),
                                              entrada.OPEN_FILTER)
        if not path:
            return
        try:
            dlg = ventana_unir.MergeDialog(self, path, C, title=f'Alinear el escaneo nuevo: {os.path.basename(path)}',
                                           ok_text='Comparar')
        except Exception as e:
            traceback.print_exc()
            self.on_fail(f'No se pudo leer {os.path.basename(path)}: {e}')
            return
        if not (dlg.exec_() and dlg.result_entry):
            return
        e = dlg.result_entry
        A, prm = self.xyz, self.params()

        def task(log):
            import comparar
            import unir
            B, *_ = unir.read_extra(e['path'], e['units'], self.load_info, log)
            Bm, d, s, M = comparar.compare(A, B, e['M'], comparar.outward_of(A, prm), log=log)
            return Bm, d, s
        self.say(f'--- Comparando {os.path.basename(self.path)} (antes) con {os.path.basename(path)} (después)')
        self.start(task, (), lambda out: self._compared(out, path))

    def _compared(self, out, path):
        import comparar
        import visor
        Bm, d, s = out
        dlg = QDialog(self)
        dlg.setWindowTitle(f'Deterioro: {os.path.basename(self.path)} → {os.path.basename(path)}')
        dlg.resize(1150, 780)
        v = QVBoxLayout(dlg)
        view = visor.Viewer3D(C['view'], 'nube', C)
        view.crop_btn.hide()
        for w in view.crop_tools:
            w.hide()
        v.addWidget(view, 1)
        lim = max(abs(s.get('p1', -1)), abs(s.get('p99', 1)), 2 * s.get('thr', 0.5)) if s else 1.0
        k = max(1, len(Bm) // PREVIEW_PTS)
        col = comparar.colors(d[::k], lim) / 255.0
        P = Bm[::k]
        view.show_cloud((P - P.mean(0)).astype(np.float32), col, None, True)
        lab = QLabel(f'<b>Azul</b>: material perdido · <b>rojo</b>: material de más · <b>gris</b>: sin escaneo antiguo · '
                     f'escala ±{lim:.2g} mm<br>' + comparar.summary_text(s))
        lab.setWordWrap(True)
        v.addWidget(lab)
        row = QHBoxLayout()
        base = os.path.splitext(path)[0]

        def save_pdf():
            p, _ = QFileDialog.getSaveFileName(dlg, 'Informe de la comparación', base + '_comparacion.pdf', 'PDF (*.pdf)')
            if p:
                comparar.report_pdf(p, Bm, d, s, (os.path.basename(self.path), os.path.basename(path)))
                self.say(f'Informe de la comparación: {p}')

        def save_ply():
            import ejemplos
            p, _ = QFileDialog.getSaveFileName(dlg, 'Nube coloreada por la diferencia', base + '_diferencia.ply',
                                               'PLY (*.ply)')
            if p:
                ejemplos.write_ply(p, Bm, comparar.colors(d, lim))
                self.say(f'Nube coloreada: {p}')
        for text, fn in (('Guardar informe PDF…', save_pdf), ('Guardar nube coloreada (PLY)…', save_ply)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton('Cerrar')
        close.clicked.connect(dlg.accept)
        row.addWidget(close)
        v.addLayout(row)
        dlg.exec_()

    # ---------- pieza de dos caras: el reverso
    def add_reverse(self):
        import entrada
        if getattr(self, 'xyz_all', None) is None:
            self.say('Abre primero la cara principal (el anverso).')
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Reverso de la pieza (la otra cara)', os.path.dirname(self.path),
                                              entrada.OPEN_FILTER)
        if not path:
            return
        if os.path.abspath(path) == os.path.abspath(self.path):
            QMessageBox.warning(self, 'Reverso', 'El reverso tiene que ser otro fichero: el escaneo de la otra cara.')
            return
        self.reverse = [dict(path=path, units=self.params().units, angle=None, flip=None)]
        if self.widgets['mode'].currentText() != 'alturas':
            self._kind_clicked('relieve')
        self._rev_note()
        self.say(f'Reverso: {os.path.basename(path)}. Al generar se pone debajo de la cara, girado para que los '
                 f'contornos coincidan; «Grosor base» es el alma entre las dos caras.')
        self.update_steps()

    def _rev_note(self):
        if not hasattr(self, 'rev_lbl'):
            return
        if not self.reverse:
            self.rev_lbl.hide()
            return
        e = self.reverse[0]
        a = f'style="color:{C["teal"]}"'
        ang = 'encajado por el contorno' if e.get('angle') is None else f'girado {e["angle"]:g}°'
        side = {None: '', True: ' · dado la vuelta', False: ' · sin dar la vuelta'}[e.get('flip')]
        self.rev_lbl.setText(f'<b>Dos caras</b>: reverso {os.path.basename(e["path"])} ({ang}{side}) · '
                             f'<a href="angle" {a}>giro…</a> · <a href="flip" {a}>darle la vuelta</a> · '
                             f'<a href="quit" {a}>quitar</a>')
        self.rev_lbl.show()

    def _rev_link(self, what):
        if not self.reverse:
            return
        e = self.reverse[0]
        if what == 'quit':
            self.reverse = []
            self.say('Reverso quitado: la pieza vuelve a tener una sola cara')
        elif what == 'flip':                  # el reverso sale hundido: mirarlo desde el otro lado
            e['flip'] = not bool(e.get('flip')) if e.get('flip') is not None else False
            self.say('Reverso: se mira desde el otro lado al generar')
        elif what == 'angle':
            v, ok = QInputDialog.getDouble(self, 'Giro del reverso', 'Giro del reverso respecto a la cara (grados, '
                                           'antihorario).\nDeja −1 para encajarlo solo por el contorno:',
                                           -1.0 if e.get('angle') is None else e['angle'], -1, 360, 1)
            if not ok:
                return
            e['angle'] = None if v < 0 else v
        self._rev_note()
        self.update_steps()

    # ---------- vasija a partir de un fragmento: perfil para el dibujo
    def save_profile(self):
        import revolucion
        v = (getattr(self, 'load_info', None) or {}).get('vessel')
        if not v:
            QMessageBox.information(self, 'Perfil de la vasija', 'Elige el tipo «Fragmento» para reconstruir la '
                                    'vasija a partir del fragmento; después podrás guardar su perfil.')
            return
        base = os.path.splitext(self.path)[0] + '_perfil.svg'
        path, _ = QFileDialog.getSaveFileName(self, 'Guardar el perfil de la vasija', base, 'SVG (*.svg)')
        if not path:
            return
        if not path.lower().endswith('.svg'):
            path += '.svg'
        sec = dict(v['section'])
        if self.params().up == '-z':                 # la vasija se imprime al revés: el dibujo también
            sec['mask'] = sec['mask'][::-1]
        revolucion.profile_svg(path, sec, os.path.basename(self.path))
        self.say(f'Perfil guardado (a tamaño real, en mm): {path}')

    # ---------- LiDAR: clases
    LAS_PRESETS = [('todas', 'todas', []), ('suelo', 'solo suelo', [2]), ('sinveg', 'suelo y edificios', [2, 6, 9, 17])]

    def _las_note(self):
        import entrada
        li = getattr(self, 'load_info', None) or {}
        if not hasattr(self, 'las_lbl') or not li.get('las'):
            if hasattr(self, 'las_lbl'):
                self.las_lbl.hide()
            return
        a = f'style="color:{C["teal"]}"'
        counts = li.get('classes')
        cur = ', '.join(entrada.class_name(c) for c in self.classes) if self.classes else 'todas menos el ruido'
        if counts:
            have = ' · '.join(f'{entrada.class_name(c)} {n / 1e6:.2g} M' if n >= 1e5 else f'{entrada.class_name(c)} {n:,}'
                              for c, n in list(counts.items())[:6])
            links = ' · '.join(f'<a href="{k}" {a}>{t}</a>' for k, t, v in self.LAS_PRESETS
                               if v != self.classes and (not v or any(c in counts for c in v)))
            self.las_lbl.setText(f'<b>LiDAR</b> con clases: {have}.<br>Se usan: <b>{cur}</b> · {links} · '
                                 f'<a href="elegir" {a}>elegir…</a>')
        else:
            on = entrada.GROUND_AUTO in self.classes
            self.las_lbl.setText('<b>LiDAR</b> sin clasificar. ' + (
                f'Se usa el <b>suelo calculado</b> (sin árboles ni edificios) · <a href="todas" {a}>usar todos los '
                f'puntos</a>' if on else
                f'<a href="calc" {a}>Quedarse solo con el suelo</a> (lo calcula: quita árboles y edificios para el '
                f'terreno desnudo)'))
        self.las_lbl.show()

    def _las_link(self, what):
        import entrada
        if what == 'calc':
            new = [entrada.GROUND_AUTO]
        elif what == 'elegir':
            counts = self.load_info.get('classes') or {}
            dlg = QDialog(self)
            dlg.setWindowTitle('Clases del LiDAR')
            v = QVBoxLayout(dlg)
            v.addWidget(QLabel('Puntos que se usan para la pieza:'))
            boxes = {}
            for c, n in counts.items():
                b = QCheckBox(f'{entrada.class_name(c)} (clase {c}): {n:,} puntos')
                b.setChecked(c in self.classes if self.classes else c not in entrada.NOISE)
                v.addWidget(b)
                boxes[c] = b
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            v.addWidget(bb)
            if not dlg.exec_():
                return
            new = [c for c, b in boxes.items() if b.isChecked()]
            if not new:
                QMessageBox.warning(self, 'Clases del LiDAR', 'Elige al menos una clase.')
                return
            if sorted(new) == sorted(c for c in counts if c not in entrada.NOISE):
                new = []
        else:
            new = next(v for k, t, v in self.LAS_PRESETS if k == what)
        if new == self.classes or self.busy():
            return
        self.classes = list(new)
        self._reload_cloud(keep_sel=True, why='--- Releyendo la nube con las clases elegidas: ' + (
            ', '.join(entrada.class_name(c) for c in new) if new else 'todas menos el ruido'))

    # ---------- plano de la cara por tres puntos
    def _plane_reset(self):
        """Nube nueva o con otras unidades: los puntos marcados ya no valen."""
        self.plane_pts = []
        self._plane_pick = None
        if hasattr(self, 'widgets') and self.widgets['plane'].currentText() == 'puntos':
            self._setting = True
            try:
                self.widgets['plane'].setCurrentText('nube')
            finally:
                self._setting = False
        self._plane_note()

    def _plane_note(self):
        if not hasattr(self, 'plane_lbl'):
            return
        a = f'style="color:{C["teal"]}"'
        if self._plane_pick is not None:
            n = len(self._plane_pick)
            self.plane_lbl.setText(f'<b>Marcando el plano:</b> clic en el punto {n + 1} de 3 sobre la cara, en la '
                                   f'vista de la nube (puntos separados, no en línea) · <a href="cancel" {a}>cancelar</a>')
        elif self.plane_pts:
            self.plane_lbl.setText(f'Plano por 3 puntos marcados · <a href="pick" {a}>marcar otros</a> · '
                                   f'<a href="clear" {a}>quitar</a>')
        else:
            self.plane_lbl.setText(f'<a href="pick" {a}>Marcar 3 puntos de la cara…</a> (si laterales, roturas o '
                                   f'peana tuercen el plano)')
        ang = (getattr(self, 'load_info', None) or {}).get('guess_corner')
        if ang and self._plane_pick is None and not self.plane_pts and self.widgets['plane'].currentText() == 'nube':
            self.plane_lbl.setText(self.plane_lbl.text() + (
                f'<br><span style="color:{C["accent_dk"]}">⚠ La nube tiene <b>dos caras grandes a {ang:.0f}°</b>: con '
                f'«nube» se mira desde la esquina, entre las dos. Elige «cara», marca 3 puntos de la cara que '
                f'quieres o, para las dos, el tipo Bloque con «Esquina (dos caras)».</span>'))

    def _plane_changed(self, *_):
        if getattr(self, '_setting', False):
            return
        if self.widgets['plane'].currentText() == 'puntos' and len(self.plane_pts) < 3:
            self._plane_link('pick')
            return
        self._plane_note()
        self._toggle_clean(keep_view=True)

    def _plane_link(self, what):
        if what == 'pick':
            if getattr(self, 'xyz_all', None) is None or not HAVE_GL:
                self.say('Abre primero una nube para marcar el plano en ella.')
                return
            self._plane_pick = []
            self.tabs.setCurrentWidget(self.cloud_view)
            self.cloud_view.pick_cb = self._plane_click
            self.cloud_view.view.setCursor(Qt.CrossCursor)
            self.say('Plano de la cara: haz clic en tres puntos de la cara en la vista de la nube.')
        elif what == 'cancel':
            self._plane_stop()
            if self.widgets['plane'].currentText() == 'puntos' and len(self.plane_pts) < 3:
                self.widgets['plane'].setCurrentText('nube')
        elif what == 'clear':
            self.plane_pts = []
            self.widgets['plane'].setCurrentText('nube')       # relee las medidas (_plane_changed)
            self._plane_note()
        self._plane_note()

    def _plane_stop(self):
        self._plane_pick = None
        if HAVE_GL:
            self.cloud_view.pick_cb = None
            self.cloud_view.view.unsetCursor()
            self.cloud_view.redraw_extra()
        self._plane_note()

    def _plane_click(self, p):
        if p is None:
            self.say('Ahí no hay ningún punto: haz clic sobre la cara.')
            return
        q = (np.asarray(p, np.float64) + np.asarray(self._cloud_center, np.float64)).tolist()
        self._plane_pick.append(q)
        self.cloud_view.redraw_extra()
        self._plane_note()
        if len(self._plane_pick) < 3:
            return
        pts = self._plane_pick
        self._plane_stop()
        if relief._plane_from_points(pts) is None:
            self.say('Los tres puntos están casi en línea: marca otros más separados.')
            return
        self.plane_pts = pts
        self.say('Plano de la cara por los tres puntos marcados')
        if self.widgets['plane'].currentText() != 'puntos':
            self.widgets['plane'].setCurrentText('puntos')         # relee las medidas (_plane_changed)
        else:
            self._toggle_clean(keep_view=True)
        self._plane_note()

    def _plane_markers(self):
        """Puntos del plano en la vista de la nube (los que se están marcando, o los guardados)."""
        import pyqtgraph.opengl as gl
        import visor
        pts = self._plane_pick if self._plane_pick is not None else (
            self.plane_pts if self.widgets['plane'].currentText() == 'puntos' else [])
        c0 = getattr(self, '_cloud_center', None)
        if not pts or c0 is None:
            return []
        P = (np.array(pts, np.float64) - c0).astype(np.float32)
        s = gl.GLScatterPlotItem(pos=P, size=13, color=(0.85, 0.2, 0.55, 1.0), pxMode=True)
        s.setGLOptions(visor.MARKER_GL)
        s.setDepthValue(30)
        items = [s]
        if len(P) == 3:
            li = gl.GLLinePlotItem(pos=np.r_[P, P[:1]], mode='line_strip', color=(0.85, 0.2, 0.55, 1.0), width=2,
                                   antialias=True)
            li.setGLOptions(visor.MARKER_GL)
            items.append(li)
        return items

    # ---------- unir otras nubes
    def _merge_note(self):
        if not hasattr(self, 'merge_lbl'):
            return
        a = f'style="color:{C["teal"]}"'
        if not self.merges:
            self.merge_lbl.setText(f'Una sola nube · <a href="add" {a}>Unir otra nube…</a> (otra tanda del escaneo, '
                                   f'la base escaneada aparte…)')
            return
        names = ', '.join(os.path.basename(m['path']) for m in self.merges)
        self.merge_lbl.setText(f'<b>Unidas:</b> {names} · <a href="add" {a}>unir otra</a> · '
                               f'<a href="last" {a}>quitar la última</a>'
                               + (f' · <a href="all" {a}>quitar todas</a>' if len(self.merges) > 1 else ''))

    def _merge_link(self, what):
        if what == 'add':
            self.merge_cloud()
        else:
            self.unmerge(all_=what == 'all')

    def merge_cloud(self):
        """Archivo › Unir otra nube: elegir el fichero, encajarlo en su ventana y volver a leer todo junto."""
        if self.busy() or getattr(self, 'xyz_all', None) is None:
            self.say('Abre primero la nube principal; después podrás unirle otras.')
            return
        import entrada
        import ventana_unir
        path, _ = QFileDialog.getOpenFileName(self, 'Unir otra nube', os.path.dirname(self.path), entrada.OPEN_FILTER)
        if not path:
            return
        if os.path.abspath(path) in [os.path.abspath(self.path)] + [os.path.abspath(m['path']) for m in self.merges]:
            QMessageBox.warning(self, 'Unir otra nube', 'Esa nube ya forma parte del modelo.')
            return
        try:
            dlg = ventana_unir.MergeDialog(self, path, C)
        except Exception as e:
            traceback.print_exc()
            self.on_fail(f'No se pudo leer {os.path.basename(path)}: {e}')
            return
        if dlg.exec_() and dlg.result_entry:
            import re
            self.merges.append(dlg.result_entry)
            self._merge_note()
            self.say(f'Nube unida: {os.path.basename(path)}. ' + re.sub('<[^>]+>', '', dlg.result_text))
            self._reload_cloud(keep_sel=True, why='--- Releyendo la nube con la nube unida')

    def unmerge(self, all_=False):
        if not self.merges or self.busy():
            return
        gone = self.merges if all_ else self.merges[-1:]
        self.merges = [] if all_ else self.merges[:-1]
        self._merge_note()
        self.say('Se quita' + ('n' if len(gone) > 1 else '') + ' la' + ('s' if len(gone) > 1 else '') +
                 ' nube' + ('s' if len(gone) > 1 else '') + ' unida' + ('s' if len(gone) > 1 else '') + ': ' +
                 ', '.join(os.path.basename(m['path']) for m in gone))
        self._reload_cloud(keep_sel=True, why='--- Releyendo la nube')

    def open_example(self, key):
        """Ayuda › Abrir un ejemplo: la nube sintética se genera la primera vez en la carpeta de datos del programa
        y se abre; el tipo de pieza se deja a la propuesta automática, que es parte de lo que enseña."""
        if self.busy():
            return
        import ejemplos
        from PyQt5.QtCore import QStandardPaths
        folder = os.path.join(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or
                              os.path.expanduser('~'), 'ejemplos')
        _, _, title, kind, desc, _ = ejemplos.info(key)
        self.say(f'--- Ejemplo: {title}. {desc}')
        self.job = None
        self.keep_groups = []
        self._set_crops_quiet([])
        self.merges = []
        self._merge_note()
        self._plane_reset()
        self.classes = []
        self.reverse = []
        self._rev_note()
        self._kind_chosen = False

        prm = self.params()

        def task(log):
            return load_task(ejemplos.ensure(key, folder, log), prm, log)
        self.start(task, (), self.on_loaded)

    def on_loaded(self, out):
        (self.path, self.xyz, self.rgb, self.stats, self.orient, self.load_info,
         self.xyz_all, self.rgb_all) = out
        self.orient_undo = []
        self.result = None
        ext = self.xyz.max(0) - self.xyz.min(0)
        QTimer.singleShot(60, lambda: self.fit_dock('fichero'))
        self.lbl_file.setText(
            f'<span style="font-size:11pt; font-weight:700; color:{C["navy"]}">{os.path.basename(self.path)}</span><br>'
            f'{len(self.xyz):,} puntos · {"con color" if self.rgb is not None else "sin color"}<br>'
            f'Extensión: {ext[0]:.0f} × {ext[1]:.0f} × {ext[2]:.0f} mm'
            + self._units_note(ext)
            + (' · <i>malla</i>' if self.load_info['kind'] == 'malla' else '')
            + (f'<br>+ {len(self.merges)} nube{"s" if len(self.merges) > 1 else ""} unida{"s" if len(self.merges) > 1 else ""}'
               f' ({self.load_info.get("n_main", 0):,} puntos de la principal)' if self.merges else '')
            + self._clean_note())
        self.lbl_tag.setText(os.path.basename(self.path))
        self._las_note()
        self._plane_note()
        self.update_plan()
        if getattr(self, 'wizard', None) is not None:
            self.wizard.cloud_loaded()
        self.center.setCurrentIndex(1)
        self.res_box.hide()
        self.res_empty.show()
        self.relief_view.clear('Pulsa «Generar» para ver la vista previa')
        if self.mesh_view is not None:
            self.mesh_view.clear()
        self.show_cloud(home=not getattr(self, '_keep_view', False))
        self._keep_view = False
        self.update_orient()
        self.tabs.setCurrentIndex(0)
        if self.ptabs.currentIndex() == TAB_ORIENT:
            self.tabs.setCurrentWidget(self.orient_view)
        self._show_guess()
        g = self.load_info.get('guess')
        if self.load_info.get('guess_new'):     # nube recién abierta: los pasos vuelven a empezar
            self._seen = set()
            self.update_steps()
        if g and self.load_info.get('guess_new'):         # nube recién abierta (no una relimpieza o un recorte)
            self.say(f'Por su forma, la nube parece: {next(k for k in recetas.KINDS if k["key"] == g[0])["short"]} '
                     f'({g[1]})')
            self._apply_guess()

    # ---------- orientación en tiempo real
    def _orient_tools(self, form):
        """Herramientas de giro de la pestaña «Orientar»: todas fijan un giro explícito (desactivan el
        enderezado automático) y se pueden deshacer."""
        def btn(text, tip, fn):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            self.orient_btns.append(b)
            return b
        self.orient_btns = []
        tip = ('Arrastra en la vista sobre algo que deba quedar recto (un borde, un renglón): se pondrá '
               'horizontal o vertical. En modo objeto, elige «Arriba» y gira en planta.')
        t = QLabel('<b>Cuadrar el giro</b> · o arrastra en la vista')
        t.setObjectName('muted')
        t.setWordWrap(True)
        t.setToolTip(tip)
        t.setStyleSheet('margin-top: 6px;')
        form.addRow(t)
        self.btn_orient_lines = btn('Líneas talladas',
                                    'Alinear con líneas talladas: busca la dirección dominante de las aristas rectas '
                                    'del relieve y la pone a escuadra', self.orient_lines)
        self.btn_orient_contour = btn('Por contorno', 'Enderezar por contorno: alinea el contorno exterior de la '
                                      'pieza con los ejes', self.orient_contour)
        self.btn_orient_undo = btn('Deshacer', 'Deshacer giro: vuelve al giro anterior', self.orient_undo_last)
        row = QHBoxLayout()                       # los tres en una fila: Orientar ya es la pestaña más larga
        row.setSpacing(4)
        for b in (self.btn_orient_lines, self.btn_orient_contour, self.btn_orient_undo):
            row.addWidget(b)
        form.addRow(row)
        row = QHBoxLayout()
        row.setSpacing(4)
        for d in (-90, -1, -0.1, 0.1, 1, 90):
            row.addWidget(btn(f'{d:+g}°'.replace('.', ','), f'Girar {d:+g}° (positivo = antihorario)',
                              lambda _=False, d=d: self.orient_rotate(d)))
        form.addRow(row)
        self.orient_undo = []
        self._orient_dirty = False
        self.orient_timer = QTimer(self)
        self.orient_timer.setSingleShot(True)
        self.orient_timer.setInterval(80)
        self.orient_timer.timeout.connect(self.update_orient)
        self._orient_enable()

    def _orient_enable(self):
        on = getattr(self, 'orient', None) is not None
        for b in self.orient_btns:
            b.setEnabled(on)
        self.btn_orient_undo.setEnabled(on and bool(self.orient_undo))
        if self.widgets['mode'].currentText() == 'objeto':      # solo hay giro en planta
            self.btn_orient_lines.setEnabled(False)
            self.btn_orient_contour.setEnabled(False)

    def _ptab_changed(self, i):
        if i == TAB_ORIENT and getattr(self, 'orient', None) is not None:
            self.tabs.setCurrentWidget(self.orient_view)
        if getattr(self, 'xyz', None) is not None and i in (0, TAB_ORIENT):
            self._seen = getattr(self, '_seen', set()) | {'tipo' if i == 0 else 'orientar'}
        self.update_steps()

    def orient_changed(self, *_):
        if getattr(self, 'orient', None) is not None:
            self.orient_timer.start()

    def update_orient(self):
        """Recalcula la vista previa de orientación (solo si se ve; si no, al mostrarla)."""
        op = getattr(self, 'orient', None)
        self._orient_enable()
        if op is None:
            return
        if self.tabs.currentWidget() is not self.orient_view and self.center.currentWidget() is self.tabs:
            self._orient_dirty = True
            return
        self._orient_dirty = False
        prm = self.params()
        detail = self.orient_view.detail()
        if detail:
            QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            d = op.compute_detail(prm) if detail else op.compute(prm)
        except Exception as e:  # noqa: BLE001  la vista previa nunca debe tumbar la aplicación
            traceback.print_exc()
            d = None
            self.say(f'Vista de orientación: {e}')
        finally:
            if detail:
                QApplication.restoreOverrideCursor()
        self.orient_view.set_data(d, prm)

    def _set_total_angle(self, total, why):
        prm = self.params()
        cur = self.orient.total_angle(prm)
        self.orient_undo.append((prm.angle, prm.auto_straighten))
        total = ((total + 180) % 360) - 180                 # en (-180, 180]
        if total == -180:
            total = 180.0
        self.widgets['auto_straighten'].setChecked(False)
        self.widgets['angle'].setValue(round(total, 2))
        self.orient_view.view.line = None
        self.say(f'Giro: {cur:+.2f}° → {total:+.2f}° ({why})')
        self.update_orient()

    def orient_rotate(self, delta, why=None):
        if getattr(self, 'orient', None) is None:
            return
        cur = self.orient.total_angle(self.params())
        self._set_total_angle(cur + delta, why or f'{delta:+g}°')

    def orient_line(self, dx, dy):
        """Línea dibujada en la vista (mm de la pieza, y hacia arriba): girar lo mínimo para que quede
        horizontal o vertical."""
        phi = float(np.degrees(np.arctan2(dy, dx)))
        dh = ((-phi + 90) % 180) - 90
        dv = ((90 - phi + 90) % 180) - 90
        delta = dh if abs(dh) <= abs(dv) else dv
        self.orient_rotate(delta, 'línea ' + ('horizontal' if delta is dh else 'vertical'))

    def orient_lines(self):
        prm = self.params()
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            L = self.orient.lines_angle(prm)
        finally:
            QApplication.restoreOverrideCursor()
        if L is None:
            self.say('No se han encontrado líneas rectas claras en el relieve.')
            return
        cur = self.orient.total_angle(prm)
        self._set_total_angle(L + 90 * round((cur - L) / 90), 'líneas talladas')   # conserva el cuarto de vuelta

    def orient_contour(self):
        prm = self.params()
        A = self.orient.auto_angle(prm)
        cur = self.orient.total_angle(prm)
        self._set_total_angle(A + 90 * round((cur - A) / 90), 'contorno')

    def orient_undo_last(self):
        if not self.orient_undo:
            return
        a, auto = self.orient_undo.pop()
        self.widgets['auto_straighten'].setChecked(auto)
        self.widgets['angle'].setValue(a)
        self.orient_view.view.line = None
        self.update_orient()

    def open_wizard(self):
        import asistente
        self.wizard = asistente.Asistente(self, make_widget, SPEC_BY_NAME, plan_html, param_label, C)
        self.wizard.finished.connect(lambda _: setattr(self, 'wizard', None))
        self.wizard.show()

    def _units_note(self, ext):
        """Unidades de la nube en la ficha del fichero; en naranja si se han supuesto y pueden estar mal."""
        li = self.load_info
        u = li['units']
        if not li.get('guessed'):
            return f' · unidades {u}' if u != 'mm' else ''
        import entrada
        hint = entrada.units_hint(li, ext)
        if hint:
            return (f'<br><span style="color:{C["accent_dk"]}">⚠ Unidades supuestas: <b>mm</b>; {hint} en '
                    f'Tamaño › Unidades de la nube.</span>')
        big = float(max(ext))
        if u == 'm' or big < 50:
            why = ('se tomó como metros porque mide menos de 20 de lado' if u == 'm' else
                   'es una pieza muy pequeña: ¿está en cm o en m?')
            return (f'<br><span style="color:{C["accent_dk"]}">⚠ Unidades supuestas: '
                    f'<b>{u}</b> ({why}). Si no es así, elígelas en Tamaño › Unidades de la nube.</span>')
        return ' · unidades supuestas: mm'

    def _clean_note(self):
        li = self.load_info
        crop = ''
        if li.get('cropped'):
            crop = (f'<br><span style="color:#7f8c8d">● {li["cropped"]:,} puntos recortados</span> (en gris; '
                    f'«Deshacer» o «Sin recortes» en la vista de la nube)')
        return self._loose_note() + crop

    def _loose_note(self):
        li = self.load_info
        if li.get('keep') is None:
            return ''
        n_all, ng = int(li['group_n'].sum()), len(li['group_n'])
        red, green = '#c0392b', '#1e8c3f'
        if not self.widgets['clean'].isChecked():
            return (f'<br><span style="color:{red}">● {n_all:,} puntos en {ng} grupos sueltos</span> (en rojo): se '
                    f'usan, la limpieza está desactivada')
        kept = li.get('kept')
        nk = int(kept.sum()) if kept is not None else 0
        out = (f'<br><span style="color:{red}">● {li["removed"]:,} puntos sueltos quitados</span>'
               + (f' · <span style="color:{green}">● {nk} grupo{"s" if nk > 1 else ""} '
                  f'conservado{"s" if nk > 1 else ""}</span>' if nk else ''))
        return out + ' (clic en un punto rojo o verde de la vista para cambiarlo)'

    def _toggle_clean(self, *_, keep_view=False):
        """Limpieza activada o no, o grupos elegidos: no hace falta volver a leer el fichero, la máscara ya está
        calculada."""
        if getattr(self, 'xyz_all', None) is None or self.busy() or getattr(self, '_setting', False):
            return
        self._keep_view = keep_view
        self.start(prepare_task, (self.path, self.xyz_all, self.rgb_all, self.load_info, self.params()),
                   self.on_loaded)

    def _set_crops_quiet(self, crops):
        self.crops = crops
        if HAVE_GL:
            self.cloud_view.set_crop_state(len(crops))

    def _set_crops(self, crops, msg):
        if getattr(self, 'xyz_all', None) is None or self.busy():
            return
        self._set_crops_quiet(crops)
        self.say(msg)
        self._toggle_clean(keep_view=True)

    def _crop_drawn(self, M, poly, inside):
        """Rectángulo de recorte dibujado en la vista: se guarda con la matriz de la vista llevada a las
        coordenadas de la nube (la vista enseña los puntos centrados en _cloud_center)."""
        if getattr(self, 'xyz_all', None) is None or self.busy():
            return
        T = np.eye(4)
        T[:3, 3] = -np.asarray(self._cloud_center, np.float64)
        Mabs = np.asarray(M, np.float64) @ T
        c = {'M': [round(float(x), 12) for x in Mabs.ravel()], 'poly': [[round(float(a), 6) for a in q] for q in poly],
             'inside': bool(inside)}
        self._set_crops(self.crops + [c], f'Recorte {len(self.crops) + 1}: '
                        + ('se deja lo de dentro del rectángulo' if inside else 'se quita lo de dentro del rectángulo'))

    def _group_clicked(self, g):
        """Clic en un grupo suelto de la vista de la nube: conservarlo (verde) o volver a quitarlo (rojo)."""
        li = getattr(self, 'load_info', None) or {}
        if li.get('keep') is None or self.busy():
            return
        if not self.widgets['clean'].isChecked():
            self.say('La limpieza está desactivada: ya se usan todos los puntos sueltos. Marca Tamaño › Quitar '
                     'grupos sueltos para elegir cuáles quitar.')
            return
        c = [round(float(x), 3) for x in li['groups'][g]]
        n = int(li['group_n'][g])
        if li['kept'] is not None and li['kept'][g]:
            tol = li['group_tol']
            self.keep_groups = [k for k in self.keep_groups if np.linalg.norm(np.subtract(k, c)) > tol]
            self.say(f'Grupo suelto de {n:,} puntos: se vuelve a quitar')
        else:
            self.keep_groups.append(c)
            self.say(f'Grupo suelto de {n:,} puntos: se conserva')
        self._toggle_clean(keep_view=True)

    def _reload_cloud(self, *_, keep_sel=False, why='--- Releyendo la nube con las nuevas unidades'):
        """Unidades cambiadas o nubes unidas: se vuelve a leer la nube (las medidas y la vista dependen de ello)."""
        if (getattr(self, 'path', None) and getattr(self, 'xyz', None) is not None and not self.busy()
                and not getattr(self, '_setting', False)):
            self.say(why)
            if not keep_sel:                      # grupos, recortes y uniones están en mm: con otras unidades no valen
                self.keep_groups = []
                self._set_crops_quiet([])
                self._plane_reset()
                if self.merges:
                    self.say('Las nubes unidas se quitan: estaban encajadas con las unidades anteriores. Vuelve a '
                             'unirlas (Archivo › Unir otra nube…).')
                    self.merges = []
                    self._merge_note()
            self.start(load_task, (self.path, self.params()), self.on_loaded)

    def update_plan(self, *_):
        self.update_steps()
        self._adv_note()                   # un parámetro cambiado deja pendiente volver a generar
        if hasattr(self, 'printer_lbl'):
            w = self.widgets
            bed = (f'cama {w["bed_x"].value():.0f} × {w["bed_y"].value():.0f}' if w['bed_x'].value() > 0
                   and w['bed_y'].value() > 0 else 'sin comprobar la cama')
            noz, lay = (f'{w[k].value():g}'.replace('.', ',') for k in ('nozzle', 'layer'))
            self.printer_lbl.setText(f'Impresora: boquilla {noz} mm · capa {lay} mm · {bed} · calidad '
                                     f'{w["quality"].currentText()} · <a href="#">cambiar</a> (pestaña Avanzado o '
                                     f'el asistente)')
        st = getattr(self, 'stats', None)
        if st is None or not hasattr(self, 'plan_lbl'):
            return
        self.plan_lbl.setText(plan_html(st, self.params()))
        self._boost_note(st)
        import asistente
        txt = asistente.behind_html(st, self.params())
        self.behind_lbl.setText(txt)
        self.behind_lbl.setVisible(bool(txt))

    def _boost_note(self, st):
        """Bajo «Anchura del detalle»: los umbrales del realce en la piedra y lo que son en la pieza."""
        prm = self.params()
        if prm.boost <= 1:
            self.boost_lbl.setText('')
            return
        try:
            s = relief.print_plan(st, prm)['scale']
        except Exception:                          # noqa: BLE001  solo es una nota
            return
        zs = prm.z_scale if prm.mode in ('alturas', '3d') else 1.0
        lo, hi, size = relief.boost_limits(prm, s, zs)
        f = lambda x: f'{x:.3g}'.replace('.', ',')     # noqa: E731
        self.boost_lbl.setText(
            f'En la piedra: desde {f(lo)} hasta {f(hi)} mm, anchura {f(size)} mm'
            f'{" (auto)" if not (prm.boost_lo and prm.boost_hi and prm.boost_size) else ""}.<br>'
            f'En la pieza: {f(lo * s * zs)} / {f(hi * s * zs)} / {f(size * s)} mm '
            f'(1 mm de la piedra = {f(s * zs)} mm; capa de {f(prm.layer)} mm).')

    def show_cloud(self, home=True):
        """La nube entera tal como se leyó; los puntos de los grupos sueltos en rojo (se quitan) o en verde (se
        conservan porque se han elegido con un clic)."""
        xyz, rgb = self.xyz_all, self.rgb_all
        li = self.load_info
        keep = li.get('keep')
        good = np.flatnonzero(keep) if keep is not None else None
        n = len(xyz) if good is None else len(good)
        step = max(1, n // PREVIEW_PTS)
        idx = np.arange(0, len(xyz), step) if good is None else good[::step]
        p = xyz[idx]
        c = rgb[idx] / 255.0 if rgb is not None else np.tile([0.3, 0.3, 0.6], (len(p), 1))
        loose = None
        if keep is not None:                     # todos los sueltos (hasta 300.000), para que se vean aunque sean pocos
            sel = slice(None, None, max(1, len(li['loose_idx']) // 300_000))
            bad, grp = li['loose_idx'][sel], li['loose_grp'][sel]
            col = np.tile([0.85, 0.12, 0.1], (len(bad), 1))
            if li.get('kept') is not None and li['kept'].any():
                col[li['kept'][grp]] = [0.1, 0.62, 0.25]
            loose = (len(p), grp)
            idx = np.r_[idx, bad]
            p = np.r_[p, xyz[bad]]
            c = np.r_[c, col]
        if li.get('crop') is not None:           # lo recortado, en gris claro
            c = np.array(c, np.float64)
            c[~li['crop'][idx]] = [0.80, 0.81, 0.82]
        if HAVE_GL:
            if not home and getattr(self, '_cloud_center', None) is not None:
                p0 = self._cloud_center
            else:
                p0 = self._cloud_center = p.mean(0)
            self.cloud_view.show_cloud((p - p0).astype(np.float32), c, loose, home)
        else:
            f = self.cloud_view.fig
            f.clear()
            ax = f.add_subplot(111, projection='3d')
            ax.scatter(p[::10, 0], p[::10, 1], p[::10, 2], s=0.3, c=c[::10])
            self.cloud_view.canvas.draw_idle()

    def run_recon(self, preview=False):
        if self.busy():                       # p. ej. el asistente acaba de cambiar la superficie: esperar
            QTimer.singleShot(200, lambda: self.run_recon(preview))
            return
        prm = self.params()
        if preview:
            prm = relief.preview_params(prm, self.stats)
            self.say(f'--- Vista previa rápida (resolución {prm.res:g}, {relief.PREVIEW_FACTOR:g} veces más gruesa; '
                     f'sin simplificar ni acabados)')
        else:
            if not self.check_memory(prm):
                return
            self.say('--- Generando superficie')
        self.job = Job()
        try:                                       # para comparar con lo que de verdad gasta (run_external)
            self.job.est = relief.memory_estimate(self.stats, prm, len(self.xyz))['gb']
        except Exception:
            self.job.est = None
        self.last_prm = self.params()
        self.next_is_preview = preview
        self.run_log_start = len(self.log.toPlainText())
        self.start(recon_task, (self.path, prm, self.job), self.on_recon)

    def check_memory(self, prm, failed=False):
        """Antes de generar (o tras un corte por falta de memoria): si no hay memoria de sobra, ventana que hay
        que aceptar con los parámetros que bajan el consumo. Devuelve True si se puede seguir."""
        if getattr(self, 'stats', None) is None or self.xyz is None:
            return True
        est = relief.memory_estimate(self.stats, prm, len(self.xyz))
        if est['avail']:
            self.say(f'Memoria estimada: ≈ {est["gb"]:.1f} GB (disponibles {est["avail"]:.1f} de '
                     f'{est["total"]:.0f} GB)')
        if est['level'] == 'ok' and not failed:
            return True
        import memoria
        dlg = memoria.MemoryDialog(self, prm, self.stats, len(self.xyz), make_widget, SPEC_BY_NAME, param_label, C,
                                   failed=failed)
        dlg.exec_()
        if dlg.choice is None:
            self.say('Generación cancelada por aviso de memoria')
            return False
        if dlg.choice == 'aplicar':
            changed = [f for f in Params.__dataclass_fields__ if getattr(dlg.p, f) != getattr(prm, f)]
            self.set_params(dlg.p)
            self.say('Cambios para ahorrar memoria: ' +
                     ', '.join(f'{param_label(f)} = {dlg._fmt(f, getattr(dlg.p, f))}' for f in changed))
        else:
            self.say('AVISO: se genera igualmente, aunque puede agotar la memoria')
        return True

    def on_recon(self, r):
        self.result = r
        self._seen = getattr(self, '_seen', set()) | {'nube', 'tipo', 'orientar'}
        self.result_reg = None                   # pieza nueva: aún no está en el registro
        self.result_preview = getattr(self, 'next_is_preview', False)
        sx, sy, sz = r.size
        color = r.vcolors is not None
        sizes = relief.file_sizes(len(r.verts), len(r.tris), color)
        t = self.tiles
        t['dims'].setText(f'{sx:.0f} × {sy:.0f} × {sz:.0f}')
        if r.bad_edges == 0:
            t['state'].setText(f'<span style="color:{C["ok"]}">✔ cerrada</span>')
        else:
            t['state'].setText(f'<span style="color:{C["bad"]}">✘ {r.bad_edges} abiertas</span>')
        t['tris'].setText(f'{len(r.tris):,}'.replace(',', '.'))
        t['stl'].setText(relief.fmt_bytes(sizes['STL']))
        prm = self.last_prm
        nl = r.relief_depth / prm.layer if prm.layer > 0 else 0
        lay_col = C['bad'] if nl < 10 and r.mode != 'objeto' else C['navy']
        self.tile_caps['layers'].setText('Altura' if r.mode == 'objeto' else 'Relieve')
        self.tile_caps['pla'].setText('PLA (hueca, sin relleno)' if r.hollow else 'PLA (relleno 15 %)')
        t['layers'].setText(f'<span style="color:{lay_col}">{r.relief_depth:.{0 if r.mode == "objeto" else 1}f} mm · '
                            f'{nl:.0f} capas</span>')
        t['pla'].setText(f'≈ {r.grams_pla * (1 if r.hollow else 0.3):,.0f} g')
        extra = ['<b>Método:</b> ' + ('esquina: un mapa de alturas por cara' if getattr(r, 'corner', False)
                                       else METHOD_NAME.get(r.mode, r.mode)),
                 f'<b>Escala:</b> {r.scale:.4f} · celda {r.cell:.3f} mm' + (f' · giro {r.angle:+.1f}°' if r.angle else ''),
                 f'<b>Otros formatos:</b> 3MF {relief.fmt_bytes(sizes["3MF"])} · PLY {relief.fmt_bytes(sizes["PLY"])} · OBJ {relief.fmt_bytes(sizes["OBJ"])}',
                 f'<b>Volumen:</b> {r.volume / 1000:,.1f} cm³ · PLA ' + ('(hueca, sin relleno)' if r.hollow else 'macizo')
                 + f' ≈ {r.grams_pla:,.0f} g',
                 f'<b>Resolución usada:</b> {r.res:.2f} (celda {r.cell:.3f} mm en la pieza)']
        if r.err_max:
            extra.append(f'<b>Simplificada:</b> error medio {r.err_mean:.3f} mm, p99 {r.err_p99:.3f} mm '
                         f'(tolerancia {relief.tolerance(prm):.3f})')
        dst = desviacion.stats(r)
        if dst:
            extra.insert(0, f'<b>Fidelidad al escaneo:</b> media {dst["mean"]:.3f} mm · 95 % de la superficie a menos de '
                         f'{dst["p95"]:.3f} mm · {dst["covered"]:.0f} % de la superficie con escaneo '
                         f'· <a href="dev" style="color:{C["teal"]}">'
                         f'<b>ver el mapa de desviación</b></a>')
        if nl < 10 and r.mode != 'objeto':
            extra.append(f'<span style="color:{C["bad"]}">⚠ El relieve ocupa pocas capas: usa capa más fina, '
                         f'pieza más grande o exageración del relieve.</span>')
        if self.result_preview:
            extra.insert(0, f'<span style="color:{C["bad"]}"><b>VISTA PREVIA</b> (resolución '
                            f'{relief.PREVIEW_FACTOR:g} veces más gruesa, sin simplificar ni acabados): sirve para '
                            f'comprobar; pulsa <b>Generar</b> para la pieza definitiva.</span>')
        self.res_extra.setText('<br>'.join(extra))
        self.res_empty.hide()
        self.res_box.show()
        QTimer.singleShot(60, lambda: self.fit_dock('resultado'))
        self.show_relief()
        self.show_mesh()
        self.tabs.setCurrentWidget(self.relief_view)

    def _link(self, what):
        if what == 'dev':
            self.show_deviation()

    def show_deviation(self):
        """Pestaña Malla 3D con la pieza coloreada por su separación del escaneo."""
        mv = self.mesh_view
        if mv is None or mv.dev is None:
            return
        self.tabs.setCurrentWidget(mv)
        mv.color_cb.setCurrentText('desviación')

    def show_relief(self):
        r = self.result
        f = self.relief_view.fig
        f.clear()
        ax = f.add_subplot(111)
        g = r.grid
        filled = np.where(np.isnan(g), np.nanmin(g), g)
        img = LightSource(315, 40).hillshade(filled, vert_exag=1, dx=r.cell, dy=r.cell)
        lo, hi = np.percentile(img[~np.isnan(g)], [1, 99.5])
        img = np.clip((img - lo) / max(hi - lo, 1e-6), 0, 1)
        img = np.ma.masked_where(np.isnan(g), img)
        h, w = g.shape
        ax.set_facecolor(C['view'])
        ax.imshow(img, cmap=STONE, origin='lower', extent=[0, w * r.cell, 0, h * r.cell], vmin=0, vmax=1)
        for sp in ax.spines.values():
            sp.set_color('#b8c2ca')
        ax.tick_params(colors=C['muted'], labelsize=8)
        ax.set_xlabel('mm')
        ax.set_ylabel('mm')
        self.relief_view.canvas.draw_idle()

    def show_mesh(self):
        if self.mesh_view is None:
            return
        r = self.result
        verts, faces, dev = r.verts, r.tris, r.dev
        if len(faces) > MESH_PREVIEW_TRIS:
            verts, faces = self._preview_mesh(r)
            if dev is not None:                     # la malla ligera toma la desviación del vértice más cercano
                from scipy.spatial import cKDTree
                dev = dev[cKDTree(r.verts).query(verts, workers=-1)[1]]
        ctr = (verts.max(0) + verts.min(0)) / 2
        ctr[2] = 0
        dst = desviacion.stats(r)
        txt = (f'media {dst["mean"]:.2f} · p95 {dst["p95"]:.2f} mm · {dst["covered"]:.0f} % escaneada'
               .replace('.', ',') if dst else '')
        self.mesh_view.show_mesh((verts - ctr).astype(np.float32), faces, smooth=r.mode in ('objeto', 'bloque'),
                                 dev=dev, dev_auto=desviacion.auto_limit(r), dev_text=txt, scale=r.scale)

    @staticmethod
    def _preview_mesh(r):
        """Malla ligera para la vista previa (la exportada no se toca)."""
        try:
            import open3d as o3d
            m = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(r.verts),
                                          o3d.utility.Vector3iVector(r.tris.astype(np.int32)))
            vox = float(np.ptp(r.verts[:, :2], 0).max()) / 1200
            m = m.simplify_vertex_clustering(voxel_size=vox)
            return np.asarray(m.vertices), np.asarray(m.triangles)
        except ImportError:
            g = r.grid
            k = max(1, int(np.ceil(max(g.shape) / 500)))
            gs = g[::k, ::k]
            msk = ~np.isnan(gs)
            base = float(np.nanmin(gs))
            vv, ff, _ = relief._mesh(np.where(msk, gs - base, 0.0), msk, r.cell * k, base)
            return vv, ff

    def _ask_format(self, title, default_name):
        filt = ';;'.join(relief.FORMATS)
        path, sel = QFileDialog.getSaveFileName(self, title, default_name, filt)
        if not path:
            return None, None
        kind = relief.FORMATS.get(sel, 'stl')
        ext = relief.EXT[kind]
        if not path.lower().endswith(ext):
            path = os.path.splitext(path)[0] + ext
        if kind == 'ply' and self.path and os.path.abspath(path) == os.path.abspath(self.path):
            QMessageBox.warning(self, 'Guardar', 'No se puede sobrescribir la nube de puntos original.')
            return None, None
        return path, kind

    def save_mesh(self):
        if self.result is None:
            return
        base = os.path.splitext(self.path)[0] + '.stl' if self.path else 'relieve.stl'
        path, kind = self._ask_format('Guardar malla', base)
        if path:
            sz = relief.file_sizes(len(self.result.verts), len(self.result.tris), self.result.vcolors is not None)
            self.say(f'Tamaño estimado: {relief.fmt_bytes(sz[{"stl": "STL", "stla": "STL ASCII", "obj": "OBJ", "ply": "PLY", "3mf": "3MF"}[kind]])}')
            if kind in ('stl', 'stla', '3mf') and self.result.vcolors is not None:
                self.say('Nota: este formato no guarda el color; usa PLY u OBJ si lo necesitas.')
            import registro
            reg = None
            if registro.auto_enabled() and not self.result_preview:
                reg = dict(prm=self.last_prm, source=self.source_info(), eid=getattr(self, 'result_reg', None))
            self.start(save_task, (path, self.result, kind, reg), self._saved)

    def _saved(self, out):
        path, eid = out
        self._saved_result = self.result
        self.update_steps()
        if eid:
            self.result_reg = eid
        self.say(f'Listo: {path}' + (f'  (registro: {eid})' if eid else ''))

    def open_registry(self):
        import ventana_registro
        ventana_registro.RegistryDialog(self, C).exec_()

    def use_registry_params(self, e):
        """«Usar sus ajustes» con una nube abierta: si cambian unidades o limpieza, se aplican a la nube."""
        units, clean = self.apply_registry_params(e)
        if units:
            self._reload_cloud(keep_sel=True)
        elif clean:
            self._toggle_clean()

    def apply_registry_params(self, e):
        p = Params.from_json(__import__('json').dumps(e.get('ajustes', {})))
        old = self.params()
        self.set_params(p)
        self.say(f'Ajustes de la pieza {e.get("id")} ({e.get("pieza", "")}) cargados')
        return old.units != p.units or old.merge != p.merge or old.classes != p.classes, any(getattr(old, k) != getattr(p, k)
                                         for k in ('clean', 'keep_groups', 'crop', 'view', 'surface', 'plane', 'plane_pts'))

    def repeat_from_registry(self, e, cloud):
        """Abrir la nube de una pieza del registro con sus ajustes (las unidades y la limpieza cuentan al leer)."""
        self.apply_registry_params(e)
        self.job = None
        self.start(load_task, (cloud, self.params()), self.on_loaded)
        self.say(f'Repetir {e.get("id")}: abriendo {cloud}. Revisa y pulsa «Generar».')

    def source_info(self):
        info = {k: v for k, v in (getattr(self, 'load_info', None) or {}).items()
                if k in ('kind', 'units', 'factor', 'removed', 'guessed')}
        info.update(path=self.path or '', points=len(self.xyz) if self.xyz is not None else 0)
        if self.merges:
            info['unidas'] = [m['path'] for m in self.merges]
        return info

    def save_pdf(self):
        if self.result is None or getattr(self, 'result_preview', False):
            return
        base = (os.path.splitext(self.path)[0] if self.path else 'pieza') + '_ficha.pdf'
        path, _ = QFileDialog.getSaveFileName(self, 'Ficha PDF de la pieza', base, 'PDF (*.pdf)')
        if not path:
            return
        if not path.lower().endswith('.pdf'):
            path += '.pdf'
        txt = self.log.toPlainText()
        start = getattr(self, 'run_log_start', 0)
        txt = txt[start:] if start <= len(txt) else txt
        import ficha
        import registro
        eid = getattr(self, 'result_reg', None)
        src = dict(self.source_info(), id=eid) if eid else self.source_info()

        def done(p):
            self.say(f'Ficha guardada: {p}')
            if eid and registro.update(eid, ficha=os.path.abspath(p)):
                self.say(f'Registro: ficha anotada en {eid}')
        self.start(lambda log: (log(f'Escribiendo la ficha {path}…'),
                                ficha.make_pdf(path, self.result, self.last_prm, src, txt, param_label))[1],
                   (), done)

    def batch(self):
        dlg = BatchDialog(self)
        if not dlg.exec_():
            return
        paths, outdir = dlg.paths, dlg.out.text().strip()
        os.makedirs(outdir, exist_ok=True)
        kind = relief.FORMATS[dlg.fmt.currentText()]
        self.say(f'--- Procesando {len(paths)} ficheros con los parámetros actuales → {outdir}')
        self.job = Job()
        prm = self.params()
        prm.keep_groups = []                      # los grupos elegidos con clic son de la nube abierta, no del lote
        prm.crop = []
        prm.merge = []                            # las nubes unidas son de la nube abierta
        self.start(batch_task, (paths, outdir, prm, kind, self.job, dlg.pdf.isChecked(),
                                dlg.reg.isChecked()),
                   lambda done: (self.say(f'Lote terminado: {len(done)}/{len(paths)} ficheros'),
                                 QMessageBox.information(self, 'Lote terminado',
                                                         f'{len(done)} de {len(paths)} ficheros generados en\n'
                                                         f'{outdir}\n\nResumen: lote_resumen.csv')))


def main():
    # visores 3D en varias ventanas (la de unir nubes): pyqtgraph compila cada shader una vez, y solo vale en todos
    # si los contextos OpenGL se comparten
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyle('Fusion')               # base neutra para que la hoja de estilos se vea igual en cualquier escritorio
    app.setApplicationName('nube2stl')
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(LOGO))       # icono de todas las ventanas (y de la barra de tareas)
    t0 = time.time()
    splash = None
    if not os.environ.get('NUBE2STL_NO_SPLASH'):
        splash = QSplashScreen(splash_pixmap())
        splash.show()
        splash.showMessage('  Preparando la interfaz…', Qt.AlignBottom | Qt.AlignLeft, QColor('#9fb9c4'))
        app.processEvents()
    w = MainWindow()

    def ready():
        w.show()
        if splash is not None:
            splash.finish(w)
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            w.start(load_task, (sys.argv[1], w.params()), w.on_loaded)
    # la portada se ve al menos 1,5 s (un clic la cierra antes)
    QTimer.singleShot(max(0, int(1500 - (time.time() - t0) * 1000)) if splash else 0, ready)
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
