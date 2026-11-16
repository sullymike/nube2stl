"""Modo «objeto» de nube2stl: objeto completo escaneado por todos los lados (estatua, vasija, capitel…).

A diferencia de los modos de relieve, aquí no hay cara frontal ni mapa de alturas: se trabaja con la nube en 3D.

1. Se gira para que el lado elegido («Arriba») quede hacia +Z, se espeja y se gira en planta si se pide.
2. Submuestreo a la resolución de impresión y limpieza de puntos sueltos.
3. Normales: las del fichero si las trae (escáneres) o estimadas por vecindad. Las estimadas no tienen signo; se
   orientan de forma coherente propagándolas por la superficie (árbol de expansión mínima, en una muestra
   reducida, y de ahí a todos los puntos por el vecino más cercano), y el signo global se decide en los puntos
   extremos del objeto, cuya normal tiene que apuntar hacia fuera.
4. Base plana para apoyar en la cama: se quitan los puntos por debajo del corte y se pone una tapa de puntos
   sintéticos con la silueta del objeto a esa altura (el fondo de un objeto casi nunca está escaneado).
5. Poisson apantallado da directamente un sólido cerrado; se aplana la base, se escala y se repara si hace falta.

Escaneo abierto por abajo («Abierto por abajo», Params.ground): un terreno, una ruina o un edificio escaneado desde
el aire solo tiene la superficie de arriba, con bordes abiertos alrededor. Poisson cierra el sólido inventando
superficie lejos de los puntos (una «burbuja»). En vez de cortar la base, se ponen puntos sintéticos: paredes
verticales en todo el contorno de la planta, desde la superficie hasta una base plana «Grosor base» mm por debajo
del punto más bajo, y la base. Así la pieza es una maqueta que apoya en su base, con los huecos pasantes (arcos,
ventanas) que tenga el escaneo.
"""
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

import recon3d
import relief

# giro que lleva el eje elegido como «arriba» a +Z (matrices de rotación propias, det = +1)
UP = {
    'z': [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    '-z': [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
    'y': [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    '-y': [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
    'x': [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
    '-x': [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
}
ORIENT_MAX = 250_000           # puntos en los que se orientan las normales (el árbol es caro)


def up_matrix(prm):
    """Giro completo de la nube: eje «arriba» a +Z, giro en planta y espejo (este último en la propia matriz)."""
    R = np.array(UP.get(prm.up, UP['z']), float)
    a = np.radians(prm.angle)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    M = Rz @ R
    if prm.mirror:
        M = np.diag([-1.0, 1, 1]) @ M
    return M


def _hemisphere(n=16):
    """Direcciones repartidas por un hemisferio (z > 0,25), en espiral de Fibonacci."""
    k = np.arange(n) + 0.5
    z = 0.25 + 0.75 * k / n
    phi = k * np.pi * (3 - np.sqrt(5))
    r = np.sqrt(1 - z ** 2)
    return np.c_[r * np.cos(phi), r * np.sin(phi), z]


def _visibility(o3d, S, N, ext, extra=None):
    """Evidencia de qué lado de cada punto es el exterior: todo lo escaneado se vio desde fuera, así que desde
    el lado exterior los rayos escapan del objeto y desde el interior chocan con él. La superficie para lanzar
    los rayos es la de los vóxeles ocupados por la nube. Devuelve (+1 si la normal apunta hacia fuera, −1 si
    hacia dentro, 0 si no se sabe) por punto. extra: puntos que también tapan los rayos (superficies sintéticas
    que cierran una pieza abierta, como la trasera de un bloque)."""
    g = ext / 220
    occ = S if extra is None or not len(extra) else np.concatenate([S, extra])
    iv = np.unique(np.floor(occ / g).astype(np.int64), axis=0)
    # caras de los cubos ocupados (12 triángulos por vóxel)
    cube = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
    tri = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    V = ((iv[:, None, :] + cube[None]) * g).reshape(-1, 3)
    T = (np.arange(len(iv))[:, None, None] * 8 + tri[None]).reshape(-1, 3)
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(V.astype(np.float32)), o3d.core.Tensor(T.astype(np.uint32)))
    H = _hemisphere()
    a = np.where(np.abs(N[:, 0:1]) < 0.9, [[1.0, 0, 0]], [[0, 1.0, 0]])
    t1 = np.cross(N, a)
    t1 /= np.linalg.norm(t1, axis=1, keepdims=True)
    t2 = np.cross(N, t1)
    esc = []
    for sgn in (1.0, -1.0):
        n = sgn * N
        D = (H[None, :, 0:1] * t1[:, None] + H[None, :, 1:2] * t2[:, None] + H[None, :, 2:3] * n[:, None])
        O = S[:, None, :] + D * (2.5 * g)              # salir del propio vóxel (y de la pared, si es fina)
        rays = np.concatenate([O, D], 2).reshape(-1, 6).astype(np.float32)
        hit = np.zeros(len(rays), bool)
        for i in range(0, len(rays), 4_000_000):
            t = scene.cast_rays(o3d.core.Tensor(rays[i:i + 4_000_000]))['t_hit'].numpy()
            hit[i:i + 4_000_000] = np.isfinite(t)
        esc.append((~hit).reshape(len(S), -1).mean(1))
    d = esc[0] - esc[1]
    return np.where(d > 0.15, 1, np.where(d < -0.15, -1, 0))


def _orient_signs(o3d, S, N, k=12, extra=None):
    """Orientación coherente de normales sin signo.

    1. Grafo de k vecinos con un peso que favorece las vecinas casi paralelas y en la misma superficie
       (1 − |nᵢ·nⱼ| + |e·nᵢ| + |e·nⱼ|, e = dirección de la arista; sin los dos últimos términos, en los huecos
       estrechos —un asa pegada al cuerpo— se unirían dos caras enfrentadas) y árbol de expansión mínima
       (Hoppe et al., 1992).
    2. El árbol se corta donde las normales vecinas no son claramente paralelas (aristas vivas, labios finos):
       ahí el signo no se puede propagar con seguridad. Cada tramo se orienta por dentro recorriendo su árbol.
    3. El signo de cada tramo lo decide la visibilidad (_visibility); los tramos sin evidencia lo toman de su
       vecino a través del corte; y si una parte no tiene ninguna, de sus puntos extremos, cuya normal tiene que
       apuntar hacia fuera."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import breadth_first_order, connected_components, minimum_spanning_tree
    n = len(S)
    _, j = cKDTree(S).query(S, k + 1, workers=-1)
    i = np.repeat(np.arange(n), k)
    j = j[:, 1:].ravel()
    ok = j < n
    i, j = i[ok], j[ok]
    e = S[j] - S[i]
    e /= np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-12)
    w = (1.0 - np.abs(np.einsum('ij,ij->i', N[i], N[j])) + np.abs(np.einsum('ij,ij->i', e, N[i]))
         + np.abs(np.einsum('ij,ij->i', e, N[j])) + 1e-6)
    T = minimum_spanning_tree(coo_matrix((w, (i, j)), shape=(n, n)).tocsr()).tocoo()
    u, v = T.row, T.col
    dot = np.einsum('ij,ij->i', N[u], N[v])
    firm = np.abs(dot) >= 0.95
    ncomp, comp = connected_components(coo_matrix((np.ones(len(u)), (u, v)), shape=(n, n)), directed=False)
    nseg, seg = connected_components(coo_matrix((np.ones(firm.sum()), (u[firm], v[firm])), shape=(n, n)),
                                     directed=False)
    # signo relativo dentro de cada tramo
    F = coo_matrix((np.sign(dot[firm]) + 0.0, (u[firm], v[firm])), shape=(n, n)).tocsr()
    F = (F + F.T).tocsr()
    rel = np.ones(n)
    roots = np.full(nseg, -1)
    roots[seg[::-1]] = np.arange(n)[::-1]
    for r in roots:
        order, pred = breadth_first_order(F, r, directed=False)
        if len(order) < 2:
            continue
        sg = np.asarray(F[pred[order[1:]], order[1:]]).ravel()
        for vv, pp, ss in zip(order[1:], pred[order[1:]], sg):
            rel[vv] = rel[pp] * ss
    N = N * rel[:, None]
    # visibilidad: puntuación de cada tramo
    ext = float(np.ptp(S, 0).max())
    vis = _visibility(o3d, S, N, ext, extra)
    score = np.bincount(seg, vis, nseg)
    size = np.bincount(seg, minlength=nseg)
    known = np.abs(score) >= np.maximum(3, 0.05 * size)
    sign = np.where(known, np.sign(score), 0.0)
    # tramos sin evidencia: del vecino ya decidido, a través del corte con normales más parecidas
    cut = ~firm
    cu, cv, cd = seg[u[cut]], seg[v[cut]], np.sign(dot[cut])
    o = np.argsort(-np.abs(dot[cut]))
    cu, cv, cd = cu[o], cv[o], cd[o]
    for _ in range(nseg):
        a1 = (sign[cu] != 0) & (sign[cv] == 0)
        a2 = (sign[cv] != 0) & (sign[cu] == 0)
        if not (a1.any() or a2.any()):
            break
        sign[cv[a1]] = sign[cu[a1]] * cd[a1]
        sign[cu[a2]] = sign[cv[a2]] * cd[a2]
    # partes sin ninguna evidencia: puntos extremos
    left = sign[seg] == 0
    if left.any():
        for c in np.unique(comp[left]):
            idx = np.flatnonzero(left & (comp == c))
            votes = 0.0
            for d in np.vstack([np.eye(3), -np.eye(3)]):
                t = S[idx] @ d
                sel = t >= np.percentile(t, 99)
                votes += np.sign(N[idx[sel]] @ d).sum()
            N[idx] *= -1.0 if votes < 0 else 1.0
        sign[sign == 0] = 1.0
    N = N * sign[seg][:, None]
    return N, {'partes': ncomp, 'tramos': nseg, 'con_evidencia': int(known.sum())}


def _orient_normals(o3d, pc, res, log, extra=None):
    """Normales sin signo -> orientadas hacia fuera de forma coherente (en una muestra de como mucho ORIENT_MAX
    puntos; al resto se le da el signo de la normal orientada más cercana)."""
    P = np.asarray(pc.points)
    k = max(1, int(np.ceil(len(P) / ORIENT_MAX)))
    sub = pc.voxel_down_sample(res * np.sqrt(k)) if k > 1 else o3d.geometry.PointCloud(pc)
    rs = res * np.sqrt(k)
    sub.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=4 * rs, max_nn=30))
    S = np.asarray(sub.points)
    Ns, info = _orient_signs(o3d, S, np.asarray(sub.normals), extra=extra)
    log(f'Normales orientadas: {info["tramos"]:,} tramos entre aristas vivas, {info["con_evidencia"]:,} con '
        f'evidencia de visibilidad' + (f'; {info["partes"]} partes separadas' if info['partes'] > 1 else ''))
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=3 * res, max_nn=30))
    N = np.asarray(pc.normals).copy()
    _, j = cKDTree(S).query(P, workers=-1)
    N *= np.where(np.einsum('ij,ij->i', N, Ns[j]) < 0, -1.0, 1.0)[:, None]
    pc.normals = o3d.utility.Vector3dVector(N)


def denoise_points(pc, res, res_scan, cells, log=lambda s: None):
    """Alisado del ruido de medida: cada punto se lleva, a lo largo de su normal, al plano medio de sus vecinos a
    menos de cells·res_scan (media ponderada con una gaussiana y solo con los vecinos del mismo lado de la
    superficie, para no mezclar las dos caras de una pared fina). Sin esto, Poisson calca el ruido del escáner y la
    superficie sale granulada («martilleada») aunque la forma sea correcta. No mueve los puntos a lo largo de la
    superficie, así que no encoge la pieza; redondea algo las aristas más finas que el radio.

    El radio se mide en separaciones del escaneo, no en celdas de la resolución de trabajo: el ruido está a la
    escala de los puntos medidos, y cuando la resolución la limita la impresora el submuestreo ya ha promediado
    varios puntos por celda (y con ellos buena parte del ruido)."""
    if cells <= 0 or not pc.has_normals():
        return
    r = cells * res_scan
    if r < 1.2 * res:
        log(f'Alisado del ruido: no hace falta (radio {r:.2f} < celda {res:g}; el submuestreo ya promedia '
            f'≈ {(res / res_scan) ** 2:.0f} puntos del escaneo por celda)')
        return
    P = np.asarray(pc.points)
    N = np.asarray(pc.normals)
    tree = cKDTree(P)
    out = np.empty_like(P)
    for i in range(0, len(P), 200_000):
        p, n = P[i:i + 200_000], N[i:i + 200_000]
        d, j = tree.query(p, 32, distance_upper_bound=r, workers=-1)
        ok = np.isfinite(d)
        j = np.where(ok, j, 0)
        w = np.where(ok, np.exp(-(2 * d / r) ** 2), 0.0) * np.clip(np.einsum('ikj,ij->ik', N[j], n), 0, 1)
        c = np.einsum('ik,ikj->ij', w, P[j]) / np.maximum(w.sum(1, keepdims=True), 1e-12)
        out[i:i + 200_000] = p - np.einsum('ij,ij->i', p - c, n)[:, None] * n
    mv = np.linalg.norm(out - P, axis=1)
    pc.points = recon3d._need_o3d().utility.Vector3dVector(out)
    log(f'Alisado del ruido (radio {r:.2f}): desplazamiento medio {mv.mean():.3f}, p99 {np.percentile(mv, 99):.3f}')


def check_file_normals(o3d, pc, log=lambda s: None, n=60_000):
    """¿Son fiables las normales del fichero? Las de una malla de fotogrametría pueden venir invertidas (todas o
    a trozos). Se comprueba con la misma visibilidad que orienta las estimadas: en una muestra, qué parte apunta
    hacia fuera. Todas al revés: se invierten. Mezcladas: se descartan (se estiman y orientan). Devuelve si se
    pueden usar."""
    P, N = np.asarray(pc.points), np.asarray(pc.normals)
    idx = np.random.default_rng(0).choice(len(P), min(n, len(P)), replace=False)
    S = P[idx]
    vis = _visibility(o3d, S, N[idx], float(np.ptp(S, 0).max()))
    good, bad = int((vis > 0).sum()), int((vis < 0).sum())
    if good + bad < 100:
        return True
    f = bad / (good + bad)
    if f > 0.85:
        pc.normals = o3d.utility.Vector3dVector(-N)
        log(f'Normales del fichero invertidas ({100 * f:.0f} % hacia dentro): se les da la vuelta')
        return True
    if f > 0.04:
        log(f'Normales del fichero poco fiables ({100 * f:.0f} % hacia dentro, p. ej. malla con caras invertidas): '
            f'se estiman y orientan de nuevo')
        return False
    return True


def clean_cloud(pc, res, log=lambda s: None):
    """Submuestreo a la resolución, puntos sueltos fuera y restos separados de la pieza principal (la mesa, el
    soporte, nubes de ruido)."""
    pc = pc.voxel_down_sample(res)
    pc, _ = pc.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.5)
    lab = np.asarray(pc.cluster_dbscan(eps=3 * res, min_points=4))
    if lab.size and lab.max() > 0:
        cnt = np.bincount(lab[lab >= 0])
        keep = np.isin(lab, np.flatnonzero(cnt >= 0.02 * cnt.max()))
        if (~keep).sum():
            log(f'Quitados {(~keep).sum():,} puntos de {len(cnt) - int((cnt >= 0.02 * cnt.max()).sum())} '
                f'grupos sueltos')
        pc = pc.select_by_index(np.flatnonzero(keep))
    log(f'Submuestreo a {res:g} y limpieza: {len(pc.points):,} puntos')
    return pc


def _base_cap(P, zc, res):
    """Tapa plana en z = zc con la silueta del objeto a esa altura: (puntos, normales) sintéticos."""
    band = P[(P[:, 2] >= zc) & (P[:, 2] < zc + 3 * res)]
    if len(band) < 10:
        return np.empty((0, 3)), np.empty((0, 3))
    m = 12
    x0, y0 = band[:, :2].min(0) - m * res
    ix = ((band[:, 0] - x0) / res).astype(int)
    iy = ((band[:, 1] - y0) / res).astype(int)
    occ0 = np.zeros((iy.max() + m + 1, ix.max() + m + 1), bool)
    occ0[iy, ix] = True
    # la sección es un anillo de puntos (la pared): se engruesa (cada vez más, hasta que no tenga huecos ni
    # pasos en diagonal por los que se escape el relleno), se rellena y se vuelve a adelgazar, un vóxel más para
    # que la tapa no asome fuera de la pared
    sq = np.ones((3, 3), bool)
    for it in (1, 2, 4, 7):
        ring = ndimage.binary_dilation(occ0, sq, iterations=it)
        occ = ndimage.binary_fill_holes(ring)
        if occ.sum() > 1.5 * ring.sum():
            break
    occ = ndimage.binary_erosion(occ, sq, iterations=it + 1)
    rr, cc = np.nonzero(occ)
    cap = np.c_[x0 + (cc + .5) * res, y0 + (rr + .5) * res, np.full(len(rr), zc)]
    return cap, np.tile([0, 0, -1.0], (len(cap), 1))


def _ground_skirt(P, res, zb):
    """Paredes verticales en el contorno de la planta (desde la superficie escaneada hasta zb) y base plana en zb:
    (puntos, normales) sintéticos para cerrar un escaneo abierto por abajo."""
    m = 3
    x0, y0 = P[:, :2].min(0) - m * res
    ix = ((P[:, 0] - x0) / res).astype(np.int64)
    iy = ((P[:, 1] - y0) / res).astype(np.int64)
    shape = (int(iy.max()) + m + 1, int(ix.max()) + m + 1)
    low = np.full(shape, np.inf)
    np.minimum.at(low, (iy, ix), P[:, 2])
    occ0 = np.isfinite(low)
    occ = ndimage.binary_fill_holes(ndimage.binary_closing(occ0, np.ones((3, 3), bool), iterations=2)) | occ0
    # altura de la superficie en las celdas sin puntos (huecos rellenados): la del vecino con puntos más cercano
    _, (ry, rx) = ndimage.distance_transform_edt(~occ0, return_indices=True)
    low = low[ry, rx]
    edge = occ & ~ndimage.binary_erosion(occ, np.ones((3, 3), bool))
    sm = ndimage.gaussian_filter(occ.astype(float), 1.5)
    gy, gx = np.gradient(sm)
    rr, cc = np.nonzero(edge)
    nx, ny = -gx[rr, cc], -gy[rr, cc]
    nn = np.hypot(nx, ny) + 1e-12
    wall, wn = [], []
    for r, c, ax, ay, h in zip(rr, cc, nx / nn, ny / nn, low[rr, cc]):
        z = np.arange(zb, h, res)
        if len(z):
            wall.append(np.c_[np.full(len(z), x0 + (c + .5) * res), np.full(len(z), y0 + (r + .5) * res), z])
            wn.append(np.tile([ax, ay, 0.0], (len(z), 1)))
    br, bc = np.nonzero(occ)
    bottom = np.c_[x0 + (bc + .5) * res, y0 + (br + .5) * res, np.full(len(br), zb)]
    pts = np.vstack(wall + [bottom]) if wall else bottom
    nrm = np.vstack(wn + [np.tile([0, 0, -1.0], (len(bottom), 1))]) if wn else np.tile([0, 0, -1.0], (len(bottom), 1))
    return pts, nrm, len(bottom), float(occ.sum() * res * res)


def _invented(verts, tris, pts, res):
    """Parte de la superficie (fracción del área) que Poisson ha puesto lejos de todos los puntos (> 3 celdas)."""
    import desviacion
    d, _ = cKDTree(pts).query(verts, workers=-1)
    A = desviacion.vertex_areas(verts, tris)
    return float(A[d > 3 * res].sum() / max(A.sum(), 1e-12))


def _top_grid(verts, n=400):
    """Vista en planta (altura máxima por celda) del objeto terminado, para la vista «Relieve sombreado»."""
    lo, hi = verts.min(0), verts.max(0)
    cell = max(hi[0] - lo[0], hi[1] - lo[1]) / n
    ix = ((verts[:, 0] - lo[0]) / cell).astype(int)
    iy = ((verts[:, 1] - lo[1]) / cell).astype(int)
    g = np.full((iy.max() + 1, ix.max() + 1), -np.inf)
    np.maximum.at(g, (iy, ix), verts[:, 2])
    g[np.isinf(g)] = np.nan
    occ = ~np.isnan(g)
    fill = ndimage.binary_closing(occ, iterations=2) & ~occ     # huecos sueltos entre vértices
    if fill.any():
        g = np.where(fill, ndimage.grey_dilation(np.nan_to_num(g, nan=-1e9), size=3), g)
        g[g < -1e8] = np.nan
    return g, cell


def reconstruct_object(p, prm, rgb=None, normals=None, log=lambda s: None):
    o3d = recon3d._need_o3d()
    for name, why in (('negative', 'el molde'), ('frame', 'el marco'), ('auto_straighten', 'el enderezado')):
        if getattr(prm, name):
            log(f'AVISO: {why} no se aplica en modo objeto')
    M = up_matrix(prm)
    c0 = p.mean(0, dtype=np.float64)
    P = np.empty((len(p), 3), np.float64)
    for i in range(0, len(p), 2_000_000):
        P[i:i + 2_000_000] = (p[i:i + 2_000_000] - c0) @ M.T
    st = relief.object_stats(p)
    pl = relief.print_plan({'length': 0, 'width': 0, 'res_scan': st['res_scan'], 'obj': st}, prm)
    s, res = pl['scale'], pl['res']
    log(f'Modo objeto: {len(P):,} puntos; «arriba» = {prm.up}; escala {s:.4f}; resolución {res:.2f} '
        f'({pl["why"]}) → {res * s:.3f} mm en la pieza')

    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P))
    del P
    if rgb is not None:
        pc.colors = o3d.utility.Vector3dVector(rgb.astype(np.float64) / 255.0)
    have_n = normals is not None and len(normals) == len(p)
    if have_n:
        pc.normals = o3d.utility.Vector3dVector(normals.astype(np.float64) @ M.T)
    pc = clean_cloud(pc, res, log)
    ground = getattr(prm, 'ground', False)
    sk = None
    if ground:              # escaneo abierto por abajo: paredes y base sintéticas (también tapan los rayos de abajo
        Pg = np.asarray(pc.points)          # al orientar las normales: el exterior es lo de arriba y los lados)
        zmin = float(np.percentile(Pg[:, 2], 0.05))
        zc = zmin - max(prm.base, 0.2) / s
        pc = pc.select_by_index(np.flatnonzero(Pg[:, 2] >= zmin))
        sk, skn, nb, area = _ground_skirt(np.asarray(pc.points), res, zc)
        log(f'Abierto por abajo: paredes en el contorno y base plana {prm.base:g} mm bajo el punto más bajo '
            f'({len(sk) - nb:,} + {nb:,} puntos sintéticos; planta de {area * s * s / 100:.1f} cm²)')
        if have_n:
            log('Normales del fichero: no se usan con «Abierto por abajo» (se orientan de nuevo, mirando desde '
                'arriba y los lados)')
            have_n = False
    if have_n:
        N = np.asarray(pc.normals)
        N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
        pc.normals = o3d.utility.Vector3dVector(N)
        if getattr(prm, 'interior', 'no') == 'no':   # las de un interior ya vienen orientadas desde dentro
            have_n = check_file_normals(o3d, pc, log)
    if have_n:
        log('Normales: las del fichero')
    else:
        log('Normales: estimando y orientando hacia fuera…')
        _orient_normals(o3d, pc, res, log, extra=sk)
    denoise_points(pc, res, pl['res_scan'], prm.denoise, log)

    # ---------------- base plana
    P = np.asarray(pc.points)
    if not ground:
        zmin = float(np.percentile(P[:, 2], 0.05))
        zc = zmin + max(prm.cut, 0.0) / s
    if ground:
        syn = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(sk))
        syn.normals = o3d.utility.Vector3dVector(skn)
        if pc.has_colors():
            syn.colors = o3d.utility.Vector3dVector(np.full((len(sk), 3), 0.55))
        pc = pc + syn
    elif prm.cut > 0:
        keep = np.flatnonzero(P[:, 2] >= zc)
        pc = pc.select_by_index(keep)
        P = np.asarray(pc.points)
        cap, capn = _base_cap(P, zc, res)
        if len(cap):
            syn = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cap))
            syn.normals = o3d.utility.Vector3dVector(capn)
            if pc.has_colors():
                syn.colors = o3d.utility.Vector3dVector(np.full((len(cap), 3), 0.55))
            pc = pc + syn
            log(f'Base plana: corte a {prm.cut:g} mm del punto más bajo, tapa de {len(cap):,} puntos '
                f'({len(cap) * (res * s) ** 2 / 100:.1f} cm² de apoyo)')
        else:
            log('AVISO: casi no hay puntos a la altura del corte: el objeto apenas apoya; sube «Corte de la base»')
    else:
        log('Sin corte de base: el objeto apoyará en su punto más bajo (puede necesitar soportes)')

    # ---------------- Poisson
    Nn = np.asarray(pc.normals)
    okn = np.isfinite(Nn).all(1) & (np.linalg.norm(np.nan_to_num(Nn), axis=1) > 0.5)
    if not okn.all():                       # Poisson no admite normales nulas (puntos repetidos, alisado…)
        pc = pc.select_by_index(np.flatnonzero(okn))
    ext = np.ptp(np.asarray(pc.points), 0).max()
    depth = prm.poisson_depth or relief.poisson_depth_auto(None, prm, res, ext=ext)
    log(f'Poisson apantallado, profundidad {depth} (celda ≈ {ext * 1.1 / 2 ** depth:.2f}), '
        f'{relief.threads_for(prm, "poisson")} hilos…')
    try:
        verts, tris, vcol = recon3d._poisson_meshlab(pc, depth, relief.threads_for(prm, 'poisson'))
    except ImportError:
        verts, tris, vcol = recon3d._poisson_open3d(o3d, pc, depth)
    inv = _invented(verts, tris, np.asarray(pc.points), res)
    del pc
    if inv > 0.03:
        log(f'AVISO: el {100 * inv:.0f} % de la superficie lo ha inventado Poisson lejos de los puntos del '
            f'escaneo (se ve gris en el mapa de desviación). Pasa cuando el escaneo no rodea el objeto: si es un '
            f'terreno, una ruina o un edificio sin la parte de abajo, elige «Terreno» en «¿Qué has escaneado?» '
            f'(abierto por abajo); si sobra algo, recórtalo en la vista de la nube.' if not ground else
            f'AVISO: el {100 * inv:.0f} % de la superficie está lejos de los puntos del escaneo: revisa la pieza '
            f'(huecos grandes en el escaneo o restos sueltos que conviene recortar).')
    bad = relief._bad_edges(tris, len(verts))
    if bad:
        verts, tris, vcol, bad = recon3d._repair(verts, tris, vcol, log)

    # ---------------- a la pieza: base en z = 0, esquina en el origen, escala
    if prm.cut > 0 or ground:
        z = verts[:, 2]
        z[z < zc + 0.5 * res] = zc                          # aplanar la base para que apoye bien en la cama
    vmin = verts.min(0)
    verts = verts - vmin
    if prm.target_len > 0 and prm.ratio <= 0:             # escala exacta: el lado mayor de la malla final
        s = prm.target_len / float(np.ptp(verts, 0).max())
    verts *= s
    vol = relief._volume(verts, tris)
    if vol < 0:
        tris = tris[:, ::-1].copy()
        vol = -vol
    sx, sy, sz = np.ptp(verts, 0)
    log(f'{len(tris):,} triángulos, aristas no-manifold: {bad}, volumen {vol / 1000:.1f} cm³, '
        f'{sx:.1f} × {sy:.1f} × {sz:.1f} mm')
    grid, cell = _top_grid(verts)
    T = relief.affine(s * M, -s * (M @ c0 + vmin))
    return relief.Result(verts, tris, vcol, grid, cell, s, prm.angle, bad, vol, mode='objeto', res=res, xform=T,
                         zclip=(-0.5 * res * s, np.inf) if prm.cut > 0 and not ground else None)
