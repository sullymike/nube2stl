"""Asistente opcional de nube2stl.

No tiene parámetros ni cálculos propios: trabaja sobre una copia de los parámetros normales (los de las pestañas),
usa los mismos controles (SPEC), el mismo cálculo de impresión (relief.print_plan) y las recetas de recetas.py
(las mismas que muestra la ayuda). Al terminar copia el resultado a las pestañas, donde se puede seguir retocando.
"""
import copy

import numpy as np

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QRadioButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

import impresoras
import recetas
import relief
from relief import Params

def behind_html(st, prm):
    """Medidas de la nube que hacen falta para elegir «Profundidad máx.» y qué saldría con el valor actual."""
    if not st or 'h_q' not in st or prm.mode == 'objeto':
        return ''
    L, W = st['length'], st['width']
    back, front = -st['h_q'][0], st['h_q'][1]
    depth = st.get('depth', 0.0)
    s = relief.piece_scale(prm, L)
    B = relief.behind_mm(prm, max(L, W))
    auto = ' (auto)' if prm.behind <= 0 else ''
    eng = ''
    if relief.uses_detrend(prm) and 'coarse' in st:      # grabado: lo que queda sin la forma de la roca
        try:
            eng = f' (grabado ≈ {relief.depth_detrended(st, prm):.1f} sin la forma de la roca)'
        except Exception:  # noqa: BLE001  la nota nunca debe fallar
            eng = ''
    txt = (f'<b>Nube</b> (mm): cara {L:.0f} × {W:.0f} · relieve ≈ {depth:.0f}{eng} · los puntos llegan a '
           f'<b>{back:.0f} por detrás</b> de la cara (laterales) y {front:.0f} por delante.')
    if prm.ratio > 0:
        txt += f' A escala 1:{prm.ratio:g}, 1 mm de la nube = {s:.3g} mm en la pieza.'
    elif prm.target_len > 0:
        txt += f' En la pieza ({prm.target_len:g} mm) 1 mm de la nube = {s:.3g} mm.'
    if prm.mode == 'bloque':
        thick = (min(B, back) + front) * s
        txt += f'<br><b>Con {B:.0f}{auto} → bloque de ≈ {thick:.0f} mm</b> de grueso'
        txt += ('' if B >= back else f'; todo el lateral: {np.ceil(back):.0f} (≈ {(back + front) * s:.0f} mm)') + '.'
        ex = [g for g in (10, 15, 20, 30, 40, 60) if front * s + 2 < g < (back + front) * s][:3]
        if ex:
            txt += ' Grosor → valor: ' + ' · '.join(f'{g} mm → {g / s - front:.0f}' for g in ex) + '.'
    elif B >= back:
        txt += (f'<br><b>Con {B:.0f}{auto}</b> no se descarta nada. Si salen rampas o un reborde en el borde, bájalo a '
                f'algo más que el relieve, p. ej. {max(1.5 * depth, depth + 5):.0f}.')
    else:
        txt += f'<br><b>Con {B:.0f}{auto}</b> se descartan los puntos más de {B:.0f} por detrás de la cara.'
    if prm.mode != 'bloque' and B < depth:
        txt += (f' <span style="color:#b03a2e">Es menos que el relieve (≈ {depth:.0f}): puede recortar los fondos '
                f'del propio relieve.</span>')
    return txt


def _len(mm, big=None):
    """Longitud en m, cm o mm (según big, para que las tres medidas de una pieza vayan en la misma unidad)."""
    big = mm if big is None else big
    return f'{mm / 1000:.3g} m' if big >= 2000 else f'{mm / 10:.3g} cm' if big >= 100 else f'{mm:.3g} mm'


STEPS = [('inicio', 'Nube de puntos', 'Empezamos',
          'El asistente te hace unas preguntas y rellena los parámetros por ti. No cambia nada hasta el final: '
          'entonces verás un resumen y podrás seguir retocando en las pestañas como siempre.'),
         ('destino', 'Para qué', '¿Para qué es?',
          'Una réplica para tu impresora (a escala, con su boquilla y su cama) o una copia digital a tamaño real '
          '(para archivarla, fresarla, imprimirla por piezas en gran formato o medir). Las preguntas siguientes '
          'cambian según lo que elijas.'),
         ('tipo', 'Qué es', '¿Qué has escaneado?',
          'Esto decide cómo se construye la pieza. Si dudas, mira la nube en la ventana principal (pestaña «Nube de '
          'puntos»): qué partes tienen puntos y cuáles no.'),
         ('objetivo', 'Qué hacer', '¿Qué quieres hacer con él?',
          'La forma general de la pieza. Solo se pregunta cuando hay varias opciones para lo que has escaneado.'),
         ('acabado', 'Acabado', 'Acabado de la pieza',
          'El borde, el grosor, la base o el rótulo, según lo que hayas elegido.'),
         ('impresora', 'Impresora', '¿Con qué la vas a imprimir?',
          'La boquilla y la capa fijan el detalle más fino que se puede imprimir. Con ellas se calculan la '
          'resolución y la simplificación óptimas: más fino no se notaría y solo haría el fichero más grande.'),
         ('detalle', 'Estado', '¿Cómo es el relieve y el escaneo?',
          'Marca lo que se cumpla. Cada opción ajusta la limpieza y el suavizado; si no marcas nada, se usan los '
          'valores por defecto, que van bien para un escaneo limpio.'),
         ('tamano', 'Tamaño', '¿De qué tamaño la quieres?',
          'El tamaño decide la escala, si cabe en la cama y cuántas capas ocupará el relieve. El cálculo se '
          'actualiza al momento.'),
         ('real', 'Tamaño real', 'Copia a tamaño real',
          'A 1:1 no hay impresora que mande: las unidades tienen que ser seguras, el detalle es el del escaneo (en '
          'mm reales) y la simplificación depende del uso.'),
         ('resumen', 'Resumen', 'Resumen',
          'Esto es lo que va a cambiar en las pestañas. Puedes volver atrás o aplicarlo; después podrás retocar '
          'cualquier valor a mano.')]
OTHER = 'Otra (introducir a mano)'


class Asistente(QDialog):
    def __init__(self, main, make_widget, spec, plan_html, label, colors):
        super().__init__(main)
        self.main, self.make_widget, self.spec, self.plan_html, self.label, self.C = \
            main, make_widget, spec, plan_html, label, colors
        self.setWindowTitle('Asistente de nube2stl')
        self.setWindowModality(Qt.WindowModal)
        scr = self.screen().availableGeometry() if hasattr(self, 'screen') and self.screen() else None
        self.resize(900, min(880, scr.height() - 60) if scr is not None else 720)   # que quepan los 8 tipos
        self.base = main.params()                 # valores de las pestañas al abrir (para el resumen)
        self.p = copy.copy(self.base)             # lo que va eligiendo el asistente
        self.reasons = {}                         # parámetro -> por qué cambia
        self.purpose = self._guess_purpose()
        self.kind = self._purpose()['kind']
        self.cond_applied = {}                   # parámetro -> (valor antes, valor puesto) por «Escaneo»
        self.cond_checked = {'socavados'} if self.base.mode == '3d' else set()
        self.purpose_mode = self.base.mode       # método que pone el objetivo (antes de las condiciones)
        self.pos = 0
        self._build()
        self._go(0, forward=True)

    # ------------------------------------------------------------------ utilidades
    @property
    def stats(self):
        return getattr(self.main, 'stats', None)

    def _guess_purpose(self):
        b = self.base
        if b.mode == 'objeto':
            if getattr(b, 'surface', 'plano') == 'revolucion' and not b.ground:
                return 'vasija'
            return 'maqueta' if b.ground else 'objeto'
        if b.mode == 'bloque':
            return 'bloque'
        if getattr(b, 'surface', 'plano') in ('cilindro', 'cono', 'revolucion'):
            return 'desenrollar'
        if b.mode == 'alturas' and getattr(b, 'detrend', 0) > 0:
            return 'grabado'
        if b.negative:
            return 'molde'
        if b.view not in ('auto', ''):
            return 'fachada'
        if b.shape == 'rectangulo':
            return 'placa'
        return 'copia'

    def _purpose(self):
        return next(x for x in recetas.PURPOSES if x['key'] == self.purpose)

    def _visible(self, key):
        real = relief.is_real(self.p)
        if key in ('impresora', 'tamano'):
            return not real
        if key == 'real':
            return real
        if key == 'objetivo':
            return len(recetas.purposes_of(self.kind)) > 1
        return key != 'acabado' or bool(self._purpose().get('ask'))

    def _conditions(self):
        """Condiciones del escaneo que tienen sentido con el método del objetivo elegido."""
        return [c for c in recetas.CONDITIONS if self.purpose_mode in c.get('modes', (self.purpose_mode,))]

    def fmt(self, name, v):
        """Valor con sus unidades, como en las pestañas."""
        row = self.spec.get(name)
        if row is None:
            return recetas.fmt_value(v)
        _, _, kind, opt, _ = row
        if kind in 'fi' and opt.get('special') and v == opt['lo']:
            return opt['special']
        if kind == 'b':
            return 'sí' if v else 'no'
        if kind in 'fi':
            dec = opt.get('dec', 0)
            txt = f'{v:.{dec}f}'.replace('.', ',') if kind == 'f' else str(v)
            return txt + (' ' + opt['suf'] if opt.get('suf') else '')
        return str(v)

    def _set(self, name, value, why):
        setattr(self.p, name, value)
        self.reasons[name] = why

    def _param_widget(self, name, why, on_change=None):
        """El mismo control que en las pestañas, enlazado a self.p."""
        _, label, kind, opt, tip = self.spec[name]
        w = self.make_widget(kind, opt, getattr(self.p, name), label)
        w.setToolTip(tip)

        def changed(*_):
            if isinstance(w, QCheckBox):
                v = w.isChecked()
            elif isinstance(w, QLineEdit):
                v = w.text().strip()
            elif isinstance(w, QComboBox):
                v = w.currentText()
            else:
                v = type(getattr(Params(), name))(w.value())
            self._set(name, v, why)
            if on_change:
                on_change()
        (getattr(w, 'valueChanged', None) or getattr(w, 'currentTextChanged', None) or getattr(w, 'textChanged', None)
         or w.toggled).connect(changed)
        return w

    def _note(self, text, obj='muted'):
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setObjectName(obj)
        lb.setTextFormat(Qt.RichText)
        return lb

    @staticmethod
    def _clear(layout):
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                w = it.widget()
                w.hide()
                w.setParent(None)              # fuera ya: deleteLater dejaría la página vieja pintada un instante
                w.deleteLater()
            elif it.layout():
                Asistente._clear(it.layout())

    # ------------------------------------------------------------------ estructura
    def _build(self):
        C = self.C
        self.setStyleSheet(f"""
            #wizSide {{ background: {C['navy']}; }}
            #wizSide QLabel {{ color: #9fb9c4; padding: 6px 4px; font-size: 10.5pt; }}
            #wizSide QLabel[state="now"] {{ color: white; font-weight: 700; border-left: 3px solid {C['accent']};
                                            padding-left: 9px; }}
            #wizSide QLabel[state="done"] {{ color: #cfe6ee; }}
            #wizSide QLabel[state="off"] {{ color: #5d7887; }}
            #wizTitle {{ font-size: 15pt; font-weight: 800; color: {C['navy']}; }}
            #wizIntro {{ color: {C['muted']}; }}
            #optTitle {{ font-weight: 700; color: {C['navy']}; }}
            #effect {{ color: {C['teal']}; font-size: 9pt; }}
            #plan {{ background: #f2f7f9; border: 1px solid #d3e4ea; border-left: 3px solid {C['teal']};
                     border-radius: 6px; padding: 7px 9px; color: #2d4250; }}
        """)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        side = QFrame()
        side.setObjectName('wizSide')
        side.setFixedWidth(190)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 22, 10, 16)
        t = QLabel('Asistente')
        t.setStyleSheet('color: white; font-size: 14pt; font-weight: 800; padding-bottom: 14px;')
        sl.addWidget(t)
        self.side_labels = []
        for _, short, _, _ in STEPS:
            lb = QLabel(short)
            sl.addWidget(lb)
            self.side_labels.append(lb)
        sl.addStretch(1)
        tip = QLabel('Opcional: todo se puede hacer también desde las pestañas.')
        tip.setWordWrap(True)
        tip.setStyleSheet('color: #7f9ba8; font-size: 8.5pt;')
        sl.addWidget(tip)
        root.addWidget(side)

        right = QVBoxLayout()
        right.setContentsMargins(26, 20, 22, 16)
        self.title = QLabel()
        self.title.setObjectName('wizTitle')
        self.intro = QLabel()
        self.intro.setObjectName('wizIntro')
        self.intro.setWordWrap(True)
        right.addWidget(self.title)
        right.addWidget(self.intro)
        right.addSpacing(10)
        self.stack = QStackedWidget()
        self.pages = {}
        for key, _, _, _ in STEPS:
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 8, 0)
            self.pages[key] = lay
            # con desplazamiento: si la página no cabe, cada texto conserva su alto (sin esto Qt los aplastaba y
            # la segunda línea de una descripción quedaba debajo de la opción siguiente)
            sc = QScrollArea()
            sc.setWidget(page)
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sc.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }')
            self.stack.addWidget(sc)
        right.addWidget(self.stack, 1)
        foot = QHBoxLayout()
        self.btn_cancel = QPushButton('Cancelar')
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_back = QPushButton('‹ Atrás')
        self.btn_back.clicked.connect(lambda: self._go(self._step(-1), forward=False))
        self.btn_next = QPushButton('Siguiente ›')
        self.btn_next.setObjectName('primary')
        self.btn_next.clicked.connect(self._next)
        foot.addWidget(self.btn_cancel)
        foot.addStretch(1)
        foot.addWidget(self.btn_back)
        foot.addWidget(self.btn_next)
        right.addLayout(foot)
        root.addLayout(right, 1)

    def _step(self, d):
        i = self.pos + d
        while 0 <= i < len(STEPS) and not self._visible(STEPS[i][0]):
            i += d
        return max(0, min(i, len(STEPS) - 1))

    def _next(self):
        key = STEPS[self.pos][0]
        getattr(self, f'_leave_{key}', lambda: None)()
        if key == 'resumen':
            self._finish()
        else:
            self._go(self._step(+1), forward=True)

    def _go(self, i, forward):
        self.pos = i
        key, _, title, intro = STEPS[i]
        self.title.setText(title)
        self.intro.setText(intro)
        lay = self.pages[key]
        self._clear(lay)
        getattr(self, f'_page_{key}')(lay, forward)
        self.stack.setCurrentIndex(i)
        for j, lb in enumerate(self.side_labels):
            k = STEPS[j][0]
            state = 'now' if j == i else 'off' if not self._visible(k) else 'done' if j < i else ''
            lb.setText(('✓ ' if state == 'done' else '') + STEPS[j][1])
            lb.setProperty('state', state)
            lb.style().unpolish(lb)
            lb.style().polish(lb)
        self.btn_back.setEnabled(i > 0)
        last = key == 'resumen'
        self.btn_next.setText(('Aplicar y generar' if self._gen_wanted() else 'Aplicar') if last else 'Siguiente ›')

    def _gen_wanted(self):
        return getattr(self, 'chk_gen', None) is not None and self.chk_gen.isChecked() and self.chk_gen.isEnabled()

    def cloud_loaded(self):
        """La ventana principal avisa al terminar de cargar una nube."""
        if STEPS[self.pos][0] in ('inicio', 'tamano', 'impresora', 'resumen'):
            self._go(self.pos, forward=False)

    # ------------------------------------------------------------------ páginas
    def _page_inicio(self, lay, forward):
        m = self.main
        if self.stats is not None:
            import os
            L, W = self.stats['length'], self.stats['width']
            txt = (f'<b>Nube cargada:</b> {os.path.basename(m.path)} · {len(m.xyz):,} puntos<br>'
                   f'Cara del relieve: {L:.0f} × {W:.0f} (unidades de la nube) · profundidad ≈ '
                   f'{self.stats.get("depth", 0):.0f}<br>'
                   f'El escaneo tiene detalle para celdas de {self.stats["res_scan"]:.2f}.')
            btn = 'Abrir otra nube…'
        else:
            txt = ('<b>Todavía no hay ninguna nube cargada.</b><br>Puedes abrirla ahora o seguir sin ella: el asistente '
                   'rellenará los parámetros igualmente, pero sin nube no puede calcular la escala, si cabe en la '
                   'cama ni las capas del relieve.')
            btn = 'Abrir nube de puntos…'
        lay.addWidget(self._note(txt, 'plan'))
        row = QHBoxLayout()
        b = QPushButton(btn)
        b.clicked.connect(m.open_file)
        b.setEnabled(not m.busy())
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(14)
        lay.addWidget(self._note(
            '<b>Qué vas a decidir</b><ol>'
            '<li><b>Para qué:</b> una réplica para tu impresora o una copia a tamaño real.</li>'
            '<li><b>Qué has escaneado:</b> una cara en relieve, plana o sobre una columna; grabados sobre una '
            'roca; un bloque con sus laterales; un objeto por todos los lados, o un terreno o edificio.</li>'
            '<li><b>Qué quieres hacer:</b> copia, placa o molde; maqueta o fachada. Solo si hay donde elegir.</li>'
            '<li><b>Impresora:</b> filamento o resina, boquilla, capa y cama.</li>'
            '<li><b>Estado del escaneo:</b> letras finas, relieve gastado, ruido o partes que vuelan.</li>'
            '<li><b>Tamaño:</b> recuerdo, normal, exposición o a medida, con el cálculo de escala, cama y capas al '
            'momento. En una copia a tamaño real, en vez de impresora y tamaño: unidades, uso y detalle.</li></ol>'
            'Las preguntas cambian según lo que elijas: por ejemplo, el marco solo se pregunta para placas y moldes, '
            'y la boquilla solo en impresoras de filamento.', 'wizIntro'))
        lay.addStretch(1)

    def _page_destino(self, lay, forward):
        grp = QButtonGroup(self)
        opts = (('imprimir', 'Imprimir una réplica',
                 'A escala, para tu impresora 3D: el tamaño, la boquilla, la capa y la cama deciden el detalle y la '
                 'simplificación. Lo normal para piezas de museo, recuerdos o prácticas.'),
                ('real', 'Copia a tamaño real (1:1)',
                 'Sin reducir: para archivarla y verla, fresarla en CNC, imprimirla por piezas en gran formato o '
                 'medir sobre ella. Sin impresora ni escala: el detalle y la simplificación van en mm reales. La '
                 'malla sale cerrada (con base).'))
        for key, title, desc in opts:
            rb = QRadioButton(title)
            rb.setObjectName('optTitle')
            rb.setChecked(self.p.target == key)
            rb.toggled.connect(lambda on, k=key: on and self._target(k))
            grp.addButton(rb)
            lay.addWidget(rb)
            d = self._note(desc)
            d.setContentsMargins(24, 0, 0, 8)
            lay.addWidget(d)
        if self.stats is not None:
            L = max(self.stats['length'], self.stats['width'])
            u = (getattr(self.main, 'load_info', None) or {}).get('units', 'mm')
            lay.addWidget(self._note(f'<b>Tu nube</b> mide unos {_len(L)} de lado (unidades: {u}). Una pieza de más '
                                     f'de 30–40 cm no cabe en casi ninguna impresora sin reducirla o trocearla.',
                                     'plan'))
        lay.addStretch(1)

    def _target(self, key):
        self._set('target', key, 'Elegido en «¿Para qué es?»')
        self._refresh_side()

    def _leave_destino(self):
        if not relief.is_real(self.p) or self.stats is None:
            return
        base = self.main._real_base(max(self.stats['length'], self.stats['width']))
        if self.p.base == Params().base and base > self.p.base:
            self._set('base', base, 'Base proporcional a una pieza a tamaño real')

    def _page_real(self, lay, forward):
        why = 'Copia a tamaño real'
        li = getattr(self.main, 'load_info', None) or {}
        m = self.main
        if m.xyz is not None:
            import entrada
            ext = m.xyz.max(0) - m.xyz.min(0)
            raw = ext / li.get('factor', 1.0)
            lay.addWidget(self._note('<b>1. Unidades de la nube</b>' + (
                ' (el fichero no las dice: comprueba cuánto mide de verdad)' if li.get('guessed') else
                f' (elegidas: {li.get("units")})'), 'optTitle'))
            grp = QButtonGroup(self)
            cur = self.p.units if self.p.units in entrada.UNITS else li.get('units', 'mm')
            for u, f in entrada.UNITS.items():
                mm = raw * f
                b = float(mm.max())
                rb = QRadioButton(f'{u}:  ' + ' × '.join(_len(x, b).split()[0] for x in mm) + ' ' + _len(b).split()[1])
                rb.setChecked(u == cur)
                rb.toggled.connect(lambda on, u=u: on and self._units(u))
                grp.addButton(rb)
                lay.addWidget(rb)
            self.lbl_units = self._note('')
            lay.addWidget(self.lbl_units)
            lay.addSpacing(8)
        lay.addWidget(self._note('<b>2. Para qué es</b>', 'optTitle'))
        form = QFormLayout()
        form.addRow(self.label('use'), self._param_widget('use', why, self._update_real))
        form.addRow(self.label('res'), self._param_widget('res', why, self._update_real))
        if self.p.mode not in ('objeto', 'bloque'):
            form.addRow(self.label('base'), self._param_widget('base', why, self._update_real))
        lay.addLayout(form)
        lay.addWidget(self._note(
            '<b>archivo</b>: con color (PLY u OBJ) · <b>cnc</b>: STL · <b>gran formato</b>: 3MF, a trocear en el '
            'laminador · <b>medir</b>: casi sin simplificar. <b>Resolución</b> auto = la del escaneo; en los tipos 3D '
            'Poisson pone su propio límite, que se ve debajo.'))
        lay.addSpacing(6)
        self.lbl_plan = self._note('', 'plan')
        lay.addWidget(self.lbl_plan)
        lay.addStretch(1)
        self._update_real()

    def _units(self, u):
        li = getattr(self.main, 'load_info', None) or {}
        if u == li.get('units') and self.base.units == 'auto':     # las supuestas: sin releer la nube
            self._set('units', 'auto', 'Unidades confirmadas para el tamaño real')
        else:
            self._set('units', u, 'Unidades confirmadas para el tamaño real')
        self._update_real()

    def _update_real(self):
        li = getattr(self.main, 'load_info', None) or {}
        same = self.p.units in ('auto', li.get('units'))
        if hasattr(self, 'lbl_units'):
            self.lbl_units.setText('' if same else
                                   f'<span style="color:{self.C["accent_dk"]}">Al aplicar, la nube se vuelve a leer '
                                   f'en {self.p.units}; el cálculo de abajo aún es con {li.get("units")}.</span>')
        if self.stats is None:
            self.lbl_plan.setText('Abre una nube de puntos (primer paso) para ver la medida, el detalle y hasta dónde '
                                  'llega Poisson.')
            return
        self.lbl_plan.setText(self.plan_html(self.stats, self.p))

    def _page_tipo(self, lay, forward):
        self.grp_kind = QButtonGroup(self)
        for it in recetas.KINDS:
            rb = QRadioButton(it['title'])
            rb.setObjectName('optTitle')
            rb.setChecked(it['key'] == self.kind)
            rb.toggled.connect(lambda on, k=it['key']: self._kind_toggled(on, k))
            rb.setToolTip(it['desc'])
            self.grp_kind.addButton(rb)
            lay.addWidget(rb)
            ex = it['desc'].split(':')[0]          # los ejemplos de la descripción y la frase corta
            d = self._note(f'{ex}. {it["line"]}' if len(ex) < 90 else it['line'])
            d.setContentsMargins(24, 0, 0, 8)
            lay.addWidget(d)
        if self.stats is not None:
            st = self.stats
            g = (getattr(self.main, 'load_info', None) or {}).get('guess')
            seems = ''
            if g:
                it = next(k for k in recetas.KINDS if k['key'] == g[0])
                seems = f'<br>Por su forma parece <b>{it["title"].lower()}</b>: {g[1]}.'
            lay.addWidget(self._note(f'<b>Tu nube:</b> {st["length"]:.0f} × {st["width"]:.0f} mm de cara y unos '
                                     f'{st.get("depth", 0):.0f} mm de fondo.' + seems, 'plan'))
        lay.addStretch(1)

    def _kind_toggled(self, on, key):
        if not on:
            return
        self.kind = key
        opts = recetas.purposes_of(key)
        if self.purpose not in [o['key'] for o in opts]:
            self.purpose = opts[0]['key']
        self._refresh_side()

    def _leave_tipo(self):
        if not self._visible('objetivo'):          # un solo objetivo para este tipo: se aplica sin preguntar
            self._leave_objetivo()

    def _page_objetivo(self, lay, forward):
        self.grp_purpose = QButtonGroup(self)
        for it in recetas.purposes_of(self.kind):
            rb = QRadioButton(it['title'])
            rb.setObjectName('optTitle')
            rb.setChecked(it['key'] == self.purpose)
            rb.toggled.connect(lambda on, k=it['key']: self._purpose_toggled(on, k))
            self.grp_purpose.addButton(rb)
            lay.addWidget(rb)
            d = self._note(it['desc'])
            d.setContentsMargins(24, 0, 0, 8)
            lay.addWidget(d)
        lay.addStretch(1)

    def _purpose_toggled(self, on, key):
        if on:
            self.purpose = key
        self._refresh_side()

    def _refresh_side(self):
        for j, lb in enumerate(self.side_labels):
            if j != self.pos:
                lb.setProperty('state', 'off' if not self._visible(STEPS[j][0]) else
                               'done' if j < self.pos else '')
                lb.style().unpolish(lb)
                lb.style().polish(lb)

    def _leave_objetivo(self):
        it = self._purpose()
        why = f'Objetivo «{it["title"]}»'
        touched = set(recetas.PURPOSE_BASE) | set(recetas.keys(recetas.PURPOSES))
        for k in touched:          # partir de un estado limpio: no arrastrar lo de otro objetivo elegido antes
            v = recetas.PURPOSE_BASE.get(k, getattr(self.base, k))
            setattr(self.p, k, v)
            self.reasons.pop(k, None)
        for k, v in {**recetas.PURPOSE_BASE, **it['params']}.items():
            self._set(k, v, why)
        for k in it.get('auto', []):          # valores que dependen del tamaño de la nube
            self._set(k, recetas.auto_value(k, self.stats), why)
        self.purpose_mode = self.p.mode

    def _page_acabado(self, lay, forward):
        it = self._purpose()
        why = f'Acabado de «{it["title"]}»'
        form = QFormLayout()
        form.setVerticalSpacing(2)
        for name in it.get('ask', []):
            _, label, kind, _, tip = self.spec[name]
            extra = None
            if name == 'behind' and self.stats is not None:
                extra = self._note('', 'plan')
                upd = lambda e=extra: e.setText(behind_html(self.stats, self.p))      # noqa: E731
                upd()
            w = self._param_widget(name, why, upd if extra is not None else None)
            if kind == 'b':
                form.addRow(w)
            else:
                form.addRow(label, w)
            n = self._note(tip)
            n.setContentsMargins(0, 0, 0, 6)
            form.addRow('', n)
            if extra is not None:
                form.addRow(extra)
        lay.addLayout(form)
        if it['key'] == 'molde':
            lay.addWidget(self._note('<b>Molde:</b> las alturas se invierten, de modo que lo que sobresale en la '
                                     'piedra queda hundido. Activa <i>Espejar</i> si es un sello y el texto debe '
                                     'quedar al derecho en la copia.', 'plan'))
        lay.addStretch(1)

    def _page_impresora(self, lay, forward):
        p = self.p
        resin = p.nozzle < 0.1
        tech_row = QHBoxLayout()
        self.rb_fdm = QRadioButton('Filamento (FDM)')
        self.rb_resin = QRadioButton('Resina (SLA/MSLA)')
        (self.rb_resin if resin else self.rb_fdm).setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.rb_fdm)
        g.addButton(self.rb_resin)
        tech_row.addWidget(self.rb_fdm)
        tech_row.addWidget(self.rb_resin)
        tech_row.addStretch(1)
        lay.addLayout(tech_row)
        form = QFormLayout()
        form.setVerticalSpacing(6)
        self.cb_printer = QComboBox()
        self.cb_printer.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)   # nombres largos de
        self.cb_printer.setMinimumContentsLength(28)                                            # los laminadores
        self.cb_printer.setMaxVisibleItems(24)
        self.cb_printer.setToolTip('Tus impresoras (las guardadas en nube2stl y las de tus laminadores) y modelos '
                                   'habituales. Al elegir una tuya se copian su cama, boquilla y capa.')
        form.addRow('Impresora', self.cb_printer)
        prow = QHBoxLayout()
        self.lbl_found = QLabel()
        self.lbl_found.setObjectName('muted')
        self.lbl_found.setWordWrap(True)
        prow.addWidget(self.lbl_found, 1)
        self.btn_psave = QPushButton('Guardar como mía…')
        self.btn_psave.setToolTip('Guarda la cama, boquilla y capa actuales como una impresora propia, para '
                                  'elegirla la próxima vez')
        self.btn_psave.clicked.connect(self._save_printer)
        self.btn_pdel = QPushButton('Quitar')
        self.btn_pdel.setToolTip('Quita de la lista una impresora guardada en nube2stl')
        self.btn_pdel.clicked.connect(self._delete_printer)
        prow.addWidget(self.btn_psave)
        prow.addWidget(self.btn_pdel)
        form.addRow('', prow)
        why = 'Impresora elegida en el asistente'
        self.w_nozzle = self._param_widget('nozzle', why, self._update_printer_info)
        self.w_layer = self._param_widget('layer', why, self._update_printer_info)
        self.w_bx = self._param_widget('bed_x', why, self._update_printer_info)
        self.w_by = self._param_widget('bed_y', why, self._update_printer_info)
        self.w_quality = self._param_widget('quality', why, self._update_printer_info)
        self.lbl_nozzle = QLabel('Boquilla')
        form.addRow(self.lbl_nozzle, self.w_nozzle)
        form.addRow(self.label('layer'), self.w_layer)
        bed = QHBoxLayout()
        bed.addWidget(self.w_bx)
        bed.addWidget(QLabel('×'))
        bed.addWidget(self.w_by)
        form.addRow('Cama (ancho × fondo)', bed)
        form.addRow(self.label('quality'), self.w_quality)
        lay.addLayout(form)
        self.lbl_printer = self._note('', 'plan')
        lay.addWidget(self.lbl_printer)
        lay.addStretch(1)
        self._fill_printers()
        self.rb_fdm.toggled.connect(self._tech_changed)
        self.cb_printer.currentIndexChanged.connect(lambda _: self._printer_changed())
        self._update_printer_info()

    def _printers(self, reload=False):
        if reload or not hasattr(self, '_pgroups'):
            self._pgroups = impresoras.groups()       # propias, de los laminadores y modelos habituales
        return self._pgroups

    def _fill_printers(self, select=None):
        tech = 'resina' if self.rb_resin.isChecked() else 'fdm'
        cb = self.cb_printer
        cb.blockSignals(True)
        cb.clear()
        found = []
        best = None
        for title, items in self._printers():
            items = [x for x in items if x['tech'] == tech]
            if not items:
                continue
            cb.addItem(f'— {title} —')
            it = cb.model().item(cb.count() - 1)
            it.setEnabled(False)
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            if title.startswith('Mis impresoras en '):
                found.append(f'{len(items)} en {title[18:]}')
            for x in items:
                cb.addItem(f'   {x["name"]}   ·   {x["bed_x"]:g} × {x["bed_y"]:g} · {x["nozzle"]:g} mm', x)
                i = cb.count() - 1
                if select is not None and x['name'] == select:
                    best = i
                # la que coincida con los valores actuales (las tuyas primero, que van antes en la lista)
                if best is None and select is None and (x['bed_x'], x['bed_y']) == (self.p.bed_x, self.p.bed_y) \
                        and (x['source'] == 'modelos' or abs(x['nozzle'] - self.p.nozzle) < 1e-6):
                    best = i
        cb.addItem(OTHER)
        cb.setCurrentIndex(best if best is not None else cb.count() - 1)
        cb.blockSignals(False)
        self.lbl_nozzle.setText('Detalle (píxel)' if tech == 'resina' else 'Boquilla')
        if tech == 'resina':
            self.lbl_found.setText('')
        elif found:
            self.lbl_found.setText('Encontradas en tus laminadores: ' + ', '.join(found) + '.')
        else:
            self.lbl_found.setText('No se ha encontrado OrcaSlicer, Bambu Studio, PrusaSlicer ni Cura con impresoras '
                                   'configuradas. Puedes guardar la tuya con «Guardar como mía…».')
        self._update_printer_buttons()

    def _current_printer(self):
        d = self.cb_printer.currentData()
        return d if isinstance(d, dict) else None

    def _update_printer_buttons(self):
        cur = self._current_printer()
        self.btn_pdel.setEnabled(cur is not None and cur['source'] == 'nube2stl')

    def _tech_changed(self, fdm):
        if fdm and self.p.nozzle < 0.1:            # vuelta a filamento desde resina: valores típicos de FDM
            self.w_nozzle.setValue(0.4)
            self.w_layer.setValue(0.2)
        self._fill_printers()
        if self._current_printer() is None:       # al cambiar de tecnología, la primera impresora de esa clase
            for i in range(self.cb_printer.count()):
                if isinstance(self.cb_printer.itemData(i), dict):
                    self.cb_printer.setCurrentIndex(i)
                    break
        else:
            self._printer_changed()

    def _printer_changed(self):
        self._update_printer_buttons()
        pr = self._current_printer()
        if pr is None:
            return
        self._applying = True
        self.w_bx.setValue(pr['bed_x'])
        self.w_by.setValue(pr['bed_y'])
        # una impresora tuya trae su boquilla y capa; de un modelo genérico solo se toma la cama
        # (así no se pisa, por ejemplo, la boquilla de 0,6 que propone «pieza grande»)
        if pr['source'] != 'modelos' or pr['tech'] == 'resina':
            self.w_nozzle.setValue(pr['nozzle'])
            self.w_layer.setValue(pr['layer'])
        self._applying = False
        self._update_printer_info()
        src = '' if pr['source'] in ('modelos', 'nube2stl') else f' de {pr["source"]}'
        for k in ('bed_x', 'bed_y', 'nozzle', 'layer'):
            self.reasons[k] = f'Impresora «{pr["name"]}»{src}'

    def _save_printer(self):
        cur = self._current_printer()
        name, ok = QInputDialog.getText(self, 'Guardar impresora', 'Nombre de la impresora:',
                                        text=cur['name'] if cur else 'Mi impresora')
        name = name.strip()
        if not ok or not name:
            return
        p = self.p
        impresoras.save_own(dict(name=name, tech='resina' if self.rb_resin.isChecked() else 'fdm',
                                 bed_x=p.bed_x, bed_y=p.bed_y, nozzle=p.nozzle, layer=p.layer))
        self._printers(reload=True)
        self._fill_printers(select=name)
        self.main.say(f'Impresora «{name}» guardada en {impresoras.own_file()}')

    def _delete_printer(self):
        cur = self._current_printer()
        if cur is None or cur['source'] != 'nube2stl':
            return
        if QMessageBox.question(self, 'Quitar impresora', f'¿Quitar «{cur["name"]}» de tus impresoras?') \
                != QMessageBox.Yes:
            return
        impresoras.delete_own(cur['name'])
        self._printers(reload=True)
        self._fill_printers()

    def _update_printer_info(self):
        p = self.p
        pr = self._current_printer() if hasattr(self, 'cb_printer') else None
        if pr is not None and not getattr(self, '_applying', False):
            differs = (pr['bed_x'], pr['bed_y']) != (p.bed_x, p.bed_y) or \
                (pr['source'] != 'modelos' and abs(pr['nozzle'] - p.nozzle) > 1e-6)
            if differs:                 # retocado a mano: ya no es esa impresora
                self.cb_printer.blockSignals(True)
                self.cb_printer.setCurrentIndex(self.cb_printer.count() - 1)
                self.cb_printer.blockSignals(False)
                self._update_printer_buttons()
        tol = relief.tolerance(p)
        resin = p.nozzle < 0.1
        txt = (f'<b>Detalle horizontal:</b> la impresora reproduce detalles de ≈ {p.nozzle / 2:.2f} mm '
               f'({"medio píxel" if resin else "media boquilla"}).<br>'
               f'<b>Detalle vertical:</b> capas de {p.layer:.2f} mm.<br>'
               f'<b>Simplificación:</b> con calidad «{p.quality}» la malla puede separarse como mucho '
               f'{tol:.3f} mm del escaneo; por debajo de eso la impresora no nota la diferencia.')
        if not resin and p.nozzle <= 0.25:
            txt += '<br>Boquilla fina: más detalle, pero la impresión tarda bastante más.'
        if self.stats is not None:
            pl = relief.print_plan(self.stats, p)
            why = {'impresora': 'la impresora', 'escaneo': 'el escaneo', 'manual': 'tu valor manual'}[pl['why']]
            txt += (f'<br><b>Con el tamaño actual ({p.target_len:.0f} mm):</b> celda de {pl["cell_print"]:.2f} mm, '
                    f'limitada por {why}.')
        self.lbl_printer.setText(txt)

    def _page_detalle(self, lay, forward):
        # deshacer lo que pusieron estas opciones la vez anterior (salvo lo que se haya cambiado después a mano)
        for k, (before, put) in self.cond_applied.items():
            if getattr(self.p, k) == put:
                setattr(self.p, k, before)
                self.reasons.pop(k, None)
        self.cond_applied = {}
        self.chk_cond = {}
        for it in self._conditions():
            cb = QCheckBox(it['title'])
            cb.setObjectName('optTitle')
            cb.setChecked(it['key'] in self.cond_checked)
            self.chk_cond[it['key']] = cb
            lay.addWidget(cb)
            ch = ' · '.join(f'{self.label(k)} {self.fmt(k, v)}' for k, v in it['params'].items())
            txt = f'{it["desc"]}<br><span style="color:{self.C["teal"]}">Cambia: {ch}</span>'
            if it.get('note'):
                txt += f'<br><i>{it["note"]}</i>'
            d = self._note(txt)
            d.setContentsMargins(24, 0, 0, 8)
            lay.addWidget(d)
        lay.addWidget(self._note('Si marcas varias, se aplican en este orden y la última manda (por ejemplo, '
                                 'con letras finas y ruido, el suavizado será el del ruido).'))
        lay.addStretch(1)

    def _leave_detalle(self):
        self.cond_checked = {k for k, cb in self.chk_cond.items() if cb.isChecked()}
        for it in self._conditions():
            if it['key'] in self.cond_checked:
                for k, v in it['params'].items():
                    before = self.cond_applied[k][0] if k in self.cond_applied else getattr(self.p, k)
                    self._set(k, v, f'Escaneo: {it["title"][0].lower() + it["title"][1:]}')
                    self.cond_applied[k] = (before, v)

    def _page_tamano(self, lay, forward):
        why = 'Tamaño elegido en el asistente'
        quick = QHBoxLayout()
        quick.setSpacing(6)
        tips = []
        for it in recetas.SIZES:
            b = QPushButton(it['title'])
            ch = recetas.size_params(it, self.p.mode, self._fdm())
            more = ' · '.join(f'{self.label(k)} {self.fmt(k, v)}' for k, v in ch.items() if k != 'target_len')
            b.setToolTip(it['desc'] + (f'\nCambia también: {more}' if more else ''))
            b.clicked.connect(lambda _=False, it=it: self._size_preset(it))
            quick.addWidget(b)
            tips.append(f'<b>{it["title"].split(" · ")[0]}</b>: {it["desc"]}' + (f' <span style="color:'
                        f'{self.C["teal"]}">También {more}.</span>' if more else ''))
        quick.addStretch(1)
        lay.addLayout(quick)
        n = self._note('<br>'.join(tips))
        n.setContentsMargins(0, 2, 0, 8)
        lay.addWidget(n)
        form = QFormLayout()
        self.w_len = self._param_widget('target_len', why, self._update_size)
        row = QHBoxLayout()
        row.addWidget(self.w_len, 1)
        self.btn_fit = QPushButton('Ajustar a la cama')
        self.btn_fit.setToolTip('El mayor tamaño (en múltiplos de 5 mm) que cabe en la cama, girándola si hace falta')
        self.btn_fit.clicked.connect(self._fit_bed)
        row.addWidget(self.btn_fit)
        form.addRow(self.label('target_len'), row)
        self.w_ratio = self._param_widget('ratio', why, self._ratio_changed)
        form.addRow(self.label('ratio'), self.w_ratio)
        obj = self.p.mode == 'objeto'
        if self.p.mode not in ('objeto', 'bloque'):     # ahí no hay relieve que exagerar ni base añadida
            self.w_z = self._param_widget('z_scale', why, self._update_size)
            form.addRow(self.label('z_scale'), self.w_z)
            self.w_base = self._param_widget('base', why, self._update_size)
            form.addRow(self.label('base'), self.w_base)
        lay.addLayout(form)
        if obj:
            lay.addWidget(self._note('El <b>tamaño</b> es el del lado mayor del objeto en 3D (alto incluido) y fija la '
                                     'escala: el detalle mínimo imprimible es siempre media boquilla, así que un '
                                     'objeto mayor muestra más detalle (hasta lo que permita el escaneo).'))
        else:
            lay.addWidget(self._note('El <b>tamaño</b> fija la escala: el detalle mínimo imprimible es siempre media '
                                     'boquilla, así que una pieza mayor muestra más detalle de la piedra (hasta lo que '
                                     'permita el escaneo). La <b>exageración</b> multiplica las alturas del relieve '
                                     'sin cambiar el contorno: útil si salen pocas capas.'))
        self.lbl_plan = self._note('', 'plan')
        lay.addWidget(self.lbl_plan)
        lay.addStretch(1)
        self._ratio_changed()

    def _fdm(self):
        return self.p.nozzle >= 0.1                     # resina: «boquilla» de 0,05 (como en el paso Impresora)

    def _size_preset(self, it):
        why = f'Tamaño «{it["title"].split(" · ")[0]}»'
        for k, v in recetas.size_params(it, self.p.mode, self._fdm()).items():
            self._set(k, v, why)
        if self.p.ratio > 0:                           # la escala fija mandaría sobre el lado mayor
            self._set('ratio', 0.0, why)
        self._go(self.pos, forward=False)               # rehacer la página con los valores nuevos

    def _ratio_changed(self):
        free = self.p.ratio <= 0                      # con escala fija, el lado mayor no cuenta
        self.w_len.setEnabled(free)
        self.btn_fit.setEnabled(free)
        self._update_size()

    def _fit_bed(self):
        st, p = self.stats, self.p
        if st is None or p.bed_x <= 0 or p.bed_y <= 0:
            return
        bmax, bmin = max(p.bed_x, p.bed_y), min(p.bed_x, p.bed_y)
        if p.mode == 'objeto' and 'obj' in st:        # la planta del objeto de pie debe caber; el lado mayor es 3D
            ex, ey, ez = relief._object_extents(st['obj'], p)
            s = min(bmax / max(ex, ey), bmin / min(ex, ey))
            self.w_len.setValue(max(5.0, int(max(ex, ey, ez) * s / 5) * 5))
            return
        s = min((bmax - 2 * p.frame) / st['length'], (bmin - 2 * p.frame) / st['width'])
        self.w_len.setValue(max(5.0, int(st['length'] * s / 5) * 5))

    def _update_size(self):
        if self.stats is None:
            self.lbl_plan.setText('Abre una nube de puntos (primer paso) para ver la escala, si cabe en la cama y '
                                  'las capas del relieve.')
            self.btn_fit.setEnabled(False)
            return
        self.btn_fit.setEnabled(self.p.bed_x > 0 and self.p.bed_y > 0)
        self.lbl_plan.setText(self.plan_html(self.stats, self.p))

    def _page_resumen(self, lay, forward):
        rows = []
        for f in Params.__dataclass_fields__:
            old, new = getattr(self.base, f), getattr(self.p, f)
            if old != new:
                rows.append(f'<tr><td><b>{self.label(f)}</b></td><td>{self.fmt(f, old)}</td>'
                            f'<td>→ <b>{self.fmt(f, new)}</b></td>'
                            f'<td style="color:{self.C["muted"]}">{self.reasons.get(f, "")}</td></tr>')
        if rows:
            html = ('<table cellspacing="0" cellpadding="3">'
                    '<tr><th align="left">Parámetro</th><th align="left">Ahora</th><th align="left">Nuevo</th>'
                    '<th align="left">Por qué</th></tr>' + ''.join(rows) + '</table>')
        else:
            html = 'No cambia ningún parámetro: los valores actuales ya coinciden con lo elegido.'
        lay.addWidget(self._note(html, 'wizIntro'))
        if self.stats is not None:
            lay.addSpacing(6)
            lay.addWidget(self._note(self.plan_html(self.stats, self.p), 'plan'))
        lay.addStretch(1)
        self.chk_gen = QCheckBox('Generar la pieza al aplicar')
        can = self.stats is not None and not self.main.busy()
        self.chk_gen.setChecked(can)
        self.chk_gen.setEnabled(can)
        self.chk_gen.toggled.connect(
            lambda _: self.btn_next.setText('Aplicar y generar' if self._gen_wanted() else 'Aplicar'))
        lay.addWidget(self.chk_gen)

    def _finish(self):
        changed = [f for f in Params.__dataclass_fields__ if getattr(self.base, f) != getattr(self.p, f)]
        gen = self._gen_wanted()
        self.main.set_params(self.p)
        self.main.say('Asistente: ' + (', '.join(f'{self.label(f)} = {self.fmt(f, getattr(self.p, f))}'
                                                 for f in changed) or 'sin cambios'))
        self.accept()
        if gen:
            self.main.run_recon()
