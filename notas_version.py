"""Notas de una versión para GitHub (Markdown), sacadas de version.HISTORY. Las usa .github/workflows/publicar.yml
y también se ven en Ayuda › Buscar actualizaciones.

Uso: python notas_version.py 0.26.0 [--anterior 0.25.1] > notas.md
Cada novedad «Tema: explicación» sale como «**Tema:** explicación».
"""
import sys

import version


def lead(item):
    """«Tema: resto» -> «**Tema:** resto», si el tema es corto (una etiqueta, no una frase)."""
    head, sep, rest = item.partition(': ')
    if sep and len(head) <= 70 and ';' not in head and '. ' not in head:
        return f'**{head}:** {rest}'
    return item


def notes(tag, repo=None, prev=None):
    """Notas de la versión tag. prev: la versión publicada anterior (por defecto, la anterior de HISTORY; '' si es
    la primera publicada: sin enlace a los cambios); si hubo versiones en medio que no se publicaron aparte, sus
    novedades van también, cada una con su título."""
    repo = repo or version.GITHUB_REPO
    hist = list(version.HISTORY)
    names = [h[0] for h in hist]
    if tag not in names:
        raise SystemExit(f'La versión {tag} no está en version.HISTORY')
    pos = names.index(tag)
    if prev is None:
        prev = names[pos + 1] if pos + 1 < len(names) else None
    end = names.index(prev) if prev in names else pos + 1
    group = hist[pos:max(end, pos + 1)]
    out = []
    for i, (v, date, items) in enumerate(group):
        if i == 0:
            out += [f'## Novedades ({date})', '']
        else:
            out += ['', f'### También de la {v} (no se publicó aparte)', '']
        out += [f'- {lead(x)}' for x in items]
    out += ['', '## Instalar o actualizar', '',
            '- **Ya instalado:** *Ayuda › Buscar actualizaciones…* la descarga e instala (o `git pull` si se '
            'instaló con git, o `python3 consola.py --actualizar`).',
            '- **Instalación nueva:** descarga *Source code (zip)* aquí abajo, o `git clone`, y ejecuta '
            '`instalar_linux.sh` o `instalar_windows.bat`. Hace falta Python 3.10, 3.11 o 3.12.',
            '- La ayuda completa está dentro del programa (**F1**) y en `ayuda.html`.']
    if prev and repo:
        out += ['', f'Cambios en el código: https://github.com/{repo}/compare/v{prev}...v{tag}']
    return '\n'.join(out) + '\n'


if __name__ == '__main__':
    args = sys.argv[1:]
    prev = None
    if '--anterior' in args:                      # la versión publicada antes (si no es la anterior de HISTORY)
        k = args.index('--anterior')
        prev = args[k + 1]
        del args[k:k + 2]
    print(notes(args[0] if args else version.__version__, prev=prev), end='')
