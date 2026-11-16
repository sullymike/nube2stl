"""Aviso de memoria de nube2stl: ventana que hay que aceptar antes de una generación que puede agotar la memoria
(o después de que el sistema la haya cortado por falta de memoria).

Muestra la estimación frente a la memoria disponible y los parámetros que bajan el consumo, con los mismos
controles que las pestañas, un valor sugerido para cada uno y la estimación actualizada al momento. Los cálculos
están en relief.memory_estimate / relief.memory_suggestions; aquí solo hay interfaz.
"""
import copy

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
                             QPushButton, QStyle, QVBoxLayout)

import relief
from relief import Params


class MemoryDialog(QDialog):
    def __init__(self, main, prm, stats, npoints, make_widget, spec, label, colors, failed=False):
        super().__init__(main)
        self.p = copy.copy(prm)
        self.p0 = copy.copy(prm)
        self.stats, self.npoints, self.spec, self.label, self.C = stats, npoints, spec, label, colors
        self.make_widget = make_widget
        self.choice = None                       # 'aplicar' | 'igual' | None (cancelar)
        self.setWindowTitle('Memoria insuficiente' if failed else 'Aviso de memoria')
        self.setModal(True)
        self.setMinimumWidth(820)
        C = colors
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(self.style().standardIcon(QStyle.SP_MessageBoxCritical if failed
                                               else QStyle.SP_MessageBoxWarning).pixmap(44, 44))
        head.addWidget(ic, 0, Qt.AlignTop)
        self.lbl_head = QLabel()
        self.lbl_head.setWordWrap(True)
        self.lbl_head.setTextFormat(Qt.RichText)
        head.addWidget(self.lbl_head, 1)
        lay.addLayout(head)
        if failed:
            intro = ('<b>El sistema ha cerrado la generación por falta de memoria.</b> Para que salga, hay que '
                     'bajar el consumo con alguno de estos parámetros:')
        else:
            intro = ('<b>Esta generación puede agotar la memoria del equipo.</b> Si se agota, el ordenador se '
                     'ralentiza mucho y el sistema puede cerrar el proceso. Puedes bajar el consumo con estos '
                     'parámetros:')
        self.lbl_head.setText(f'<span style="font-size:11pt">{intro}</span>')

        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setMinimumHeight(24)
        lay.addWidget(self.bar)
        self.lbl_est = QLabel()
        self.lbl_est.setWordWrap(True)
        self.lbl_est.setTextFormat(Qt.RichText)
        lay.addWidget(self.lbl_est)

        box = QFrame()
        box.setObjectName('memBox')
        box.setStyleSheet(f'#memBox {{ background: white; border: 1px solid {C["line"]}; border-radius: 8px; }}')
        self.grid = QGridLayout(box)
        self.grid.setContentsMargins(12, 10, 12, 10)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(4)
        for col, t in enumerate(['Parámetro', 'Valor', 'Sugerido', '']):
            h = QLabel(f'<b>{t}</b>')
            h.setStyleSheet(f'color: {C["muted"]};')
            self.grid.addWidget(h, 0, col)
        self.widgets, self.sugg_btn = {}, {}
        lay.addWidget(box)
        self.lbl_tip = QLabel('Cerrar otros programas (navegador, otros visores 3D…) también libera memoria. '
                              'Los cambios se copian a las pestañas al pulsar «Aplicar y generar».')
        self.lbl_tip.setObjectName('muted')
        self.lbl_tip.setWordWrap(True)
        lay.addWidget(self.lbl_tip)

        btns = QHBoxLayout()
        b_cancel = QPushButton('Cancelar')
        b_cancel.clicked.connect(self.reject)
        self.b_anyway = QPushButton('Generar igualmente')
        self.b_anyway.setToolTip('Generar con los valores originales, asumiendo el riesgo')
        self.b_anyway.clicked.connect(lambda: self._done('igual'))
        self.b_apply = QPushButton('Aplicar y generar')
        self.b_apply.setObjectName('primary')
        self.b_apply.setDefault(True)
        self.b_apply.clicked.connect(lambda: self._done('aplicar'))
        btns.addWidget(b_cancel)
        btns.addStretch(1)
        btns.addWidget(self.b_anyway)
        btns.addWidget(self.b_apply)
        lay.addLayout(btns)
        if failed:
            self.b_anyway.setText('Reintentar igual')
        self._rows()
        self._update()
        self.adjustSize()

    # ------------------------------------------------------------------
    def _rows(self):
        sugg = relief.memory_suggestions(self.stats, self.p0, self.npoints)
        r = 1
        for name, value, why in sugg:
            _, label, kind, opt, tip = self.spec[name]
            w = self.make_widget(kind, opt, getattr(self.p, name), label)
            w.setToolTip(tip)
            sig = getattr(w, 'valueChanged', None) or getattr(w, 'currentTextChanged', None) or w.toggled
            sig.connect(lambda *_, n=name, ww=w: self._changed(n, ww))
            self.widgets[name] = w
            self.grid.addWidget(QLabel(label), r, 0)
            self.grid.addWidget(w, r, 1)
            if value is None:
                s = QLabel('—')
                self.grid.addWidget(s, r, 2)
            else:
                b = QPushButton(f'Usar {self._fmt(name, value)}')
                b.clicked.connect(lambda _=False, ww=w, v=value: self._set_widget(ww, v))
                self.grid.addWidget(b, r, 2)
                self.sugg_btn[name] = b
            w.setMinimumWidth(150)
            n = QLabel(why)
            n.setObjectName('muted')
            n.setWordWrap(True)
            n.setFixedWidth(330)
            n.setMinimumHeight(n.heightForWidth(330))
            self.grid.addWidget(n, r, 3)
            self.grid.setRowMinimumHeight(r, max(34, n.heightForWidth(330) + 4))
            r += 1
        self.grid.setColumnStretch(3, 1)

    def _fmt(self, name, v):
        _, _, kind, opt, _ = self.spec[name]
        if kind in 'fi' and opt.get('special') and v == opt['lo']:
            return opt['special']
        if kind == 'f':
            return f'{v:.{opt.get("dec", 1)}f}'.replace('.', ',') + (' ' + opt['suf'] if opt.get('suf') else '')
        return str(v) + (' ' + opt['suf'] if kind == 'i' and opt.get('suf') else '')

    @staticmethod
    def _set_widget(w, v):
        if isinstance(w, QCheckBox):
            w.setChecked(bool(v))
        elif isinstance(w, QComboBox):
            w.setCurrentText(str(v))
        else:
            w.setValue(v)

    def _changed(self, name, w):
        if isinstance(w, QCheckBox):
            v = w.isChecked()
        elif isinstance(w, QComboBox):
            v = w.currentText()
        else:
            v = type(getattr(Params(), name))(w.value())
        setattr(self.p, name, v)
        self._update()

    def _update(self):
        e0 = relief.memory_estimate(self.stats, self.p0, self.npoints)
        e = relief.memory_estimate(self.stats, self.p, self.npoints)
        C = self.C
        pct = int(100 * e['gb'] / max(e['avail'], 0.01))
        self.bar.setMaximum(max(100, pct))
        self.bar.setValue(pct)
        self.bar.setFormat(f'≈ {e["gb"]:.1f} GB de {e["avail"]:.1f} GB disponibles ({pct} %)')
        col = {'ok': C['ok'], 'justo': '#d68a1a', 'excede': C['bad']}[e['level']]
        self.bar.setStyleSheet(f'QProgressBar {{ border: 1px solid {C["line"]}; border-radius: 6px; '
                               f'background: white; text-align: center; color: #1b2a33; font-weight: 600; }} '
                               f'QProgressBar::chunk {{ background: {col}; border-radius: 5px; }}')
        verdict = {'ok': f'<span style="color:{C["ok"]}">✔ Cabe con margen.</span>',
                   'justo': f'<span style="color:#b36b00">⚠ Va justo: el equipo irá lento mientras genera.</span>',
                   'excede': f'<span style="color:{C["bad"]}">✘ No cabe en la memoria disponible.</span>'}[e['level']]
        changed = e['gb'] != e0['gb']
        self.lbl_est.setText(
            (f'Con los valores originales: ≈ {e0["gb"]:.1f} GB · con los nuevos: <b>≈ {e["gb"]:.1f} GB</b>. '
             if changed else f'Estimación: <b>≈ {e["gb"]:.1f} GB</b> ') +
            f'({e["detail"]}; el equipo tiene {e["total"]:.0f} GB en total). {verdict}')
        self.b_apply.setEnabled(changed)

    def _done(self, choice):
        self.choice = choice
        self.accept()
