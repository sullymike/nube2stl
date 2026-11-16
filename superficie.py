"""Superficies que no son un plano, y propuesta del tipo de escaneo al abrir una nube.

- Relieve sobre un cilindro (miliario, fuste de columna, ara, sello cilíndrico, vasija decorada): se ajusta el
  cilindro y la nube se «desenrolla». Cada punto pasa a (arco, altura a lo largo del eje, distancia al cilindro), de
  modo que la inscripción queda como una placa plana vista desde arriba y todo lo demás (mapa de alturas, 3D,
  tamaño, orientación) funciona igual que con una cara plana.
- Grabados sobre roca irregular (petroglifos, grafitos, icnitas, marcas de cantero): a las alturas de la cara se les
  resta su forma general (un suavizado muy ancho, de «detrend» mm). Queda solo el grabado, que se puede exagerar.
  Es el «modelo de relieve local» que se usa en arqueología con los modelos digitales del terreno.
- guess_kind: con la forma de la nube, qué tipo de escaneo parece (relieve, bloque, objeto, terreno, curvo,
  grabado) y por qué, para proponerlo en el selector de la ventana.
"""
import numpy as np
from scipy import ndimage, optimize


# ------------------------------------------------------------------ cilindro
def _basis(a):
    """Eje unitario y dos vectores perpendiculares (e2 = a × e1)."""
    a = np.asarray(a, np.float64)
    a = a / np.linalg.norm(a)
    t = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(a, t)
    e1 /= np.linalg.norm(e1)
    return a, e1, np.cross(a, e1)


def _circle(x, y):
    """Circunferencia por mínimos cuadrados algebraicos (Kåsa): centro y radio."""
    A = np.c_[x, y, np.ones_like(x)]
    s = np.linalg.lstsq(A, x * x + y * y, rcond=None)[0]
    cx, cy = s[0] / 2, s[1] / 2
    return cx, cy, float(np.sqrt(max(s[2] + cx * cx + cy * cy, 0.0)))


def _section(q, a):
    """Sección perpendicular al eje a: circunferencia ajustada y distancias de cada punto a ella."""
    a, e1, e2 = _basis(a)
    x, y = q @ e1, q @ e2
    cx, cy, R = _circle(x, y)
    return a, e1, e2, cx, cy, R, np.hypot(x - cx, y - cy) - R


def fit_cylinder(p, nmax=30_000):
    """Cilindro que mejor se ajusta a la nube: eje (punto y dirección), radio, separación media de los puntos
    (rms), arco que abarca la cara (grados) y ángulo de la costura (en medio del mayor tramo sin puntos).
    Se prueba a partir de los tres ejes principales de la nube y se afina la dirección (Nelder-Mead); una segunda
    pasada sin los puntos más alejados (el relieve no debe torcer el cilindro)."""
    q = np.asarray(p[:: max(1, len(p) // nmax)], np.float64)
    m = q.mean(0)
    q = q - m
    _, _, vt = np.linalg.svd(q, full_matrices=False)

    def solve(q, a0):
        a0, e1, e2 = _basis(a0)

        def cost(th):
            return float(np.mean(_section(q, a0 + th[0] * e1 + th[1] * e2)[6] ** 2))
        r = optimize.minimize(cost, [0.0, 0.0], method='Nelder-Mead',
                              options=dict(xatol=1e-5, fatol=1e-12, maxiter=300))
        return r.fun, _basis(a0 + r.x[0] * e1 + r.x[1] * e2)[0]

    best = min((solve(q, a0) for a0 in vt), key=lambda t: t[0])
    a, *_, d = _section(q, best[1])
    mad = np.median(np.abs(d - np.median(d))) * 1.4826 + 1e-9
    qi = q[np.abs(d) < 3 * mad]
    if len(qi) > 100:
        best = solve(qi, a)
    a, e1, e2, cx, cy, R, d = _section(q, best[1])
    # eje hacia arriba si la pieza está de pie; si está tumbada, su componente mayor en positivo
    if abs(a[2]) >= 0.5:
        sgn = np.sign(a[2])
    else:
        sgn = np.sign(a[np.argmax(np.abs(a))])
    if sgn < 0:
        a, e1, e2 = _basis(-a)
        cx, cy, R, d = (*_circle(q @ e1, q @ e2), None)
        d = np.hypot(q @ e1 - cx, q @ e2 - cy) - R
    c = m + cx * e1 + cy * e2
    th = np.arctan2(q @ e2 - cy, q @ e1 - cx)
    seam, arc = _seam(th)
    return {'center': c, 'axis': a, 'e1': e1, 'e2': e2, 'R': R, 'rms': float(np.sqrt(np.mean(d * d))),
            'arc': arc, 'seam': seam}


def _seam(th, bins=720):
    """Ángulo en medio del mayor tramo vacío (por donde se corta para desenrollar) y arco ocupado (grados)."""
    h = np.histogram(th, bins, (-np.pi, np.pi))[0] > 0
    if h.all():
        return float(np.pi), 360.0
    hh = np.r_[h, h]                           # circular: buscar el mayor tramo vacío dando la vuelta
    best, run, end = 0, 0, 0
    for i, v in enumerate(hh):
        run = 0 if v else run + 1
        if run > best:
            best, end = run, i
    best = min(best, bins)
    mid = (end - best / 2 + 0.5) % bins
    return float(-np.pi + mid * 2 * np.pi / bins), 360.0 * (1 - best / bins)


def unroll(p, cyl=None, log=lambda s: None):
    """Nube desenrollada: x = arco (mm sobre el cilindro), y = a lo largo del eje, z = distancia al cilindro
    (hacia fuera). Mirada desde +Z (desde fuera), con el eje hacia arriba de la imagen."""
    cyl = cyl or fit_cylinder(p)
    c, a, e1, e2, R = cyl['center'], cyl['axis'], cyl['e1'], cyl['e2'], cyl['R']
    out = np.empty((len(p), 3), np.float64)
    for i in range(0, len(p), 2_000_000):     # por bloques para no multiplicar la memoria
        d = np.asarray(p[i:i + 2_000_000], np.float64) - c
        x, y = d @ e1, d @ e2
        th = (np.arctan2(y, x) - cyl['seam']) % (2 * np.pi)
        out[i:i + 2_000_000, 0] = R * (th - np.pi)
        out[i:i + 2_000_000, 1] = d @ a
        out[i:i + 2_000_000, 2] = np.hypot(x, y) - R
    log(f'Superficie cilíndrica: radio {R:.0f} mm (Ø {2 * R:.0f}), la cara abarca {cyl["arc"]:.0f}° '
        f'({R * np.radians(cyl["arc"]):.0f} mm desenrollados), separación media del cilindro {cyl["rms"]:.1f} mm')
    L = float(np.ptp(out[:, 1]))
    if R > 4 * max(L, R * np.radians(cyl['arc'])):
        log('AVISO: la cara es casi plana (el cilindro sale con un radio muy grande); con «Relieve» sale igual')
    if cyl['arc'] > 330:
        log('La pieza está escaneada casi entera alrededor: se desenrolla cortando por el tramo con menos puntos '
            '(gira la pieza en Orientar si la costura cae en mal sitio)')
    return out.astype(p.dtype if p.dtype in (np.float32, np.float64) else np.float64)


def unroll_surface(p, surface, log=lambda s: None):
    """Desenrollar según la superficie elegida (Params.surface)."""
    if surface == 'cono':
        import revolucion
        return revolucion.unroll_cone(p, log=log)
    if surface == 'revolucion':
        import revolucion
        return revolucion.unroll_revolution(p, log=log)
    return unroll(p, log=log)


# ------------------------------------------------------------------ forma general (grabados)
_MONO = [(0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2)]      # h ≈ c0 + c1·x + c2·y + c3·x² + c4·xy + c5·y²


def low_surface(grid, mask, sigma):
    """Forma general de las alturas: en cada celda, la superficie cuadrática que mejor se ajusta a su entorno con
    pesos gaussianos (sigma en celdas), usando solo las celdas de la pieza. Con una cuadrática local, y no una
    media, ni el borde de una roca en pendiente ni una joroba dejan rastro; lo que queda son los detalles más
    estrechos que sigma (el grabado). Se calcula en una rejilla más gruesa y se interpola, para que una sigma de
    cientos de celdas siga costando poco."""
    k = max(1, int(sigma / 6))
    w = mask.astype(np.float64)
    g = np.where(mask, grid, 0.0).astype(np.float64)
    nv, nu = grid.shape
    if k > 1:
        pv, pu = (-nv) % k, (-nu) % k
        gw = np.pad(g * w, ((0, pv), (0, pu))).reshape((nv + pv) // k, k, (nu + pu) // k, k).sum((1, 3))
        w = np.pad(w, ((0, pv), (0, pu))).reshape((nv + pv) // k, k, (nu + pu) // k, k).sum((1, 3)) / (k * k)
        g = gw / np.maximum(w * k * k, 1e-9)
    s = sigma / k
    r = int(3 * s) + 1
    t = np.arange(-r, r + 1) / s                    # desplazamientos en sigmas: sistema bien condicionado
    ker = [np.exp(-t * t / 2) * t ** a for a in range(5)]

    def C(img, a, b):          # Σ img(vecino) · dx^a · dy^b · gauss
        return ndimage.correlate1d(ndimage.correlate1d(img, ker[a], 1, mode='constant'), ker[b], 0,
                                   mode='constant')
    mom = {(a, b): C(w, a, b) for a in range(5) for b in range(5) if a + b <= 4}
    wg = w * g
    rhs = np.stack([C(wg, a, b) for a, b in _MONO], -1)
    A = np.empty(g.shape + (6, 6))
    for i, (a1, b1) in enumerate(_MONO):
        for j, (a2, b2) in enumerate(_MONO):
            A[..., i, j] = mom[(a1 + a2, b1 + b2)]
    s0 = mom[(0, 0)]
    ridge = np.array([1e-9, 1e-3, 1e-3, 1e-2, 1e-2, 1e-2])       # sin datos alrededor: hacia una media
    A[..., range(6), range(6)] += ridge * (s0[..., None] + 1e-6)
    low = np.linalg.solve(A, rhs[..., None])[..., 0, 0]
    if k > 1:                                  # de vuelta a la rejilla fina (centros de los bloques)
        yy = (np.arange(nv) + 0.5) / k - 0.5
        xx = (np.arange(nu) + 0.5) / k - 0.5
        YY, XX = np.meshgrid(yy, xx, indexing='ij')
        low = ndimage.map_coordinates(low, [YY, XX], order=1, mode='nearest')
    return low


def detrend_sigma(size, res):
    """Sigma (celdas) del suavizado que quita las formas de más de «size» mm (res: mm por celda)."""
    return max(size / res / 3.0, 1.0)


def detrend_default(length):
    """Tamaño razonable de la forma que se quita: una cuarta parte del lado mayor, redondeado."""
    v = max(length / 4.0, 10.0)
    e = 10 ** np.floor(np.log10(v))
    return float(min((1, 2, 2.5, 5, 10), key=lambda f: abs(f * e - v)) * e)


# ------------------------------------------------------------------ qué parece la nube
def _grid_layers(q, ncell=60):
    """Rejilla gruesa sobre los dos ejes mayores: por celda, altura media y espesor (rango en el tercer eje)."""
    u, v, w = q[:, 0], q[:, 1], q[:, 2]
    L = max(np.ptp(u), np.ptp(v))
    r = L / ncell
    iu = ((u - u.min()) / r).astype(np.int64)
    iv = ((v - v.min()) / r).astype(np.int64)
    nu, nv = iu.max() + 1, iv.max() + 1
    cell = iv * nu + iu
    lo = np.full(nu * nv, np.inf)
    hi = np.full(nu * nv, -np.inf)
    np.minimum.at(lo, cell, w)
    np.maximum.at(hi, cell, w)
    cnt = np.bincount(cell, minlength=nu * nv)
    mean = np.bincount(cell, w, minlength=nu * nv) / np.maximum(cnt, 1)
    occ = cnt >= 3
    return (mean.reshape(nv, nu), (hi - lo).reshape(nv, nu), occ.reshape(nv, nu), r)


def guess_kind(p, nmax=150_000, factor=1.0):
    """Tipo de escaneo que parece la nube (mm): (clave de recetas.KINDS, motivo para enseñar, datos).
    factor: multiplica las medidas (1000 si las unidades se han supuesto en mm pero parecen metros).
    Es una propuesta: la ventana la enseña y deja elegir otra."""
    q = np.asarray(p[:: max(1, len(p) // nmax)], np.float64) * factor
    if len(q) < 500:
        return 'relieve', 'hay pocos puntos para saberlo', {}
    m = q.mean(0)
    q = q - m
    _, _, vt = np.linalg.svd(q, full_matrices=False)
    Q = q @ vt.T
    ext = np.percentile(Q, 99, 0) - np.percentile(Q, 1, 0)
    L = float(ext[0])
    info = {'L': L, 'ext': [float(e) for e in ext]}
    if L > 6000:
        return 'terreno', f'mide {L / 1000:.0f} m: parece un terreno, una ruina o un edificio', info
    mean, thick, occ, r = _grid_layers(Q)
    t = thick[occ]
    two = float(np.mean(t > 0.35 * ext[2])) if ext[2] > 0 else 0.0     # celdas con cara delante y detrás
    wall = float(np.mean(t > max(4 * r, 0.06 * L)))                    # celdas con una pared casi de canto
    cyl = fit_cylinder(q, 15_000)
    plane_rms = float(np.sqrt(np.mean(Q[:, 2] ** 2)))
    info.update(two=two, wall=wall, R=cyl['R'], arc=cyl['arc'], cyl_rms=cyl['rms'], plane_rms=plane_rms)
    good_cyl = cyl['rms'] < 0.025 * cyl['R'] and cyl['R'] < 1.5 * L
    if (two > 0.45 or ext[2] > 0.45 * L) and L > 1500:
        return ('interior', f'es una superficie cerrada de {L / 1000:.1f} m: parece un interior (sala, cueva, '
                            f'cisterna) escaneado desde dentro; si es un objeto grande escaneado por fuera, elige '
                            f'«Objeto»', info)
    try:                                          # dos caras grandes en ángulo: la esquina de un bloque
        import esquina
        tf = esquina.two_faces(q, nmax=len(q))
    except Exception:  # noqa: BLE001  la propuesta nunca debe fallar
        tf = None
    if tf is not None and two < 0.45:
        info['corner'] = tf['angle']
        return ('bloque', f'tiene dos caras grandes a {tf["angle"]:.0f}°, unidas por una arista: parece la esquina de '
                          f'un bloque. Con «Esquina (dos caras)» cada cara se hace con su propio mapa de alturas',
                info)
    if two > 0.45 or ext[2] > 0.45 * L:
        if good_cyl and cyl['arc'] > 300 and cyl['rms'] < 0.012 * cyl['R']:
            return ('curvo', f'es casi un cilindro de Ø {2 * cyl["R"]:.0f} mm escaneado alrededor (columna, '
                             f'miliario, ara): se puede desenrollar', info)
        return 'objeto', 'está escaneado por delante y por detrás: parece un objeto completo', info
    if (good_cyl and 35 < cyl['arc'] <= 300 and cyl['rms'] < 0.4 * plane_rms
            and plane_rms > 0.02 * L):
        return ('curvo', f'la cara es curva, como un cilindro de Ø {2 * cyl["R"]:.0f} mm (columna, miliario, '
                         f'ara): se puede desenrollar', info)
    if plane_rms > 0.03 * L and not good_cyl:     # curva, pero no un cilindro: ¿una pieza a torno?
        import revolucion
        try:
            ax = revolucion.fit_axis(q, nmax=20_000)
            _, rr, _ = revolucion.cyl_coords(q, ax)
            Rm = float(np.median(rr))
            info.update(rev_rms=ax['rms'], rev_arc=ax['arc'], rev_R=Rm)
            if ax['rms'] < 0.03 * L and 20 < ax['arc'] <= 300 and Rm < 3 * L:
                if np.median(t) > 2 * r:       # dos paredes (dentro y fuera) con grosor: un fragmento
                    return ('fragmento', f'es un trozo de una pieza a torno de Ø {2 * Rm:.0f} mm con su grosor: '
                                         f'parece un fragmento de vasija; se puede reconstruir entera', info)
                info['surface'] = 'revolucion'
                return ('curvo', f'la cara es curva, de una pieza a torno de Ø ≈ {2 * Rm:.0f} mm: se puede '
                                 f'desenrollar con su perfil (superficie «revolución»)', info)
        except Exception:  # noqa: BLE001  la propuesta nunca debe fallar
            pass
    if wall > 0.03:
        return 'bloque', 'la cara trae paredes de canto alrededor: parece un bloque con sus laterales', info
    # forma general frente a detalle: la roca es una forma ancha y suave; un relieve tiene el detalle a media escala
    g = np.where(occ, mean, np.nan)
    g = np.where(occ, g, np.nanmean(g))
    low = low_surface(g, occ, g.shape[1] / 10)
    hp = (g - low)[occ]
    lw = low[occ]
    rng_low = float(np.percentile(lw, 98) - np.percentile(lw, 2))
    rng_hp = float(np.percentile(hp, 98) - np.percentile(hp, 2))
    info.update(low=rng_low, detail=rng_hp)
    if rng_low > 0.06 * L and rng_low > 4 * rng_hp:
        return ('grabado', 'la superficie es irregular y el detalle es poco profundo: parecen grabados sobre una '
                           'roca', info)
    if abs(vt[2][2]) > 0.8 and L > 2500:
        return 'terreno', f'es una superficie casi horizontal de {L / 1000:.1f} m: parece un terreno', info
    return 'relieve', 'es una cara casi plana: parece un relieve o una inscripción', info
