"""Esquina: dos caras con relieve que se juntan en una arista (la esquina de un friso, un sillar de esquina con
inscripción, un ara con dos caras decoradas).

El modo bloque está pensado para una cara principal y laterales cortos: el lateral solo pasa por Poisson. Aquí cada
cara tiene su propio mapa de alturas, con todo el tratamiento del modo alturas (relleno de huecos, picos,
suavizados), y la pieza es el sólido que queda detrás de las dos:

    dentro  ⇔  z_A ≤ h_A(x, y_A)  y  z_B ≤ h_B(x, y_B)  y  delante de las dos traseras  y  entre los extremos

con (x, y, z) las coordenadas de cada cara (x a lo largo de la arista, y alejándose de ella en el plano de la cara,
z hacia fuera). Ese sólido se convierte en malla con tetraedros en marcha (marching tetrahedra) sobre una rejilla
3D con el tamaño de celda de los mapas: sale cerrada y sin agujeros por construcción.

Las dos caras se buscan solas (two_faces): el plano con más puntos y, entre los que quedan, el mayor plano que
forma con él un ángulo de al menos 30°. Sirve también para avisar al abrir la nube (superficie.guess_kind).
"""
import numpy as np
from scipy import ndimage

import relief


# ------------------------------------------------------------------ las dos caras
def _ransac_plane(q, thr, rng, trials=400, ref=None, min_angle=30.0):
    """Plano con más puntos cerca (MSAC). Con ref (una normal), solo planos que formen con ella al menos min_angle
    grados. -> (normal, punto, puntuación) o None"""
    best = None
    cmax = np.cos(np.radians(min_angle))
    for _ in range(trials):
        a, b, c = q[rng.choice(len(q), 3, replace=False)]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln < 1e-9:
            continue
        n = n / ln
        if ref is not None and abs(np.dot(n, ref)) > cmax:
            continue
        r2 = ((q - a) @ n / thr) ** 2
        k = float(np.clip(1 - r2, 0, None).sum())
        if best is None or k > best[2]:
            best = (n, a, k)
    return best


def _refine(Q, n, c):
    """Plano de los puntos de una cara sin dejarse llevar por el relieve: robusto (RANSAC + Tukey) y por la
    mediana (el fondo de la cara)."""
    m = Q.mean(0)
    n = relief._robust_normal(Q - m, n)
    h = (Q - m) @ n
    return n, m + n * float(np.median(h))


def two_faces(p, nmax=40_000, min_frac=0.15):
    """¿Dos caras grandes que se juntan en una arista? -> dict(nA, cA, nB, cB, fracA, fracB, angle) con las
    normales hacia fuera (al lado contrario de la piedra) y angle el ángulo de la esquina por dentro de la piedra,
    o None. A es la cara con más puntos."""
    q = np.asarray(p[:: max(1, len(p) // nmax)], np.float64)
    if len(q) < 500:
        return None
    m = q.mean(0)
    q = q - m
    ax0 = np.linalg.svd(q[:: max(1, len(q) // 5000)], full_matrices=False)[2][0]
    L = float(np.ptp(q @ ax0))
    thr = 0.012 * L
    rng = np.random.default_rng(0)
    r1 = _ransac_plane(q, thr, rng)
    if r1 is None:
        return None
    nA, pA, _ = r1
    dA = np.abs((q - pA) @ nA)
    rest = q[dA > 3 * thr]
    if len(rest) < min_frac * len(q):
        return None
    r2 = _ransac_plane(rest, thr, rng, ref=nA)
    if r2 is None:
        return None
    nB, pB, _ = r2
    dB = np.abs((q - pB) @ nB)
    onA, onB = (dA < 2.5 * thr) & (dA <= dB), (dB < 2.5 * thr) & (dB < dA)
    fA, fB = float(onA.mean()), float(onB.mean())
    if fB < min_frac or fA + fB < 0.6:
        return None
    nA, cA = _refine(q[onA], nA, pA)
    nB, cB = _refine(q[onB], nB, pB)
    # caras planas de verdad: el fondo pegado a su plano (una roca curva también se deja cubrir por dos planos
    # anchos, pero sus puntos se reparten por toda la franja)
    flat = [float(np.median(np.abs((q[on] - c) @ n))) / thr for on, n, c in ((onA, nA, cA), (onB, nB, cB))]
    if max(flat) > 0.2:
        return None
    # fuera: la otra cara está del lado de la piedra
    if np.median((q[onB] - cA) @ nA) > 0:
        nA = -nA
    if np.median((q[onA] - cB) @ nB) > 0:
        nB = -nB
    # que las dos caras lleguen de verdad a la arista común (y no sean, p. ej., dos planos sueltos de una roca)
    e = np.cross(nA, nB)
    e /= np.linalg.norm(e)
    o = _edge_point(nA, cA, nB, cB, np.zeros(3))
    yA, yB = _away(nA, e, q[onA] - o), _away(nB, e, q[onB] - o)
    gapA = float(np.percentile((q[onA] - o) @ yA, 2))
    gapB = float(np.percentile((q[onB] - o) @ yB, 2))
    if max(gapA, gapB) > 0.15 * L:
        return None
    ang = float(np.degrees(np.arccos(np.clip(np.dot(yA, yB), -1, 1))))
    return dict(nA=nA, cA=cA + m, nB=nB, cB=cB + m, fracA=fA, fracB=fB, angle=ang, L=L, flat=max(flat))


def _edge_point(nA, cA, nB, cB, near):
    """Punto de la arista (corte de los dos planos) más cercano a «near»."""
    e = np.cross(nA, nB)
    Mx = np.array([nA, nB, e])
    return np.linalg.solve(Mx, [np.dot(nA, cA), np.dot(nB, cB), np.dot(e, near)])


def _away(n, e, rel):
    """Dirección en el plano de la cara (normal n) perpendicular a la arista e, hacia donde están sus puntos."""
    y = np.cross(n, e)
    y /= np.linalg.norm(y)
    return y if np.median(rel @ y) > 0 else -y


# ------------------------------------------------------------------ tetraedros en marcha
# cubo: esquina k = (bit0: x, bit1: y, bit2: z); seis tetraedros alrededor de la diagonal 0–7 (la misma en todos
# los cubos, así las caras compartidas se parten igual y la malla queda cerrada)
_TETS = np.array([[0, 1, 3, 7], [0, 3, 2, 7], [0, 2, 6, 7], [0, 6, 4, 7], [0, 4, 5, 7], [0, 5, 1, 7]])
_OFF = np.array([[k & 1, (k >> 1) & 1, (k >> 2) & 1] for k in range(8)])


def _cases():
    """Para cada combinación de vértices dentro (4 bits), los triángulos como aristas del tetraedro."""
    out = {}
    for code in range(1, 15):
        ins = [k for k in range(4) if code >> k & 1]
        outs = [k for k in range(4) if not code >> k & 1]
        if len(ins) == 1 or len(outs) == 1:
            a, others = (ins[0], outs) if len(ins) == 1 else (outs[0], ins)
            out[code] = [[(a, others[0]), (a, others[1]), (a, others[2])]]
        else:
            a, b = ins
            c, d = outs
            out[code] = [[(a, c), (a, d), (b, d)], [(a, c), (b, d), (b, c)]]
    return out


_CASES = _cases()


def march(field_slab, shape, origin, step, chunk=24, log=lambda s: None):
    """Superficie f = 0 de un campo (f > 0 dentro) en una rejilla regular, por tandas a lo largo de x.
    field_slab(i0, i1) -> f en las capas x = i0..i1-1, con forma (i1 - i0, ny, nz). -> (vértices, triángulos)"""
    nx, ny, nz = shape
    NG = np.int64(nx) * ny * nz
    keys_all, pos_all, tris_all = [], [], []
    nv = 0
    for i0 in range(0, nx - 1, chunk):
        i1 = min(i0 + chunk + 1, nx)
        f = field_slab(i0, i1).astype(np.float64)
        f[f == 0] = -1e-12
        ins = f > 0
        k = i1 - i0
        # cubos con esquinas dentro y fuera
        anyin = np.zeros((k - 1, ny - 1, nz - 1), bool)
        allin = np.ones((k - 1, ny - 1, nz - 1), bool)
        for dx, dy, dz in _OFF:
            c = ins[dx:k - 1 + dx, dy:ny - 1 + dy, dz:nz - 1 + dz]
            anyin |= c
            allin &= c
        ci, cj, ck = np.nonzero(anyin & ~allin)
        if not len(ci):
            continue
        # índice global y valor de cada esquina de los cubos activos
        gi = np.stack([((ci + i0 + dx) * ny + cj + dy) * nz + ck + dz for dx, dy, dz in _OFF], 1).astype(np.int64)
        fv = np.stack([f[ci + dx, cj + dy, ck + dz] for dx, dy, dz in _OFF], 1)
        P = np.stack([np.c_[ci + i0 + dx, cj + dy, ck + dz] for dx, dy, dz in _OFF], 1).astype(np.float64)
        for tet in _TETS:
            tg, tf, tp = gi[:, tet], fv[:, tet], P[:, tet]
            code = ((tf > 0) * (1 << np.arange(4))).sum(1)
            for c, tris in _CASES.items():
                sel = np.flatnonzero(code == c)
                if not len(sel):
                    continue
                g, fvv, pp = tg[sel], tf[sel], tp[sel]
                inside = (fvv > 0)[..., None]
                o_dir = (np.where(~inside, pp, 0).sum(1) / np.maximum((~inside).sum(1), 1) -
                         np.where(inside, pp, 0).sum(1) / np.maximum(inside.sum(1), 1))
                for tri in tris:
                    vk, vp = [], []
                    for a, b in tri:
                        ga, gb = g[:, a], g[:, b]
                        fa, fb = fvv[:, a], fvv[:, b]
                        t = (fa / (fa - fb))[:, None]
                        vp.append(pp[:, a] + t * (pp[:, b] - pp[:, a]))
                        vk.append(np.minimum(ga, gb) * NG + np.maximum(ga, gb))
                    nrm = np.cross(vp[1] - vp[0], vp[2] - vp[0])
                    flip = np.einsum('ij,ij->i', nrm, o_dir) < 0
                    vk[1], vk[2] = np.where(flip, vk[2], vk[1]), np.where(flip, vk[1], vk[2])
                    vp[1], vp[2] = (np.where(flip[:, None], vp[2], vp[1]), np.where(flip[:, None], vp[1], vp[2]))
                    m = len(sel)
                    keys_all.append(np.concatenate(vk))
                    pos_all.append(np.concatenate(vp))
                    tris_all.append(nv + np.arange(3 * m).reshape(3, m).T)
                    nv += 3 * m
    if not keys_all:
        raise ValueError('La esquina no da ninguna superficie: revisa la nube')
    keys = np.concatenate(keys_all)
    pos = np.concatenate(pos_all)
    tris = np.concatenate(tris_all)
    uk, first, inv = np.unique(keys, return_index=True, return_inverse=True)
    V = pos[first] * step + origin
    T = inv[tris]
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    return V, T


# ------------------------------------------------------------------ reconstrucción
def reconstruct_corner(p, prm, rgb=None, log=lambda s: None):
    p = np.asarray(p, np.float64)
    tf = two_faces(p)
    if tf is None:
        raise ValueError('No se encuentran dos caras grandes que se junten en una arista. «Esquina» es para dos '
                         'caras con relieve en ángulo (une antes los dos escaneos con Archivo › Unir otra nube…); '
                         'si es una cara con laterales cortos, quita «Esquina».')
    nA, cA, nB, cB = tf['nA'], tf['cA'], tf['nB'], tf['cB']
    pts = getattr(prm, 'plane_pts', None) or []
    if getattr(prm, 'plane', 'nube') == 'puntos' and len(pts) >= 3:      # la cara principal la marca el usuario
        n3 = relief._plane_from_points(pts)
        if n3 is not None and abs(np.dot(n3, nB)) > abs(np.dot(n3, nA)):
            nA, cA, nB, cB = nB, cB, nA, cA
    if prm.flip:                                                         # «darle la vuelta»: la otra cara arriba
        nA, cA, nB, cB = nB, cB, nA, cA
    e = np.cross(nA, nB)
    e /= np.linalg.norm(e)
    o = _edge_point(nA, cA, nB, cB, p.mean(0))
    # cada punto, a la cara de su lado del plano bisector de la esquina (primero, a la del plano más cercano)
    rel = p - o
    sideA = np.abs(rel @ nA) <= np.abs(rel @ nB)
    yA = _away(nA, e, rel[sideA])
    yB = _away(nB, e, rel[~sideA])
    sideA = rel @ (yA - yB) >= 0
    if prm.mirror:
        e = -e
    theta = float(np.degrees(np.arccos(np.clip(np.dot(yA, yB), -1, 1))))
    log(f'Esquina: dos caras a {theta:.1f}° (por dentro de la piedra); principal con el {100 * sideA.mean():.0f} % '
        f'de los puntos, la otra con el {100 * (~sideA).mean():.0f} %')
    if prm.z_scale != 1:
        log('AVISO: la exageración del relieve no se aplica en una esquina (deformaría la otra cara)')
    for name, why in (('negative', 'el molde'), ('frame', 'el marco')):
        if getattr(prm, name):
            log(f'AVISO: {why} no se aplica en una esquina')

    # ---- un mapa de alturas por cara (x a lo largo de la arista, el mismo en las dos)
    faces = {}
    for key, side, n, y in (('A', sideA, nA, yA), ('B', ~sideA, nB, yB)):
        Q = rel[side]
        faces[key] = dict(X=Q @ e, Y=Q @ y, H=Q @ n, n=n, y=y)
    plan = relief.print_plan(relief._face_stats_uv(faces['A']['X'], faces['A']['Y']), prm)
    res = plan['res']
    for key in 'AB':
        F = faces[key]
        g, mask, _, valid, u0, v0 = relief.grid_from(F['X'], F['Y'], F['H'], None, res, prm,
                                                     log=lambda s, k=key: log(f'  cara {"principal" if k == "A" else "lateral"}: {s}'))
        F.update(grid=g, mask=mask, u0=u0, v0=v0, depth=float(-np.percentile(F['H'], 0.5)))
    A, B = faces['A'], faces['B']
    # traseras: paralelas a cada cara, a la profundidad que alcanza la otra cara por detrás
    DA = float(np.percentile(-(rel[~sideA] @ nA), 99.5))
    DB = float(np.percentile(-(rel[sideA] @ nB), 99.5))
    # extremos a lo largo de la arista: los de los puntos (la máscara de cada mapa pierde alguna celda en el borde)
    ea = [np.percentile(F['X'], [0.05, 99.95]) for F in (A, B)]
    x0, x1 = float(min(ea[0][0], ea[1][0])), float(max(ea[0][1], ea[1][1]))
    la, lb = float(np.ptp(ea[0])), float(np.ptp(ea[1]))
    if abs(la - lb) > 0.1 * max(la, lb):
        log(f'AVISO: las caras miden {la:.0f} y {lb:.0f} a lo largo de la arista: la más corta se alarga con su borde')
    # caja en las coordenadas de la cara principal (x, y_A, z_A): los cuatro planos de la sección y un margen
    R = np.array([e, yA, nA])
    nB_ = R @ nB                                       # normal de la otra cara en esas coordenadas (x = 0)
    hA, hB = float(np.nanmax(A['grid'])), float(np.nanmax(B['grid']))
    corners = []
    for za, zb in ((hA, hB), (hA, -DB), (-DA, hB), (-DA, -DB)):
        # z_A = za  y  (y, z)·nB_ = zb
        Mx = np.array([[0, 1.0], [nB_[1], nB_[2]]])
        try:
            yy, zz = np.linalg.solve(Mx, [za, zb])
        except np.linalg.LinAlgError:
            continue
        corners.append((zz, yy))
    corners = np.array(corners)
    pad = 2 * res
    ylo, yhi = corners[:, 1].min() - pad, corners[:, 1].max() + pad
    zlo, zhi = corners[:, 0].min() - pad, corners[:, 0].max() + pad
    shape = (int(np.ceil((x1 - x0) / res)) + 3, int(np.ceil((yhi - ylo) / res)) + 1,
             int(np.ceil((zhi - zlo) / res)) + 1)
    ncell = shape[0] * shape[1] * shape[2]
    if ncell > 150_000_000:
        raise ValueError(f'La esquina necesita una rejilla 3D de {ncell / 1e6:.0f} millones de celdas: sube la '
                         f'resolución (mm/celda) o baja el tamaño')
    origin = np.array([x0 - res, ylo, zlo])
    log(f'Rejilla 3D de la esquina: {shape[0]} × {shape[1]} × {shape[2]} celdas de {res:g}; caras hasta '
        f'{DA:.0f} y {DB:.0f} de fondo')
    yB_ = R @ yB

    def lookup(F, X, Y):
        r = (Y - F['v0']) / res - 0.5
        c = (X - F['u0']) / res - 0.5
        return ndimage.map_coordinates(F['grid'], [r.ravel(), c.ravel()], order=1, mode='nearest').reshape(X.shape)

    xs = origin[0] + np.arange(shape[0]) * res
    ys = origin[1] + np.arange(shape[1]) * res
    zs = origin[2] + np.arange(shape[2]) * res

    def field(i0, i1):
        X = xs[i0:i1, None, None]
        Y = ys[None, :, None]
        Z = zs[None, None, :]
        X3, Y3, Z3 = np.broadcast_arrays(X, Y, Z)
        f = lookup(A, X3[:, :, :1][..., 0], Y3[:, :, :1][..., 0])[:, :, None] - Z      # bajo la cara principal
        yb = Y * yB_[1] + Z * yB_[2]
        zb = Y * nB_[1] + Z * nB_[2]
        yb3, zb3 = np.broadcast_arrays(yb, zb)
        yb3 = np.broadcast_to(yb3, X3.shape)
        zb3 = np.broadcast_to(zb3, X3.shape)
        f = np.minimum(f, lookup(B, X3, yb3) - zb3)                                      # y bajo la otra
        f = np.minimum(f, Z + DA)                                                         # delante de las traseras
        f = np.minimum(f, zb3 + DB)
        f = np.minimum(f, np.minimum(X - x0, x1 - X))                                     # entre los extremos
        return f

    V, T = march(field, shape, origin, res, log=log)
    # ---- a la pieza: la cara principal arriba, apoyada en su trasera
    lo = V.min(0)
    L = float(np.ptp(V, 0).max())
    s = relief.piece_scale(prm, L)
    verts = ((V - lo) * s).astype(np.float64)
    bad = relief._bad_edges(T, len(verts))
    vol = relief._volume(verts, T)
    log(f'{len(T):,} triángulos, aristas no-manifold: {bad}, volumen {vol / 1000:.1f} cm³')
    xform = relief.affine(s * R, -s * (R @ o) - s * lo)
    out = np.where(A['mask'], (A['grid'] - lo[2]) * s, np.nan)
    return relief.Result(verts, T.astype(np.int64), None, out, res * s, s, 0.0, bad, vol, mode='bloque',
                         res=res, xform=xform, corner=True)
