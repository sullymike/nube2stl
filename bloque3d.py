"""Modo «bloque» de nube2stl: la cara del relieve y los laterales del bloque que se escanearon.

Es el término medio entre los modos de relieve (solo la cara: una altura por celda, los laterales se pierden) y el
modo objeto (todo cerrado y escaneado por todos los lados). Un escaneo de una cara de un sillar suele llevar
también parte de sus laterales: unos centímetros o decímetros de las caras vecinas, a veces solo de algunos
lados. Aquí esos puntos se conservan como superficie real.

1. La etapa del mapa de alturas (heightmap) da el encuadre: plano de la cara, giro, espejo, silueta y la altura
   del frente en cada celda. La «Profundidad máx.» fija hasta dónde llega el bloque por detrás.
2. Se toman todos los puntos reales dentro de la silueta (cara y laterales), sin quedarse con uno por celda.
3. Se cierra la pieza con puntos sintéticos: una trasera plana a la profundidad del bloque y paredes verticales
   siguiendo la silueta, pero solo donde no hay lateral escaneado cerca.
4. Las normales de los puntos reales se orientan hacia fuera como en el modo objeto (propagación y visibilidad),
   con la trasera y las paredes sintéticas tapando los rayos para que la pieza abierta se comporte como cerrada.
5. Poisson apantallado da un sólido cerrado; se aplana la trasera, que es la cara que apoya en la cama.
"""
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

import objeto3d
import recon3d
import relief


def reconstruct_block(hm, prm, log=lambda s: None, mesh=False):
    o3d = recon3d._need_o3d()
    for name, why in (('negative', 'el molde'), ('frame', 'el marco')):
        if getattr(prm, name):
            log(f'AVISO: {why} no se aplica en modo bloque')
    if prm.z_scale != 1:
        log('AVISO: la exageración del relieve no se aplica en modo bloque (deformaría los laterales)')
    res, grid, mask = hm.res, hm.grid, hm.mask.copy()
    nv, nu = mask.shape

    # ---------------- puntos reales: cara y laterales, dentro de la silueta
    X, Y, Z = hm.U - hm.u0, hm.V - hm.v0, hm.H
    c = np.floor(X / res).astype(np.int64)
    r = np.floor(Y / res).astype(np.int64)
    near = ndimage.binary_dilation(mask, iterations=3)       # los laterales caen justo en el borde de la silueta
    ok = (c >= 0) & (c < nu) & (r >= 0) & (r < nv)
    ok[ok] = near[r[ok], c[ok]]
    if prm.despike > 0:      # puntos que sobresalen claramente del frente del mapa de alturas
        rr, cc = np.clip(r[ok], 0, nv - 1), np.clip(c[ok], 0, nu - 1)
        ok[ok] &= Z[ok] <= grid[rr, cc] + prm.despike
    zb = float(np.percentile(Z[ok], 0.05))                    # trasera: lo más hondo que llega el escaneo
    front = float(np.percentile(grid[mask], 50))
    P = np.c_[X[ok], Y[ok], Z[ok]].astype(np.float64)
    col = hm.col[ok] / 255.0 if hm.col is not None else None
    nside = int((Z[ok] < hm.floor - 4 * res).sum())
    log(f'Modo bloque: {len(P):,} puntos; {nside:,} de los laterales (por debajo del fondo del relieve); '
        f'el bloque llega a {front - zb:.0f} por detrás de la cara (límite: Profundidad máx. {getattr(relief._project, "behind", prm.behind):.0f})')
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P))
    if col is not None:
        pc.colors = o3d.utility.Vector3dVector(col.astype(np.float64))
    del P
    pc = objeto3d.clean_cloud(pc, res, log)
    R = np.asarray(pc.points)

    # ---------------- puntos sintéticos: trasera y paredes donde falta lateral
    top = np.where(mask, grid, hm.floor)
    syn_p, syn_n = [], []
    step = 3
    rr, cc = np.nonzero(mask[::step, ::step])
    rr, cc = rr * step, cc * step
    syn_p.append(np.c_[(cc + .5) * res, (rr + .5) * res, np.full(len(rr), zb)])
    syn_n.append(np.tile([0, 0, -1.0], (len(rr), 1)))
    mp = np.pad(mask, 1)
    rr, cc = np.nonzero(mask)
    wall_p, wall_n = [], []
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        b = ~mp[rr + 1 + dr, cc + 1 + dc]
        br, bc = rr[b], cc[b]
        x = (bc + .5 + .5 * dc) * res
        y = (br + .5 + .5 * dr) * res
        ztop = top[br, bc]
        nz = np.maximum(1, np.ceil((ztop - zb) / res).astype(int))
        k = np.repeat(np.arange(len(br)), nz)
        frac = (np.arange(nz.sum()) - np.repeat(np.cumsum(nz) - nz, nz)) / np.repeat(nz, nz)
        wall_p.append(np.c_[x[k], y[k], zb + frac * (ztop[k] - zb)])
        wall_n.append(np.tile([dc, dr, 0.0], (len(k), 1)))
    WP, WN = np.concatenate(wall_p), np.concatenate(wall_n)
    d, _ = cKDTree(R).query(WP, workers=-1)
    gap = d > 4 * res                                         # aquí no hay lateral escaneado: pared sintética
    log(f'Laterales: {100 * (1 - gap.mean()):.0f} % del perímetro (en altura) tiene escaneo; '
        f'el resto se cierra con paredes verticales')
    syn_p.append(WP[gap])
    syn_n.append(WN[gap])
    SP, SN = np.concatenate(syn_p), np.concatenate(syn_n)
    # los puntos reales por debajo de la trasera o pegados a ella sobran (la trasera los sustituye)
    keep = np.flatnonzero(R[:, 2] > zb + 1.5 * res)
    pc = pc.select_by_index(keep)

    # ---------------- normales de los puntos reales, con la caja sintética tapando los rayos
    log('Normales: estimando y orientando hacia fuera…')
    objeto3d._orient_normals(o3d, pc, res, log, extra=SP)
    if mesh and prm.denoise > 0:
        log('Alisado del ruido: no se aplica, los puntos salen de una malla (no tienen ruido de medida que alisar)')
    else:
        objeto3d.denoise_points(pc, res, hm.res_scan, prm.denoise, log)
    syn = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(SP))
    syn.normals = o3d.utility.Vector3dVector(SN)
    if col is not None:
        syn.colors = o3d.utility.Vector3dVector(np.full((len(SP), 3), 0.55))
    allpc = pc + syn
    log(f'Puntos sintéticos (trasera y paredes): {len(SP):,}')

    # ---------------- Poisson
    ext = np.ptp(np.asarray(allpc.points), 0).max()
    depth = prm.poisson_depth or int(np.clip(np.ceil(np.log2(ext * 1.1 / (1.25 * res))), 6, 11))
    est_t = int(3.7 * len(allpc.points))
    log(f'Estimación: ≈ {est_t:,} triángulos → {relief.sizes_text(est_t // 2, est_t, col is not None)}')
    log(f'Poisson apantallado, profundidad {depth} (celda ≈ {ext * 1.1 / 2 ** depth:.2f}), '
        f'{relief.threads_for(prm, "poisson")} hilos…')
    try:
        verts, tris, vcol = recon3d._poisson_meshlab(allpc, depth, relief.threads_for(prm, 'poisson'))
    except ImportError:
        verts, tris, vcol = recon3d._poisson_open3d(o3d, allpc, depth)
    del allpc
    bad = relief._bad_edges(tris, len(verts))
    if bad:
        verts, tris, vcol, bad = recon3d._repair(verts, tris, vcol, log)

    # ---------------- a la pieza: trasera plana en z = 0, escala por el lado mayor de la cara
    z = verts[:, 2] - zb
    z[z < 0.5 * res] = 0.0
    verts[:, 2] = z
    vmin = verts[:, :2].min(0)
    verts[:, :2] -= vmin
    s = relief.piece_scale(prm, float(np.ptp(verts[:, :2], 0).max()))
    verts *= s
    vol = relief._volume(verts, tris)
    if vol < 0:
        tris = tris[:, ::-1].copy()
        vol = -vol
    sx, sy, sz = np.ptp(verts, 0)
    log(f'{len(tris):,} triángulos, aristas no-manifold: {bad}, volumen {relief.fmt_vol(vol)}, '
        f'{sx:.1f} × {sy:.1f} × {sz:.1f} mm (grosor del bloque {sz:.1f} mm)')
    out = np.where(mask, (top - zb) * s, np.nan)
    return relief.Result(verts, tris, vcol, out, res * s, s, hm.ang, bad, vol, mode='bloque',
                         xform=relief.grid_to_piece(hm, s, 1.0, (-vmin[0], -vmin[1], 0), zb))
