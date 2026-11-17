"""Núcleo de nube2stl: nube de puntos PLY de un relieve -> malla cerrada (mapa de alturas + base) -> STL/OBJ/PLY.

Unidades: los parámetros de lectura/limpieza están en unidades de la nube (normalmente mm reales);
los de la pieza (base, marco, lado final) están en mm de la pieza impresa.
"""
from dataclasses import dataclass, asdict, field, fields
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from scipy import ndimage
from scipy.interpolate import griddata
from scipy.spatial import ConvexHull

PLY_TYPES = {'char': 'i1', 'int8': 'i1', 'uchar': 'u1', 'uint8': 'u1', 'short': 'i2', 'int16': 'i2',
             'ushort': 'u2', 'uint16': 'u2', 'int': 'i4', 'int32': 'i4', 'uint': 'u4', 'uint32': 'u4',
             'float': 'f4', 'float32': 'f4', 'double': 'f8', 'float64': 'f8'}
PLA_DENSITY = 1.24  # g/cm³
def _available_cpus():
    """Procesadores que este programa puede usar de verdad: respeta la afinidad (taskset, cpuset de contenedores)
    y el límite de CPU de cgroups (docker --cpus, systemd). Devuelve (número, conjunto de ids o None)."""
    ids = None
    try:
        ids = os.sched_getaffinity(0)             # Linux
        n = len(ids)
    except (AttributeError, OSError):
        n = os.cpu_count() or 1                   # Windows / macOS
    try:                                          # cgroups v2: "max 100000" o "200000 100000" (= 2 CPU)
        quota, period = open('/sys/fs/cgroup/cpu.max').read().split()[:2]
        if quota != 'max':
            n = max(1, min(n, int(-(-int(quota) // int(period)))))
    except (OSError, ValueError):
        pass
    return max(1, n), ids


NCPU, _CPU_IDS = _available_cpus()
FROZEN = bool(getattr(sys, 'frozen', False))       # ejecutable hecho con PyInstaller
HERE = getattr(sys, '_MEIPASS', None) if FROZEN else os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)   # Windows: que los procesos auxiliares no abran consola


def preload_pymeshlab_qt():
    """Solo en el ejecutable y en procesos sin interfaz: carga primero las librerías Qt que trae PyMeshLab.
    En el paquete, el cargador del sistema encontraría antes las de PyQt5 (otra versión de Qt) y PyMeshLab no
    arrancaría; una librería ya cargada se reutiliza por su nombre, así que basta con cargarlas antes."""
    if not FROZEN:
        return
    import ctypes
    import glob
    base = os.path.join(HERE, 'pymeshlab')
    pend = [f for pat in ('*Qt5*.so*', '*Qt5*.dll', '*icu*.so*', '*icu*.dll')
            for d in (base, os.path.join(base, 'lib')) for f in glob.glob(os.path.join(d, pat))]
    order = ['icu', 'Core', 'DBus', 'Gui', 'Network', 'Xml', 'Widgets', 'OpenGL', 'Svg']   # dependencias primero
    pend.sort(key=lambda f: (next((i for i, k in enumerate(order) if k in os.path.basename(f)), len(order)), f))
    mode = getattr(ctypes, 'RTLD_GLOBAL', 0)
    for _ in range(4):                                  # varias pasadas por si el orden de dependencias no cuadra
        left = []
        for f in pend:
            try:
                ctypes.CDLL(f, mode=mode)
            except OSError:
                left.append(f)
        if not left or len(left) == len(pend):
            break
        pend = left


def helper_cmd(name, *args):
    """Orden para lanzar un proceso auxiliar ('proceso' o 'decimar').
    Desde el código fuente: el mismo Python con el script. En el ejecutable: el ejecutable de consola
    (nube2stl-consola) con --proceso / --decimar, porque ahí no hay un Python aparte."""
    if FROZEN:
        d = os.path.dirname(sys.executable)
        exe = os.path.join(d, 'nube2stl-consola' + ('.exe' if sys.platform == 'win32' else ''))
        return [exe if os.path.exists(exe) else sys.executable, f'--{name}', *args]
    return [sys.executable, '-u', os.path.join(HERE, f'{name}.py'), *args]


def _physical_cores():
    """Núcleos físicos (sin contar Hyper-Threading) entre los procesadores disponibles."""
    try:
        base = '/sys/devices/system/cpu'
        cores = set()
        for d in os.listdir(base):
            if d.startswith('cpu') and d[3:].isdigit() and (_CPU_IDS is None or int(d[3:]) in _CPU_IDS):
                t = os.path.join(base, d, 'topology')
                cores.add((open(os.path.join(t, 'physical_package_id')).read().strip(),
                           open(os.path.join(t, 'core_id')).read().strip()))
        if cores:
            return min(len(cores), NCPU)
    except OSError:
        pass
    try:                                          # Windows / macOS, si está psutil
        import psutil
        return max(1, min(psutil.cpu_count(logical=False) or NCPU, NCPU))
    except ImportError:
        return NCPU


NCORES = _physical_cores()


def threads_for(prm, kind):
    """Procesadores a usar. Automático (0): todos los lógicos para ordenar y simplificar; los núcleos físicos
    para Poisson (medido: con Hyper-Threading, Poisson va más lento con todos los hilos lógicos)."""
    if prm is not None and prm.workers > 0:
        return min(prm.workers, NCPU)
    return NCORES if kind == 'poisson' else NCPU


# ------------------------------------------------------------------ tamaño de los ficheros
def file_sizes(nverts, ntris, color=False):
    """Tamaño en bytes de cada formato (medido: STL exacto, el resto con error < 3 %)."""
    digits = len(str(max(1, nverts)))
    return {'STL': 84 + 50 * ntris,
            'STL ASCII': int(227.2 * ntris),
            'OBJ': int(nverts * (25 + (18 if color else 0)) + ntris * (3 * (digits + 1) + 2)),
            'PLY': 250 + nverts * (15 if color else 12) + 13 * ntris,
            '3MF': 1200 + int(12.5 * ntris)}          # comprimido: 11–14 bytes por triángulo


def fmt_bytes(b):
    return f'{b / 1e6:,.1f} MB' if b >= 1e6 else f'{b / 1e3:,.0f} kB'


def sizes_text(nverts, ntris, color=False, only=None):
    sz = file_sizes(nverts, ntris, color)
    keys = only or ['STL', 'PLY', 'OBJ']
    return ' · '.join(f'{k} ≈ {fmt_bytes(sz[k])}' for k in keys)


# ------------------------------------------------------------------ lectura
def read_ply_points(path, normals=False):
    """Devuelve (xyz (N,3) float32, rgb (N,3) uint8 o None). Soporta binario LE/BE y ASCII.
    Con normals=True devuelve además las normales del fichero (nx, ny, nz) o None si no las trae."""
    with open(path, 'rb') as f:
        if f.readline().strip() != b'ply':
            raise ValueError('No es un fichero PLY')
        fmt, elements, cur = None, [], None
        while True:
            line = f.readline()
            if not line:
                raise ValueError('Cabecera PLY incompleta')
            w = line.decode('ascii', 'replace').split()
            if not w or w[0] in ('comment', 'obj_info'):
                continue
            if w[0] == 'format':
                fmt = w[1]
            elif w[0] == 'element':
                cur = [w[1], int(w[2]), []]
                elements.append(cur)
            elif w[0] == 'property':
                if w[1] == 'list':
                    cur[2].append((w[4], 'list', w[2], w[3]))
                else:
                    cur[2].append((w[2], PLY_TYPES[w[1]]))
            elif w[0] == 'end_header':
                break
        if not elements or elements[0][0] != 'vertex':
            raise ValueError('El primer elemento del PLY no es "vertex"')
        _, n, props = elements[0]
        if any(len(p) > 2 for p in props):
            raise ValueError('Propiedades lista en vértices no soportadas')
        names = [p[0] for p in props]
        rgbn = next((c for c in (('red', 'green', 'blue'), ('r', 'g', 'b'), ('diffuse_red', 'diffuse_green',
                                                                             'diffuse_blue'))
                     if all(x in names for x in c)), None)
        nrm = ('nx', 'ny', 'nz') if normals and all(c in names for c in ('nx', 'ny', 'nz')) else None
        if fmt == 'ascii':
            data = np.loadtxt(f, max_rows=n, ndmin=2)
            xyz = data[:, [names.index(c) for c in 'xyz']]
            rgb = data[:, [names.index(c) for c in rgbn]].astype(np.uint8) if rgbn else None
            out = xyz.astype(np.float32), rgb
            if normals:
                out += (data[:, [names.index(c) for c in nrm]].astype(np.float32) if nrm else None,)
            return out
        end = '<' if fmt == 'binary_little_endian' else '>'
        a = np.fromfile(f, dtype=np.dtype([(p[0], end + p[1]) for p in props]), count=n)
    xyz = np.empty((n, 3), np.float32)       # float32 basta (error < 1 µm en piezas de metros) y ahorra memoria
    for k, c in enumerate('xyz'):
        xyz[:, k] = a[c]
    rgb = np.stack([a[c] for c in rgbn], 1).astype(np.uint8) if rgbn else None
    nv = np.stack([a[c] for c in nrm], 1).astype(np.float32) if nrm else None
    del a
    return (xyz, rgb, nv) if normals else (xyz, rgb)


PREVIEW_FACTOR = 3.0


def preview_params(prm, stats):
    """Parámetros de la vista previa rápida: resolución PREVIEW_FACTOR veces más gruesa (≈ 1/9 de celdas o puntos),
    sin simplificar y sin acabados (hueco, rótulo). Misma escala, orientación y método que la definitiva."""
    import copy
    p = copy.copy(prm)
    pl = print_plan(stats, prm)
    p.res = pl['res'] * PREVIEW_FACTOR
    if has_detail(prm):                           # las zonas, también más gruesas
        p.detail_res = (prm.detail_res if prm.detail_res > 0 else pl['res_scan']) * PREVIEW_FACTOR
    p.poisson_depth = 0
    p.simplify = 100.0
    p.wall, p.label = 0.0, ''
    return p


def is_real(prm):
    """Destino «Copia sin impresora» (target 'real'): copia digital para archivo, fresado, gran formato o medir, no
    una réplica para la impresora: sin cama ni boquilla, detalle y simplificación según el escaneo y el uso. A 1:1
    salvo que se fije otra escala (ratio: 1:10, 1:50… o 0,5 = 2:1)."""
    return getattr(prm, 'target', 'imprimir') == 'real'


def real_scale(prm):
    """Escala de la copia sin impresora: 1/ratio, o 1 (tamaño real) si no se fija."""
    r = getattr(prm, 'ratio', 0)
    return 1.0 / r if r > 0 else 1.0


def scale_txt(s):
    """Escala legible: 1:1, 1:10, 2:1…"""
    if abs(s - 1) < 1e-9:
        return '1:1'
    return f'1:{1 / s:.3g}' if s < 1 else f'{s:.3g}:1'


# simplificación a tamaño real: error admisible = esta fracción del detalle (celda), según el uso
REAL_TOL = {'archivo': 0.25, 'cnc': 0.25, 'gran formato': 0.25, 'medir': 0.05}


def fmt_len(mm):
    """Longitud legible: m (desde 2 m), cm (desde 10 cm) o mm."""
    return f'{mm / 1000:.2f} m' if mm >= 2000 else f'{mm / 10:.1f} cm' if mm >= 100 else f'{mm:.1f} mm'


def fmt_dims(*mm):
    """Varias medidas en la misma unidad (la que corresponde a la mayor): «71.8 × 26.4 × 6.1 cm»."""
    big = max(mm)
    k, u = (1000, 'm') if big >= 2000 else (10, 'cm') if big >= 100 else (1, 'mm')
    return ' × '.join(f'{v / k:.{2 if u == "m" else 1}f}' for v in mm) + f' {u}'


def fmt_vol(mm3):
    """Volumen legible: m³ (desde 0,1 m³), litros (desde 1 l) o cm³."""
    return (f'{mm3 / 1e9:,.2f} m³' if mm3 >= 1e8 else f'{mm3 / 1e6:,.1f} l' if mm3 >= 1e6 else
            f'{mm3 / 1000:,.1f} cm³')


def real_base(L):
    """Grosor de base razonable para una copia sin impresora: 0,5 % del lado mayor de la pieza (mm de la pieza, ya
    escalada), entre 3 y 200 mm."""
    return float(min(200, max(3, round(0.005 * L / 5) * 5)))


def warn_thin_base(prm, L, log):
    """Aviso si, en una copia sin impresora, la base es una lámina para el tamaño de la pieza (en la ventana se
    pone proporcional al elegir el destino; desde la consola o con unos ajustes guardados, no). L: mm de la nube."""
    if not is_real(prm):
        return
    Lp = L * real_scale(prm)
    if prm.base < 0.5 * real_base(Lp):
        log(f'AVISO: base de {prm.base:g} mm bajo una pieza de {fmt_len(Lp)}: es una lámina. Para esta copia '
            f'conviene unos {real_base(Lp):g} mm (--base {real_base(Lp):g})')


def by_length(prm):
    """¿La escala la fija el lado mayor final (target_len)? No con escala 1:N fijada ni a tamaño real."""
    return prm.target_len > 0 and getattr(prm, 'ratio', 0) <= 0 and not is_real(prm)


def piece_scale(prm, L):
    """Factor nube (mm) -> pieza (mm): 1/ratio si se fija la escala 1:ratio; si no, lado mayor final / L;
    tamaño real si target_len = 0. Copia sin impresora: real_scale (1:1 si no se fija la escala)."""
    if is_real(prm):
        return real_scale(prm)
    if getattr(prm, 'ratio', 0) > 0:
        return 1.0 / prm.ratio
    return prm.target_len / L if prm.target_len > 0 and L > 0 else 1.0


# ------------------------------------------------------------------ parámetros
@dataclass
class Params:
    # orientación (unidades de la nube)
    view: str = 'auto'             # desde dónde se mira la cara: 'auto' (plano principal) | 'arriba' | 'frente' |
                                   # 'detrás' | 'izquierda' | 'derecha' | 'abajo' (ejes de la nube)
    plane: str = 'nube'            # plano de la cara (con «Mirar desde» auto): 'nube' (toda la nube) | 'cara' (solo la
                                   # cara frontal, sin laterales ni roturas) | 'puntos' (los tres de plane_pts)
    plane_pts: list = field(default_factory=list)     # tres puntos (mm de la nube) marcados en la cara
    behind: float = 0.0            # descarta puntos más atrás que esto respecto al plano medio (0 = auto, behind_mm)
    flip: bool = False             # invertir la cara frontal detectada
    mirror: bool = False           # espejar izquierda-derecha
    auto_straighten: bool = False  # girar en el plano para alinear el contorno con los ejes
    angle: float = 0.0             # giro adicional en el plano (grados, antihorario)
    surface: str = 'plano'         # superficie de la cara: 'plano' | 'cilindro' | 'cono' | 'revolucion' (se ajusta y
                                   # la cara se desenrolla; en el modo objeto, 'revolucion' = vasija a partir de un
                                   # fragmento: se gira la sección alrededor del eje, ver revolucion.py)
    detrend: float = 0.0           # grabados: quitar la forma general de la roca, las formas de más de esto (mm de la
                                   # nube); 0 = no. Solo modo alturas
    # mapa de alturas
    res: float = 0.0               # tamaño de celda (0 = automática según impresora y escaneo)
    front_pct: float = 90.0        # percentil de altura dentro de cada celda
    min_pts: int = 2               # puntos mínimos para que una celda sea "pieza"
    fill: str = 'vecino'           # relleno de huecos: 'vecino' | 'interpolado' (planos) | 'suave' (biarmónico)
    # limpieza
    despike: float = 0.0           # umbral de picos (0 = no)
    smooth: int = 3                # mediana NxN (0/1 = no)
    gauss: float = 0.0             # suavizado gaussiano, sigma en unidades de la nube (0 = no)
    edges: bool = False            # el suavizado respeta las aristas (filtro guiado en vez del gaussiano)
    floor_pct: float = 5.0         # recorte del fondo por percentil
    erode: float = 0.0             # recorta el contorno hacia dentro
    # pieza (mm de la pieza final)
    shape: str = 'contorno'        # 'contorno' | 'rectangulo'
    z_scale: float = 1.0           # exageración del relieve
    boost: float = 1.0             # realce de lo pequeño: multiplica por esto los detalles de boost_lo mm de la piedra,
                                   # cada vez menos hasta nada en boost_hi (1 = no; ver boost_small y boost_mesh)
    boost_lo: float = 0.0          # mm de la piedra (la nube): por debajo de la mitad, ruido. 0 = auto (0,1 mm en la pieza)
    boost_hi: float = 0.0          # mm de la piedra: desde aquí ya no se realza. 0 = auto (0,5 mm en la pieza)
    boost_size: float = 0.0        # mm de la piedra: radio del entorno de cada detalle. 0 = auto (3 mm en la pieza)
    negative: bool = False         # molde: invierte alturas
    base: float = 3.0              # grosor de base bajo el punto más bajo
    frame: float = 0.0             # ancho del marco perimetral (0 = sin marco)
    target_len: float = 200.0      # lado mayor final (0 = tamaño real); no cuenta si se fija la escala (ratio)
    ratio: float = 0.0             # escala 1:ratio (0 = la que dé el lado mayor final; 1 = tamaño real)
    units: str = 'auto'            # unidades de la nube: 'auto' | 'mm' | 'cm' | 'm' | 'pulgadas' (se pasa a mm)
    clean: bool = True             # al cargar, quitar grupos de puntos separados de la pieza
    keep_groups: list = field(default_factory=list)   # centros (mm) de grupos sueltos que se conservan (clic en la vista)
    crop: list = field(default_factory=list)          # recortes hechos en la vista de la nube (ver entrada.crop_mask)
    detail: list = field(default_factory=list)        # zonas de detalle (como los recortes: M, poly), ver zone_mask
    merge: list = field(default_factory=list)         # otras nubes unidas a esta: {'path', 'units', 'M'} (ver unir.py)
    reverse: list = field(default_factory=list)       # pieza de dos caras: el reverso, [{'path', 'units', 'angle'
                                                      # (None = encajar por el contorno), 'shift'}] (ver back_face)
    classes: list = field(default_factory=list)       # LiDAR (LAS): clases que se usan (vacío = todas menos el ruido;
                                                      # 99 = suelo calculado, si el fichero no trae clases)
    wall: float = 0.0              # pieza hueca: grosor de pared en mm de la pieza (0 = maciza)
    drain: float = 0.0             # diámetro de los agujeros de drenaje (mm; 0 = sin agujeros; solo si es hueca)
    label: str = ''                # rótulo grabado en la base ('|' parte líneas)
    label_size: float = 0.0        # altura de las letras del rótulo (mm; 0 = automática)
    # impresora (mm)
    nozzle: float = 0.4            # diámetro de la boquilla
    layer: float = 0.2             # altura de capa
    bed_x: float = 220.0           # ancho de la cama (0 = no comprobar)
    bed_y: float = 220.0           # fondo de la cama
    quality: str = 'alta'          # tolerancia de la simplificación automática: 'normal' | 'alta' | 'maxima'
    target: str = 'imprimir'       # destino: 'imprimir' (réplica a escala para la impresora) | 'real' (copia 1:1)
    use: str = 'archivo'           # tamaño real: 'archivo' | 'cnc' | 'gran formato' | 'medir' (simplificación)
    # método y malla
    mode: str = 'alturas'          # 'alturas' (2,5D) | '3d' (Poisson, socavados) | 'bloque' (cara y laterales)
                                   # | 'objeto' (completo, escaneado por todos los lados)
    corner: bool = False           # modo bloque: esquina, dos caras con relieve en ángulo, cada una con su mapa de
                                   # alturas (esquina.py)
    up: str = 'z'                  # modo objeto: eje de la nube que queda hacia arriba (z, -z, y, -y, x, -x)
    interior: str = 'no'           # modo objeto, escaneos desde dentro (sala, cueva): 'no' | 'corte' (maqueta sin techo
                                   # dentro de un bloque) | 'negativo' (el hueco como sólido); ver interior.py
    ceiling: float = 0.0           # interior en corte: altura del corte sobre el suelo (mm de la nube; 0 = ¾ del alto)
    cut: float = 1.0               # modo objeto: corte plano de la base, mm de la pieza sobre el punto más bajo
    ground: bool = False           # modo objeto: escaneo abierto por abajo (terreno, ruina, edificio): se cierra con
                                   # paredes verticales hasta una base plana de «base» mm bajo el punto más bajo
    denoise: float = 3.0           # modos objeto y bloque: alisado del ruido, radio en separaciones del escaneo (0 = no)
    poisson_depth: int = 0         # profundidad del octree de Poisson (0 = automática)
    detail_res: float = 0.0        # resolución de las zonas de detalle (0 = la del escaneo)
    simplify: float = 0.0          # % de triángulos a conservar (0 = automático según boquilla, 100 = no)
    workers: int = 0               # procesadores a usar (0 = automático)

    def to_json(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, s):
        d = json.loads(s)
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Result:
    verts: np.ndarray
    tris: np.ndarray
    vcolors: object                # (Nv,3) uint8 o None
    grid: np.ndarray               # alturas finales por celda (NaN fuera), mm de la pieza
    cell: float                    # tamaño de celda en la pieza (mm)
    scale: float                   # factor nube -> pieza
    angle: float                   # giro total aplicado (grados)
    bad_edges: int
    volume: float                  # mm³
    err_mean: float = 0.0          # error medio de la simplificación (mm de la pieza)
    err_max: float = 0.0           # error máximo de la simplificación (mm de la pieza)
    mode: str = 'alturas'
    err_p99: float = 0.0           # percentil 99 del error de simplificación (mm de la pieza)
    res: float = 0.0               # resolución usada (unidades de la nube)
    xform: object = None           # (4,4) transformación afín nube (mm) -> pieza (mm), para el mapa de desviación
    dev: object = None             # (Nv,) desviación de cada vértice respecto al escaneo (mm; NaN = sin datos)
    hollow: bool = False           # hueca: el volumen ya es el de la pared
    zclip: object = None           # (zmin, zmax) en la pieza: la parte de la nube que el modo representa
    corner: bool = False           # pieza de esquina (esquina.py): un mapa de alturas por cara

    @property
    def relief_depth(self):
        """Profundidad del relieve en la pieza (percentil 2–98 de las alturas, mm); en modo objeto, su altura."""
        if self.mode == 'objeto':
            return float(np.ptp(self.verts[:, 2])) if len(self.verts) else 0.0
        g = self.grid[~np.isnan(self.grid)]
        return float(np.percentile(g, 98) - np.percentile(g, 2)) if g.size else 0.0

    @property
    def size(self):
        return self.verts.max(0) - self.verts.min(0)

    @property
    def grams_pla(self):
        return self.volume / 1000 * PLA_DENSITY


# ------------------------------------------------------------------ geometría
# vistas fijas: normal de la cara (hacia el observador) y dirección «arriba» en la imagen, en ejes de la nube
VIEWS = {'arriba': ((0, 0, 1), (0, 1, 0)), 'abajo': ((0, 0, -1), (0, 1, 0)),
         'frente': ((0, -1, 0), (0, 0, 1)), 'detrás': ((0, 1, 0), (0, 0, 1)),
         'derecha': ((1, 0, 0), (0, 0, 1)), 'izquierda': ((-1, 0, 0), (0, 0, 1))}


def eff_view(prm):
    """«Mirar desde» efectivo: una nube desenrollada (superficie cilíndrica) se mira siempre desde arriba."""
    return 'arriba' if getattr(prm, 'surface', 'plano') != 'plano' else getattr(prm, 'view', 'auto')


def uses_detrend(prm):
    return getattr(prm, 'detrend', 0) > 0 and prm.mode == 'alturas'


def _frame_axes(p, prm):
    m = p.mean(0, dtype=np.float64)
    view = eff_view(prm)
    if view in VIEWS:                     # mirar desde un lado fijo (p. ej. la fachada de un edificio)
        n, v = (np.array(a, np.float64) for a in VIEWS[view])
        u = np.cross(v, n)
        if prm.flip:
            n = -n
            u = -u
        if prm.mirror:
            u = -u
        return m, u, v, n
    s = p[:: max(1, len(p) // 200000)].astype(np.float64) - m
    _, _, vt = np.linalg.svd(s, full_matrices=False)
    u, v, n = vt
    # las caras laterales/superiores del bloque forman una cola larga hacia atrás:
    # el frente es el lado con la cola extrema más corta
    lo, med, hi = np.percentile(s @ n, [0.5, 50, 99.5])
    if hi - med > med - lo:
        n = -n
    plane = getattr(prm, 'plane', 'nube')
    pts = getattr(prm, 'plane_pts', None) or []
    if plane == 'puntos' and len(pts) >= 3:
        n2 = _plane_from_points(pts)
        if n2 is not None:
            u, v, n = _with_normal(u, v, n, n2)
    elif plane == 'cara':
        u, v, n = _with_normal(u, v, n, _robust_normal(s, n))
    if prm.flip:
        n = -n
    if v[2] < 0:
        v = -v
    if np.dot(np.cross(u, v), n) < 0:
        u = -u
    if prm.mirror:
        u = -u
    return m, u, v, n


def _plane_from_points(pts):
    """Normal del plano por los puntos marcados (3 o más, ajuste por mínimos cuadrados); None si están en línea."""
    P = np.asarray(pts, np.float64)[:, :3]
    _, sv, vt = np.linalg.svd(P - P.mean(0), full_matrices=False)
    if len(sv) < 2 or sv[1] < 1e-6 * max(sv[0], 1e-12):
        return None
    return vt[-1]


def _robust_normal(s, n, iters=6):
    """Normal del plano de la cara frontal sin dejarse llevar por los laterales, las roturas o una peana: PCA
    ponderado (pesos biponderados de Tukey sobre la distancia al plano) desde el plano con más puntos
    (RANSAC), repetido hasta que se asienta."""
    s = s[:: max(1, len(s) // 100_000)]
    # arranque: el plano con más puntos a menos del 0,5 % del tamaño (RANSAC), no el medio de toda la nube; con
    # una franja más ancha gana un plano intermedio entre la cara y una peana poco inclinada
    rng = np.random.default_rng(0)
    sub = s[rng.choice(len(s), min(len(s), 20_000), replace=False)]
    ax0 = np.linalg.svd(sub - sub.mean(0), full_matrices=False)[2][0]
    thr = 0.005 * float(np.ptp(sub @ ax0))             # tamaño a lo largo de la nube (no de los ejes x, y, z)
    best = (-1, n, s.mean(0))
    for _ in range(300):
        a, b, c3 = sub[rng.choice(len(sub), 3, replace=False)]
        nn = np.cross(b - a, c3 - a)
        ln = np.linalg.norm(nn)
        if ln < 1e-9:
            continue
        nn /= ln
        r2 = ((sub - a) @ nn / thr) ** 2           # MSAC: cuenta cuánto se acercan, no solo si están dentro
        k = float(np.clip(1 - r2, 0, None).sum())  # (un plano torcido que roza cara y relieve puntúa menos)
        if k > best[0]:
            best = (k, nn if np.dot(nn, n) >= 0 else -nn, a)
    _, n, c = best
    # afinar con los puntos de ese plano (mínimos cuadrados), estrechando la franja: tres puntos al azar dan un
    # plano algo torcido y con él entrarían puntos de una peana o un lateral cerca de la arista
    band = thr
    for _ in range(4):
        q = s[np.abs((s - c) @ n) < band]
        if len(q) < 10:
            break
        c = q.mean(0)
        nn = np.linalg.eigh(np.cov((q - c).T))[1][:, 0]
        n = nn if np.dot(nn, n) >= 0 else -nn
        band = max(band / 2, 3 * 1.4826 * float(np.median(np.abs((q - c) @ n))))
    for _ in range(iters):
        d = (s - c) @ n
        near = np.abs(d) < band                    # la escala del ruido, solo con los puntos de la cara
        mad = 1.4826 * np.median(np.abs(d[near])) + 1e-9 if near.sum() > 10 else \
            1.4826 * np.median(np.abs(d - np.median(d))) + 1e-9
        r = d / (4.685 * mad)                      # biponderado de Tukey: lo lejano no cuenta nada
        w = np.where(np.abs(r) < 1, (1 - r ** 2) ** 2, 0.0)
        c = (w[:, None] * s).sum(0) / w.sum()
        q = s - c
        cov = (q * w[:, None]).T @ q
        n_new = np.linalg.eigh(cov)[1][:, 0]
        if np.dot(n_new, n) < 0:
            n_new = -n_new
        done = np.dot(n_new, n) > 1 - 1e-10
        n = n_new
        if done:
            break
    return n


def _with_normal(u, v, n, n2):
    """Ejes de la cara con la normal n2 (del lado de n), y u, v lo más parecidos a los de antes."""
    n2 = np.asarray(n2, np.float64)
    n2 = n2 / np.linalg.norm(n2)
    if np.dot(n2, n) < 0:
        n2 = -n2
    u2 = u - np.dot(u, n2) * n2
    if np.linalg.norm(u2) < 1e-6:
        u2 = v - np.dot(v, n2) * n2
    u2 /= np.linalg.norm(u2)
    v2 = np.cross(n2, u2)
    if np.dot(v2, v) < 0:
        v2 = -v2
    return u2, v2, n2


def plane_key(prm):
    """Lo que decide los ejes de la cara (para las cachés de la vista de orientación)."""
    return (eff_view(prm), getattr(prm, 'plane', 'nube'),
            tuple(tuple(round(float(x), 4) for x in q) for q in (getattr(prm, 'plane_pts', None) or [])))


BEHIND_MIN, BEHIND_FRAC = 250.0, 0.25


def behind_mm(prm, length):
    """«Profundidad máx.» en mm de la nube: la fijada o, en «auto» (0), 250 mm o la cuarta parte del lado mayor de la
    cara si es más (una lápida: 250; la fachada de un acueducto de 37 m: unos 9 m, en vez de dejar fuera la mitad)."""
    if prm.behind > 0:
        return float(prm.behind)
    return max(BEHIND_MIN, BEHIND_FRAC * float(length or 0.0))


def _project(p, prm):
    m, u, v, n = _frame_axes(p, prm)
    A = np.stack([u, v, n], 1)
    UVH = np.empty((len(p), 3), np.float32)
    for i in range(0, len(p), 2_000_000):          # por bloques para no duplicar la nube en memoria
        UVH[i:i + 2_000_000] = (p[i:i + 2_000_000] - m) @ A
    H = UVH[:, 2]
    L = max(float(np.ptp(UVH[:, 0])), float(np.ptp(UVH[:, 1]))) if len(UVH) else 0.0
    _project.behind = behind_mm(prm, L)
    keep = H > np.median(H) - _project.behind
    _project.dropped = 1 - keep.mean() if len(keep) else 0.0
    UVH = UVH[keep]
    _project.last = (m, A)
    return UVH[:, 0].copy(), UVH[:, 1].copy(), UVH[:, 2].copy(), keep


def _rotate(U, V, deg):
    if deg == 0:
        return U, V
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return U * c - V * s, U * s + V * c


def _straighten_angle(U, V, res):
    """Ángulo (grados) que alinea el rectángulo mínimo del contorno con los ejes."""
    r = max(res * 4, (np.ptp(U) + np.ptp(V)) / 800)
    iu = ((U - U.min()) / r).astype(np.int64)
    iv = ((V - V.min()) / r).astype(np.int64)
    occ = np.zeros((iv.max() + 1, iu.max() + 1), bool)
    occ[iv, iu] = True
    occ = ndimage.binary_opening(ndimage.binary_fill_holes(ndimage.binary_closing(occ, iterations=2)))
    lab, k = ndimage.label(occ)
    if k > 1:
        occ = lab == (np.argmax(ndimage.sum(occ, lab, range(1, k + 1))) + 1)
    pts = np.argwhere(occ)[:, ::-1].astype(float)
    if len(pts) < 3:
        return 0.0
    hull = pts[ConvexHull(pts).vertices]
    e = np.diff(np.vstack([hull, hull[:1]]), axis=0)
    best, best_a = np.inf, 0.0
    for a in np.arctan2(e[:, 1], e[:, 0]):
        x, y = _rotate(hull[:, 0], hull[:, 1], -np.degrees(a))
        area = np.ptp(x) * np.ptp(y)
        if area < best:
            best, best_a = area, -np.degrees(a)
    return ((best_a + 45) % 90) - 45


def _face_stats_uv(U, V, pts_per_cell=4):
    """Largo y ancho de la cara (percentiles 0,2–99,8 %) y resolución que permite el escaneo
    (~pts_per_cell puntos por celda)."""
    k = max(1, len(U) // 1_000_000)
    du = np.subtract(*np.percentile(U[::k], [99.8, 0.2]))
    dv = np.subtract(*np.percentile(V[::k], [99.8, 0.2]))
    r = 10.0 * max(1.0, (np.ptp(U) + np.ptp(V)) / 4000)
    iu = ((U - U.min()) / r).astype(np.int64)
    iv = ((V - V.min()) / r).astype(np.int64)
    area = len(np.unique(iv * (iu.max() + 1) + iu)) * r * r
    return {'length': float(max(du, dv)), 'width': float(min(du, dv)),
            'res_scan': float(np.sqrt(pts_per_cell * area / len(U)))}


def _depth_estimate(U, V, H, length, prm, ncells=150):
    """Profundidad aproximada del relieve (unidades de la nube, sin exagerar), como la medirá Result.relief_depth:
    mapa de alturas grueso (~150 celdas a lo largo), percentil del frente por celda, recorte del fondo y rango
    p2–p98. Medido con los ejemplos: un 5–15 % por encima del valor final."""
    k = max(1, len(U) // 2_000_000)
    U, V, H = U[::k], V[::k], H[::k]
    r = length / ncells
    iu = ((U - U.min()) / r).astype(np.int64)
    iv = ((V - V.min()) / r).astype(np.int64)
    key = iv * (iu.max() + 1) + iu
    o = np.lexsort((H, key))
    ks, hs = key[o], H[o]
    ends = np.r_[np.nonzero(np.diff(ks))[0], len(ks) - 1]
    starts = np.r_[0, ends[:-1] + 1]
    cnt = ends - starts + 1
    top = hs[starts + ((cnt - 1) * prm.front_pct / 100).astype(np.int64)][cnt >= 3]
    if top.size == 0:
        return 0.0
    top = np.maximum(top, np.percentile(top, prm.floor_pct))
    return float(np.percentile(top, 98) - np.percentile(top, 2))


def _coarse_grid(U, V, H, length, ncells=300):
    """Altura media por celda en una rejilla gruesa (NaN sin puntos) y su celda: para estimar al momento la
    profundidad que queda al quitar la forma de la roca (depth_detrended)."""
    k = max(1, len(U) // 3_000_000)
    U, V, H = U[::k], V[::k], H[::k]
    r = length / ncells
    iu = ((U - U.min()) / r).astype(np.int64)
    iv = ((V - V.min()) / r).astype(np.int64)
    nu, nv = int(iu.max()) + 1, int(iv.max()) + 1
    cell = iv * nu + iu
    cnt = np.bincount(cell, minlength=nu * nv)
    g = np.bincount(cell, H.astype(np.float64), minlength=nu * nv) / np.maximum(cnt, 1)
    return np.where(cnt >= 2, g, np.nan).reshape(nv, nu).astype(np.float32), float(r)


def depth_detrended(stats, prm):
    """Profundidad aproximada del relieve (nube, sin exagerar) tras quitar la forma general de «detrend» mm."""
    import superficie
    g, r = stats['coarse']
    g = g.astype(np.float64)
    mask = ~np.isnan(g)
    if mask.sum() < 20:
        return stats.get('depth', 0.0)
    g = _fill_nearest(g, mask)
    d = (g - superficie.low_surface(g, mask, superficie.detrend_sigma(prm.detrend, r)))[mask]
    d = np.maximum(d, np.percentile(d, prm.floor_pct))
    return float(np.percentile(d, 98) - np.percentile(d, 2))


def object_stats(p, pts_per_cell=4):
    """Medidas del objeto completo (modo objeto), en los ejes de la nube: extensión en x, y, z (percentiles
    0,2–99,8 %), área de la superficie y resolución que permite el escaneo (~pts_per_cell puntos por celda)."""
    k = max(1, len(p) // 2_000_000)
    q = p[::k]
    ext = np.percentile(q, 99.8, axis=0) - np.percentile(q, 0.2, axis=0)
    r = float(ext.max()) / 200
    iq = np.floor((q - q.min(0)) / r).astype(np.int64)
    n = iq.max(0) + 1
    occ = len(np.unique((iq[:, 0] * n[1] + iq[:, 1]) * n[2] + iq[:, 2]))
    area = occ * r * r / 1.5          # una superficie corta de media 1,5 vóxeles por r² de área
    return {'ext': [float(e) for e in ext], 'area': float(area),
            'res_scan': float(np.sqrt(pts_per_cell * area / len(p)))}


def face_stats(p, prm=None):
    """Medidas de la cara: largo, ancho, resolución del escaneo y profundidad del relieve (unidades de la nube).
    En 'obj', las del objeto completo (object_stats), para el modo objeto."""
    prm = prm or Params()
    U, V, H, _ = _project(p, prm)
    st = _face_stats_uv(U, V)
    st['depth'] = _depth_estimate(U, V, H, st['length'], prm)
    st['coarse'] = _coarse_grid(U, V, H, st['length'])
    st['obj'] = object_stats(p)
    # hasta dónde llega el escaneo por detrás y por delante del plano medio (laterales del bloque), sin el límite
    # de «Profundidad máx.»: lo usa el modo bloque para el grosor
    m, _, _, n = _frame_axes(p, prm)
    h = (p[:: max(1, len(p) // 1_000_000)] - m) @ n
    h = h - np.median(h)
    st['h_q'] = [float(np.percentile(h, 0.05)), float(np.percentile(h, 99.9))]
    return st


def estimate_res(p, prm=None, pts_per_cell=4):
    """Resolución que deja ~pts_per_cell puntos por celda en la cara del relieve."""
    return round(face_stats(p, prm)['res_scan'], 2)


def _nice(x):
    """Redondea a 2 cifras significativas (resoluciones legibles)."""
    if x <= 0:
        return x
    e = np.floor(np.log10(x)) - 1
    return float(np.round(x / 10 ** e) * 10 ** e)


QUALITY = {'normal': 1.0, 'alta': 0.5, 'maxima': 0.25}


def tolerance(prm, res=None):
    """Error admisible al simplificar (mm de la pieza): media boquilla o media capa (lo menor), por el factor de
    calidad. Por debajo de eso la impresora no reproduce la diferencia.
    Copia sin impresora: una fracción de la celda según el uso (REAL_TOL); res en mm de la nube, la tolerancia en
    mm de la pieza (a 1:10, la décima parte)."""
    if is_real(prm):
        return (res or prm.res or 1.0) * REAL_TOL.get(getattr(prm, 'use', 'archivo'), 0.25) * real_scale(prm)
    return min(prm.nozzle, prm.layer) / 2 * QUALITY.get(prm.quality, 0.5)


def print_plan(stats, prm):
    """Cálculo de resoluciones para imprimir. stats: face_stats(). Unidades de la nube salvo indicación.

    - Detalle imprimible: una boquilla de diámetro d traza líneas de ~d de ancho, así que el detalle más fino que
      reproduce en planta es ~d/2 (muestreo de Nyquist) -> celda en la pieza = d/2 -> en la nube = d / (2·escala).
    - Detalle del escaneo: ~4 puntos por celda.
    - Resolución automática = la mayor de las dos (afinar más solo añade triángulos que no se imprimirían).
    - Tolerancia de la simplificación = min(boquilla, capa) / 2, en mm de la pieza.
    """
    if prm.mode == 'objeto' and 'obj' in stats:
        return _print_plan_object(stats['obj'], prm)
    L, W = stats['length'], stats['width']
    s = piece_scale(prm, L)
    res_scan = stats['res_scan']
    res_print = res_scan if is_real(prm) else prm.nozzle / (2 * s)     # a tamaño real no hay boquilla
    if prm.res > 0:
        res, why = prm.res, 'manual'
    elif res_print >= res_scan and not is_real(prm):
        res, why = _nice(res_print), 'impresora'
    else:
        res, why = _nice(res_scan), 'escaneo'
    sx, sy = L * s + 2 * prm.frame, W * s + 2 * prm.frame
    fits = None
    if prm.bed_x > 0 and prm.bed_y > 0 and not is_real(prm):
        fits = (sx <= prm.bed_x and sy <= prm.bed_y) or (sx <= prm.bed_y and sy <= prm.bed_x)
    raw = depth_detrended(stats, prm) if uses_detrend(prm) and 'coarse' in stats else stats.get('depth', 0.0)
    depth = raw * s * prm.z_scale                                   # relieve en la pieza (mm)
    return {'scale': s, 'res_print': res_print, 'res_scan': res_scan, 'res': res, 'why': why,
            'cell_print': res * s, 'tol': tolerance(prm, res), 'size': (sx, sy), 'fits': fits,
            'cells': (L / res) * (W / res), 'depth': depth,
            'layers': depth / prm.layer if prm.layer > 0 else 0.0,
            'height': (min(behind_mm(prm, max(L, W)), -stats['h_q'][0]) + stats['h_q'][1]) * s
            if prm.mode == 'bloque' and 'h_q' in stats else depth + prm.base}


def _object_extents(ob, prm):
    """Extensión (ancho, fondo, alto) del objeto ya puesto con «arriba» hacia +Z (unidades de la nube). El giro
    en planta no se tiene en cuenta (la planta solo se usa para comprobar la cama)."""
    ax = {'z': (0, 1, 2), '-z': (0, 1, 2), 'y': (0, 2, 1), '-y': (0, 2, 1), 'x': (1, 2, 0), '-x': (2, 1, 0)}
    i, j, k = ax.get(prm.up, (0, 1, 2))
    e = ob['ext']
    return e[i], e[j], e[k]


def _print_plan_object(ob, prm):
    """print_plan del modo objeto: la escala la fija el lado mayor del objeto en 3D y las «celdas» son las de la
    superficie (área / resolución²), que es lo que cuesta Poisson."""
    ex, ey, ez = _object_extents(ob, prm)
    L = max(ex, ey, ez)
    s = piece_scale(prm, L)
    res_scan = ob['res_scan']
    res_print = res_scan if is_real(prm) else prm.nozzle / (2 * s)
    if prm.res > 0:
        res, why = prm.res, 'manual'
    elif res_print >= res_scan and not is_real(prm):
        res, why = _nice(res_print), 'impresora'
    else:
        res, why = _nice(res_scan), 'escaneo'
    sx, sy = ex * s, ey * s
    height = max(ez * s - max(prm.cut, 0.0), 0.0)
    fits = None
    if prm.bed_x > 0 and prm.bed_y > 0 and not is_real(prm):
        fits = (sx <= prm.bed_x and sy <= prm.bed_y) or (sx <= prm.bed_y and sy <= prm.bed_x)
    return {'scale': s, 'res_print': res_print, 'res_scan': res_scan, 'res': res, 'why': why,
            'cell_print': res * s, 'tol': tolerance(prm, res), 'size': (sx, sy), 'fits': fits,
            'cells': ob['area'] / res ** 2, 'depth': height,
            'layers': height / prm.layer if prm.layer > 0 else 0.0, 'height': height, 'object': True,
            'ext': max(ex, ey, ez)}


# ------------------------------------------------------------------ memoria
# Modelo medido con los ficheros de ejemplo (pico de memoria del proceso de reconstrucción y sus subprocesos,
# en un equipo de 32 GB): ver ayuda, sección «Memoria».
MEM_BASE_GB = 1.1                 # Python + NumPy + Open3D + PyMeshLab cargados
MEM_PER_POINT = 42e-9             # GB por punto de la nube (lectura, proyección, orden)
MEM_PER_TRI = 0.47e-6             # GB por triángulo del mapa de alturas antes de simplificar (malla, simplificación
                                  # en paralelo y medida del error)
TRIS_PER_CELL = 3.5               # triángulos por celda del rectángulo de la cara (paredes, base y bordes incluidos)
MEM_PER_POINT_3D = 110e-9         # modo 3D: GB por punto (submuestreo, normales y limpieza con Open3D)
MEM_PER_CELL_3D = {9: 2.7e-6, 10: 4.3e-6, 11: 6.9e-6, 12: 11e-6, 13: 17.6e-6}   # GB por celda según profundidad
                                  # (9 y 10 medidas; 11–13 extrapoladas ×1,6 por nivel)


def memory_available():
    """(disponible, total) en GB, o (None, None) si no se puede saber."""
    try:
        import psutil
        v = psutil.virtual_memory()
        return v.available / 2**30, v.total / 2**30
    except ImportError:
        pass
    try:
        info = dict(line.split(':', 1) for line in open('/proc/meminfo'))
        kb = lambda k: float(info[k].split()[0]) / 2**20  # noqa: E731
        return kb('MemAvailable'), kb('MemTotal')
    except (OSError, KeyError, ValueError):
        return None, None


DEPTH_MAX, DEPTH_MAX_ZONES = 11, 12   # profundidad automática máxima de Poisson (con zonas de detalle, una más:
                                      # el octree solo se afina donde hay puntos densos, en la zona)


def poisson_depth_auto(stats, prm, res, ext=None):
    """La misma profundidad automática que usan los modos 3D y objeto, a partir del tamaño de la cara (o del
    objeto, o de ext si se da). Con zonas de detalle, res es la de las zonas."""
    if ext is None:
        ext = (max(_object_extents(stats['obj'], prm)) if prm.mode == 'objeto' and 'obj' in stats
               else max(stats['length'], stats['width']))
    hi = DEPTH_MAX_ZONES if has_detail(prm) else DEPTH_MAX
    return int(np.clip(np.ceil(np.log2(ext * 1.1 / (1.25 * res))), 6, hi))


# ------------------------------------------------------------------ zonas de detalle
ZONE_MODES = ('3d', 'objeto', 'bloque')     # los que usan Poisson (el octree se afina solo donde hay puntos densos)


def has_detail(prm):
    """¿Hay zonas de detalle que se aplican? Solo en los tipos con Poisson (no en mapa de alturas ni esquina)."""
    return bool(getattr(prm, 'detail', None)) and prm.mode in ZONE_MODES and not getattr(prm, 'corner', False)


def zone_mask(xyz, zones):
    """Puntos dentro de alguna zona de detalle (cada zona es un polígono en pantalla con la matriz de la vista,
    como un recorte: entrada.crop_mask)."""
    import entrada
    m = np.zeros(len(xyz), bool)
    for z in zones or ():
        m |= entrada.crop_mask(xyz, [dict(z, inside=True)])
    return m


def zone_res(prm, res, res_scan):
    """Resolución de las zonas de detalle (unidades de la nube), o None si no son más finas que la general."""
    if not has_detail(prm):
        return None
    r = prm.detail_res if prm.detail_res > 0 else res_scan
    return r if r < 0.99 * res else None


def zoned_voxels(o3d, pc, fine, res, rf):
    """Submuestreo con dos celdas: rf dentro de las zonas (fine, máscara por punto) y res fuera."""
    if fine is None or rf is None or not fine.any():
        return pc.voxel_down_sample(res)
    a = pc.select_by_index(np.flatnonzero(fine)).voxel_down_sample(rf)
    b = pc.select_by_index(np.flatnonzero(~fine)).voxel_down_sample(res)
    return a + b


def poisson_cell(stats, prm, res):
    """(profundidad, celda) de Poisson: la celda a la que de verdad llega con esa profundidad (en piezas grandes,
    más gruesa que la resolución pedida: la profundidad automática no pasa de 11)."""
    depth = prm.poisson_depth or poisson_depth_auto(stats, prm, res)
    ext = (max(_object_extents(stats['obj'], prm)) if prm.mode == 'objeto' and 'obj' in stats
           else max(stats['length'], stats['width']))
    return depth, ext * 1.1 / 2 ** depth


def memory_estimate(stats, prm, npoints, avail=None):
    """Pico de memoria previsto para generar con estos parámetros y comparación con la memoria libre."""
    pl = print_plan(stats, prm)
    cells = pl['cells']
    if prm.mode in ('3d', 'objeto', 'bloque'):
        rf = zone_res(prm, pl['res'], pl['res_scan'])
        depth, pcell = poisson_cell(stats, prm, rf or pl['res'])
        if rf:                                  # zonas de detalle: su parte de la superficie, a la celda fina
            frac = float(stats.get('detail_frac', 0.1))
            cf = max(rf, pcell)
            cells = cells * (1 - frac) + cells * frac * (pl['res'] / cf) ** 2
        elif pcell > pl['res']:                 # en piezas grandes y finas (un acueducto a escala 1) es más gruesa
            cells *= (pl['res'] / pcell) ** 2   # que la resolución pedida, y no hay tantas celdas
        per_cell = MEM_PER_CELL_3D.get(depth) or (MEM_PER_CELL_3D[13] if depth > 13
                                                    else MEM_PER_CELL_3D[9] / 1.6 ** (9 - depth))
        if prm.mode == 'bloque':      # los laterales añaden superficie (perímetro × grosor), aprox. un 30 %
            cells *= 1.3
        gb = MEM_BASE_GB + MEM_PER_POINT_3D * npoints + per_cell * cells
        detail = f'{npoints / 1e6:.1f} M puntos, {cells / 1e6:.2f} M celdas, Poisson a profundidad {depth}'
    else:
        tris = TRIS_PER_CELL * cells
        gb = MEM_BASE_GB + MEM_PER_POINT * npoints + MEM_PER_TRI * tris
        detail = f'{npoints / 1e6:.1f} M puntos, ≈ {tris / 1e6:.1f} M triángulos antes de simplificar'
    av, total = memory_available()
    if avail is not None:
        av = avail
    if av is None:
        level = 'ok'
    elif gb > 0.9 * av:
        level = 'excede'
    elif gb > 0.65 * av:
        level = 'justo'
    else:
        level = 'ok'
    return {'gb': gb, 'avail': av or 0.0, 'total': total or 0.0, 'level': level, 'detail': detail,
            'cells': cells}


def memory_suggestions(stats, prm, npoints):
    """Parámetros que bajan el consumo: [(nombre, valor sugerido o None, explicación)]. El valor sugerido es el
    más cercano al actual que deja la estimación con margen (≤ 65 % de la memoria disponible)."""
    import copy
    est0 = memory_estimate(stats, prm, npoints)
    budget = 0.65 * est0['avail'] if est0['avail'] else est0['gb'] * 0.5
    fits = lambda q: memory_estimate(stats, q, npoints, avail=est0['avail'])['gb'] <= budget  # noqa: E731

    def search(name, values):
        for v in values:
            q = copy.copy(prm)
            setattr(q, name, v)
            if fits(q):
                return v
        return None
    out = []
    pl = print_plan(stats, prm)
    if prm.mode in ('3d', 'objeto', 'bloque'):
        if prm.mode in ('3d', 'bloque'):
                out.append(('mode', 'alturas', 'El mapa de alturas usa mucha menos memoria y tarda segundos; '
                        'a cambio rellena los socavados.'))
        d0 = prm.poisson_depth or poisson_depth_auto(stats, prm, pl['res'])
        out.append(('poisson_depth', search('poisson_depth', range(d0 - 1, 5, -1)),
                    f'Ahora {d0}{" (automática)" if not prm.poisson_depth else ""}. Cada nivel menos usa '
                    f'bastante menos memoria, con la mitad de detalle.'))
    res0 = pl['res']
    cand = sorted({_nice(res0 * f) for f in (1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 6, 8)})
    out.append(('res', search('res', [r for r in cand if r > res0]),
                f'Ahora {res0:g}'.replace('.', ',') + (' (automática)' if prm.res <= 0 else '') +
                '. Celdas mayores: menos detalle, pero al doblarla la memoria baja a la cuarta parte.'))
    if prm.target_len > 0 and prm.ratio <= 0 and pl['why'] == 'impresora' and prm.res <= 0:
        L = prm.target_len
        out.append(('target_len', search('target_len', [float(v) for v in range(int(L // 10) * 10 - 10, 9, -10)]),
                    'Pieza más pequeña: la celda sigue siendo de media boquilla, así que hay menos celdas.'))
        noz = [n for n in (0.6, 0.8, 1.0) if n > prm.nozzle]
        if noz:
            out.append(('nozzle', search('nozzle', noz),
                        'Solo si vas a imprimir con esa boquilla: el detalle imprimible es más grueso.'))
    return out


# ------------------------------------------------------------------ vista previa de la orientación
class OrientPreview:
    """Vista previa rápida de la orientación (para verla en tiempo real mientras se gira, se espeja o se pone marco).

    Al cargar se proyecta una vez una muestra de la nube sobre el plano de la cara sin invertir ni espejar. Invertir
    la cara y espejar solo cambian signos (invertir: n -> -n obliga a u -> -u; espejar: u -> -u), y el giro es una
    rotación en el plano, así que cada cambio cuesta milisegundos. Usa los mismos ejes que heightmap(), de modo que
    lo que se ve es lo que se genera (con menos resolución)."""

    def __init__(self, p, nmax=1_500_000, view='auto', prm=None):
        self.p = p
        self.q = p[:: max(1, len(p) // nmax)]      # muestra sin proyectar (vista del modo objeto)
        self._setup(prm if prm is not None else Params(view=view))   # view: el efectivo (eff_view)

    def _setup(self, prm):
        """Ejes de la cara para «Mirar desde» y el plano de prm (con la nube entera, como heightmap())."""
        self.view = eff_view(prm)
        self.key = plane_key(prm)
        base = Params(flip=False, mirror=False, view=self.view, plane=getattr(prm, 'plane', 'nube'),
                      plane_pts=list(getattr(prm, 'plane_pts', None) or []))
        self.m, self.u, self.v, self.n = _frame_axes(self.p, base)
        A = np.stack([self.u, self.v, self.n], 1)
        UVH = ((self.q - self.m) @ A).astype(np.float32)
        self.U0, self.V0, self.H0 = UVH[:, 0].copy(), UVH[:, 1].copy(), UVH[:, 2].copy()
        st = _face_stats_uv(self.U0, self.V0)
        self.length = st['length']
        self._auto = {}

    def _uvh(self, prm):
        if plane_key(prm) != self.key:
            self._setup(prm)
        U, V, H = self.U0, self.V0, self.H0
        if prm.flip != prm.mirror:            # solo uno de los dos: u cambia de signo
            U = -U
        if prm.flip:
            H = -H
        keep = H > np.median(H) - behind_mm(prm, self.length)
        return U[keep], V[keep], H[keep]

    def auto_angle(self, prm):
        """El giro de «Enderezar automáticamente» (por el contorno), en caché."""
        key = (prm.flip, prm.mirror, prm.behind, plane_key(prm))
        if key not in self._auto:
            U, V, _ = self._uvh(prm)
            self._auto[key] = _straighten_angle(U, V, self.length / 400)
        return self._auto[key]

    def total_angle(self, prm):
        if prm.mode == 'objeto':                  # en modo objeto el giro es en planta y no hay enderezado
            return prm.angle
        return prm.angle + (self.auto_angle(prm) if prm.auto_straighten else 0.0)

    def compute_object(self, prm, ncells=420):
        """Modo objeto: alzado frontal del objeto ya puesto con «arriba» hacia +Z (mirando hacia +Y), con la
        misma escala que la pieza final, en el formato de compute(); 'cut' es la altura del corte de la base y
        'dims' el ancho, fondo y alto finales (mm)."""
        import objeto3d
        P = (self.q - self.q.mean(0, dtype=np.float64)) @ objeto3d.up_matrix(prm).T
        lo, hi = np.percentile(P, 0.2, axis=0), np.percentile(P, 99.8, axis=0)
        P = P[np.all((P >= lo - 0.02 * (hi - lo)) & (P <= hi + 0.02 * (hi - lo)), axis=1)]
        # puntos sueltos (ruido del escáner): los que están solos en su vóxel
        g = float((hi - lo).max()) / 300
        key = np.floor((P - lo) / g).astype(np.int64)
        key = (key[:, 0] * 400 + key[:, 1]) * 400 + key[:, 2]
        _, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        P = P[cnt[inv] >= 2]
        lo, hi = P.min(0), P.max(0)
        ext = hi - lo
        s = piece_scale(prm, float(ext.max()))
        res = max(ext[0], ext[2]) / ncells
        iu = ((P[:, 0] - lo[0]) / res).astype(np.int64)
        iv = ((P[:, 2] - lo[2]) / res).astype(np.int64)
        nu, nv = int(iu.max()) + 1, int(iv.max()) + 1
        cell = iv * nu + iu
        depth = -(P[:, 1] - lo[1])                 # cuanto más cerca del observador (Y menor), más alto
        h = np.full(nu * nv, -np.inf)
        np.maximum.at(h, cell, depth)
        h = h.reshape(nv, nu)
        mask = np.isfinite(h)
        mask = ndimage.binary_closing(mask, iterations=1) | mask
        h = np.where(np.isfinite(h), h, np.nan)
        h = _fill_nearest(h, ~np.isnan(h))
        # celdas sin ningún punto de la cara delantera en la muestra: se vería la pared de atrás (un hoyo)
        med = ndimage.median_filter(h, size=5)
        h = np.where(h < med - 3 * res, med, h)
        h = np.where(mask, (h - np.nanmin(h)) * s, np.nan)
        cellmm = res * s
        return {'h': h, 'piece': mask, 'scan': mask, 'ring': np.zeros_like(mask), 'cell': cellmm,
                'size': (nu * cellmm, nv * cellmm), 'angle': prm.angle, 'auto': 0.0, 'frame_cells': 0,
                'object': True, 'cut': max(prm.cut, 0.0), 'dims': tuple(float(e) * s for e in ext)}

    def compute(self, prm, ncells=420):
        """Mapa de alturas grueso ya orientado, con la forma y el marco finales, en mm de la pieza.
        Devuelve dict: h (alturas con NaN fuera), piece (máscara de la pieza), ring (marco), cell (mm),
        size (ancho, alto en mm), angle (giro total), auto (giro automático)."""
        if prm.mode == 'objeto':
            return self.compute_object(prm, ncells)
        if prm.mode == 'bloque':      # sin marco, placa, molde ni exageración: como saldrá
            prm = Params(**{**asdict(prm), 'frame': 0.0, 'shape': 'contorno', 'negative': False, 'z_scale': 1.0})
        U, V, H = self._uvh(prm)
        ang = self.total_angle(prm)
        U, V = _rotate(U, V, ang)
        res = max(np.ptp(U), np.ptp(V)) / ncells
        iu = ((U - U.min()) / res).astype(np.int64)
        iv = ((V - V.min()) / res).astype(np.int64)
        nu, nv = int(iu.max()) + 1, int(iv.max()) + 1
        cell = iv * nu + iu
        cnt = np.bincount(cell, minlength=nu * nv).reshape(nv, nu)
        hs = np.bincount(cell, H.astype(np.float64), minlength=nu * nv).reshape(nv, nu)
        grid = np.where(cnt > 0, hs / np.maximum(cnt, 1), np.nan)
        mask = cnt >= 1
        mask = ndimage.binary_closing(mask, iterations=2)
        mask = ndimage.binary_fill_holes(mask)
        mask = ndimage.binary_opening(mask, iterations=1)
        lab, k = ndimage.label(mask)
        if k == 0:
            return None
        if k > 1:
            mask = lab == (np.argmax(ndimage.sum(mask, lab, range(1, k + 1))) + 1)
        if prm.erode > 0:
            mask = ndimage.distance_transform_edt(mask) > prm.erode / res
            if not mask.any():
                return None
        r0, r1 = np.flatnonzero(mask.any(1))[[0, -1]]
        c0, c1 = np.flatnonzero(mask.any(0))[[0, -1]]
        mask, grid = mask[r0:r1 + 1, c0:c1 + 1], grid[r0:r1 + 1, c0:c1 + 1]
        valid = ~np.isnan(grid)
        grid = _fill_nearest(grid, valid)
        if uses_detrend(prm):
            import superficie
            grid = grid - superficie.low_surface(grid, mask, superficie.detrend_sigma(prm.detrend, res))
        grid = np.maximum(grid, np.percentile(grid[mask], prm.floor_pct)) if prm.floor_pct > 0 else grid
        return self._piece_view(grid, mask, res, prm, ang, self.auto_angle(prm) if prm.auto_straighten else 0.0)

    @staticmethod
    def _piece_view(grid, mask, res, prm, ang, auto):
        """De la rejilla de alturas (unidades de la nube) a lo que muestra la vista: alturas en mm de la pieza con
        molde, realce, exageración, placa y marco, como _reconstruct_25d pero sin hacer la malla."""
        h = grid - grid[mask].min()
        if prm.negative:
            h = h[mask].max() - h
        if prm.mode in ('alturas', '3d', 'bloque'):
            h = _boost(h * prm.z_scale, None, mask, res, prm)[0] / prm.z_scale
        nv, nu = mask.shape
        # misma escala y marco que _reconstruct_25d
        s = piece_scale(prm, (max(nu, nv) * res))
        piece = mask.copy()
        if prm.shape == 'rectangulo':
            piece = np.ones_like(mask)
            h = np.where(mask, h, 0.0)
        ring = np.zeros_like(piece)
        F = 0
        if prm.frame > 0:
            F = max(1, int(round(prm.frame / (res * s))))
            if by_length(prm):
                for _ in range(3):
                    s = prm.target_len / ((max(nu, nv) + 2 * F) * res)
                    F = max(1, int(round(prm.frame / (res * s))))
                s = prm.target_len / ((max(nu, nv) + 2 * F) * res)
            h = np.pad(h, F)
            mask = np.pad(mask, F)
            piece = np.pad(piece, F)
            ring = ~piece if prm.shape == 'rectangulo' else (ndimage.distance_transform_edt(~piece) <= F) & ~piece
            piece = piece | ring
        cellmm = res * s
        return {'h': np.where(mask, h * s * prm.z_scale, np.nan), 'piece': piece, 'scan': mask, 'ring': ring,
                'cell': cellmm, 'size': (piece.shape[1] * cellmm, piece.shape[0] * cellmm), 'angle': ang,
                'auto': auto, 'frame_cells': F}

    # parámetros que no cambian el mapa de alturas (se aplican después, en _piece_view: no hace falta rehacerlo)
    AFTER_GRID = ('z_scale', 'negative', 'boost', 'boost_lo', 'boost_hi', 'boost_size', 'shape', 'frame', 'base',
                  'simplify', 'wall', 'drain', 'label', 'label_size', 'label_depth', 'reverse', 'quality')

    def compute_detail(self, prm, log=lambda s: None):
        """Vista previa con el detalle de la pieza, sin generar nada: el mapa de alturas de verdad (heightmap() con
        la nube entera y la resolución que tendrá la pieza) y lo que se le hace antes de la malla (realce, molde,
        exageración, marco). El mapa se guarda: cambiar el realce, la exageración o el marco cuesta décimas de
        segundo; girar o cambiar la resolución lo rehace (unos segundos con millones de puntos)."""
        if prm.mode == 'objeto':
            return self.compute_object(prm, ncells=1200)
        if prm.mode == 'bloque':
            prm = Params(**{**asdict(prm), 'frame': 0.0, 'shape': 'contorno', 'negative': False, 'z_scale': 1.0})
        key = tuple((k, repr(v)) for k, v in asdict(prm).items() if k not in self.AFTER_GRID)
        if getattr(self, '_det_key', None) != key:
            self._det = heightmap(self.p, prm, None, log)
            self._det_key = key
        hm = self._det
        auto = (hm.ang - prm.angle) if prm.auto_straighten else 0.0
        d = self._piece_view(hm.grid, hm.mask, hm.res, prm, hm.ang, auto)
        if d is not None:
            d['detail'] = True
        return d

    def lines_angle(self, prm):
        """Giro total que alinea con los ejes las líneas rectas del relieve (bordes tallados, renglones, marcos de
        la propia piedra): histograma de la orientación del gradiente de alturas, módulo 90°, ponderado por su
        intensidad, solo en el interior de la pieza (el contorno no cuenta)."""
        q = Params(**{**asdict(prm), 'angle': 0.0, 'auto_straighten': False, 'frame': 0.0, 'shape': 'contorno',
                      'negative': False, 'z_scale': 1.0})
        d = self.compute(q, ncells=500)
        if d is None:
            return None
        h = np.where(np.isnan(d['h']), 0, d['h'])
        inner = ndimage.binary_erosion(d['scan'], iterations=6)
        gx, gy = ndimage.sobel(h, 1), ndimage.sobel(h, 0)
        w = (gx ** 2 + gy ** 2)[inner]
        if w.sum() == 0:
            return None
        th = np.degrees(np.arctan2(gy[inner], gx[inner])) % 90
        hist, edges = np.histogram(th, bins=180, range=(0, 90), weights=w)
        hist = ndimage.gaussian_filter1d(hist, 1.5, mode='wrap')
        k = int(np.argmax(hist))
        a, b, c = hist[k - 1], hist[k], hist[(k + 1) % 180]
        off = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        peak = (k + 0.5 + off) * 0.5
        delta = -(((peak + 45) % 90) - 45)        # giro mínimo que lleva esa dirección a un eje
        return delta


# ------------------------------------------------------------------ reconstrucción
class HM:
    """Resultado de la etapa de mapa de alturas (unidades de la nube)."""
    pass


def _sort_cells(cell, H, ncells, K=NCPU):
    """Orden por celda y, dentro de cada celda, por altura. En paralelo: se reparten los puntos en bandas de
    celdas (ordenación radix, O(n)) y cada banda se ordena en un hilo (numpy libera el GIL al ordenar)."""
    span = float(H.max() - H.min()) * 1.0001 + 1e-9
    key = cell + (H - H.min()) / span                     # clave única: celda + fracción de altura
    K = max(1, min(K, 64))
    if K == 1 or len(cell) < 2_000_000:
        return np.argsort(key)
    band = (cell * K // ncells).astype(np.uint8)
    ob = np.argsort(band, kind='stable')
    bounds = np.searchsorted(band[ob], np.arange(K + 1))

    def work(b):
        idx = ob[bounds[b]:bounds[b + 1]]
        return idx[np.argsort(key[idx])]

    with ThreadPoolExecutor(K) as ex:
        return np.concatenate(list(ex.map(work, range(K))))


def heightmap(p, prm: Params, rgb=None, log=lambda s: None, fine=None):
    """fine: máscara de los puntos de las zonas de detalle (se guarda en hm.fine, alineada con hm.U)."""
    log(f'{len(p):,} puntos')
    U, V, H, keep = _project(p, prm)
    col = rgb[keep] if rgb is not None else None
    vw = eff_view(prm)
    if getattr(prm, 'surface', 'plano') != 'plano':
        log(f'Cara desenrollada ({prm.surface}), vista desde fuera')
    else:
        log('Plano principal y cara frontal calculados' if vw not in VIEWS else f'Cara vista desde: {vw}')
    drop = getattr(_project, 'dropped', 0.0)
    if drop > 0.05:
        log(f'AVISO: «Profundidad máx.» ({_project.behind:.0f} mm{" auto" if prm.behind <= 0 else ""}) deja fuera el {100 * drop:.0f} % de los puntos, los que '
            f'quedan más atrás del plano medio de la cara. Si son parte de la pieza (terreno en pendiente, un '
            f'edificio visto de frente), súbela.')

    plan = print_plan(_face_stats_uv(U, V), prm)
    res = plan['res']
    if prm.res > 0:
        log(f'Resolución manual: {res:g} (celda de {plan["cell_print"]:.3f} mm en la pieza)')
    else:
        lim = {'impresora': 'la impresora', 'escaneo': 'el escaneo'}.get(plan['why'], plan['why'])
        log(f'Resolución automática: {res:g} — limita {lim} '
            + ('(copia sin impresora); ' if is_real(prm) else
               f'(escaneo {plan["res_scan"]:.2f}, boquilla {prm.nozzle:g} mm → {plan["res_print"]:.2f}); ')
            + f'celda de {plan["cell_print"]:.3f} mm en la pieza')
    if plan['fits'] is False:
        log(f'AVISO: la pieza ({plan["size"][0]:.0f} × {plan["size"][1]:.0f} mm) no cabe en la cama '
            f'({prm.bed_x:.0f} × {prm.bed_y:.0f} mm)')

    ang = prm.angle
    if prm.auto_straighten:
        a = _straighten_angle(U, V, res)
        log(f'Enderezado automático: {a:+.2f}°')
        ang += a
    U, V = _rotate(U, V, ang)

    grid, mask, cgrid, valid, u0, v0 = grid_from(U, V, H, col, res, prm, log)

    hm = HM()
    hm.U, hm.V, hm.H, hm.col, hm.ang, hm.res, hm.res_scan = U, V, H, col, ang, res, plan['res_scan']
    hm.fine = fine[keep] if fine is not None else None
    hm.grid, hm.mask, hm.cgrid, hm.valid = grid, mask, cgrid, valid
    hm.u0, hm.v0 = u0, v0                                     # esquina de la celda (0,0)
    hm.floor = float(grid[mask].min())
    # nube -> rejilla (X = U - u0, Y = V - v0, Z = H): giro del plano principal y giro en el plano
    m, A = _project.last
    a = np.radians(ang)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    L = Rz @ A.T
    hm.to_grid = affine(L, -L @ m - [hm.u0, hm.v0, 0])
    return hm


def grid_from(U, V, H, col, res, prm, log=lambda s: None):
    """Rejilla de alturas de una cara (coordenadas U, V en su plano y altura H): una altura por celda (el percentil
    «front_pct» de las de la celda), máscara de la pieza, relleno, picos, forma de la roca y suavizados.
    -> (grid, mask, cgrid, valid, u0, v0), con (u0, v0) la esquina de la celda (0, 0)"""
    # --- rejilla
    iu = ((U - U.min()) / res).astype(np.int64)
    iv = ((V - V.min()) / res).astype(np.int64)
    nu, nv = int(iu.max()) + 1, int(iv.max()) + 1
    if nu * nv > 60_000_000:
        raise ValueError(f'Rejilla demasiado grande ({nu}x{nv}); aumenta la resolución (mm/celda)')
    cell = iv * nu + iu
    order = _sort_cells(cell, H, nv * nu, threads_for(prm, 'sort'))
    cs, Hs = cell[order], H[order]
    starts = np.flatnonzero(np.r_[True, cs[1:] != cs[:-1]])
    counts = np.diff(np.r_[starts, len(cs)])
    pick = starts + (counts * prm.front_pct / 100).astype(np.int64).clip(max=counts - 1)
    grid = np.full(nv * nu, np.nan)
    cnt = np.zeros(nv * nu, np.int64)
    grid[cs[pick]] = Hs[pick]
    cnt[cs[starts]] = counts
    grid, cnt = grid.reshape(nv, nu), cnt.reshape(nv, nu)
    cgrid = None
    if col is not None:
        cgrid = np.stack([np.bincount(cell, col[:, k].astype(np.float64), nv * nu) for k in range(3)], 1)
        cgrid = (cgrid / np.maximum(cnt.reshape(-1, 1), 1)).reshape(nv, nu, 3)
    del cs, Hs, order, cell
    log(f'Mapa de alturas {nu} x {nv} celdas ({(cnt > 0).mean() * 100:.0f} % con datos, '
        f'{np.median(cnt[cnt > 0]):.0f} puntos/celda de mediana)')

    # --- máscara
    mask = cnt >= prm.min_pts
    mask = ndimage.binary_closing(mask, iterations=3)
    mask = ndimage.binary_fill_holes(mask)
    mask = ndimage.binary_opening(mask, iterations=2)
    lab, k = ndimage.label(mask)
    if k == 0:
        raise ValueError('No queda ninguna región; revisa los parámetros')
    if k > 1:
        mask = lab == (np.argmax(ndimage.sum(mask, lab, range(1, k + 1))) + 1)
    if prm.erode > 0:
        mask = ndimage.distance_transform_edt(mask) > prm.erode / res
        if not mask.any():
            raise ValueError('El recorte de borde elimina toda la pieza')
        lab, k = ndimage.label(mask)
        if k > 1:
            mask = lab == (np.argmax(ndimage.sum(mask, lab, range(1, k + 1))) + 1)
    r0, r1 = np.flatnonzero(mask.any(1))[[0, -1]]
    c0, c1 = np.flatnonzero(mask.any(0))[[0, -1]]
    sl = (slice(r0, r1 + 1), slice(c0, c1 + 1))
    mask, grid = mask[sl], grid[sl]
    if cgrid is not None:
        cgrid = cgrid[sl]

    # --- relleno, picos, suavizado
    valid = ~np.isnan(grid)
    grid = _fill_nearest(grid, valid)
    if prm.despike > 0:
        ref = ndimage.median_filter(grid, size=7)
        spikes = valid & (np.abs(grid - ref) > prm.despike)
        log(f'Picos eliminados: {spikes.sum():,} celdas')
        valid &= ~spikes
        grid = _fill_nearest(np.where(valid, grid, np.nan), valid)
    holes = mask & ~valid
    if prm.fill == 'interpolado' and holes.any():
        grid = _fill_interp(grid, valid, holes)
    elif prm.fill == 'suave' and holes.any():
        grid = _fill_smooth(grid, valid, holes, log=log)
    log(f'Huecos rellenados: {holes.sum():,} celdas ({holes.sum() / mask.sum() * 100:.1f} % de la pieza, '
        f'método {prm.fill})')
    if cgrid is not None:
        idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
        cgrid = cgrid[idx[0], idx[1]]
    if uses_detrend(prm):
        import superficie
        before = np.percentile(grid[mask], 98) - np.percentile(grid[mask], 2)
        grid = grid - superficie.low_surface(grid, mask, superficie.detrend_sigma(prm.detrend, res))
        after = np.percentile(grid[mask], 98) - np.percentile(grid[mask], 2)
        log(f'Forma general de la roca quitada (formas de más de {prm.detrend:g} mm): el relieve pasa de '
            f'{before:.1f} a {after:.1f} mm')
    if prm.smooth > 1:
        grid = ndimage.median_filter(grid, size=prm.smooth)
    if prm.gauss > 0 and getattr(prm, 'edges', False):
        grid, noise = edge_smooth(grid, prm.gauss / res)
        log(f'Suavizado que respeta las aristas (filtro guiado, radio {prm.gauss:g} mm, ruido estimado {noise:.3g})')
    elif prm.gauss > 0:
        grid = ndimage.gaussian_filter(grid, prm.gauss / res)
    if prm.floor_pct > 0:
        grid = np.maximum(grid, np.percentile(grid[mask], prm.floor_pct))
    return grid, mask, cgrid, valid, U.min() + c0 * res, V.min() + r0 * res



def affine(L, t):
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = L, t
    return T


def grid_to_piece(hm, s, zs=1.0, shift=(0, 0, 0), z0=0.0, flipz=False):
    """Rejilla -> pieza: x = s·(X + sx), y = s·(Y + sy), z = ±s·zs·(Z − z0) + sz."""
    k = -1.0 if flipz else 1.0
    return affine(np.diag([s, s, k * s * zs]), [s * shift[0], s * shift[1], -k * s * zs * z0 + shift[2]]) @ hm.to_grid


def reconstruct(p, prm: Params, rgb=None, log=lambda s: None, normals=None, mesh=False):
    fine = None
    if getattr(prm, 'detail', None):
        if has_detail(prm):
            fine = zone_mask(p, prm.detail)
            log(f'Zonas de detalle: {len(prm.detail)} ({fine.sum():,} puntos, {100 * fine.mean():.1f} % de la nube)')
            if not fine.any():
                log('AVISO: ningún punto cae en las zonas de detalle')
                fine = None
        else:
            log('AVISO: las zonas de detalle solo se aplican en los tipos con Poisson (3d, bloque, objeto, terreno); '
                'aquí se ignoran')
    if prm.mode == 'objeto':
        import objeto3d
        r = objeto3d.reconstruct_object(p, prm, rgb, normals, log, mesh=mesh, fine=fine)
    elif prm.mode == 'bloque' and getattr(prm, 'corner', False):
        import esquina
        r = esquina.reconstruct_corner(p, prm, rgb, log)
    else:
        hm = heightmap(p, prm, rgb, log, fine=fine)
        if prm.mode == '3d':
            import recon3d
            r = recon3d.reconstruct3d(hm, prm, log)
        elif prm.mode == 'bloque':
            import bloque3d
            r = bloque3d.reconstruct_block(hm, prm, log, mesh=mesh)
        else:
            back = back_face(hm, prm, log) if getattr(prm, 'reverse', None) else None
            r = _reconstruct_25d(hm, prm, log, back)
        r.res = hm.res
    rf = getattr(r, 'detail_res', None)         # zonas de detalle (lo pone el modo; el realce rehace el Result)
    if prm.mode != 'alturas':                   # en el mapa de alturas ya se ha hecho (_boost)
        r = _boost_result(r, prm, log)
    color = r.vcolors is not None
    log(f'Tamaño del fichero sin simplificar: {sizes_text(len(r.verts), len(r.tris), color)}')
    if prm.simplify <= 0 and rf and r.xform is not None:          # dos tolerancias: la de las zonas, más fina
        tol = tolerance(prm, r.res)
        tol_in = min(tol, REAL_TOL.get(getattr(prm, 'use', 'archivo'), 0.25) * rf * r.scale)
        Tinv = np.linalg.inv(np.asarray(r.xform, np.float64))
        inside = lambda v: zone_mask(np.asarray(v, np.float64) @ Tinv[:3, :3].T + Tinv[:3, 3], prm.detail)  # noqa: E731
        r = simplify_zoned(r, tol, tol_in, inside, log)
        log(f'Tamaño final: {sizes_text(len(r.verts), len(r.tris), color)}')
    elif prm.simplify <= 0:
        r = simplify_auto(r, tolerance(prm, r.res), log, threads_for(prm, 'simplify'))
        log(f'Tamaño final: {sizes_text(len(r.verts), len(r.tris), color)}')
    elif prm.simplify < 100:
        f = prm.simplify / 100
        log(f'Estimado tras simplificar al {prm.simplify:g} %: '
            f'{sizes_text(int(len(r.verts) * f), int(len(r.tris) * f), color)}')
        r = simplify(r, prm.simplify, log, threads_for(prm, 'simplify'))
        log(f'Tamaño final: {sizes_text(len(r.verts), len(r.tris), color)}')
    try:
        import desviacion
        r.dev = desviacion.compute(r, p, log)
    except Exception as e:                      # el mapa es informativo: nunca debe impedir tener la pieza
        log(f'(no se pudo calcular el mapa de desviación: {e})')
    if prm.wall > 0 or prm.label.strip():
        import acabado
        r0 = r
        r = acabado.apply(r, prm, log)
        if r is not r0 and r0.dev is not None:  # hueco, agujeros y letras: superficie añadida (sin escaneo)
            import desviacion
            r.dev = desviacion.transfer(r0, r)
    depth = r.relief_depth
    nl = depth / prm.layer if prm.layer > 0 else 0
    if is_real(prm):                            # copia sin impresora: ni capas ni impresora
        log(f'Copia sin impresora, {scale_txt(real_scale(prm))}: {fmt_len(depth)} de '
            + ('alto' if r.mode == 'objeto' else 'relieve'))
        return r
    if r.mode == 'objeto':
        log(f'Objeto de {depth:.1f} mm de alto ≈ {nl:.0f} capas de {prm.layer:g} mm')
        return r
    log(f'Relieve de {depth:.1f} mm en la pieza ≈ {nl:.0f} capas de {prm.layer:g} mm')
    if prm.layer > 0 and nl < 10:
        log(f'AVISO: el relieve ocupa pocas capas ({nl:.0f}); se verá escalonado. Prueba una capa más fina, '
            f'una pieza más grande o «Exageración del relieve».')
    return r


BOOST_AUTO = (0.1, 0.5, 3.0)        # «auto»: desde, hasta y anchura del realce en mm de la pieza


def boost_limits(prm, s, zs=1.0):
    """Desde, hasta y anchura del realce en mm de la piedra (la nube, ya en mm). Se escriben así porque es lo que
    se mide en la nube y no cambia al cambiar el tamaño de la pieza; 0 = automático: lo que en la pieza (escala s,
    exageración zs) es BOOST_AUTO. «Hasta» es siempre al menos el doble de «desde»."""
    lo = prm.boost_lo if prm.boost_lo > 0 else BOOST_AUTO[0] / (s * zs)
    hi = prm.boost_hi if prm.boost_hi > 0 else max(BOOST_AUTO[1] / (s * zs), 3 * lo)
    size = prm.boost_size if prm.boost_size > 0 else BOOST_AUTO[2] / s
    return lo, max(hi, 2 * lo), size


def boost_text(prm, s, zs=1.0):
    """«desde 0,9 hasta 5,3 mm de la piedra, anchura 52 mm (en la pieza: 0,05 / 0,30 / 3 mm)»"""
    lo, hi, size = boost_limits(prm, s, zs)
    f = lambda x: f'{x:.3g}'.replace('.', ',')     # noqa: E731
    return (f'desde {f(lo)} hasta {f(hi)} mm de la piedra, anchura {f(size)} mm (en la pieza: '
            f'{f(lo * s * zs)} / {f(hi * s * zs)} / {f(size * s)} mm)')


def _boost(h, hb, mask, res, prm, log=lambda s: None):
    """Realce de lo pequeño (boost_small) en la cara y en el reverso. h en unidades de la nube (mm de la piedra)
    ya multiplicadas por la exageración: el detalle se mide en la piedra, sin exagerar."""
    if getattr(prm, 'boost', 1.0) <= 1 or is_real(prm):      # copia sin impresora: fiel, sin realce
        return h, hb
    nv, nu = mask.shape
    s = piece_scale(prm, max(nu, nv) * res)
    zs = max(prm.z_scale, 1e-9)
    lo, hi, size = boost_limits(prm, s, zs)
    out = []
    for x in (h, hb):
        if x is None:
            out.append(None)
            continue
        y, (n, mx) = boost_small(x / zs, mask, res, prm.boost, lo, hi, size)
        out.append(np.clip(y * zs, 0, None))
        log(f'Realce de lo pequeño: ×{prm.boost:g} {boost_text(prm, s, zs)}: {n / max(mask.sum(), 1) * 100:.0f} % '
            f'de la {"cara" if x is h else "reverso"} cambia, como mucho {mx:.2f} mm de la piedra '
            f'({mx * s * zs:.2f} mm en la pieza)')
    return out[0], out[1]


def _reconstruct_25d(hm, prm, log, back=None):
    grid, mask, cgrid, res, ang = hm.grid, hm.mask, hm.cgrid, hm.res, hm.ang
    # --- alturas relativas, molde, exageración
    h = grid - grid[mask].min()
    if prm.negative:
        h = h[mask].max() - h
        if back is not None:
            log('AVISO: un molde no tiene reverso: se hace solo con la cara')
            back = None
    h = np.clip(h, 0, None) * prm.z_scale
    hb = None
    if back is not None:                      # reverso, ya en la rejilla de la cara: alturas hacia abajo
        hb = np.clip(back - back[mask].min(), 0, None) * prm.z_scale
    h, hb = _boost(h, hb, mask, res, prm, log)

    # --- forma: rectángulo y marco
    nv, nu = mask.shape
    s = piece_scale(prm, (max(nu, nv) * res))
    if prm.shape == 'rectangulo':
        h = np.where(mask, h, 0.0)
        if hb is not None:
            hb = np.where(mask, hb, 0.0)
        mask = np.ones_like(mask)
    if prm.frame > 0:
        F = max(1, int(round(prm.frame / (res * s))))
        # el marco se añade fuera: recalcular la escala para que el lado mayor siga cumpliéndose
        if by_length(prm):
            for _ in range(3):
                s = prm.target_len / ((max(nu, nv) + 2 * F) * res)
                F = max(1, int(round(prm.frame / (res * s))))
            s = prm.target_len / ((max(nu, nv) + 2 * F) * res)
        fh = np.percentile(h[mask], 98)
        h = np.pad(h, F, constant_values=0.0)
        mask_p = np.pad(mask, F)
        if prm.shape == 'rectangulo':
            ring = ~mask_p
        else:
            ring = (ndimage.distance_transform_edt(~mask_p) <= F) & ~mask_p
        h = np.where(ring, fh, h)
        if hb is not None:
            fb = np.percentile(hb[mask], 98)
            hb = np.where(ring, fb, np.pad(hb, F, constant_values=0.0))
        mask = mask_p | ring
        if cgrid is not None:
            cgrid = np.pad(cgrid, ((F, F), (F, F), (0, 0)), mode='edge')
            cgrid[ring] = 160
        log(f'Marco de {F} celdas a {fh * s:.1f} mm sobre la base')

    cellmm = res * s
    hf = h * s
    verts, tris, vcol = _mesh(hf, mask, cellmm, prm.base, cgrid, hb=None if hb is None else hb * s)
    if hb is not None:
        lift = float(np.nanmax(np.where(mask, hb * s, np.nan)))
        verts[:, 2] += lift                   # el reverso por debajo: la pieza empieza en z = 0
        log(f'Pieza de dos caras: reverso de {lift:.1f} mm de relieve bajo un alma de {prm.base:g} mm')
    bad = _bad_edges(tris, len(verts))
    vol = _volume(verts, tris)
    log(f'{len(tris):,} triángulos, aristas no-manifold: {bad}, volumen {fmt_vol(vol)}')
    out = np.where(mask, hf + prm.base, np.nan)
    off = (F * res, F * res) if prm.frame > 0 else (0, 0)
    if prm.negative:
        hmax = float(hm.grid[hm.mask].max() - hm.floor)
        top = s * prm.z_scale * hmax + prm.base
        T = grid_to_piece(hm, s, prm.z_scale, (*off, top), hm.floor, flipz=True)
        zc = (-np.inf, top + 0.5 * cellmm)
    else:
        T = grid_to_piece(hm, s, prm.z_scale, (*off, prm.base), hm.floor)
        zc = (prm.base - 0.5 * cellmm, np.inf)
    return Result(verts, tris, vcol, out, cellmm, s, ang, bad, vol, xform=T, zclip=zc)


def back_face(hm, prm, log=lambda s: None):
    """Reverso de una pieza de dos caras (moneda, medalla, tablilla) en la rejilla de la cara: su mapa de alturas
    (mirado desde su lado y espejado, porque se ve desde detrás), girado y desplazado para que su contorno coincida
    con el de la cara. El giro se busca solo (el de mayor coincidencia de contornos; con piezas redondas no hay
    contorno que lo decida: pon el giro a mano) o se toma de reverse[0]['angle']. -> rejilla de alturas del reverso
    (NaN fuera), o None si no se puede"""
    import entrada
    e = prm.reverse[0]
    try:
        xyz, _, _, _ = entrada.load_points(entrada.resolve_reverse(e['path'], getattr(prm, '_main_path', None)),
                                           Params(units=e.get('units', prm.units), clean=prm.clean), log=lambda s: None)
    except Exception as ex:  # noqa: BLE001
        log(f'AVISO: no se pudo leer el reverso ({ex}): se hace solo con la cara')
        return None
    pb = Params(**{k: getattr(prm, k) for k in ('view', 'behind', 'front_pct', 'min_pts', 'fill', 'despike', 'smooth',
                                                 'gauss', 'edges', 'floor_pct', 'erode', 'plane', 'units')})
    pb.res = hm.res
    pb.angle, pb.auto_straighten, pb.target_len, pb.ratio = 0.0, False, prm.target_len, prm.ratio
    pb.target = getattr(prm, 'target', 'imprimir')
    flip = e.get('flip')                           # None: decidir; True/False: elegido a mano
    # visto desde su lado, el reverso sale espejado respecto a como queda bajo la cara; «invertir» ya da la vuelta
    # (y espeja): solo hay que espejar aparte si no se invierte
    pb.flip = bool(flip)
    pb.mirror = prm.mirror if pb.flip else not prm.mirror
    hb = heightmap(xyz, pb, None, lambda s: None)
    if flip is None:
        # una lámina fina no dice de qué lado se mira; en un relieve el fondo es lo más bajo y lo tallado sobresale
        p2, p50, p98 = np.percentile(hb.grid[hb.mask], [2, 50, 98])
        if p98 - p50 < p50 - p2:
            pb.flip = True
            pb.mirror = prm.mirror
            hb = heightmap(xyz, pb, None, lambda s: None)
            log('Reverso: se mira desde el otro lado (el relieve salía hundido)')
    mA = hm.mask
    gB = _fill_nearest(np.where(hb.mask, hb.grid, np.nan), hb.mask)
    mB = hb.mask
    cA = np.argwhere(mA).mean(0)
    cB = np.argwhere(mB).mean(0)

    def warp(arr, theta, shift, order, k=1):
        """arr (rejilla del reverso) llevada a la de la cara (reducidas k veces): giro theta (grados) alrededor de
        los centros y desplazamiento shift (celdas de la cara)."""
        t = np.radians(theta)
        R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
        out_shape = tuple(int(np.ceil(n / k)) for n in mA.shape)
        c_out = (cA + shift) / k
        c_in = cB / k
        src = arr[::k, ::k] if k > 1 else arr
        return ndimage.affine_transform(src.astype(np.float64), R.T, offset=c_in - R.T @ c_out,
                                        output_shape=out_shape, order=order, mode='constant', cval=0.0)
    ang = e.get('angle')
    shift = np.asarray(e.get('shift', (0.0, 0.0)), np.float64)
    if ang is None:
        k = max(1, int(max(mA.shape) / 160))
        a_small = mA[::k, ::k]

        def iou(th):
            b = warp(mB, th, shift, 0, k) > 0.5
            return (a_small & b).sum() / max((a_small | b).sum(), 1)
        coarse = [(iou(th), th) for th in range(0, 360, 3)]
        best = max(coarse)
        fine = [(iou(th), th) for th in np.arange(best[1] - 3, best[1] + 3.01, 0.5)]
        score, ang = max(fine)
        others = [s for s, th in coarse if min(abs(th - ang) % 360, 360 - abs(th - ang) % 360) > 20]
        flat = max(others) > score - 0.01 if others else False
        log(f'Reverso encajado por el contorno: giro {ang:.1f}°, coincidencia {100 * score:.0f} %'
            + (' — AVISO: el contorno casi no cambia al girar (pieza redonda): elige el giro a mano' if flat else ''))
    else:
        log(f'Reverso con el giro elegido: {ang:.1f}°')
    g = warp(gB, ang, shift, 1)
    m = warp(mB, ang, shift, 0) > 0.5
    miss = mA & ~m
    if miss.any() and m.any():
        g = _fill_nearest(np.where(m, g, np.nan), m)
    if mA.sum() and miss.sum() / mA.sum() > 0.1:
        log(f'AVISO: el {100 * miss.sum() / mA.sum():.0f} % de la cara no tiene reverso debajo (contornos '
            f'distintos): se rellena con el borde más cercano')
    return np.where(mA, g, np.nan)


MIN_TRIS_PER_STRIP = 150_000


DECIMATE_SIZE = 100.0     # lado mayor al que se lleva la malla para simplificarla (ver _decimate_mesh)


def _decimate_mesh(V, T, vcol, target, workers, log=lambda s: None):
    """Simplifica a ~target triángulos. Devuelve (verts, tris, vcol) cerrada, o None si no se pudo.
    Se simplifica con la malla llevada a DECIMATE_SIZE de lado y centrada: con coordenadas de decenas de metros en
    mm (una copia a tamaño real de un acueducto), PyMeshLab dejaba aristas abiertas que a escala reducida no deja."""
    c = V.mean(0)
    k = DECIMATE_SIZE / max(float(np.ptp(V, 0).max()), 1e-9)
    out = _decimate_mesh_n((V - c) * k, T, vcol, target, workers, log)
    if out is None:
        return None
    return (out[0] / k + c, out[1], out[2])


def _decimate_mesh_n(V, T, vcol, target, workers, log=lambda s: None):
    import pymeshlab
    out = None
    workers = min(workers, len(T) // MIN_TRIS_PER_STRIP)   # franjas demasiado finas no compensan
    if workers > 1:
        try:
            out = _decimate_parallel(V, T, vcol, target / len(T), workers)
            if _bad_edges(out[1], len(out[0])):
                log('AVISO: la simplificación en paralelo no cosió bien las franjas; se repite en un solo proceso')
                out = None
        except Exception as e:  # noqa: BLE001
            log(f'AVISO: simplificación en paralelo no disponible ({e}); se usa un solo proceso')
            out = None
    if out is None:
        ms = pymeshlab.MeshSet()
        kw = {'v_color_matrix': np.c_[vcol / 255.0, np.ones(len(vcol))]} if vcol is not None else {}
        ms.add_mesh(pymeshlab.Mesh(vertex_matrix=V.astype(np.float64), face_matrix=T.astype(np.int32), **kw))
        _decimate(ms, max(1000, int(target)))
        m = ms.current_mesh()
        c = (m.vertex_color_matrix()[:, :3] * 255).round().astype(np.uint8) if vcol is not None else None
        out = (m.vertex_matrix(), m.face_matrix().astype(np.int64), c)
    if _bad_edges(out[1], len(out[0])):
        return None
    return out


def _mesh_error(V0, T0, V, T, nsamples=400_000, pts=None):
    """Distancia de la superficie original a la simplificada: (media, p99, máximo, p99,9), en las unidades de la
    malla. Muestrea vértices y centros de cara originales. Usa Open3D (multihilo) o PyMeshLab si no está."""
    rng = np.random.default_rng(0)
    if pts is None:                          # puntos de referencia: vértices y centros de cara originales
        pts = np.concatenate([V0, V0[T0].mean(1)])
    if len(pts) > nsamples:
        pts = pts[rng.choice(len(pts), nsamples, replace=False)]
    try:
        import open3d as o3d
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(o3d.core.Tensor(V.astype(np.float32)), o3d.core.Tensor(T.astype(np.uint32)))
        d = scene.compute_distance(o3d.core.Tensor(pts.astype(np.float32))).numpy()
        p99, p999 = np.percentile(d, [99, 99.9])
        return float(d.mean()), float(p99), float(d.max()), float(p999)
    except ImportError:
        import pymeshlab
        ms = pymeshlab.MeshSet()
        ms.add_mesh(pymeshlab.Mesh(vertex_matrix=V.astype(np.float64), face_matrix=T.astype(np.int32)))
        ms.add_mesh(pymeshlab.Mesh(vertex_matrix=V0.astype(np.float64), face_matrix=T0.astype(np.int32)))
        h = ms.get_hausdorff_distance(sampledmesh=1, targetmesh=0, samplevert=True, sampleface=True,
                                      samplenum=min(nsamples, len(V0)))
        return h['mean'], h['RMS'] * 2.6, h['max'], h['RMS'] * 3.3   # sin percentiles: aproximación gaussiana


def _decimate_selected(V, T, vcol, sel, target):
    """Simplifica solo las caras sel (bool por cara) hasta que de ellas queden ~target (PyMeshLab cuenta solo las
    seleccionadas; las demás no se tocan). Con la malla llevada a DECIMATE_SIZE de lado, como _decimate_mesh.
    -> (verts, tris, vcol) cerrada, o None."""
    import pymeshlab
    c = V.mean(0)
    k = DECIMATE_SIZE / max(float(np.ptp(V, 0).max()), 1e-9)
    kw = {'v_color_matrix': np.c_[vcol / 255.0, np.ones(len(vcol))]} if vcol is not None else {}
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=(V - c) * k, face_matrix=T.astype(np.int32),
                               f_scalar_array=sel.astype(np.float64), **kw))
    ms.compute_selection_by_scalar_per_face(minq=0.5, maxq=1.5)
    _decimate(ms, max(50, int(target)), selected=True)
    m = ms.current_mesh()
    out = (m.vertex_matrix() / k + c, m.face_matrix().astype(np.int64),
           (m.vertex_color_matrix()[:, :3] * 255).round().astype(np.uint8) if vcol is not None else None)
    return None if _bad_edges(out[1], len(out[0])) else out


def simplify_zoned(r, tol_out, tol_in, inside, log=lambda s: None):
    """Simplificación automática con dos tolerancias: fuera de las zonas de detalle, tol_out; dentro, tol_in (más
    fina). inside(verts) -> bool por vértice. Primero se simplifica lo de fuera (sin tocar las zonas) y luego lo de
    dentro, cada parte mientras su error (medido solo con los puntos originales de esa parte) cumpla su tolerancia,
    como simplify_auto."""
    try:
        import pymeshlab  # noqa: F401
    except ImportError:
        log('AVISO: pymeshlab no está instalado; no se simplifica')
        return r
    V0, T0 = r.verts, r.tris
    zin = inside(V0)
    pts_all = np.concatenate([V0, V0[T0].mean(1)])
    in_all = np.concatenate([zin, zin[T0].any(1)])
    n0 = len(T0)
    log(f'Simplificación por zonas: fuera, tolerancia {tol_out:.3g} mm; en las zonas de detalle, {tol_in:.3g} mm…')
    cur, errs = (r.verts, r.tris, r.vcolors), []
    for name, tol, want in (('fuera de las zonas', tol_out, False), ('en las zonas', tol_in, True)):
        ref = pts_all[in_all == want]
        if not len(ref):
            continue
        best, best_err, pct = None, None, 25.0
        f_in = inside(cur[0])[cur[1]].any(1)
        sel = f_in if want else ~f_in
        n_sel = int(sel.sum())
        base = cur
        while n_sel * pct / 100 >= 500:
            out = _decimate_selected(base[0], base[1], base[2], sel, n_sel * pct / 100)
            if out is None:
                break
            err = _mesh_error(V0, T0, out[0], out[1], pts=ref)
            ok = err[1] <= tol and err[3] <= 2 * tol
            log(f'  {name}, {pct:g} % ({len(out[1]):,} triángulos en total): error p99 {err[1]:.3g} mm, '
                f'p99,9 {err[3]:.3g} mm {"✔" if ok else "✘"}')
            if not ok:
                break
            best, best_err = out, err
            pct /= 2
        if best is not None:
            cur = best
            errs.append(best_err)
    if not errs:
        log('No se puede simplificar sin superar la tolerancia: se conserva la malla completa')
        return r
    err = _mesh_error(V0, T0, cur[0], cur[1])
    log(f'Simplificada: {n0:,} -> {len(cur[1]):,} triángulos ({len(cur[1]) / n0 * 100:.1f} %), '
        f'error medio {err[0]:.3g} mm, p99 {err[1]:.3g} mm, máximo {err[2]:.3g} mm')
    return _simplified(r, cur, err)


def _simplified(r, out, err):
    verts, tris, vcol = out
    return Result(verts, tris, vcol, r.grid, r.cell, r.scale, r.angle, 0, _volume(verts, tris),
                  err[0], err[2], r.mode, err[1], r.res, xform=r.xform, hollow=r.hollow, zclip=r.zclip, corner=r.corner)


def simplify(r: Result, pct, log=lambda s: None, workers=None):
    """Simplificación adaptativa (quadric edge collapse) a un % fijo de triángulos, conservando la malla cerrada.

    Con mallas grandes se trocea en franjas que se simplifican en paralelo, una por procesador, conservando
    intactos los vértices de los cortes; después se cosen por esos vértices comunes.
    """
    try:
        import pymeshlab  # noqa: F401
    except ImportError:
        log('AVISO: pymeshlab no está instalado; no se simplifica')
        return r
    workers = workers or NCPU
    n0 = len(r.tris)
    log(f'Simplificando al {pct:g} %' + (f' en paralelo ({min(workers, n0 // MIN_TRIS_PER_STRIP)} procesos)…'
                                       if n0 >= 2 * MIN_TRIS_PER_STRIP and workers > 1 else '…'))
    out = _decimate_mesh(r.verts, r.tris, r.vcolors, n0 * pct / 100, workers, log)
    if out is None:
        log('AVISO: la simplificación abrió la malla; se conserva sin simplificar')
        return r
    err = _mesh_error(r.verts, r.tris, out[0], out[1])
    log(f'Simplificada: {n0:,} -> {len(out[1]):,} triángulos ({len(out[1]) / n0 * 100:.1f} %), '
        f'error medio {err[0]:.3f} mm, p99 {err[1]:.3f} mm, máximo {err[2]:.3f} mm')
    return _simplified(r, out, err)


def simplify_auto(r: Result, tol, log=lambda s: None, workers=None):
    """Simplifica todo lo posible sin que la impresión lo note: va reduciendo (25 %, 12 %, 6 %…) mientras el error
    respecto a la malla original cumpla: percentil 99 <= tol y percentil 99,9 <= 2·tol (mm de la pieza).
    El segundo criterio evita que se cuelen zonas pequeñas con errores grandes (aristas, letras finas)."""
    try:
        import pymeshlab  # noqa: F401
    except ImportError:
        log('AVISO: pymeshlab no está instalado; no se simplifica')
        return r
    workers = workers or NCPU
    n0 = len(r.tris)
    log(f'Simplificación automática: tolerancia {tol:.3f} mm (p99 ≤ {tol:.3f}, p99,9 ≤ {2 * tol:.3f})…')
    best, best_err, cur = None, None, (r.verts, r.tris, r.vcolors)
    pct = 25.0
    while n0 * pct / 100 >= 2000:
        out = _decimate_mesh(cur[0], cur[1], cur[2], n0 * pct / 100, workers, log)
        if out is None:
            break
        err = _mesh_error(r.verts, r.tris, out[0], out[1])
        ok = err[1] <= tol and err[3] <= 2 * tol
        log(f'  {pct:g} % ({len(out[1]):,} triángulos): error p99 {err[1]:.3f} mm, p99,9 {err[3]:.3f} mm '
            f'{"✔" if ok else "✘ (supera la tolerancia)"}')
        if not ok:
            break
        best, best_err, cur = out, err, out
        pct /= 2
    if best is None:
        log('No se puede simplificar sin superar la tolerancia: se conserva la malla completa')
        return r
    log(f'Simplificada: {n0:,} -> {len(best[1]):,} triángulos ({len(best[1]) / n0 * 100:.1f} %), '
        f'error medio {best_err[0]:.3f} mm, p99 {best_err[1]:.3f} mm, máximo {best_err[2]:.3f} mm')
    return _simplified(r, best, best_err)


def _decimate(ms, target, selected=False):
    ms.meshing_decimation_quadric_edge_collapse(targetfacenum=int(target), qualitythr=0.3, preserveboundary=True,
                                                preservenormal=True, preservetopology=True, optimalplacement=True,
                                                planarquadric=True, planarweight=0.002, autoclean=True,
                                                selected=selected)


def _decimate_parallel(V, T, vcolors, frac, workers):
    """Trocea por franjas en X, simplifica cada franja en un proceso (decimar.py) y las cose."""
    cx = (V[T[:, 0], 0] + V[T[:, 1], 0] + V[T[:, 2], 0]) / 3
    cuts = np.quantile(cx, np.linspace(0, 1, workers + 1)[1:-1])
    tile = np.searchsorted(cuts, cx)
    color = vcolors is not None
    with tempfile.TemporaryDirectory(prefix='nube2stl_dec_') as td:
        procs = []
        for k in range(workers):
            F = T[tile == k]
            if len(F) == 0:
                continue
            uniq, inv = np.unique(F, return_inverse=True)
            data = {'v': V[uniq], 'f': inv.reshape(-1, 3).astype(np.int32)}
            if color:
                data['c'] = np.c_[vcolors[uniq] / 255.0, np.ones(len(uniq))]
            fin, fout = os.path.join(td, f'in{k}.npz'), os.path.join(td, f'out{k}.npz')
            np.savez(fin, **data)
            target = max(100, int(len(F) * frac))
            procs.append((subprocess.Popen(helper_cmd('decimar', fin, fout, str(target)), stdout=subprocess.DEVNULL,
                                           stderr=subprocess.PIPE, creationflags=NO_WINDOW), fout))
        vs, fs, cs, off = [], [], [], 0
        for p, fout in procs:
            _, err = p.communicate()
            if p.returncode != 0:
                raise RuntimeError(err.decode(errors='replace').strip().splitlines()[-1:] or p.returncode)
            d = np.load(fout)
            vs.append(d['v'])
            fs.append(d['f'].astype(np.int64) + off)
            if color:
                cs.append(d['c'])
            off += len(d['v'])
    allv = np.concatenate(vs)
    # coser: los vértices de los cortes no se han movido, así que coinciden exactamente
    uv, first, inv = np.unique(allv, axis=0, return_index=True, return_inverse=True)
    tris = inv.reshape(-1)[np.concatenate(fs)]
    tris = tris[(tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])]
    vcol = (np.concatenate(cs)[first, :3] * 255).round().clip(0, 255).astype(np.uint8) if color else None
    return uv, tris, vcol


def edge_smooth(grid, sigma, k=3.0):
    """Suavizado que respeta las aristas (filtro guiado de He et al., guiado por la propia altura): en las zonas
    lisas promedia como un gaussiano de radio ≈ 2·sigma celdas; donde la altura cambia mucho más que el ruido (el
    canto de una letra, una moldura) apenas toca nada. El ruido se mide en la propia rejilla (MAD de lo que quita
    una mediana 3×3) y el umbral es k veces ese ruido. -> (rejilla, ruido)"""
    g = grid.astype(np.float64)
    noise = 1.4826 * float(np.median(np.abs(g - ndimage.median_filter(g, size=3)))) + 1e-9
    eps = (k * noise) ** 2
    size = max(3, int(round(2 * sigma)) * 2 + 1)
    box = lambda a: ndimage.uniform_filter(a, size=size, mode='nearest')     # noqa: E731
    m = box(g)
    var = box(g * g) - m * m
    a = var / (var + eps)
    b = m - a * m
    return (box(a) * g + box(b)).astype(grid.dtype), noise


def boost_curve(gain, lo, hi, n=2048):
    """Curva del realce de lo pequeño: altura del detalle (mm de la pieza, ≥ 0) -> altura realzada. Lo que se
    añade sube de nada en lo/2 (por debajo es ruido y no se toca) a (gain − 1)·lo en lo (gain× justo en el umbral) y
    luego va bajando, cada vez menos aumento, hasta nada en hi; más allá de hi no se toca. Rampas suaves
    (smoothstep) y curva forzada creciente: un detalle nunca adelanta a otro más alto ni sale un escalón.
    -> (a, f) para np.interp"""
    a = np.linspace(0.0, hi * 1.5 + 1e-9, n)
    st = lambda u: (lambda c: c * c * (3 - 2 * c))(np.clip(u, 0, 1))    # noqa: E731
    up = st((a - lo / 2) / max(lo / 2, 1e-9))
    down = 1 - st((a - lo) / max(hi - lo, 1e-9))
    return a, np.maximum.accumulate(a + (gain - 1) * lo * np.where(a < lo, up, down))


def _median_base(g, r):
    """Superficie de referencia: mediana de un entorno de radio r celdas, calculada sobre una rejilla reducida (la
    mediana a toda resolución sería muy lenta) y vuelta a la resolución original con interpolación lineal."""
    f = max(1, r // 6)
    gs = ndimage.uniform_filter(g, f, mode='nearest')[f // 2::f, f // 2::f] if f > 1 else g
    med = ndimage.median_filter(gs, size=2 * max(1, round(r / f)) + 1, mode='nearest')
    med = ndimage.uniform_filter(med, 3, mode='nearest')
    if f == 1:
        return med
    yy, xx = np.mgrid[0:g.shape[0], 0:g.shape[1]].astype(np.float32)
    return ndimage.map_coordinates(med, [(yy - f // 2) / f, (xx - f // 2) / f], order=1, mode='nearest')


def boost_small(h, mask, cellmm, gain=2.0, lo=0.1, hi=0.5, size=3.0):
    """Realza los detalles de poco relieve (mm de la pieza), positivos y negativos: lo que sobresale o se hunde
    respecto a su entorno entre lo y hi mm se multiplica por hasta «gain» (gain× en lo, nada en hi; ver
    boost_curve). El entorno es la mediana en un radio «size» mm (_median_base): no cuenta lo que ocupa menos de la
    mitad del entorno, así que una letra gastada junto a una moldura o a una letra grande se mide respecto a la
    piedra que la rodea, no respecto a ellas. Junto al borde de un relieve grande (a unas pocas celdas, según la
    reducción de _median_base) el realce se apaga, para que su rampa no se tome por detalle ni salga un escalón falso a su lado.
    -> (h realzada, (cuántas celdas cambian, máximo añadido en mm))"""
    if gain <= 1 or hi <= lo:
        return h, (0, 0.0)
    g = np.where(mask, h, np.nan).astype(np.float64)
    g = _fill_nearest(np.nan_to_num(g, nan=0.0), mask) if not mask.all() else np.nan_to_num(g)
    r = max(1, int(round(size / max(cellmm, 1e-9))))
    base = _median_base(g, r)
    d = g - base
    big = np.abs(d) > hi
    if big.any():                                       # la referencia se suaviza en unas 1,5·f celdas junto a un
        dist = ndimage.distance_transform_edt(~big)     # canto (f: reducción de _median_base): ahí no se realza
        keep = np.clip((dist - 1.0 - 1.5 * max(1, r // 6)) / 1.5, 0, 1)
    else:
        keep = np.ones_like(g)
    ax, f = boost_curve(gain, lo, hi)
    nd = np.sign(d) * np.interp(np.abs(d), ax, f, right=np.nan)
    nd = np.where(np.isnan(nd), d, nd)
    add = np.where(mask, (nd - d) * keep, 0.0)
    return (h + add).astype(h.dtype), (int((np.abs(add) > 1e-3).sum()), float(np.abs(add).max()))


def _vertex_normals(V, T):
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    vn = np.zeros((len(V), 3))
    for k in range(3):
        for c in range(3):
            vn[:, c] += np.bincount(T[:, k], fn[:, c], minlength=len(V))
    return vn / np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)


def _voxel_pick(P, h):
    """Un vértice por cubo de lado h (el primero): índices."""
    k = np.floor((P - P.min(0)) / h).astype(np.int64)
    n = k.max(0) + 1
    key = (k[:, 0] * n[1] + k[:, 1]) * n[2] + k[:, 2]
    return np.unique(key, return_index=True)[1]


def boost_mesh(V, T, gain=2.0, lo=0.1, hi=0.5, size=3.0, chunk=4096):
    """Realce de lo pequeño sobre una malla 3D (modos objeto, 3D, bloque…; mm de la pieza), lo mismo que boost_small
    en el mapa de alturas: cada detalle se mide respecto a su entorno (una cuádrica ajustada a la superficie en un
    radio «size», que sigue las curvas del objeto sin tomarlas por detalle) y se mueve a lo largo de la normal según
    boost_curve. El ajuste es robusto (no cuenta lo que se aparta más de hi: una letra grande, una moldura), como la
    mediana del mapa de alturas; junto a lo grande, a una pared o a una arista viva el realce se apaga.
    -> (V realzados, (vértices cambiados, máximo añadido en mm))"""
    from scipy.spatial import cKDTree
    if gain <= 1 or hi <= lo or len(V) < 10:
        return V, (0, 0.0)
    P = V.astype(np.float64)
    vn = _vertex_normals(P, T)
    qi = _voxel_pick(P, size / 8)                     # puntos para ajustar (unos 200 en cada entorno)
    Q, NQ = P[qi], vn[qi]
    ci = _voxel_pick(P, size / 3)                     # centros donde se ajusta la superficie de referencia
    C = P[ci]
    tq = cKDTree(Q)
    K = int(min(len(Q), 320))
    sig2 = (size / 2) ** 2
    nc = np.zeros((len(C), 3))
    t1 = np.zeros((len(C), 3))
    t2 = np.zeros((len(C), 3))
    coef = np.zeros((len(C), 6))
    valid = np.zeros(len(C), bool)
    for i in range(0, len(C), chunk):
        c = C[i:i + chunk]
        dist, idx = tq.query(c, k=K, distance_upper_bound=size, workers=-1)
        ok = np.isfinite(dist)
        idx = np.where(ok, idx, 0)
        D = Q[idx] - c[:, None]
        n0 = vn[ci[i:i + chunk]]
        wd = np.where(ok, np.exp(-dist ** 2 / (2 * sig2)), 0.0)
        agree = np.einsum('ckj,cj->ck', NQ[idx], n0)
        n = np.einsum('ck,ckj->cj', wd * np.clip(agree, 0, 1), NQ[idx])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        a = np.where(np.abs(n[:, 0:1]) < 0.9, [[1.0, 0, 0]], [[0, 1.0, 0]])
        n[np.linalg.norm(n, axis=1) < 0.5] = [0, 0, 1.0]   # sin normales que valgan: el centro no se usa (good)
        u = np.cross(n, a)
        u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-12)
        v = np.cross(n, u)
        x, y, z = (np.einsum('ckj,cj->ck', D, e) for e in (u, v, n))
        w = wd * (np.einsum('ckj,cj->ck', NQ[idx], n) > 0.3)   # la otra cara de una pared fina no cuenta
        M = np.stack([x * x, x * y, y * y, x, y, np.ones_like(x)], -1)
        A = np.einsum('ck,cki,ckj->cij', w, M, M) + np.eye(6) * 1e-9
        b = np.einsum('ck,cki,ck->ci', w, M, z)
        good = ((w > 0.05).sum(1) >= 20) & (np.linalg.norm(n, axis=1) > 0.5)
        cf = np.zeros((len(c), 6))
        zm = np.nanmedian(np.where(w > 0.05, z, np.nan), axis=1)    # arranque robusto: la mediana del entorno
        w = w * np.clip(2 - np.abs(z - np.nan_to_num(zm)[:, None]) / hi, 0, 1)
        for _ in range(3):                            # ajuste robusto: lo que se aparta más de hi (una letra
            A = np.einsum('ck,cki,ckj->cij', w, M, M) + np.eye(6) * 1e-9          # grande, una moldura) no
            b = np.einsum('ck,cki,ck->ci', w, M, z)                               # cuenta, como la mediana
            ok = good & ((w > 0.05).sum(1) >= 12)                                  # del mapa de alturas
            cf[ok] = np.linalg.solve(A[ok], b[ok][..., None])[..., 0]
            res = z - np.einsum('cki,ci->ck', M, cf)
            w = w * np.clip(2 - np.abs(res) / hi, 0, 1)
        nc[i:i + chunk], t1[i:i + chunk], t2[i:i + chunk], coef[i:i + chunk] = n, u, v, cf
        valid[i:i + chunk] = good
    tc = cKDTree(C)
    ax, f = boost_curve(gain, lo, hi)
    d = np.zeros(len(P))
    nv = np.zeros((len(P), 3))
    big = np.zeros(len(P), bool)
    for i in range(0, len(P), 200_000):
        p = P[i:i + 200_000]
        dist, j = tc.query(p, k=min(4, len(C)), workers=-1)
        if j.ndim == 1:
            dist, j = dist[:, None], j[:, None]
        D = p[:, None] - C[j]
        x, y, z = (np.einsum('vkj,vkj->vk', D, e[j]) for e in (t1, t2, nc))
        q = (coef[j] * np.stack([x * x, x * y, y * y, x, y, np.ones_like(x)], -1)).sum(-1)
        w = valid[j] * (np.einsum('vkj,vj->vk', nc[j], vn[i:i + 200_000]) > 0.3) / (dist + size / 10)
        dz = z - q
        wr = w * np.clip(2 - np.abs(dz) / hi, 0, 1)   # un centro del otro lado de un canto grande no cuenta
        w = np.where(wr.sum(1, keepdims=True) > 0, wr, w)
        sw = np.maximum(w.sum(1), 1e-12)
        d[i:i + 200_000] = (w * dz).sum(1) / sw
        n = np.einsum('vk,vkj->vj', w, nc[j])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        nv[i:i + 200_000] = n
        big[i:i + 200_000] = ((w.sum(1) == 0) | (np.abs(d[i:i + 200_000]) > hi)            # relieve grande,
                              | (np.einsum('vj,vj->v', n, vn[i:i + 200_000]) < 0.7))       # pared o arista viva
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])  # arista viva: normales de vértices vecinos
    ed = np.einsum('ij,ij->i', vn[E[:, 0]], vn[E[:, 1]])           # que se separan más de 45°
    md = np.ones(len(P))
    np.minimum.at(md, E[:, 0], ed)
    np.minimum.at(md, E[:, 1], ed)
    big |= md < 0.7
    # junto a lo grande no se realza: su rampa no es un detalle (ni sale un escalón falso a su lado)
    el = float(np.median(np.linalg.norm(P[T[:4000, 0]] - P[T[:4000, 1]], axis=1))) if len(T) else size / 10
    keep = np.ones(len(P))
    if big.any() and not big.all():
        db = cKDTree(P[big]).query(P[~big], workers=-1, distance_upper_bound=8 * el)[0]
        keep[~big] = np.clip((db - 3 * el) / (3 * el), 0, 1)
    keep[big] = 0
    nd = np.sign(d) * np.interp(np.abs(d), ax, f, right=np.nan)
    add = np.where(np.isnan(nd), 0.0, nd - d) * keep
    out = P + add[:, None] * nv
    changed, mx = int((np.abs(add) > 1e-3).sum()), float(np.abs(add).max(initial=0))
    boost_mesh.last = dict(d=d, big=big, keep=keep, add=add)          # para mirar qué ha hecho (pruebas)
    return out.astype(V.dtype), (changed, mx)


def _boost_result(r, prm, log=lambda s: None):
    """Realce de lo pequeño en una pieza hecha con Poisson (la malla ya está en mm de la pieza)."""
    if getattr(prm, 'boost', 1.0) <= 1 or is_real(prm):
        return r
    s = r.scale                                 # la malla está en mm de la pieza: los umbrales, a la pieza
    zs = prm.z_scale if r.mode == '3d' else 1.0
    lo, hi, size = boost_limits(prm, s, zs)
    r.verts, (n, mx) = boost_mesh(r.verts, r.tris, prm.boost, lo * s * zs, hi * s * zs, size * s)
    r.volume = _volume(r.verts, r.tris)
    log(f'Realce de lo pequeño: ×{prm.boost:g} {boost_text(prm, s, zs)}: {n / max(len(r.verts), 1) * 100:.0f} % '
        f'de los vértices cambia, como mucho {mx:.2f} mm en la pieza')
    return r


def _fill_nearest(grid, valid):
    if valid.all():
        return grid
    idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    return grid[idx[0], idx[1]]


def _fill_interp(grid, valid, holes):
    ring = valid & ndimage.binary_dilation(holes, iterations=2)
    src = np.argwhere(ring)
    dst = np.argwhere(holes)
    if len(src) < 4:
        return grid
    vals = griddata(src, grid[ring], dst, method='linear')
    ok = ~np.isnan(vals)
    grid = grid.copy()
    grid[dst[ok, 0], dst[ok, 1]] = vals[ok]
    return grid


def _fill_smooth(grid, valid, holes, max_cells=400_000, log=lambda s: None):
    """Relleno biarmónico: en cada hueco, la superficie más suave (mínima curvatura, Δ²u = 0) que empalma con el
    borde en altura y pendiente. Continúa las curvas del relieve en vez de tender planos. Los huecos con más de
    max_cells celdas se rellenan con el interpolado (el sistema sería muy grande)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import spsolve
    lab, k = ndimage.label(holes)
    if k == 0:
        return grid
    grid = grid.copy()
    big = 0
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        r0, r1 = max(sl[0].start - 3, 0), min(sl[0].stop + 3, grid.shape[0])
        c0, c1 = max(sl[1].start - 3, 0), min(sl[1].stop + 3, grid.shape[1])
        box = (slice(r0, r1), slice(c0, c1))
        h = lab[box] == i
        if h.sum() > max_cells:
            big += 1
            g2 = _fill_interp(grid, valid, lab == i)
            grid[lab == i] = g2[lab == i]
            continue
        g = grid[box]
        known = ~h & valid[box]
        ny, nx = g.shape
        idx = np.arange(ny * nx).reshape(ny, nx)
        # laplaciano en las celdas con sus 4 vecinas dentro de la caja
        cc = idx[1:-1, 1:-1].ravel()
        rows = np.arange(len(cc))
        R = np.concatenate([rows] * 5)
        C = np.concatenate([cc, idx[:-2, 1:-1].ravel(), idx[2:, 1:-1].ravel(), idx[1:-1, :-2].ravel(),
                            idx[1:-1, 2:].ravel()])
        V = np.concatenate([np.full(len(cc), -4.0)] + [np.ones(len(cc))] * 4)
        L = coo_matrix((V, (R, C)), shape=(len(cc), ny * nx)).tocsr()
        unk = h.ravel()
        fix = ~unk
        A = L[:, unk]
        b = -(L[:, fix] @ np.where(known, g, np.nanmean(g[known]) if known.any() else 0.0).ravel()[fix])
        try:
            u = spsolve((A.T @ A).tocsc(), A.T @ b)
        except Exception:  # noqa: BLE001  sistema singular (hueco sin borde conocido): el interpolado
            continue
        gb = g.copy()
        gb[h] = u
        grid[box] = np.where(h, gb, g)
    if big:
        log(f'  {big} hueco(s) muy grandes rellenados con el interpolado')
    return grid


def _mesh(h, mask, cell, base, cgrid=None, hb=None):
    """Sólido cerrado de la rejilla: arriba las alturas h + base, abajo el plano z = 0 o, con hb (pieza de dos
    caras), el reverso hacia abajo (z = −hb)."""
    nv, nu = h.shape
    hp = np.pad(np.where(mask, h, np.nan), 1, constant_values=np.nan)
    # altura de cada vértice = media de las celdas de la pieza que lo tocan
    st = np.stack([hp[:-1, :-1], hp[:-1, 1:], hp[1:, :-1], hp[1:, 1:]])
    n = (~np.isnan(st)).sum(0)
    vz = np.nan_to_num(st).sum(0) / np.maximum(n, 1) + base
    VY, VX = np.mgrid[0:nv + 1, 0:nu + 1] * cell
    W = nu + 1
    rr, cc = np.nonzero(mask)
    a_, b_ = rr * W + cc, rr * W + cc + 1
    c_, d_ = (rr + 1) * W + cc + 1, (rr + 1) * W + cc
    top = np.concatenate([np.stack([a_, b_, c_], 1), np.stack([a_, c_, d_], 1)])
    NV = VX.size
    bot = top[:, ::-1] + NV
    mp = np.pad(mask, 1)
    walls = []
    for dr, dc, e0, e1 in [(-1, 0, a_, b_), (0, 1, b_, c_), (1, 0, c_, d_), (0, -1, d_, a_)]:
        out = ~mp[rr + 1 + dr, cc + 1 + dc]
        p0, p1 = e0[out], e1[out]
        walls += [np.stack([p1, p0, p0 + NV], 1), np.stack([p1, p0 + NV, p1 + NV], 1)]
    tris = np.concatenate([top, bot] + walls)
    X, Y, Z = VX.ravel(), VY.ravel(), vz.ravel()
    Zb = np.zeros_like(Z)
    if hb is not None:
        bp = np.pad(np.where(mask, hb, np.nan), 1, constant_values=np.nan)
        sb = np.stack([bp[:-1, :-1], bp[:-1, 1:], bp[1:, :-1], bp[1:, 1:]])
        Zb = -(np.nan_to_num(sb).sum(0) / np.maximum((~np.isnan(sb)).sum(0), 1)).ravel()
    verts = np.concatenate([np.stack([X, Y, Z], 1), np.stack([X, Y, Zb], 1)])
    vcol = None
    if cgrid is not None:
        cp = np.pad(cgrid, ((1, 1), (1, 1), (0, 0)), mode='edge')
        vc = (cp[:-1, :-1] + cp[:-1, 1:] + cp[1:, :-1] + cp[1:, 1:]) / 4
        vc = vc.reshape(-1, 3)
        vcol = np.concatenate([vc, vc * 0.6]).clip(0, 255).astype(np.uint8)
    used = np.zeros(len(verts), bool)
    used[tris.ravel()] = True
    remap = np.cumsum(used) - 1
    return verts[used], remap[tris], (vcol[used] if vcol is not None else None)


def _bad_edges(tris, nverts):
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e.sort(1)
    _, c = np.unique(e[:, 0].astype(np.int64) * nverts + e[:, 1], return_counts=True)
    return int((c != 2).sum())


def _volume(verts, tris):
    t = verts[tris]
    return float(np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6)


# ------------------------------------------------------------------ exportación
FORMATS = {'STL binario (*.stl)': 'stl', '3MF (*.3mf)': '3mf', 'STL ASCII (*.stl)': 'stla', 'OBJ (*.obj)': 'obj',
           'PLY con color (*.ply)': 'ply'}
EXT = {'stl': '.stl', 'stla': '.stl', '3mf': '.3mf', 'obj': '.obj', 'ply': '.ply'}


def export(path, r: Result, kind=None, meta=None):
    """Guarda la malla. meta: dict opcional (title, id, scale, source, notes…) que se graba en el fichero
    (cabecera del STL, metadatos del 3MF, comentario del OBJ/PLY) para saber luego de dónde salió."""
    kind = kind or {'.stl': 'stl', '.obj': 'obj', '.ply': 'ply', '.3mf': '3mf'}.get(path[path.rfind('.'):].lower(),
                                                                                 'stl')
    w = {'stl': write_stl, 'stla': write_stl_ascii, 'obj': write_obj, 'ply': write_ply, '3mf': write_3mf}[kind]
    w(path, r, meta or {})


def _meta_line(meta):
    from version import __version__
    parts = [f'nube2stl {__version__}'] + [str(meta[k]) for k in ('id', 'scale', 'title') if meta.get(k)]
    return ' · '.join(parts)


def _normals(t):
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    return n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)


def write_stl(path, r, meta=None):
    t = r.verts.astype(np.float32)[r.tris]
    rec = np.zeros(len(r.tris), dtype=[('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    rec['n'], rec['v'] = _normals(t), t
    head = _meta_line(meta or {}).replace('·', '-').encode('ascii', 'replace')[:80]
    if head.lower().startswith(b'solid'):              # algunos lectores tomarían el binario por ASCII
        head = b'_' + head[:79]
    with open(path, 'wb') as f:
        f.write(head.ljust(80, b' '))
        f.write(np.uint32(len(r.tris)).tobytes())
        rec.tofile(f)


def write_stl_ascii(path, r, meta=None):
    t = r.verts[r.tris]
    n = _normals(t)
    with open(path, 'w') as f:
        f.write('solid ' + _meta_line(meta or {}).replace('·', '-').encode('ascii', 'replace').decode() + '\n')
        for i in range(0, len(t), 100000):
            tb, nb = t[i:i + 100000], n[i:i + 100000]
            f.write(''.join(
                f'facet normal {a[0]:.6e} {a[1]:.6e} {a[2]:.6e}\n outer loop\n'
                f'  vertex {b[0, 0]:.6e} {b[0, 1]:.6e} {b[0, 2]:.6e}\n'
                f'  vertex {b[1, 0]:.6e} {b[1, 1]:.6e} {b[1, 2]:.6e}\n'
                f'  vertex {b[2, 0]:.6e} {b[2, 1]:.6e} {b[2, 2]:.6e}\n endloop\nendfacet\n'
                for a, b in zip(nb, tb)))
        f.write('endsolid nube2stl\n')


def write_obj(path, r, meta=None):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# ' + _meta_line(meta or {}) + '\n')
        if r.vcolors is not None:
            np.savetxt(f, np.c_[r.verts, r.vcolors / 255.0], fmt='v %.4f %.4f %.4f %.3f %.3f %.3f')
        else:
            np.savetxt(f, r.verts, fmt='v %.4f %.4f %.4f')
        np.savetxt(f, r.tris + 1, fmt='f %d %d %d')


def write_ply(path, r, meta=None):
    hascol = r.vcolors is not None
    com = _meta_line(meta or {}).encode('ascii', 'replace').decode()
    hdr = ['ply', 'format binary_little_endian 1.0', f'comment {com}', f'element vertex {len(r.verts)}',
           'property float x', 'property float y', 'property float z']
    if hascol:
        hdr += ['property uchar red', 'property uchar green', 'property uchar blue']
    hdr += [f'element face {len(r.tris)}', 'property list uchar int vertex_indices', 'end_header']
    vd = [('x', '<f4'), ('y', '<f4'), ('z', '<f4')] + ([('r', 'u1'), ('g', 'u1'), ('b', 'u1')] if hascol else [])
    v = np.zeros(len(r.verts), dtype=vd)
    v['x'], v['y'], v['z'] = r.verts.T
    if hascol:
        v['r'], v['g'], v['b'] = r.vcolors.T
    fc = np.zeros(len(r.tris), dtype=[('n', 'u1'), ('i', '<i4', 3)])
    fc['n'], fc['i'] = 3, r.tris
    with open(path, 'wb') as f:
        f.write(('\n'.join(hdr) + '\n').encode())
        v.tofile(f)
        fc.tofile(f)


def write_3mf(path, r, meta=None):
    """3MF (el formato de los laminadores actuales): malla en milímetros dentro de un zip, con metadatos.
    Lo abren PrusaSlicer, Bambu Studio, OrcaSlicer, Cura… y no hay duda de unidades como en el STL."""
    import datetime
    import zipfile
    from xml.sax.saxutils import escape
    from version import __version__
    meta = meta or {}
    V = np.asarray(r.verts, np.float64)
    T = np.asarray(r.tris, np.int64)
    md = {'Application': f'nube2stl {__version__}', 'CreationDate': datetime.date.today().isoformat(),
          'Title': meta.get('title') or meta.get('id') or 'pieza'}
    desc = '; '.join(f'{k}: {meta[k]}' for k in ('id', 'scale', 'source', 'notes') if meta.get(k))
    if desc:
        md['Description'] = desc
    if meta.get('author'):
        md['Designer'] = meta['author']
    head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<model unit="millimeter" xml:lang="es-ES" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
            + ''.join(f' <metadata name="{k}">{escape(str(v))}</metadata>\n' for k, v in md.items())
            + ' <resources>\n  <object id="1" type="model" name="' + escape(str(md['Title']), {'"': '&quot;'})
            + '">\n   <mesh>\n    <vertices>\n')
    mid = '    </vertices>\n    <triangles>\n'
    tail = ('    </triangles>\n   </mesh>\n  </object>\n </resources>\n'
            ' <build>\n  <item objectid="1"/>\n </build>\n</model>\n')
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
             ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
             ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
             '</Types>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            ' <Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
            '</Relationships>\n')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr('[Content_Types].xml', types)
        z.writestr('_rels/.rels', rels)
        with z.open('3D/3dmodel.model', 'w', force_zip64=True) as f:
            f.write(head.encode())
            for i in range(0, len(V), 200_000):
                f.write(''.join('     <vertex x="%.4f" y="%.4f" z="%.4f"/>\n' % tuple(v)
                                for v in V[i:i + 200_000].tolist()).encode())
            f.write(mid.encode())
            for i in range(0, len(T), 200_000):
                f.write(''.join('     <triangle v1="%d" v2="%d" v3="%d"/>\n' % tuple(t)
                                for t in T[i:i + 200_000].tolist()).encode())
            f.write(tail.encode())
