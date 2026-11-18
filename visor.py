"""Visor 3D de nube2stl (nube de puntos y malla), sobre pyqtgraph.opengl.

Navegación:
  - botón izquierdo: girar alrededor del centro de giro;
  - botón derecho, central o Mayús + izquierdo: desplazar;
  - rueda: acercar o alejar hacia el punto que hay bajo el cursor;
  - doble clic: el punto bajo el cursor pasa a ser el centro de giro (se marca en naranja mientras se gira);
  - teclado: flechas desplazan, + y - acercan, R vista inicial, F encuadrar.
El punto bajo el cursor se busca proyectando en la CPU los puntos de la vista (los mismos que se dibujan), sin leer
el búfer de profundidad: funciona igual con cualquier controlador gráfico.
"""
import numpy as np
from OpenGL import GL
from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF, QVector3D
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton,
                             QVBoxLayout, QWidget)
import pyqtgraph.opengl as gl
from pyqtgraph.opengl import shaders

from corte import clip_mesh, section_lines  # noqa: F401  (geometría del corte, sin Qt)

# Luces que siguen a la cámara (en coordenadas de la vista: +z apunta hacia el observador). Se registran como
# programas de pyqtgraph y se usan por nombre en GLMeshItem(shader=...).
_VERT = """
    varying vec3 normal;
    void main() {
        normal = normalize(gl_NormalMatrix * gl_Normal);
        gl_FrontColor = gl_Color;
        gl_BackColor = gl_Color;
        gl_Position = ftransform();
    }
"""
_FRAG = """
    varying vec3 normal;
    void main() {
        vec3 n = normalize(normal);
        float p = %s;
        vec4 c = gl_Color;
        gl_FragColor = vec4(c.rgb * (%s), c.a);
    }
"""
shaders.ShaderProgram('n2s_frontal', [shaders.VertexShader(_VERT),
                                      shaders.FragmentShader(_FRAG % ('abs(n.z)', '0.30 + 0.75 * p'))])
shaders.ShaderProgram('n2s_rasante', [shaders.VertexShader(_VERT),
                                      shaders.FragmentShader(_FRAG % (
                                          'max(dot(n, normalize(vec3(-0.75, 0.55, 0.38))), 0.0)',
                                          '0.22 + 0.95 * p'))])
LIGHTS = {'rasante móvil': 'n2s_rasante', 'frontal': 'n2s_frontal', 'rasante fija': None}

MARKER_GL = {GL.GL_DEPTH_TEST: False, GL.GL_BLEND: True,
             'glBlendFunc': (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)}


def fmt_mm(x):
    """Medida legible con coma decimal: 2 decimales por debajo de 10 mm, 1 hasta 1000, ninguno por encima."""
    dec = 2 if x < 10 else 1 if x < 1000 else 0
    return f'{x:.{dec}f}'.replace('.', ',')


class _LassoOverlay(QWidget):
    """Trazo del lazo o del pincel encima de la vista 3D (no recibe el ratón). Con el pincel enseña también un
    círculo del tamaño del pincel donde está el cursor."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.pts = []
        self.mode = 'lazo'            # 'lazo' | 'pincel'
        self.r = 0                    # radio del pincel (píxeles)
        self.erase = False
        self.cursor_at = None

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rgb = (90, 110, 130) if self.erase else (224, 122, 63)
        if self.mode == 'pincel':
            if self.pts:
                pen = QPen(QColor(*rgb, 110), 2 * self.r, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                p.setPen(pen)
                if len(self.pts) == 1:
                    p.drawPoint(self.pts[0])
                else:
                    p.drawPolyline(QPolygonF(self.pts))
            if self.cursor_at is not None:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 200), 3))
                p.drawEllipse(self.cursor_at, self.r, self.r)
                p.setPen(QPen(QColor(*rgb), 1.5))
                p.drawEllipse(self.cursor_at, self.r, self.r)
            return
        if len(self.pts) < 2:
            return
        poly = QPolygonF(self.pts)
        p.setBrush(QColor(*rgb, 40))
        p.setPen(QPen(QColor(*rgb), 2))
        p.drawPolygon(poly)


def brush_centers(P, mvp, p00, size, stroke, r_px):
    """Trazo del pincel en pantalla -> centros en la escena y radio (unidades de la escena).

    P (N,3) puntos de la vista; mvp la matriz 4×4 de la vista (puntos -> −1…1); p00 el elemento [0,0] de la
    proyección (para pasar píxeles a unidades a cada profundidad); size (ancho, alto) en píxeles; stroke (K,2) el
    trazo en píxeles; r_px el radio del pincel en píxeles. A lo largo del trazo, cada medio radio, se toma el punto
    visible más cercano al cursor: el de delante entre los que caen en el círculo (los de detrás, a más de un radio
    de profundidad, no cuentan). Así se pinta la superficie que se ve, no lo que queda detrás."""
    from scipy.spatial import cKDTree
    W, H = size
    S = np.asarray(stroke, np.float64).reshape(-1, 2)
    if len(S) > 1:                                    # remuestrear cada medio radio
        seg = np.linalg.norm(np.diff(S, axis=0), axis=1)
        t = np.r_[0, np.cumsum(seg)]
        n = max(2, int(t[-1] / max(1.0, r_px / 2)) + 1)
        tt = np.linspace(0, t[-1], n)
        S = np.c_[np.interp(tt, t, S[:, 0]), np.interp(tt, t, S[:, 1])]
    M = np.asarray(mvp, np.float64)
    P = np.asarray(P)
    lo, hi = S.min(0) - r_px, S.max(0) + r_px
    h = P @ M[:3, :3].T.astype(np.float32) + M[:3, 3].astype(np.float32)
    w = P @ M[3, :3].astype(np.float32) + np.float32(M[3, 3])
    ok = w > 1e-9
    w1 = np.where(ok, w, 1)
    sx = (h[:, 0] / w1 + 1) / 2 * W
    sy = (1 - h[:, 1] / w1) / 2 * H
    ok &= (np.abs(h[:, 2] / w1) < 1) & (sx >= lo[0]) & (sx <= hi[0]) & (sy >= lo[1]) & (sy <= hi[1])
    idx = np.flatnonzero(ok)
    if not len(idx):
        return np.zeros((0, 3)), 0.0
    tree = cKDTree(np.c_[sx[idx], sy[idx]])
    cen, rad = [], []
    for k, c in enumerate(tree.query_ball_point(S, r_px)):
        if not c:
            continue
        c = idx[c]
        wmin = float(w[c].min())
        rw = r_px * 2 * wmin / (p00 * W)               # el radio en unidades a esa profundidad
        front = c[w[c] <= wmin + rw]
        j = front[np.argmin((sx[front] - S[k, 0]) ** 2 + (sy[front] - S[k, 1]) ** 2)]
        cen.append(P[j])
        rad.append(rw)
    if not cen:
        return np.zeros((0, 3)), 0.0
    return np.asarray(cen, np.float64), float(np.median(rad))


class NavView(gl.GLViewWidget):
    clicked = pyqtSignal(QPointF)          # clic izquierdo sin arrastrar (no se emite si es parte de un doble clic)
    escaped = pyqtSignal()                 # tecla Esc
    boxed = pyqtSignal(QRectF)             # modo recorte: rectángulo dibujado (píxeles de la vista)
    lassoed = pyqtSignal(object)           # modo recorte con lazo: [QPointF] (píxeles de la vista)
    brushed = pyqtSignal(object)           # pincel: trazo [QPointF] (píxeles de la vista)

    def __init__(self, bg):
        super().__init__()
        self._click_at = None
        self.crop_mode = False
        self.crop_shape = 'rect'           # 'rect' | 'lazo'
        self.brush_r = 0                   # radio del pincel en píxeles (0 = sin pincel)
        self._band = None
        self._lasso = None
        self._drawing = None               # 'lazo' | 'pincel' mientras se dibuja
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(lambda: self.clicked.emit(self._click_at))
        self.setBackgroundColor(bg)
        self.setFocusPolicy(Qt.StrongFocus)
        self.pick = None                 # (N,3) puntos para buscar bajo el cursor
        self.home = None                 # parámetros de la vista inicial
        self.size_hint_scene = 1.0       # tamaño de la escena (para límites de zoom)
        self.marker = None
        self._press = None
        self._anim = None

    def _overlay(self):
        if self._lasso is None:
            self._lasso = _LassoOverlay(self)
        self._lasso.setGeometry(self.rect())
        return self._lasso

    def set_brush(self, r, erase=False):
        """Pincel de radio r píxeles (0 = sin pincel); erase: borrar en vez de pintar (solo cambia el color)."""
        self.brush_r = r
        self.setMouseTracking(r > 0)
        if r > 0:
            o = self._overlay()
            o.mode, o.r, o.erase = 'pincel', r, erase
            o.show()
            o.raise_()
            o.update()
        elif self._lasso is not None and self._drawing is None:
            self._lasso.cursor_at = None
            self._lasso.hide()

    def set_draw_erase(self, erase):
        if self._lasso is not None:
            self._lasso.erase = erase
            self._lasso.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._lasso is not None:
            self._lasso.setGeometry(self.rect())

    def leaveEvent(self, ev):
        if self._lasso is not None and self._lasso.cursor_at is not None:
            self._lasso.cursor_at = None
            self._lasso.update()
        super().leaveEvent(ev)

    # ------------------------------------------------------------------ escena
    def clear_scene(self):
        self.clear()
        self.marker = None
        self.pick = None

    def set_scene(self, pick_points, home):
        """pick_points: puntos (en coordenadas de la vista) para el doble clic y el zoom al cursor.
        home: dict(center=(x,y,z), distance, elevation, azimuth)."""
        self.pick = np.asarray(pick_points, dtype=np.float32) if pick_points is not None else None
        self.home = home
        if self.pick is not None and len(self.pick):
            self.size_hint_scene = float(np.ptp(self.pick, 0).max()) or 1.0
        self.marker = gl.GLScatterPlotItem(pos=np.zeros((1, 3), np.float32), size=11,
                                           color=(0.88, 0.48, 0.25, 0.95), pxMode=True)
        self.marker.setGLOptions(MARKER_GL)
        self.marker.setDepthValue(10)
        self.marker.setVisible(False)
        self.addItem(self.marker)
        self.go_home()

    def go_home(self):
        if self.home:
            h = self.home
            self._animate(QVector3D(*h['center']), h['distance'], h.get('elevation'), h.get('azimuth'))

    def view_from(self, elevation, azimuth):
        self._animate(self.opts['center'], self.opts['distance'], elevation, azimuth)

    def fit(self):
        """Encuadra toda la pieza manteniendo la orientación."""
        if self.pick is None or not len(self.pick):
            return
        lo, hi = self.pick.min(0), self.pick.max(0)
        c = (lo + hi) / 2
        r = float(np.linalg.norm(hi - lo)) / 2
        d = r / np.tan(np.radians(self.opts['fov']) / 2) * 1.05
        self._animate(QVector3D(*c), d, None, None)

    # ------------------------------------------------------------------ proyección y búsqueda
    def _mvp(self):
        m = self.projectionMatrix() * self.viewMatrix()
        return np.array(m.data(), dtype=np.float64).reshape(4, 4).T     # Qt guarda por columnas

    def project(self, P):
        """Coordenadas de pantalla (píxeles lógicos), profundidad NDC y máscara de visibles."""
        M = self._mvp()
        h = P @ M[:3, :3].T.astype(np.float32) + M[:3, 3].astype(np.float32)
        w = P @ M[3, :3].astype(np.float32) + np.float32(M[3, 3])
        ok = w > 1e-9
        w = np.where(ok, w, 1)
        nx, ny, nz = h[:, 0] / w, h[:, 1] / w, h[:, 2] / w
        ok &= (np.abs(nx) <= 1) & (np.abs(ny) <= 1) & (nz > -1) & (nz < 1)
        sx = (nx + 1) / 2 * self.width()
        sy = (1 - ny) / 2 * self.height()
        return sx, sy, nz, ok

    def point_at(self, pos, radius=7):
        """Punto de la escena más cercano a la cámara bajo la posición de pantalla pos (o None)."""
        if self.pick is None or not len(self.pick):
            return None
        sx, sy, nz, ok = self.project(self.pick)
        for r in (radius, radius * 3):
            near = ok & ((sx - pos.x()) ** 2 + (sy - pos.y()) ** 2 <= r * r)
            idx = np.flatnonzero(near)
            if len(idx):
                # el más cercano a la cámara; entre los casi igual de cercanos, el más centrado en el cursor
                zmin = nz[idx].min()
                front = idx[nz[idx] <= zmin + 2e-4]
                d2 = (sx[front] - pos.x()) ** 2 + (sy[front] - pos.y()) ** 2
                return self.pick[front[np.argmin(d2)]].astype(np.float64)
        return None

    # ------------------------------------------------------------------ movimiento
    def _animate(self, center, distance, elevation, azimuth, steps=10):
        c0, d0 = QVector3D(self.opts['center']), self.opts['distance']
        e0, a0 = self.opts['elevation'], self.opts['azimuth']
        e1 = e0 if elevation is None else elevation
        a1 = a0 if azimuth is None else a0 + ((azimuth - a0 + 180) % 360 - 180)    # por el camino corto
        if self._anim:
            self._anim.stop()
        self._target = (QVector3D(center), distance, e1, a1)
        k = [0]

        def step():
            k[0] += 1
            t = k[0] / steps
            t = t * t * (3 - 2 * t)
            self.opts['center'] = c0 * (1 - t) + center * t
            self.opts['distance'] = d0 * (1 - t) + distance * t
            self.opts['elevation'] = e0 * (1 - t) + e1 * t
            self.opts['azimuth'] = a0 * (1 - t) + a1 * t
            self.update()
            if k[0] >= steps:
                self._anim.stop()
        self._anim = QTimer(self)
        self._anim.timeout.connect(step)
        self._anim.start(16)

    def _zoom(self, factor, pos=None):
        d = self.opts['distance']
        nd = float(np.clip(d * factor, self.size_hint_scene * 1e-3, self.size_hint_scene * 50))
        f = nd / d
        target = self.point_at(pos) if pos is not None else None
        if target is not None:
            # escalar alrededor del punto del cursor: ese punto se queda quieto en la pantalla
            t = QVector3D(*target)
            self.opts['center'] = t + (self.opts['center'] - t) * f
        self.opts['distance'] = nd
        self.update()

    def _show_marker(self, on):
        if self.marker is not None:
            c = self.opts['center']
            self.marker.setData(pos=np.array([[c.x(), c.y(), c.z()]], np.float32))
            self.marker.setVisible(on)

    # ------------------------------------------------------------------ eventos
    def mousePressEvent(self, ev):
        self.mousePos = ev.localPos()
        self._press = ev.button()
        self._press_at = ev.localPos()
        draw = ev.button() == Qt.LeftButton and not (ev.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier))
        if draw and (self.brush_r > 0 or (self.crop_mode and self.crop_shape == 'lazo')):
            o = self._overlay()
            o.mode = 'pincel' if self.brush_r > 0 else 'lazo'
            o.pts = [ev.localPos()]
            o.show()
            o.raise_()
            o.update()
            self._drawing = o.mode
            return
        if self.crop_mode and draw:
            from PyQt5.QtWidgets import QRubberBand
            if self._band is None:
                self._band = QRubberBand(QRubberBand.Rectangle, self)
            self._band.setGeometry(ev.pos().x(), ev.pos().y(), 0, 0)
            self._band.show()
            return
        if ev.button() == Qt.LeftButton and not (ev.modifiers() & Qt.ShiftModifier):
            self._show_marker(True)
        self.setCursor(Qt.ClosedHandCursor if ev.button() != Qt.LeftButton or ev.modifiers() & Qt.ShiftModifier
                       else Qt.SizeAllCursor)

    def mouseMoveEvent(self, ev):
        lpos = ev.localPos()
        if not hasattr(self, 'mousePos'):
            self.mousePos = lpos
        diff = lpos - self.mousePos
        self.mousePos = lpos
        b = ev.buttons()
        if self.brush_r > 0 and self._lasso is not None:     # el círculo del pincel sigue al cursor
            self._lasso.cursor_at = lpos
            self._lasso.update()
        if self._drawing:
            if (lpos - self._lasso.pts[-1]).manhattanLength() >= 3:
                self._lasso.pts.append(lpos)
                self._lasso.update()
            return
        if self._band is not None and self._band.isVisible():
            from PyQt5.QtCore import QRect
            p0 = self._press_at
            self._band.setGeometry(QRect(int(p0.x()), int(p0.y()), int(lpos.x() - p0.x()), int(lpos.y() - p0.y()))
                                   .normalized())
            return
        if b & (Qt.RightButton | Qt.MiddleButton) or (b & Qt.LeftButton and ev.modifiers() & Qt.ShiftModifier):
            self.pan(diff.x(), diff.y(), 0, relative='view')
        elif b & Qt.LeftButton:
            self.orbit(-diff.x() * 0.5, diff.y() * 0.5)
            self._show_marker(True)

    def mouseReleaseEvent(self, ev):
        if self._drawing:
            kind, self._drawing = self._drawing, None
            pts = self._lasso.pts
            self._lasso.pts = []
            if kind == 'pincel':
                self._lasso.update()
                self.brushed.emit(pts)
                return
            self._lasso.hide()
            if len(pts) >= 3 and QPolygonF(pts).boundingRect().width() > 3 and \
                    QPolygonF(pts).boundingRect().height() > 3:
                self.lassoed.emit(pts)
            return
        if self._band is not None and self._band.isVisible():
            self._band.hide()
            r = QRectF(self._press_at, ev.localPos()).normalized()
            self.unsetCursor()
            if r.width() > 3 and r.height() > 3:
                self.boxed.emit(r)
            return
        p0 = getattr(self, '_press_at', None)
        if (ev.button() == Qt.LeftButton and p0 is not None and not (ev.modifiers() & Qt.ShiftModifier)
                and (ev.localPos() - p0).manhattanLength() <= 4):
            from PyQt5.QtWidgets import QApplication
            self._click_at = ev.localPos()
            self._click_timer.start(QApplication.doubleClickInterval())
        self._show_marker(False)
        self.unsetCursor()
        self.update()

    def mouseDoubleClickEvent(self, ev):
        self._click_timer.stop()
        p = self.point_at(ev.localPos())
        if p is None:
            return
        # centrar en ese punto y acercarse un poco: ahora se gira alrededor de él
        self._animate(QVector3D(*p), self.opts['distance'] * 0.7, None, None)
        QTimer.singleShot(180, lambda: self._show_marker(True))
        QTimer.singleShot(900, lambda: self._show_marker(False))

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y() or ev.angleDelta().x()
        self._zoom(0.999 ** delta, ev.posF() if hasattr(ev, 'posF') else QPointF(ev.pos()))

    def keyPressEvent(self, ev):
        k = ev.key()
        step = 25
        moves = {Qt.Key_Left: (step, 0), Qt.Key_Right: (-step, 0), Qt.Key_Up: (0, step), Qt.Key_Down: (0, -step)}
        if k in moves:
            self.pan(*moves[k], 0, relative='view')
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self._zoom(0.8)
        elif k == Qt.Key_Minus:
            self._zoom(1.25)
        elif k in (Qt.Key_R, Qt.Key_Home):
            self.go_home()
        elif k == Qt.Key_F:
            self.fit()
        elif k == Qt.Key_Escape:
            self.escaped.emit()
        else:
            super().keyPressEvent(ev)


class DevLegend(QWidget):
    """Barra de colores del mapa de desviación: azul falta material, blanco coincide, rojo sobra; gris sin escaneo."""

    def __init__(self, colors):
        super().__init__()
        self.C = colors
        self.lim = 0.5
        self.setMinimumSize(260, 30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_limit(self, lim):
        self.lim = lim
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        gw = w - 74
        g = QLinearGradient(0, 0, gw, 0)
        g.setColorAt(0, QColor(40, 90, 200))
        g.setColorAt(0.5, QColor(238, 238, 232))
        g.setColorAt(1, QColor(210, 50, 40))
        p.setPen(QColor(self.C['line']))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(0.5, 1.5, gw, 10), 3, 3)
        p.setBrush(QColor(150, 150, 150))
        p.drawRoundedRect(QRectF(gw + 10, 1.5, 14, 10), 2, 2)
        p.setPen(QColor(self.C['muted']))
        f = p.font()
        f.setPointSizeF(7.6)
        p.setFont(f)
        L = f'{self.lim:g}'.replace('.', ',')
        p.drawText(QRectF(0, 13, gw, 16), Qt.AlignLeft | Qt.AlignVCenter, f'−{L} mm falta')
        p.drawText(QRectF(0, 13, gw, 16), Qt.AlignHCenter | Qt.AlignVCenter, '0')
        p.drawText(QRectF(0, 13, gw, 16), Qt.AlignRight | Qt.AlignVCenter, f'sobra +{L} mm')
        p.drawText(QRectF(gw + 2, 13, 72, 16), Qt.AlignLeft | Qt.AlignVCenter, 'sin escaneo')


class Viewer3D(QWidget):
    """Vista 3D con barra de vistas y opciones encima y la ayuda de navegación debajo."""
    group_clicked = pyqtSignal(int)      # nube: clic sobre un punto de un grupo suelto (número de grupo)
    crop_drawn = pyqtSignal(object, object, str)    # nube: matriz 4×4 de la vista, polígono −1…1 y qué hacer
                                                    # ('dentro', 'fuera'; 'detalle' o 'detalle-borrar': zona de
                                                    # detalle, no recorta)
    zone_brushed = pyqtSignal(object, float, bool)  # nube: trazo del pincel (centros en la vista, radio, borrar)
    box_requested = pyqtSignal(str)                 # nube: «caja con medidas…» elegida ('dentro'/'fuera')
    part_picked = pyqtSignal(object, str)           # nube: clic en una parte (punto en la vista, 'dentro'/'fuera')
    crop_undo = pyqtSignal()
    crop_clear = pyqtSignal()
    HINT = ('<b>Izq.</b> girar · <b>Der.</b>, <b>central</b> o <b>Mayús+izq.</b> desplazar · <b>Rueda</b> zoom hacia '
            'el cursor · <b>Doble clic</b> girar alrededor de ese punto · <b>R</b> vista inicial · <b>F</b> encuadrar')

    def __init__(self, bg, kind, colors):
        super().__init__()
        self.kind = kind                         # 'nube' | 'malla'
        self.C = colors
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget()
        bar.setObjectName('viewBar')
        bar.setStyleSheet(f'#viewBar {{ background: white; border-bottom: 1px solid {colors["line"]}; }} '
                          f'#viewBar QToolButton {{ padding: 3px 9px; border: 1px solid transparent; '
                          f'border-radius: 5px; color: {colors["navy"]}; }} '
                          f'#viewBar QToolButton:hover {{ background: #eef5f8; border-color: #d3e4ea; }}')
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 4, 8, 4)
        bl.setSpacing(2)
        self.view = NavView(bg)
        views = [('Inicial', 'Vista inicial (R)', self.view.go_home),
                 ('Encuadrar', 'Ver toda la pieza sin cambiar la orientación (F)', self.view.fit)]
        if kind == 'malla':
            views += [('Relieve de frente', 'Mirar el relieve de frente, desde arriba', lambda: self.view.view_from(90, -90)),
                      ('Rasante', 'Vista casi tumbada: resalta el relieve', lambda: self.view.view_from(18, -90)),
                      ('Perfil', 'De lado, para ver alturas y base', lambda: self.view.view_from(0, 0))]
        else:
            views += [('Arriba', 'Desde +Z', lambda: self.view.view_from(90, -90)),
                      ('Frente', 'Desde −Y', lambda: self.view.view_from(0, -90)),
                      ('Lado', 'Desde +X', lambda: self.view.view_from(0, 0))]
        for text, tip, fn in views:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addSpacing(8)
        ms = QToolButton()
        ms.setText('📏 Medir')
        ms.setCheckable(True)
        ms.setCursor(Qt.PointingHandCursor)
        ms.setToolTip('Medir la distancia entre dos puntos: con esto marcado, haz clic en un punto y después en '
                      'otro.\n' + ('En la nube, en mm reales del original.' if kind == 'nube' else
                                     'En la pieza, en mm de la pieza impresa y, al lado, los mm reales del original.')
                      + '\nUn tercer clic empieza otra medida; Esc la borra. Se sigue pudiendo girar y hacer zoom.')
        ms.toggled.connect(self._measure_toggled)
        bl.addWidget(ms)
        self.measure_btn = ms
        self.measure_lbl = QLabel('')
        self.measure_lbl.setStyleSheet(f'color: {colors["navy"]}; font-weight: 600; padding-left: 4px;')
        bl.addWidget(self.measure_lbl)
        self.measure = []                        # puntos de la medida (0, 1 o 2), en coordenadas de la vista
        self.measure_scale = None                # malla: factor nube -> pieza (para dar también los mm reales)
        self._measure_items = []
        self.extra_items = lambda: []
        self.pick_cb = None                 # función que recibe el punto del próximo clic (None = clic normal)            # objetos propios que se vuelven a dibujar tras cada redibujado
        bl.addStretch(1)
        self.opts = {}
        if kind == 'nube':
            cr = QToolButton()
            cr.setText('✂ Recortar')
            cr.setCheckable(True)
            cr.setCursor(Qt.PointingHandCursor)
            cr.setToolTip('Recortar la nube: con esto marcado, arrastra un rectángulo o dibuja un lazo en la vista.\n'
                          'Se recorta en la dirección en que miras: gira la vista para que lo que quieres quede\n'
                          'separado de lo demás (p. ej. desde arriba para una franja del terreno). Puedes hacer\n'
                          'varios seguidos desde vistas distintas; lo recortado se ve en gris claro.\n'
                          'Para girar sin soltar la herramienta: Ctrl + botón izquierdo.')
            bl.addWidget(cr)
            self.crop_btn = cr
            sh = QComboBox()
            sh.addItems(['▭ rectángulo', '◌ lazo', '👆 una parte (clic)', '▣ caja con medidas…'])
            sh.setToolTip('Forma: rectángulo (arrastrar de una esquina a otra) o lazo (dibujar el contorno a mano\n'
                          'alzada, con el botón pulsado; al soltar se cierra).\n'
                          'Una parte: clic sobre una pieza separada del resto (una estatua entre varias, un\n'
                          'fragmento suelto): se deja solo ella o se quita, todo lo que está unido a ese punto.\n'
                          'Con esta, el botón izquierdo sigue girando la vista.\n'
                          'Caja con medidas: x, y, z mínimos y máximos en mm de la nube, para recortar con números\n'
                          'exactos o repetir el mismo recorte en otra nube con las mismas coordenadas.')
            bl.addWidget(sh)
            self.crop_shape = sh
            cm = QComboBox()
            cm.addItems(['dejar lo de dentro', 'quitar lo de dentro'])
            cm.setToolTip('Qué hacer con los puntos que quedan dentro: dejarlos (y quitar el resto) o quitarlos')
            bl.addWidget(cm)
            self.crop_how = cm
            zb = QToolButton()
            zb.setText('🖌 Zona de detalle')
            zb.setCheckable(True)
            zb.setCursor(Qt.PointingHandCursor)
            zb.setToolTip('Marcar dónde se quiere más resolución (tipos con Poisson: objeto, terreno, bloque, 3d).\n'
                          'No se quita nada: lo marcado se tiñe de naranja y ahí la pieza se hace con la celda de\n'
                          '«Resolución de las zonas»; en el resto, con la normal.\n'
                          'Pincel: pinta la superficie que tocas. Lazo y rectángulo: marcan todo lo que queda\n'
                          'dentro, también lo de detrás en la dirección en que miras.\n'
                          'Para girar sin soltar la herramienta: Ctrl + botón izquierdo.')
            bl.addWidget(zb)
            self.zone_btn = zb
            zt = QComboBox()
            zt.addItems(['🖌 pincel', '◌ lazo', '▭ rectángulo'])
            zt.setToolTip('Pincel: arrastra con el botón izquierdo sobre la pieza y se pinta lo que toca el círculo.\n'
                          'Lazo: dibuja el contorno a mano alzada. Rectángulo: arrastra de una esquina a otra.')
            bl.addWidget(zt)
            self.zone_tool = zt
            zs = QComboBox()
            zs.addItems(list(self.BRUSH))
            zs.setCurrentText('mediano')
            zs.setToolTip('Tamaño del pincel en la pantalla (el círculo que sigue al cursor). Con zoom, el mismo\n'
                          'pincel pinta un trozo más pequeño de la pieza.')
            bl.addWidget(zs)
            self.zone_size = zs
            ze = QComboBox()
            ze.addItems(['pintar', 'borrar'])
            ze.setToolTip('Pintar añade a las zonas de detalle; borrar quita de ellas lo que toques\n'
                          '(también con el lazo o el rectángulo)')
            bl.addWidget(ze)
            self.zone_erase = ze
            un = QToolButton()
            un.setText('Deshacer')
            un.setToolTip('Deshacer el último recorte, trazo o zona de detalle')
            un.clicked.connect(self.crop_undo.emit)
            bl.addWidget(un)
            cl = QToolButton()
            cl.setText('Quitar todo')
            cl.setToolTip('Quitar todos los recortes y las zonas de detalle: vuelve la nube entera')
            cl.clicked.connect(self.crop_clear.emit)
            bl.addWidget(cl)
            self.crop_tools = [sh, cm, un, cl, zt, zs, ze]
            self.mark_tools = [un, cl]
            self._shape_prev = 0
            sh.currentIndexChanged.connect(self._shape_changed)
            cr.toggled.connect(lambda on: self._tool_toggled(cr, on))
            zb.toggled.connect(lambda on: self._tool_toggled(zb, on))
            for c in (sh, zt, zs, ze):
                c.currentIndexChanged.connect(lambda *_: self._apply_tool())
            self._apply_tool()
            bl.addSpacing(10)
            bl.addWidget(QLabel('Puntos'))
            cb = QComboBox()
            cb.addItems(['1 px', '2 px', '3 px', '5 px'])
            cb.setCurrentText('2 px')
            cb.setToolTip('Tamaño de los puntos en pantalla')
            bl.addWidget(cb)
            self.opts['size'] = cb
        else:
            bl.addWidget(QLabel('Luz'))
            cb = QComboBox()
            cb.addItems(list(LIGHTS))
            cb.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            cb.setToolTip('Rasante móvil: luz baja desde la izquierda de la pantalla; al girar, la luz\n'
                          'gira contigo, así que siempre marca el relieve de lo que miras.\n'
                          'Frontal: ilumina de frente lo que miras; ninguna zona queda en sombra.\n'
                          'Rasante fija: la luz está fija en la pieza (como una foto con luz rasante).')
            bl.addWidget(cb)
            self.opts['light'] = cb
            ch = QCheckBox('Suave')
            ch.setToolTip('Sombreado suave: interpola la luz entre triángulos vecinos, como se verá la pieza\n'
                          'impresa (las facetas de la simplificación son de centésimas de mm). Sin marcar se\n'
                          've cada triángulo plano. Las aristas vivas (bordes de la base) se ven algo romas.')
            bl.addWidget(ch)
            self.opts['smooth'] = ch
            ch = QCheckBox('Triángulos')
            ch.setToolTip('Dibujar las aristas de los triángulos (para ver la simplificación)')
            bl.addWidget(ch)
            self.opts['edges'] = ch
        lay.addWidget(bar)
        self.dev = None
        self.zone = None                         # malla: vértices de las zonas de detalle
        if kind == 'malla':
            lay.addWidget(self._tools_bar(colors))
        lay.addWidget(self.view, 1)
        hint = QLabel(self.HINT + (' · <b>Clic</b> en un punto rojo: conservar su grupo' if kind == 'nube' else ''))
        hint.setStyleSheet(f'background: white; color: {colors["muted"]}; font-size: 8.5pt; padding: 3px 8px; '
                           f'border-top: 1px solid {colors["line"]};')
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.hint = hint
        self._data = None
        for w in self.opts.values():
            (getattr(w, 'currentTextChanged', None) or w.toggled).connect(lambda *_: self._redraw())
        self._cut_timer = QTimer(self)
        self._cut_timer.setSingleShot(True)
        self._cut_timer.setInterval(60)
        self._cut_timer.timeout.connect(lambda: self._redraw())
        self.loose = None
        self.view.clicked.connect(self._clicked)
        self.view.boxed.connect(self._boxed)
        self.view.lassoed.connect(self._lassoed)
        self.view.brushed.connect(self._brushed)
        self.view.escaped.connect(self.clear_measure)

    def _tools_bar(self, colors):
        """Segunda barra de la malla: color (piedra / desviación) y corte."""
        bar = QWidget()
        bar.setObjectName('toolBar')
        bar.setStyleSheet(f'#toolBar {{ background: #fbfcfd; border-bottom: 1px solid {colors["line"]}; }}')
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 3, 10, 3)
        bl.setSpacing(6)
        bl.addWidget(QLabel('Color'))
        cb = QComboBox()
        cb.addItems(['piedra', 'desviación'])
        cb.setToolTip('Desviación: cuánto se separa la pieza del escaneo en cada punto (mm de la pieza).\n'
                      'Azul: a la pieza le falta material (aristas redondeadas, detalle perdido).\n'
                      'Rojo: le sobra (huecos rellenados, alisado). Blanco: coincide.\n'
                      'Gris: no hay escaneo cerca (base, trasera, paredes y tapas que añade el programa).\n'
                      'Zonas de detalle (si la pieza las tiene): en naranja la parte hecha con más resolución;\n'
                      'con «Triángulos» se ve la diferencia de tamaño de los triángulos.')
        bl.addWidget(cb)
        self.color_cb = cb
        sc = QComboBox()
        sc.addItems(['escala auto', '± 0,05 mm', '± 0,1 mm', '± 0,25 mm', '± 0,5 mm', '± 1 mm', '± 2 mm', '± 5 mm'])
        sc.setToolTip('Desviación que corresponde al azul y al rojo intensos.\n'
                      'Auto: la que no supera el 95 % de la superficie escaneada.')
        bl.addWidget(sc)
        self.scale_cb = sc
        self.legend = DevLegend(colors)
        bl.addWidget(self.legend, 1)
        self.dev_info = QLabel('')
        self.dev_info.setStyleSheet(f'color: {colors["muted"]}; font-size: 8pt;')
        bl.addWidget(self.dev_info)
        self.zone_info = QLabel('')
        self.zone_info.setStyleSheet(f'color: {colors["muted"]}; font-size: 8pt;')
        bl.addWidget(self.zone_info)
        self.dev_widgets = [sc, self.legend, self.dev_info]
        self._spacer = QWidget()
        self._spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bl.addWidget(self._spacer, 1)
        ch = QCheckBox('Corte')
        ch.setToolTip('Cortar la pieza con un plano para ver su interior: grosor de la pared en las piezas\n'
                      'huecas, socavados, el perfil del relieve… El contorno del corte se dibuja en rojo.')
        bl.addWidget(ch)
        self.cut_cb = ch
        ax = QComboBox()
        ax.addItems(['X', 'Y', 'Z'])
        ax.setCurrentText('Y')
        ax.setToolTip('Eje perpendicular al plano de corte')
        bl.addWidget(ax)
        self.cut_axis = ax
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 1000)
        sl.setValue(500)
        sl.setFixedWidth(150)
        sl.setToolTip('Posición del plano de corte')
        bl.addWidget(sl)
        self.cut_slider = sl
        self.cut_lbl = QLabel('')
        self.cut_lbl.setMinimumWidth(62)
        self.cut_lbl.setStyleSheet(f'color: {colors["muted"]}; font-size: 8pt;')
        bl.addWidget(self.cut_lbl)
        cb.currentTextChanged.connect(lambda *_: self._redraw())
        sc.currentTextChanged.connect(lambda *_: self._redraw())
        ch.toggled.connect(lambda *_: self._redraw())
        ax.currentTextChanged.connect(lambda *_: self._redraw())
        sl.valueChanged.connect(lambda *_: self._cut_timer.start())
        self._sync_tools()
        return bar

    ZONE_COLOR = (0.93, 0.48, 0.15)

    def _sync_tools(self):
        have = self.dev is not None and np.isfinite(self.dev).any()
        zone = getattr(self, 'zone', None) is not None and self.zone.any()
        items = ['piedra'] + (['desviación'] if have else []) + (['zonas de detalle'] if zone else [])
        cur = self.color_cb.currentText()
        if [self.color_cb.itemText(i) for i in range(self.color_cb.count())] != items:
            self.color_cb.blockSignals(True)
            self.color_cb.clear()
            self.color_cb.addItems(items)
            self.color_cb.setCurrentText(cur if cur in items else 'piedra')
            self.color_cb.blockSignals(False)
        self.color_cb.setEnabled(len(items) > 1)
        self.zone_info.setVisible(zone and self.color_cb.currentText() == 'zonas de detalle')
        on = have and self.color_cb.currentText() == 'desviación'
        for w in self.dev_widgets:
            w.setVisible(on)
        self._spacer.setVisible(not on)
        for w in (self.cut_axis, self.cut_slider, self.cut_lbl):
            w.setEnabled(self.cut_cb.isChecked())

    def dev_limit(self):
        t = self.scale_cb.currentText()
        if t.startswith('escala'):
            return self.dev_auto
        return float(t.strip('± mm').replace(',', '.'))

    # ------------------------------------------------------------------ contenido
    def clear(self):
        self._data = None
        self.view.clear_scene()

    def show_cloud(self, pts, cols, loose=None, home=True):
        """pts (N,3) ya centrados; cols (N,3) en 0–1. loose: (inicio, grupos) si los puntos desde «inicio» son de
        grupos sueltos (grupos: número de grupo de cada uno), para poder elegirlos con un clic.
        home=False: mantener la cámara (se ha cambiado solo el color de algún grupo)."""
        self._data = ('nube', pts.astype(np.float32), cols)
        self.loose = loose
        if home:
            self.measure = []
            self.measure_lbl.setText('clic en el primer punto' if self.measure_btn.isChecked() else '')
        if not home and self.view.home is not None:
            self._redraw()
            return
        ext = float(np.ptp(pts, 0).max())
        self._redraw(home=dict(center=(0, 0, 0), distance=ext * 1.5, elevation=30, azimuth=45))

    # ------------------------------------------------------------------ medir
    def _measure_toggled(self, on):
        if on and self.kind == 'nube':
            for b in (self.crop_btn, self.zone_btn):
                if b.isChecked():
                    b.setChecked(False)
        if on:
            self.view.setCursor(Qt.CrossCursor)
            self.measure_lbl.setText('clic en el primer punto')
        else:
            self.view.unsetCursor()
            self.clear_measure()

    def clear_measure(self):
        self.measure = []
        if self.measure_btn.isChecked():
            self.measure_lbl.setText('clic en el primer punto')
        else:
            self.measure_lbl.setText('')
        self._draw_measure()

    def _measure_click(self, pos):
        p = self.view.point_at(pos)
        if p is None:
            self.measure_lbl.setText('ahí no hay ningún punto: haz clic sobre la pieza')
            return
        if len(self.measure) >= 2:
            self.measure = []
        self.measure.append(p)
        self._draw_measure()
        if len(self.measure) == 1:
            self.measure_lbl.setText('clic en el segundo punto')
            return
        a, b = self.measure
        d = b - a
        dist = float(np.linalg.norm(d))
        txt = f'{fmt_mm(dist)} mm'
        if self.kind == 'malla':
            txt += ' en la pieza'
            s = self.measure_scale
            if s and abs(s - 1) > 1e-6:
                txt += f' · ≈ {fmt_mm(dist / s)} mm reales'
        txt += f'   (Δx {fmt_mm(abs(d[0]))} · Δy {fmt_mm(abs(d[1]))} · Δz {fmt_mm(abs(d[2]))})'
        self.measure_lbl.setText(txt)

    def _draw_measure(self):
        v = self.view
        for it in self._measure_items:
            try:
                v.removeItem(it)
            except ValueError:
                pass
        self._measure_items = []
        if not self.measure:
            v.update()
            return
        P = np.array(self.measure, np.float32)
        pts = gl.GLScatterPlotItem(pos=P, size=11, color=(0.88, 0.48, 0.25, 1.0), pxMode=True)
        pts.setGLOptions(MARKER_GL)
        pts.setDepthValue(20)
        items = [pts]
        if len(P) == 2:
            li = gl.GLLinePlotItem(pos=P, mode='lines', color=(0.88, 0.48, 0.25, 1.0), width=2.5, antialias=True)
            li.setGLOptions(MARKER_GL)
            li.setDepthValue(19)
            items.append(li)
        for it in items:
            v.addItem(it)
        self._measure_items = items

    def set_crop_state(self, n):
        """Número de recortes y zonas hechos (para activar Deshacer / Quitar todo)."""
        for w in self.mark_tools:
            w.setEnabled(n > 0)

    def hide_marks(self):
        """Sin herramientas de recorte ni de zonas (vistas que solo enseñan una nube)."""
        for w in [self.crop_btn, self.zone_btn] + self.crop_tools:
            w.hide()
        self._hidden_marks = True

    CROP_HOW = ('dentro', 'fuera')
    BRUSH = {'fino': 6, 'mediano': 14, 'grueso': 30, 'muy grueso': 60}     # radio en píxeles

    def _how(self):
        """Qué hacer con lo dibujado: 'dentro' o 'fuera' (recorte), 'detalle' o 'detalle-borrar' (zonas)."""
        if self.zone_btn.isChecked():
            return 'detalle-borrar' if self.zone_erase.currentIndex() else 'detalle'
        return self.CROP_HOW[self.crop_how.currentIndex()]

    def _shape_changed(self, i):
        """«Caja con medidas…» no es una forma de dibujar: abre la ventana de la caja y vuelve a la forma de antes."""
        if i == 3:
            self.crop_shape.blockSignals(True)
            self.crop_shape.setCurrentIndex(self._shape_prev)
            self.crop_shape.blockSignals(False)
            self._apply_tool()
            self.box_requested.emit(self.CROP_HOW[self.crop_how.currentIndex()])
        else:
            self._shape_prev = i

    def _tool_toggled(self, btn, on):
        """Recortar, Zona de detalle y Medir no pueden estar marcados a la vez."""
        if on:
            for b in (self.crop_btn, self.zone_btn, self.measure_btn):
                if b is not btn and b.isChecked():
                    b.setChecked(False)
        self._apply_tool()

    def _apply_tool(self):
        """Estado de la vista y opciones visibles según la herramienta marcada."""
        crop, zone = self.crop_btn.isChecked(), self.zone_btn.isChecked()
        brush = zone and self.zone_tool.currentIndex() == 0
        if not getattr(self, '_hidden_marks', False):
            for w in (self.crop_shape, self.crop_how):
                w.setVisible(crop)
            for w in (self.zone_tool, self.zone_erase):
                w.setVisible(zone)
            self.zone_size.setVisible(brush)
        v = self.view
        part = crop and self.crop_shape.currentIndex() == 2
        v.crop_mode = (crop and not part) or (zone and not brush)
        if crop:
            v.crop_shape = ('rect', 'lazo', 'parte', 'rect')[self.crop_shape.currentIndex()]
        elif zone:
            v.crop_shape = 'lazo' if self.zone_tool.currentIndex() == 1 else 'rect'
        erase = zone and self.zone_erase.currentIndex() == 1
        v.set_brush(self.BRUSH[self.zone_size.currentText()] if brush else 0, erase)
        v.set_draw_erase(erase)
        if part:
            v.setCursor(Qt.PointingHandCursor)
        elif crop or zone:
            v.setCursor(Qt.CrossCursor)
        elif not self.measure_btn.isChecked():
            v.unsetCursor()

    def _brushed(self, pts):
        """Trazo del pincel -> centros en la escena y radio, para la ventana."""
        v = self.view
        if v.pick is None or not len(v.pick):
            return
        p00 = float(v.projectionMatrix().data()[0])
        r_px = self.BRUSH[self.zone_size.currentText()]
        cen, r = brush_centers(v.pick, v._mvp(), p00, (max(v.width(), 1), max(v.height(), 1)),
                               [[q.x(), q.y()] for q in pts], r_px)
        if len(cen):
            self.zone_brushed.emit(cen, r, self.zone_erase.currentIndex() == 1)

    def _lassoed(self, pts):
        """Lazo dibujado -> matriz de la vista y polígono en coordenadas −1…1."""
        w, h = max(self.view.width(), 1), max(self.view.height(), 1)
        poly = [[2 * q.x() / w - 1, 1 - 2 * q.y() / h] for q in pts]
        self.crop_drawn.emit(self.view._mvp(), poly, self._how())

    def _boxed(self, r):
        """Rectángulo dibujado en modo recorte -> matriz de la vista y polígono en coordenadas −1…1."""
        w, h = max(self.view.width(), 1), max(self.view.height(), 1)
        xs = [2 * r.left() / w - 1, 2 * r.right() / w - 1]
        ys = [1 - 2 * r.bottom() / h, 1 - 2 * r.top() / h]
        poly = [[xs[0], ys[0]], [xs[1], ys[0]], [xs[1], ys[1]], [xs[0], ys[1]]]
        self.crop_drawn.emit(self.view._mvp(), poly, self._how())

    def redraw_extra(self):
        """Volver a dibujar solo los marcadores de extra_items (sin tocar la cámara)."""
        if self._data is not None:
            self._redraw()

    def _clicked(self, pos):
        if self.pick_cb is not None:          # alguien espera un punto (p. ej. el plano de la cara)
            self.pick_cb(self.view.point_at(pos))
            return
        if self.measure_btn.isChecked():
            self._measure_click(pos)
            return
        if self.kind == 'nube' and self.crop_btn.isChecked() and self.crop_shape.currentIndex() == 2:
            p = self.view.point_at(pos)
            if p is not None:
                self.part_picked.emit(p, self.CROP_HOW[self.crop_how.currentIndex()])
            return
        if self.view.crop_mode or self.view.brush_r:
            return
        """Clic en la nube: si cae sobre un punto de un grupo suelto, avisar con su número."""
        if self.loose is None or self._data is None or self._data[0] != 'nube':
            return
        start, grp = self.loose
        P = self._data[1][start:]
        if not len(P):
            return
        sx, sy, nz, ok = self.view.project(P)
        for r in (8, 16):
            idx = np.flatnonzero(ok & ((sx - pos.x()) ** 2 + (sy - pos.y()) ** 2 <= r * r))
            if len(idx):
                d2 = (sx[idx] - pos.x()) ** 2 + (sy[idx] - pos.y()) ** 2
                self.group_clicked.emit(int(grp[idx[np.argmin(d2 + 1e3 * (nz[idx] - nz[idx].min()))]]))
                return

    def show_mesh(self, verts, faces, smooth=None, dev=None, dev_auto=0.5, dev_text='', scale=None, zone=None,
                  zone_text=''):
        """verts ya colocados (base en z=0, centrada en XY). smooth: marcar o no «Suave» (None = dejarlo).
        dev: desviación por vértice (mm, NaN sin escaneo) para el color «desviación».
        scale: factor nube -> pieza, para dar también los mm reales al medir.
        zone: vértices de las zonas de detalle (bool), para el color «zonas de detalle»; con zonas nuevas se elige
        ese color, para que se vean nada más generar."""
        new_zone = zone is not None and zone.any() and getattr(self, 'zone', None) is None
        self.dev, self.dev_auto = dev, dev_auto
        self.zone = zone if zone is not None and zone.any() else None
        self.zone_info.setText(zone_text)
        self.measure_scale = scale
        self.measure = []
        self.measure_lbl.setText('clic en el primer punto' if self.measure_btn.isChecked() else '')
        self.dev_info.setText(dev_text)
        self._sync_tools()
        if new_zone:
            self.color_cb.blockSignals(True)
            self.color_cb.setCurrentText('zonas de detalle')
            self.color_cb.blockSignals(False)
            self._sync_tools()
        if smooth is not None:
            self.opts['smooth'].blockSignals(True)
            self.opts['smooth'].setChecked(smooth)
            self.opts['smooth'].blockSignals(False)
        self._data = ('malla', verts.astype(np.float32), faces)
        zmax = float(verts[:, 2].max())
        exy = float(np.ptp(verts[:, :2], 0).max())
        tall = zmax > 0.5 * exy               # objeto de pie (modo objeto): vista casi de lado, como una foto
        self._redraw(home=dict(center=(0, 0, zmax / 2), distance=max(exy, 1.3 * zmax) * (1.25 if tall else 1.05),
                               elevation=15 if tall else 40, azimuth=-90))

    def _redraw(self, home=None):
        if self._data is None:
            return
        v = self.view
        keep = None if home else (QVector3D(v.opts['center']), v.opts['distance'], v.opts['elevation'],
                                  v.opts['azimuth'], v.home)
        moving = home is None and v._anim is not None and v._anim.isActive()
        if moving:                                 # redibujo a mitad de una animación: acabarla hacia su destino
            keep = (*v._target, v.home)
        v.clear_scene()
        kind, P, extra = self._data
        if kind == 'nube':
            size = float(self.opts['size'].currentText().split()[0])
            col = np.c_[extra, np.ones(len(extra))].astype(np.float32)
            v.addItem(gl.GLScatterPlotItem(pos=P, color=col, size=size, pxMode=True, glOptions='opaque'))
            pick = P
        else:
            faces = extra
            self._sync_tools()
            shader = LIGHTS[self.opts['light'].currentText()]
            stone = (0.86, 0.83, 0.79, 1.0)
            smooth = self.opts['smooth'].isChecked()
            vcol = None
            if self.color_cb.currentText() == 'desviación' and self.dev is not None:
                import desviacion
                lim = self.dev_limit()
                self.legend.set_limit(lim)
                vcol = np.c_[desviacion.colors(self.dev, lim) / 255.0, np.ones(len(P))].astype(np.float32)
            elif self.color_cb.currentText() == 'zonas de detalle' and getattr(self, 'zone', None) is not None:
                vcol = np.tile(np.array(stone, np.float32), (len(P), 1))
                vcol[self.zone, :3] = self.ZONE_COLOR
            line = None
            if self.cut_cb.isChecked():
                a = 'XYZ'.index(self.cut_axis.currentText())
                lo, hi = float(P[:, a].min()), float(P[:, a].max())
                c = lo + (hi - lo) * self.cut_slider.value() / 1000
                self.cut_lbl.setText(f'{self.cut_axis.currentText()} = {c - lo:.1f} mm')
                line = section_lines(P, faces, a, c)
                P, faces, vcol = clip_mesh(P, faces, a, c, vcol)     # se ve lo que queda por debajo del plano
            if shader:
                if vcol is not None:
                    md = gl.MeshData(vertexes=P, faces=faces, vertexColors=vcol)
                    item = gl.GLMeshItem(meshdata=md, smooth=True, shader=shader, glOptions='opaque')
                else:
                    md = gl.MeshData(vertexes=P, faces=faces)
                    item = gl.GLMeshItem(meshdata=md, smooth=smooth, shader=shader, color=stone, glOptions='opaque')
            else:
                md = gl.MeshData(vertexes=P, faces=faces)
                nrm = md.vertexNormals() if (smooth or vcol is not None) else md.faceNormals()
                nrm = nrm / (np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12)
                light = np.array([-0.5, -0.6, 0.8])
                light /= np.linalg.norm(light)
                shade = 0.3 + 0.7 * np.clip(nrm @ light, 0, 1)
                if vcol is not None:
                    cols = np.c_[shade[:, None] * vcol[:, :3], np.ones_like(shade)]
                    key = 'vertexColors'
                else:
                    cols = np.c_[shade * stone[0], shade * stone[1], shade * stone[2], np.ones_like(shade)]
                    key = 'vertexColors' if smooth else 'faceColors'
                md = gl.MeshData(vertexes=P, faces=faces, **{key: cols.astype(np.float32)})
                item = gl.GLMeshItem(meshdata=md, smooth=key == 'vertexColors', shader=None, glOptions='opaque')
            if self.opts['edges'].isChecked():
                item.opts['drawEdges'] = True
                item.opts['edgeColor'] = (0.16, 0.30, 0.38, 0.55)
            v.addItem(item)
            if line is not None and len(line):
                li = gl.GLLinePlotItem(pos=line, mode='lines', color=(0.85, 0.12, 0.10, 1.0), width=2.5,
                                       antialias=True)
                li.setGLOptions({GL.GL_DEPTH_TEST: True, GL.GL_BLEND: True, GL.GL_LINE_SMOOTH: True,
                                 'glBlendFunc': (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)})
                li.setDepthValue(5)
                v.addItem(li)
            # para buscar bajo el cursor: vértices y centros de los triángulos (los grandes y planos tienen
            # pocos vértices)
            pick = np.concatenate([P, P[faces].mean(1)])
        self._measure_items = []
        if home is not None:
            v.set_scene(pick, home)
        else:
            v.set_scene(pick, keep[4])
            if self.view._anim:
                self.view._anim.stop()
            v.opts['center'], v.opts['distance'], v.opts['elevation'], v.opts['azimuth'] = keep[:4]
            v.update()
        self._draw_measure()                      # la medida sigue a la vista al cambiar luz, corte, etc.
        for it in self.extra_items():
            v.addItem(it)
