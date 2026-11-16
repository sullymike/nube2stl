"""Registro de piezas: qué se ha generado, de qué nube, con qué ajustes y dónde está.

Cada pieza es un fichero JSON en la carpeta del registro (P-0001.json, P-0002.json…) con una miniatura PNG al lado.
Se eligió un fichero por pieza (y no una base de datos) para que la carpeta pueda estar en una unidad compartida o
sincronizada (OneDrive, red): varios ordenadores pueden apuntar ahí sin bloqueos ni ficheros corruptos.
Tras cada cambio se rehace registro.csv (para Excel / LibreOffice), que es solo un índice: la fuente son los JSON.

Lo usan la interfaz (al guardar, en lotes y en la ventana «Registro de piezas») y la consola (--registro).
Sin Qt.
"""
import csv
import datetime
import glob
import hashlib
import json
import os
import platform
import re
import sys

from version import __version__

CSV_NAME = 'registro.csv'
FIELDS_CSV = ['id', 'fecha', 'pieza', 'nube', 'puntos', 'malla', 'formato', 'ancho_mm', 'fondo_mm', 'alto_mm',
              'escala', 'modo', 'volumen_cm3', 'gramos_pla', 'hueca', 'cerrada', 'triangulos', 'desviacion_media_mm',
              'desviacion_p95_mm', 'superficie_escaneada_pct', 'rotulo', 'notas', 'ficha', 'version', 'equipo']


# ------------------------------------------------------------------ configuración
def config_file():
    base = os.environ.get('APPDATA') if sys.platform == 'win32' else \
        os.environ.get('XDG_CONFIG_HOME', os.path.join(os.path.expanduser('~'), '.config'))
    return os.path.join(base or os.path.expanduser('~'), 'nube2stl', 'config.json')


def load_config():
    try:
        with open(config_file(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(**kw):
    cfg = load_config()
    cfg.update(kw)
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    with open(config_file(), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def default_dir():
    home = os.path.expanduser('~')
    for d in ('Documentos', 'Documents', 'Mis documentos'):
        if os.path.isdir(os.path.join(home, d)):
            return os.path.join(home, d, 'nube2stl_registro')
    return os.path.join(home, 'nube2stl_registro')


def registry_dir():
    return load_config().get('registro_dir') or default_dir()


def auto_enabled():
    """Registrar automáticamente cada pieza guardada (por defecto, sí)."""
    return bool(load_config().get('registro_auto', True))


def prefix():
    p = re.sub(r'[^A-Za-z0-9]', '', str(load_config().get('registro_prefijo', 'P'))) or 'P'
    return p[:6]


# ------------------------------------------------------------------ utilidades
def quick_hash(path, chunk=1 << 20):
    """Huella rápida de un fichero (tamaño + SHA-1 del primer y último MB): reconoce la misma nube aunque se mueva
    o cambie de nombre, sin leer gigas."""
    try:
        size = os.path.getsize(path)
        h = hashlib.sha1(str(size).encode())
        with open(path, 'rb') as f:
            h.update(f.read(chunk))
            if size > 2 * chunk:
                f.seek(-chunk, os.SEEK_END)
                h.update(f.read(chunk))
        return h.hexdigest()[:16]
    except OSError:
        return ''


def scale_text(scale):
    if not scale:
        return ''
    return f'1:{1 / scale:.3g}' if scale < 1 else (f'{scale:.3g}:1' if scale > 1 else '1:1')


def _entry_path(d, eid):
    return os.path.join(d, f'{eid}.json')


def _write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)                       # nunca queda un JSON a medias


def new_id(d=None):
    """Reserva el siguiente número libre creando el fichero en exclusiva (seguro con varios ordenadores)."""
    d = d or registry_dir()
    os.makedirs(d, exist_ok=True)
    pre = prefix()
    nums = [int(m.group(1)) for f in os.listdir(d) for m in [re.match(rf'{pre}-(\d+)\.json$', f)] if m]
    n = max(nums, default=0) + 1
    while True:
        eid = f'{pre}-{n:04d}'
        try:
            fd = os.open(_entry_path(d, eid), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return eid
        except FileExistsError:
            n += 1


# ------------------------------------------------------------------ entradas
def make_entry(r, prm, source, out_path=None, kind=None, title=None):
    """Datos de una pieza (sin id). source: dict de la nube ('path', 'points', 'units', 'kind', 'removed')."""
    import desviacion
    source = source or {}
    sx, sy, sz = (float(v) for v in r.size)
    st = desviacion.stats(r) or {}
    cloud = source.get('path', '')
    e = {
        'pieza': title or (os.path.splitext(os.path.basename(cloud))[0] if cloud else 'pieza'),
        'fecha': datetime.datetime.now().isoformat(timespec='seconds'),
        'nube': {'ruta': os.path.abspath(cloud) if cloud else '', 'nombre': os.path.basename(cloud),
                 'bytes': os.path.getsize(cloud) if cloud and os.path.exists(cloud) else 0,
                 'huella': quick_hash(cloud) if cloud else '', 'puntos': int(source.get('points', 0) or 0),
                 'unidades': source.get('units', ''), 'tipo': source.get('kind', 'nube'),
                 'quitados': int(source.get('removed', 0) or 0)},
        'salidas': [],
        'ficha': '',
        'medidas_mm': [round(sx, 2), round(sy, 2), round(sz, 2)],
        'escala': scale_text(r.scale), 'factor_escala': float(r.scale),
        'modo': r.mode,
        'volumen_cm3': round(r.volume / 1000, 2), 'gramos_pla': round(r.grams_pla, 1), 'hueca': bool(r.hollow),
        'cerrada': r.bad_edges == 0, 'triangulos': int(len(r.tris)),
        'desviacion': {k: round(st[k], 4) for k in ('mean', 'p50', 'p95', 'max', 'covered') if k in st},
        'rotulo': prm.label, 'notas': '',
        'ajustes': json.loads(prm.to_json()),
        'version': __version__, 'equipo': platform.node(),
    }
    if out_path:
        e['salidas'].append(_output(out_path, kind))
    return e


def _output(path, kind=None):
    return {'ruta': os.path.abspath(path), 'formato': kind or os.path.splitext(path)[1].lstrip('.').lower(),
            'bytes': os.path.getsize(path) if os.path.exists(path) else 0,
            'fecha': datetime.datetime.now().isoformat(timespec='seconds')}


def add(entry, eid=None, r=None, d=None):
    """Guarda la entrada (con el id dado o uno nuevo), su miniatura si se da la malla, y rehace el CSV."""
    d = d or registry_dir()
    eid = eid or new_id(d)
    entry = dict(entry, id=eid)
    _write_json(_entry_path(d, eid), entry)
    if r is not None:
        try:
            thumbnail(r, os.path.join(d, f'{eid}.png'))
        except Exception:                       # la miniatura es un extra: nunca debe impedir registrar
            pass
    write_csv(d)
    return eid


def get(eid, d=None):
    d = d or registry_dir()
    try:
        with open(_entry_path(d, eid), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def update(eid, d=None, **fields):
    d = d or registry_dir()
    e = get(eid, d)
    if e is None:
        return None
    e.update(fields)
    _write_json(_entry_path(d, eid), e)
    write_csv(d)
    return e


def add_output(eid, path, kind=None, d=None):
    """Otra salida de la misma pieza (otro formato, o guardada de nuevo)."""
    e = get(eid, d)
    if e is None:
        return None
    outs = [o for o in e.get('salidas', []) if os.path.abspath(o['ruta']) != os.path.abspath(path)]
    return update(eid, d, salidas=outs + [_output(path, kind)])


def delete(eid, d=None):
    """Quita la entrada del registro (no borra la malla ni la nube)."""
    d = d or registry_dir()
    for ext in ('.json', '.png'):
        try:
            os.remove(os.path.join(d, eid + ext))
        except OSError:
            pass
    write_csv(d)


def entries(d=None):
    """Todas las entradas, la más reciente primero (las ilegibles o a medias se saltan)."""
    d = d or registry_dir()
    out = []
    for p in glob.glob(os.path.join(d, '*.json')):
        try:
            with open(p, encoding='utf-8') as f:
                e = json.load(f)
            if isinstance(e, dict) and e.get('id'):
                out.append(e)
        except (OSError, ValueError):
            continue
    out.sort(key=lambda e: (e.get('fecha', ''), e.get('id', '')), reverse=True)
    return out


def search(text, d=None, items=None):
    """Entradas que contienen todas las palabras de text (en id, nombre, nube, rótulo, notas, modo, escala…)."""
    items = entries(d) if items is None else items
    words = [w for w in text.lower().split() if w]
    if not words:
        return items

    def hay(e):
        s = ' '.join(str(x) for x in (e.get('id'), e.get('pieza'), e.get('nube', {}).get('nombre'),
                                      e.get('nube', {}).get('ruta'), e.get('rotulo'), e.get('notas'),
                                      e.get('modo'), e.get('escala'), e.get('fecha', '')[:10],
                                      ' '.join(o.get('ruta', '') for o in e.get('salidas', []))))
        return s.lower()
    return [e for e in items if all(w in hay(e) for w in words)]


def same_cloud(e, path):
    """¿path es la misma nube con la que se hizo la entrada? (por la huella; None si no se puede saber)."""
    h = e.get('nube', {}).get('huella')
    if not h or not os.path.exists(path):
        return None
    return quick_hash(path) == h


def find_cloud(e):
    """Ruta de la nube de la entrada: la guardada si existe; si no, una con el mismo nombre y huella junto a las
    mallas o en la carpeta del registro (por si se movió). None si no aparece."""
    n = e.get('nube', {})
    p = n.get('ruta', '')
    if p and os.path.exists(p):
        return p
    name = n.get('nombre')
    if not name:
        return None
    dirs = {os.path.dirname(o.get('ruta', '')) for o in e.get('salidas', [])} | {registry_dir()}
    for d in dirs:
        q = os.path.join(d, name)
        if d and os.path.exists(q) and same_cloud(e, q) is not False:
            return q
    return None


def row(e):
    """Fila del CSV."""
    out = e.get('salidas') or [{}]
    last = out[-1]
    dv = e.get('desviacion', {})
    m = e.get('medidas_mm') or ['', '', '']

    def num(x, f='{:.2f}'):
        return f.format(x).replace('.', ',') if isinstance(x, (int, float)) and not isinstance(x, bool) else ''
    return {'id': e.get('id', ''), 'fecha': e.get('fecha', '').replace('T', ' '), 'pieza': e.get('pieza', ''),
            'nube': e.get('nube', {}).get('ruta', ''), 'puntos': e.get('nube', {}).get('puntos', ''),
            'malla': last.get('ruta', ''), 'formato': last.get('formato', ''),
            'ancho_mm': num(m[0], '{:.1f}'), 'fondo_mm': num(m[1], '{:.1f}'), 'alto_mm': num(m[2], '{:.1f}'),
            'escala': e.get('escala', ''), 'modo': e.get('modo', ''),
            'volumen_cm3': num(e.get('volumen_cm3'), '{:.1f}'), 'gramos_pla': num(e.get('gramos_pla'), '{:.0f}'),
            'hueca': 'sí' if e.get('hueca') else 'no', 'cerrada': 'sí' if e.get('cerrada') else 'no',
            'triangulos': e.get('triangulos', ''),
            'desviacion_media_mm': num(dv.get('mean'), '{:.3f}'), 'desviacion_p95_mm': num(dv.get('p95'), '{:.3f}'),
            'superficie_escaneada_pct': num(dv.get('covered'), '{:.0f}'),
            'rotulo': e.get('rotulo', ''), 'notas': e.get('notas', ''), 'ficha': e.get('ficha', ''),
            'version': e.get('version', ''), 'equipo': e.get('equipo', '')}


def write_csv(d=None, path=None, items=None):
    """Índice para hojas de cálculo (punto y coma, UTF-8 con BOM: Excel lo abre bien con acentos)."""
    d = d or registry_dir()
    path = path or os.path.join(d, CSV_NAME)
    items = entries(d) if items is None else items
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, FIELDS_CSV, delimiter=';')
            w.writeheader()
            for e in sorted(items, key=lambda e: e.get('id', '')):
                w.writerow(row(e))
        os.replace(tmp, path)
    except OSError:                             # abierto en Excel (Windows lo bloquea): se rehará en el próximo cambio
        return None
    return path


def thumbnail(r, path, px=220):
    """Miniatura PNG de la pieza (perspectiva sombreada, fondo transparente)."""
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    import ficha
    V, F = ficha._light_mesh(r, 25_000)
    V = V - (V.max(0) + V.min(0)) / 2
    tall = np.ptp(V[:, 2]) > 0.5 * np.ptp(V[:, :2], 0).max()
    fig = Figure(figsize=(px / 100, px / 100), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.96])
    ficha._view(ax, V, F, ficha._rot(-35, 20 if tall else 35), '')
    ax.axis('off')
    fig.savefig(path, transparent=True)
    return path


def meta(eid, e):
    """Metadatos para grabar en el fichero de la malla (cabecera STL, 3MF…)."""
    return {'id': eid, 'title': e.get('pieza', ''), 'scale': e.get('escala', ''),
            'source': e.get('nube', {}).get('nombre', ''), 'notes': e.get('rotulo', '')}


def export_registered(path, r, prm, source, kind=None, eid=None, title=None, d=None, log=lambda s: None):
    """Guarda la malla y la anota en el registro. Si eid ya existe (la misma pieza guardada otra vez, en otro
    formato…) añade la salida a esa entrada; si no, crea una nueva. El id queda grabado en el fichero.
    Devuelve el id (o None si no se pudo registrar: la malla se guarda igual)."""
    import relief
    d = d or registry_dir()
    try:
        if eid and get(eid, d) is not None:
            e = get(eid, d)
            relief.export(path, r, kind, meta(eid, e))
            add_output(eid, path, kind, d)
            log(f'Registro: {eid} (añadida la salida {os.path.basename(path)})')
            return eid
        e = make_entry(r, prm, source, None, kind, title)
        eid = new_id(d)
    except OSError as ex:
        log(f'AVISO: no se pudo usar la carpeta del registro ({d}): {ex}. La pieza se guarda sin registrar.')
        relief.export(path, r, kind)
        return None
    try:
        relief.export(path, r, kind, meta(eid, e))
    except Exception:
        try:
            os.remove(_entry_path(d, eid))      # liberar el número reservado
        except OSError:
            pass
        raise
    e['salidas'].append(_output(path, kind))
    add(e, eid, r, d)
    log(f'Registro: pieza {eid} anotada en {d}')
    return eid
