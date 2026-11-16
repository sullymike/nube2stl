"""Impresoras que el usuario ya tiene: las de sus laminadores y las guardadas en nube2stl.

Laminadores reconocidos (solo lectura, nunca se modifican sus ficheros):
  - OrcaSlicer y Bambu Studio: perfiles JSON de máquina (user/*/machine), resolviendo la herencia («inherits»)
    con los perfiles de sistema (system/<marca>/machine).
  - PrusaSlicer y SuperSlicer: printer/*.ini con bed_shape.
  - UltiMaker Cura: definition_changes/*.inst.cfg con machine_width / machine_depth.
Las impresoras propias se guardan en impresoras.json, en la carpeta de configuración del usuario.

Cada impresora es un dict: name, source, tech ('fdm'/'resina'), bed_x, bed_y, nozzle, layer.
"""
import configparser
import glob
import json
import os
import re
import sys

import recetas

APP_NAMES = {'BambuStudio': 'Bambu Studio'}


def _config_dirs(app):
    """Carpetas donde puede estar la configuración de una aplicación en Linux, Windows o macOS."""
    home = os.path.expanduser('~')
    out = [os.path.join(home, '.config', app),
           os.path.join(home, '.var', 'app', f'com.{app.lower()}.{app}', 'config', app)]   # flatpak (aprox.)
    out += glob.glob(os.path.join(home, '.var', 'app', '*', 'config', app))
    if sys.platform == 'win32':
        out.append(os.path.join(os.environ.get('APPDATA', ''), app))
    if sys.platform == 'darwin':
        out.append(os.path.join(home, 'Library', 'Application Support', app))
    seen, res = set(), []
    for d in out:
        if os.path.isdir(d) and os.path.realpath(d) not in seen:
            seen.add(os.path.realpath(d))
            res.append(d)
    return res


def own_file():
    base = os.environ.get('APPDATA') if sys.platform == 'win32' else \
        os.environ.get('XDG_CONFIG_HOME', os.path.join(os.path.expanduser('~'), '.config'))
    return os.path.join(base, 'nube2stl', 'impresoras.json')


def _bed_from_points(pts):
    xy = [tuple(float(v) for v in p.split('x')) for p in pts if 'x' in p]
    if not xy:
        return None
    xs, ys = zip(*xy)
    return round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1)


def _layer_from_profile(name, default=0.2):
    m = re.match(r'\s*(\d+(?:\.\d+)?)\s*mm', name or '')
    return float(m.group(1)) if m else default


def _first(v):
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, str):
        v = v.split(',')[0].split(';')[0]
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ OrcaSlicer / Bambu Studio
def _orca_like(app):
    out = []
    for root in _config_dirs(app):
        cache = {}
        index = {}                   # nombre de perfil de sistema -> fichero (índice de cada marca: system/<marca>.json)
        for vf in glob.glob(os.path.join(root, 'system', '*.json')):
            try:
                vj = json.load(open(vf, encoding='utf-8'))
            except (OSError, ValueError):
                continue
            vdir = os.path.splitext(vf)[0]
            for it in vj.get('machine_list', []):
                if 'name' in it and 'sub_path' in it:
                    index.setdefault(it['name'], os.path.join(vdir, it['sub_path']))

        def norm(n):                 # Orca ha renombrado perfiles: «… (0.4 nozzle)» -> «… 0.4 nozzle»
            return re.sub(r'\s+', ' ', n.replace('(', ' ').replace(')', ' ')).strip().lower()
        nindex = {norm(k): v for k, v in index.items()}

        def load_system(name):
            if name not in cache:
                cache[name] = None
                f = index.get(name) or nindex.get(norm(name))
                if f:
                    try:
                        cache[name] = json.load(open(f, encoding='utf-8'))
                    except (OSError, ValueError):
                        pass
            return cache[name]

        def resolve(d, depth=0):
            par = d.get('inherits')
            if par and depth < 12:
                pd = load_system(par)
                if pd:
                    return {**resolve(pd, depth + 1), **d}
            return d

        files = glob.glob(os.path.join(root, 'user', '*', 'machine', '*.json'))
        try:      # la impresora seleccionada ahora en el laminador, aunque sea un perfil de sistema
            conf = json.load(open(os.path.join(root, f'{app}.conf'), encoding='utf-8'))
            current = (conf.get('presets') or {}).get('machine')
        except (OSError, ValueError):
            current = None
        items = []
        for f in files:
            if 'sync-conflict' in f:
                continue
            try:
                items.append(json.load(open(f, encoding='utf-8')))
            except (OSError, ValueError):
                continue
        if current and not any(d.get('name') == current for d in items):
            d = load_system(current)
            if d:
                items.append(d)
        for d in items:
            r = resolve(d)
            bed = _bed_from_points(r.get('printable_area') or [])
            noz = _first(r.get('nozzle_diameter'))
            if not bed or not noz:
                continue
            out.append(dict(name=r.get('name', '?'), source=APP_NAMES.get(app, app), tech='fdm',
                            bed_x=bed[0], bed_y=bed[1],
                            nozzle=noz, layer=_layer_from_profile(r.get('default_print_profile'))))
    return out


# ------------------------------------------------------------------ PrusaSlicer / SuperSlicer
def _prusa_like(app):
    out = []
    for root in _config_dirs(app):
        for f in glob.glob(os.path.join(root, 'printer', '*.ini')):
            kv = {}
            try:
                for line in open(f, encoding='utf-8', errors='replace'):
                    if '=' in line and not line.lstrip().startswith('#'):
                        k, v = line.split('=', 1)
                        kv[k.strip()] = v.strip()
            except OSError:
                continue
            bed = _bed_from_points(kv.get('bed_shape', '').split(','))
            noz = _first(kv.get('nozzle_diameter'))
            if not bed or not noz:
                continue
            out.append(dict(name=os.path.splitext(os.path.basename(f))[0], source=app, tech='fdm',
                            bed_x=bed[0], bed_y=bed[1], nozzle=noz, layer=0.2))
    return out


# ------------------------------------------------------------------ Cura
def _cura():
    out = []
    home = os.path.expanduser('~')
    roots = glob.glob(os.path.join(home, '.local', 'share', 'cura', '*'))
    if sys.platform == 'win32':
        roots += glob.glob(os.path.join(os.environ.get('APPDATA', ''), 'cura', '*'))
    if sys.platform == 'darwin':
        roots += glob.glob(os.path.join(home, 'Library', 'Application Support', 'cura', '*'))
    for root in roots:
        nozzles = {}
        for f in glob.glob(os.path.join(root, 'definition_changes', '*.inst.cfg')):
            cp = configparser.ConfigParser(interpolation=None)
            try:
                cp.read(f, encoding='utf-8')
            except (configparser.Error, OSError):
                continue
            if cp.has_option('values', 'machine_nozzle_size'):
                nozzles[cp.get('general', 'name', fallback='')] = _first(cp.get('values', 'machine_nozzle_size'))
        for f in glob.glob(os.path.join(root, 'definition_changes', '*.inst.cfg')):
            cp = configparser.ConfigParser(interpolation=None)
            try:
                cp.read(f, encoding='utf-8')
                w, d = cp.getfloat('values', 'machine_width'), cp.getfloat('values', 'machine_depth')
            except (configparser.Error, OSError, ValueError):
                continue
            name = re.sub(r'_settings$', '', cp.get('general', 'name', fallback=os.path.basename(f)))
            noz = next((v for k, v in nozzles.items() if k.startswith(name) and v), 0.4)
            out.append(dict(name=name, source='Cura', tech='fdm', bed_x=w, bed_y=d, nozzle=noz, layer=0.2))
    return out


# ------------------------------------------------------------------ propias
def load_own():
    try:
        return [dict(p, source='nube2stl') for p in json.load(open(own_file(), encoding='utf-8'))]
    except (OSError, ValueError):
        return []


def save_own(printer):
    """Añade (o sustituye, si ya existe el nombre) una impresora propia."""
    items = [p for p in load_own() if p['name'] != printer['name']]
    items.append({k: printer[k] for k in ('name', 'tech', 'bed_x', 'bed_y', 'nozzle', 'layer')})
    os.makedirs(os.path.dirname(own_file()), exist_ok=True)
    with open(own_file(), 'w', encoding='utf-8') as f:
        json.dump(sorted(items, key=lambda p: p['name'].lower()), f, ensure_ascii=False, indent=1)


def delete_own(name):
    items = [p for p in load_own() if p['name'] != name]
    with open(own_file(), 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ todo junto
def builtin():
    return [dict(name=n, source='modelos', tech=t, bed_x=x, bed_y=y, nozzle=noz, layer=lay)
            for n, t, x, y, noz, lay in recetas.PRINTERS]


def from_slicers():
    found = []
    for fn, arg in [(_orca_like, 'OrcaSlicer'), (_orca_like, 'BambuStudio'), (_prusa_like, 'PrusaSlicer'),
                    (_prusa_like, 'SuperSlicer'), (_cura, None)]:
        try:
            found += fn(arg) if arg else fn()
        except Exception:  # noqa: BLE001  un perfil raro no debe impedir abrir el asistente
            pass
    seen, out = set(), []
    for p in sorted(found, key=lambda p: (p['source'], p['name'].lower())):
        key = (p['source'], p['name'])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def groups():
    """[(título del grupo, [impresoras])] en el orden en que se muestran: propias, laminadores, modelos."""
    res = []
    own = load_own()
    if own:
        res.append(('Mis impresoras (guardadas en nube2stl)', own))
    sl = from_slicers()
    for src in dict.fromkeys(p['source'] for p in sl):
        res.append((f'Mis impresoras en {src}', [p for p in sl if p['source'] == src]))
    res.append(('Modelos habituales', builtin()))
    return res


if __name__ == '__main__':
    for title, items in groups():
        print(f'== {title} ({len(items)})')
        for p in items:
            print(f"  {p['name']}: cama {p['bed_x']:g} × {p['bed_y']:g}, boquilla {p['nozzle']:g}, capa {p['layer']:g}")
