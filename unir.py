"""Unir varias nubes en un solo modelo (sin Qt: lo usan la ventana, el proceso de reconstrucción y la consola).

Cada nube añadida se guarda en los ajustes (Params.merge) como {'path', 'units', 'M'}: el fichero, sus unidades
('igual' = las de la nube principal) y la matriz 4×4 que la lleva a las coordenadas de la principal (mm). Al leer:
  1. se lee el fichero y se pasa a mm;
  2. si las dos nubes venían georreferenciadas, se deshace la diferencia entre sus orígenes (read_any resta a cada
     fichero el suyo), de modo que dos nubes que ya compartían coordenadas siguen encajando con M = identidad;
  3. se aplica M y, en la zona común, se quitan los puntos de la nube añadida que ya tienen uno de la principal muy
     cerca (si no, saldría una «doble piel» con la pequeña separación que siempre queda).

Tres maneras de obtener M (ventana_unir.py):
  - ya alineadas: identidad;
  - automática, si se solapan: características FPFH + RANSAC (Open3D) y después ICP;
  - por puntos: tres o más pares de puntos homólogos (Kabsch), y ICP si se solapan. Sin solape es la única manera.
Después se mide siempre la calidad: qué parte de la nube añadida cae sobre la principal y cuánto se separa allí.
"""
import os

import numpy as np

IDENTITY = [float(x) for x in np.eye(4).ravel()]


# ------------------------------------------------------------------ lectura
def resolve_path(path, main_path=None):
    """Ruta de una nube unida; si no está (carpeta movida u otro ordenador), la busca junto a la principal."""
    if os.path.exists(path):
        return path
    if main_path:
        alt = os.path.join(os.path.dirname(os.path.abspath(main_path)), os.path.basename(path))
        if os.path.exists(alt):
            return alt
    raise FileNotFoundError(f'No se encuentra la nube unida {path} (ni junto a la nube principal)')


def read_extra(path, units, main_info, log=lambda s: None):
    """Nube añadida en mm, en el sistema de la principal salvo la matriz M (con el mismo origen georreferenciado).
    main_info: info de entrada.load_points de la principal (unidades, factor, origen). -> (xyz, rgb, nrm, info)"""
    import entrada
    xyz, rgb, nrm, kind = entrada.read_any(path, log)
    origin = getattr(entrada.read_any, 'origin', None)
    u = main_info.get('units', 'mm') if units in ('igual', '', None) else units
    xyz, u, f = entrada.to_mm(xyz, u, log)
    o_main = np.asarray(main_info.get('origin_mm', (0, 0, 0)), np.float64)
    o_b = np.asarray(origin if origin is not None else (0, 0, 0), np.float64) * f
    shift = o_b - o_main
    if np.abs(shift).max() > 0:
        xyz = (xyz.astype(np.float64) + shift).astype(np.float32)
    return xyz, rgb, nrm, {'kind': kind, 'units': u, 'factor': f}


def transform(M, xyz, nrm=None):
    M = np.asarray(M, np.float64).reshape(4, 4)
    R, t = M[:3, :3], M[:3, 3]
    out = np.empty_like(xyz, dtype=np.float32)
    for i in range(0, len(xyz), 2_000_000):
        out[i:i + 2_000_000] = xyz[i:i + 2_000_000].astype(np.float64) @ R.T + t
    if nrm is not None:
        nrm = (nrm.astype(np.float64) @ R.T).astype(np.float32)
    return out, nrm


# ------------------------------------------------------------------ lo que se repite, calculado una vez
# Al alinear se pregunta muchas veces lo mismo de las mismas nubes (su separación, sus vecinos, la nube reducida
# para el ICP): construir un árbol de vecinos con millones de puntos usa un solo procesador y tarda segundos, así
# que se guarda. La clave identifica la nube por su memoria y una muestra de sus valores (si se cambia, cambia).
_CACHE = {}
_CACHE_MAX = 10


def clear_cache():
    """Soltar lo guardado (al cerrar la ventana de unir: los árboles de millones de puntos ocupan memoria)."""
    _CACHE.clear()


def _key(P, *extra):
    P0 = P
    P = np.asarray(P)
    if P.ndim != 2 or not len(P):
        return None
    smp = P[:: max(1, len(P) // 997)]
    return (id(P0), P.__array_interface__['data'][0], P.shape, str(P.dtype), float(smp.sum()),
            float(smp[:, 0] @ np.arange(len(smp))), *extra)


def _cached(P, what, make, *extra):
    k = _key(P, what, *extra)
    if k is None:
        return make()
    if k not in _CACHE:
        while len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[k] = make()
    return _CACHE[k]


def kdtree(P):
    """Árbol de vecinos de la nube (sin equilibrar: se construye en menos de la mitad de tiempo y busca igual)."""
    from scipy.spatial import cKDTree
    return _cached(P, 'tree', lambda: cKDTree(np.asarray(P, np.float64), balanced_tree=False,
                                              compact_nodes=False))


def spacing(P, n=20_000):
    """Separación típica entre puntos vecinos (mediana de la distancia al más cercano, en una muestra)."""
    if len(P) < 3:
        return 1.0

    def make():
        Q = np.asarray(P)
        q = Q[np.random.default_rng(0).choice(len(Q), min(n, len(Q)), replace=False)]
        d, _ = kdtree(P).query(q, k=2, workers=-1)
        return float(np.median(d[:, 1])) or 1.0
    return _cached(P, 'spacing', make, n)


def merge(xyz, rgb, nrm, info, entries, main_path=None, log=lambda s: None):
    """Une a la nube principal las nubes de entries (Params.merge). -> (xyz, rgb, nrm)."""
    from scipy.spatial import cKDTree
    for k, e in enumerate(entries):
        path = resolve_path(e['path'], main_path)
        log(f'Uniendo la nube {k + 2}: {os.path.basename(path)}')
        b, brgb, bnrm, binfo = read_extra(path, e.get('units', 'igual'), info, log)
        b, bnrm = transform(e.get('M', IDENTITY), b, bnrm)
        # zona común: fuera los puntos de la añadida con uno de la principal más cerca que 1,5 separaciones
        sp = spacing(xyz)
        d, _ = cKDTree(xyz).query(b, distance_upper_bound=1.5 * sp, workers=-1)
        keep = ~np.isfinite(d)
        if not keep.all():
            log(f'  zona común: se quitan {(~keep).sum():,} puntos repetidos de la nube añadida '
                f'({100 * (~keep).mean():.0f} %)')
        b = b[keep]
        brgb = brgb[keep] if brgb is not None else None
        bnrm = bnrm[keep] if bnrm is not None else None
        if rgb is not None or brgb is not None:
            gray = lambda n: np.full((n, 3), 160, np.uint8)                         # noqa: E731
            rgb = np.concatenate([rgb if rgb is not None else gray(len(xyz)), brgb if brgb is not None else gray(len(b))])
        nrm = np.concatenate([nrm, bnrm]) if nrm is not None and bnrm is not None else None
        xyz = np.concatenate([xyz, b])
        log(f'  {len(b):,} puntos añadidos; en total {len(xyz):,}')
    return xyz, rgb, nrm


# ------------------------------------------------------------------ alineación
def kabsch(src, dst):
    """Giro y traslación (sin escala) que mejor llevan los puntos src a dst. -> (M 4×4, error medio de los pares)"""
    src, dst = np.asarray(src, np.float64), np.asarray(dst, np.float64)
    cs, cd = src.mean(0), dst.mean(0)
    H = (src - cs).T @ (dst - cd)
    U, _, Vt = np.linalg.svd(H)
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = R, cd - R @ cs
    res = np.linalg.norm(src @ R.T + M[:3, 3] - dst, axis=1)
    return M, float(np.sqrt(np.mean(res ** 2)))




def collinear(pts, tol=0.03):
    """¿Los puntos están casi en línea? (el giro alrededor de esa línea no queda fijado)"""
    P = np.asarray(pts, np.float64)
    if len(P) < 3:
        return True
    sv = np.linalg.svd(P - P.mean(0), compute_uv=False)
    return bool(sv[1] < tol * sv[0])

def face_at_edge(P, e0, e1, flip=False):
    """Como _face_at_edge, guardada: darle la vuelta solo cambia el signo de la normal."""
    key = tuple(np.round(np.r_[np.asarray(e0, float), np.asarray(e1, float)], 9))
    f = dict(_cached(P, 'face', lambda: _face_at_edge(P, e0, e1), key))
    if flip:
        f['n'] = -f['n']
    return f


def _face_at_edge(P, e0, e1, flip=False):
    """Cara de una nube junto a una arista marcada con dos puntos (sus extremos). -> dict(e: dirección de la
    arista, d: dirección dentro del plano de la cara que se aleja de la arista, n: normal hacia fuera, p0, p1: los
    extremos llevados al plano, rms: separación al plano, skew: lo claro que es «fuera»)
    Fuera es el lado hacia el que sale el relieve (el fondo es lo más bajo), como en el reverso de dos caras."""
    sp = spacing(P)                                      # antes de convertir: guardada con la nube
    P = np.asarray(P, np.float64)
    e0, e1 = np.asarray(e0, np.float64), np.asarray(e1, np.float64)
    L = float(np.linalg.norm(e1 - e0))
    if L <= 0:
        raise ValueError('Los dos puntos de la arista son el mismo: marca sus dos extremos, separados.')
    e = (e1 - e0) / L
    # puntos a menos de una franja de la arista (entre sus extremos): el arranque de la cara
    rel = P - e0
    t = rel @ e
    perp = rel - np.outer(t, e)
    dist = np.linalg.norm(perp, axis=1)
    R = max(0.3 * L, 25 * sp)
    near = (t > -0.1 * L) & (t < 1.1 * L) & (dist < R)
    if near.sum() < 30:
        near = np.argsort(dist)[:2000]
    import relief
    Q = P[near]
    c = Q.mean(0)
    _, _, vt = np.linalg.svd(Q - c, full_matrices=False)
    n = relief._robust_normal(Q - c, vt[2])             # el fondo de la cara, sin dejarse torcer por el relieve
    n = n - e * np.dot(n, e)                             # la arista está en el plano
    n /= np.linalg.norm(n)
    hq = (Q - c) @ n
    c = c + n * np.median(hq)                            # el plano por el fondo (la mediana), no por la media
    rms = 1.4826 * float(np.median(np.abs(hq - np.median(hq))))
    d = np.cross(n, e)
    if np.dot(Q.mean(0) - e0, d) < 0:                    # d: de la arista hacia la cara
        d = -d
    # fuera: el lado al que sale el relieve, en la cara junto a la arista (hasta 4 franjas)
    Ps = P[:: max(1, len(P) // 200_000)]
    rs = Ps - e0
    ts = rs @ e
    ds = np.linalg.norm(rs - np.outer(ts, e), axis=1)
    h = (Ps[(ts > -0.1 * L) & (ts < 1.1 * L) & (ds < 4 * R)] - c) @ n
    lo, med, hi = np.percentile(h, [2, 50, 98]) if len(h) > 50 else (0, 0, 0)
    skew = float((hi - med) - (med - lo))
    if skew < 0:
        n = -n
    if flip:
        n = -n
    # los extremos, llevados al plano de la cara
    p0 = e0 - n * np.dot(e0 - c, n)
    p1 = e1 - n * np.dot(e1 - c, n)
    # y al borde de la nube: donde termina la cara (los clics caen a un mm arriba o abajo de la arista)
    hq = (Q - c) @ n
    on = np.abs(hq) < max(3 * rms, 2 * sp)
    tq = (Q - p0) @ e
    band = on & (tq > 0.1 * L) & (tq < 0.9 * L)
    if band.sum() > 200:
        # borde de la nube a lo largo de la arista: lo más cercano a ella en cada tramo, y una recta por esos puntos
        dq, tb = (Q[band] - p0) @ d, tq[band]
        nb = 24
        k = np.minimum(((tb - 0.1 * L) / (0.8 * L) * nb).astype(int), nb - 1)
        bt, bd = [], []
        for i in range(nb):
            m = k == i
            if m.sum() >= 5:
                j = np.flatnonzero(m)[np.argsort(dq[m])[: max(1, m.sum() // 500)]]
                bt.append(tb[j].mean())
                bd.append(dq[j].mean())
        if len(bt) >= 6:
            bt, bd = np.array(bt), np.array(bd)
            ok = np.ones(len(bt), bool)
            for _ in range(3):                           # sin los tramos con una rotura o un desconchón
                sl, a0 = np.polyfit(bt[ok], bd[ok], 1)
                r = bd - (a0 + sl * bt)
                ok = np.abs(r) <= max(3 * 1.4826 * np.median(np.abs(r[ok])), 2 * sp)
            if abs(a0) < 0.05 * L + 5 * sp and abs(sl) < 0.05:
                t0, t1 = 0.0, float(np.dot(p1 - p0, e))
                p0, p1 = p0 + (a0 + sl * t0) * d, p0 + t1 * e + (a0 + sl * t1) * d
                e = (p1 - p0) / np.linalg.norm(p1 - p0)
                d = np.cross(n, e) * (1 if np.dot(np.cross(n, e), d) > 0 else -1)
    return dict(e=e, d=d, n=n, p0=p0, p1=p1, L=L, rms=rms, skew=abs(skew), sp=sp)


def edge_align(A, B, a_pts, b_pts, angle=90.0, other_side=False, flip_b=False, known='a'):
    """Encajar B con A por una arista común y el ángulo entre las dos caras (sin solape: dos caras de un bloque
    escaneadas por separado). a_pts y b_pts: los dos extremos de la arista en cada nube, EN EL MISMO ORDEN en las
    dos (primero el de abajo): así la arista tiene la misma dirección en las dos y la añadida no puede quedar
    del revés (arriba por abajo). angle: ángulo de la esquina medido por dentro de la piedra (90 en un sillar;
    180 = las dos caras seguidas en el mismo plano).
    El lado de la piedra solo hace falta saberlo en una cara («known»: 'a' la principal, por su relieve y
    other_side; 'b' la añadida, por su relieve y flip_b); el de la otra sale de él, porque con la arista en la
    misma dirección las dos caras tienen que encajar sin espejarse. other_side: la otra solución (B al otro
    lado de A), si «fuera» se ha decidido mal en la cara conocida.
    -> (M 4×4, info) con info = dict(angle, len_a, len_b, rms_a, rms_b, sure_a, sure_b, known)"""
    fa = face_at_edge(A, *a_pts[:2], flip=other_side and known == 'a')
    fb = face_at_edge(B, *b_pts[:2], flip=flip_b)
    th = np.radians(angle)

    def target(fa):
        e, dA, nA = fa['e'], fa['d'], fa['n']
        d_t = np.cos(th) * dA - np.sin(th) * nA         # B sale de la arista hacia el lado de la piedra de A
        n_t = -np.sin(th) * dA - np.cos(th) * nA        # y su «fuera» mira lejos de la piedra
        return np.c_[e, d_t, n_t]
    T = target(fa)
    S = np.c_[fb['e'], fb['d'], fb['n']]
    if np.linalg.det(T) * np.linalg.det(S) < 0:         # las dos caras no encajan sin espejarse
        if known == 'a':                                # la piedra de la añadida está al otro lado
            S[:, 2] = -S[:, 2]
        else:                                           # la de la principal
            fa = dict(fa, n=-fa['n'])
            T = target(fa)
    Rm = T @ S.T
    U, _, Vt = np.linalg.svd(Rm)                          # limpiar redondeos: giro exacto
    Rm = U @ Vt
    ma, mb = (fa['p0'] + fa['p1']) / 2, (fb['p0'] + fb['p1']) / 2
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = Rm, ma - Rm @ mb
    return M, dict(angle=float(angle), len_a=fa['L'], len_b=fb['L'], rms_a=fa['rms'], rms_b=fb['rms'],
                   sure_a=fa['skew'] > 4 * max(fa['rms'], 1e-9), sure_b=fb['skew'] > 4 * max(fb['rms'], 1e-9),
                   known=known)


def other_face_strip(P, f, min_pts=300):
    """¿La nube P trae también una franja de la otra cara junto a la arista? f: su face_at_edge. Se buscan los
    puntos que bajan del plano de la cara hacia el lado de la piedra, más que su propio relieve, a lo largo de la
    arista; deben formar una franja plana que arranca en la arista. -> dict(angle: ángulo de la esquina por
    dentro de la piedra, medido; w: ancho de la franja; n: puntos; rms) o None"""
    P = np.asarray(P, np.float64)
    e, d, n, p0, L, sp = f['e'], f['d'], f['n'], f['p0'], f['L'], f['sp']
    rel = P - p0
    t = rel @ e
    x, z = rel @ d, rel @ n
    along = (t > 0.05 * L) & (t < 0.95 * L)
    face = along & (x > 0.15 * L) & (x < 0.6 * L)
    if face.sum() < 50:
        return None
    zmin = max(6 * f['rms'], 8 * sp, -float(np.percentile(z[face], 0.5)) + 3 * f['rms'])
    W = 0.6 * L
    cand = along & (z < -zmin) & (np.abs(x) < W) & (-z < W)
    if cand.sum() < min_pts:
        return None
    import relief
    Q = P[cand]
    c = Q.mean(0)
    _, _, vt = np.linalg.svd(Q - c, full_matrices=False)
    nS = relief._robust_normal(Q - c, vt[2])              # el fondo de la franja, sin su relieve
    r = (Q - c) @ nS
    r = r - np.median(r)
    s = max(1.4826 * float(np.median(np.abs(r))), sp)
    ok = np.abs(r) < 3 * s
    if ok.sum() < min_pts:
        return None
    bv = np.cross(e, nS)
    bx, bz = float(bv @ d), float(bv @ n)
    hb = np.hypot(bx, bz)
    if hb < 0.5:
        return None
    b = np.array([bx, bz]) / hb
    if b[1] > 0:                                           # de la arista hacia la piedra (z negativa)
        b = -b
    reach = np.c_[x[cand][ok], z[cand][ok]] @ b             # lo lejos de la arista a lo largo de la franja
    w = float(np.percentile(reach, 98) - min(0.0, float(np.percentile(reach, 2))))
    miss = abs(float((p0 - c) @ nS - np.median((Q - c) @ nS)))   # el plano de la franja pasa por la arista
    rms = float(np.sqrt(np.mean(r[ok] ** 2)))
    if w < max(10 * sp, 2 * zmin) or miss > 0.25 * w + 3 * zmin or rms > 0.15 * w:
        return None
    ang = float(np.degrees(np.arctan2(-b[1], b[0])))
    return dict(angle=ang, w=w, n=int(ok.sum()), rms=rms)


def _normals_at(tree, P, idx, k=12):
    """Normales (PCA de k vecinos) de los puntos P[idx]."""
    _, nb = tree.query(P[idx], k=k, workers=-1)
    Q = P[nb] - P[nb].mean(1, keepdims=True)
    C = np.einsum('nki,nkj->nij', Q, Q)
    return np.linalg.eigh(C)[1][:, :, 0]


def separation(A, B, near):
    """Separación de cada punto de B a la superficie de A, con signo (+ por fuera / encima de A), o NaN si no tiene
    un punto de A a menos de near (fuera de la zona común). Las normales de A (PCA de 12 vecinos) no tienen lado:
    se orientan como la cara si A es casi plana (un relieve) o hacia fuera del centro si es un objeto."""
    A = np.asarray(A, np.float64)
    B = np.asarray(B, np.float64)
    out = np.full(len(B), np.nan)
    if len(A) < 20 or not len(B):
        return out
    tree = kdtree(A)
    d, j = tree.query(B, workers=-1)
    ok = np.flatnonzero(d < near)
    if not len(ok):
        return out
    n = _normals_at(tree, A, j[ok])
    c = A[:: max(1, len(A) // 200_000)]
    w, V = np.linalg.eigh(np.cov((c - c.mean(0)).T))
    if w[0] < 0.1 * w[1]:                              # casi plana: todas hacia el lado de la normal de la cara
        ref = np.broadcast_to(V[:, 0], n.shape)
    else:                                              # un objeto: hacia fuera
        ref = A[j[ok]] - c.mean(0)
    n *= np.where(np.einsum('ij,ij->i', n, ref) < 0, -1.0, 1.0)[:, None]
    out[ok] = np.einsum('ij,ij->i', B[ok] - A[j[ok]], n)
    return out


def overlap_refine(A, B, M0, r0, slide=(), log=lambda s: None, iters=30, nmax=30_000):
    """Afinar M0 con la zona común. Primero se busca, en una rejilla, el desplazamiento a lo largo de «slide»
    (direcciones en las que una franja casi lisa apenas tira: a lo largo de la arista y por la propia franja), que
    un ICP recorrería muy despacio; después un ICP punto a plano amortiguado (lo que la zona común no fija se queda
    como estaba en vez de irse a la deriva). r0: hasta dónde se buscan parejas (mm). -> (M, dict(n: parejas, rms,
    moved: mm, turned: grados)) o (M0, None) si no hay zona común"""
    from scipy.spatial import cKDTree
    sp = max(spacing(A), spacing(B))
    treeA = kdtree(A)
    A = np.asarray(A, np.float64)
    B = np.asarray(B, np.float64)
    dist, _ = treeA.query(transform(M0, B)[0], distance_upper_bound=r0, workers=-1)
    src = np.flatnonzero(np.isfinite(dist))
    if len(src) < 300:
        return M0, None
    src = src[np.random.default_rng(0).permutation(len(src))[:nmax]]
    S = B[src]
    M = np.asarray(M0, np.float64).copy()
    # la parte de A junto a la zona común, con sus normales calculadas una vez
    dA, _ = cKDTree(transform(M, S)[0]).query(A, distance_upper_bound=r0 + 4 * sp, workers=-1)
    ia = np.flatnonzero(np.isfinite(dA))
    An, Nn = A[ia], _normals_at(treeA, A, ia)
    tree = cKDTree(An)
    c_noise = 3 * sp

    def cost(p):
        dd, j = tree.query(p, workers=-1)
        r = np.einsum('ij,ij->i', p - An[j], Nn[j])
        return float(np.mean(np.minimum(r * r, c_noise ** 2) + np.minimum(dd * dd, (4 * r0) ** 2) * 1e-4))

    def search(M, half, step):
        dirs = [np.asarray(v, np.float64) / np.linalg.norm(v) for v in slide][:2]
        if not dirs:
            return M
        k = np.arange(-half, half + step / 2, step)
        sub = S[:: max(1, len(S) // 12_000)]
        p0 = transform(M, sub)[0]
        shift = np.zeros(3)
        for _ in range(3):                                 # una dirección cada vez, por turnos (casi independientes)
            for dv in dirs:
                best = min(k, key=lambda a: cost(p0 + shift + a * dv))
                shift = shift + best * dv
        D = np.eye(4)
        D[:3, 3] = shift
        return D @ M

    def icp(M, n_it, c_thr):
        for _ in range(n_it):
            p = transform(M, S)[0]
            dd, j = tree.query(p, workers=-1)
            nq = Nn[j]
            r = np.einsum('ij,ij->i', p - An[j], nq)
            u = r / c_thr
            w = np.where((np.abs(u) < 1) & (dd < 2 * c_thr), (1 - u * u) ** 2, 0.0)     # Tukey
            if (w > 0).sum() < 100:
                break
            c = (p * w[:, None]).sum(0) / w.sum()
            J = np.c_[np.cross(p - c, nq), nq]
            H = J.T @ (J * w[:, None])
            x = -np.linalg.solve(H + 1e-3 * np.trace(H) / 6 * np.eye(6), J.T @ (w * r))
            om, tr = x[:3], x[3:]
            a = np.linalg.norm(om)
            K = np.array([[0, -om[2], om[1]], [om[2], 0, -om[0]], [-om[1], om[0], 0]])
            R = np.eye(3) + (np.sin(a) / a * K + (1 - np.cos(a)) / a ** 2 * K @ K if a > 1e-12 else K)
            D = np.eye(4)
            D[:3, :3], D[:3, 3] = R, c - R @ c + tr
            M = D @ M
            c_thr = max(3 * sp, 0.7 * c_thr)
            if a < 1e-7 and np.linalg.norm(tr) < 1e-3 * sp:
                break
        return M

    M = icp(M, 8, r0)                                    # giros y la separación entre caras
    half = min(r0, 4.0 + 10 * sp)
    M = search(M, half, half / 8)                        # deslizar: primero grueso, luego fino
    M = search(M, half / 8, half / 40)
    M = icp(M, iters, 6 * sp)
    M = search(M, half / 40, half / 200)
    p = transform(M, S)[0]
    dd, j = tree.query(p, workers=-1)
    r = np.abs(np.einsum('ij,ij->i', p - An[j], Nn[j]))
    good = dd < 3 * sp
    p0 = transform(M0, S)[0]
    moved = float(np.linalg.norm(p - p0, axis=1).mean())
    dR = M[:3, :3] @ np.asarray(M0)[:3, :3].T
    turned = float(np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1))))
    info = dict(n=int(good.sum()), rms=float(np.sqrt(np.mean(r[good] ** 2))) if good.any() else float('nan'),
                moved=moved, turned=turned)
    log(f'  afinado con la franja común: {info["n"]:,} parejas, separación {info["rms"]:.2f} mm; la añadida se '
        f'mueve {moved:.2f} mm y gira {turned:.2f}°')
    return M, info


def edge_align_strip(A, B, a_pts, b_pts, angle=90.0, other_side=False, use_strip=True, log=lambda s: None):
    """«Por la arista» aprovechando una franja de la otra cara, si alguna nube la trae (un escaneo que dobla un
    poco la esquina): el ángulo se mide en esa franja en vez de tomar «angle», y la zona común afina la unión,
    sobre todo a lo largo de la arista, que es lo que los clics no fijan bien. Sin franja, como edge_align.
    -> (M, info) con, además de lo de edge_align: strip (dict de other_face_strip y de dónde sale, o None),
    angle_typed, refine (dict de overlap_refine o None)"""
    st, flips, known = None, (other_side, False), 'a'
    if use_strip:
        # la franja está del lado de la piedra: se busca a los dos lados de cada cara (con franja, «fuera» por
        # el relieve sale al revés, porque la franja parece un relieve muy hondo)
        found = []
        for k, (P, pts, where) in enumerate(((A, a_pts, 'principal'), (B, b_pts, 'añadida'))):
            for fl in (False, True):
                s = other_face_strip(P, face_at_edge(P, *pts[:2], flip=fl))
                if s:
                    found.append(dict(s, where=where, k=k, flip=fl))
        st = max(found, key=lambda s: s['w'], default=None)
        if st:
            mine = [s for s in found if s['k'] == st['k']]
            if len(mine) == 1:                    # su lado de la piedra queda decidido (y el de la otra, con él)
                flips, known = ((st['flip'], False), 'a') if st['k'] == 0 else ((False, st['flip']), 'b')
    ang = st['angle'] if st else angle
    if st:
        log(f'  franja de la otra cara en la {st["where"]}: {st["w"]:.0f} mm de ancho; ángulo de la esquina '
            f'medido: {ang:.1f}°')
    M, info = edge_align(A, B, a_pts, b_pts, ang, flips[0], flips[1], known)
    fa = face_at_edge(A, *a_pts[:2])
    # el lado de la principal que ha quedado: la cara añadida está del lado de la piedra
    Bc = transform(M, np.asarray(B[:: max(1, len(B) // 20_000)], np.float64))[0]
    if np.median((Bc - fa['p0']) @ fa['n']) > 0:
        fa = dict(fa, n=-fa['n'])
    info.update(strip=st, angle_typed=float(angle), refine=None)
    if st:
        r0 = max(0.02 * max(info['len_a'], info['len_b']), 10 * fa['sp'])
        th = np.radians(ang)
        # la zona común está en la cara de la otra nube: con la franja en la principal, en la cara añadida; con la
        # franja en la añadida, en la principal. Se desliza dentro de ese plano
        b_dir = np.cos(th) * fa['d'] - np.sin(th) * fa['n'] if st['k'] == 0 else fa['d']
        M2, rf = overlap_refine(A, B, M, r0, slide=(fa['e'], b_dir), log=log)
        if rf is not None and rf['n'] >= 200:
            M, info['refine'] = M2, rf
    return M, info


def edge_verdict(info):
    """Frase para la unión por la arista. -> (texto, nivel)"""
    la, lb = info['len_a'], info['len_b']
    diff = abs(la - lb)
    st, rf = info.get('strip'), info.get('refine')
    if st:
        txt = (f'Unidas por la arista con la franja de la otra cara que trae la {st["where"]} ({st["w"]:.0f} mm de '
               f'ancho): ángulo medido {info["angle"]:.1f}°')
        if abs(info['angle'] - info.get('angle_typed', info['angle'])) > 0.05:
            txt += f' (el escrito, {info["angle_typed"]:g}°, no cuenta)'
        if rf:
            txt += (f'; afinada con la zona común, separación {rf["rms"]:.2f} mm (se ha movido {rf["moved"]:.1f} mm '
                    f'respecto a los clics)')
        txt += f'. La arista marcada mide {la:.0f} mm en la principal y {lb:.0f} en la añadida'
    else:
        txt = (f'Unidas por la arista con las caras a {info["angle"]:g}°. La arista marcada mide {la:.0f} mm en la '
               f'principal y {lb:.0f} en la añadida')
    level = 'ok' if (st and rf) else 'aviso'
    if diff > 0.15 * max(la, lb):
        txt += ' (mucha diferencia: ¿son los mismos extremos?)'
        level = 'mal'
    if st:
        return txt + '.', level
    txt += ('. Comprueba en la vista que la añadida sale hacia el lado de la piedra; si sale hacia fuera, marca '
            '«Al otro lado».')
    if not info.get('sure_a' if info.get('known', 'a') == 'a' else 'sure_b'):
        txt += ' (La cara principal es casi lisa: no se sabe bien de qué lado está la piedra.)'
    return txt, level


def _pc(P, voxel):
    """Nube reducida a un punto por vóxel, con normales (para open3d); guardada, porque el ICP la pide muchas veces
    (la automática prueba decenas de posiciones)."""
    def make():
        import open3d as o3d
        pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(P, np.float64)))
        pc = pc.voxel_down_sample(voxel)
        pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=2.5 * voxel, max_nn=30))
        return pc
    return _cached(P, 'pc', make, round(float(voxel), 9))


def _voxel(A, B):
    """Tamaño de vóxel para alinear: una centésima de la nube menor, sin bajar de la separación de los puntos."""
    ext = min(float(np.ptp(A, 0).max()), float(np.ptp(B, 0).max()))
    return max(ext / 100.0, spacing(A, 5000), spacing(B, 5000))


def _rot_about(axis, deg, c):
    """Giro de deg grados alrededor de la recta por c con dirección axis (4×4)."""
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    t = np.radians(deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    R = np.eye(3) + np.sin(t) * K + (1 - np.cos(t)) * K @ K
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = R, c - R @ c
    return M


def auto_align(A, B, log=lambda s: None, tries=3, cross=True):
    """Alineación automática de B sobre A (necesita solape): FPFH + RANSAC, y después ICP desde esa solución y
    desde la misma girada 90°, 180° y 270° alrededor de cada eje de la zona común. Las piezas simétricas (vasijas,
    columnas) engañan a RANSAC: encajan casi igual de bien giradas, y solo el detalle fino (la decoración) decide.
    Se queda la que menos se separa.
    Con cross, se repite al revés (A sobre B): si las dos respuestas no coinciden, la zona común no basta para decidir.
    En piezas casi planas o lisas (lápidas, rocas) la añadida puede resbalar sobre la principal y quedar «bien» con
    una separación igual de pequeña; ni esto lo detecta siempre, por eso la ventana pide comprobarla en la vista.
    -> (M 4×4, aviso: '' | 'simetrica' (otra posición girada encaja casi igual) | 'inestable' (las dos no coinciden))"""
    import open3d as o3d
    reg = o3d.pipelines.registration
    v = _voxel(A, B)
    log(f'Alineación automática: puntos característicos con vóxel de {v:.1f} mm…')
    pa, pb = _pc(A, v), _pc(B, v)
    kf = o3d.geometry.KDTreeSearchParamHybrid(radius=5 * v, max_nn=100)
    fa, fb = reg.compute_fpfh_feature(pa, kf), reg.compute_fpfh_feature(pb, kf)
    best = None
    for k in range(tries):
        r = reg.registration_ransac_based_on_feature_matching(
            pb, pa, fb, fa, True, 1.5 * v, reg.TransformationEstimationPointToPoint(False), 3,
            [reg.CorrespondenceCheckerBasedOnEdgeLength(0.9), reg.CorrespondenceCheckerBasedOnDistance(1.5 * v)],
            reg.RANSACConvergenceCriteria(200_000, 0.999))
        if best is None or r.fitness > best.fitness:
            best = r
    log(f'  primera aproximación: {100 * best.fitness:.0f} % de los puntos con pareja')
    M0 = np.asarray(best.transformation)
    Bs = B[:: max(1, len(B) // 40_000)]
    Bm, _ = transform(M0, Bs)
    c = Bm.mean(0)
    _, sv, vt = np.linalg.svd(Bm - c, full_matrices=False)
    starts = [M0]
    for i, ax in enumerate(vt):
        o = np.delete(sv, i)
        step = 15 if o.max() < 1.4 * o.min() else 90        # eje de simetría de revolución: pasos finos
        starts += [_rot_about(ax, d, c) @ M0 for d in range(step, 360, step)]
    log(f'  probando {len(starts)} posiciones (la pieza girada sobre sus ejes, por si es simétrica)…')
    cands = []
    for S in starts:
        M = refine(A, B, S, voxel=v, passes=(1.0,))
        q = quality(A, B, M, n=15_000)
        if q['overlap'] > 0.05:
            cands.append((q['rms'] / max(q['overlap'], 1e-3) ** 0.5, M))
    cands.sort(key=lambda t: t[0])
    finals = []
    for score, M in cands[:3]:                 # las tres mejores, afinadas del todo
        M = refine(A, B, M, voxel=v, passes=(0.35,))
        q = quality(A, B, M, n=30_000)
        finals.append((q['rms'] / max(q['overlap'], 1e-3) ** 0.5, M, q))
    finals.sort(key=lambda t: t[0])
    score, M, q = finals[0]
    ambiguous = False
    Bt, _ = transform(M, Bs)
    for s2, M2, q2 in finals[1:]:
        moved = np.median(np.linalg.norm(transform(M2, Bs)[0] - Bt, axis=1))
        if s2 < 1.25 * score and moved > 10 * q['sp']:
            ambiguous = True
    log(f'  afinado con la zona común (ICP): {100 * q["overlap"]:.0f} % de la nube añadida sobre la principal, '
        f'separación {q["rms"]:.2f} mm' + (' — AVISO: otra posición distinta encaja casi igual de bien (pieza '
                                          'simétrica)' if ambiguous else ''))
    flag = 'simetrica' if ambiguous else ''
    if cross:
        log('Comprobación: la misma alineación al revés (la principal sobre la añadida)…')
        M2, _ = auto_align(B, A, log=lambda s: None, tries=tries, cross=False)
        M2 = np.linalg.inv(M2)
        moved = float(np.median(np.linalg.norm(transform(M2, Bs)[0] - Bt, axis=1)))
        if moved > 10 * q['sp']:
            flag = 'inestable'
            log(f'  AVISO: al revés sale otra posición (a {moved:.1f} mm): la zona común no basta para decidir')
        else:
            log(f'  coincide (diferencia {moved:.2f} mm)')
    return M, flag


def refine(A, B, M0, log=lambda s: None, voxel=None, passes=(1.0, 0.35)):
    """Afinar con ICP (punto a plano) a partir de M0, en pasadas cada vez más finas. -> M 4×4"""
    import open3d as o3d
    reg = o3d.pipelines.registration
    v = voxel or _voxel(A, B)
    M = np.asarray(M0, np.float64).reshape(4, 4)
    for f in passes:
        pa, pb = _pc(A, v * f), _pc(B, v * f)
        r = reg.registration_icp(pb, pa, 3 * v * f, M, reg.TransformationEstimationPointToPlane(),
                                 reg.ICPConvergenceCriteria(max_iteration=60))
        M = np.asarray(r.transformation)
    log(f'  afinado con la zona común (ICP): {100 * r.fitness:.0f} % de los puntos con pareja, separación '
        f'{r.inlier_rmse:.2f} mm')
    return M


def quality(A, B, M, n=60_000):
    """Calidad de la unión: qué parte de B (ya movida) cae sobre A y cuánto se separa allí.
    -> dict(overlap: 0–1, rms: mm, sp: separación de los puntos de A, d: distancias de la muestra, idx)"""
    rng = np.random.default_rng(1)
    idx = rng.choice(len(B), min(n, len(B)), replace=False)
    Bm, _ = transform(M, B[idx])
    sp = spacing(A)
    d, _ = kdtree(A).query(Bm, workers=-1)
    near = d < 4 * sp
    return {'overlap': float(near.mean()), 'rms': float(np.sqrt(np.mean(d[near] ** 2))) if near.any() else float('nan'),
            'sp': sp, 'd': d, 'idx': idx}


def verdict(q, pairs_rms=None, flag='', how='puntos'):
    """Frase con la calidad de la unión y, si hace falta, un aviso. -> (texto, nivel 'ok' | 'aviso' | 'mal')
    flag: el aviso de auto_align; how: 'igual' (ya alineadas), 'auto' (nunca se da por buena sin mirarla) o
    'puntos'."""
    auto = how == 'auto'
    ov, rms, sp = q['overlap'], q['rms'], q['sp']
    if flag == 'simetrica':
        return (f'Zona común: {100 * ov:.0f} %, separación media {rms:.2f} mm, pero la pieza parece simétrica: otra '
                f'posición distinta encaja casi igual de bien. Comprueba que la decoración coincide o usa «Por '
                f'puntos» marcando detalles que no se repitan.', 'aviso')
    if flag == 'inestable':
        return (f'Zona común: {100 * ov:.0f} %, separación media {rms:.2f} mm, pero al repetirla al revés sale otra '
                f'posición: puede que la forma de la zona común no baste para encajarlas solas. Compruébalo en la vista '
                f'con el color «zona común»; si no encaja, usa «Por puntos».', 'aviso')
    if ov < 0.05 and how == 'igual':
        return ('Las nubes no se solapan: se juntan tal cual, con sus coordenadas. Comprueba en la vista que cada '
                'una está donde debe (si no, no comparten coordenadas: usa «Por puntos»).', 'aviso')
    if ov < 0.05:
        txt = 'Las nubes no se solapan: la unión depende solo de los puntos marcados'
        if pairs_rms is not None:
            txt += f' (error de los pares: {pairs_rms:.1f} mm)'
            return txt + '. Comprueba en la vista que encajan.', 'aviso' if pairs_rms < 10 * sp else 'mal'
        return txt + '. Sin solape no se puede alinear de forma automática: usa «Por puntos».', 'mal'
    txt = f'Zona común: {100 * ov:.0f} % de la nube añadida, separación media {rms:.2f} mm'
    if pairs_rms is not None:
        txt += f' · error de los pares {pairs_rms:.1f} mm'
    if rms > 3 * sp:
        return txt + '. La separación es grande: puede que la alineación sea falsa o que las unidades no ' \
                     'coincidan.', 'mal'
    if ov < 0.2:
        return txt + '. Poco solape: comprueba en la vista que encaja (una alineación automática con tan poco ' \
                     'solape puede ser falsa).', 'aviso'
    if auto:
        return txt + '. Compruébalo en la vista con el color «zona común»: en piezas casi planas o lisas la ' \
                     'automática puede resbalar y dar una separación igual de pequeña. Si no encaja, usa «Por ' \
                     'puntos».', 'aviso'
    return txt + '.', 'ok'
