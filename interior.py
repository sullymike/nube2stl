"""Interiores: salas, cuevas, cisternas, criptas o tumbas escaneadas desde dentro.

Un escaneo desde dentro es la piel de un hueco. Hay dos piezas útiles (Params.interior, con el método objeto):
- 'corte' (maqueta en corte, como una casa de muñecas): se quita el techo por encima de una altura (Params.ceiling)
  y la sala queda como un hueco abierto por arriba dentro de un bloque. Las normales se orientan hacia dentro de la
  sala (que es «fuera» del bloque) y se añaden las caras del bloque: los lados y el fondo, y la tapa a la altura
  del corte alrededor de la sala.
- 'negativo': el volumen del hueco como un sólido (el aire de la cueva): las normales, hacia fuera del hueco.
En los dos casos el resultado son puntos con normales que el método objeto convierte en malla con Poisson.
"""
import numpy as np
from scipy import ndimage


def _normals_from_inside(P, log):
    """Normales orientadas hacia dentro del hueco: hacia el punto de vista más cercano entre unos cuantos puntos
    interiores (el centro de la nube y los centros de sus trozos), como si se escanease desde ahí."""
    import open3d as o3d
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P.astype(np.float64)))
    ext = float(np.ptp(P, 0).max())
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=ext / 60, max_nn=30))
    N = np.asarray(pc.normals).copy()
    # puntos de vista: el centro y una rejilla de centros en planta a media altura (salas alargadas o en L)
    lo, hi = P.min(0), P.max(0)
    zc = (lo[2] + hi[2]) / 2
    g = np.stack(np.meshgrid(np.linspace(lo[0], hi[0], 5)[1:-1], np.linspace(lo[1], hi[1], 5)[1:-1]), -1).reshape(-1, 2)
    views = np.r_[[P.mean(0)], np.c_[g, np.full(len(g), zc)]]
    from scipy.spatial import cKDTree
    _, iv = cKDTree(views).query(P, workers=-1)
    to_view = views[iv] - P
    s = np.sign(np.einsum('ij,ij->i', N, to_view))
    s[s == 0] = 1
    log(f'Normales orientadas hacia dentro del hueco ({len(views)} puntos de vista interiores)')
    return N * s[:, None]


def _slice_inside(P, z, res, band):
    """Planta del hueco a la altura z: celdas encerradas por las paredes (puntos a ±band de z). -> (máscara, origen)"""
    sel = np.abs(P[:, 2] - z) < band
    lo = P[:, :2].min(0) - 4 * res
    n = np.ceil((P[:, :2].max(0) + 4 * res - lo) / res).astype(int) + 1
    img = np.zeros((n[1], n[0]), bool)
    ij = ((P[sel, :2] - lo) / res).astype(int)
    img[ij[:, 1], ij[:, 0]] = True
    walls = ndimage.binary_closing(ndimage.binary_dilation(img, iterations=1), iterations=3)
    inside = ndimage.binary_fill_holes(walls) & ~walls
    return inside, walls, lo


def cutaway(P, prm, log=lambda s: None):
    """Maqueta en corte. -> (puntos, normales) en mm de la nube."""
    P = np.asarray(P, np.float64)
    z0, z1 = np.percentile(P[:, 2], [0.2, 99.8])
    H = z1 - z0
    ceil = getattr(prm, 'ceiling', 0.0)
    zc = z0 + (ceil if ceil > 0 else 0.75 * H)
    keep = P[:, 2] <= zc
    P = P[keep]
    N = _normals_from_inside(P, log)
    ext = float(np.ptp(P[:, :2], 0).max())
    res = max(ext / 300, 1.0)
    margin = max(0.05 * ext, 4 * res)
    inside, walls, lo = _slice_inside(P, zc - 2 * res, res, 2 * res)
    if inside.sum() < 10:
        log('AVISO: a la altura del corte no se ve una sala cerrada por paredes: baja «Altura del corte» o '
            'recorta lo que sobre')
    # bloque: lados y fondo a «margin» de la sala, tapa a la altura del corte fuera del hueco
    bx0, by0 = P[:, 0].min() - margin, P[:, 1].min() - margin
    bx1, by1 = P[:, 0].max() + margin, P[:, 1].max() + margin
    bz0 = P[:, 2].min() - margin
    step = res
    xs, ys = np.arange(bx0, bx1 + step, step), np.arange(by0, by1 + step, step)
    zs = np.arange(bz0, zc + step / 2, step)
    pts, nrm = [], []
    X, Y = np.meshgrid(xs, ys)
    # tapa: fuera del hueco (y de las paredes escaneadas)
    ij = ((np.c_[X.ravel(), Y.ravel()] - lo) / res).astype(int)
    ok = (ij[:, 0] >= 0) & (ij[:, 1] >= 0) & (ij[:, 0] < inside.shape[1]) & (ij[:, 1] < inside.shape[0])
    hole = np.zeros(len(ij), bool)
    hole[ok] = (inside | walls)[ij[ok, 1], ij[ok, 0]]
    top = np.c_[X.ravel(), Y.ravel(), np.full(X.size, zc)][~hole]
    pts.append(top)
    nrm.append(np.tile([0, 0, 1.0], (len(top), 1)))
    bot = np.c_[X.ravel(), Y.ravel(), np.full(X.size, bz0)]
    pts.append(bot)
    nrm.append(np.tile([0, 0, -1.0], (len(bot), 1)))
    for axis, v, sgn in ((0, bx0, -1), (0, bx1, 1), (1, by0, -1), (1, by1, 1)):
        a, z = np.meshgrid(ys if axis == 0 else xs, zs)
        side = np.c_[np.full(a.size, v), a.ravel(), z.ravel()] if axis == 0 else np.c_[a.ravel(), np.full(a.size, v), z.ravel()]
        n = np.zeros(3)
        n[axis] = sgn
        pts.append(side)
        nrm.append(np.tile(n, (len(side), 1)))
    syn = np.concatenate(pts)
    log(f'Maqueta en corte: techo quitado por encima de {zc - z0:.0f} mm del suelo (de {H:.0f} de alto); bloque con '
        f'{margin:.0f} mm de margen alrededor ({len(syn):,} puntos del bloque)')
    return np.r_[P, syn].astype(np.float32), np.r_[N, np.concatenate(nrm)].astype(np.float32)


def negative(P, prm, log=lambda s: None):
    """El volumen del hueco como sólido. -> (puntos, normales)"""
    P = np.asarray(P, np.float64)
    N = -_normals_from_inside(P, log)
    log('Negativo del interior: el volumen del hueco como un sólido (el «aire» de la sala o la cueva)')
    return P.astype(np.float32), N.astype(np.float32)


def prepare(P, prm, log=lambda s: None):
    kind = getattr(prm, 'interior', 'no')
    if kind == 'corte':
        return cutaway(P, prm, log)
    if kind == 'negativo':
        return negative(P, prm, log)
    return P, None
