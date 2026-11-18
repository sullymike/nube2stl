"""Comparar dos escaneos de la misma pieza (deterioro): dónde ha perdido o ganado material entre una fecha y otra.

1. Se alinea el escaneo nuevo (B) sobre el antiguo (A) como al unir nubes (ventana_unir: ya alineadas, por puntos o
   automática) y después se afina con un ICP robusto (pérdida de Tukey): las zonas que han cambiado no tiran de la
   alineación, que se apoya en lo que sigue igual.
2. Para cada punto de B, la distancia con signo a la superficie de A (al plano de sus vecinos de A, por la normal
   hacia fuera): < 0 se ha perdido material (azul), > 0 hay material de más, depósitos o costras (rojo).
3. Resumen (qué parte de la superficie ha perdido más de un umbral, la mayor pérdida…) e informe PDF con la vista
   en planta coloreada y el histograma.

Varias fechas (serie): cada escaneo se compara con el primero (la referencia), con el mismo umbral, y además se
sigue el cambio en los mismos lugares (puntos del primero) para ver la evolución y, con fechas, el ritmo en mm/año.

Desde la consola: python3 comparar.py antes.ply despues.ply [otro.ply…] informe.pdf [--alinear auto] [--umbral 0.5]
[--fechas 2015,2019,2024]
"""
import datetime
import os
import sys

import numpy as np


def _normals(A, k=20, outward=None):
    """Normales de A hacia fuera: hacia el que mira la cara (outward = vector) o desde el centro (objetos)."""
    import open3d as o3d
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(A, np.float64)))
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(k))
    N = np.asarray(pc.normals)
    if outward is not None:
        s = np.sign(N @ np.asarray(outward, np.float64))
    else:
        s = np.sign(np.einsum('ij,ij->i', N, A - A.mean(0)))
    s[s == 0] = 1
    return N * s[:, None]


def outward_of(A, prm=None):
    """Hacia dónde está «fuera»: en una cara (relieve, lápida, grabado), hacia el que la mira (la normal de su
    plano); en un objeto entero (método objeto, o una nube que no es una lámina), None = desde el centro."""
    import relief
    if prm is not None and getattr(prm, 'mode', '') == 'objeto':
        return None
    q = np.asarray(A[:: max(1, len(A) // 50_000)], np.float64)
    sv = np.linalg.svd(q - q.mean(0), compute_uv=False)
    if prm is None and sv[2] > 0.25 * sv[0]:
        return None
    return relief._frame_axes(q, prm or relief.Params())[3]


def robust_refine(A, B, M0, log=lambda s: None):
    """ICP punto a plano con pérdida de Tukey: lo que ha cambiado (más de unos mm) no cuenta. -> M 4×4"""
    import open3d as o3d
    import unir
    reg = o3d.pipelines.registration
    v = unir._voxel(A, B) * 0.5
    pa, pb = unir._pc(A, v), unir._pc(B, v)
    M = np.asarray(M0, np.float64).reshape(4, 4)
    for f in (4.0, 2.0, 1.0):
        est = reg.TransformationEstimationPointToPlane(reg.TukeyLoss(k=f * v))
        r = reg.registration_icp(pb, pa, 3 * f * v, M, est, reg.ICPConvergenceCriteria(max_iteration=50))
        M = np.asarray(r.transformation)
    log(f'Afinado robusto (sin las zonas que han cambiado): {100 * r.fitness:.0f} % de los puntos con pareja, '
        f'separación {r.inlier_rmse:.2f} mm')
    M0 = np.asarray(M0, np.float64).reshape(4, 4)
    c = np.asarray(B, np.float64)[:: max(1, len(B) // 20000)]
    moved = np.linalg.norm((c @ M[:3, :3].T + M[:3, 3]) - (c @ M0[:3, :3].T + M0[:3, 3]), axis=1).max()
    if moved > 10 * v:
        log(f'AVISO: el afinado ha movido el escaneo hasta {moved:.1f} mm respecto a la alineación de partida. Si la '
            f'pieza es casi lisa (sin forma que agarre), puede haberse deslizado sobre sí misma: compruébalo en la '
            f'vista (los bordes deben coincidir)')
    return M


def signed_distances(A, B, outward=None, k=8, log=lambda s: None):
    """Distancia con signo de cada punto de B a la superficie de A (mm). NaN donde A no tiene puntos cerca (fuera
    de la zona escaneada en las dos fechas)."""
    from scipy.spatial import cKDTree
    import unir
    A = np.asarray(A, np.float64)
    B = np.asarray(B, np.float64)
    sp = unir.spacing(A)
    na = A[:: max(1, len(A) // 3_000_000)]
    N = _normals(na, outward=outward)
    tree = cKDTree(na)
    d, idx = tree.query(B, k=k, workers=-1)
    w = 1.0 / (d + 0.3 * sp) ** 2
    nn = (N[idx] * w[..., None]).sum(1)
    nn /= np.linalg.norm(nn, axis=1, keepdims=True) + 1e-12
    pc = (na[idx] * w[..., None]).sum(1) / w.sum(1)[:, None]
    out = np.einsum('ij,ij->i', B - pc, nn)
    far = d[:, 0] > np.maximum(8 * sp, 3 * np.abs(out) + 2 * sp)
    out[far] = np.nan
    log(f'Distancias de {len(B):,} puntos del escaneo nuevo al antiguo; {100 * (~far).mean():.0f} % con escaneo '
        f'antiguo debajo')
    return out.astype(np.float32)


def summary(d, thr):
    """Resumen de la comparación (mm). thr: umbral para contar pérdida o ganancia."""
    v = d[np.isfinite(d)]
    if not len(v):
        return {}
    return {'n': int(len(v)), 'thr': thr, 'loss_pct': float((v < -thr).mean() * 100),
            'gain_pct': float((v > thr).mean() * 100), 'max_loss': float(-v.min()), 'max_gain': float(v.max()),
            'p1': float(np.percentile(v, 1)), 'p99': float(np.percentile(v, 99)),
            'noise': float(1.4826 * np.median(np.abs(v - np.median(v)))), 'median': float(np.median(v))}


def summary_text(s):
    if not s:
        return 'Los dos escaneos no se solapan: no hay nada que comparar.'
    return (f'Pérdida de más de {s["thr"]:g} mm: {s["loss_pct"]:.1f} % de la superficie (máx. {s["max_loss"]:.1f} '
            f'mm) · material de más (costras, depósitos): {s["gain_pct"]:.1f} % (máx. {s["max_gain"]:.1f} mm) · '
            f'ruido de la comparación ≈ {s["noise"]:.2f} mm (lo que está por debajo no es un cambio real)')


def default_threshold(d):
    """Umbral por defecto: tres veces el ruido de la comparación (lo que no ha cambiado), mínimo 0,2 mm."""
    v = d[np.isfinite(d)]
    if not len(v):
        return 0.5
    x = max(3 * 1.4826 * np.median(np.abs(v - np.median(v))), 0.2)
    for c in (0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 50):
        if x <= c:
            return float(c)
    return float(np.ceil(x))


def colors(d, lim):
    import desviacion
    return desviacion.colors(d, lim)


def report_pdf(path, B, d, s, names=('antes', 'después'), outward=None, notes=''):
    """Informe en PDF: planta del escaneo nuevo coloreada por la diferencia, histograma y resumen."""
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    from matplotlib.figure import Figure
    from version import __version__
    B = np.asarray(B, np.float64)
    if outward is None:                           # planta: mirando desde el lado de la cara (o desde arriba)
        _, _, vt = np.linalg.svd(B[:: max(1, len(B) // 50_000)] - B.mean(0), full_matrices=False)
        outward = vt[2] if vt[2][2] >= 0 else -vt[2]
    from superficie import _basis
    n, e1, e2 = _basis(outward)
    X, Y, Z = (B - B.mean(0)) @ e1, (B - B.mean(0)) @ e2, (B - B.mean(0)) @ n
    lim = max(abs(s.get('p1', -1)), abs(s.get('p99', 1)), 2 * s.get('thr', 0.5)) if s else 1.0
    cmap = LinearSegmentedColormap.from_list('dif', ['#285ac8', '#eeeee8', '#d23228'])
    k = max(1, len(B) // 400_000)
    o = np.argsort(Z[::k])                        # lo de delante, encima
    xs, ys, ds = X[::k][o], Y[::k][o], d[::k][o]
    with PdfPages(path) as pdf:
        fig = Figure(figsize=(8.27, 11.69))
        fig.text(0.06, 0.965, 'Comparación de dos escaneos', fontsize=17, weight='bold', va='top', color='#1f3a5f')
        fig.text(0.06, 0.935, f'{names[0]}  →  {names[1]}', fontsize=10.5, va='top', color='#b5651d')
        fig.text(0.94, 0.965, f'nube2stl {__version__}', fontsize=8, ha='right', va='top', color='#777')
        fig.text(0.94, 0.950, datetime.datetime.now().strftime('%d/%m/%Y %H:%M'), fontsize=8, ha='right', va='top',
                 color='#777')
        ax = fig.add_axes([0.06, 0.40, 0.80, 0.50])
        ok = np.isfinite(ds)
        ax.scatter(xs[~ok], ys[~ok], s=0.2, c='#bbbbbb', linewidths=0, rasterized=True)
        sc = ax.scatter(xs[ok], ys[ok], s=0.2, c=ds[ok], cmap=cmap, norm=TwoSlopeNorm(0, -lim, lim), linewidths=0,
                        rasterized=True)
        ax.set_aspect('equal')
        ax.set_xlabel('mm')
        ax.set_title('Escaneo nuevo coloreado por la diferencia (azul: pérdida · rojo: material de más · gris: '
                     'sin escaneo antiguo)', fontsize=8)
        cax = fig.add_axes([0.88, 0.45, 0.02, 0.40])
        cb = fig.colorbar(sc, cax=cax)
        cb.set_label('mm', fontsize=8)
        ah = fig.add_axes([0.10, 0.20, 0.80, 0.14])
        v = d[np.isfinite(d)]
        if len(v):
            ah.hist(np.clip(v, -lim, lim), bins=120, color='#555', alpha=0.85)
            for t in (-s['thr'], s['thr']):
                ah.axvline(t, color='#b5651d', lw=0.8, ls='--')
        ah.set_xlabel('diferencia (mm)', fontsize=8)
        ah.set_yticks([])
        ah.tick_params(labelsize=7)
        import textwrap
        fig.text(0.06, 0.135, textwrap.fill(summary_text(s), 105), fontsize=8.5, va='top')
        if notes:
            fig.text(0.06, 0.075, textwrap.fill(notes, 120), fontsize=7.5, va='top', color='#555')
        pdf.savefig(fig)


def compare(A, B, M=None, outward=None, thr=None, log=lambda s: None):
    """Alinea (afinado robusto desde M), mide y resume. -> (B alineado, distancias, resumen, M)"""
    import unir
    M = robust_refine(A, B, M if M is not None else np.eye(4), log)
    if outward is None:
        outward = outward_of(A)
    Bm = unir.transform(M, B)[0]
    d = signed_distances(A, Bm, outward, log=log)
    thr = thr or default_threshold(d)
    s = summary(d, thr)
    log(summary_text(s))
    unir.clear_cache()                        # los árboles de vecinos guardados ocupan memoria
    return Bm, d, s, M


REF_PTS = 60_000          # lugares de la referencia en los que se sigue la evolución


def series(A, Bs, Ms=None, outward=None, thr=None, dates=None, log=lambda s: None):
    """Serie de escaneos de la misma pieza: A, el primero (la referencia), y Bs, los siguientes por orden. Cada uno se
    alinea con la referencia (no con el anterior, para no acumular errores) y se mide como en compare. El umbral es
    común (el mayor de los automáticos), para que las cifras de cada fecha se puedan comparar.
    También se sigue el cambio en los mismos lugares: REF_PTS puntos de A; en cada fecha, cuánto se ha movido la
    superficie allí (< 0 pérdida). Con fechas (años, con decimales), el ritmo de cada lugar en mm/año (recta por
    mínimos cuadrados, desde la referencia = 0).
    -> dict(steps=[dict(Bm, d, s, M)], ref=puntos de A, change=(lugares × fechas, NaN sin datos; la referencia
       incluida, 0), rate=(lugares,) o None, thr, dates)"""
    Ms = list(Ms) if Ms is not None else [None] * len(Bs)
    steps = []
    for i, (B, M) in enumerate(zip(Bs, Ms)):
        log(f'--- Fecha {i + 2} de {len(Bs) + 1}')
        Bm, d, s, M = compare(A, B, M, outward, thr, log)
        steps.append(dict(Bm=Bm, d=d, s=s, M=M))
    if thr is None:
        thr = max(default_threshold(st['d']) for st in steps)
    for st in steps:
        st['s'] = summary(st['d'], thr)
    A = np.asarray(A, np.float64)
    ref = A[np.random.default_rng(0).choice(len(A), min(REF_PTS, len(A)), replace=False)]
    change = np.zeros((len(ref), len(Bs) + 1), np.float32)
    for i, st in enumerate(steps):          # A frente a la superficie de esa fecha: + si A queda fuera (se perdió)
        change[:, i + 1] = -signed_distances(st['Bm'], ref, outward)
    rate = None
    if dates is not None and len(dates) == len(Bs) + 1:
        t = np.asarray(dates, np.float64) - float(dates[0])
        if np.ptp(t) > 0:
            ok = np.isfinite(change)
            tt = np.where(ok, t, 0.0)
            n = ok.sum(1)
            st_ = (tt * np.nan_to_num(change)).sum(1)
            t2 = (tt * tt).sum(1)
            rate = np.where((n >= 2) & (t2 > 0), st_ / np.maximum(t2, 1e-12), np.nan).astype(np.float32)
    log('Serie: ' + ' · '.join(f'{i + 2}: pérdida {st["s"].get("loss_pct", 0):.1f} %' for i, st in enumerate(steps)))
    return dict(steps=steps, ref=ref.astype(np.float32), change=change, rate=rate, thr=thr,
                dates=list(dates) if dates is not None else None)


def date_of(path):
    """Fecha de un escaneo en años con decimales: del nombre (2019, 2019-05, 2019-05-21, 20190521) o, si no, de
    la fecha del fichero. -> (años, texto)"""
    import re
    name = os.path.basename(path)
    m = re.search(r'(?<!\d)((?:19|20)\d\d)[-_.]?(0[1-9]|1[0-2])?[-_.]?(0[1-9]|[12]\d|3[01])?(?!\d)', name)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
        txt = f'{y}' + (f'-{mo:02d}' if mo else '') + (f'-{d:02d}' if d else '')
        return y + ((mo or 1) - 1) / 12 + ((d or 1) - 1) / 365.25, txt
    t = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    return t.year + (t.timetuple().tm_yday - 1) / 365.25, t.strftime('%Y-%m-%d')


def parse_date(txt):
    """«2019», «2019-05», «2019-05-21», «21/05/2019» o «2019,4» -> años con decimales, o None."""
    import re
    txt = txt.strip().replace(',', '.')
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', txt)
    if m:
        return int(m.group(3)) + (int(m.group(2)) - 1) / 12 + (int(m.group(1)) - 1) / 365.25
    m = re.fullmatch(r'(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?', txt)
    if m:
        return int(m.group(1)) + (int(m.group(2) or 1) - 1) / 12 + (int(m.group(3) or 1) - 1) / 365.25
    try:
        return float(txt)
    except ValueError:
        return None


def series_text(res, names):
    """Tabla de la serie en texto (una línea por fecha)."""
    rows = []
    for i, st in enumerate(res['steps']):
        s = st['s']
        if not s:
            rows.append(f'{names[i + 1]}: no se solapa con la referencia')
            continue
        rows.append(f'{names[i + 1]}: pérdida {s["loss_pct"]:.1f} % (máx. {s["max_loss"]:.1f} mm) · material de más '
                    f'{s["gain_pct"]:.1f} % · mediana {s["median"]:+.2f} mm · ruido {s["noise"]:.2f} mm')
    if res.get('rate') is not None:
        r = res['rate'][np.isfinite(res['rate'])]
        if len(r):
            rows.append(f'Ritmo en los lugares que pierden: el 5 % que más, {-np.percentile(r, 5):.2f} mm/año o más; '
                        f'mediana de toda la superficie {np.median(r):+.3f} mm/año')
    return '\n'.join(rows)


def report_series_pdf(path, res, names, labels=None, outward=None, notes=''):
    """Informe de una serie: tabla y gráfica de la evolución, un mapa por fecha (misma escala) y el ritmo o el
    cambio total sobre la referencia."""
    import textwrap
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    from matplotlib.figure import Figure
    from superficie import _basis
    from version import __version__
    steps, ref = res['steps'], np.asarray(res['ref'], np.float64)
    labels = labels or names
    if outward is None:
        _, _, vt = np.linalg.svd(ref - ref.mean(0), full_matrices=False)
        outward = vt[2] if vt[2][2] >= 0 else -vt[2]
    n, e1, e2 = _basis(outward)
    c0 = ref.mean(0)
    cmap = LinearSegmentedColormap.from_list('dif', ['#285ac8', '#eeeee8', '#d23228'])
    lim = max([max(abs(st['s'].get('p1', -1)), abs(st['s'].get('p99', 1))) for st in steps if st['s']] + [2 * res['thr']])
    x = res['dates'] if res.get('dates') else list(range(len(names)))

    def head(fig, title):
        fig.text(0.06, 0.965, title, fontsize=17, weight='bold', va='top', color='#1f3a5f')
        fig.text(0.06, 0.935, '  →  '.join(labels), fontsize=9.5, va='top', color='#b5651d')
        fig.text(0.94, 0.965, f'nube2stl {__version__}', fontsize=8, ha='right', va='top', color='#777')
        fig.text(0.94, 0.950, datetime.datetime.now().strftime('%d/%m/%Y %H:%M'), fontsize=8, ha='right', va='top',
                 color='#777')

    def plan(ax, P, val, lim_, title):
        P = np.asarray(P, np.float64) - c0
        X, Y, Z = P @ e1, P @ e2, P @ n
        k = max(1, len(P) // 250_000)
        o = np.argsort(Z[::k])
        xs, ys, vs = X[::k][o], Y[::k][o], val[::k][o]
        ok = np.isfinite(vs)
        ax.scatter(xs[~ok], ys[~ok], s=0.2, c='#bbbbbb', linewidths=0, rasterized=True)
        sc = ax.scatter(xs[ok], ys[ok], s=0.2, c=vs[ok], cmap=cmap, norm=TwoSlopeNorm(0, -lim_, lim_), linewidths=0,
                        rasterized=True)
        ax.set_aspect('equal')
        ax.set_title(title, fontsize=8.5)
        ax.tick_params(labelsize=6)
        return sc

    with PdfPages(path) as pdf:
        fig = Figure(figsize=(8.27, 11.69))
        head(fig, f'Serie de {len(names)} escaneos')
        ax = fig.add_axes([0.10, 0.62, 0.80, 0.26])
        loss = [0.0] + [st['s'].get('loss_pct', np.nan) for st in steps]
        gain = [0.0] + [st['s'].get('gain_pct', np.nan) for st in steps]
        ax.plot(x, loss, 'o-', color='#285ac8', label=f'pérdida de más de {res["thr"]:g} mm (% de la superficie)')
        ax.plot(x, gain, 's-', color='#d23228', label='material de más (%)')
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7, rotation=20)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=7.5, loc='upper left')
        ax.set_title('Evolución respecto a la primera fecha', fontsize=9)
        ch = res['change']
        a2 = fig.add_axes([0.10, 0.30, 0.80, 0.24])
        q = [np.nanpercentile(ch[:, j], [5, 50, 95]) if np.isfinite(ch[:, j]).any() else [np.nan] * 3
             for j in range(ch.shape[1])]
        q = np.array(q)
        a2.fill_between(x, q[:, 0], q[:, 2], color='#999', alpha=0.3, label='90 % de los lugares')
        a2.plot(x, q[:, 1], 'k.-', label='mediana')
        worst = np.nanargmin(np.nan_to_num(ch[:, -1], nan=np.inf)) if np.isfinite(ch[:, -1]).any() else None
        if worst is not None:
            a2.plot(x, ch[worst], '-', color='#285ac8', lw=1.2, label='el lugar que más ha perdido')
        a2.axhline(0, color='#444', lw=0.6)
        a2.set_xticks(x)
        a2.set_xticklabels(labels, fontsize=7, rotation=20)
        a2.tick_params(labelsize=7)
        a2.set_ylabel('mm (< 0: pérdida)', fontsize=7.5)
        a2.legend(fontsize=7, loc='lower left')
        a2.set_title(f'Cambio de la superficie en los mismos {len(ref):,} lugares'.replace(',', '.'), fontsize=9)
        lines = [w for row in series_text(res, labels).split('\n') for w in textwrap.wrap(row, 118)]
        fig.text(0.06, 0.24, '\n'.join(lines), fontsize=7.8, va='top', linespacing=1.5)
        if notes:
            fig.text(0.06, 0.06, textwrap.fill(notes, 120), fontsize=7.5, va='top', color='#555')
        pdf.savefig(fig)
        per = 4
        for p0 in range(0, len(steps), per):            # mapas, cuatro por página, con la misma escala
            fig = Figure(figsize=(8.27, 11.69))
            head(fig, 'Cada fecha frente a la primera')
            sc = None
            for j, st in enumerate(steps[p0:p0 + per]):
                ax = fig.add_axes([0.06 + 0.45 * (j % 2), 0.50 - 0.42 * (j // 2), 0.40, 0.38])
                sc = plan(ax, st['Bm'], st['d'], lim, f'{labels[p0 + j + 1]} (azul: pérdida · rojo: de más)')
            if sc is not None:
                cb = fig.colorbar(sc, cax=fig.add_axes([0.93, 0.30, 0.015, 0.40]))
                cb.set_label('mm', fontsize=8)
            pdf.savefig(fig)
        fig = Figure(figsize=(8.27, 11.69))
        rate = res.get('rate')
        if rate is not None:
            head(fig, 'Ritmo de cambio')
            rl = max(float(np.nanpercentile(np.abs(rate), 98)) if np.isfinite(rate).any() else 0.1, 1e-3)
            ax = fig.add_axes([0.06, 0.35, 0.80, 0.55])
            sc = plan(ax, ref, rate, rl, 'mm/año en cada lugar (recta desde la primera fecha; azul: pierde)')
        else:
            head(fig, 'Cambio total')
            ax = fig.add_axes([0.06, 0.35, 0.80, 0.55])
            sc = plan(ax, ref, ch[:, -1], lim, f'{labels[-1]} frente a {labels[0]}, en los lugares de la primera')
        cb = fig.colorbar(sc, cax=fig.add_axes([0.88, 0.40, 0.02, 0.40]))
        cb.set_label('mm/año' if rate is not None else 'mm', fontsize=8)
        pdf.savefig(fig)


def main(argv):
    import argparse
    import entrada
    import relief
    import unir
    ap = argparse.ArgumentParser(prog='comparar.py', description='Comparar dos o más escaneos de la misma pieza')
    ap.add_argument('escaneos', nargs='+', help='antes después [más fechas, por orden…] informe.pdf')
    ap.add_argument('--alinear', choices=['no', 'auto'], default='no',
                    help='no: ya comparten coordenadas (por defecto); auto: alineación automática (necesita forma '
                         'marcada; compruébala)')
    ap.add_argument('--umbral', type=float, help='mm a partir de los que se cuenta un cambio (por defecto, 3 veces el '
                                                 'ruido)')
    ap.add_argument('--unidades', default='auto')
    ap.add_argument('--fechas', help='fechas de los escaneos, por orden y separadas por comas (2015,2019-05,2024); '
                                     'por defecto, del nombre del fichero o de su fecha')
    ap.add_argument('--ply', help='guardar también el escaneo nuevo coloreado (PLY; con varias fechas, el último)')
    a = ap.parse_args(argv)
    if len(a.escaneos) < 3:
        ap.error('hacen falta al menos dos escaneos y el informe PDF')
    scans, informe = a.escaneos[:-1], a.escaneos[-1]
    prm = relief.Params(units=a.unidades)
    pts = [entrada.load_points(f, prm, print)[0] for f in scans]
    A, Bs = pts[0], pts[1:]
    Ms = []
    for B in Bs:
        M = None
        if a.alinear == 'auto':
            M, flag = unir.auto_align(A, B, print)
            if flag:
                print('AVISO: alineación dudosa (' + flag + '): compruébala, o alinea por puntos en la ventana')
        Ms.append(M)
    names = [os.path.basename(f) for f in scans]
    if len(Bs) == 1:
        Bm, d, s, M = compare(A, Bs[0], Ms[0], thr=a.umbral, log=print)
        report_pdf(informe, Bm, d, s, tuple(names))
    else:
        if a.fechas:
            dates = [parse_date(t) for t in a.fechas.split(',')]
            if len(dates) != len(scans) or None in dates:
                ap.error('--fechas: una fecha por escaneo, por orden (2015,2019-05,2024)')
            labels = [t.strip() for t in a.fechas.split(',')]
        else:
            dl = [date_of(f) for f in scans]
            dates, labels = [d[0] for d in dl], [d[1] for d in dl]
        res = series(A, Bs, Ms, outward_of(A), a.umbral, dates, print)
        print(series_text(res, labels))
        report_series_pdf(informe, res, names, labels)
        Bm, d, s = res['steps'][-1]['Bm'], res['steps'][-1]['d'], res['steps'][-1]['s']
    print(f'Informe: {informe}')
    if a.ply:
        import ejemplos
        ejemplos.write_ply(a.ply, Bm, colors(d, max(abs(s['p1']), abs(s['p99']))) if s else None)
        print(f'Nube coloreada: {a.ply}')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main(sys.argv[1:]))
