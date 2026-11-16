"""Comparar dos escaneos de la misma pieza (deterioro): dónde ha perdido o ganado material entre una fecha y otra.

1. Se alinea el escaneo nuevo (B) sobre el antiguo (A) como al unir nubes (ventana_unir: ya alineadas, por puntos o
   automática) y después se afina con un ICP robusto (pérdida de Tukey): las zonas que han cambiado no tiran de la
   alineación, que se apoya en lo que sigue igual.
2. Para cada punto de B, la distancia con signo a la superficie de A (al plano de sus vecinos de A, por la normal
   hacia fuera): < 0 se ha perdido material (azul), > 0 hay material de más, depósitos o costras (rojo).
3. Resumen (qué parte de la superficie ha perdido más de un umbral, la mayor pérdida…) e informe PDF con la vista
   en planta coloreada y el histograma.

Desde la consola: python3 comparar.py antes.ply despues.ply informe.pdf [--alinear auto] [--umbral 0.5]
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


def main(argv):
    import argparse
    import entrada
    import relief
    import unir
    ap = argparse.ArgumentParser(prog='comparar.py', description='Comparar dos escaneos de la misma pieza')
    ap.add_argument('antes')
    ap.add_argument('despues')
    ap.add_argument('informe', help='informe PDF')
    ap.add_argument('--alinear', choices=['no', 'auto'], default='no',
                    help='no: ya comparten coordenadas (por defecto); auto: alineación automática (necesita forma '
                         'marcada; compruébala)')
    ap.add_argument('--umbral', type=float, help='mm a partir de los que se cuenta un cambio (por defecto, 3 veces el '
                                                 'ruido)')
    ap.add_argument('--unidades', default='auto')
    ap.add_argument('--ply', help='guardar también el escaneo nuevo coloreado (PLY)')
    a = ap.parse_args(argv)
    prm = relief.Params(units=a.unidades)
    A, *_ = entrada.load_points(a.antes, prm, print)
    B, *_ = entrada.load_points(a.despues, prm, print)
    M = None
    if a.alinear == 'auto':
        M, flag = unir.auto_align(A, B, print)
        if flag:
            print('AVISO: alineación dudosa (' + flag + '): compruébala, o alinea por puntos en la ventana')
    Bm, d, s, M = compare(A, B, M, thr=a.umbral, log=print)
    report_pdf(a.informe, Bm, d, s, (os.path.basename(a.antes), os.path.basename(a.despues)))
    print(f'Informe: {a.informe}')
    if a.ply:
        import ejemplos
        ejemplos.write_ply(a.ply, Bm, colors(d, max(abs(s['p1']), abs(s['p99']))) if s else None)
        print(f'Nube coloreada: {a.ply}')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main(sys.argv[1:]))
