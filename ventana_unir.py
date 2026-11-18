"""Ventana «Unir otra nube»: encajar una segunda nube con la abierta (ya alineadas, por puntos, por una arista común
o automática), ver
cómo queda y la calidad de la unión, y aceptarla. La matemática está en unir.py."""
import os

import numpy as np
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout,
                             QLabel, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget)

import unir

NMAX = 350_000            # puntos de cada nube que se dibujan
COL_A = np.array([0.30, 0.52, 0.80])
COL_B = np.array([0.90, 0.50, 0.20])
COL_NEAR = np.array([0.15, 0.68, 0.35])
PAIR_COLS = [(0.95, 0.85, 0.10), (0.85, 0.15, 0.55), (0.10, 0.80, 0.85), (0.55, 0.30, 0.95), (0.95, 0.35, 0.25),
             (0.40, 0.90, 0.30)]
UNITS = ['igual', 'auto', 'mm', 'cm', 'm', 'pulgadas']


class _Job(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.log.emit))
        except Exception as e:                # noqa: BLE001  (se enseña en la ventana)
            self.failed.emit(str(e))


class MergeDialog(QDialog):
    def __init__(self, main, path_b, colors, title=None, ok_text='Unir'):
        super().__init__(main)
        import visor
        self.main, self.path_b, self.C = main, os.path.abspath(path_b), colors
        self.setWindowTitle(title or f'Unir otra nube: {os.path.basename(path_b)}')
        self._ok_text = ok_text
        self.resize(1250, 780)
        self.result_entry = None
        A = main.xyz_all
        k = max(1, len(A) // NMAX)
        self.A = A                                       # nube principal (mm, con las ya unidas)
        self.As = A[::k].astype(np.float64)
        self.rgbA = main.rgb_all[::k] / 255.0 if main.rgb_all is not None else None
        self.c0 = self.As.mean(0)
        self.M = None                                    # matriz de la añadida (None: aún sin encajar)
        self.pa, self.pb = [], []                        # pares de puntos: principal (mm) y añadida (sus mm)
        self.job = None
        self.q = None
        self.edge_info = None

        lay = QHBoxLayout(self)
        self.viewer = visor.Viewer3D(colors['view'], 'nube', colors)
        self.viewer.hide_marks()
        self.viewer.extra_items = self._markers
        self.viewer.measure_btn.hide()                   # el clic marca los pares de puntos
        self.viewer.measure_lbl.hide()
        self.viewer.hint.setText(visor.Viewer3D.HINT + ' · <b>Clic</b> (por puntos o por la arista): marcar un punto')
        self.viewer.view.clicked.connect(self._pick)
        lay.addWidget(self.viewer, 1)
        side = QWidget()                                 # con barra si no cabe (cuatro modos con su explicación)
        v = QVBoxLayout(side)
        v.setContentsMargins(6, 0, 0, 0)

        def note(text, obj='muted'):
            lb = QLabel(text)
            lb.setWordWrap(True)
            lb.setTextFormat(Qt.RichText)
            lb.setObjectName(obj)
            return lb
        v.addWidget(note(f'<b>Principal</b> (azul): {os.path.basename(main.path)}, {len(A):,} puntos<br>'
                         f'<b>Añadida</b> (naranja): {os.path.basename(path_b)}', ''))
        row = QHBoxLayout()
        row.addWidget(QLabel('Unidades de la añadida'))
        self.units = QComboBox()
        self.units.addItems(UNITS)
        self.units.setToolTip('igual: las mismas que la nube principal (lo normal si salen del mismo escáner o del '
                              'mismo programa).\nauto: como al abrir una nube (metros si mide menos de 20 de lado).')
        self.units.currentTextChanged.connect(self._load_b)
        row.addWidget(self.units, 1)
        v.addLayout(row)
        v.addSpacing(6)
        v.addWidget(note('<b>¿Cómo se encajan?</b>', ''))
        self.how = QButtonGroup(self)
        self.rb = {}
        for key, text, tip in [
                ('igual', 'Ya están alineadas', 'Comparten coordenadas: el mismo proyecto de fotogrametría, '
                 'georreferenciadas o exportadas juntas.'),
                ('puntos', 'Por puntos (recomendada)', 'Marca el mismo punto en las dos nubes, al menos 3 pares '
                 'bien repartidos; si se solapan, se afina después con la zona común. Funciona siempre, también sin '
                 'solape.'),
                ('arista', 'Por la arista (dos caras en ángulo)', 'Dos caras de un bloque escaneadas por separado: '
                 'marca los dos extremos de la arista en cada nube, primero el de abajo, y di el ángulo entre las '
                 'caras. Si un escaneo '
                 'dobla un poco la esquina, esa franja mide el ángulo y afina la unión. Con puntos sueltos no se '
                 'puede: todos caerían en la arista, en línea.'),
                ('auto', 'Automática (necesita solape)', 'Busca sola la zona común. Acierta en piezas con forma '
                 'marcada donde se solapan (aristas, esquinas); en piezas casi planas, rocas lisas o piezas '
                 'simétricas puede encajar mal: compruébalo siempre.')]:
            r = QRadioButton(text)
            r.setToolTip(tip)
            self.how.addButton(r)
            self.rb[key] = r
            v.addWidget(r)
            v.addWidget(note(tip))
            r.toggled.connect(lambda on, k=key: on and self._how_changed(k))
        self.pairs_lbl = note('', 'plan')
        v.addWidget(self.pairs_lbl)
        prow = QHBoxLayout()
        self.btn_undo = QPushButton('Quitar el último')
        self.btn_undo.clicked.connect(self._undo_pick)
        self.btn_clear = QPushButton('Borrar los puntos')
        self.btn_clear.clicked.connect(self._clear_picks)
        prow.addWidget(self.btn_undo)
        prow.addWidget(self.btn_clear)
        self.pair_tools = [self.btn_undo, self.btn_clear]
        v.addLayout(prow)
        erow = QHBoxLayout()
        erow.addWidget(QLabel('Ángulo entre las caras'))
        self.angle = QDoubleSpinBox()
        self.angle.setRange(20, 180)
        self.angle.setDecimals(1)
        self.angle.setSuffix('°')
        self.angle.setValue(90)
        self.angle.setToolTip('Medido por dentro de la piedra: 90° en un sillar; más en un bloque achaflanado o en '
                              'un polígono (135° en un octógono); 180° si las dos caras siguen en el mismo plano.')
        erow.addWidget(self.angle, 1)
        v.addLayout(erow)
        self.chk_side = QCheckBox('Al otro lado')
        self.chk_side.setToolTip('Si la añadida sale hacia fuera de la piedra en vez de hacia dentro (en caras casi '
                                 'lisas no se sabe bien de qué lado está la piedra).')
        v.addWidget(self.chk_side)
        self.chk_strip = QCheckBox('Usar la franja de la otra cara, si la hay')
        self.chk_strip.setChecked(True)
        self.chk_strip.setToolTip('Si un escaneo dobla un poco la esquina y trae una franja de la otra cara: el ángulo\n'
                                  'se mide en ella (el escrito no cuenta) y la zona común afina la unión, también a lo\n'
                                  'largo de la arista, que es lo que los clics fijan peor. Hace falta algo de relieve\n'
                                  'en la franja para fijar lo largo de la arista.')
        v.addWidget(self.chk_strip)
        self.edge_tools = [erow.itemAt(0).widget(), self.angle, self.chk_side, self.chk_strip]
        self.angle.valueChanged.connect(self._edge_redo)
        self.chk_side.toggled.connect(self._edge_redo)
        self.chk_strip.toggled.connect(self._edge_redo)
        self.chk_icp = QCheckBox('Afinar con la zona común (ICP)')
        self.chk_icp.setChecked(True)
        self.chk_icp.setToolTip('Después de los puntos, ajustar con todos los puntos de la zona común. Si las nubes '
                                'no se solapan no hace nada.')
        v.addWidget(self.chk_icp)
        self.btn_align = QPushButton('Alinear')
        self.btn_align.setObjectName('primary')
        self.btn_align.clicked.connect(self._align)
        v.addWidget(self.btn_align)
        self.status = note('', 'plan')
        v.addWidget(self.status)
        row = QHBoxLayout()
        row.addWidget(QLabel('Color'))
        self.color = QComboBox()
        self.color.addItems(['azul y naranja', 'zona común', 'separación', 'colores reales'])
        self.color.setToolTip('azul y naranja: cada nube de un color.\nzona común: en verde los puntos de la añadida '
                              'que caen sobre la principal.\nseparación: en la zona común, a cuánto queda la añadida '
                              'de la principal: blanco = coinciden,\nrojo = por encima (fuera), azul = por debajo; '
                              'un lado rojo y otro azul = la alineación está girada.\nEn gris lo que no está en la '
                              'zona común y la principal.\ncolores reales: los del escaneo.')
        self.color.currentTextChanged.connect(lambda *_: self._show(home=False))
        row.addWidget(self.color, 1)
        v.addLayout(row)
        self.sep_lbl = note('')
        self.sep_lbl.hide()
        v.addWidget(self.sep_lbl)
        v.addStretch(1)
        brow = QHBoxLayout()
        cancel = QPushButton('Cancelar')
        cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton(self._ok_text)
        self.btn_ok.setObjectName('primary')
        self.btn_ok.setToolTip('Añadir esta nube a la principal con la alineación que se ve')
        self.btn_ok.clicked.connect(self._accept)
        brow.addWidget(cancel)
        brow.addStretch(1)
        brow.addWidget(self.btn_ok)
        v.addLayout(brow)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(390)
        lay.addWidget(scroll)
        self._load_b()
        q = unir.quality(self.A, self.B, np.eye(4), n=20_000)       # ¿ya comparten coordenadas?
        self.rb['igual' if q['overlap'] > 0.05 and q['rms'] < 3 * q['sp'] else 'puntos'].setChecked(True)

    # ------------------------------------------------------------------ datos
    def _load_b(self, *_):
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            B, rgb, _, info = unir.read_extra(self.path_b, self.units.currentText(), self.main.load_info)
        finally:
            QApplication.restoreOverrideCursor()
        self.B = B
        k = max(1, len(B) // NMAX)
        self.Bs = B[::k].astype(np.float64)
        self.rgbB = rgb[::k] / 255.0 if rgb is not None else None
        self.binfo = info
        self.M = np.eye(4) if self._how() == 'igual' else None
        self.pa, self.pb = [], []
        self.q = None
        self._after_change(home=True)

    def _how(self):
        return next((k for k, r in self.rb.items() if r.isChecked()), 'auto')

    def _how_changed(self, key):
        self.pa, self.pb = [], []
        self.q = None
        self.edge_info = None
        self.M = np.eye(4) if key == 'igual' else None
        self._after_change(home=True)

    def _after_change(self, home=False):
        how = self._how()
        if how == 'igual' and self.M is not None:
            self.q = unir.quality(self.A, self.B, self.M)
        picks = how in ('puntos', 'arista')
        for w in self.pair_tools:
            w.setVisible(picks)
        self.chk_icp.setVisible(how == 'puntos')
        for w in self.edge_tools:
            w.setVisible(how == 'arista')
        self.pairs_lbl.setVisible(picks)
        self.btn_align.setVisible(how != 'igual')
        need = 2 if how == 'arista' else 3
        self.btn_align.setEnabled(how == 'auto' or min(len(self.pa), len(self.pb)) >= need)
        self._update_pairs_text()
        self._update_status()
        self.btn_ok.setEnabled(self.M is not None and self.job is None)
        self._show(home)

    def _update_pairs_text(self):
        if self._how() == 'arista':
            na, nb = len(self.pa), len(self.pb)
            nxt = ('Haz clic en el extremo de <b>abajo</b> de la arista en la <b>principal</b> (azul)' if na == 0 else
                   'Ahora el extremo de <b>arriba</b> en la principal' if na == 1 else
                   'Ahora el extremo de <b>abajo</b> de la misma arista en la <b>añadida</b> (naranja)' if nb == 0 else
                   'Ahora el de <b>arriba</b> en la añadida' if nb == 1 else
                   'Listo: pulsa <b>Alinear</b>')
            self.pairs_lbl.setText(f'Arista: {na} de 2 puntos en la principal, {nb} de 2 en la añadida.<br>{nxt}. '
                                   f'<b>El orden importa</b>: primero abajo y después arriba en las dos (si la arista '
                                   f'está tumbada, el mismo extremo primero en las dos); así la añadida no puede quedar '
                                   f'del revés. Marca los extremos lo más separados posible y en el borde de cada cara '
                                   f'(las esquinas).')
            for w in self.pair_tools:
                w.setEnabled(bool(self.pa))
            return
        n = min(len(self.pa), len(self.pb))
        nxt = ('Haz clic en un punto de la <b>principal</b> (azul)' if len(self.pa) == len(self.pb) else
               'Ahora el <b>mismo punto</b> en la <b>añadida</b> (naranja)')
        self.pairs_lbl.setText(f'{n} par{"es" if n != 1 else ""} de puntos{" (hacen falta 3)" if n < 3 else ""}.<br>'
                               f'{nxt}. Elige detalles que se reconozcan bien en las dos y repartidos (esquinas, '
                               f'letras, roturas), no todos en línea. Gira y acerca la vista como siempre.')
        for w in self.pair_tools:
            w.setEnabled(bool(self.pa))

    def _update_status(self, extra=''):
        if self.M is None:
            self.status.setText('Aún sin encajar: la añadida se ve al lado de la principal.' + extra)
            return
        if self.q is None:
            self.status.setText(extra)
            return
        pr = getattr(self, 'pairs_rms', None) if self._how() == 'puntos' else None
        how = self._how()
        if how == 'arista' and self.edge_info is not None:
            txt, level = unir.edge_verdict(self.edge_info)
            col = {'ok': self.C['ok'], 'aviso': self.C['accent_dk'], 'mal': self.C['bad']}[level]
            self.status.setText(f'<span style="color:{col}">{"✔" if level == "ok" else "⚠"} {txt}</span>' + extra)
            return
        if how == 'puntos' and len(self.pa) >= 3 and unir.collinear(self.pa):
            extra = ('<br>Los puntos marcados están casi en línea: el giro alrededor de esa línea no queda fijado. '
                     'Si las dos nubes solo comparten una arista, usa «Por la arista».') + extra
        txt, level = unir.verdict(self.q, pr, getattr(self, 'ambiguous', '') if how == 'auto' else '', how)
        col = {'ok': self.C['ok'], 'aviso': self.C['accent_dk'], 'mal': self.C['bad']}[level]
        self.status.setText(f'<span style="color:{col}">{"✔" if level == "ok" else "⚠"} {txt}</span>' + extra)

    # ------------------------------------------------------------------ vista
    def _b_display(self):
        """Puntos de la añadida tal como se ven (mm de la principal): encajados con M o, sin encajar, al lado."""
        if self.M is not None:
            return unir.transform(self.M, self.Bs)[0].astype(np.float64)
        ea, eb = np.ptp(self.As, 0), np.ptp(self.Bs, 0)
        off = self.As.mean(0) - self.Bs.mean(0) + [(ea[0] + eb[0]) / 2 * 1.15, 0, 0]
        return self.Bs + off

    def _show(self, home=False):
        Bd = self._b_display()
        mode = self.color.currentText()
        ca = np.tile(COL_A, (len(self.As), 1)) if mode != 'colores reales' or self.rgbA is None else self.rgbA
        cb = np.tile(COL_B, (len(self.Bs), 1)) if mode != 'colores reales' or self.rgbB is None else self.rgbB
        if mode == 'zona común' and self.M is not None:
            from scipy.spatial import cKDTree
            d, _ = cKDTree(self.As).query(Bd, workers=-1)
            sp = self.q['sp'] if self.q else unir.spacing(self.As)
            cb = np.array(cb, np.float64)
            cb[d < 4 * max(sp, unir.spacing(self.As))] = COL_NEAR
        self.sep_lbl.setVisible(mode == 'separación' and self.M is not None)
        if mode == 'separación' and self.M is not None:
            import desviacion
            sp = max(self.q['sp'] if self.q else 0.0, unir.spacing(self.As))
            d = unir.separation(self.As, Bd, 4 * sp)
            fin = np.isfinite(d)
            lim = max(float(np.percentile(np.abs(d[fin]), 95)) if fin.any() else 0.0, 0.5 * sp, 0.01)
            cb = desviacion.colors(d, lim) / 255.0
            ca = np.tile([0.80, 0.81, 0.82], (len(self.As), 1))
            f = lambda x: f'{x:.2g}'.replace('.', ',')                  # noqa: E731
            if fin.any():
                med = float(np.median(d[fin]))
                self.sep_lbl.setText(
                    f'Separación en la zona común ({100 * fin.mean():.0f} % de la añadida): mediana {f(med)} mm, '
                    f'95 % a menos de {f(lim)} mm. Colores: ±{f(lim)} mm (azul por debajo, rojo por encima).')
            else:
                self.sep_lbl.setText('No hay zona común: las nubes no se tocan con esta alineación.')
        self.Bd = Bd
        P = np.r_[self.As, Bd] - self.c0
        self.viewer.show_cloud(P.astype(np.float32), np.r_[ca, cb], None, home=home or self.viewer.view.home is None)

    def _markers(self):
        import pyqtgraph.opengl as gl
        from visor import MARKER_GL
        items = []
        if self._how() not in ('puntos', 'arista'):
            return items
        Bpts = []
        if self.pb:
            pb = np.array(self.pb)
            Bpts = (unir.transform(self.M, pb)[0] if self.M is not None else
                    pb + (self._b_display()[0] - self.Bs[0]))
        groups = [(np.array(self.pa) - self.c0 if self.pa else [], 'a'),
                  (np.array(Bpts) - self.c0 if len(Bpts) else [], 'b')]
        for pts, kind in groups:
            for j, p in enumerate(pts):
                c = PAIR_COLS[j % len(PAIR_COLS)]
                s = gl.GLScatterPlotItem(pos=np.array([p], np.float32), size=14, color=(*c, 1.0), pxMode=True)
                s.setGLOptions(MARKER_GL)
                s.setDepthValue(30)
                items.append(s)
                if hasattr(gl, 'GLTextItem'):
                    from PyQt5.QtGui import QColor, QFont
                    t = gl.GLTextItem(pos=p, text=f' {j + 1}{kind}', color=QColor(30, 30, 30),
                                      font=QFont('Sans', 11, QFont.Bold))
                    t.setGLOptions(MARKER_GL)
                    items.append(t)
        return items

    # ------------------------------------------------------------------ puntos
    def _pick(self, pos):
        how = self._how()
        if how not in ('puntos', 'arista') or self.job is not None:
            return
        v = self.viewer.view
        P = np.r_[self.As, self.Bd] - self.c0
        sx, sy, nz, ok = v.project(P.astype(np.float32))
        for r in (7, 20):
            idx = np.flatnonzero(ok & ((sx - pos.x()) ** 2 + (sy - pos.y()) ** 2 <= r * r))
            if len(idx):
                break
        else:
            self._update_status('<br>Ahí no hay ningún punto: haz clic sobre una nube.')
            return
        zmin = nz[idx].min()
        front = idx[nz[idx] <= zmin + 2e-4]
        i = int(front[np.argmin((sx[front] - pos.x()) ** 2 + (sy[front] - pos.y()) ** 2)])
        if how == 'arista':
            if len(self.pb) >= 2:
                self._update_status('<br>Ya están los cuatro puntos: pulsa «Alinear» o borra alguno.')
                return
            want_a = len(self.pa) < 2
        else:
            want_a = len(self.pa) == len(self.pb)
        is_a = i < len(self.As)
        if is_a != want_a:
            self._update_status('<br>Ese punto es de la otra nube: ' +
                                ('primero uno de la principal (azul).' if want_a else
                                 'ahora toca la añadida (naranja).'))
            return
        if is_a:
            self.pa.append(self.As[i])
        else:
            self.pb.append(self.Bs[i - len(self.As)])
        self._after_change()

    def _undo_pick(self):
        if self._how() == 'arista':
            (self.pb or self.pa).pop()
            self._after_change()
            return
        if len(self.pa) > len(self.pb):
            self.pa.pop()
        elif self.pb:
            self.pb.pop()
        self._after_change()

    def _clear_picks(self):
        self.pa, self.pb = [], []
        self._after_change()

    # ------------------------------------------------------------------ alinear
    def _align(self):
        how = self._how()
        A, B = self.A, self.B
        if how == 'puntos':
            n = min(len(self.pa), len(self.pb))
            pa, pb, icp = np.array(self.pa[:n]), np.array(self.pb[:n]), self.chk_icp.isChecked()

            def work(log):
                M, prms = unir.kabsch(pb, pa)
                q = unir.quality(A, B, M)
                if icp and q['overlap'] > 0.05:
                    M = unir.refine(A, B, M, log)
                    q = unir.quality(A, B, M)
                return M, q, prms, ''
        elif how == 'arista':
            pa, pb = np.array(self.pa[:2]), np.array(self.pb[:2])
            ang, other, strip = self.angle.value(), self.chk_side.isChecked(), self.chk_strip.isChecked()

            def work(log):
                M, info = unir.edge_align_strip(A, B, pa, pb, ang, other, strip, log)
                return M, unir.quality(A, B, M), None, info
        else:
            def work(log):
                M, amb = unir.auto_align(A, B, log)
                return M, unir.quality(A, B, M), None, amb
        self.job = _Job(work)
        self.job.log.connect(lambda s: self._update_status(f'<br><i>{s.strip()}</i>'))
        self.job.done.connect(self._aligned)
        self.job.failed.connect(self._failed)
        self.btn_align.setEnabled(False)
        self.btn_ok.setEnabled(False)
        self.status.setText('Alineando… (la automática tarda de medio minuto a un minuto)')
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self.job.start()

    def _aligned(self, out):
        QApplication.restoreOverrideCursor()
        self.job = None
        self.M, self.q, self.pairs_rms, self.ambiguous = out
        if isinstance(self.ambiguous, dict):              # por la arista: sus datos
            self.edge_info, self.ambiguous = self.ambiguous, ''
        if self.color.currentText() == 'azul y naranja':
            self.color.setCurrentText('zona común')
        self._after_change(home=False)

    def _edge_redo(self, *_):
        if self._how() == 'arista' and self.M is not None and self.job is None and \
                min(len(self.pa), len(self.pb)) >= 2:
            self._align()

    def _failed(self, msg):
        QApplication.restoreOverrideCursor()
        self.job = None
        self._after_change()
        self._update_status(f'<br><span style="color:{self.C["bad"]}">No se pudo alinear: {msg}</span>')

    def done(self, r):
        unir.clear_cache()
        super().done(r)

    def _accept(self):
        if self.M is None:
            return
        self.result_entry = {'path': self.path_b, 'units': self.units.currentText(),
                             'M': [round(float(x), 10) for x in np.asarray(self.M).ravel()]}
        self.result_text = self.status.text()
        self.accept()
