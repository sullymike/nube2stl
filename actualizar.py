"""Buscar e instalar versiones nuevas de nube2stl publicadas en GitHub.

Sin Qt: lo usan la ventana (Ayuda › Buscar actualizaciones…) y la consola (``consola.py --actualizar``).

Cómo se actualiza depende de cómo esté instalado el programa:

- ``git``         carpeta clonada con git: ``git pull`` (solo avance rápido, sin tocar cambios propios) y, si ha
                  cambiado ``requisitos.txt``, ``pip install -r requisitos.txt`` en el mismo Python.
- ``carpeta``     código fuente descomprimido de un .zip: se descarga el .zip de la versión nueva y se copian sus
                  ficheros encima; los que se sustituyen se guardan antes en ``.anterior/<versión>/``.
- ``ejecutable``  programa construido con PyInstaller: no se puede cambiar a sí mismo; se abre la página de la
                  versión para descargarla.

Las versiones son las «releases» del repositorio (etiquetas ``v0.21.0``); si no hay ninguna, las etiquetas.
A GitHub solo se le envía la petición, con el nombre y la versión del programa como agente; nada del usuario.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

from version import GITHUB_REPO, __version__

HERE = os.path.dirname(os.path.abspath(__file__))
API = 'https://api.github.com/repos/{repo}'
# lo que nunca se toca al copiar una versión nueva encima (entornos, ejecutables construidos, copias de seguridad)
KEEP = {'.venv', 'venv', 'venv-build', 'dist', 'build', '.anterior', '.git', '__pycache__'}
DAY = 24 * 3600


class UpdateError(Exception):
    pass


def repo():
    """``usuario/repositorio`` en GitHub ('' si aún no está configurado). NUBE2STL_REPO lo cambia (pruebas)."""
    return (os.environ.get('NUBE2STL_REPO') or GITHUB_REPO or '').strip().strip('/')


def page_url(r=None):
    r = r or repo()
    return f'https://github.com/{r}' if r else ''


def parse_version(s):
    """'v0.21.0' → (0, 21, 0); '0.21' → (0, 21, 0); una preversión ('0.21.0-beta') cuenta como anterior a la final."""
    m = re.match(r'\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$', str(s or ''))
    if not m:
        return None
    nums = tuple(int(x or 0) for x in m.groups()[:3])
    return nums + ((0,) if m.group(4).strip() else (1,))


def newer(a, b=__version__):
    """¿Es la versión ``a`` posterior a ``b``?"""
    va, vb = parse_version(a), parse_version(b)
    return bool(va and vb and va > vb)


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers={'User-Agent': f'nube2stl/{__version__}',
                                               'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return f.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(f'No se encuentra {url}: ¿el repositorio «{repo()}» existe y es público?') from e
        if e.code == 403:
            raise UpdateError('GitHub no responde ahora (demasiadas consultas seguidas); prueba dentro de un rato') \
                from e
        raise UpdateError(f'GitHub ha respondido {e.code} {e.reason}') from e
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f'Sin conexión con GitHub ({getattr(e, "reason", e)})') from e


def latest(r=None, timeout=10):
    """La última versión publicada: dict(version, tag, url, notes, zip, date). La busca en las «releases» y, si no hay
    ninguna, en las etiquetas ``vX.Y.Z``."""
    r = r or repo()
    if not r:
        raise UpdateError('Aún no se ha indicado el repositorio de GitHub (GITHUB_REPO en version.py)')
    base = API.format(repo=r)
    try:
        d = json.loads(_get(base + '/releases/latest', timeout))
        tag = d.get('tag_name', '')
        if parse_version(tag):
            return dict(version=tag.lstrip('v'), tag=tag, url=d.get('html_url') or page_url(r),
                        notes=(d.get('body') or '').strip(), date=(d.get('published_at') or '')[:10],
                        zip=d.get('zipball_url') or f'{base}/zipball/{tag}')
    except UpdateError as e:
        if 'No se encuentra' not in str(e):            # sin «releases»: probar con las etiquetas
            raise
    tags = [t['name'] for t in json.loads(_get(base + '/tags?per_page=100', timeout)) if parse_version(t['name'])]
    if not tags:
        raise UpdateError(f'El repositorio «{r}» no tiene ninguna versión publicada todavía')
    tag = max(tags, key=parse_version)
    return dict(version=tag.lstrip('v'), tag=tag, url=f'{page_url(r)}/releases/tag/{tag}', notes='', date='',
                zip=f'{base}/zipball/{tag}')


def check(timeout=10):
    """dict de ``latest`` más ``new`` (¿hay una posterior a la instalada?) y ``kind`` (cómo se actualizaría)."""
    d = latest(timeout=timeout)
    return dict(d, new=newer(d['version']), current=__version__, kind=install_kind())


# ------------------------------------------------------------------ cómo está instalado
def _git(*args, cwd=HERE, timeout=120):
    try:
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
    except FileNotFoundError as e:
        raise UpdateError('No se encuentra git en este equipo') from e
    except subprocess.TimeoutExpired as e:
        raise UpdateError(f'git {args[0]} tarda demasiado') from e
    return p.returncode, (p.stdout + p.stderr).strip()


def install_kind(folder=HERE):
    if getattr(sys, 'frozen', False):
        return 'ejecutable'
    if os.path.exists(os.path.join(folder, '.git')) and shutil.which('git'):
        return 'git'
    return 'carpeta'


KIND_TEXT = {'git': 'copia de git: se actualiza con «git pull»',
             'carpeta': 'carpeta con el código: se descargan los ficheros nuevos y se guardan los de ahora',
             'ejecutable': 'ejecutable: hay que descargar la versión nueva y sustituir la carpeta'}


def _sha(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None


def _pip(folder, log):
    req = os.path.join(folder, 'requisitos.txt')
    log('Han cambiado las librerías necesarias: pip install -r requisitos.txt (puede tardar unos minutos)…')
    p = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', req], cwd=folder, capture_output=True,
                       text=True)
    tail = '\n'.join((p.stdout + p.stderr).strip().splitlines()[-4:])
    if p.returncode:
        raise UpdateError('pip no ha podido instalar las librerías:\n' + tail)
    log(tail)


def update_git(log=print, folder=HERE):
    """git pull --ff-only; luego pip si ha cambiado requisitos.txt. Devuelve True si ha cambiado algo."""
    code, out = _git('status', '--porcelain', '--untracked-files=no', cwd=folder)
    if code:
        raise UpdateError('git status: ' + out)
    if out:
        raise UpdateError('Hay ficheros del programa cambiados a mano; no se actualiza para no perderlos:\n' + out
                          + '\n(guárdalos con «git stash» o deshazlos con «git checkout -- .» y vuelve a probar)')
    req0 = _sha(os.path.join(folder, 'requisitos.txt'))
    _, head0 = _git('rev-parse', 'HEAD', cwd=folder)
    log('git pull…')
    code, out = _git('pull', '--ff-only', '--tags', cwd=folder, timeout=300)
    log(out)
    if code:
        raise UpdateError('git pull no ha podido actualizar (¿hay cambios propios confirmados?):\n' + out)
    _, head1 = _git('rev-parse', 'HEAD', cwd=folder)
    if _sha(os.path.join(folder, 'requisitos.txt')) != req0:
        _pip(folder, log)
    return head0 != head1


def _download(url, log, timeout=60):
    log(f'Descargando {url}…')
    return _get(url, timeout)


def update_folder(info, log=print, folder=HERE, data=None):
    """Copia encima los ficheros del .zip de la versión ``info``; los que cambian se guardan en .anterior/<versión>.
    ``data`` (bytes del .zip) evita la descarga (pruebas). Devuelve la lista de ficheros cambiados."""
    data = data if data is not None else _download(info['zip'], log)
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError('La descarga no es un .zip válido') from e
    names = [n for n in z.namelist() if not n.endswith('/')]
    root = os.path.commonprefix([n.split('/')[0] + '/' for n in names]) if names else ''
    if not root or not any(n == root + 'nube2stl.py' for n in names):
        raise UpdateError('El .zip descargado no parece de nube2stl (falta nube2stl.py)')
    req0 = _sha(os.path.join(folder, 'requisitos.txt'))
    backup = os.path.join(folder, '.anterior', __version__)
    changed = []
    with tempfile.TemporaryDirectory(dir=folder, prefix='.nueva-') as tmp:
        for n in names:                                    # primero todo a una carpeta temporal: si falla, no se toca
            rel = n[len(root):]
            parts = rel.split('/')
            if not rel or parts[0] in KEEP or '..' in parts or os.path.isabs(rel):
                continue
            dst = os.path.join(tmp, *parts)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(n) as src, open(dst, 'wb') as f:
                shutil.copyfileobj(src, f)
            old = os.path.join(folder, *parts)
            if _sha(old) != _sha(dst):
                changed.append(rel)
        for rel in changed:
            old = os.path.join(folder, *rel.split('/'))
            if os.path.exists(old):
                b = os.path.join(backup, *rel.split('/'))
                os.makedirs(os.path.dirname(b), exist_ok=True)
                shutil.copy2(old, b)
            os.makedirs(os.path.dirname(old), exist_ok=True)
            shutil.copy2(os.path.join(tmp, *rel.split('/')), old)
    log(f'{len(changed)} ficheros nuevos o cambiados' + (f'; los de antes están en {backup}' if changed and
                                                           os.path.isdir(backup) else ''))
    if req0 != _sha(os.path.join(folder, 'requisitos.txt')):
        _pip(folder, log)
    return changed


def update(info=None, log=print):
    """Instala la última versión según cómo esté instalado el programa. Devuelve True si ha cambiado algo (hay que
    cerrar y volver a abrir el programa)."""
    kind = install_kind()
    if kind == 'ejecutable':
        raise UpdateError('El ejecutable no puede actualizarse solo: descarga la versión nueva de '
                          + ((info or {}).get('url') or page_url()))
    if kind == 'git':
        return update_git(log)
    info = info or latest()
    return bool(update_folder(info, log))


# ------------------------------------------------------------------ búsqueda al abrir (una vez al día como mucho)
def due(cfg, now=None):
    """¿Toca buscar al abrir? cfg: la configuración del programa (registro.load_config())."""
    if not repo() or not cfg.get('buscar_actualizaciones', True):
        return False
    return (now or time.time()) - float(cfg.get('ultima_busqueda', 0) or 0) > DAY


def main(argv):
    """consola.py --actualizar [--comprobar]"""
    only_check = '--comprobar' in argv
    try:
        d = check()
    except UpdateError as e:
        print(f'No se puede comprobar: {e}')
        return 2
    print(f'Instalada: {__version__} · última publicada: {d["version"]}' + (f' ({d["date"]})' if d['date'] else ''))
    if not d['new']:
        print('Ya tienes la última versión.')
        return 0
    if d['notes']:
        print('\n' + d['notes'] + '\n')
    print(f'Instalación: {KIND_TEXT[d["kind"]]}')
    if only_check:
        return 1
    try:
        changed = update(d)
    except UpdateError as e:
        print(f'No se ha podido actualizar: {e}')
        return 2
    print('Actualizado a la ' + d['version'] + '.' if changed else 'No había nada que cambiar.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
