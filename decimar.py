"""Simplifica una franja de malla (lo usa relief.simplify para trabajar en paralelo, un proceso por franja).

Uso: python decimar.py entrada.npz salida.npz triangulos_objetivo
Los vértices del borde de la franja (los cortes) no se mueven ni se eliminan, para poder coser las franjas después.
"""
import os
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import pymeshlab  # noqa: E402


def main(fin, fout, target):
    from relief import _decimate
    d = np.load(fin)
    kw = {'v_color_matrix': d['c']} if 'c' in d else {}
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=d['v'], face_matrix=d['f'], **kw))
    _decimate(ms, int(target))
    m = ms.current_mesh()
    out = {'v': m.vertex_matrix(), 'f': m.face_matrix()}
    if 'c' in d:
        out['c'] = m.vertex_color_matrix()
    np.savez(fout, **out)


if __name__ == '__main__':
    main(*sys.argv[1:4])
