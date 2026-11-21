"""Notas de una versión para GitHub (Markdown), sacadas de version.HISTORY. Las usa .github/workflows/publicar.yml
y también se ven en Ayuda › Buscar actualizaciones.

Uso: python notas_version.py 0.26.0 > notas.md
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


def notes(tag, repo=None):
    repo = repo or version.GITHUB_REPO
    hist = list(version.HISTORY)
    pos = next((i for i, h in enumerate(hist) if h[0] == tag), None)
    if pos is None:
        raise SystemExit(f'La versión {tag} no está en version.HISTORY')
    _, date, items = hist[pos]
    prev = hist[pos + 1][0] if pos + 1 < len(hist) else None
    out = [f'## Novedades ({date})', '']
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
    print(notes(sys.argv[1] if len(sys.argv) > 1 else version.__version__), end='')
