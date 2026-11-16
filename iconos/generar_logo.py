"""Genera iconos/logo.svg (el logo de nube2stl): la nube de puntos se convierte en un relieve impreso por capas.

Uso: python3 iconos/generar_logo.py   (luego la aplicación lo usa como icono, cabecera, portada y Acerca de)
"""
import numpy as np
import os
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.svg')
g = lambda x, c, w: np.exp(-((x - c) / w) ** 2)
def prof(x):                      # perfil de la piedra: dos relieves, el de la derecha más alto
    return 176 - 62 * g(x, 84, 30) - 96 * g(x, 178, 30) + 6 * g(x, 132, 10)
X0, X1, XS, YB = 34, 222, 124, 196
xs = np.linspace(XS, X1, 80)
solid = f'M{XS} {YB} L{XS} {prof(XS):.1f} ' + ' '.join(f'L{x:.1f} {prof(x):.1f}' for x in xs) + f' L{X1} {YB} Z'
rng = np.random.default_rng(3)
dots = []
step = 8.6
for x in np.arange(X0 + 4, XS - 2, step):
    t = (x - X0) / (XS - X0)
    for y in np.arange(prof(x) + 2, YB - 2, step):
        if rng.random() > 0.30 + 0.70 * t:          # nube más suelta hacia la izquierda
            continue
        jx, jy = rng.normal(0, 1.0, 2) * (1.6 - t)
        dots.append(f'<circle cx="{x + jx:.1f}" cy="{y + jy:.1f}" r="{2.0 + 1.7 * t:.1f}" '
                    f'fill="#ffe6d3" opacity="{0.40 + 0.60 * t:.2f}"/>')
# capas de impresión: segmentos horizontales solo dentro del sólido (Qt no admite clipPath)
layers = []
xx = np.linspace(XS, X1, 400)
for y in np.arange(YB - 7, 60, -7.5):
    inside = prof(xx) < y - 1.5
    edges = np.flatnonzero(np.diff(np.r_[0, inside.astype(int), 0]))
    for a, b in zip(edges[::2], edges[1::2]):
        x0, x1 = xx[a], xx[b - 1]
        if x1 - x0 > 2:
            layers.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{x1 - x0:.1f}" height="1.8" fill="#8a3d12" opacity="0.30"/>')
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <!-- nube2stl: la nube de puntos (izquierda) se convierte en un relieve sólido impreso por capas (derecha) -->
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1f3b4d"/><stop offset="1" stop-color="#2a7f9e"/>
    </linearGradient>
    <linearGradient id="solid" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f6a468"/><stop offset="1" stop-color="#e07a3f"/>
    </linearGradient>
  </defs>
  <rect x="8" y="8" width="240" height="240" rx="54" fill="url(#bg)"/>
  <rect x="10" y="10" width="236" height="236" rx="52" fill="none" stroke="#ffffff" stroke-opacity="0.35" stroke-width="4"/>
  {''.join(dots)}
  <path d="{solid}" fill="url(#solid)"/>
  {''.join(layers)}
  <rect x="{X0 - 6}" y="{YB}" width="{X1 - X0 + 12}" height="18" rx="4" fill="#c9642b"/>
  <rect x="{X0 - 6}" y="{YB}" width="{X1 - X0 + 12}" height="3" fill="#ffffff" opacity="0.20"/>
</svg>
'''
open(OUT, 'w').write(svg); print(len(dots), 'puntos', len(layers), 'capas')

# icono de Windows (.ico con varios tamaños) a partir de logo.png; logo.png se renderiza desde el SVG con Qt
try:
    from PIL import Image
    png = os.path.join(os.path.dirname(OUT), 'logo.png')
    if os.path.exists(png):
        Image.open(png).save(os.path.join(os.path.dirname(OUT), 'logo.ico'),
                             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
except ImportError:
    pass
