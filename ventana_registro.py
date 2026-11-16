"""Ventana «Registro de piezas»: buscar, ver, anotar y repetir las piezas generadas (ver registro.py)."""
import os

from PyQt5.QtCore import QSize, Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QIcon, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

import registro

COLS = [('id', 'Nº'), ('fecha', 'Fecha'), ('pieza', 'Pieza'), ('nube', 'Nube'), ('medidas', 'Medidas (mm)'),
        ('escala', 'Escala'), ('modo', 'Método'), ('fid', 'Desv. p95'), ('notas', 'Notas')]


def _open(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class RegistryDialog(QDialog):
    def __init__(self, main, colors):
        super().__init__(main)
        self.main, self.C = main, colors
        self.setWindowTitle('Registro de piezas')
        self.resize(1180, 700)
        self.items, self.shown = [], []
        lay = QVBoxLayout(self)
        # carpeta y búsqueda
        top = QHBoxLayout()
        self.dir_lbl = QLabel()
        self.dir_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.dir_lbl, 1)
        for text, tip, fn in [('Cambiar carpeta…', 'Elegir dónde se guarda el registro. Puede ser una carpeta\n'
                               'compartida o sincronizada (OneDrive, red): así todos los\nordenadores ven las mismas '
                               'piezas.', self.change_dir),
                              ('Abrir carpeta', 'Abrir la carpeta del registro en el explorador',
                               lambda: _open(registro.registry_dir())),
                              ('Exportar CSV…', 'Guardar la lista (la de la búsqueda actual) para Excel / LibreOffice',
                               self.export_csv)]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            top.addWidget(b)
        lay.addLayout(top)
        srow = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar: número, nombre, nube, rótulo, notas, fecha (2026-11), método, escala…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self._filter_timer.start())
        srow.addWidget(self.search, 1)
        self.auto = QCheckBox('Anotar automáticamente cada pieza guardada')
        self.auto.setChecked(registro.auto_enabled())
        self.auto.setToolTip('Al pulsar Guardar (y en los lotes) la pieza se anota sola, con un número que también\n'
                             'queda grabado dentro del fichero (cabecera del STL, metadatos del 3MF).')
        self.auto.toggled.connect(lambda on: registro.save_config(registro_auto=bool(on)))
        srow.addWidget(self.auto)
        lay.addLayout(srow)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(self.fill)
        # tabla y detalle
        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(40, 40))
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)          # pieza y nube: anchos fijos, ajustables
        hh.setSectionResizeMode(3, QHeaderView.Interactive)
        hh.resizeSection(2, 150)
        hh.resizeSection(3, 130)
        hh.setSectionResizeMode(len(COLS) - 1, QHeaderView.Stretch)
        hh.setMinimumSectionSize(40)
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.table.doubleClicked.connect(lambda *_: self.repeat())
        split.addWidget(self.table)
        det = QWidget()
        dl = QVBoxLayout(det)
        dl.setContentsMargins(8, 0, 0, 0)
        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setMinimumHeight(160)
        dl.addWidget(self.thumb)
        self.name = QLineEdit()
        self.name.setPlaceholderText('Nombre de la pieza')
        self.name.editingFinished.connect(self.save_name)
        dl.addWidget(self.name)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.info.setOpenExternalLinks(False)
        self.info.linkActivated.connect(_open)
        dl.addWidget(self.info)
        dl.addWidget(QLabel('Notas (se guardan solas):'))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText('Impresora, filamento, quién la pidió, dónde está la pieza impresa, '
                                      'incidencias…')
        self.notes.setMaximumHeight(110)
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(600)
        self._notes_timer.timeout.connect(self.save_notes)
        self.notes.textChanged.connect(lambda: self._notes_timer.start())
        dl.addWidget(self.notes)
        btns = QHBoxLayout()
        self.b_repeat = QPushButton('Repetir…')
        self.b_repeat.setToolTip('Abrir la nube de origen con los mismos ajustes, lista para pulsar «Generar»\n'
                                 '(doble clic en la lista hace lo mismo)')
        self.b_repeat.clicked.connect(self.repeat)
        self.b_params = QPushButton('Usar sus ajustes')
        self.b_params.setToolTip('Poner en la ventana principal los ajustes de esta pieza (sin cambiar de nube)')
        self.b_params.clicked.connect(self.use_params)
        self.b_folder = QPushButton('Abrir carpeta')
        self.b_folder.setToolTip('Abrir la carpeta donde está la malla')
        self.b_folder.clicked.connect(self.open_folder)
        self.b_del = QPushButton('Quitar')
        self.b_del.setToolTip('Quitar esta pieza del registro (no se borra la malla ni la nube)')
        self.b_del.clicked.connect(self.remove)
        for b in (self.b_repeat, self.b_params, self.b_folder, self.b_del):
            btns.addWidget(b)
        dl.addLayout(btns)
        dl.addStretch(1)
        split.addWidget(det)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([720, 440])
        lay.addWidget(split, 1)
        self.count = QLabel()
        self.count.setStyleSheet(f'color: {colors["muted"]};')
        lay.addWidget(self.count)
        self.reload()

    # ------------------------------------------------------------------ datos
    def reload(self, keep=None):
        d = registro.registry_dir()
        self.dir_lbl.setText(f'<b>Carpeta del registro:</b> {d}')
        self.items = registro.entries(d) if os.path.isdir(d) else []
        self.fill(keep)

    def current(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        eid = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        return next((e for e in self.items if e.get('id') == eid), None)

    def fill(self, keep=None):
        keep = keep or (self.current() or {}).get('id')
        self.shown = registro.search(self.search.text(), items=self.items)
        d = registro.registry_dir()
        t = self.table
        t.setSortingEnabled(False)
        t.setRowCount(len(self.shown))
        sel = None
        for i, e in enumerate(self.shown):
            m = e.get('medidas_mm') or [0, 0, 0]
            dv = e.get('desviacion', {})
            vals = {'id': e.get('id', ''), 'fecha': e.get('fecha', '')[:16].replace('T', ' '),
                    'pieza': e.get('pieza', ''), 'nube': e.get('nube', {}).get('nombre', ''),
                    'medidas': ' × '.join(f'{v:.0f}' for v in m), 'escala': e.get('escala', ''),
                    'modo': e.get('modo', ''),
                    'fid': f'{dv["p95"]:.2f} mm'.replace('.', ',') if 'p95' in dv else '',
                    'notas': (e.get('notas', '') or '').replace('\n', ' · ')}
            for j, (k, _) in enumerate(COLS):
                it = QTableWidgetItem(str(vals[k]))
                if j == 0:
                    it.setData(Qt.UserRole, e.get('id'))
                    png = os.path.join(d, f'{e.get("id")}.png')
                    if os.path.exists(png):
                        it.setIcon(QIcon(png))
                t.setItem(i, j, it)
            if e.get('id') == keep:
                sel = i
        t.setSortingEnabled(True)
        n, tot = len(self.shown), len(self.items)
        self.count.setText(f'{n} de {tot} piezas' if n != tot else f'{tot} piezas')
        if sel is not None:
            t.selectRow(sel)
        elif self.shown:
            t.selectRow(0)
        else:
            self.show_selected()

    def show_selected(self):
        e = self.current()
        for w in (self.b_repeat, self.b_params, self.b_folder, self.b_del, self.name, self.notes):
            w.setEnabled(e is not None)
        if e is None:
            self.thumb.clear()
            self.info.setText('')
            self.name.setText('')
            self.notes.blockSignals(True)
            self.notes.setPlainText('')
            self.notes.blockSignals(False)
            return
        png = os.path.join(registro.registry_dir(), f'{e["id"]}.png')
        self.thumb.setPixmap(QPixmap(png) if os.path.exists(png) else QPixmap())
        self.name.setText(e.get('pieza', ''))
        self.notes.blockSignals(True)
        self.notes.setPlainText(e.get('notas', ''))
        self.notes.blockSignals(False)
        self.info.setText(self._details(e))

    def _details(self, e):
        C = self.C
        n = e.get('nube', {})
        m = e.get('medidas_mm') or [0, 0, 0]
        dv = e.get('desviacion', {})

        def link(p):
            ok = p and os.path.exists(p)
            name = os.path.basename(p) if p else '—'
            return (f'<a href="{p}">{name}</a>' if ok else
                    f'<span style="color:{C["bad"]}">{name} (no está)</span>' if p else name)
        cloud = registro.find_cloud(e)
        rows = [f'<b>{e["id"]}</b> · {e.get("fecha", "").replace("T", " ")[:16]} · {e.get("equipo", "")} · '
                f'nube2stl {e.get("version", "")}',
                f'<b>Nube:</b> {link(cloud or n.get("ruta", ""))} · {n.get("puntos", 0):,} puntos'.replace(',', '.')
                + (f' · {n.get("unidades")}' if n.get('unidades') else ''),
                f'<b>Medidas:</b> {m[0]:.1f} × {m[1]:.1f} × {m[2]:.1f} mm · escala {e.get("escala", "")} · '
                f'{e.get("modo", "")}',
                f'<b>Pieza:</b> {e.get("volumen_cm3", 0):.1f} cm³ · ≈ {e.get("gramos_pla", 0):.0f} g PLA'
                + (' (hueca)' if e.get('hueca') else '') + f' · {e.get("triangulos", 0):,} triángulos'.replace(',', '.')
                + (' · cerrada' if e.get('cerrada') else f' · <span style="color:{C["bad"]}">no cerrada</span>')]
        if dv:
            rows.append(f'<b>Fidelidad:</b> media {dv.get("mean", 0):.3f} mm · 95 % a menos de {dv.get("p95", 0):.3f} mm'
                        f' · {dv.get("covered", 0):.0f} % con escaneo')
        if e.get('rotulo'):
            rows.append(f'<b>Rótulo:</b> {e["rotulo"].replace("|", " / ")}')
        outs = e.get('salidas', [])
        if outs:
            rows.append('<b>Mallas:</b> ' + ' · '.join(link(o.get('ruta', '')) for o in outs))
        if e.get('ficha'):
            rows.append(f'<b>Ficha:</b> {link(e["ficha"])}')
        return '<br>'.join(rows)

    # ------------------------------------------------------------------ acciones
    def save_notes(self):
        e = self.current()
        if e is None:
            return
        txt = self.notes.toPlainText()
        if txt != e.get('notas', ''):
            new = registro.update(e['id'], notas=txt)
            if new:
                e.update(new)
                r = self.table.currentRow()
                if r >= 0:
                    self.table.item(r, len(COLS) - 1).setText(txt.replace('\n', ' · '))

    def save_name(self):
        e = self.current()
        if e is None:
            return
        txt = self.name.text().strip()
        if txt and txt != e.get('pieza'):
            new = registro.update(e['id'], pieza=txt)
            if new:
                e.update(new)
                r = self.table.currentRow()
                if r >= 0:
                    self.table.item(r, 2).setText(txt)

    def change_dir(self):
        d = QFileDialog.getExistingDirectory(self, 'Carpeta del registro de piezas', registro.registry_dir())
        if not d:
            return
        registro.save_config(registro_dir=d)
        self.reload()
        self.main.say(f'Registro de piezas en {d}')

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Exportar registro', 'registro_piezas.csv', 'CSV (*.csv)')
        if path:
            if not path.lower().endswith('.csv'):
                path += '.csv'
            if registro.write_csv(path=path, items=self.shown):
                self.main.say(f'Registro exportado: {path} ({len(self.shown)} piezas)')
            else:
                QMessageBox.warning(self, 'Exportar', f'No se pudo escribir {path} (¿está abierto en otro programa?)')

    def open_folder(self):
        e = self.current()
        if e is None:
            return
        outs = [o['ruta'] for o in e.get('salidas', []) if os.path.exists(o.get('ruta', ''))]
        _open(os.path.dirname(outs[-1]) if outs else registro.registry_dir())

    def use_params(self):
        e = self.current()
        if e is None:
            return
        self.main.use_registry_params(e)

    def repeat(self):
        e = self.current()
        if e is None:
            return
        cloud = registro.find_cloud(e)
        if cloud is None:
            n = e.get('nube', {})
            QMessageBox.information(self, 'Repetir', f'No encuentro la nube original:\n{n.get("ruta", "")}\n\n'
                                    'Elige dónde está ahora (se comprobará que es la misma).')
            ext = os.path.splitext(n.get('nombre', ''))[1]
            cloud, _ = QFileDialog.getOpenFileName(self, f'¿Dónde está {n.get("nombre", "")}?',
                                                   os.path.dirname(n.get('ruta', '')) or os.getcwd(),
                                                   f'Nube (*{ext});;Todos (*)' if ext else 'Todos (*)')
            if not cloud:
                return
        if registro.same_cloud(e, cloud) is False:
            if QMessageBox.question(self, 'Repetir', 'Ese fichero no es exactamente la misma nube con la que se '
                                    'hizo la pieza (ha cambiado su contenido).\n\n¿Usarlo igualmente?') \
                    != QMessageBox.Yes:
                return
        self.main.repeat_from_registry(e, cloud)
        self.accept()

    def remove(self):
        e = self.current()
        if e is None:
            return
        if QMessageBox.question(self, 'Quitar del registro',
                                f'¿Quitar {e["id"]} ({e.get("pieza", "")}) del registro?\n\n'
                                'La malla, la ficha y la nube no se borran; solo deja de figurar en la lista.') \
                != QMessageBox.Yes:
            return
        registro.delete(e['id'])
        self.reload()
