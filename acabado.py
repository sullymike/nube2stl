"""Acabados de nube2stl sobre la malla terminada (mm de la pieza): pieza hueca, agujeros de drenaje y rótulo
grabado en la base. Se aplican en el proceso de reconstrucción (necesitan PyMeshLab), después de simplificar.

- Hueca (Params.wall): superficie interior a «wall» mm de la exterior (remallado uniforme con desplazamiento de
  PyMeshLab: distancia con signo en una rejilla y marching cubes), con las normales hacia dentro. Donde la pieza es
  más fina que dos paredes no queda hueco. Los trozos de hueco diminutos se descartan.
- Drenaje (Params.drain): dos agujeros verticales desde la base hasta el suelo del hueco, en las zonas más anchas
  de ese suelo y lo más separados posible (resina: uno vacía el líquido y el otro deja entrar el aire).
- Rótulo (Params.label): texto grabado en la cara de apoyo (z = 0), en espejo para que se lea mirando la base por
  debajo, en la zona más amplia de la base y sin pisar los agujeros.
Los agujeros y el rótulo son diferencias booleanas (PyMeshLab / libigl), que dan una malla cerrada.
"""
import numpy as np
from scipy import ndimage

import relief


def _ms_mesh(V, F):
    import pymeshlab
    return pymeshlab.Mesh(vertex_matrix=np.asarray(V, np.float64), face_matrix=np.asarray(F, np.int32))


def _cur(ms):
    m = ms.current_mesh()
    return m.vertex_matrix().copy(), m.face_matrix().astype(np.int64)


# ------------------------------------------------------------------ hueco
def cavity(V, F, wall, log=lambda s: None):
    """Sólido del hueco (normales hacia fuera del hueco) o None si la pieza es demasiado fina.

    La superficie desplazada de PyMeshLab (distancia con signo + marching cubes) está bien situada pero su malla
    suele tener aristas no-variedad; se usan sus puntos y normales para un Poisson, que da un sólido cerrado."""
    import pymeshlab
    ext = np.ptp(V, 0)
    cell = max(wall / 2, float(ext.max()) / 400)
    ms = pymeshlab.MeshSet()
    ms.add_mesh(_ms_mesh(V, F))
    ms.generate_resampled_uniform_mesh(cellsize=pymeshlab.PureValue(cell), offset=pymeshlab.PureValue(-wall),
                                       mergeclosevert=True, discretize=False, multisample=False, absdist=False)
    if ms.current_mesh().face_number() == 0:
        return None
    # trozos de hueco muy pequeños (rincones algo más gruesos que dos paredes): fuera
    ms.meshing_remove_connected_component_by_diameter(mincomponentdiag=pymeshlab.PureValue(4 * wall))
    ms.meshing_remove_unreferenced_vertices()
    if ms.current_mesh().vertex_number() < 100:
        return None
    ms.compute_normal_per_vertex()
    depth = int(np.clip(np.ceil(np.log2(float(ext.max()) * 1.1 / cell)), 5, 10))
    ms.generate_surface_reconstruction_screened_poisson(depth=depth, scale=1.1, samplespernode=1.5, pointweight=4,
                                                         preclean=True)
    ms.meshing_remove_connected_component_by_face_number(mincomponentsize=100)
    ms.meshing_remove_unreferenced_vertices()
    if ms.current_mesh().face_number() > 150_000:      # el hueco no se ve: basta una malla ligera
        ms.meshing_decimation_quadric_edge_collapse(targetfacenum=150_000, preservetopology=True, autoclean=True)
    Vc, Fc = _cur(ms)
    if relief._bad_edges(Fc, len(Vc)):
        log('AVISO: el hueco no ha salido cerrado; la pieza se deja maciza')
        return None
    if relief._volume(Vc, Fc) < 0:
        Fc = Fc[:, ::-1].copy()
    wmin = None
    try:                                                # grosor real de la pared (distancia del hueco a la piel)
        import open3d as o3d
        sc = o3d.t.geometry.RaycastingScene()
        sc.add_triangles(o3d.core.Tensor(V.astype(np.float32)), o3d.core.Tensor(F.astype(np.uint32)))
        sd = sc.compute_signed_distance(o3d.core.Tensor(Vc.astype(np.float32))).numpy()
        wmin = float(-sd.max())
        if wmin < 0.5 * wall:
            log(f'AVISO: en algún punto la pared queda en {max(wmin, 0):.2f} mm (pedida {wall:g})')
    except ImportError:
        pass
    return Vc, Fc, wmin


def _floor_points(Vc, Fc):
    """Centros de los triángulos del suelo del hueco (miran hacia abajo, cerca de su punto más bajo)."""
    t = Vc[Fc]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    c = t.mean(1)
    z0 = c[:, 2].min()
    sel = (n[:, 2] < -0.8) & (c[:, 2] < z0 + max(2.0, 0.05 * np.ptp(c[:, 2])))
    return c[sel]


def drain_holes(Vc, Fc, d, log=lambda s: None):
    """Centros (x, y, z del suelo del hueco) de hasta dos agujeros de diámetro d."""
    P = _floor_points(Vc, Fc)
    if len(P) < 3:
        return []
    r = d / 2
    g = max(r / 3, 0.2)
    lo = P[:, :2].min(0) - 2 * g
    ij = np.floor((P[:, :2] - lo) / g).astype(int)
    m = np.zeros(ij.max(0) + 3, bool)
    m[ij[:, 0], ij[:, 1]] = True
    m = ndimage.binary_fill_holes(ndimage.binary_closing(m, iterations=2))
    dist = ndimage.distance_transform_edt(m) * g
    ok = dist >= r + g                              # el agujero entero cae sobre el suelo del hueco
    if not ok.any():
        log(f'AVISO: el suelo del hueco es demasiado estrecho para agujeros de {d:g} mm; no se hacen')
        return []
    cand = np.argwhere(ok)
    a = cand[np.argmax(dist[ok])]
    far = np.hypot(*(cand - a).T)
    holes = [a]
    if far.max() * g > 3 * d:
        holes.append(cand[np.argmax(far)])
    out = []
    tree_z = P[:, 2]
    for h in holes:
        xy = lo + (h + 0.5) * g
        near = np.hypot(*(P[:, :2] - xy).T) < max(r, 2 * g)
        out.append((xy[0], xy[1], float(tree_z[near].max() if near.any() else tree_z.min())))
    return out


def _cylinder(x, y, r, z0, z1, n=32):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.c_[x + r * np.cos(a), y + r * np.sin(a)]
    V = np.r_[np.c_[ring, np.full(n, z0)], np.c_[ring, np.full(n, z1)], [[x, y, z0], [x, y, z1]]]
    i = np.arange(n)
    j = (i + 1) % n
    F = np.r_[np.c_[i, j, n + j], np.c_[i, n + j, n + i],          # lateral
              np.c_[np.full(n, 2 * n), j, i], np.c_[np.full(n, 2 * n + 1), n + i, n + j]]   # tapas
    return V, F


# ------------------------------------------------------------------ rótulo
def _font(px):
    from PIL import ImageFont
    try:
        import matplotlib
        import os
        p = os.path.join(matplotlib.get_data_path(), 'fonts', 'ttf', 'DejaVuSans-Bold.ttf')
        return ImageFont.truetype(p, px)
    except Exception:
        return ImageFont.load_default()


def text_mask(text, height_mm, px_mm):
    """Máscara del texto (filas = y, columnas = x) con letras de height_mm de alto, a px_mm mm por píxel."""
    from PIL import Image, ImageDraw
    lines = [s.strip() for s in text.split('|') if s.strip()]
    px = max(8, int(round(height_mm / px_mm)))
    font = _font(int(px * 1.38))             # tamaño de letra para que las mayúsculas midan ~px
    dummy = ImageDraw.Draw(Image.new('L', (1, 1)))
    boxes = [dummy.textbbox((0, 0), s, font=font) for s in lines]
    w = max(b[2] - b[0] for b in boxes) + 4
    lh = int(px * 1.45)
    img = Image.new('L', (w, lh * len(lines) + 4), 0)
    d = ImageDraw.Draw(img)
    for k, (s, b) in enumerate(zip(lines, boxes)):
        d.text((2 - b[0] + (w - 4 - (b[2] - b[0])) // 2, 2 + k * lh - b[1] + (lh - px) // 2), s, fill=255, font=font)
    m = np.asarray(img) > 127
    rows, cols = np.nonzero(m)
    m = m[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    return m[::-1]                              # fila 0 = y menor


def _no_diagonals(m):
    """Rellena los contactos solo por esquina (darían vértices no-variedad al hacer el prisma)."""
    m = m.copy()
    for _ in range(4):
        a, b, c, d = m[:-1, :-1], m[:-1, 1:], m[1:, :-1], m[1:, 1:]
        p1 = a & d & ~b & ~c
        p2 = b & c & ~a & ~d
        if not (p1.any() or p2.any()):
            break
        m[:-1, 1:] |= p1
        m[:-1, :-1] |= p2
    return m


def base_footprint(V, F, g):
    """Zona de apoyo (triángulos en z ≈ 0 que miran hacia abajo) en una rejilla de paso g: (máscara, origen)."""
    t = V[F]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    a2 = np.linalg.norm(n, axis=1)
    sel = (np.abs(t[:, :, 2]).max(1) < 0.05) & (n[:, 2] < -0.98 * a2) & (a2 > 0)
    if not sel.any():
        return None, None
    import entrada
    P, _, _ = entrada.sample_mesh(V, F[sel], n=int(min(4e6, max(1e4, 4 * (a2[sel].sum() / 2) / g ** 2))))
    lo = P[:, :2].min(0) - g
    ij = np.floor((P[:, :2] - lo) / g).astype(int)
    m = np.zeros(ij.max(0) + 2, bool)
    m[ij[:, 0], ij[:, 1]] = True
    m = ndimage.binary_closing(m, iterations=1) | m
    return m.T, lo                                 # filas = y


def place_label(foot, lo, g, text, size, avoid, log):
    """Busca dónde cabe el rótulo: (máscara del texto en la rejilla, fila, columna) o None."""
    margin = 1.0
    free = ndimage.binary_erosion(foot, iterations=max(1, int(np.ceil(margin / g))))
    yy, xx = np.mgrid[0:foot.shape[0], 0:foot.shape[1]]
    for (x, y, rr) in avoid:
        free &= np.hypot(lo[0] + (xx + .5) * g - x, lo[1] + (yy + .5) * g - y) > rr + margin
    S = np.pad(free.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    cy, cx = np.argwhere(free).mean(0) if free.any() else (0, 0)
    sizes = [size] if size > 0 else [8, 7, 6, 5, 4, 3.5, 3, 2.5, 2]
    for h in sizes:
        m = text_mask(text, h, g)[:, ::-1]          # en espejo: se lee mirando la base desde abajo
        th, tw = m.shape
        if th >= free.shape[0] or tw >= free.shape[1]:
            continue
        win = S[th:, tw:] - S[:-th, tw:] - S[th:, :-tw] + S[:-th, :-tw]
        ok = np.argwhere(win == th * tw)
        if len(ok):
            k = np.argmin(np.hypot(ok[:, 0] + th / 2 - cy, ok[:, 1] + tw / 2 - cx))
            return m, ok[k][0], ok[k][1], h
        if size > 0:
            break
    return None


# ------------------------------------------------------------------ todo junto
def apply(r, prm, log=lambda s: None):
    """Aplica hueco, drenaje y rótulo a un Result (en mm de la pieza)."""
    wall, drain, text = getattr(prm, 'wall', 0), getattr(prm, 'drain', 0), getattr(prm, 'label', '').strip()
    if wall <= 0 and not text:
        return r
    import pymeshlab
    V, F = r.verts.astype(np.float64), r.tris.astype(np.int64)
    z0 = V[:, 2].min()
    V = V - [0, 0, z0]
    vol0 = relief._volume(V, F)
    cuts = []                           # sólidos a restar (agujeros y letras)
    cav, holes = None, []
    if wall > 0:
        thin = 2 * wall + 0.5
        if min(np.ptp(V, 0)) < thin:
            log(f'Pieza hueca: la pieza mide menos de {thin:.1f} mm en algún sentido; se deja maciza')
        else:
            log(f'Pieza hueca: pared de {wall:g} mm…')
            cav = cavity(V, F, wall, log)
            if cav is None:
                log('Pieza hueca: no cabe ningún hueco con esa pared; se deja maciza')
            elif drain > 0:
                holes = drain_holes(cav[0], cav[1], drain, log)
                for (x, y, zf) in holes:
                    cuts.append(_cylinder(x, y, drain / 2, -1.0, zf + wall / 2 + 0.5))
    label_cut = None
    if text:
        g = 0.15
        foot, lo = base_footprint(V, F, g)
        if foot is None:
            log('AVISO: no hay cara de apoyo plana en z = 0; no se graba el rótulo')
        else:
            pl = place_label(foot, lo, g, text, getattr(prm, 'label_size', 0),
                             [(x, y, drain / 2) for x, y, _ in holes], log)
            if pl is None:
                log(f'AVISO: el rótulo «{text}» no cabe en la base (prueba un texto más corto o letras más '
                    f'pequeñas); no se graba')
            else:
                m, r0, c0, h = pl
                depth = max(0.4, min(1.0, 3 * prm.layer))
                if cav is not None:
                    depth = min(depth, 0.45 * wall)
                m = _no_diagonals(m)
                Vt, Ft, _ = relief._mesh(np.zeros(m.shape), m, g, depth + 1.0)
                Vt[:, 0] += lo[0] + c0 * g
                Vt[:, 1] += lo[1] + r0 * g
                Vt[:, 2] -= 1.0
                label_cut = (Vt, Ft)
                log(f'Rótulo «{text}» grabado en la base: letras de {h:g} mm, {depth:.1f} mm de hondo, '
                    f'{m.shape[1] * g:.0f} × {m.shape[0] * g:.0f} mm (en espejo: se lee mirando la base por debajo)')
    if label_cut is not None:
        cuts.append(label_cut)
    # montar: exterior − (hueco ∪ agujeros ∪ letras)
    # hueco cerrado sin agujeros y bien dentro de la pieza: basta añadirlo al revés. Si se acerca a la piel (o no se
    # ha podido medir), resta booleana: aunque asomara, el resultado no se cruza consigo mismo
    safe = cav is not None and cav[2] is not None and cav[2] > 0.5 * wall
    if cav is not None and not cuts and safe:
        Vc, Fc = cav[:2]
        V2 = np.r_[V, Vc]
        F2 = np.r_[F, Fc[:, ::-1] + len(V)]
    elif cav is not None or cuts:
        ms = pymeshlab.MeshSet()
        ms.add_mesh(_ms_mesh(V, F))
        tool = cav[:2] if cav is not None else cuts.pop(0)
        ms.add_mesh(_ms_mesh(*tool))
        tid = 1
        for c in cuts:
            ms.add_mesh(_ms_mesh(*c))
            ms.generate_boolean_union(first_mesh=tid, second_mesh=ms.current_mesh_id())
            tid = ms.current_mesh_id()
        ms.generate_boolean_difference(first_mesh=0, second_mesh=tid)
        V2, F2 = _cur(ms)
    else:
        return r
    bad = relief._bad_edges(F2, len(V2))
    vol = relief._volume(V2, F2)
    if vol < 0:
        F2 = F2[:, ::-1].copy()
        vol = -vol
    if bad:
        log(f'AVISO: tras el hueco / rótulo quedan {bad} aristas abiertas')
    if cav is not None:
        log(f'Pieza hueca: volumen {vol0 / 1000:.1f} → {vol / 1000:.1f} cm³ ({100 * vol / vol0:.0f} %)'
            + (f'; {len(holes)} agujero(s) de drenaje de {drain:g} mm' if holes else
               ('; sin agujeros de drenaje (en resina hacen falta)' if drain <= 0 else '')))
    V2[:, 2] += z0
    vcol = None
    if r.vcolors is not None and len(V2) >= len(r.verts) and np.allclose(V2[:len(r.verts)], r.verts):
        vcol = np.r_[r.vcolors, np.full((len(V2) - len(r.verts), 3), 128, np.uint8)]
    return relief.Result(V2, F2, vcol, r.grid, r.cell, r.scale, r.angle, bad, vol, r.err_mean, r.err_max,
                         r.mode, r.err_p99, r.res, xform=r.xform, hollow=r.hollow or cav is not None,
                         zclip=r.zclip, corner=r.corner, view_grid=getattr(r, 'view_grid', None))
