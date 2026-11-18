#!/usr/bin/env python3
"""nube2stl sin interfaz gráfica: línea de comandos y procesos auxiliares de la generación.

Uso:  python3 nube2stl.py --cli entrada.ply salida.stl [--param valor ...]
      python3 consola.py  --cli entrada.ply salida.stl [--param valor ...]      (lo mismo)
      nube2stl-consola    --cli entrada.ply salida.stl …                         (ejecutable)
      python3 consola.py  --actualizar [--comprobar]     buscar en GitHub una versión nueva (e instalarla)

En el ejecutable, la interfaz lanza también aquí sus procesos auxiliares (--proceso y --decimar), porque no hay
un Python aparte. Este módulo no carga nunca PyQt5: PyMeshLab trae su propio Qt y no pueden convivir.
"""
import argparse
import os
import sys
import time
from dataclasses import fields

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import relief  # noqa: E402
from relief import Params  # noqa: E402
from version import AUTHOR, YEAR, __version__  # noqa: E402


def cli(argv):
    ap = argparse.ArgumentParser(prog='nube2stl --cli',
                                 description=f'nube2stl {__version__}: nube de puntos o malla (PLY, XYZ, PTS, CSV, LAS, LAZ, E57, OBJ, STL) -> malla cerrada')
    ap.add_argument('entrada')
    ap.add_argument('salida', help='.stl, .3mf, .obj o .ply')
    ap.add_argument('--ajustes', help='fichero .json de ajustes guardado desde la interfaz')
    ap.add_argument('--ascii', action='store_true', help='STL en ASCII')
    ap.add_argument('--ficha', metavar='FICHA.pdf', help='escribir también la ficha PDF de la pieza')
    ap.add_argument('--registro', action='store_true',
                    help='anotar la pieza en el registro de piezas (la carpeta elegida en la interfaz)')
    ap.add_argument('--nombre', help='nombre de la pieza en el registro (por defecto, el de la nube)')
    ap.add_argument('--reverso', metavar='FICHERO',
                    help='pieza de dos caras (moneda, medalla, tablilla): la nube de la otra cara; se pone debajo, '
                         'encajada por el contorno (o con --giro_reverso), con «--base» de alma entre las dos')
    ap.add_argument('--giro_reverso', type=float, help='giro del reverso en grados (por defecto, por el contorno)')
    ap.add_argument('--perfil', metavar='PERFIL.svg',
                    help='vasija a partir de un fragmento (--mode objeto --surface revolucion): guardar también el '
                         'perfil para el dibujo, a tamaño real')
    ap.add_argument('--clases', metavar='2,6',
                    help='LiDAR (LAS/LAZ): clases que se usan, separadas por comas (2 suelo, 6 edificios, 3-5 '
                         'vegetación…; por defecto todas menos el ruido). «suelo» calcula el suelo si el fichero no '
                         'trae clases')
    ap.add_argument('--unir', metavar='FICHERO', action='append', default=[],
                    help='unir otra nube ya alineada con la principal (mismas coordenadas y unidades); se puede '
                         'repetir. Para alinearla, únela en la interfaz y guarda los ajustes: --ajustes la recuerda')
    for f in fields(Params):
        if f.type in (list, 'list'):              # solo con --ajustes (p. ej. keep_groups, elegidos en la interfaz)
            continue
        if f.type in (bool, 'bool'):
            ap.add_argument(f'--{f.name}', type=lambda s: s.lower() in ('1', 'true', 'si', 'sí', 'yes'),
                            metavar='true/false')
        else:
            t = {'float': float, 'int': int, 'str': str}.get(f.type if isinstance(f.type, str) else f.type.__name__)
            ap.add_argument(f'--{f.name}', type=t)
    a = ap.parse_args(argv)
    prm = Params.from_json(open(a.ajustes).read()) if a.ajustes else Params()
    for f in fields(Params):
        v = getattr(a, f.name, None)
        if v is not None:
            setattr(prm, f.name, v)
    import unir
    if a.reverso:
        prm.reverse = [dict(path=os.path.abspath(a.reverso), units=prm.units, angle=a.giro_reverso, flip=None)]
    if a.clases:
        import entrada
        prm.classes = [entrada.GROUND_AUTO if c.strip().lower() == 'suelo' else int(c) for c in a.clases.split(',')
                       if c.strip()]
    prm.merge = list(prm.merge or []) + [dict(path=os.path.abspath(f), units='igual', M=unir.IDENTITY) for f in a.unir]
    sys.stdout.reconfigure(errors='replace')     # consola o fichero sin UTF-8 (Windows): no fallar por «≈», «→»
    t = time.time()
    print(f'nube2stl {__version__}')
    import entrada
    lines = []

    def log(s):
        print(s)
        lines.append(s)
    xyz, rgb, nrm, info = entrada.load_points(a.entrada, prm, log)
    if a.perfil:
        if info.get('vessel'):
            import revolucion
            sec = dict(info['vessel']['section'])
            if prm.up == '-z':
                sec['mask'] = sec['mask'][::-1]
            revolucion.profile_svg(a.perfil, sec, os.path.basename(a.entrada))
            print(f'Perfil: {a.perfil}')
        else:
            print('AVISO: --perfil solo sirve con --mode objeto --surface revolucion (vasija a partir de un fragmento)')
    npts = len(xyz)
    st = relief.face_stats(xyz, prm)
    est = relief.memory_estimate(st, prm, len(xyz))
    log(f'Memoria estimada: ≈ {est["gb"]:.1f} GB (disponibles {est["avail"]:.1f} GB)')
    if est['level'] != 'ok':
        sug = relief.memory_suggestions(st, prm, len(xyz))
        log('AVISO: ' + ('no cabe en la memoria disponible' if est['level'] == 'excede' else 'la memoria va justa')
              + '. Para bajar el consumo: ' + '; '.join(f'--{n} {v:g}' if isinstance(v, float) else f'--{n} {v}'
                                                                for n, v, _ in sug if v is not None))
    r = relief.reconstruct(xyz, prm, rgb, log=log, normals=nrm if prm.mode == 'objeto' else None,
                           mesh=info.get('kind') == 'malla')
    src = dict(info, path=os.path.abspath(a.entrada), points=npts)
    kind = 'stla' if a.ascii else None
    eid = None
    if a.registro:
        import registro
        eid = registro.export_registered(a.salida, r, prm, src, kind, title=a.nombre, log=log)
    else:
        relief.export(a.salida, r, kind)
    if a.ficha:
        import ficha
        ficha.make_pdf(a.ficha, r, prm, dict(src, id=eid) if eid else src, '\n'.join(lines))
        print(f'Ficha: {a.ficha}')
        if eid:
            registro.update(eid, ficha=os.path.abspath(a.ficha))
    sx, sy, sz = r.size
    mat = (relief.vol_text(r) if relief.is_real(prm) else
           f'{r.grams_pla:.0f} g PLA {"(hueca)" if r.hollow else "macizo"}')
    print(f'Guardado {a.salida}: {sx:.1f} x {sy:.1f} x {sz:.1f} mm, {mat}, '
          f'{relief.mesh_state(r)[1] if relief.mesh_state(r)[0] else "NO cerrada"}, {time.time() - t:.1f} s')
    return 0 if relief.mesh_state(r)[0] else 1


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if argv[0] in ('--version', '-V'):
        print(f'nube2stl {__version__} · {AUTHOR}, {YEAR}')
        return 0
    if argv[0] == '--actualizar':
        import actualizar
        return actualizar.main(argv[1:])
    relief.preload_pymeshlab_qt()           # en el ejecutable, antes de cargar PyMeshLab
    if argv[0] == '--proceso':
        import proceso
        proceso.main(*argv[1:4])
        return 0
    if argv[0] == '--decimar':
        import decimar
        decimar.main(*argv[1:4])
        return 0
    if argv[0] == '--cli':
        return cli(argv[1:])
    return cli(argv)                        # «nube2stl-consola entrada.ply salida.stl …» también vale


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
