"""Mapa de desviación: cuánto se separa la pieza final del escaneo, vértice a vértice (en mm de la pieza).

Cada modo guarda en el resultado la transformación exacta nube -> pieza (Result.xform). Con ella se lleva la nube
(sin alisar) a la pieza y, para cada vértice de la malla final, se ajusta un plano a los puntos del escaneo más
cercanos y se mide la distancia con signo:
    > 0   la pieza sobresale del escaneo (sobra material: relleno, alisado de un hueco…)
    < 0   la pieza queda por dentro (falta material: aristas redondeadas, detalle perdido al simplificar…)
    NaN   no hay escaneo cerca: superficie añadida por el programa (base, trasera, paredes, tapa)
    +inf  superficie que añaden los acabados (hueco, agujeros de drenaje, letras): no cuenta en los porcentajes
Incluye todo lo que hace el programa (resolución, relleno, alisado, simplificación) y también el ruido del escáner.
"""
import numpy as np

MAX_CLOUD = 2_000_000


def vertex_normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    N = np.zeros_like(V, dtype=np.float64)
    for k in range(3):
        np.add.at(N, F[:, k], n)
    return N / (np.linalg.norm(N, axis=1, keepdims=True) + 1e-12)


def vertex_areas(V, F):
    a = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    A = np.zeros(len(V))
    for k in range(3):
        np.add.at(A, F[:, k], a / 3)
    return A


def compute(r, p, log=lambda s: None, k=10):
    """Desviación de cada vértice de r respecto a la nube p (mm, sin transformar). Devuelve (Nv,) float32."""
    from scipy.spatial import cKDTree
    if r.xform is None or p is None or not len(p):
        return None
    T = np.asarray(r.xform, np.float64)
    sel = p if len(p) <= MAX_CLOUD else p[np.random.default_rng(0).choice(len(p), MAX_CLOUD, replace=False)]
    Q = sel.astype(np.float64) @ T[:3, :3].T + T[:3, 3]
    if r.zclip is not None:                             # lo que el modo deja fuera (bajo el fondo, bajo el corte)
        Q = Q[(Q[:, 2] >= r.zclip[0]) & (Q[:, 2] <= r.zclip[1])]
    V = np.asarray(r.verts, np.float64)
    N = vertex_normals(V, np.asarray(r.tris))
    # separación típica de los puntos en la pieza -> radio de búsqueda
    sp = np.median(cKDTree(Q[:200_000:max(1, len(Q) // 200_000)] if len(Q) > 200_000 else Q)
                   .query(Q[:20_000:max(1, len(Q) // 20_000)], 2)[0][:, 1])
    R = max(6 * sp, 3 * r.cell, 1.0)
    tree = cKDTree(Q)
    dev = np.full(len(V), np.nan, np.float32)
    for i in range(0, len(V), 300_000):
        v, nv = V[i:i + 300_000], N[i:i + 300_000]
        d, j = tree.query(v, k, distance_upper_bound=R, workers=-1)
        ok = np.isfinite(d).sum(1) >= 4                    # al menos 4 puntos del escaneo cerca
        if not ok.any():
            continue
        jj = j[ok]
        good = np.isfinite(d[ok])
        jj = np.where(good, jj, jj[:, :1])                  # rellenar los que faltan con el más cercano
        P = Q[jj]                                           # (m, k, 3)
        w = good.astype(np.float64)
        c = (P * w[..., None]).sum(1) / w.sum(1, keepdims=True)
        X = (P - c[:, None]) * np.sqrt(w)[..., None]
        C = np.einsum('mki,mkj->mij', X, X)
        _, vec = np.linalg.eigh(C)
        n = vec[:, :, 0]                                    # normal del plano local del escaneo
        cos = (n * nv[ok]).sum(1)
        n *= np.sign(cos + 1e-12)[:, None]
        d_ = ((v[ok] - c) * n).sum(1)
        # si el escaneo cercano mira hacia otro lado (> 60°) es otra superficie: p. ej. la cara vista desde una
        # pared vertical que añade el programa. Ahí no se mide.
        d_[np.abs(cos) < 0.5] = np.nan
        dev[np.flatnonzero(ok) + i] = d_
    st = stats(r, dev)
    if st:
        log(f'Desviación respecto al escaneo: media {st["mean"]:.3f} mm, 95 % de la superficie a menos de '
            f'{st["p95"]:.3f} mm (máx. {st["max"]:.2f} mm); superficie con escaneo: {st["covered"]:.0f} %')
    return dev


def transfer(r0, r1, tol=1e-3):
    """Pasa la desviación de r0 a r1 (r0 tras los acabados): los vértices que no han cambiado la conservan y los
    nuevos (hueco, drenaje, rótulo) se marcan con +inf (sin escaneo y fuera de los porcentajes)."""
    from scipy.spatial import cKDTree
    d, j = cKDTree(np.asarray(r0.verts)).query(np.asarray(r1.verts), workers=-1)
    out = r0.dev[j].astype(np.float32)
    out[d > tol] = np.inf
    return out


def stats(r, dev=None):
    """Resumen ponderado por área: media, mediana, p95 y máximo de |desviación|, % de superficie con escaneo y
    % dentro de ±0,1 / ±0,25 / ±0,5 mm."""
    dev = r.dev if dev is None else dev
    if dev is None:
        return None
    A = vertex_areas(np.asarray(r.verts, np.float64), np.asarray(r.tris))
    ok = np.isfinite(dev)
    if not ok.any():
        return None
    total = A[~np.isinf(dev)].sum()
    a, d = A[ok], np.abs(dev[ok].astype(np.float64))
    o = np.argsort(d)
    cw = np.cumsum(a[o]) / a.sum()

    def pct(q):
        return float(d[o][min(np.searchsorted(cw, q), len(o) - 1)])
    out = dict(mean=float((a * d).sum() / a.sum()), p50=pct(0.5), p95=pct(0.95), max=float(d.max()),
               covered=float(100 * a.sum() / total), signed=float((a * dev[ok]).sum() / a.sum()))
    for t in (0.1, 0.25, 0.5):
        out[f'within_{t:g}'] = float(100 * a[d <= t].sum() / a.sum())
    return out


def colors(dev, lim):
    """Colores (Nv,3) uint8: azul = falta material, blanco = coincide, rojo = sobra; gris = sin escaneo."""
    dev = np.asarray(dev, np.float64)
    t = np.clip(np.where(np.isfinite(dev), dev, 0.0) / max(lim, 1e-6), -1, 1)
    c = np.empty((len(dev), 3))
    neg, pos = t < 0, t >= 0
    blue, red, white = np.array([40, 90, 200]), np.array([210, 50, 40]), np.array([238, 238, 232])
    c[neg] = white + (blue - white) * (-t[neg])[:, None]
    c[pos] = white + (red - white) * t[pos][:, None]
    c[~np.isfinite(dev)] = [150, 150, 150]
    return c.round().astype(np.uint8)


def auto_limit(r):
    """Escala de colores por defecto: el p95 redondeado (mínimo 0,05 mm)."""
    st = stats(r)
    if not st:
        return 0.5
    x = max(st['p95'], 0.05)
    for v in (0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10):
        if x <= v:
            return v
    return float(np.ceil(x))
