"""Proceso independiente de reconstrucción (sin PyQt5).

PyMeshLab incluye su propia copia de Qt, incompatible con la de PyQt5: no pueden cargarse en el mismo proceso.
La interfaz lanza este script, que lee la nube, reconstruye y deja el resultado en un .npz.

Uso: python proceso.py nube.ply ajustes.json salida.npz
Escribe el registro por la salida estándar, una línea por mensaje.
"""
import os
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import relief  # noqa: E402


def save_result(path, r):
    extra = {'vcolors': r.vcolors} if r.vcolors is not None else {}
    if r.dev is not None:
        extra['dev'] = r.dev
    if r.xform is not None:
        extra['xform'] = np.asarray(r.xform)
    if r.zclip is not None:
        extra['zclip'] = np.asarray(r.zclip, float)
    if getattr(r, 'view_grid', None) is not None:
        extra['view_grid'] = np.asarray(r.view_grid[0], np.float32)
        extra['view_cell'] = np.array(float(r.view_grid[1]))
    if getattr(r, 'detail_zones', None):          # zonas de detalle, para pintarlas en la malla
        import json
        extra['detail_zones'] = np.array(json.dumps(r.detail_zones))
        extra['detail_xform'] = np.asarray(r.detail_xform, float)
        extra['detail_res'] = np.array(float(r.detail_res))
    np.savez(path, verts=r.verts, tris=r.tris, grid=r.grid,
             scalars=np.array([r.cell, r.scale, r.angle, r.bad_edges, r.volume, r.err_mean, r.err_max,
                              r.err_p99, r.res]),
             mode=np.array(r.mode), hollow=np.array(r.hollow), corner=np.array(r.corner),
             open_edges=np.array(int(r.open_edges)), **extra)


def load_result(path):
    d = np.load(path)
    cell, scale, angle, bad, vol, em, ex, e99, res = d['scalars']
    r = relief.Result(d['verts'], d['tris'], d['vcolors'] if 'vcolors' in d else None, d['grid'], float(cell),
                         float(scale), float(angle), int(bad), float(vol), float(em), float(ex), str(d['mode']),
                         float(e99), float(res), xform=d['xform'] if 'xform' in d else None,
                         dev=d['dev'] if 'dev' in d else None, hollow=bool(d['hollow']) if 'hollow' in d else False,
                         zclip=tuple(d['zclip']) if 'zclip' in d else None,
                         corner=bool(d['corner']) if 'corner' in d else False,
                         open_edges=int(d['open_edges']) if 'open_edges' in d else 0,
                         view_grid=(d['view_grid'], float(d['view_cell'])) if 'view_grid' in d else None)
    if 'detail_zones' in d:
        import json
        r.detail_zones = json.loads(str(d['detail_zones']))
        r.detail_xform, r.detail_res = d['detail_xform'], float(d['detail_res'])
    return r


def main(ply, params_json, out):
    relief.lower_priority_for_oom()
    # la interfaz lee esta salida en UTF-8 (en Windows la consola usaría cp1252 y fallaría con «≈», «→», «°»)
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    log = lambda s: print(s, flush=True)  # noqa: E731
    prm = relief.Params.from_json(open(params_json, encoding='utf-8').read())
    import entrada
    xyz, rgb, nrm, info = entrada.load_points(ply, prm, log)
    r = relief.reconstruct(xyz, prm, rgb, log, normals=nrm if prm.mode == 'objeto' else None,
                           mesh=info.get('kind') == 'malla')
    del xyz, rgb, nrm
    save_result(out, r)


if __name__ == '__main__':
    main(*sys.argv[1:4])
