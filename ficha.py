"""Ficha PDF de una pieza generada con nube2stl: para el archivo y para poder repetirla igual.

Página 1: vistas de la malla (frente, lado, planta y perspectiva), medidas de la pieza, nube de origen y
parámetros (marcados los que no son los de por defecto). Páginas siguientes: registro de la generación y los
ajustes en JSON (se pueden copiar a un .json y cargarlos con «Cargar ajustes» o --ajustes).
Solo usa matplotlib (sin pyplot: vale igual en la interfaz que en la consola).
"""
import datetime
import os
import textwrap

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure

import relief
from version import __version__

A4 = (8.27, 11.69)
INK, MUTED, LINE, ACC = '#1f3a4d', '#5f7380', '#c9d3da', '#e07a2e'
STONE = np.array([0.86, 0.83, 0.79])


def _light_mesh(r, max_tris=60_000, with_dev=False):
    V, F = r.verts, r.tris
    V0 = V
    if len(F) > max_tris:
        try:
            import open3d as o3d
            m = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V.astype(np.float64)),
                                          o3d.utility.Vector3iVector(F.astype(np.int32)))
            m = m.simplify_quadric_decimation(max_tris)
            V, F = np.asarray(m.vertices), np.asarray(m.triangles)
        except ImportError:
            F = F[np.random.default_rng(0).choice(len(F), max_tris, replace=False)]
    if with_dev:
        dev = r.dev
        if dev is not None and len(V) != len(V0):
            from scipy.spatial import cKDTree
            dev = dev[cKDTree(V0).query(V)[1]]
        return np.asarray(V, float), np.asarray(F), dev
    return np.asarray(V, float), np.asarray(F)


def _dev_page(pdf, r, name):
    import desviacion
    st = desviacion.stats(r)
    if not st:
        return
    V, F, dev = _light_mesh(r, 80_000, with_dev=True)
    lim = desviacion.auto_limit(r)
    vc = desviacion.colors(dev, lim) / 255.0
    fcol = vc[F].mean(1)
    fig = Figure(figsize=A4)
    fig.text(0.06, 0.965, 'Fidelidad al escaneo', fontsize=15, color=INK, weight='bold', va='top')
    fig.text(0.06, 0.94, name, fontsize=10, color=ACC, va='top')
    fig.text(0.06, 0.918, 'Distancia de cada punto de la pieza al escaneo (mm de la pieza). Azul: a la pieza le '
             'falta material; rojo: le sobra; gris: superficie que añade el programa (base, trasera, paredes, tapa).',
             fontsize=7.4, color=MUTED, va='top', wrap=True)
    c = (V.max(0) + V.min(0)) / 2
    tall = np.ptp(V[:, 2]) > 0.5 * np.ptp(V[:, :2], 0).max()
    views = ([(0, 0, 'Frente'), (-35, 28, 'Perspectiva')] if tall else [(0, 90, 'Planta'), (-30, 35, 'Perspectiva')])
    for i, (az, el, tt) in enumerate(views):
        ax = fig.add_axes([0.06 + i * 0.46, 0.56, 0.42, 0.33])
        _view(ax, V - c, F, _rot(az, el), tt, fcol)
    # barra de colores
    ax = fig.add_axes([0.20, 0.525, 0.60, 0.012])
    g = np.linspace(-lim, lim, 256)
    ax.imshow(desviacion.colors(g, lim)[None] / 255.0, aspect='auto', extent=[-lim, lim, 0, 1])
    ax.set_yticks([])
    ax.tick_params(labelsize=7, colors=MUTED)
    ax.set_xlabel('mm  (− falta · + sobra)', fontsize=7, color=MUTED)
    for s in ax.spines.values():
        s.set_color(LINE)
    # histograma ponderado por área
    A = desviacion.vertex_areas(np.asarray(r.verts, float), np.asarray(r.tris))
    ok = np.isfinite(r.dev)
    d = np.clip(r.dev[ok], -2 * lim, 2 * lim)
    ax = fig.add_axes([0.08, 0.13, 0.42, 0.30])
    bins = np.linspace(-2 * lim, 2 * lim, 61)
    h, e = np.histogram(d, bins, weights=A[ok])
    ctr = (e[:-1] + e[1:]) / 2
    ax.bar(ctr, 100 * h / h.sum(), width=np.diff(e), color=desviacion.colors(ctr, lim) / 255.0, edgecolor=LINE,
           linewidth=0.3)
    ax.set_xlabel('desviación (mm)', fontsize=7.5, color=MUTED)
    ax.set_ylabel('% de la superficie escaneada', fontsize=7.5, color=MUTED)
    ax.tick_params(labelsize=7, colors=MUTED)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    rows = [('Superficie con escaneo', f'{st["covered"]:.0f} %'),
            ('Desviación media', f'{st["mean"]:.3f} mm'),
            ('Mediana', f'{st["p50"]:.3f} mm'),
            ('95 % de la superficie', f'a menos de {st["p95"]:.3f} mm'),
            ('Máxima', f'{st["max"]:.2f} mm'),
            ('Tendencia', f'{st["signed"]:+.3f} mm ' + ('(sobra)' if st['signed'] > 0 else '(falta)')),
            ('Dentro de ± 0,1 mm', f'{st["within_0.1"]:.0f} %'),
            ('Dentro de ± 0,25 mm', f'{st["within_0.25"]:.0f} %'),
            ('Dentro de ± 0,5 mm', f'{st["within_0.5"]:.0f} %')]
    _table(fig, 0.56, 0.44, 0.38, 'Resumen', rows)
    fig.text(0.56, 0.20, 'Incluye todo lo que hace el programa (resolución, relleno de huecos, alisado,\n'
             'simplificación) y también el ruido propio del escáner.', fontsize=6.6, color=MUTED, va='top')
    pdf.savefig(fig)


def _view(ax, V, F, R, title, fcol=None):
    """Proyección ortográfica con la matriz R (filas: eje x de pantalla, eje y de pantalla, hacia el observador),
    dibujando de atrás adelante y sombreando por la normal. fcol: color base de cada triángulo (0–1)."""
    P = V @ R.T
    t = P[F]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    vis = n[:, 2] > 0
    t, n = t[vis], n[vis]
    order = np.argsort(t[:, :, 2].mean(1))
    L = np.array([-0.45, 0.55, 0.70])
    L /= np.linalg.norm(L)
    sh = 0.30 + 0.70 * np.clip(n @ L, 0, 1)
    base = STONE if fcol is None else fcol[vis]
    if fcol is not None:
        sh = 0.55 + 0.45 * (sh - 0.30) / 0.70           # menos contraste: que mande el color
    col = np.c_[sh[:, None] * base, np.ones(len(sh))][order]
    ax.add_collection(PolyCollection(t[order][:, :, :2], facecolors=col, edgecolors='none', rasterized=True))
    lo, hi = t[:, :, :2].reshape(-1, 2).min(0), t[:, :, :2].reshape(-1, 2).max(0)
    pad = 0.04 * (hi - lo).max()
    ax.set_xlim(lo[0] - pad, hi[0] + pad)
    ax.set_ylim(lo[1] - pad, hi[1] + pad)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(LINE)
    ax.set_title(title, fontsize=8, color=MUTED, pad=3)


def _rot(az, el):
    a, e = np.radians(az), np.radians(el)
    # cámara mirando hacia el origen desde (az, el): x pantalla, y pantalla, hacia el observador
    fwd = np.array([np.cos(e) * np.sin(a), -np.cos(e) * np.cos(a), np.sin(e)])
    right = np.array([np.cos(a), np.sin(a), 0.0])
    up = np.cross(fwd, right)
    return np.array([right, up, fwd])


def _table(fig, x, y, w, title, rows, fs=7.6, lh=0.0145):
    fig.text(x, y, title.upper(), fontsize=8.5, color=ACC, weight='bold', va='top')
    y -= 0.022
    for k, v in rows:
        fig.text(x, y, k, fontsize=fs, color=MUTED, va='top')
        lines = textwrap.wrap(str(v), int(w * 0.58 * A4[0] * 72 / (fs * 0.55))) or ['']
        for ln in lines:
            fig.text(x + w * 0.42, y, ln, fontsize=fs, color=INK, va='top')
            y -= lh
    return y - 0.01


def _json_lines(prm):
    """Los ajustes en JSON válido, un parámetro por línea; las listas (recortes) en una línea partida a 110."""
    import json
    from dataclasses import asdict
    d = asdict(prm)
    out = ['{']
    for i, (k, v) in enumerate(d.items()):
        txt = f'  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}' + (',' if i < len(d) - 1 else '')
        out += textwrap.wrap(txt, 110, subsequent_indent='    ', break_long_words=True) or [txt]
    return out + ['}']


def make_pdf(path, r, prm, source=None, log_text='', label_fn=None):
    """Escribe la ficha. source: dict con 'path', 'points', 'units', 'removed', 'kind' (todo opcional)."""
    source = source or {}
    label_fn = label_fn or (lambda n: n)
    V, F = _light_mesh(r)
    sx, sy, sz = r.size
    with PdfPages(path) as pdf:
        fig = Figure(figsize=A4)
        name = os.path.basename(source.get('path', '')) or 'pieza'
        fig.text(0.06, 0.965, 'Ficha de la pieza', fontsize=17, color=INK, weight='bold', va='top')
        fig.text(0.06, 0.935, name + (f'   ·   {source["id"]}' if source.get('id') else ''), fontsize=11, color=ACC,
                 va='top')
        fig.text(0.94, 0.965, f'nube2stl {__version__}', fontsize=8, color=MUTED, ha='right', va='top')
        fig.text(0.94, 0.950, datetime.datetime.now().strftime('%d/%m/%Y %H:%M'), fontsize=8, color=MUTED,
                 ha='right', va='top')
        # vistas
        c = (V.max(0) + V.min(0)) / 2
        Vc = V - c
        views = [(0, 0, 'Frente'), (90, 0, 'Lado derecho'), (0, 90, 'Planta'), (-35, 28, 'Perspectiva')]
        for i, (az, el, tt) in enumerate(views):
            ax = fig.add_axes([0.06 + (i % 2) * 0.46, 0.685 - (i // 2) * 0.225, 0.42, 0.21])
            _view(ax, Vc, F, _rot(az, el), tt)
        scale = (f'1:{1 / r.scale:.3g}' if r.scale < 1 else f'{r.scale:.3g}:1') if r.scale else '—'
        piece = [('Medidas', f'{sx:.1f} × {sy:.1f} × {sz:.1f} mm' if getattr(prm, 'target', '') != 'real' else
                  relief.fmt_dims(sx, sy, sz)),
                 ('Escala', scale + (' (copia sin impresora)' if getattr(prm, 'target', '') == 'real' else
                                    f' (fijada 1:{prm.ratio:g})' if prm.ratio > 0 else ' (por el lado mayor)')),
                 ('Método', r.mode),
                 ('Volumen', relief.vol_text(r) if getattr(prm, 'target', '') == 'real' else
                  f'{r.volume / 1000:,.1f} cm³ · PLA ' + ('(hueca)' if r.hollow else 'macizo')
                  + f' ≈ {r.grams_pla:,.0f} g'),
                 ('Malla', f'{len(r.tris):,} triángulos · ' + relief.mesh_state(r)[1]),
                 ('Resolución', f'{r.res:.3g} mm en la nube → celda de {r.cell:.3f} mm en la pieza'),
                 ('Simplificación', f'error medio {r.err_mean:.3f} mm, p99 {r.err_p99:.3f} mm' if r.err_max
                  else 'sin simplificar')]
        if prm.wall > 0:
            piece.append(('Hueca', f'pared {prm.wall:g} mm' + (f', drenaje {prm.drain:g} mm' if prm.drain else '')))
        if prm.label.strip():
            piece.append(('Rótulo', prm.label.replace('|', ' / ')))
        import desviacion
        dst = desviacion.stats(r)
        if dst:
            piece.append(('Fidelidad', f'media {dst["mean"]:.3f} mm; 95 % a menos de {dst["p95"]:.3f} mm '
                                       f'({dst["covered"]:.0f} % con escaneo; ver pág. 2)'))
        y = _table(fig, 0.06, 0.435, 0.42, 'Pieza', piece)
        src = [('Fichero', source.get('path', '—')), ('Puntos', f'{source["points"]:,}' if 'points' in source else '—'),
               ('Tipo', source.get('kind', 'nube')), ('Unidades', source.get('units', prm.units)),
               ('Puntos sueltos quitados', f'{source.get("removed", 0):,}')]
        _table(fig, 0.06, y, 0.42, 'Nube de origen', src)
        d = relief.Params()
        rows = []
        for f_ in relief.Params.__dataclass_fields__:
            v, v0 = getattr(prm, f_), getattr(d, f_)
            mark = '• ' if v != v0 else ''
            if isinstance(v, list):                         # recortes, grupos conservados: cuántos
                v = len(v) if v else '—'
            else:
                v = {True: 'sí', False: 'no'}.get(v, v) if isinstance(v, bool) else (v if v != '' else '—')
            rows.append((mark + label_fn(f_), v))
        half = (len(rows) + 1) // 2
        fig.text(0.52, 0.435, 'PARÁMETROS', fontsize=8.5, color=ACC, weight='bold', va='top')
        fig.text(0.52, 0.420, '• = distinto del valor por defecto', fontsize=6.5, color=MUTED, va='top')
        for col, chunk in enumerate((rows[:half], rows[half:])):
            yy = 0.402
            for k, v in chunk:
                fig.text(0.52 + col * 0.215, yy, str(k)[:21], fontsize=6.3, color=MUTED, va='top')
                fig.text(0.52 + col * 0.215 + 0.13, yy, str(v)[:13], fontsize=6.3, color=INK, va='top')
                yy -= 0.0128
        pdf.savefig(fig)
        _dev_page(pdf, r, name)
        # registro y ajustes
        lines = []
        for ln in (log_text or '').splitlines():
            lines += textwrap.wrap(ln, 110) or ['']
        lines += ['', 'Ajustes (copiar a un .json para repetir la pieza):', ''] + _json_lines(prm)
        per = 88
        for p0 in range(0, len(lines), per):
            fig = Figure(figsize=A4)
            fig.text(0.06, 0.965, 'Registro de la generación' + (' (cont.)' if p0 else ''), fontsize=12, color=INK,
                     weight='bold', va='top')
            fig.text(0.06, 0.94, '\n'.join(lines[p0:p0 + per]), fontsize=6.6, family='monospace', color=INK,
                     va='top', linespacing=1.25)
            pdf.savefig(fig)
        info = pdf.infodict()
        info['Title'] = f'Ficha de {name}' + (f' ({source["id"]})' if source.get('id') else '')
        info['Creator'] = f'nube2stl {__version__}'
    return path
