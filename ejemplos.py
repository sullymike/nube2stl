#!/usr/bin/env python3
"""Nubes de ejemplo sintéticas, una por cada tipo de pieza: para aprender a usar el programa, para enseñarlo en
clase y para las pruebas automáticas (pruebas.py usa estas mismas funciones).

Cada ejemplo imita lo que sale de un escáner: ruido de medida, una posición y un giro cualesquiera, y solo lo que
el escáner habría visto. Se generan en unos segundos y no ocupan nada en el programa.

Desde la ventana: Ayuda › Abrir un ejemplo. Desde la consola:
    python3 ejemplos.py [carpeta]          (escribe todos los ejemplos en la carpeta, por defecto ./ejemplos)
"""
import os
import sys

import numpy as np


# ------------------------------------------------------------------ utilidades
def write_ply(path, xyz, rgb=None, nrm=None, comment=None):
    """PLY binario con x, y, z y, si se dan, normales y color."""
    props = [('x', 'f4'), ('y', 'f4'), ('z', 'f4')]
    if nrm is not None:
        props += [('nx', 'f4'), ('ny', 'f4'), ('nz', 'f4')]
    if rgb is not None:
        props += [('red', 'u1'), ('green', 'u1'), ('blue', 'u1')]
    rec = np.empty(len(xyz), dtype=[(k, '<' + t) for k, t in props])
    for k, c in enumerate('xyz'):
        rec[c] = xyz[:, k]
    if nrm is not None:
        for k, c in enumerate(('nx', 'ny', 'nz')):
            rec[c] = nrm[:, k]
    if rgb is not None:
        for k, c in enumerate(('red', 'green', 'blue')):
            rec[c] = rgb[:, k]
    tn = {'f4': 'float', 'u1': 'uchar'}
    head = ['ply', 'format binary_little_endian 1.0'] + ([f'comment {comment}'] if comment else []) + \
        [f'element vertex {len(xyz)}'] + [f'property {tn[t]} {k}' for k, t in props] + ['end_header']
    with open(path, 'wb') as f:
        f.write(('\n'.join(head) + '\n').encode('ascii'))
        rec.tofile(f)


def _stone(rng, n, shade=None, base=(178, 170, 156)):
    """Color de piedra con grano; shade (≤ 0 en los surcos) oscurece."""
    c = np.tile(np.array(base, float), (n, 1)) + rng.normal(0, 9, (n, 1))
    if shade is not None:
        c *= (1 + 0.12 * np.clip(shade, -3, 3))[:, None]
    return np.clip(c, 0, 255).astype(np.uint8)


def _place(P, rx=0.0, rz=0.0, shift=(0, 0, 0)):
    """Girar (grados, en X y después en Z) y desplazar: como sale de un escáner, no alineado con los ejes."""
    a, b = np.radians(rx), np.radians(rz)
    Rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    Rz = np.array([[np.cos(b), -np.sin(b), 0], [np.sin(b), np.cos(b), 0], [0, 0, 1]])
    R = Rz @ Rx
    return (P @ R.T + shift).astype(np.float32), R


# ------------------------------------------------------------------ los ejemplos
def lapida(n=250_000, seed=7):
    """Lápida de 400 × 260 mm: moldura en el borde, tres renglones de letras incisas (2 mm) y una roseta en relieve
    (4 mm). Escaneada de pie (la cara mira hacia −Y), solo por delante. -> (xyz, rgb)"""
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(0, 400, n), rng.uniform(0, 260, n)
    edge = np.minimum.reduce([x, 400 - x, y, 260 - y])
    z = 3.0 * (edge < 18) + 1.5 * ((edge >= 18) & (edge < 22))                     # moldura
    z += 4 * np.exp(-((x - 200) ** 2 + (y - 205) ** 2) / 220) * (1 + 0.4 * np.cos(6 * np.arctan2(y - 205, x - 200)))
    g = np.zeros(n)
    for row, y0 in enumerate((150, 105, 60)):                                      # «letras»: trazos incisos
        for j in range(12 - 2 * row):
            x0 = 60 + j * 25 + 12 * row
            g -= 2 * ((np.abs(x - x0) < 2) & (np.abs(y - y0) < 14))               # trazo vertical
            if j % 3 == 0:
                g -= 2 * ((np.abs(y - y0 - 12) < 2) & (np.abs(x - x0 - 6) < 7))   # trazo horizontal
    z = z + np.maximum(g, -2) + rng.normal(0, 0.08, n)
    P = np.c_[x - 200, -z, y]                                  # de pie: la cara mira hacia −Y
    xyz, _ = _place(P, rz=12, shift=(1500, -300, 900))
    return xyz, _stone(rng, n, g + 0.2 * z)


def gastada(n=300_000, seed=11):
    """Lápida gastada de 300 × 200 mm: los cantos redondeados, 6 mm (forma grande, no se debe realzar), dos
    renglones de letras incisas que el tiempo ha dejado en 0,3-0,9 mm de hondo (más gastadas hacia la derecha) y una
    palma en relieve de 0,5 mm. Impresa a 150 mm, las letras se quedan en 0,15-0,45 mm: menos de dos capas. Para
    «Realzar lo pequeño». Escaneada tumbada, solo por arriba. -> (xyz, rgb)"""
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(0, 300, n), rng.uniform(0, 200, n)
    edge = np.minimum.reduce([x, 300 - x, y, 200 - y])
    z = -6.0 * np.clip((16 - edge) / 16, 0, 1) ** 1.5                                     # cantos redondeados
    z += 0.6 * np.sin(x / 70) * np.cos(y / 55)                                            # la losa no es plana
    g = np.zeros(n)
    for row, y0 in enumerate((130, 85)):
        for j in range(9):
            x0 = 45 + j * 25
            deep = 0.9 - 0.6 * j / 8                                                       # más gastado a la derecha
            stroke = (np.abs(x - x0) < 1.6) & (np.abs(y - y0) < 13)
            if (j + row) % 2 == 0:
                stroke |= (np.abs(y - y0 - 11) < 1.6) & (np.abs(x - x0 - 5) < 6)
            else:
                stroke |= (np.abs(np.abs(x - x0) - 0.55 * (y - y0 + 13)) < 1.6) & (np.abs(y - y0) < 13)
            g = np.minimum(g, -deep * stroke)
    palm = np.exp(-((x - 150) ** 2) / 4) * (np.abs(y - 45) < 18)
    for k in range(-3, 4):                                                                 # hojas de la palma
        yy = 45 + 5 * k
        palm = np.maximum(palm, np.exp(-((y - yy - 0.5 * np.abs(x - 150)) ** 2) / 1.5) * (np.abs(x - 150) < 14))
    z = z + g + 0.5 * palm + rng.normal(0, 0.04, n)
    xyz, _ = _place(np.c_[x - 150, y - 100, z], rx=4, rz=-18, shift=(800, 400, 50))
    return xyz, _stone(rng, n, 2 * g + 0.1 * z)


def bloque(n=120_000, seed=0):
    """Bloque de 200 × 140 con relieve (bultos y un surco) en la cara y los 30 mm de arriba de dos laterales,
    girado y desplazado como sale de un escáner. -> (xyz, rgb)"""
    rng = np.random.default_rng(seed)
    nf = int(n * 0.8)
    x, y = rng.uniform(0, 200, nf), rng.uniform(0, 140, nf)
    z = (6 * np.exp(-((x - 60) ** 2 + (y - 70) ** 2) / 300) + 4 * np.exp(-((x - 140) ** 2 + (y - 50) ** 2) / 200)
         - 3 * np.exp(-((y - 105) / 2.5) ** 2) * (x > 30) * (x < 170))
    face = np.c_[x, y, z]
    ns = n - nf
    s1 = np.c_[np.zeros(ns // 2), rng.uniform(0, 140, ns // 2), rng.uniform(-30, 0, ns // 2)]
    s2 = np.c_[rng.uniform(0, 200, ns - ns // 2), np.zeros(ns - ns // 2), rng.uniform(-30, 0, ns - ns // 2)]
    P = np.concatenate([face, s1, s2]) + rng.normal(0, 0.08, (n, 3))
    a = np.radians(25)
    R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    P = P @ R.T + [1000, -500, 80]
    rgb = rng.integers(90, 200, (n, 3)).astype(np.uint8)
    return P.astype(np.float32), rgb


def columna(n=150_000, R=200.0, H=600.0, arc=160.0, seed=1):
    """Fuste de columna (o miliario) de radio R: la cara abarca «arc» grados, con cinco renglones de «letras»
    (surcos de 3 mm) y un bulto, con el eje inclinado 20° y desplazado. -> (xyz, rgb, eje)"""
    rng = np.random.default_rng(seed)
    th = rng.uniform(-np.radians(arc) / 2, np.radians(arc) / 2, n)
    z = rng.uniform(0, H, n)
    s = R * th
    d = np.zeros(n)
    for k in range(5):
        for j in range(-6, 7):
            d[(np.abs(s - j * 30) < 3) & (np.abs(z - 150 - 70 * k) < 20)] -= 3
    d += 8 * np.exp(-((s - 100) ** 2 + (z - 500) ** 2) / 2000)
    r = R + d + rng.normal(0, 0.1, n)
    xyz, Rot = _place(np.c_[r * np.cos(th), r * np.sin(th), z], rx=20, shift=(300, 200, 50))
    return xyz, _stone(rng, n, d / 1.5, base=(196, 186, 170)), Rot @ [0, 0, 1.0]


def roca(n=200_000, L=800.0, seed=2):
    """Roca con una joroba de 160 mm y una ondulación, con grabados de 2 mm (tres círculos y una línea), como un
    panel de petroglifos. -> (xyz, rgb)"""
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(0, L, n), rng.uniform(0, L * 0.7, n)
    z = 160 * np.exp(-((x - L * 0.45) ** 2 + (y - L * 0.3) ** 2) / (2 * (L * 0.35) ** 2)) + 40 * np.sin(x / L * 3)
    g = np.zeros(n)
    for cx, cy, rr in [(250, 250, 60), (500, 300, 90), (600, 150, 40)]:
        g -= 2 * (np.abs(np.hypot(x - cx, y - cy) - rr) < 3)
    g -= 2 * ((np.abs(y - 0.5 * x - 50) < 3) & (x > 100) & (x < 400))
    g = np.maximum(g, -2)
    xyz = np.c_[x, y, z + g + rng.normal(0, 0.1, n)].astype(np.float32)
    return xyz, _stone(rng, n, g * 0.5 + 0.004 * z - 0.3, base=(150, 132, 112))


def vasija(n=400_000, seed=1):
    """Vasija completa con asas, escaneada por todos los lados menos el fondo (generar_objeto_sintetico.py).
    -> (xyz, rgb, normales)"""
    import generar_objeto_sintetico as g
    P, N, col = g.vasija(n, 'z', seed)
    return P.astype(np.float32), col, N


def terreno(n=250_000, seed=5, k=50.0):
    """Terreno ondulado de 20 × 10 m con un muro (15 m × 1 m × 4 m) que tiene un arco pasante, sacado por
    fotogrametría desde arriba: solo la superficie de arriba y de los lados. En mm. -> (xyz, rgb)"""
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(0, 400, n), rng.uniform(0, 200, n)
    z = 20 * np.sin(x / 90) + 10 * np.cos(y / 60)
    ground = np.c_[x, y, z]
    m = n // 2
    wx, wz = rng.uniform(50, 350, m), rng.uniform(0, 80, m)
    hole = (np.abs(wx - 200) < 30) & (wz < 50)
    side = rng.integers(0, 2, m)
    wall = np.c_[wx, 90 + 20 * side, wz + 30][~hole]
    top = np.c_[rng.uniform(50, 350, n // 12), rng.uniform(90, 110, n // 12), np.full(n // 12, 110.0)]
    a = rng.uniform(0, 1, n // 30)
    intr = np.c_[170 + 60 * a, rng.uniform(90, 110, n // 30), np.full(n // 30, 80.0)]
    terr = ground[~((ground[:, 1] > 90) & (ground[:, 1] < 110) & (ground[:, 0] > 50) & (ground[:, 0] < 350)
                    & ~((np.abs(ground[:, 0] - 200) < 30)))].copy()
    terr[(terr[:, 1] > 88) & (terr[:, 1] < 112) & (terr[:, 0] > 165) & (terr[:, 0] < 235), 2] = 30.0
    pts = np.vstack([terr, wall, top, intr]) * k
    nt = len(terr)
    col = np.empty((len(pts), 3))
    col[:nt] = [104, 128, 76] + rng.normal(0, 10, (nt, 1))                       # hierba
    col[nt:] = [182, 160, 128] + rng.normal(0, 10, (len(pts) - nt, 1))           # sillares
    return (pts + [12_000, 5_000, 600]).astype(np.float32), np.clip(col, 0, 255).astype(np.uint8)


def _pot_r(z):
    """Perfil de la olla del fragmento (radio exterior, mm) a la altura z: panza, cuello y borde exvasado."""
    z = np.asarray(z, np.float64)
    return 55 + 35 * np.sin(np.pi * np.clip(z, 0, 150) / 190) - 14 * np.exp(-((z - 128) / 12) ** 2) \
        + 6 * np.clip((z - 135) / 15, 0, 1) ** 2


def fragmento(n=220_000, seed=3, span=80.0, z0=55.0, z1=150.0, wall=6.0, with_axis=False):
    """Fragmento de una olla a torno: pared exterior con dos acanaladuras y una banda de incisiones, pared interior,
    borde y fracturas, de z0 a z1 y span grados alrededor. -> (xyz, rgb) o, con with_axis, (xyz, rgb, eje, punto)"""
    rng = np.random.default_rng(seed)
    th0 = np.radians(span)
    parts, shade = [], []
    no, ni = int(n * 0.55), int(n * 0.35)
    # exterior (densidad uniforme en área ≈ uniforme en z y θ, el radio varía poco)
    z = rng.uniform(z0, z1, no)
    th = rng.uniform(0, th0, no)
    dec = -0.8 * (np.abs(((z - 100) % 9) - 4.5) < 0.6) * (z > 85) * (z < 115)              # acanaladuras
    dec -= 0.6 * ((np.abs(((th * 95) % 6) - 3) < 0.5) & (z > 70) & (z < 82))              # incisiones
    r = _pot_r(z) + dec + rng.normal(0, 0.05, no)
    parts.append(np.c_[r * np.cos(th), r * np.sin(th), z])
    shade.append(dec)
    # interior
    z = rng.uniform(z0, z1, ni)
    th = rng.uniform(0, th0, ni)
    r = _pot_r(z) - wall + rng.normal(0, 0.05, ni)
    parts.append(np.c_[r * np.cos(th), r * np.sin(th), z])
    shade.append(np.zeros(ni))
    # fracturas (los dos lados y abajo) y el labio (arriba)
    nf = (n - no - ni) // 4
    for kind in ('a', 'b', 'abajo', 'labio'):
        u = rng.uniform(0, 1, nf)
        if kind in ('a', 'b'):
            z = rng.uniform(z0, z1, nf)
            th = np.full(nf, 0.0 if kind == 'a' else th0) + rng.normal(0, 0.01, nf)
        else:
            z = np.full(nf, z0 if kind == 'abajo' else z1) + rng.normal(0, 0.3, nf)
            th = rng.uniform(0, th0, nf)
        r = _pot_r(z) - wall * u
        parts.append(np.c_[r * np.cos(th), r * np.sin(th), z])
        shade.append(np.full(nf, -0.5))
    P = np.concatenate(parts)
    xyz, R = _place(P, rx=62, rz=-35, shift=(120, -40, 300))
    rgb = _stone(rng, len(P), np.concatenate(shade) * 2, base=(176, 112, 78))
    if with_axis:
        return xyz, rgb, R @ [0, 0, 1.0], (R @ np.zeros(3) + [120, -40, 300])
    return xyz, rgb


def cisterna(n=300_000, seed=4):
    """Cisterna abovedada escaneada desde dentro (mm): 3 × 2 m de planta, muros de 1,6 m, bóveda de cañón hasta 2,6 m,
    un escalón en el suelo y un nicho en un muro. Solo las superficies interiores. -> (xyz, rgb)"""
    rng = np.random.default_rng(seed)
    L, W, Hw, R = 3000.0, 2000.0, 1600.0, 1000.0
    parts = []
    a = n * 6 // 30
    x, y = rng.uniform(0, L, a), rng.uniform(0, W, a)
    parts.append(np.c_[x, y, np.where(x < 700, 250.0, 0.0)])                      # suelo con escalón
    m = n // 60
    parts.append(np.c_[np.full(m, 700.0), rng.uniform(0, W, m), rng.uniform(0, 250, m)])    # frente del escalón
    for yy in (0.0, W):                                                             # muros largos
        k = n * 5 // 30
        x, z = rng.uniform(0, L, k), rng.uniform(0, Hw, k)
        z = np.where(x < 700, np.maximum(z, 250), z)
        niche = (yy == W) & (np.abs(x - 1800) < 300) & (z > 500) & (z < 1200)
        parts.append(np.c_[x, np.where(niche, W + 350, yy), z])                    # el nicho, 35 cm de fondo
    k = n // 100                                                                    # los lados del nicho
    y, t = rng.uniform(W, W + 350, k), rng.uniform(0, 1, k)
    for xx in (1500.0, 2100.0):
        parts.append(np.c_[np.full(k, xx), y, 500 + 700 * t])
    for zz in (500.0, 1200.0):
        parts.append(np.c_[1500 + 600 * t, y, np.full(k, zz)])
    for xx in (0.0, L):                                                             # testeros (con la bóveda)
        k = n * 3 // 30
        y, z = rng.uniform(0, W, k), rng.uniform(0, Hw + R, k)
        ok = z <= Hw + np.sqrt(np.clip(R ** 2 - (y - W / 2) ** 2, 0, None))
        z = np.where(xx == 0, np.maximum(z, 250), z)
        parts.append(np.c_[np.full(ok.sum(), xx), y[ok], z[ok]])
    k = n * 7 // 30
    x, t = rng.uniform(0, L, k), rng.uniform(0, np.pi, k)                           # bóveda de cañón
    parts.append(np.c_[x, W / 2 + R * np.cos(t), Hw + R * np.sin(t)])
    P = np.concatenate(parts)
    P = P + rng.normal(0, 3, P.shape)
    rgb = _stone(rng, len(P), None, base=(150, 146, 138))
    return (P + [5000, 3000, 100]).astype(np.float32), rgb



def esquina(n=260_000, seed=5, split=False, angle=90.0, strip=0.0):
    """Esquina de un friso (mm): cara principal de 240 × 150 con una moldura y una greca en relieve, y la cara
    lateral de 240 × 110 con dentículos junto a la arista, una roseta y letras incisas, a «angle» grados (por dentro de la piedra). Con
    split=True, las dos caras como dos escaneos, la segunda movida y girada (para «Unir otra nube › Por la
    arista»): ((xyz, rgb), (xyz, rgb), M), con M la transformación que devuelve la segunda a su sitio. strip: mm de
    la cara lateral que también se ven en el primer escaneo (una franja junto a la arista, muestreada aparte, como
    la vería otro escaneo); 0 = sin solape."""
    rng = np.random.default_rng(seed)
    na = n * 15 // 26
    nb = n - na
    x, y = rng.uniform(0, 240, na), rng.uniform(0, 150, na)
    za = 3.0 * (y > 130)                                                     # moldura de arriba
    za += 1.5 * ((np.abs(((x + 10) % 30) - 15) < 4) & (y > 40) & (y < 100))  # greca
    za += 1.5 * ((np.abs(y - 70) < 4) & (x > 15) & (x < 225))
    sa = -za / 3
    za += rng.normal(0, 0.05, na)
    th = np.radians(angle)
    b_dir = np.array([0, np.cos(th), -np.sin(th)])                          # de la arista hacia la lateral
    b_out = np.array([0, -np.sin(th), -np.cos(th)])                          # fuera de la lateral

    def side(x2, z, noise):
        r = np.hypot(x2 - 60, z + 55)
        yb = -2.0 * ((r < 30) & (np.abs(np.sin(np.arctan2(z + 55, x2 - 60) * 4)) * 30 > r - 5))   # roseta
        yb += 1.2 * ((np.abs(((x2 - 110) % 14) - 7) < 1.2) & (x2 > 110) & (x2 < 225) & (np.abs(z + 55) < 15))
        yb -= 1.5 * (np.clip(3 - np.abs((x2 % 16) - 8), 0, 1) * np.clip(5 - np.abs(z + 9), 0, 1))       # dentículos
        P = np.outer(x2, [1, 0, 0]) + np.outer(-z, b_dir) + np.outer(-(yb + noise), b_out)
        return P, yb / 2

    x2, z = rng.uniform(0, 240, nb), rng.uniform(-110, 0, nb)
    B, sb = side(x2, z, rng.normal(0, 0.05, nb))
    A = np.c_[x, y, za]
    if split:
        ca = _stone(rng, na, sa)
        cb = _stone(rng, nb, sb)
        if strip > 0:                                     # la franja de la lateral vista desde el primer escaneo
            ns = int(nb * strip / 110)
            S, ss = side(rng.uniform(0, 240, ns), rng.uniform(-strip, 0, ns), rng.normal(0, 0.05, ns))
            A, ca = np.r_[A, S], np.r_[ca, _stone(rng, ns, ss)]
        Bm, R = _place(B, rx=35, rz=70, shift=(400, -150, 80))
        M = np.eye(4)                                     # la que devuelve la lateral a su sitio
        M[:3, :3], M[:3, 3] = R.T, -R.T @ np.array([400, -150, 80.0])
        return (A.astype(np.float32), ca), (Bm.astype(np.float32), cb), M
    P = np.r_[A, B]
    xyz, _ = _place(P, rx=20, rz=-25, shift=(300, 200, 100))
    return xyz, _stone(rng, len(P), np.r_[sa, sb])

# (clave, fichero, título, tipo que debe proponer la ventana, qué enseña, generador)
EXAMPLES = [
    ('lapida', 'ejemplo_lapida.ply', 'Lápida con inscripción', 'relieve',
     'Una cara casi plana con letras incisas, una moldura y una roseta. Para ver el mapa de alturas, el tamaño, '
     'Orientar (cuadrar el giro con los renglones) y la placa con marco o el molde.', lapida),
    ('gastada', 'ejemplo_gastada.ply', 'Lápida gastada (letras poco hondas)', 'relieve',
     'Letras incisas de 0,3 a 0,9 mm y una palma de 0,5 mm; los cantos, redondeados, caen 6 mm. Impresa a 150 mm, '
     'las letras ocupan una o dos capas. Compara «Exageración relieve» (también crecen los cantos) con «Realzar lo '
     'pequeño» (solo crecen las letras y la palma) y mira el mapa de desviación: es lo que se ha añadido.', gastada),
    ('bloque', 'ejemplo_bloque.ply', 'Bloque con dos laterales', 'bloque',
     'La cara de un sillar con los 30 mm de arriba de dos laterales. Para comparar «Relieve» (los laterales se '
     'pierden) con «Bloque» (se conservan) y el campo «Profundidad máx.».', lambda: bloque()),
    ('esquina', 'ejemplo_esquina.ply', 'Esquina de un friso (dos caras)', 'bloque',
     'Dos caras con relieve a 90°: una greca y una moldura en la principal; dentículos, una roseta y letras en la '
     'lateral. Con '
     '«Bloque» y «Esquina (dos caras)» cada cara se hace con su propio mapa de alturas; compara sin «Esquina» (la '
     'lateral sale más blanda) y con «Relieve» (la nota de «Plano de la cara» avisa de que se mira desde la esquina).',
     lambda: esquina()),
    ('columna', 'ejemplo_columna.ply', 'Inscripción en una columna', 'curvo',
     'Medio fuste de 400 mm de diámetro con cinco renglones de letras. Con «Curvo» se desenrolla: mira la pestaña '
     'Orientación y mide la pieza en Malla 3D (los mm reales a lo largo del arco).', lambda: columna()[:2]),
    ('roca', 'ejemplo_roca.ply', 'Grabados sobre roca', 'grabado',
     'Un panel de roca con jorobas y grabados de 2 mm. Compara «Relieve» (la forma de la roca se come el grabado) '
     'con «Grabado», y prueba distintos valores de «Quitar forma de la roca» y de exageración.', roca),
    ('vasija', 'ejemplo_vasija.ply', 'Vasija completa', 'objeto',
     'Escaneada por todos los lados menos el fondo, con asas y una banda decorada. Para el método objeto: qué va '
     'arriba, el corte de la base, la pieza hueca y el mapa de desviación.', vasija),
    ('fragmento', 'ejemplo_fragmento.ply', 'Fragmento de olla', 'fragmento',
     'Un fragmento de borde y panza de una olla a torno (80° de la circunferencia), con acanaladuras e incisiones. '
     'Con «Fragmento» se busca el eje y se reconstruye la olla entera (Archivo › Guardar el perfil, para el '
     'dibujo); con «Curvo» y superficie «revolución», se desenrolla la decoración.', fragmento),
    ('cisterna', 'ejemplo_cisterna.ply', 'Cisterna abovedada (interior)', 'interior',
     'Escaneada desde dentro: suelo con un escalón, muros, un nicho y bóveda de cañón (3 × 2 m, en mm). Con '
     '«Interior»: la maqueta en corte (sin el techo, como una casa de muñecas) o el negativo (el hueco como sólido).',
     cisterna),
    ('terreno', 'ejemplo_terreno.ply', 'Terreno con un muro y un arco', 'terreno',
     'Fotogrametría aérea de 20 × 10 m (en mm). Para «Terreno» (maqueta cerrada con el '
     'arco pasante), la fachada en relieve («Relieve» mirando desde el frente) y el recorte en la vista de la nube.',
     terreno),
]


def info(key):
    return next(e for e in EXAMPLES if e[0] == key)


def ensure(key, folder, log=lambda s: None):
    """Ruta del ejemplo en la carpeta; lo genera si no está."""
    _, fname, title, _, _, make = info(key)
    path = os.path.join(folder, fname)
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        log(f'Generando el ejemplo «{title}»…')
        out = make()
        xyz, rgb = out[0], out[1]
        nrm = out[2] if key == 'vasija' else None
        tmp = path + '.tmp'
        write_ply(tmp, xyz, rgb, nrm, comment=f'ejemplo sintetico de nube2stl: {key}')
        os.replace(tmp, path)
    return path


def main(argv):
    folder = argv[0] if argv else 'ejemplos'
    for key, *_ in EXAMPLES:
        print(ensure(key, folder, print))


if __name__ == '__main__':
    main(sys.argv[1:])
