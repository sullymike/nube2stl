"""Lectura de nubes de puntos y mallas para nube2stl, unidades y limpieza al cargar.

Formatos:
- PLY (nube o malla), XYZ / PTS / TXT / CSV / ASC (columnas de texto), LAS / LAZ (láser aéreo y terrestre,
  necesita laspy; LAZ además lazrs), E57 (escáneres láser, necesita pye57), OBJ y STL (mallas).
- De una malla (OBJ, STL o PLY con caras) se toman puntos repartidos por la superficie según el área, con la normal
  de cada triángulo (que el modo objeto usa como normales del fichero) y el color de los vértices o de la textura.

Unidades: todo se pasa a milímetros al cargar (Params.units: auto, mm, cm, m, pulgadas). En automático, una nube de
menos de 20 unidades de lado se toma como metros (una piedra o una estatua no mide 2 cm) y lo demás como mm.

Limpieza (Params.clean): se agrupan los puntos en vóxeles, se buscan las partes conectadas y se quitan las que
tienen menos del 2 % de los puntos de la mayor (puntos sueltos, trozos de suelo o de soporte separados de la pieza).
Se hace antes que nada, así que también el plano de la cara y las medidas se calculan ya sin ellos.
"""
import os

import numpy as np
from scipy import ndimage

UNITS = {'mm': 1.0, 'cm': 10.0, 'm': 1000.0, 'pulgadas': 25.4}
EXTS = ('.ply', '.xyz', '.pts', '.txt', '.csv', '.asc', '.las', '.laz', '.e57', '.obj', '.stl')
OPEN_FILTER = ('Nubes y mallas (*.ply *.xyz *.pts *.txt *.csv *.asc *.las *.laz *.e57 *.obj *.stl);;'
               'PLY (*.ply);;Texto XYZ / PTS / CSV (*.xyz *.pts *.txt *.csv *.asc);;LAS / LAZ (*.las *.laz);;'
               'E57 (*.e57);;Mallas OBJ / STL (*.obj *.stl);;Todos (*)')
MESH_MAX_POINTS = 8_000_000


# ------------------------------------------------------------------ mallas -> puntos
CHUNK_TRIS = 2_000_000                        # triángulos por bloque al muestrear una malla (memoria acotada)


def sample_mesh(V, F, vcol=None, uvcol=None, n=None, tris=None, nverts=None):
    """Puntos repartidos por la superficie de una malla (proporcional al área), con la normal del triángulo.
    vcol: color por vértice (N,3) uint8; uvcol: función (tri, bary) -> color (para texturas); tri son índices de F.
    tris: en vez de V y F, los triángulos sueltos (n,3,3) (un STL, sin unir vértices); nverts: cuántos vértices
    tendría (para el número de puntos; por defecto, la mitad de los triángulos, lo de una malla cerrada).
    Por bloques de CHUNK_TRIS triángulos y sin copias de toda la malla: con 19 M triángulos, la versión anterior
    (V[F] entero en float64, filtrado y copiado) pasaba de 5 GB solo en esto."""
    if tris is not None:
        nt = len(tris)
        get = lambda idx: np.asarray(tris[idx], np.float64)                     # noqa: E731
        nv = int(nverts) if nverts else max(nt // 2, 1)
    else:
        V = np.asarray(V)
        F = np.asarray(F)
        if F.dtype != np.int32 and len(V) < 2**31:
            F = F.astype(np.int32)
        nt = len(F)
        get = lambda idx: V[F[idx]].astype(np.float64)                          # noqa: E731
        nv = len(V)
    a2 = np.empty(nt, np.float64)                  # doble del área de cada triángulo
    for i in range(0, nt, CHUNK_TRIS):
        t = get(slice(i, i + CHUNK_TRIS))
        a2[i:i + len(t)] = np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    rng = np.random.default_rng(0)
    if n is None:                                  # lado típico: mediana de una muestra de aristas
        k = np.sort(rng.choice(nt, 1_000_000, replace=False)) if nt > 1_000_000 else np.arange(nt)
        t = get(k)
        e = np.linalg.norm(t[:, 1] - t[:, 0], axis=1)
        med = float(np.median(e[e > 0])) if (e > 0).any() else 1.0
        n = int(np.clip(4 * a2.sum() / 2 / med ** 2, nv, MESH_MAX_POINTS))
    cdf = np.cumsum(a2)
    del a2
    tri = np.searchsorted(cdf, rng.random(n) * cdf[-1], side='right').clip(max=nt - 1)
    del cdf
    r1, r2 = rng.random(n), rng.random(n)
    s1 = np.sqrt(r1)
    b = np.c_[1 - s1, s1 * (1 - r2), s1 * r2]
    del r1, r2, s1
    P = np.empty((n, 3), np.float32)
    N = np.empty((n, 3), np.float32)
    for i in range(0, n, CHUNK_TRIS):
        sl = slice(i, i + CHUNK_TRIS)
        t = get(tri[sl])
        cr = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        P[sl] = np.einsum('ij,ijk->ik', b[sl], t)
        N[sl] = cr / np.maximum(np.linalg.norm(cr, axis=1), 1e-300)[:, None]
    col = None
    if uvcol is not None:
        col = uvcol(tri, b)
    elif vcol is not None and tris is None:
        col = np.empty((n, 3), np.uint8)
        for i in range(0, n, CHUNK_TRIS):
            sl = slice(i, i + CHUNK_TRIS)
            col[sl] = np.einsum('ij,ijk->ik', b[sl], vcol[F[tri[sl]]].astype(np.float32)).round().clip(0, 255)
    return P, col, N


# ------------------------------------------------------------------ lectores
def _ply(path):
    """PLY: nube (vértices) o malla (vértices y triángulos)."""
    import relief
    xyz, rgb, nrm = relief.read_ply_points(path, normals=True)
    faces = _ply_faces(path, len(xyz))
    if faces is not None and len(faces):
        return dict(V=xyz, F=faces, vcol=rgb)
    return dict(xyz=xyz, rgb=rgb, nrm=nrm)


def _ply_faces(path, nv):
    """Triángulos de un PLY binario cuyas caras sean todas triángulos con 'list uchar int|uint' (lo habitual).
    None si no hay caras o no se pueden leer así (se usa entonces solo la nube de vértices)."""
    with open(path, 'rb') as f:
        f.readline()
        fmt, els = None, []
        while True:
            line = f.readline()
            if not line:
                return None
            w = line.decode('ascii', 'replace').split()
            if not w:
                continue
            if w[0] == 'format':
                fmt = w[1]
            elif w[0] == 'element':
                els.append([w[1], int(w[2]), []])
            elif w[0] == 'property':
                els[-1][2].append(w[1:])
            elif w[0] == 'end_header':
                break
        names = [e[0] for e in els]
        if 'face' not in names or fmt == 'ascii' or names[0] != 'vertex':
            return None
        import relief
        end = '<' if fmt == 'binary_little_endian' else '>'
        vsize = sum(np.dtype(relief.PLY_TYPES[p[0]]).itemsize for p in els[0][2])
        fe = els[names.index('face')]
        if names.index('face') != 1 or len(fe[2]) != 1 or fe[2][0][0] != 'list':
            return None
        ct, it = relief.PLY_TYPES[fe[2][0][1]], relief.PLY_TYPES[fe[2][0][2]]
        f.seek(f.tell() + vsize * nv)
        dt = np.dtype([('n', end + ct), ('i', end + it, 3)])
        a = np.fromfile(f, dtype=dt, count=fe[1])
    if len(a) < fe[1] or (a['n'] != 3).any():
        return None
    return a['i'].astype(np.int64)


def _stl(path):
    """STL: los triángulos sueltos, tal cual (para repartir puntos no hace falta unir los vértices repetidos, y
    unirlos con np.unique costaba varios GB y casi todo el tiempo de lectura en una malla grande)."""
    with open(path, 'rb') as f:
        head = f.read(84)
        n = int(np.frombuffer(head[80:84], '<u4')[0]) if len(head) == 84 else 0
        size = os.path.getsize(path)
        if len(head) == 84 and 84 + 50 * n == size:
            rec = np.fromfile(f, dtype=[('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')], count=n)
            T = np.ascontiguousarray(rec['v'])
            del rec
        else:
            import re
            f.seek(0)
            txt = f.read().decode('ascii', 'replace')
            T = np.array(re.findall(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', txt), np.float32).reshape(-1, 3, 3)
    return dict(T=T)


OBJ_BLOCK = 128 * 2**20                       # bytes de texto por bloque al leer un OBJ


def _obj_texture(path):
    """(imagen RGB uint8, nombre) de la textura del OBJ (map_Kd del primer material con imagen), o (None, motivo)."""
    folder = os.path.dirname(os.path.abspath(path))
    mtl = None
    with open(path, 'rb') as f:
        for _ in range(2000):
            line = f.readline()
            if not line:
                break
            if line.startswith(b'mtllib'):
                mtl = line.split(None, 1)[1].strip().decode('utf-8', 'replace')
                break
    if not mtl:
        return None, None
    mp = os.path.join(folder, mtl)
    if not os.path.exists(mp):
        return None, f'falta {mtl}'
    maps = []
    with open(mp, 'r', errors='replace') as f:
        for line in f:
            t = line.strip().split(None, 1)
            if len(t) == 2 and t[0].lower() == 'map_kd':
                maps.append(t[1].split()[-1])
    if not maps:
        return None, None
    ip = os.path.join(folder, maps[0])
    if not os.path.exists(ip):
        return None, f'falta {maps[0]}'
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        img = np.asarray(Image.open(ip).convert('RGB'))
    except Exception as e:                    # noqa: BLE001
        return None, f'no se pudo abrir {maps[0]} ({e})'
    return img, maps[0] + (f' (de {len(maps)} texturas, solo la primera)' if len(maps) > 1 else '')


def _obj(path):
    """OBJ por bloques de texto con NumPy, sin pasar por objetos de Python por línea: un OBJ de 2,5 GB (10 M
    vértices con color, 19 M triángulos) se lee con la memoria de los vértices y triángulos, cuando Open3D pasaba
    de 8 GB. Color por vértice (v x y z r g b) y textura (vt y map_Kd del .mtl, la primera imagen)."""
    img, why = _obj_texture(path)
    want_uv = img is not None
    Vs, Fs, VTs, FTs = [], [], [], []
    nv = 0
    width = None                              # números por línea «v» (3, o 6 con color)
    with open(path, 'rb') as f:
        rest = b''
        while True:
            chunk = f.read(OBJ_BLOCK)
            if not chunk and not rest:
                break
            data = rest + chunk
            if chunk:
                cut = data.rfind(b'\n') + 1
                if cut == 0:
                    rest = data
                    continue
                data, rest = data[:cut], data[cut:]
            else:
                rest = b''
            lines = data.split(b'\n')
            del data
            vl = [ln[2:] for ln in lines if ln.startswith(b'v ')]
            fl = [ln[2:] for ln in lines if ln.startswith(b'f ')]
            tl = [ln[3:] for ln in lines if ln.startswith(b'vt ')] if want_uv else []
            del lines
            if vl:
                if width is None:
                    width = len(vl[0].split())
                arr = np.fromstring(b' '.join(vl), sep=' ')
                if arr.size != len(vl) * width:          # anchos distintos: línea a línea, con los 3 primeros
                    arr = np.array([[float(x) for x in ln.split()[:3]] for ln in vl])
                    width = 3 if width != 3 else width
                    if Vs and Vs[0].shape[1] != 3:
                        Vs = [v[:, :3] for v in Vs]
                arr = arr.reshape(len(vl), -1)
                if Vs and arr.shape[1] != Vs[0].shape[1]:
                    arr = arr[:, :3]
                    Vs = [v[:, :3] for v in Vs]
                Vs.append(arr)
                del vl
            if tl:
                VTs.append(np.array([[float(x) for x in ln.split()[:2]] for ln in tl])
                           if len(tl) < 1000 else _obj_uv_block(tl))
            if fl:
                F, FT = _obj_faces(fl, want_uv)
                del fl
                cur, curt = sum(len(v) for v in Vs), sum(len(v) for v in VTs)
                F[F < 0] += cur + 1                      # índices relativos (-1 = el último vértice leído)
                Fs.append(F)
                if FT is not None:
                    FT[FT < 0] += curt + 1
                    FTs.append(FT)
            nv = sum(len(v) for v in Vs)
    if not Vs or not Fs:
        return _obj_python(path)
    V = np.concatenate(Vs)
    del Vs
    F = np.concatenate(Fs) - 1
    del Fs
    if F.min() < 0 or F.max() >= nv:
        raise ValueError('OBJ con caras que apuntan a vértices que no existen')
    out = dict(V=np.ascontiguousarray(V[:, :3]), F=F.astype(np.int32 if nv < 2**31 else np.int64))
    if V.shape[1] >= 6:
        c = V[:, 3:6]
        out['vcol'] = (c * 255 if c.max() <= 1 else c).round().clip(0, 255).astype(np.uint8)
    del V
    if want_uv and VTs and FTs and sum(len(x) for x in FTs) == len(F):
        uv = np.concatenate(VTs).astype(np.float32)
        ft = np.concatenate(FTs) - 1
        h, w = img.shape[:2]

        def uvcol(tri, b):
            col = np.empty((len(tri), 3), np.uint8)
            for i in range(0, len(tri), CHUNK_TRIS):
                sl = slice(i, i + CHUNK_TRIS)
                u = np.einsum('ij,ijk->ik', b[sl], uv[ft[tri[sl]]])
                x = (np.mod(u[:, 0], 1) * (w - 1)).round().astype(int)
                y = ((1 - np.mod(u[:, 1], 1)) * (h - 1)).round().astype(int)
                col[sl] = img[y, x]
            return col
        out['uvcol'] = uvcol
        out['note'] = f'color de la textura {why}'
    elif why:
        out['note'] = f'sin la textura: {why}'
    return out


def _obj_uv_block(tl):
    arr = np.fromstring(b' '.join(tl), sep=' ')
    if arr.size == 2 * len(tl):
        return arr.reshape(-1, 2)
    if arr.size == 3 * len(tl):
        return arr.reshape(-1, 3)[:, :2]
    return np.array([[float(x) for x in ln.split()[:2]] for ln in tl])


def _obj_faces(fl, want_uv):
    """Caras de un bloque de líneas «f» (sin el «f »): (F (n,3) índices desde 1, FT índices de vt o None).
    Lo normal (todas triángulos con el mismo formato v, v/t, v//n o v/t/n) va de una vez con NumPy; si no,
    línea a línea, abriendo los polígonos en abanico."""
    first = fl[0].split()
    tok = first[0]
    m = tok.count(b'/') + 1 - tok.count(b'//')            # números por vértice
    has_t = b'/' in tok and b'//' not in tok and tok.split(b'/')[1] != b''
    if all(len(ln.split()) == 3 for ln in fl[:1000]) and len(first) == 3:
        arr = np.fromstring(b' '.join(fl).replace(b'//', b' ').replace(b'/', b' '), sep=' ')
        if arr.size == len(fl) * 3 * m:
            arr = arr.reshape(len(fl), 3, m).astype(np.int64)
            return arr[:, :, 0], (arr[:, :, 1] if (want_uv and has_t) else None)
    F, FT = [], []
    for ln in fl:
        idx = [t.split(b'/') for t in ln.split()]
        vi = [int(t[0]) for t in idx]
        ti = [int(t[1]) if len(t) > 1 and t[1] else 0 for t in idx]
        for k in range(1, len(vi) - 1):
            F.append((vi[0], vi[k], vi[k + 1]))
            FT.append((ti[0], ti[k], ti[k + 1]))
    FT = np.array(FT, np.int64)
    return np.array(F, np.int64).reshape(-1, 3), (FT if (want_uv and has_t and (FT != 0).all()) else None)


def _obj_python(path):
    V, F = [], []
    with open(path, 'r', errors='replace') as f:
        for line in f:
            if line.startswith('v '):
                V.append([float(x) for x in line.split()[1:7]])
            elif line.startswith('f '):
                idx = [int(x.split('/')[0]) for x in line.split()[1:]]
                for k in range(1, len(idx) - 1):
                    F.append([idx[0], idx[k], idx[k + 1]])
    nv = len(V)
    width = min(len(v) for v in V) if V else 3
    V = np.array([v[:width] for v in V], float)
    F = np.array(F, np.int64)
    F = np.where(F < 0, nv + F, F - 1)
    out = dict(V=V[:, :3], F=F)
    if width >= 6:
        c = V[:, 3:6]
        out['vcol'] = (c * 255 if c.max() <= 1 else c).round().clip(0, 255).astype(np.uint8)
    return out


def _text(path):
    """Columnas de texto: x y z [intensidad] [r g b] [nx ny nz], separadas por espacios, comas, ; o tabuladores.
    Se saltan cabeceras (nombres de columna, o el número de puntos de los .pts)."""
    names = None
    with open(path, 'rb') as f:
        head = []
        pos = 0
        while True:
            line = f.readline()
            if not line:
                break
            s = line.decode('utf-8', 'replace').strip()
            vals = s.replace(',', ' ').replace(';', ' ').replace('\t', ' ').split()
            try:
                [float(v) for v in vals]
                isnum = len(vals) >= 3
            except ValueError:
                isnum = False
            if isnum:
                ncol = len(vals)
                break
            if s and not s.startswith(('#', '//')) and len(vals) >= 3:
                names = [v.strip('"\'').lower() for v in vals]
            pos = f.tell()
            head.append(s)
        else:
            raise ValueError('No hay datos numéricos')
        f.seek(pos)
        chunks, rest = [], b''
        while True:
            blk = f.read(64 << 20)
            if not blk:
                break
            blk = rest + blk
            cut = blk.rfind(b'\n')
            if cut < 0:
                rest = blk
                continue
            rest = blk[cut + 1:]
            chunks.append(_parse_block(blk[:cut], ncol))
        if rest.strip():
            chunks.append(_parse_block(rest, ncol))
    a = np.concatenate(chunks)
    return _columns(a, names)


def _parse_block(b, ncol):
    b = b.replace(b',', b' ').replace(b';', b' ').replace(b'\t', b' ').replace(b'\r', b' ')
    v = np.array(b.split(), dtype=np.float64)   # más rápido que loadtxt y sin avisos de numpy
    m = len(v) // ncol
    return v[:m * ncol].reshape(m, ncol)


def _columns(a, names):
    n = a.shape[1]
    xyz, rgb, nrm = a[:, :3], None, None
    if names and len(names) == n:
        def find(*cands):
            for c in cands:
                if all(x in names for x in c):
                    return [names.index(x) for x in c]
            return None
        ix = find(('x', 'y', 'z'), ('//x', 'y', 'z'))
        if ix:
            xyz = a[:, ix]
        ic = find(('r', 'g', 'b'), ('red', 'green', 'blue'))
        if ic:
            rgb = a[:, ic]
        inn = find(('nx', 'ny', 'nz'))
        if inn:
            nrm = a[:, inn]
    else:
        rest = a[:, 3:]

        def is_rgb(c):
            return c.shape[1] == 3 and c.min() >= 0 and c.max() <= 255 and c.max() > 1.5 and \
                np.allclose(c[:1000], np.round(c[:1000]))

        def is_nrm(c):
            return c.shape[1] == 3 and np.allclose(np.linalg.norm(c[:1000], axis=1), 1, atol=0.05)
        if n == 6:
            rgb, nrm = (rest, None) if is_rgb(rest) else (None, rest if is_nrm(rest) else None)
        elif n == 7:                                    # x y z intensidad r g b (PTS)
            rgb = rest[:, 1:] if is_rgb(rest[:, 1:]) else None
        elif n >= 9:
            a1, a2 = rest[:, :3], rest[:, 3:6]
            if is_rgb(a1) and is_nrm(a2):
                rgb, nrm = a1, a2
            elif is_nrm(a1) and is_rgb(a2):
                rgb, nrm = a2, a1
            elif n == 10 and is_rgb(rest[:, 1:4]):
                rgb = rest[:, 1:4]
    return dict(xyz=xyz, big=True, rgb=None if rgb is None else rgb.round().clip(0, 255).astype(np.uint8),
                nrm=None if nrm is None else nrm.astype(np.float32))


def _las(path):
    try:
        import laspy
    except ImportError:
        raise RuntimeError('Para leer LAS/LAZ hace falta instalar laspy (y lazrs para LAZ): '
                           'pip install "laspy[lazrs]"')
    las = laspy.read(path)
    xyz = np.c_[las.x, las.y, las.z]
    rgb = None
    if all(hasattr(las, c) for c in ('red', 'green', 'blue')):
        c = np.c_[las.red, las.green, las.blue].astype(np.float32)
        if c.max() > 0:
            rgb = (c / 256 if c.max() > 255 else c).round().clip(0, 255).astype(np.uint8)
    cls = np.asarray(las.classification, np.uint8) if hasattr(las, 'classification') else None
    return dict(xyz=xyz, rgb=rgb, nrm=None, big=True, cls=cls)


def _e57(path):
    try:
        import pye57
    except ImportError:
        raise RuntimeError('Para leer E57 hace falta instalar pye57: pip install pye57')
    e = pye57.E57(path)
    P, C = [], []
    for i in range(e.scan_count):
        d = e.read_scan(i, colors=True, ignore_missing_fields=True, transform=True)
        P.append(np.c_[d['cartesianX'], d['cartesianY'], d['cartesianZ']])
        if 'colorRed' in d and len(d['colorRed']):
            C.append(np.c_[d['colorRed'], d['colorGreen'], d['colorBlue']])
    rgb = np.concatenate(C).clip(0, 255).astype(np.uint8) if len(C) == len(P) and C else None
    return dict(xyz=np.concatenate(P), rgb=rgb, nrm=None, big=True)


READERS = {'.ply': _ply, '.stl': _stl, '.obj': _obj, '.las': _las, '.laz': _las, '.e57': _e57,
           '.xyz': _text, '.pts': _text, '.txt': _text, '.csv': _text, '.asc': _text}


def read_any(path, log=lambda s: None):
    """Lee cualquier formato admitido -> (xyz float32, rgb uint8|None, nrm float32|None, tipo 'nube'|'malla')."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in READERS:
        raise ValueError(f'Formato no admitido: {ext or "(sin extensión)"}. Admitidos: {", ".join(EXTS)}')
    d = READERS[ext](path)
    read_any.units = None                     # unidades que trae escritas el fichero (nube unida guardada: mm)
    if ext == '.ply':
        with open(path, 'rb') as f:
            head = f.read(4096).split(b'end_header')[0]
        if b'comment nube2stl unidades: mm' in head:
            read_any.units = 'mm'
    read_any.origin = None                    # origen restado a las coordenadas georreferenciadas (unidades del fichero)
    read_any.classes = None                   # clase de cada punto (LAS), o None
    read_any.tris = 0                         # triángulos de la malla leída (para la estimación de memoria)
    if 'T' in d:                              # triángulos sueltos (STL)
        T = d['T']
        c = np.floor(np.median(T[:: max(1, len(T) // 100_000), 0].astype(np.float64), 0)) if len(T) else np.zeros(3)
        if np.abs(c).max() > 1e4:
            log(f'Coordenadas grandes (georreferenciadas): se resta el origen {c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f}')
            read_any.origin = c
            for i in range(0, len(T), CHUNK_TRIS):          # en float64 por bloques y de vuelta a float32
                T[i:i + CHUNK_TRIS] = (T[i:i + CHUNK_TRIS].astype(np.float64) - c).astype(np.float32)
        read_any.tris = len(T)
        P, rgb, nrm = sample_mesh(None, None, tris=T)
        log(f'Malla de {len(T):,} triángulos: {len(P):,} puntos repartidos por su superficie (con las normales de '
            f'la malla)')
        return P, rgb, nrm, 'malla'
    if 'F' in d:
        V, F = np.asarray(d['V'], np.float64), d['F']
        # malla georreferenciada (UTM en metros, típica de fotogrametría): restar el centro antes de pasar a float32,
        # como con las nubes; si no, a 4 500 000 m la precisión de float32 es de medio metro
        c = np.floor(np.median(V[:: max(1, len(V) // 100_000)], 0))
        if np.abs(c).max() > 1e4:
            log(f'Coordenadas grandes (georreferenciadas): se resta el origen {c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f}')
            V = V - c
            read_any.origin = c
        read_any.tris = len(F)
        P, rgb, nrm = sample_mesh(V, F, d.get('vcol'), d.get('uvcol'))
        log(f'Malla de {len(V):,} vértices y {len(F):,} triángulos: {len(P):,} puntos repartidos por su superficie'
            f' (con las normales de la malla' + (', y su color)' if rgb is not None else ')'))
        if d.get('note'):
            log(f'OBJ: {d["note"]}')
        return P, rgb, nrm, 'malla'
    xyz = d['xyz']
    if d.get('big'):
        # coordenadas georreferenciadas (LAS, E57): restar el centro antes de pasar a float32 para no perder precisión
        c = np.floor(np.median(xyz[:: max(1, len(xyz) // 100_000)], 0))
        if np.abs(c).max() > 1e4:
            log(f'Coordenadas grandes (georreferenciadas): se resta el origen {c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f}')
            xyz = xyz - c
            read_any.origin = c
    xyz = np.ascontiguousarray(xyz, np.float32)
    ok = np.isfinite(xyz).all(1)
    if not ok.all():
        log(f'Quitados {(~ok).sum():,} puntos sin coordenadas válidas')
        xyz = xyz[ok]
        d['rgb'] = None if d.get('rgb') is None else d['rgb'][ok]
        d['nrm'] = None if d.get('nrm') is None else d['nrm'][ok]
        d['cls'] = None if d.get('cls') is None else d['cls'][ok]
    read_any.classes = d.get('cls')
    return xyz, d.get('rgb'), d.get('nrm'), 'nube'


# ------------------------------------------------------------------ clases del LiDAR (LAS)
# clases ASPRS habituales (PNOA y la mayoría de vuelos)
LAS_CLASSES = {0: 'sin clasificar', 1: 'sin clasificar', 2: 'suelo', 3: 'vegetación baja', 4: 'vegetación media',
               5: 'vegetación alta', 6: 'edificios', 7: 'ruido', 8: 'puntos clave', 9: 'agua', 10: 'vías férreas',
               11: 'carreteras', 12: 'solape', 13: 'cables', 14: 'cables', 15: 'torres', 16: 'conectores',
               17: 'puentes', 18: 'ruido alto', 19: 'solape', 20: 'solape', 21: 'nieve', 22: 'superficies temporales'}
NOISE = (7, 18)
GROUND_AUTO = 99                              # «suelo calculado»: el fichero no trae clases y se busca el suelo


def class_name(c):
    return 'suelo calculado' if c == GROUND_AUTO else LAS_CLASSES.get(int(c), f'clase {int(c)}')


def ground_mask(xyz, log=lambda s: None):
    """Suelo de una nube aérea sin clasificar (mm): filtro morfológico progresivo (Zhang et al. 2003). Se toma el
    punto más bajo de cada celda y se «abre» con ventanas cada vez mayores: lo que sobresale más de lo que permite
    la pendiente del terreno (árboles, edificios) no es suelo. -> máscara de los puntos de suelo."""
    from scipy import ndimage
    q = xyz[:: max(1, len(xyz) // 2_000_000)]
    ext = np.ptp(q[:, :2], 0)
    cell = max(float(np.sqrt(ext[0] * ext[1] / max(len(xyz), 1))) * 2.0, 250.0)   # ~4 puntos por celda, ≥ 25 cm
    lo = xyz[:, :2].min(0)
    ij = np.floor((xyz[:, :2] - lo) / cell).astype(np.int64)
    nx, ny = ij.max(0) + 1
    flat = ij[:, 1] * nx + ij[:, 0]
    zmin = np.full(nx * ny, np.inf)
    np.minimum.at(zmin, flat, xyz[:, 2].astype(np.float64))
    zmin = zmin.reshape(ny, nx)
    empty = ~np.isfinite(zmin)
    if empty.any():
        idx = ndimage.distance_transform_edt(empty, return_distances=False, return_indices=True)
        zmin = zmin[idx[0], idx[1]]
    surf = zmin.copy()
    slope, dh0, dhmax = 0.3, 300.0, 3000.0          # pendiente admitida (30 %), 30 cm, 3 m (en mm)
    w_prev, w = 1, 3
    while w * cell < 60_000:                        # ventanas hasta 60 m (edificios grandes)
        op = ndimage.grey_opening(surf, size=(w, w))
        dh = min(dh0 + slope * (w - w_prev) * cell, dhmax)
        surf = np.where(surf - op > dh, op, surf)
        w_prev, w = w, 2 * w - 1
    zs = ndimage.uniform_filter(surf, 3)            # un poco más suave, para los puntos en pendiente
    g = xyz[:, 2] - zs.ravel()[flat] < max(dh0, 0.3 * cell)
    log(f'Suelo calculado (filtro morfológico, celdas de {cell / 1000:.2f} m): {g.mean() * 100:.0f} % de los puntos')
    return g


def class_counts(cls):
    """{clase: número de puntos}, de más a menos."""
    v, n = np.unique(cls, return_counts=True)
    return {int(a): int(b) for a, b in sorted(zip(v, n), key=lambda t: -t[1])}


# ------------------------------------------------------------------ unidades
def guess_units(xyz):
    q = xyz[:: max(1, len(xyz) // 200_000)]
    ext = float((np.percentile(q, 99.9, 0) - np.percentile(q, 0.1, 0)).max())
    return ('m', ext) if ext < 20 else ('mm', ext)


def to_mm(xyz, units, log=lambda s: None):
    """Pasa la nube a milímetros. Devuelve (xyz, unidad usada, factor)."""
    if units not in UNITS:
        u, ext = guess_units(xyz)
        how = f'automáticas: la nube mide {ext:.3g} de lado'
    else:
        u, how = units, 'elegidas'
    f = UNITS[u]
    if f != 1:
        xyz *= np.float32(f)
    log(f'Unidades de la nube: {u} ({how})' + (f' → se pasa a mm (× {f:g})' if f != 1 else ''))
    return xyz, u, f


# ------------------------------------------------------------------ limpieza
def part_cell(xyz):
    """Lado de los vóxeles con los que se buscan las partes separadas de una nube: ~1/300 de su tamaño, o 2,5 veces
    la separación entre puntos si es mayor."""
    n = len(xyz)
    q = xyz[:: max(1, n // 500_000)]
    lo, hi = np.percentile(q, 0.05, 0), np.percentile(q, 99.95, 0)
    g = float((hi - lo).max()) / 300
    # nubes poco densas: la rejilla no puede ser más fina que la separación entre puntos (si no, la propia
    # superficie se parte en trozos y se quitarían partes reales). Separación ≈ √(área / puntos), con el área
    # estimada por los vóxeles ocupados en una rejilla gruesa (una superficie corta ~1,5 vóxeles por r²).
    r = float((hi - lo).max()) / 150
    occ = len(np.unique(np.floor((q - lo) / r).astype(np.int64), axis=0))
    spacing = np.sqrt(occ * r * r / 1.5 / n)
    return max(g, 2.5 * spacing)


def part_labels(xyz, g):
    """Partes conectadas de la nube, uniendo vóxeles de lado g vecinos (también en diagonal, y con un vóxel de
    margen). -> (uk, inv, cnt, vl, k, g): vóxeles ocupados, vóxel de cada punto, puntos por vóxel, parte de cada
    vóxel ocupado (1…k) y el lado usado (más grueso si la nube tiene puntos muy lejanos)."""
    n = len(xyz)
    lo = xyz.min(0)
    dims = np.floor((xyz.max(0) - lo) / g).astype(np.int64) + 1
    if np.prod(dims) > 30_000_000:                     # nube con puntos muy lejanos: rejilla más gruesa
        g *= np.cbrt(np.prod(dims) / 30_000_000)
        dims = np.floor((xyz.max(0) - lo) / g).astype(np.int64) + 1
    key = np.empty(n, np.int64)
    for i in range(0, n, 4_000_000):
        b = np.floor((xyz[i:i + 4_000_000] - lo) / g).astype(np.int64)
        key[i:i + 4_000_000] = (b[:, 0] * dims[1] + b[:, 1]) * dims[2] + b[:, 2]
    uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    del key
    occ = np.zeros(int(np.prod(dims)), bool)
    occ[uk] = True
    occ = occ.reshape(dims)
    lab, k = ndimage.label(ndimage.binary_dilation(occ, np.ones((3, 3, 3), bool)), np.ones((3, 3, 3), int))
    return uk, inv.ravel(), cnt, lab.ravel()[uk], k, g


def part_mask(xyz, point):
    """Puntos de la parte conectada que contiene (o tiene más cerca) point: una pieza separada del resto."""
    if len(xyz) < 10:
        return np.ones(len(xyz), bool)
    _, inv, _, vl, k, _ = part_labels(xyz, part_cell(xyz))
    pl = vl[inv]
    from scipy.spatial import cKDTree
    _, j = cKDTree(xyz[:: max(1, len(xyz) // 2_000_000)]).query(np.asarray(point, np.float64))
    return pl == pl[j * max(1, len(xyz) // 2_000_000)]


def clean_mask(xyz, log=lambda s: None, min_frac=0.02, keep_groups=(), info=None):
    """Máscara de los puntos que se quedan: los de las partes conectadas con al menos min_frac de los puntos de la
    mayor (en una rejilla de vóxeles de ~1/300 del tamaño de la nube, o 2,5 veces la separación entre puntos si es
    mayor, uniendo vóxeles vecinos).
    keep_groups: centros (mm) de grupos sueltos que se conservan igualmente (los que el usuario marca en la vista).
    info: si es un dict, se rellena con los grupos sueltos: 'groups' (m,3) centros, 'group_n' puntos de cada uno,
    'loose_idx' índices de sus puntos, 'loose_grp' grupo de cada uno y 'group_tol' (tolerancia para reconocerlos)."""
    n = len(xyz)
    if n < 1000:
        return np.ones(n, bool)
    uk, inv, cnt, vl, k, g = part_labels(xyz, part_cell(xyz))
    if k <= 1:
        log('Puntos sueltos: no hay grupos de puntos separados de la pieza')
        return np.ones(n, bool)
    pts = np.bincount(vl, cnt, k + 1)
    keep_l = pts >= min_frac * pts.max()
    keep_l[0] = False
    loose_l = np.flatnonzero(~keep_l[1:]) + 1             # etiquetas de los grupos sueltos
    if len(loose_l):
        pl = vl[inv]                                      # parte de cada punto
        idx = np.flatnonzero(~keep_l[pl])
        lp = pl[idx]
        cen = np.stack([np.bincount(lp, xyz[idx, a].astype(np.float64), k + 1) for a in range(3)], 1)
        cen = cen[loose_l] / pts[loose_l, None]
        kept = np.zeros(len(loose_l), bool)
        if len(keep_groups):
            from scipy.spatial import cKDTree
            d, _ = cKDTree(np.asarray(keep_groups, np.float64).reshape(-1, 3)).query(cen)
            kept = d <= g
            keep_l[loose_l[kept]] = True
        if info is not None:
            pos = np.full(k + 1, -1, np.int64)
            pos[loose_l] = np.arange(len(loose_l))
            info.update(groups=cen, group_n=pts[loose_l].astype(np.int64), loose_idx=idx, loose_grp=pos[lp],
                        group_tol=g)
        if kept.any():
            log(f'Se conservan {int(kept.sum())} grupos sueltos elegidos ({int(pts[loose_l[kept]].sum()):,} puntos)')
    keep = keep_l[vl][inv]
    rm = n - int(keep.sum())
    if rm:
        log(f'Puntos sueltos: {rm:,} ({100 * rm / n:.2f} %) en {int((~keep_l[1:]).sum())} '
            f'grupos separados de la pieza')
    else:
        log('Puntos sueltos: no hay grupos de puntos separados de la pieza')
    return keep


# ------------------------------------------------------------------ recorte
def crop_mask(xyz, crops):
    """Máscara de los puntos que quedan tras los recortes hechos en la vista de la nube. Cada recorte es un dict:
    'M' matriz 4×4 (16 números, por filas) que lleva los puntos (mm) a la pantalla de aquel momento (coordenadas
    normalizadas −1…1), 'poly' el polígono dibujado en esa pantalla [[x, y], …] y 'inside' True = quedarse con lo
    de dentro (False = quitar lo de dentro). O bien 'part' [x, y, z]: la parte conectada con ese punto (clic en
    una pieza separada), entre lo que han dejado los recortes anteriores. O 'box' [x0, x1, y0, y1, z0, z1]: una caja
    con medidas en mm de la nube (se repite igual en otra nube con las mismas coordenadas). Se aplican en orden."""
    from matplotlib.path import Path
    keep = np.ones(len(xyz), bool)
    for c in crops or ():
        if 'box' in c:                        # caja con medidas: x0, x1, y0, y1, z0, z1 (mm de la nube)
            b = np.asarray(c['box'], np.float64).reshape(3, 2)
            inside = np.ones(len(xyz), bool)
            for a in range(3):
                inside &= (xyz[:, a] >= b[a, 0]) & (xyz[:, a] <= b[a, 1])
            keep &= inside if c.get('inside', True) else ~inside
            continue
        if 'part' in c:                       # una parte (clic): la conectada con ese punto, entre lo que queda
            idx = np.flatnonzero(keep)
            if len(idx):
                inside = part_mask(xyz[idx], c['part'])
                keep[idx] = inside if c.get('inside', True) else ~inside
            continue
        M = np.asarray(c['M'], np.float64).reshape(4, 4)
        path = Path(np.asarray(c['poly'], np.float64))
        lo, hi = np.asarray(c['poly']).min(0), np.asarray(c['poly']).max(0)
        for i in range(0, len(xyz), 2_000_000):
            q = xyz[i:i + 2_000_000].astype(np.float64)
            h = q @ M[:3, :3].T + M[:3, 3]
            w = q @ M[3, :3] + M[3, 3]
            ok = w > 1e-12
            x = np.where(ok, h[:, 0] / np.where(ok, w, 1), 9.0)
            y = np.where(ok, h[:, 1] / np.where(ok, w, 1), 9.0)
            inside = ok & (x >= lo[0]) & (x <= hi[0]) & (y >= lo[1]) & (y <= hi[1])
            j = np.flatnonzero(inside)
            if len(j):
                inside[j] = path.contains_points(np.c_[x[j], y[j]])
            keep[i:i + 2_000_000] &= inside if c.get('inside', True) else ~inside
    return keep


def units_doubt(info, ext):
    """¿Merece la pena preguntar las unidades supuestas? Tomada por metros (mide menos de 20), una pieza de menos de
    50 mm, o una malla de fotogrametría de 20 a 5000 «mm» (suelen venir en metros: un acueducto de 37 m quedaba en
    37 mm)."""
    if not info.get('guessed'):
        return False
    big = float(max(ext))
    return info.get('units') == 'm' or big < 50 or bool(units_hint(info, ext))


def units_hint(info, ext):
    """Aviso si las unidades supuestas pueden estar mal: las mallas de fotogrametría (OBJ, PLY con caras…) suelen
    venir en metros aunque midan más de 20 de lado."""
    big = float(max(ext))
    if info.get('guessed') and info.get('units') == 'mm' and info.get('kind') == 'malla' and 20 <= big < 5000:
        return (f'las mallas de fotogrametría suelen venir en metros: si el original mide {big:.0f} m y no '
                f'{big:.0f} mm, elige «m»')
    return ''


# ------------------------------------------------------------------ todo junto
def load_points(path, prm=None, log=lambda s: None, mark_only=False):
    """Lee la nube (cualquier formato), la pasa a mm y la limpia. -> (xyz, rgb, nrm, info).
    mark_only: no quita nada; busca los grupos sueltos y devuelve su máscara en info['keep'] (None si no hay) y los
    grupos (ver clean_mask), para que la ventana enseñe la nube entera con lo que se quitaría marcado."""
    xyz, rgb, nrm, kind = read_any(path, log)
    origin = read_any.origin
    cls = read_any.classes
    units = getattr(prm, 'units', 'auto') if prm is not None else 'auto'
    if units not in UNITS and getattr(read_any, 'units', None):
        units = read_any.units
        log(f'Unidades escritas en el fichero: {units}')
    xyz, u, f = to_mm(xyz, units, log)
    info = {'kind': kind, 'mesh_tris': int(getattr(read_any, 'tris', 0) or 0), 'units': u, 'factor': f, 'removed': 0, 'guessed': units not in UNITS, 'keep': None,
            'origin_mm': [float(x) * f for x in origin] if origin is not None else [0.0, 0.0, 0.0]}
    xyz, rgb, nrm = _filter_classes(xyz, rgb, nrm, cls, prm, info, log)
    merges = getattr(prm, 'merge', None) if prm is not None else None
    if merges:                                # otras nubes unidas a esta (unir.py), antes de limpiar y recortar
        import unir
        info['n_main'] = len(xyz)
        xyz, rgb, nrm = unir.merge(xyz, rgb, nrm, info, merges, path, log)
    if mark_only:
        keep = clean_mask(xyz, log, info=info)
        info['keep'] = None if 'groups' not in info else keep
        hint = units_hint(info, xyz.max(0) - xyz.min(0)) if len(xyz) else ''
        if hint:
            log('AVISO: ' + hint)
        return xyz, rgb, nrm, info
    if prm is None or getattr(prm, 'clean', True):
        keep = clean_mask(xyz, log, keep_groups=getattr(prm, 'keep_groups', None) or ())
        if not keep.all():
            info['removed'] = int((~keep).sum())
            log(f'Limpieza: se quitan esos {info["removed"]:,} puntos sueltos')
            xyz = xyz[keep]
            rgb = rgb[keep] if rgb is not None else None
            nrm = nrm[keep] if nrm is not None else None
    crops = getattr(prm, 'crop', None) if prm is not None else None
    if crops:
        keep = crop_mask(xyz, crops)
        info['cropped'] = int((~keep).sum())
        log(f'Recorte: se quitan {info["cropped"]:,} puntos ({len(crops)} recorte{"s" if len(crops) > 1 else ""} '
            f'hechos en la vista de la nube)')
        xyz = xyz[keep]
        rgb = rgb[keep] if rgb is not None else None
        nrm = nrm[keep] if nrm is not None else None
    hint = units_hint(info, xyz.max(0) - xyz.min(0)) if len(xyz) else ''
    if hint:
        log('AVISO: ' + hint)
    if unrolls(prm):
        import superficie
        xyz = superficie.unroll_surface(xyz, prm.surface, log=log)
        nrm = None
    elif getattr(prm, 'interior', 'no') != 'no' and getattr(prm, 'mode', '') == 'objeto':
        import interior
        xyz, nrm = interior.prepare(xyz, prm, log)
        rgb = None
    elif is_vessel(prm):
        import revolucion
        xyz, nrm, vinfo = revolucion.vessel(xyz, rgb=rgb, log=log)
        info['vessel'] = vinfo
        rgb = None
    return xyz, rgb, nrm, info


def _filter_classes(xyz, rgb, nrm, cls, prm, info, log):
    """LiDAR con clases: quedarse con las elegidas (Params.classes; vacío = todas menos el ruido). Sin clases en el
    fichero, «suelo calculado» (GROUND_AUTO) busca el suelo con ground_mask. Deja en info['classes'] los recuentos
    del fichero y en info['las'] si es un LAS."""
    want = [int(c) for c in (getattr(prm, 'classes', None) or [])] if prm is not None else []
    has = cls is not None and len(np.unique(cls[:: max(1, len(cls) // 1_000_000)])) > 1
    if cls is not None or GROUND_AUTO in want:
        info['las'] = True
    if has:
        info['classes'] = class_counts(cls)
        log('Clases del LiDAR: ' + ' · '.join(f'{class_name(c)} ({c}) {n:,}' for c, n in info['classes'].items()))
    keep = None
    if has and want and GROUND_AUTO not in want:
        keep = np.isin(cls, want)
        how = ', '.join(class_name(c) for c in want)
    elif has:
        noise = np.isin(cls, NOISE)
        if noise.any():
            keep = ~noise
            how = 'todas menos el ruido'
    if GROUND_AUTO in want:
        g = ground_mask(xyz, log)
        keep = g if keep is None else keep & g
        how = 'suelo calculado'
    if keep is not None and not keep.all():
        log(f'Clases: {how} → se quedan {keep.sum():,} de {len(keep):,} puntos')
        xyz = xyz[keep]
        rgb = rgb[keep] if rgb is not None else None
        nrm = nrm[keep] if nrm is not None else None
        if not len(xyz):
            raise ValueError('Ningún punto de las clases elegidas: elige otras.')
    return xyz, rgb, nrm


def resolve_reverse(path, main_path=None):
    """Ruta del reverso; si no está (carpeta movida), la busca junto a la nube principal."""
    if os.path.exists(path) or not main_path:
        return path
    alt = os.path.join(os.path.dirname(main_path), os.path.basename(path))
    return alt if os.path.exists(alt) else path


def unrolls(prm):
    """Hay que desenrollar la nube: superficie cilíndrica en los modos que miran una cara."""
    return (prm is not None and getattr(prm, 'surface', 'plano') != 'plano'
            and getattr(prm, 'mode', 'alturas') != 'objeto')


def is_vessel(prm):
    """Vasija a partir de un fragmento: método objeto con superficie de revolución."""
    return (prm is not None and getattr(prm, 'surface', 'plano') == 'revolucion'
            and getattr(prm, 'mode', 'alturas') == 'objeto')
