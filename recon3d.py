"""Modo 3D de nube2stl: reconstrucción de Poisson apantallada que conserva los socavados del relieve.

Un escaneo de una sola cara no encierra ningún volumen, y Poisson necesita una superficie cerrada para dar
un sólido estanco. Por eso, a los puntos reales de la cara frontal se les añaden puntos sintéticos que forman
una trasera plana y paredes verticales siguiendo el contorno: el conjunto es una "caja" cuya tapa es el
relieve escaneado y Poisson devuelve directamente un sólido cerrado.
"""
import numpy as np
from scipy import ndimage

import relief


def _need_o3d():
    try:
        import open3d as o3d
        o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
        return o3d
    except ImportError as e:
        raise RuntimeError('El modo 3D necesita Open3D (pip install open3d). Ejecuta el programa con '
                           '.venv/bin/python o instala Open3D.') from e


def reconstruct3d(hm, prm, log=lambda s: None):
    o3d = _need_o3d()
    if prm.negative:
        log('AVISO: el modo molde no se aplica en modo 3D (un molde con socavados no se puede desmoldar)')
    if prm.frame > 0:
        log('AVISO: el marco no se aplica en modo 3D')
    res, grid, mask = hm.res, hm.grid, hm.mask.copy()
    nv, nu = mask.shape
    floor = hm.floor
    s = relief.piece_scale(prm, (max(nu, nv) * res))
    zb = floor - prm.base / s                      # plano trasero (unidades de la nube)
    relief.warn_thin_base(prm, max(nu, nv) * res, log)

    # ---------------- puntos reales dentro de la pieza y por delante del fondo
    X = hm.U - hm.u0
    Y = hm.V - hm.v0
    Z = hm.H
    c = np.floor(X / res).astype(np.int64)
    r = np.floor(Y / res).astype(np.int64)
    ok = (c >= 0) & (c < nu) & (r >= 0) & (r < nv)
    ok[ok] = mask[r[ok], c[ok]]
    ok &= Z >= floor - 0.5 * res
    if prm.despike > 0:      # puntos que sobresalen claramente del frente del mapa de alturas
        ok[ok] &= Z[ok] <= grid[r[ok], c[ok]] + prm.despike
    P = np.c_[X[ok], Y[ok], np.maximum(Z[ok], floor)].astype(np.float64)
    P[:, 2] = floor + (P[:, 2] - floor) * prm.z_scale
    col = hm.col[ok] / 255.0 if hm.col is not None else None
    log(f'Modo 3D: {len(P):,} puntos de la cara frontal')

    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P))
    if col is not None:
        pc.colors = o3d.utility.Vector3dVector(col.astype(np.float64))
    pc = pc.voxel_down_sample(res)
    pc, _ = pc.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.5)
    log(f'Submuestreo a {res:g} y limpieza: {len(pc.points):,} puntos')
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=3 * res, max_nn=30))
    # todo lo escaneado se vio desde delante: las normales deben apuntar hacia +Z (hacia el escáner)
    pc.orient_normals_to_align_with_direction(np.array([0.0, 0.0, 1.0]))

    # ---------------- puntos sintéticos: trasera, paredes y (si es rectángulo) suelo exterior
    top = floor + (np.where(mask, grid, floor) - floor) * prm.z_scale     # altura del frente por celda
    if prm.shape == 'rectangulo':
        outside = ~mask
        mask = np.ones_like(mask)
    else:
        outside = np.zeros_like(mask)
    syn_p, syn_n = [], []
    step = 3                                                  # la trasera es plana: basta 1 punto cada 3 celdas
    rr, cc = np.nonzero(mask[::step, ::step])
    rr, cc = rr * step, cc * step
    syn_p.append(np.c_[(cc + .5) * res, (rr + .5) * res, np.full(len(rr), zb)])
    syn_n.append(np.tile([0, 0, -1.0], (len(rr), 1)))
    if outside.any():                                          # suelo plano fuera del contorno (placa rectangular)
        rr, cc = np.nonzero(outside)
        syn_p.append(np.c_[(cc + .5) * res, (rr + .5) * res, np.full(len(rr), floor)])
        syn_n.append(np.tile([0, 0, 1.0], (len(rr), 1)))
    mp = np.pad(mask, 1)
    rr, cc = np.nonzero(mask)
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        b = ~mp[rr + 1 + dr, cc + 1 + dc]
        br, bc = rr[b], cc[b]
        x = (bc + .5 + .5 * dc) * res
        y = (br + .5 + .5 * dr) * res
        ztop = top[br, bc]
        nz = np.maximum(1, np.ceil((ztop - zb) / res).astype(int))
        k = np.repeat(np.arange(len(br)), nz)
        frac = (np.arange(nz.sum()) - np.repeat(np.cumsum(nz) - nz, nz)) / np.repeat(nz, nz)
        syn_p.append(np.c_[x[k], y[k], zb + frac * (ztop[k] - zb)])
        syn_n.append(np.tile([dc, dr, 0.0], (len(k), 1)))
    SP, SN = np.concatenate(syn_p), np.concatenate(syn_n)
    syn = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(SP))
    syn.normals = o3d.utility.Vector3dVector(SN)
    if col is not None:
        syn.colors = o3d.utility.Vector3dVector(np.full((len(SP), 3), 0.55))
    allpc = pc + syn
    log(f'Puntos sintéticos (trasera y paredes): {len(SP):,}')

    # ---------------- Poisson
    ext = np.ptp(np.asarray(allpc.points), 0).max()
    depth = prm.poisson_depth or int(np.clip(np.ceil(np.log2(ext * 1.1 / (1.25 * res))), 6, 11))
    if depth >= 11:
        log(f'AVISO: profundidad {depth} con {len(allpc.points):,} puntos puede necesitar 6–10 GB de memoria y '
            f'varios minutos. Si falta memoria, aumenta la resolución o fija una profundidad menor.')
    # estimación (medida en los ejemplos: Poisson da ~3,7 triángulos por punto de entrada)
    est_t = int(3.7 * len(allpc.points))
    msg = f'Estimación: ≈ {est_t:,} triángulos → {relief.sizes_text(est_t // 2, est_t, col is not None)}'
    if 0 < prm.simplify < 100:
        f = prm.simplify / 100
        msg += f'; tras simplificar al {prm.simplify:g} % ≈ {relief.sizes_text(int(est_t * f) // 2, int(est_t * f), col is not None)}'
    log(msg)
    log(f'Poisson apantallado, profundidad {depth} (celda ≈ {ext * 1.1 / 2 ** depth:.2f}), {relief.threads_for(prm, "poisson")} hilos…')
    try:
        verts, tris, vcol = _poisson_meshlab(allpc, depth, relief.threads_for(prm, "poisson"))
    except ImportError:
        verts, tris, vcol = _poisson_open3d(o3d, allpc, depth)
    del allpc
    bad = relief._bad_edges(tris, len(verts))
    if bad:
        verts, tris, vcol, bad = _repair(verts, tris, vcol, log)

    # ---------------- a coordenadas de la pieza: base plana en z=0 y escala
    z = verts[:, 2] - zb
    z[z < 0.5 * res] = 0.0                                # aplanar la base para que apoye bien en la cama
    verts[:, 2] = z
    verts *= s
    vol = relief._volume(verts, tris)
    if vol < 0:                                           # normales hacia dentro: invertir orientación
        tris = tris[:, ::-1].copy()
        vol = -vol
    log(f'{len(tris):,} triángulos, aristas no-manifold: {bad}, volumen {relief.fmt_vol(vol)}')
    out = np.where(mask, (np.where(mask & ~outside, top, floor) - zb) * s, np.nan)
    return relief.Result(verts, tris, vcol, out, res * s, s, hm.ang, bad, vol, mode='3d',
                         xform=relief.grid_to_piece(hm, s, prm.z_scale, (0, 0, prm.base), floor),
                         zclip=(prm.base - 0.5 * res * s, np.inf))


def _poisson_meshlab(pc, depth, threads=relief.NCORES):
    """Poisson de Kazhdan (vía PyMeshLab): da mallas cerradas y 2-variedad, y transfiere el color."""
    import pymeshlab
    P, N = np.asarray(pc.points), np.asarray(pc.normals)
    kw = {}
    if pc.has_colors():
        kw['v_color_matrix'] = np.c_[np.asarray(pc.colors), np.ones(len(P))]
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=P, v_normals_matrix=N, **kw))
    ms.generate_surface_reconstruction_screened_poisson(depth=depth, scale=1.1, samplespernode=1.5,
                                                         pointweight=4, preclean=False, threads=threads)
    nf = ms.current_mesh().face_number()
    ms.meshing_remove_connected_component_by_face_number(mincomponentsize=max(10, nf // 100))
    ms.meshing_remove_unreferenced_vertices()
    m = ms.current_mesh()
    vcol = None
    if pc.has_colors() and m.has_vertex_color():
        vcol = (m.vertex_color_matrix()[:, :3] * 255).round().clip(0, 255).astype(np.uint8)
    return m.vertex_matrix().copy(), m.face_matrix().astype(np.int64), vcol


def _poisson_open3d(o3d, pc, depth):
    mesh, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pc, depth=depth, scale=1.1, linear_fit=True)
    tri_cl, n_cl, _ = mesh.cluster_connected_triangles()
    tri_cl = np.asarray(tri_cl)
    if len(n_cl) > 1:
        mesh.remove_triangles_by_mask(tri_cl != int(np.argmax(np.asarray(n_cl))))
        mesh.remove_unreferenced_vertices()
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_duplicated_vertices()
    vcol = (np.asarray(mesh.vertex_colors) * 255).clip(0, 255).astype(np.uint8) if mesh.has_vertex_colors() else None
    return np.asarray(mesh.vertices).copy(), np.asarray(mesh.triangles).astype(np.int64), vcol


def _repair(verts, tris, vcol, log):
    try:
        import pymeshlab
    except ImportError:
        log('AVISO: la malla de Poisson tiene aristas abiertas y pymeshlab no está instalado para repararla')
        return verts, tris, vcol, relief._bad_edges(tris, len(verts))
    ms = pymeshlab.MeshSet()
    kw = {'v_color_matrix': np.c_[vcol / 255.0, np.ones(len(vcol))]} if vcol is not None else {}
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=verts, face_matrix=tris.astype(np.int32), **kw))
    ms.meshing_repair_non_manifold_edges(method='Remove Faces')
    ms.meshing_repair_non_manifold_vertices()
    if relief._bad_edges(ms.current_mesh().face_matrix().astype(np.int64), ms.current_mesh().vertex_number()):
        ms.meshing_close_holes(maxholesize=1000)
    ms.meshing_remove_unreferenced_vertices()
    m = ms.current_mesh()
    verts, tris = m.vertex_matrix(), m.face_matrix().astype(np.int64)
    vcol = (m.vertex_color_matrix()[:, :3] * 255).round().astype(np.uint8) if vcol is not None else None
    bad = relief._bad_edges(tris, len(verts))
    log(f'Reparación de la malla: quedan {bad} aristas abiertas')
    return verts, tris, vcol, bad
