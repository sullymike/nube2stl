#!/usr/bin/env python3
"""Nube de puntos sintética de un objeto completo (escaneado por todos los lados) para probar el modo «objeto».

Es una vasija de unos 300 mm de alto, con eje vertical en Z:
- cuerpo de revolución con una banda decorada en relieve (bollones y un meandro) que forma socavados suaves;
- dos asas en arco (agujeros pasantes: la pieza no es una simple «piel» deformada de una esfera);
- boca abierta con labio y la parte alta de la pared interior (lo que un escáner ve desde arriba);
- el fondo NO está escaneado (apoyaba en la mesa), como en un escaneo real;
- ruido de medida, algunos puntos sueltos y color (terracota con la banda en negro).

Uso: python3 generar_objeto_sintetico.py [salida.ply] [--puntos N] [--eje z|y] [--normales]
     --eje y   deja el eje de la vasija en Y (como sale de muchos programas de fotogrametría)
     --normales guarda también las normales (nx, ny, nz), como hacen algunos escáneres
"""
import argparse

import numpy as np

H = 300.0          # alto (mm)
WALL = 7.0         # grosor de la pared en la boca


_PZ = [0, 12, 30, 70, 110, 150, 190, 225, 250, 268, 285, 300]
_PR = [56, 60, 64, 96, 116, 119, 108, 84, 64, 55, 57, 66]


def profile(z):
    """Radio exterior del cuerpo en función de la altura (sin decoración): pie, panza, hombro, cuello y boca."""
    from scipy.interpolate import PchipInterpolator
    return PchipInterpolator(_PZ, _PR)(np.clip(z, 0, H))


def decoration(z, th):
    """Relieve de la banda decorada (mm hacia fuera): bollones y un meandro de ondas."""
    d = np.zeros_like(z)
    band = (z > 120) & (z < 185)
    # 14 bollones hemisféricos de 11 mm de radio y 7 mm de alto, a 160 mm de altura
    ang = np.mod(th, 2 * np.pi / 14) - np.pi / 14
    R = profile(np.full_like(z, 160.0))
    dist2 = (ang * R) ** 2 + (z - 160) ** 2
    d += np.where(dist2 < 11 ** 2, 7 * np.sqrt(np.clip(1 - dist2 / 11 ** 2, 0, 1)), 0)
    # meandro: cordón ondulado de 3 mm a 132 mm de altura
    zc = 132 + 5 * np.sin(10 * th)
    d += 3 * np.exp(-((z - zc) / 2.2) ** 2)
    # dos molduras horizontales que enmarcan la banda
    d += 2.5 * (np.exp(-((z - 120) / 2.0) ** 2) + np.exp(-((z - 185) / 2.0) ** 2))
    return np.where(band | (np.abs(z - 120) < 8) | (np.abs(z - 185) < 8), d, 0)


def sample_revolution(n, rng, z0, z1, rfun, sign=1.0):
    """Puntos repartidos por área sobre una superficie de revolución r = rfun(z, θ)."""
    zz = np.linspace(z0, z1, 4000)
    r = rfun(zz, np.zeros_like(zz))
    dr = np.gradient(r, zz)
    w = r * np.sqrt(1 + dr ** 2)
    cdf = np.cumsum(w)
    cdf /= cdf[-1]
    z = np.interp(rng.random(n), cdf, zz)
    th = rng.random(n) * 2 * np.pi
    rr = rfun(z, th)
    # normal aproximada: (cos, sin, -dr/dz) para la pared exterior; invertida para la interior
    e = 0.05
    drz = (rfun(z + e, th) - rfun(z - e, th)) / (2 * e)
    drt = (rfun(z, th + 1e-3) - rfun(z, th - 1e-3)) / 2e-3
    c, s = np.cos(th), np.sin(th)
    nr = np.c_[c * rr + s * drt, s * rr - c * drt, -drz * rr]     # (∂P/∂θ × ∂P/∂z) con el signo hacia fuera
    nr /= np.linalg.norm(nr, axis=1, keepdims=True)
    P = np.c_[rr * c, rr * s, z]
    return P, sign * nr


def sample_torus_arc(n, rng, center, axis_dir, R, r, a0, a1):
    """Asa: arco de toro en el plano vertical que contiene axis_dir (horizontal) y Z."""
    u = rng.uniform(a0, a1, 4 * n)
    v = rng.uniform(0, 2 * np.pi, 4 * n)
    keep = rng.random(4 * n) < (R + r * np.cos(v)) / (R + r)     # muestreo por área
    u, v = u[keep][:n], v[keep][:n]
    ex, ez = np.asarray(axis_dir, float), np.array([0, 0, 1.0])
    ey = np.cross(ez, ex)
    ring = np.cos(u)[:, None] * ex + np.sin(u)[:, None] * ez          # dirección del eje del tubo en el arco
    nrm = np.cos(v)[:, None] * ring + np.sin(v)[:, None] * ey
    P = center + R * ring + r * nrm
    return P, nrm


def vasija(N=1_200_000, eje='z', semilla=1):
    """La vasija: (puntos, normales, color uint8), en una posición arbitraria como sale de un escáner."""
    rng = np.random.default_rng(semilla)
    outer = lambda z, th: profile(z) + decoration(z, th)                          # noqa: E731
    inner = lambda z, th: profile(z) - WALL                                       # noqa: E731
    parts = []
    # pared exterior, desde 3 mm sobre el suelo (lo que queda por debajo no se ve en el escaneo)
    parts.append(sample_revolution(int(N * 0.80), rng, 3.0, H, outer) + ('cuerpo',))
    # pared interior visible desde arriba (últimos 70 mm)
    parts.append(sample_revolution(int(N * 0.08), rng, H - 70, H, inner, sign=-1.0) + ('interior',))
    # labio: anillo superior entre la pared interior y la exterior
    n = int(N * 0.02)
    rad = np.sqrt(rng.uniform((profile(H) - WALL) ** 2, profile(H) ** 2, n))
    th = rng.random(n) * 2 * np.pi
    parts.append((np.c_[rad * np.cos(th), rad * np.sin(th), np.full(n, H)], np.tile([0, 0, 1.0], (n, 1)), 'labio'))
    # asas: arcos de toro a ambos lados, del hombro (z1) al cuello (z2), abombados hacia fuera
    z1, z2, R, rt = 172.0, 262.0, 58.0, 8.0
    A = np.array([profile(np.array(z1)) - 4, z1])
    B = np.array([profile(np.array(z2)) - 4, z2])
    M, ch = (A + B) / 2, B - A
    out = np.array([ch[1], -ch[0]]) / np.linalg.norm(ch)                 # normal de la cuerda hacia fuera (+x)
    C = M + out * np.sqrt(R ** 2 - (np.linalg.norm(ch) / 2) ** 2)
    uA = np.arctan2(*(A - C)[::-1])
    uB = np.arctan2(*(B - C)[::-1])
    if uB < uA:
        uB += 2 * np.pi
    for sgn in (1, -1):
        d = np.array([sgn, 0, 0.0])
        cen = d * C[0] + np.array([0, 0, C[1]])
        P, Nn = sample_torus_arc(int(N * 0.05), rng, cen, d, R, rt, uA, uB)
        rr = np.hypot(P[:, 0], P[:, 1])
        vis = rr > outer(P[:, 2], np.arctan2(P[:, 1], P[:, 0])) - 0.5                  # lo que queda dentro no se ve
        parts.append((P[vis], Nn[vis], 'asa'))
        # la parte del cuerpo que queda dentro del tubo del asa tampoco se ve
        Pb, Nb, kb = parts[0]
        ha, hb = Pb @ d - C[0], Pb[:, 2] - C[1]
        ey = np.cross([0, 0, 1.0], d)
        dist = np.hypot(np.hypot(ha, hb) - R, Pb @ ey)
        u = np.mod(np.arctan2(hb, ha) - uA, 2 * np.pi)
        hid = (dist < rt + 0.3) & (u <= uB - uA + 0.05)
        parts[0] = (Pb[~hid], Nb[~hid], kb)
    P = np.concatenate([p[0] for p in parts])
    Nn = np.concatenate([p[1] for p in parts])
    kind = np.concatenate([np.full(len(p[0]), p[2]) for p in parts])

    # color: terracota; banda decorada en negro; asas algo más oscuras; ruido de color
    col = np.tile([176.0, 96.0, 62.0], (len(P), 1))
    band = (kind == 'cuerpo') & (P[:, 2] > 118) & (P[:, 2] < 187)
    col[band] = [52, 40, 34]
    col[kind == 'interior'] = [140, 74, 48]
    col[kind == 'asa'] *= 0.9
    col += rng.normal(0, 8, col.shape)

    # ruido de medida (0,15 mm a lo largo de la normal y 0,05 en todas direcciones) y puntos sueltos
    P = P + Nn * rng.normal(0, 0.15, (len(P), 1)) + rng.normal(0, 0.05, P.shape)
    nout = len(P) // 2000
    lo, hi = P.min(0) - 20, P.max(0) + 20
    P = np.concatenate([P, rng.uniform(lo, hi, (nout, 3))])
    Nn = np.concatenate([Nn, np.tile([0, 0, 1.0], (nout, 1))])
    col = np.concatenate([col, np.full((nout, 3), 128.0)])
    # posición arbitraria (como sale de un escáner) y, si se pide, eje en Y
    if eje == 'y':
        P = P[:, [0, 2, 1]] * [1, 1, -1]
        Nn = Nn[:, [0, 2, 1]] * [1, 1, -1]
    P += [512.3, -87.1, 41.7]
    o = rng.permutation(len(P))
    P, Nn, col = P[o], Nn[o], col[o]

    return P, Nn, np.clip(col, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('salida', nargs='?', default='objeto_sintetico.ply')
    ap.add_argument('--puntos', type=int, default=1_200_000)
    ap.add_argument('--eje', choices=['z', 'y'], default='z')
    ap.add_argument('--normales', action='store_true')
    ap.add_argument('--semilla', type=int, default=1)
    a = ap.parse_args()
    import ejemplos
    P, Nn, col = vasija(a.puntos, a.eje, a.semilla)
    ejemplos.write_ply(a.salida, P, col, Nn if a.normales else None,
                       comment='nube sintetica de nube2stl: vasija con asas')
    ext = P.max(0) - P.min(0)
    print(f'{a.salida}: {len(P):,} puntos, {ext[0]:.0f} × {ext[1]:.0f} × {ext[2]:.0f} mm, eje {a.eje}'
          + (', con normales' if a.normales else ''))


if __name__ == '__main__':
    main()
