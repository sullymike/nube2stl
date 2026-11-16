"""Vista de corte del visor: geometría pura (numpy, sin Qt) para poder probarla aparte."""
import numpy as np


def section_lines(P, F, axis, c):
    """Segmentos (2M,3) donde el plano coord[axis] = c corta los triángulos (para GLLinePlotItem mode='lines')."""
    d = P[:, axis].astype(np.float64) - c
    D = d[F]
    s = np.sign(D)
    s[s == 0] = 1
    cross = ~((s[:, 0] == s[:, 1]) & (s[:, 1] == s[:, 2]))
    if not cross.any():
        return np.zeros((0, 3), np.float32)
    Fc, Dc, Sc = F[cross], D[cross], s[cross]
    pts = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        e = Sc[:, a] != Sc[:, b]
        t = Dc[:, a] / np.where(e, Dc[:, a] - Dc[:, b], 1)
        q = P[Fc[:, a]] + (P[Fc[:, b]] - P[Fc[:, a]]) * t[:, None]
        pts.append((q, e))
    # cada triángulo que cruza tiene exactamente dos aristas cortadas
    Q = np.stack([p for p, _ in pts], 1)
    E = np.stack([e for _, e in pts], 1)
    first = np.argmax(E, 1)
    last = 2 - np.argmax(E[:, ::-1], 1)
    i = np.arange(len(Q))
    return np.stack([Q[i, first], Q[i, last]], 1).reshape(-1, 3).astype(np.float32)


def clip_mesh(P, F, axis, c, cols=None):
    """Parte de la malla con coord[axis] <= c, recortando exactamente los triángulos que cruzan el plano.
    Devuelve (P2, F2, cols2): los vértices nuevos se añaden al final (cols interpolados si se dan)."""
    d = P[:, axis].astype(np.float64) - c
    below = d[F] <= 0
    nb = below.sum(1)
    keep = F[nb == 3]
    newP, newC, tris = [], [], []
    base = len(P)

    def cut(a, b):                                   # punto del plano en la arista a-b (índices (m,))
        t = (d[a] / (d[a] - d[b]))[:, None]
        newP.append(P[a] + (P[b] - P[a]) * t)
        if cols is not None:
            newC.append(cols[a] + (cols[b] - cols[a]) * t)
        n0 = base + sum(len(x) for x in newP[:-1])
        return np.arange(n0, n0 + len(a))
    for k in (1, 2):
        T, B = F[nb == k], below[nb == k]
        if not len(T):
            continue
        # rotar cada triángulo para que el vértice «distinto» quede el primero (conserva la orientación)
        odd = np.argmax(B if k == 1 else ~B, 1)
        i = np.arange(len(T))
        v0, v1, v2 = T[i, odd], T[i, (odd + 1) % 3], T[i, (odd + 2) % 3]
        p01, p02 = cut(v0, v1), cut(v0, v2)
        if k == 1:                                   # solo v0 debajo: queda un triángulo
            tris.append(np.c_[v0, p01, p02])
        else:                                        # v0 encima: queda un cuadrilátero v1 v2 p02 p01
            tris += [np.c_[p01, v1, v2], np.c_[p01, v2, p02]]
    if not tris:
        return P, keep, cols
    P2 = np.concatenate([P] + [x.astype(P.dtype) for x in newP])
    F2 = np.concatenate([keep] + tris).astype(F.dtype)
    C2 = None if cols is None else np.concatenate([cols] + [x.astype(cols.dtype) for x in newC])
    return P2, F2, C2
