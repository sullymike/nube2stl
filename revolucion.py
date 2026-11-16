"""Superficies de revolución: vasijas a torno, fustes con éntasis, piezas troncocónicas.

- fit_axis: eje de revolución de una nube. En una superficie de revolución todas las normales cortan al eje; eso es
  una condición lineal sobre el eje (un «complejo lineal de rectas», Pottmann y Wallner), que se resuelve con un
  problema de autovalores y se afina quitando los puntos que no la cumplen (roturas, asas, relieve).
- profile: el perfil, radio frente a altura a lo largo del eje.
- unroll_cone / unroll_revolution: la decoración de una pieza troncocónica o abombada como placa plana. El cono es
  desarrollable y se extiende sin deformar (un sector de corona); una superficie de revolución cualquiera no lo es,
  y se «desenrolla» como en los dibujos de vasijas (rollout): cada altura con su propia circunferencia, y la altura a
  lo largo del perfil.
- vessel: de un fragmento de cerámica a torno, la vasija entera (la parte de alturas que cubre el fragmento):
  la sección del fragmento en el plano (altura, radio) se gira 360° alrededor del eje.
- profile_svg: el perfil para dibujo arqueológico (sección en negro a un lado del eje, silueta al otro).
"""
import numpy as np
from scipy import ndimage


# ------------------------------------------------------------------ eje
def _sample(p, nmax):
    return np.asarray(p[:: max(1, len(p) // nmax)], np.float64)


def normals(q, k=20):
    import open3d as o3d
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(q))
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(k))
    return np.asarray(pc.normals)


def _complex(P, N, w):
    """Eje (dirección a, punto x0) cuyo complejo lineal mejor cumplen las rectas normales (p, n), con pesos w:
    a·(p × n) + (x0 × a)·n = 0. -> (a, x0, residuos)"""
    J = np.c_[np.cross(P, N), N] * np.sqrt(w)[:, None]
    M = J.T @ J
    A, B, C = M[:3, :3], M[:3, 3:], M[3:, 3:]
    Ci = np.linalg.pinv(C)
    S = A - B @ Ci @ B.T
    ev, V = np.linalg.eigh(S)
    a = V[:, 0]
    cbar = -Ci @ B.T @ a
    x0 = np.cross(a, cbar)                       # punto del eje más cercano al origen
    res = np.cross(P, N) @ a + N @ cbar
    return a, x0, res


def fit_axis(p, nmax=60_000, nrm=None, log=lambda s: None):
    """Eje de revolución de la nube. -> dict(point, axis, e1, e2, rms (mm, separación al perfil), arc (grados que
    abarca alrededor), seam (ángulo de la costura))."""
    q = _sample(p, nmax)
    m = q.mean(0)
    q = q - m
    N = _sample(nrm, nmax) if nrm is not None and len(nrm) == len(p) else normals(q)
    w = np.ones(len(q))
    for _ in range(6):                           # pesos: fuera lo que no gira (rotura, asa, relieve)
        a, x0, res = _complex(q, N, w)
        r = np.abs(res)
        s = 1.4826 * np.median(r) + 1e-12
        w = 1.0 / (1.0 + (r / (2.0 * s)) ** 2)
    a = a / np.linalg.norm(a)
    x0 = x0 - a * np.dot(x0, a)
    ax = _frame(m + x0, a, q + m)
    t, r, th = cyl_coords(q + m, ax)
    from superficie import _seam
    ax['seam'], ax['arc'] = _seam(th)
    lo, hi = _profile_fn(t, r, q=10), _profile_fn(t, r, q=90)      # con dos paredes: cada punto, a la suya
    ax['rms'] = float(np.sqrt(np.mean(np.minimum(np.abs(r - lo(t)), np.abs(r - hi(t))) ** 2)))
    return ax


def _frame(c, a, q):
    """Ejes del sistema de la pieza: a hacia «arriba» (hacia donde la pieza es más ancha, la boca, salvo que esté
    tumbada), e1 y e2 perpendiculares."""
    from superficie import _basis
    t = (q - c) @ a
    r = np.linalg.norm((q - c) - np.outer(t, a), axis=1)
    lo, hi = np.percentile(t, [10, 90])
    wide_top = np.median(r[t > hi]) >= np.median(r[t < lo])
    if not wide_top:
        a = -a
    a, e1, e2 = _basis(a)
    return {'point': np.asarray(c, np.float64), 'axis': a, 'e1': e1, 'e2': e2}


def cyl_coords(p, ax):
    """(altura a lo largo del eje, radio, ángulo) de cada punto."""
    d = np.asarray(p, np.float64) - ax['point']
    t = d @ ax['axis']
    x, y = d @ ax['e1'], d @ ax['e2']
    return t, np.hypot(x, y), np.arctan2(y, x)


# ------------------------------------------------------------------ perfil
def _profile_fn(t, r, nb=80, q=50):
    """Perfil suave r(t): percentil q del radio en tramos de altura, suavizado; función interpolante."""
    lo, hi = np.percentile(t, [0.2, 99.8])
    nb = int(max(8, min(nb, len(t) // 50)))
    e = np.linspace(lo, hi, nb + 1)
    k = np.clip(np.digitize(t, e) - 1, 0, nb - 1)
    tc, rc = [], []
    for i in range(nb):
        s = k == i
        if s.sum() >= 5:
            tc.append(t[s].mean())
            rc.append(np.percentile(r[s], q))
    tc, rc = np.array(tc), np.array(rc)
    if len(rc) >= 5:
        rc = ndimage.gaussian_filter1d(rc, 1.0, mode='nearest')
    return lambda x: np.interp(x, tc, rc)


def profile(p, ax, nb=120):
    """Perfil medio (radio frente a altura) para informar y dibujar. -> (t, r)"""
    t, r, _ = cyl_coords(_sample(p, 400_000), ax)
    f = _profile_fn(t, r, nb)
    tt = np.linspace(*np.percentile(t, [0.2, 99.8]), nb)
    return tt, f(tt)


def fit_cone(p, ax):
    """Recta del perfil r = r0 + k·t (cono); con k ≈ 0 es un cilindro. -> (r0, k, rms)"""
    t, r, _ = cyl_coords(_sample(p, 200_000), ax)
    w = np.ones_like(t)
    for _ in range(5):
        A = np.c_[np.ones_like(t), t] * np.sqrt(w)[:, None]
        r0, k = np.linalg.lstsq(A, r * np.sqrt(w), rcond=None)[0]
        d = r - (r0 + k * t)
        s = 1.4826 * np.median(np.abs(d)) + 1e-9
        w = 1.0 / (1.0 + (d / (2.5 * s)) ** 2)
    return float(r0), float(k), float(np.sqrt(np.mean(d ** 2)))


# ------------------------------------------------------------------ desenrollar
def _theta(th, seam):
    return ((th - seam) % (2 * np.pi)) - np.pi        # −π…π, con la costura en los extremos


def unroll_cone(p, ax=None, log=lambda s: None):
    """Desarrollo del cono (sin deformar): la cara queda como un sector de corona. x, y en el plano, z = distancia
    a la superficie del cono hacia fuera."""
    ax = ax or fit_axis(p, log=log)
    r0, k, rms = fit_cone(p, ax)
    out = np.empty((len(p), 3), np.float64)
    if abs(k) < 0.02:                                 # casi cilindro: desarrollo del cilindro
        R = r0 + k * np.median(cyl_coords(_sample(p, 100_000), ax)[0])
        for i in range(0, len(p), 2_000_000):
            t, r, th = cyl_coords(p[i:i + 2_000_000], ax)
            out[i:i + 2_000_000] = np.c_[R * _theta(th, ax['seam']), t, r - (r0 + k * t)]
        log(f'Superficie cónica casi cilíndrica (pendiente {k:.3f}): se desenrolla como un cilindro de radio {R:.0f}')
        return out.astype(np.float32)
    s = np.sqrt(1 + k * k)
    sina, cosa = abs(k) / s, 1 / s
    ta = -r0 / k                                      # vértice del cono, sobre el eje
    for i in range(0, len(p), 2_000_000):
        t, r, th = cyl_coords(p[i:i + 2_000_000], ax)
        rho = np.abs(t - ta) * s                      # distancia al vértice a lo largo de la generatriz
        phi = _theta(th, ax['seam']) * sina
        y = rho * np.cos(phi)
        out[i:i + 2_000_000, 0] = rho * np.sin(phi)
        out[i:i + 2_000_000, 1] = (y - np.median(y)) * (1 if k < 0 else -1)   # la boca (lo ancho) arriba
        out[i:i + 2_000_000, 2] = (r - (r0 + k * t)) * cosa
    log(f'Superficie cónica: semiángulo {np.degrees(np.arcsin(sina)):.1f}°, la cara abarca {ax["arc"]:.0f}°; se '
        f'desarrolla sin deformar (queda como un sector de corona). Separación media del cono {rms:.1f} mm')
    return out.astype(np.float32)


def unroll_revolution(p, ax=None, log=lambda s: None):
    """Desenrollado de una superficie de revolución con su perfil (rollout): x = arco a la altura de cada punto
    (con el radio del perfil allí), y = longitud a lo largo del perfil, z = distancia al perfil hacia fuera. Las
    medidas a lo largo de cada circunferencia y del perfil son las reales; en las piezas muy abombadas la
    decoración se estira algo en horizontal lejos de la línea media."""
    ax = ax or fit_axis(p, log=log)
    t0, r0 = profile(p, ax, nb=200)
    dt, dr = np.diff(t0), np.diff(r0)
    s0 = np.r_[0, np.cumsum(np.hypot(dt, dr))]       # longitud a lo largo del perfil
    slope = np.gradient(r0, t0)
    out = np.empty((len(p), 3), np.float64)
    for i in range(0, len(p), 2_000_000):
        t, r, th = cyl_coords(p[i:i + 2_000_000], ax)
        rp = np.interp(t, t0, r0)
        sl = np.interp(t, t0, slope)
        out[i:i + 2_000_000, 0] = rp * _theta(th, ax['seam'])
        out[i:i + 2_000_000, 1] = np.interp(t, t0, s0)
        out[i:i + 2_000_000, 2] = (r - rp) / np.sqrt(1 + sl * sl)
    steep = np.abs(slope).max()
    log(f'Superficie de revolución: Ø de {2 * r0.min():.0f} a {2 * r0.max():.0f} mm a lo largo de {s0[-1]:.0f} mm '
        f'de perfil, la cara abarca {ax["arc"]:.0f}°; separación media del perfil {ax["rms"]:.1f} mm')
    if steep > 3:
        log('AVISO: el perfil tiene tramos casi horizontales (un hombro o un fondo plano): ahí el desenrollado se '
            'deforma; recorta en la vista de la nube la banda decorada')
    if ax['arc'] < 40:
        log(f'AVISO: la cara abarca solo {ax["arc"]:.0f}° alrededor del eje: el eje es poco fiable')
    return out.astype(np.float32)


# ------------------------------------------------------------------ vasija entera
def section(p, ax, res=None):
    """Sección del fragmento en el plano (altura, radio): imagen binaria con la pared (y el fondo, si está), en
    celdas de res mm. -> dict(mask (filas = altura de abajo arriba, columnas = radio desde el eje), res, t0)"""
    t, r, _ = cyl_coords(p, ax)
    if res is None:
        from scipy.spatial import cKDTree
        q = _sample(p, 20_000)
        d, _ = cKDTree(q).query(q, k=2)
        # en una superficie, la separación de una muestra de n puntos crece como 1/√n
        res = float(max(np.median(d[:, 1]) * np.sqrt(len(q) / max(len(p), 1)) * 2.5, 0.2))
    t0, rmax = float(t.min()), float(r.max())
    nt = int((t.max() - t0) / res) + 4
    nr = int(rmax / res) + 4
    img = np.zeros((nt, 2 * nr), bool)                # con el reflejo (radio negativo): el eje no es un borde
    it = ((t - t0) / res).astype(np.int64) + 1
    ir = (r / res).astype(np.int64)
    img[it, nr + ir] = True
    img[it, nr - 1 - ir] = True
    st = ndimage.generate_binary_structure(2, 1)
    img = ndimage.binary_closing(img, st, iterations=3)
    img = ndimage.binary_fill_holes(img)
    half = img[:, nr:]                                # la pieza mayor del lado positivo, y su reflejo
    lab, k = ndimage.label(half)
    if k > 1:
        half = lab == (np.argmax(ndimage.sum(half, lab, range(1, k + 1))) + 1)
    img = np.concatenate([half[:, ::-1], half], axis=1)
    return {'mask': img, 'res': res, 't0': t0 - res, 'nr': nr}


def vessel(p, ax=None, rgb=None, log=lambda s: None):
    """La vasija entera (en las alturas que cubre el fragmento): la sección girada 360°. Puntos y normales en el
    sistema de la pieza (z = eje hacia arriba, la boca arriba), listos para el método objeto. -> (xyz, nrm, info)"""
    ax = ax or fit_axis(p, log=log)
    sec = section(p, ax)
    img, res, nr = sec['mask'], sec['res'], sec['nr']
    sm = ndimage.gaussian_filter(img.astype(np.float64), 1.5)
    gt, gr = np.gradient(sm)                          # hacia dentro de la pieza
    er = ndimage.binary_erosion(img)
    edge = img & ~er
    it, ic = np.nonzero(edge[:, nr:])                 # solo el lado del radio positivo
    ic = ic + nr
    T = it * res
    R = (ic - nr + 0.5) * res
    nt_, nr_ = -gt[it, ic], -gr[it, ic]
    ln = np.hypot(nt_, nr_) + 1e-12
    nt_, nr_ = nt_ / ln, nr_ / ln
    k = np.maximum(8, np.ceil(2 * np.pi * np.maximum(R, res) / res)).astype(np.int64)
    idx = np.repeat(np.arange(len(R)), k)
    start = np.r_[0, np.cumsum(k)[:-1]]
    j = np.arange(idx.size) - np.repeat(start, k)
    phi = 2 * np.pi * (j + 0.5 * (it[idx] % 2)) / k[idx]
    c, s = np.cos(phi), np.sin(phi)
    rows = np.nonzero(img[:, nr:].any(1))[0]
    brow = np.nonzero(img[:, nr])[0]                  # filas donde la pieza cruza el eje: el fondo
    if len(brow) and brow.mean() > rows.mean():       # el fondo arriba: la vasija estaba al revés
        T = T.max() - T
        nt_ = -nt_
        ax = dict(ax, axis=-ax['axis'], e2=-ax['e2'])
        sec = dict(sec, mask=img[::-1].copy())
        img = sec['mask']
        rows = np.nonzero(img[:, nr:].any(1))[0]
    xyz = np.c_[R[idx] * c, R[idx] * s, T[idx]].astype(np.float32)
    nrm = np.c_[nr_[idx] * c, nr_[idx] * s, nt_[idx]].astype(np.float32)
    H = img.shape[0] * res
    wall = img[:, nr:].sum(1)[rows] * res
    has_base = bool(img[rows, nr].any())              # el fondo cruza el eje
    info = {'section': sec, 'axis': ax, 'height': float(H), 'rim_d': float(2 * R.max()), 'base': has_base,
            'wall': float(np.median(wall))}
    log(f'Vasija a partir del fragmento: eje ajustado (separación media del perfil {ax["rms"]:.1f} mm, el fragmento '
        f'abarca {ax["arc"]:.0f}° alrededor), Ø máx. {2 * R.max():.0f} mm, alto {T.max() - T.min():.0f} mm, pared '
        f'≈ {info["wall"]:.1f} mm' + (', con fondo' if has_base else ', sin fondo (el fragmento no llega)') +
        f'; {len(xyz):,} puntos girados')
    if ax['arc'] < 30:
        log(f'AVISO: el fragmento abarca solo {ax["arc"]:.0f}° alrededor del eje: el eje, y con él el diámetro, son '
            f'poco fiables')
    if not has_base:
        log('El fragmento no llega al fondo: la vasija se cierra por abajo con el corte plano de la base (el fondo '
            'real se desconoce)')
    return xyz, nrm, info


def profile_svg(path, sec, title=''):
    """Perfil para dibujo arqueológico, a tamaño real (mm): a la derecha del eje la sección de la pared en negro, a
    la izquierda la silueta exterior; líneas de la boca y del fondo, y escala gráfica."""
    img, res, nr = sec['mask'], sec['res'], sec['nr']
    half = img[:, nr:]
    rows = np.nonzero(half.any(1))[0]
    H = (rows.max() - rows.min() + 1) * res
    Rm = half.shape[1] * res
    pad = 15.0
    W, Ht = 2 * Rm + 2 * pad, H + 2 * pad + 15
    y = lambda i: pad + (rows.max() - i + 0.5) * res      # noqa: E731  fila (abajo = 0) -> y del SVG (arriba = 0)
    cx = pad + Rm
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}mm" height="{Ht:.1f}mm" '
             f'viewBox="0 0 {W:.2f} {Ht:.2f}">', f'<title>{title or "Perfil"}</title>']
    # sección: tramos negros fila a fila (a la derecha del eje)
    for i in rows:
        on = np.flatnonzero(np.diff(np.r_[0, half[i].astype(np.int8), 0]))
        for a, b in zip(on[::2], on[1::2]):
            parts.append(f'<rect x="{cx + a * res:.2f}" y="{y(i) - res / 2:.2f}" width="{(b - a) * res:.2f}" '
                         f'height="{res + 0.02:.2f}" fill="black"/>')
    # silueta exterior a la izquierda
    outer = [(cx - (np.flatnonzero(half[i]).max() + 1) * res, y(i)) for i in rows]
    parts.append('<polyline fill="none" stroke="black" stroke-width="0.35" points="' +
                 ' '.join(f'{a:.2f},{b:.2f}' for a, b in outer) + '"/>')
    top, bot = y(rows.max()), y(rows.min())
    for yy, xx in ((top, outer[-1][0]), (bot, outer[0][0])):
        parts.append(f'<line x1="{xx:.2f}" y1="{yy:.2f}" x2="{cx:.2f}" y2="{yy:.2f}" stroke="black" '
                     f'stroke-width="0.25"/>')
    parts.append(f'<line x1="{cx:.2f}" y1="{top - 5:.2f}" x2="{cx:.2f}" y2="{bot + 5:.2f}" stroke="black" '
                 f'stroke-width="0.2" stroke-dasharray="3,1,0.5,1"/>')
    sb = 50.0 if Rm > 60 else 20.0                    # escala gráfica
    yb = Ht - 8
    parts.append(f'<line x1="{pad:.2f}" y1="{yb:.2f}" x2="{pad + sb:.2f}" y2="{yb:.2f}" stroke="black" '
                 f'stroke-width="0.6"/>')
    parts.append(f'<text x="{pad + sb + 2:.2f}" y="{yb + 1.2:.2f}" font-size="3.5" font-family="sans-serif">'
                 f'{sb:.0f} mm</text>')
    parts.append('</svg>')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))
