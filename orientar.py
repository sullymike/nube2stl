"""Vista de orientación de nube2stl: la pieza como quedará (giro, espejo, forma y marco) en tiempo real, con
cuadrícula de guías en mm y una línea de referencia que se dibuja para poner horizontal o vertical algo que
debe quedar recto (un borde, un renglón, el marco tallado en la piedra).

En modo objeto muestra el alzado frontal del objeto tal como se imprimirá (de pie sobre la cama) y la altura
del corte de la base.

Los cálculos están en relief.OrientPreview; aquí solo se dibuja y se recoge el ratón.
"""
import numpy as np
from matplotlib.colors import LightSource
from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt5.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget


def nice_step(span, n=10):
    raw = span / n
    for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
        if s >= raw:
            return s
    return 1000


class OrientView(QWidget):
    lineDrawn = pyqtSignal(float, float)        # dx, dy en mm de la pieza (y hacia arriba)

    def __init__(self, colors, cmap):
        super().__init__()
        self.C, self.cmap = colors, cmap
        self.setMouseTracking(True)
        self.setMinimumSize(300, 240)
        self.img = None
        self.d = None
        self.grid_on = True
        self.step = 0                            # 0 = automática
        self.line = None                         # (p0, p1) en mm mientras se dibuja
        self.zoom, self.off = 1.0, QPointF(0, 0)
        self._pan = None
        self.message = 'Abre una nube de puntos para ver la orientación.'

    # ------------------------------------------------------------------ datos
    def set_data(self, d, layer=0.0):
        """layer > 0: alturas en capas de ese grosor, como las hará la impresora (lo que no llega a una capa no se
        ve)."""
        self.d = d
        if d is None:
            self.img = None
            self.update()
            return
        h, piece, ring = d['h'], d['piece'], d['ring']
        if layer > 0:
            h = np.floor(h / layer + 0.5) * layer
        z = np.where(np.isnan(h), 0.0, h)
        ls = LightSource(azdeg=315, altdeg=35)
        lo, hi = np.nanpercentile(h, [2, 99.5]) if np.isfinite(h).any() else (0, 1)
        rgb = ls.shade(z, cmap=self.cmap, vert_exag=2.5, dx=d['cell'], dy=d['cell'], blend_mode='soft',
                       vmin=lo, vmax=max(hi, lo + 1e-6))[..., :3]
        rgba = np.zeros(h.shape + (4,), np.uint8)
        rgba[..., :3] = (rgb * 255).astype(np.uint8)
        rgba[..., 3] = np.where(piece, 255, 0)
        if ring.any():
            rgba[ring] = (214, 196, 170, 255)              # marco: color de piedra lisa
        plate = piece & np.isnan(h) & ~ring                 # placa rectangular fuera del contorno escaneado
        rgba[plate] = (226, 214, 196, 255)
        rgba = np.ascontiguousarray(np.flipud(rgba))        # fila 0 = abajo
        self.img = QImage(rgba.data, rgba.shape[1], rgba.shape[0], 4 * rgba.shape[1], QImage.Format_RGBA8888).copy()
        self.update()

    def reset_view(self):
        self.zoom, self.off = 1.0, QPointF(0, 0)
        self.update()

    # ------------------------------------------------------------------ transformación mm <-> pantalla
    def _xf(self):
        W, H = self.d['size']
        m = 34
        k = min((self.width() - 2 * m) / W, (self.height() - 2 * m) / H) * self.zoom
        ox = (self.width() - W * k) / 2 + self.off.x()
        oy = (self.height() + H * k) / 2 + self.off.y()
        return k, ox, oy

    def to_screen(self, x, y):
        k, ox, oy = self._xf()
        return QPointF(ox + x * k, oy - y * k)

    def to_mm(self, p):
        k, ox, oy = self._xf()
        return QPointF((p.x() - ox) / k, (oy - p.y()) / k)

    # ------------------------------------------------------------------ dibujo
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(self.C['view']))
        if self.d is None or self.img is None:
            p.setPen(QColor(self.C['muted']))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        W, H = self.d['size']
        k, ox, oy = self._xf()
        target = QRectF(ox, oy - H * k, W * k, H * k)
        p.setRenderHint(QPainter.SmoothPixmapTransform, k * self.d['cell'] < 3)
        p.drawImage(target, self.img)

        # cuadrícula de guías (mm de la pieza), anclada al centro de la pieza
        if self.grid_on:
            step = self.step or nice_step(max(W, H))
            pen = QPen(QColor(42, 127, 158, 90), 1)
            p.setPen(pen)
            cx, cy = W / 2, H / 2
            n = int(max(W, H) / step) + 2
            for i in range(-n, n + 1):
                x = cx + i * step
                if 0 <= x <= W:
                    p.drawLine(self.to_screen(x, 0), self.to_screen(x, H))
                y = cy + i * step
                if 0 <= y <= H:
                    p.drawLine(self.to_screen(0, y), self.to_screen(W, y))
            pen = QPen(QColor(224, 122, 63, 170), 1.2, Qt.DashLine)     # ejes centrales
            p.setPen(pen)
            p.drawLine(self.to_screen(cx, 0), self.to_screen(cx, H))
            p.drawLine(self.to_screen(0, cy), self.to_screen(W, cy))

        # contorno exterior y borde interior del marco
        rect = self.d['rect']
        p.setPen(QPen(QColor(self.C['navy']), 1.4) if rect else QPen(QColor(self.C['muted']), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(target)                            # placa (o, con contorno, el rectángulo que la encierra)
        if self.d.get('object'):
            self._paint_cut(p, W)
        F = self.d['frame_cells']
        if F and rect:
            f = F * self.d['cell']
            p.setPen(QPen(QColor(self.C['navy']), 1, Qt.DotLine))
            p.drawRect(QRectF(self.to_screen(f, H - f), self.to_screen(W - f, f)))

        # línea de referencia
        if self.line:
            a, b = self.line
            p.setPen(QPen(QColor(self.C['accent']), 2.2))
            p.drawLine(self.to_screen(a.x(), a.y()), self.to_screen(b.x(), b.y()))
            for q in (a, b):
                p.setBrush(QColor(self.C['accent']))
                p.drawEllipse(self.to_screen(q.x(), q.y()), 4, 4)
            dx, dy = b.x() - a.x(), b.y() - a.y()
            if dx or dy:
                phi = np.degrees(np.arctan2(dy, dx))
                p.setPen(QColor(self.C['accent']))
                p.drawText(self.to_screen(b.x(), b.y()) + QPointF(8, -8), f'{phi:+.1f}°')

        # rótulos
        f = QFont()
        f.setPointSizeF(9)
        p.setFont(f)
        p.setPen(QColor(self.C['ink']))
        p.drawText(QPointF(target.left(), target.top() - 8), f'{W:.0f} mm')
        p.save()
        p.translate(target.left() - 8, target.bottom())
        p.rotate(-90)
        p.drawText(QPointF(0, 0), f'{H:.0f} mm')
        p.restore()

    def _paint_cut(self, p, W):
        """Modo objeto: cama (línea de apoyo) y corte de la base, con la parte que se quita sombreada."""
        cut = self.d.get('cut', 0.0)
        bad = QColor(self.C['bad'])
        if cut > 0:
            shade = QColor(bad)
            shade.setAlpha(55)
            p.fillRect(QRectF(self.to_screen(0, cut), self.to_screen(W, 0)), shade)
            p.setPen(QPen(bad, 1.6, Qt.DashLine))
            p.drawLine(self.to_screen(-W * 0.05, cut), self.to_screen(W * 1.05, cut))
            p.setPen(bad)
            p.drawText(self.to_screen(0, cut) + QPointF(6, -6), f'corte de la base · {cut:g} mm')
        p.setPen(QPen(QColor(self.C['navy']), 2.4))
        p.drawLine(self.to_screen(-W * 0.08, cut), self.to_screen(W * 1.08, cut))

    # ------------------------------------------------------------------ ratón
    def mousePressEvent(self, ev):
        if self.d is None:
            return
        if ev.button() == Qt.LeftButton and self.d.get('object'):
            return                                # en modo objeto no hay línea de referencia
        if ev.button() == Qt.LeftButton:
            q = self.to_mm(ev.localPos())
            self.line = (q, q)
            self.update()
        elif ev.button() in (Qt.RightButton, Qt.MiddleButton):
            self._pan = ev.localPos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        if self._pan is not None:
            self.off += ev.localPos() - self._pan
            self._pan = ev.localPos()
            self.update()
        elif self.line and ev.buttons() & Qt.LeftButton:
            self.line = (self.line[0], self.to_mm(ev.localPos()))
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._pan is not None:
            self._pan = None
            self.unsetCursor()
            return
        if self.line and ev.button() == Qt.LeftButton:
            a, b = self.line
            k = self._xf()[0]
            if np.hypot(b.x() - a.x(), b.y() - a.y()) * k > 15:         # al menos 15 px: no es un clic
                self.lineDrawn.emit(b.x() - a.x(), b.y() - a.y())
            else:
                self.line = None
            self.update()

    def wheelEvent(self, ev):
        if self.d is None:
            return
        pos = ev.posF() if hasattr(ev, 'posF') else QPointF(ev.pos())
        before = self.to_mm(pos)
        self.zoom = float(np.clip(self.zoom * 1.0015 ** ev.angleDelta().y(), 0.5, 30))
        after = self.to_screen(before.x(), before.y())
        self.off += pos - after                       # el punto bajo el cursor se queda quieto
        self.update()

    def mouseDoubleClickEvent(self, ev):
        self.reset_view()


class OrientPanel(QWidget):
    """Pestaña «Orientación»: barra de opciones, vista y línea de estado."""
    detailChanged = pyqtSignal()                # vista rápida <-> detalle de la pieza
    TIP = ('<b>Arrastra</b> a lo largo de algo que deba quedar recto para ponerlo horizontal o vertical · '
           '<b>rueda</b> zoom · <b>botón derecho</b> desplazar · <b>doble clic</b> encuadrar')
    TIP_OBJ = ('El objeto de frente, como quedará de pie en la cama. Elige <b>Arriba</b> y gira en planta · '
               '<b>rueda</b> zoom · <b>botón derecho</b> desplazar · <b>doble clic</b> encuadrar')

    def __init__(self, colors, cmap):
        super().__init__()
        C = colors
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget()
        bar.setObjectName('viewBar')
        bar.setStyleSheet(f'#viewBar {{ background: white; border-bottom: 1px solid {C["line"]}; }}')
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 4, 10, 4)
        self.chk_grid = QCheckBox('Cuadrícula')
        self.chk_grid.setChecked(True)
        self.cb_step = QComboBox()
        self.cb_step.addItems(['automática', '1 mm', '2 mm', '5 mm', '10 mm', '20 mm', '50 mm'])
        self.cb_step.setToolTip('Separación de las guías, en mm de la pieza')
        bl.addWidget(self.chk_grid)
        bl.addWidget(self.cb_step)
        bl.addSpacing(14)
        self.cb_detail = QComboBox()
        self.cb_detail.addItems(['Vista rápida', 'Detalle de la pieza'])
        self.cb_detail.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_detail.setMinimumContentsLength(21)
        self.cb_detail.setToolTip('<b>Vista rápida</b>: una rejilla gruesa, al instante, para girar y encuadrar.<br>'
                                  '<b>Detalle de la pieza</b>: el mapa de alturas de verdad, con la resolución que '
                                  'tendrá la pieza y el realce de lo pequeño, sin generar la malla. La primera vez '
                                  'tarda unos segundos; después, cambiar el realce, la exageración o el marco es '
                                  'inmediato (girar o cambiar el tamaño lo rehace).')
        self.chk_layers = QCheckBox('Capas')
        self.chk_layers.setToolTip('Muestra las alturas en capas de la impresora (Avanzado › Impresora): lo que no '
                                   'llega a media capa no se verá en la pieza impresa.')
        bl.addWidget(self.cb_detail)
        bl.addWidget(self.chk_layers)
        bl.addSpacing(14)
        self.tip = tip = QLabel(self.TIP)
        tip.setStyleSheet(f'color: {C["muted"]}; font-size: 8.5pt;')
        tip.setWordWrap(True)
        bl.addWidget(tip, 1)
        lay.addWidget(bar)
        self.view = OrientView(colors, cmap)
        lay.addWidget(self.view, 1)
        self.info = QLabel()
        self.info.setStyleSheet(f'background: white; color: {C["ink"]}; padding: 4px 10px; '
                                f'border-top: 1px solid {C["line"]};')
        self.info.setTextFormat(Qt.RichText)
        lay.addWidget(self.info)
        self._layer_mm = 0.2
        self.chk_grid.toggled.connect(self._opts)
        self.cb_step.currentTextChanged.connect(self._opts)
        self.cb_detail.currentIndexChanged.connect(lambda *_: self.detailChanged.emit())
        self.chk_layers.toggled.connect(self._reshade)

    def _opts(self, *_):
        self.view.grid_on = self.chk_grid.isChecked()
        t = self.cb_step.currentText()
        self.view.step = 0 if t == 'automática' else float(t.split()[0])
        self.view.update()

    def detail(self):
        return self.cb_detail.currentIndex() == 1

    def _layer(self):
        return self._layer_mm if self.chk_layers.isChecked() else 0.0

    def _reshade(self, *_):
        self.view.set_data(self.view.d, self._layer())
        self._info_layers()

    def _info_layers(self):
        t = self.info.text().split(' · <b>Capas')[0]
        if self.chk_layers.isChecked() and self.view.d is not None:
            t += f' · <b>Capas de {self._layer_mm:g} mm</b>'
        self.info.setText(t)

    def set_data(self, d, prm):
        self._layer_mm = prm.layer if prm.layer > 0 else 0.2
        if d is not None:
            d['rect'] = prm.shape == 'rectangulo' and not d.get('object')
        self.view.set_data(d, self._layer())
        self.tip.setText(self.TIP_OBJ if d is not None and d.get('object') else self.TIP)
        if d is None:
            self.info.setText('')
            return
        if d.get('object'):
            W, D, H = d['dims']
            parts = [f'<b>Vista de frente.</b> Objeto: {W:.0f} × {D:.0f} × {max(H - d["cut"], 0):.0f} mm',
                     f'<b>Arriba:</b> {prm.up}', f'<b>Giro en planta:</b> {prm.angle:+.1f}°',
                     f'<b>Corte de la base:</b> {d["cut"]:g} mm' if d['cut'] > 0 else 'sin corte de base']
            if prm.mirror:
                parts.append('espejado')
            self.info.setText(' · '.join(parts))
            self._info_layers()
            return
        W, H = d['size']
        parts = [f'<b>Pieza:</b> {W:.0f} × {H:.0f} mm' + (f' · celda de {d["cell"]:.2f} mm' if d.get('detail') else ''),
                 f'<b>Giro total:</b> {d["angle"]:+.2f}°' +
                 (f' (automático {d["auto"]:+.2f}° + manual {prm.angle:+.2f}°)' if prm.auto_straighten else '')]
        if prm.frame > 0:
            parts.append(f'<b>Marco:</b> {prm.frame:g} mm')
        if prm.mirror:
            parts.append('espejada')
        if prm.flip:
            parts.append('cara invertida')
        if d.get('detail') and prm.boost > 1:
            parts.append(f'<b>Realce</b> ×{prm.boost:g}')
        self.info.setText(' · '.join(parts))
        self._info_layers()
