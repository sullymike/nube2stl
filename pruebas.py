#!/usr/bin/env python3
"""Pruebas automáticas de nube2stl (sin dependencias: unittest de la biblioteca estándar).

Uso:  .venv/bin/python pruebas.py            (todas, menos de un minuto)
      .venv/bin/python pruebas.py rapidas    (sin las reconstrucciones 3D, unos segundos)

Generan sus propios datos sintéticos (un bloque con relieve y laterales, una vasija completa) en una carpeta
temporal, así que no dependen de ninguna nube real. Comprueban lo que no se ve a simple vista y se podría estropear
al tocar el código: que las mallas salgan cerradas y con la medida pedida, que el modo objeto siga pegado a la
superficie, la lectura de cada formato, las unidades, la limpieza, la pieza hueca, el rótulo, la ficha PDF, el mapa
de desviación, el corte, el 3MF y el registro de piezas (en una carpeta temporal: no toca el registro real).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ejemplos  # noqa: E402
import entrada  # noqa: E402
import relief  # noqa: E402

TMP = None


def setUpModule():
    global TMP
    TMP = tempfile.mkdtemp(prefix='nube2stl_pruebas_')


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def relief_block(n=120_000, seed=0):
    """Bloque de 200 × 140 con relieve y dos laterales (ejemplos.bloque). Devuelve (xyz, rgb)."""
    return ejemplos.bloque(n, seed)


def column(**kw):
    """Media columna con letras (ejemplos.columna). Devuelve (xyz, eje)."""
    xyz, _, axis = ejemplos.columna(**kw)
    return xyz, axis


def rock(**kw):
    """Roca con grabados (ejemplos.roca)."""
    return ejemplos.roca(**kw)[0]


write_ply = ejemplos.write_ply


def box_result(w=40, d=30, h=20):
    V, F, _ = relief._mesh(np.zeros((d, w)), np.ones((d, w), bool), 1.0, float(h))
    return relief.Result(V, F, None, np.full((d, w), float(h)), 1.0, 1.0, 0, 0, relief._volume(V, F), mode='objeto')


def quiet(*a, **k):
    pass


def apart(test):
    """pymeshlab no se puede cargar después de PyQt5 (choque de librerías de Qt; la ventana genera en otro proceso).
    Si otra prueba ya ha cargado la ventana, esta se repite sola en un proceso nuevo. Devuelve True si se ha hecho
    así (y entonces la prueba ya está comprobada)."""
    if 'PyQt5.QtWidgets' not in sys.modules:
        return False
    name = f'{type(test).__name__}.{test._testMethodName}'
    r = subprocess.run([sys.executable, os.path.abspath(__file__), name], capture_output=True, text=True)
    test.assertEqual(r.returncode, 0, r.stderr[-3000:])
    return True


# ------------------------------------------------------------------ rápidas
class Formatos(unittest.TestCase):
    """Cada formato de entrada se lee con los mismos puntos y colores."""

    @classmethod
    def setUpClass(cls):
        cls.P, cls.C = relief_block(5000)

    def check(self, path, color=True, tol=1e-3):
        xyz, rgb, nrm, kind = entrada.read_any(path)
        self.assertEqual(kind, 'nube')
        self.assertEqual(len(xyz), len(self.P))
        off = np.median(self.P, 0) - np.median(xyz, 0)            # LAS/E57 pueden restar un origen
        self.assertLess(np.abs(np.sort(xyz[:, 0] + off[0]) - np.sort(self.P[:, 0])).max(), tol)
        if color:
            self.assertIsNotNone(rgb)
            self.assertEqual(int(rgb.astype(int).sum()), int(self.C.astype(int).sum()))

    def test_ply(self):
        p = os.path.join(TMP, 'f.ply')
        write_ply(p, self.P, self.C)
        self.check(p)

    def test_texto(self):
        p = os.path.join(TMP, 'f.xyz')
        np.savetxt(p, np.c_[self.P, self.C], fmt='%.4f %.4f %.4f %d %d %d')
        self.check(p, tol=1e-3)
        p = os.path.join(TMP, 'f.csv')
        np.savetxt(p, np.c_[self.P, self.C], fmt='%.4f,%.4f,%.4f,%d,%d,%d', header='X,Y,Z,R,G,B', comments='')
        self.check(p)
        p = os.path.join(TMP, 'f.pts')                               # PTS: nº de puntos, x y z intensidad r g b
        with open(p, 'w') as f:
            f.write(f'{len(self.P)}\n')
            np.savetxt(f, np.c_[self.P, np.zeros(len(self.P)), self.C], fmt='%.4f %.4f %.4f %d %d %d %d')
        self.check(p)

    def test_las(self):
        try:
            import laspy
        except ImportError:
            self.skipTest('laspy no instalado')
        for ext in ('.las', '.laz'):
            p = os.path.join(TMP, 'f' + ext)
            hdr = laspy.LasHeader(point_format=2, version='1.2')
            hdr.scales = [0.0001] * 3
            hdr.offsets = self.P.min(0)
            las = laspy.LasData(hdr)
            las.x, las.y, las.z = self.P[:, 0], self.P[:, 1], self.P[:, 2]
            las.red, las.green, las.blue = (self.C.astype(np.uint16) * 256).T
            try:
                las.write(p)
            except Exception as e:                                 # LAZ sin lazrs
                self.skipTest(f'{ext}: {e}')
            self.check(p, tol=2e-3)

    def test_e57(self):
        try:
            import pye57
        except ImportError:
            self.skipTest('pye57 no instalado')
        p = os.path.join(TMP, 'f.e57')
        e = pye57.E57(p, mode='w')
        e.write_scan_raw({'cartesianX': self.P[:, 0].astype(float), 'cartesianY': self.P[:, 1].astype(float),
                          'cartesianZ': self.P[:, 2].astype(float), 'colorRed': self.C[:, 0],
                          'colorGreen': self.C[:, 1], 'colorBlue': self.C[:, 2]})
        e.close()
        self.check(p, tol=2e-3)

    def test_mallas(self):
        r = box_result()
        for ext, fn in (('.stl', relief.write_stl), ('.obj', relief.write_obj), ('.ply', relief.write_ply)):
            p = os.path.join(TMP, 'm' + ext)
            fn(p, r)
            xyz, rgb, nrm, kind = entrada.read_any(p)
            self.assertEqual(kind, 'malla', ext)
            self.assertIsNotNone(nrm)
            lo, hi = xyz.min(0), xyz.max(0)
            np.testing.assert_allclose(hi - lo, [40, 30, 20], atol=0.3, err_msg=ext)
            # las normales de la malla apuntan hacia fuera: en la cara de arriba, +z
            top = xyz[:, 2] > 19.99
            self.assertGreater(nrm[top, 2].mean(), 0.99, ext)


    def test_obj_formatos(self):
        """El lector de OBJ va por bloques con NumPy (un OBJ de 2,5 GB con Open3D pasaba de 8 GB). Aquí las
        variantes que tiene que entender: cuadriláteros, v//n, v/t/n, índices negativos, color por vértice y la
        textura del .mtl. Un cubo de 10 mm: los puntos tienen que quedar en sus caras y con su color."""
        from PIL import Image
        C = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0, 0, 10], [10, 0, 10], [10, 10, 10],
                      [0, 10, 10]], float)
        Q = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [0, 4, 7, 3]]
        col = ''.join(f'v {x:g} {y:g} {z:g} 1 0 0\n' for x, y, z in C)          # rojo, en 0..1
        variants = {
            'quads': ''.join(f'v {x:g} {y:g} {z:g}\n' for x, y, z in C)
                     + ''.join('f ' + ' '.join(str(i + 1) for i in q) + '\n' for q in Q),
            'vn': ''.join(f'v {x:g} {y:g} {z:g}\n' for x, y, z in C) + 'vn 0 0 1\n'
                  + ''.join(f'f {q[0] + 1}//1 {q[k] + 1}//1 {q[k + 1] + 1}//1\n' for q in Q for k in (1, 2)),
            'negativos': ''.join(f'v {x:g} {y:g} {z:g}\n' for x, y, z in C)
                         + ''.join(f'f {q[0] - 8} {q[k] - 8} {q[k + 1] - 8}\n' for q in Q for k in (1, 2)),
            'color': col + ''.join(f'f {q[0] + 1} {q[k] + 1} {q[k + 1] + 1}\n' for q in Q for k in (1, 2)),
        }
        # textura: imagen verde, y todas las vt en su centro
        Image.fromarray(np.full((8, 8, 3), (0, 200, 0), np.uint8)).save(os.path.join(TMP, 'tex.png'))
        _write(os.path.join(TMP, 'tex.mtl'), 'newmtl m\nmap_Kd tex.png\n')
        variants['textura'] = ('mtllib tex.mtl\n' + ''.join(f'v {x:g} {y:g} {z:g}\n' for x, y, z in C)
                               + 'vt 0.5 0.5\nvn 0 0 1\nusemtl m\n'
                               + ''.join(f'f {q[0] + 1}/1/1 {q[k] + 1}/1/1 {q[k + 1] + 1}/1/1\n'
                                         for q in Q for k in (1, 2)))
        for name, txt in variants.items():
            path = os.path.join(TMP, 'tex.obj' if name == 'textura' else f'o_{name}.obj')   # tex.obj, junto a tex.mtl
            _write(path, txt)
            P, rgb, nrm, kind = entrada.read_any(path)
            self.assertEqual(kind, 'malla', name)
            self.assertGreaterEqual(len(P), 8, name)                   # pocos puntos: el cubo tiene 8 vértices
            self.assertTrue(((P > -1e-3) & (P < 10 + 1e-3)).all(), name)
            on_face = np.isclose(P, 0, atol=1e-3).any(1) | np.isclose(P, 10, atol=1e-3).any(1)
            self.assertTrue(on_face.all(), name)                       # en la superficie del cubo
            if name == 'color':
                self.assertTrue((rgb == [255, 0, 0]).all())
            elif name == 'textura':
                self.assertTrue((rgb == [0, 200, 0]).all())
            else:
                self.assertIsNone(rgb, name)

    def test_stl_por_bloques(self):
        """El STL se reparte sin unir los vértices repetidos (np.unique de 58 M vértices costaba GB). Lo que sale
        tiene que ser lo mismo que con la malla unida, también con coordenadas UTM (se resta el origen)."""
        r = box_result()
        r.verts = r.verts + np.array([440000.0, 4500000.0, 600.0])
        p = os.path.join(TMP, 'utm.stl')
        relief.write_stl(p, r)
        P, _, nrm, kind = entrada.read_any(p)
        self.assertEqual(kind, 'malla')
        self.assertIsNotNone(entrada.read_any.origin)
        np.testing.assert_allclose(P.max(0) - P.min(0), [40, 30, 20], atol=0.3)
        old = entrada.CHUNK_TRIS
        try:                                            # con bloques diminutos sale exactamente lo mismo
            entrada.CHUNK_TRIS = 3
            P2, _, nrm2, _ = entrada.read_any(p)
        finally:
            entrada.CHUNK_TRIS = old
        np.testing.assert_allclose(P2, P, atol=1e-4)
        np.testing.assert_allclose(nrm2, nrm, atol=1e-5)


class Unidades(unittest.TestCase):
    def test_auto_metros(self):
        P, _ = relief_block(3000)
        m = (P - P.min(0)) / 1000                                   # 0,2 m de lado
        xyz, u, f = entrada.to_mm(m.copy(), 'auto')
        self.assertEqual(u, 'm')
        self.assertAlmostEqual(float(np.ptp(xyz[:, 0])), float(np.ptp(m[:, 0])) * 1000, places=2)

    def test_auto_mm_y_elegidas(self):
        P, _ = relief_block(3000)
        self.assertEqual(entrada.to_mm(P.copy(), 'auto')[1], 'mm')
        xyz, u, f = entrada.to_mm(P.copy(), 'cm')
        self.assertEqual(f, 10)
        self.assertAlmostEqual(float(np.ptp(xyz[:, 2])), float(np.ptp(P[:, 2])) * 10, places=1)


class Limpieza(unittest.TestCase):
    def test_quita_grupos_separados(self):
        P, _ = relief_block(60_000)
        rng = np.random.default_rng(3)
        lejos = P[:500] + [600, 0, 0]                               # trozo de suelo apartado
        sueltos = rng.uniform(P.min(0) - 300, P.max(0) + 300, (200, 3))
        X = np.concatenate([P, lejos, sueltos]).astype(np.float32)
        keep = entrada.clean_mask(X)
        self.assertGreaterEqual(keep[:len(P)].mean(), 0.999)        # la pieza se queda
        self.assertFalse(keep[len(P):len(P) + 500].any())           # el trozo se va
        self.assertLess(keep[len(P) + 500:].mean(), 0.05)           # los sueltos casi todos


class Escala(unittest.TestCase):
    def test_piece_scale(self):
        p = relief.Params(target_len=150)
        self.assertAlmostEqual(relief.piece_scale(p, 300), 0.5)
        p.ratio = 5
        self.assertAlmostEqual(relief.piece_scale(p, 300), 0.2)
        p = relief.Params(target_len=0)
        self.assertEqual(relief.piece_scale(p, 300), 1.0)

    def test_alturas_a_escala(self):
        P, C = relief_block(80_000)
        for prm, side in ((relief.Params(target_len=120), 120.0), (relief.Params(ratio=4), 200 / 4)):
            prm.simplify = 100
            r = relief.reconstruct(P, prm, C, quiet)
            self.assertEqual(r.bad_edges, 0)
            self.assertAlmostEqual(float(r.size[:2].max()), side, delta=side * 0.03)

    def test_vista_previa(self):
        P, _ = relief_block(20_000)
        st = relief.face_stats(P)
        prm = relief.Params(wall=2, label='X')
        pv = relief.preview_params(prm, st)
        self.assertAlmostEqual(pv.res, relief.print_plan(st, prm)['res'] * relief.PREVIEW_FACTOR)
        self.assertEqual((pv.wall, pv.label, pv.simplify), (0.0, '', 100.0))
        self.assertEqual(prm.wall, 2)                                # el original no se toca


class Acabado(unittest.TestCase):
    def test_hueca_con_drenaje_y_rotulo(self):
        import acabado
        r = box_result(60, 50, 30)
        out = acabado.apply(r, relief.Params(wall=3, drain=4, label='AB 12'), quiet)
        self.assertEqual(out.bad_edges, 0)
        inner = (60 - 6) * (50 - 6) * (30 - 6)
        self.assertAlmostEqual(out.volume, r.volume - inner, delta=0.08 * inner)
        np.testing.assert_allclose(out.size, r.size, atol=1e-6)

    def test_rotulo_solo(self):
        import acabado
        r = box_result(60, 40, 10)
        out = acabado.apply(r, relief.Params(label='MUSEO|1:5'), quiet)
        self.assertEqual(out.bad_edges, 0)
        self.assertLess(out.volume, r.volume)
        self.assertGreater(out.volume, r.volume * 0.97)

    def test_pieza_fina_queda_maciza(self):
        import acabado
        r = box_result(40, 30, 4)
        msgs = []
        out = acabado.apply(r, relief.Params(wall=3), msgs.append)
        self.assertAlmostEqual(out.volume, r.volume)
        self.assertTrue(any('maciza' in m for m in msgs))


class Ficha(unittest.TestCase):
    def test_pdf(self):
        import ficha
        p = os.path.join(TMP, 'ficha.pdf')
        ficha.make_pdf(p, box_result(), relief.Params(label='X'), dict(path='caja.ply', points=10), 'línea\n' * 200)
        with open(p, 'rb') as f:
            self.assertEqual(f.read(4), b'%PDF')
        self.assertGreater(os.path.getsize(p), 10_000)


class Desviacion(unittest.TestCase):
    """El mapa de desviación usa la transformación nube -> pieza de cada modo: si estuviera mal, saldría enorme."""

    @classmethod
    def setUpClass(cls):
        cls.P, cls.C = relief_block(100_000)

    def check(self, prm, limit=0.15):
        import desviacion
        prm.simplify = 100
        r = relief.reconstruct(self.P, prm, self.C, quiet)
        st = desviacion.stats(r)
        self.assertIsNotNone(st)
        self.assertLess(st['p50'], limit, prm)
        self.assertGreater(st['covered'], 35, prm)
        return r

    def test_alturas(self):
        self.check(relief.Params(target_len=200))

    def test_alturas_transformada(self):
        # molde, espejo, marco, exageración y giros a la vez
        self.check(relief.Params(target_len=150, negative=True, mirror=True, frame=4, z_scale=2, angle=10,
                                 auto_straighten=True), limit=0.13)

    def test_hueca_no_cuenta_el_interior(self):
        import desviacion
        r = self.check(relief.Params(target_len=200, base=12))
        r2 = self.check(relief.Params(target_len=200, base=12, wall=2))
        self.assertTrue(np.isinf(r2.dev).any())                   # el hueco: superficie añadida
        self.assertAlmostEqual(desviacion.stats(r2)['covered'], desviacion.stats(r)['covered'], delta=3)


class Corte(unittest.TestCase):
    def test_recorte_exacto(self):
        import corte
        import open3d as o3d
        m = o3d.geometry.TriangleMesh.create_sphere(10, 40)
        P, F = np.asarray(m.vertices), np.asarray(m.triangles)
        P2, F2, C2 = corte.clip_mesh(P, F, 0, 2.5, np.ones((len(P), 4)))
        used = np.unique(F2)
        self.assertLessEqual(P2[used, 0].max(), 2.5 + 1e-9)
        a = 0.5 * np.linalg.norm(np.cross(P2[F2[:, 1]] - P2[F2[:, 0]], P2[F2[:, 2]] - P2[F2[:, 0]]), axis=1)
        self.assertAlmostEqual(a.sum(), 2 * np.pi * 10 * 12.5, delta=0.01 * 785)   # casquete esférico
        L = corte.section_lines(P, F, 0, 2.5)
        np.testing.assert_allclose(np.linalg.norm(L[:, 1:], axis=1), np.sqrt(100 - 2.5 ** 2), atol=0.05)
        self.assertEqual(len(C2), len(P2))


class Formato3MF(unittest.TestCase):
    def test_ida_y_vuelta(self):
        import zipfile
        import xml.etree.ElementTree as ET
        r = box_result()
        p = os.path.join(TMP, 'caja.3mf')
        relief.export(p, r, None, {'id': 'P-0007', 'title': 'Caja <&> «prueba»', 'scale': '1:5'})
        z = zipfile.ZipFile(p)
        self.assertEqual(sorted(z.namelist()), ['3D/3dmodel.model', '[Content_Types].xml', '_rels/.rels'])
        root = ET.fromstring(z.read('3D/3dmodel.model'))
        ns = {'m': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
        self.assertEqual(root.get('unit'), 'millimeter')
        V = np.array([[float(v.get(k)) for k in 'xyz'] for v in root.iterfind('.//m:vertex', ns)])
        T = np.array([[int(t.get(k)) for k in ('v1', 'v2', 'v3')] for t in root.iterfind('.//m:triangle', ns)])
        np.testing.assert_allclose(V, r.verts, atol=1e-3)
        np.testing.assert_array_equal(T, r.tris)
        md = {m.get('name'): m.text for m in root.iterfind('m:metadata', ns)}
        self.assertEqual(md['Title'], 'Caja <&> «prueba»')
        self.assertIn('P-0007', md['Description'])

    def test_cabecera_stl(self):
        p = os.path.join(TMP, 'caja.stl')
        relief.export(p, box_result(), None, {'id': 'P-0007', 'scale': '1:5'})
        with open(p, 'rb') as f:
            head = f.read(80)
        self.assertIn(b'P-0007', head)
        self.assertFalse(head.startswith(b'solid'))


class Registro(unittest.TestCase):
    def setUp(self):
        import registro
        self.reg = registro
        self.dir = tempfile.mkdtemp(prefix='reg_', dir=TMP)
        self._cfg = registro.config_file
        cfg = os.path.join(self.dir, 'config.json')
        registro.config_file = lambda: cfg                        # no tocar la configuración del usuario
        registro.save_config(registro_dir=os.path.join(self.dir, 'piezas'))

    def tearDown(self):
        self.reg.config_file = self._cfg

    def test_registrar_buscar_y_quitar(self):
        reg = self.reg
        r = box_result()
        cloud = os.path.join(TMP, 'nube_registro.ply')
        write_ply(cloud, *relief_block(2000))
        src = dict(path=cloud, points=2000, units='mm')
        prm = relief.Params(label='MAN 7')
        a = reg.export_registered(os.path.join(self.dir, 'a.stl'), r, prm, src)
        b = reg.export_registered(os.path.join(self.dir, 'a.3mf'), r, prm, src, '3mf', eid=a)
        c = reg.export_registered(os.path.join(self.dir, 'c.stl'), r, prm, src, title='Estela romana')
        self.assertEqual((a, b, c), ('P-0001', 'P-0001', 'P-0002'))
        e = reg.get(a)
        self.assertEqual([o['formato'] for o in e['salidas']], ['stl', '3mf'])
        self.assertEqual(e['ajustes']['label'], 'MAN 7')
        self.assertTrue(reg.same_cloud(e, cloud))
        self.assertEqual([x['id'] for x in reg.search('estela')], ['P-0002'])
        reg.update(a, notas='impresa en PLA gris')
        self.assertEqual([x['id'] for x in reg.search('gris')], ['P-0001'])
        d = reg.registry_dir()
        self.assertTrue(os.path.exists(os.path.join(d, 'P-0001.png')))
        with open(os.path.join(d, 'registro.csv'), encoding='utf-8-sig') as f:
            rows = f.read().splitlines()
        self.assertEqual(len(rows), 3)
        self.assertIn('impresa en PLA gris', rows[1])
        reg.delete(c)
        self.assertEqual([x['id'] for x in reg.entries()], ['P-0001'])
        self.assertEqual(reg.new_id(), 'P-0002')                   # el hueco se reutiliza solo si es el último


# ------------------------------------------------------------------ reconstrucciones 3D (lentas)
class Modos3D(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.P, cls.C = relief_block(150_000)

    def run_mode(self, mode, **kw):
        prm = relief.Params(mode=mode, target_len=200, **kw)
        r = relief.reconstruct(self.P, prm, self.C, quiet)
        self.assertEqual(r.bad_edges, 0, mode)
        self.assertAlmostEqual(float(r.size[:2].max()), 200, delta=4, msg=mode)
        return r

    def test_3d(self):
        import desviacion
        r = self.run_mode('3d')
        self.assertLess(desviacion.stats(r)['p50'], 0.15)

    def test_bloque(self):
        import desviacion
        r = self.run_mode('bloque')
        self.assertLess(desviacion.stats(r)['p50'], 0.15)
        self.assertGreater(r.size[2], 25)                           # los laterales escaneados (30 mm) cuentan

    def test_bloque_celda_gruesa(self):
        """Con celdas mucho mayores que el escaneo (2,5 y 4 mm; el escaneo da 0,9) los laterales caen fuera de la
        rejilla de la cara. Antes se perdían y Poisson inventaba la pieza por detrás: 200 × 200 y aristas abiertas."""
        if apart(self):
            return
        for res in (2.5, 4.0):
            r = relief.reconstruct(self.P, relief.Params(mode='bloque', target_len=200, res=res), self.C, quiet)
            self.assertEqual(r.bad_edges, 0, res)
            self.assertAlmostEqual(float(r.size[1]), 142, delta=5, msg=res)     # el bloque mide 200 × 140
            self.assertGreater(r.size[2], 25, res)


class Objeto(unittest.TestCase):
    """La vasija sintética: cerrada, de la altura pedida y pegada a la superficie escaneada."""

    def test_vasija(self):
        from scipy.spatial import cKDTree
        p = os.path.join(TMP, 'vasija.ply')
        subprocess.run([sys.executable, os.path.join(HERE, 'generar_objeto_sintetico.py'), p, '--puntos', '400000'],
                       check=True, capture_output=True)
        xyz, rgb, nrm, info = entrada.load_points(p, relief.Params())
        prm = relief.Params(mode='objeto', target_len=150, cut=1)
        r = relief.reconstruct(xyz, prm, rgb, quiet, normals=nrm)
        self.assertEqual(r.bad_edges, 0)
        self.assertAlmostEqual(float(r.size.max()), 150, delta=0.5)
        # distancia de la malla a la nube (alineadas por la escala y el mínimo): casi toda la superficie cerca
        q = (xyz - xyz.min(0)) * r.scale
        q[:, 2] -= q[:, 2].max()                                    # alinear por la boca (la base está cortada)
        V = r.verts - r.verts.min(0)
        V[:, 2] -= V[:, 2].max()
        d, _ = cKDTree(q[::3]).query(V[::5], workers=-1)
        self.assertLess(np.median(d), 1.0)
        self.assertGreater(r.volume, 0)
        import desviacion                                           # y el mapa de desviación lo confirma
        st = desviacion.stats(r)
        self.assertLess(st['p50'], 0.1)
        self.assertGreater(st['covered'], 85)


class Recorte(unittest.TestCase):
    """Recortes de la vista de la nube y vistas fijas de la cara."""

    def test_recorte(self):
        rng = np.random.default_rng(3)
        xyz = rng.uniform(-10, 10, (20_000, 3)).astype(np.float32)
        M = np.eye(4)
        M[:3, :3] /= 10                                        # «pantalla»: x/10, y/10
        c = {'M': M.ravel().tolist(), 'poly': [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]], 'inside': True}
        k = entrada.crop_mask(xyz, [c])
        ref = (np.abs(xyz[:, 0]) <= 5) & (np.abs(xyz[:, 1]) <= 5)
        self.assertTrue(np.array_equal(k, ref))
        k2 = entrada.crop_mask(xyz, [c, dict(c, inside=False)])  # dejar dentro y después quitarlo: nada
        self.assertFalse(k2.any())
        # load_points aplica los recortes de los ajustes
        p = os.path.join(TMP, 'recorte.ply')
        write_ply(p, xyz)
        q, *_ = entrada.load_points(p, relief.Params(units='mm', clean=False, crop=[c]))
        self.assertEqual(len(q), int(ref.sum()))

    def test_una_parte(self):
        """«Una parte (clic)»: tres esferas separadas; se deja (o se quita) la del punto del clic. Si dos se tocan,
        no se separan, pero sí después de recortar la unión con un rectángulo."""
        rng = np.random.default_rng(4)

        def sphere(c, r, n):
            v = rng.normal(size=(n, 3))
            return c + r * v / np.linalg.norm(v, axis=1, keepdims=True)
        a, b, c = sphere([0, 0, 0], 50, 40_000), sphere([200, 0, 0], 50, 40_000), sphere([0, 200, 0], 30, 20_000)
        xyz = np.r_[a, b, c].astype(np.float32)
        k = entrada.crop_mask(xyz, [{'part': [200, 0, 50], 'inside': True}])      # clic en lo alto de b
        self.assertTrue(k[40_000:80_000].all())
        self.assertFalse(k[:40_000].any() or k[80_000:].any())
        k = entrada.crop_mask(xyz, [{'part': [0, 200, 30], 'inside': False}])     # quitar c
        self.assertEqual(int(k.sum()), 80_000)
        self.assertFalse(k[80_000:].any())
        # dos esferas que se tocan: una sola parte
        d = sphere([95, 0, 0], 50, 40_000)
        xyz2 = np.r_[a, d].astype(np.float32)
        self.assertEqual(int(entrada.crop_mask(xyz2, [{'part': [0, 0, 50]}]).sum()), 80_000)
        # quitando la unión con un rectángulo (vista desde arriba, la franja 40 < x < 55), sí
        M = np.eye(4)
        M[:3, :3] /= 200
        band = {'M': M.ravel().tolist(), 'poly': [[0.2, -1], [0.275, -1], [0.275, 1], [0.2, 1]], 'inside': False}
        k = entrada.crop_mask(xyz2, [band, {'part': [0, 0, 50]}])
        self.assertTrue(k[:40_000][a[:, 0] < 40].all())
        self.assertFalse(k[40_000:].any())

    def test_caja(self):
        """Caja con medidas (mm de la nube): dejar o quitar lo de dentro, y encadenada con otros recortes."""
        xyz = np.random.default_rng(5).uniform(-10, 10, (20_000, 3)).astype(np.float32)
        box = {'box': [-5, 5, -2, 8, 0, 10], 'inside': True}
        k = entrada.crop_mask(xyz, [box])
        ref = (np.abs(xyz[:, 0]) <= 5) & (xyz[:, 1] >= -2) & (xyz[:, 1] <= 8) & (xyz[:, 2] >= 0)
        self.assertTrue(np.array_equal(k, ref))
        self.assertTrue(np.array_equal(entrada.crop_mask(xyz, [dict(box, inside=False)]), ~ref))
        self.assertFalse(entrada.crop_mask(xyz, [box, dict(box, inside=False)]).any())

    def test_mirar_desde_auto_con_marco(self):
        """«Mirar desde: auto» con un marco alto: el marco es una cola de alturas como los laterales de un bloque, pero
        es una meseta que mira de frente y rodea la pieza, así que está delante. Lápida de 200 × 140 con un listel de
        6 mm de 8, 14 o 20 mm de ancho (18–43 % de la superficie) y letras incisas, inclinada 30°; y la lápida de
        ejemplo (moldura de 3 mm), que antes se veía desde detrás. El bloque y la lápida gastada (cantos redondeados,
        una rampa) siguen como antes."""
        rng = np.random.default_rng(4)
        a = np.radians(30)
        R = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
        front = R @ [0, 0, 1]
        for w in (8, 14, 20):
            x, y = rng.uniform(0, 200, 200_000), rng.uniform(0, 140, 200_000)
            inner = (x > w) & (x < 200 - w) & (y > w) & (y < 140 - w)
            z = np.where(inner, 0.0, 6.0)
            z[inner & (np.sin(x / 3) > 0.6) & (((y > 40) & (y < 55)) | ((y > 80) & (y < 95)))] -= 2.0
            P = (np.c_[x, y, z] + rng.normal(0, 0.05, (200_000, 3))) @ R.T + [500, 200, -40]
            self.assertGreater(float(relief._frame_axes(P.astype(np.float32), relief.Params())[3] @ front), 0.9, w)
        U, V, H, _ = relief._project(ejemplos.lapida()[0], relief.Params())
        c = np.hypot(U - np.median(U), V - np.median(V))
        self.assertGreater(np.median(H[c > np.percentile(c, 97)]), np.median(H[c < np.percentile(c, 30)]) + 2)
        self.assertGreater(float(relief._frame_axes(ejemplos.bloque()[0], relief.Params())[3][2]), 0.9)
        self.assertGreater(float(relief._frame_axes(ejemplos.gastada()[0], relief.Params())[3][2]), 0.9)

    def test_vistas(self):
        p = np.random.default_rng(1).normal(size=(5000, 3)).astype(np.float32)
        for view, n in relief.VIEWS.items():
            _, u, v, nn = relief._frame_axes(p, relief.Params(view=view))
            self.assertTrue(np.allclose(nn, n[0]))
            self.assertGreater(np.dot(np.cross(u, v), nn), 0.99)      # ejes a derechas (no espeja)


class AbiertoAbajo(unittest.TestCase):
    """Terreno escaneado solo por arriba con un muro con un arco pasante: «Abierto por abajo» da una maqueta
    cerrada, sin superficie inventada, y el arco sigue pasante."""

    def test_terreno_con_arco(self):
        rng = np.random.default_rng(5)
        n = 250_000
        x, y = rng.uniform(0, 400, n), rng.uniform(0, 200, n)
        z = 20 * np.sin(x / 90) + 10 * np.cos(y / 60)
        ground = np.c_[x, y, z]
        # muro de 300 × 20 × 80 con un arco de 60 de ancho y 50 de alto en el centro (caras visibles y el intradós)
        m = 120_000
        wx, wz = rng.uniform(50, 350, m), rng.uniform(0, 80, m)
        hole = (np.abs(wx - 200) < 30) & (wz < 50)
        side = rng.integers(0, 2, m)
        wall = np.c_[wx, 90 + 20 * side, wz + 30][~hole]
        top = np.c_[rng.uniform(50, 350, 20_000), rng.uniform(90, 110, 20_000), np.full(20_000, 110.0)]
        a = rng.uniform(0, 1, 8000)
        intr = np.c_[170 + 60 * a, rng.uniform(90, 110, 8000), np.full(8000, 80.0)]
        terr = ground[~((ground[:, 1] > 90) & (ground[:, 1] < 110) & (ground[:, 0] > 50) & (ground[:, 0] < 350)
                        & ~((np.abs(ground[:, 0] - 200) < 30)))]
        terr = terr.copy()
        terr[(terr[:, 1] > 88) & (terr[:, 1] < 112) & (terr[:, 0] > 165) & (terr[:, 0] < 235), 2] = 30.0
        pts = np.vstack([terr, wall, top, intr]).astype(np.float32)
        prm = relief.Params(mode='objeto', ground=True, target_len=120, base=3)
        r = relief.reconstruct(pts, prm, None, quiet)
        self.assertEqual(r.bad_edges, 0)
        self.assertGreater(r.volume, 0)
        sz = r.size
        self.assertAlmostEqual(float(sz.max()), 120, delta=0.6)
        self.assertLess(float(sz[2]), 0.45 * 120)          # sin «burbuja»: la altura es la del muro y el terreno
        import desviacion
        st = desviacion.stats(r)
        self.assertLess(st['p50'], 0.15)
        # el arco sigue pasante: un rayo horizontal (en Y) por el centro del arco no toca la malla
        import open3d as o3d
        sc = o3d.t.geometry.RaycastingScene()
        sc.add_triangles(o3d.core.Tensor(r.verts.astype(np.float32)), o3d.core.Tensor(r.tris.astype(np.uint32)))
        T = np.asarray(r.xform)
        c = np.array([200, 0, 60.0]) @ T[:3, :3].T + T[:3, 3]      # centro del arco, delante del muro
        hit = sc.cast_rays(o3d.core.Tensor(np.array([[c[0], -50, c[2], 0, 1, 0]], np.float32)))['t_hit'].numpy()
        self.assertFalse(np.isfinite(hit[0]))
        c2 = np.array([110, 0, 60.0]) @ T[:3, :3].T + T[:3, 3]     # fuera del arco el muro sí está
        hit2 = sc.cast_rays(o3d.core.Tensor(np.array([[c2[0], -50, c2[2], 0, 1, 0]], np.float32)))['t_hit'].numpy()
        self.assertTrue(np.isfinite(hit2[0]))


class Superficies(unittest.TestCase):
    """Relieve sobre un cilindro (desenrollado), grabados sobre roca (sin la forma general) y propuesta del tipo."""

    def test_cilindro(self):
        import superficie
        P, axis = column()
        c = superficie.fit_cylinder(P)
        self.assertAlmostEqual(c['R'], 200, delta=2)
        self.assertAlmostEqual(c['arc'], 160, delta=4)
        self.assertGreater(abs(float(np.dot(c['axis'], axis))), 0.999)
        p = os.path.join(TMP, 'columna.ply')
        write_ply(p, P)
        U, *_ = entrada.load_points(p, relief.Params(units='mm', clean=False, surface='cilindro'))
        arc = 2 * np.pi * 200 * 160 / 360                          # 558 mm de cara desenrollada
        self.assertAlmostEqual(float(np.ptp(U[:, 0])), arc, delta=0.03 * arc)
        self.assertAlmostEqual(float(np.ptp(U[:, 1])), 600, delta=3)
        self.assertLess(float(np.percentile(U[:, 2], 99.5) - np.percentile(U[:, 2], 0.5)), 12)   # ya no es curva
        r = relief.reconstruct(U, relief.Params(surface='cilindro', target_len=0), None, quiet)
        self.assertEqual(r.bad_edges, 0)
        self.assertLess(r.relief_depth, 12)

    def test_grabado(self):
        R = rock()
        prm = relief.Params(target_len=0, simplify=100, smooth=0, floor_pct=0)     # surcos de solo 2 celdas
        r0 = relief.reconstruct(R, prm, None, quiet)
        prm.detrend = 200.0
        r = relief.reconstruct(R, prm, None, quiet)
        self.assertGreater(r0.relief_depth, 100)
        self.assertLess(r.relief_depth, 4)                          # queda el grabado (2 mm)
        self.assertGreater(r.relief_depth, 1.5)
        self.assertEqual(r.bad_edges, 0)
        st = relief.face_stats(R)                                   # la estimación del cálculo de impresión
        self.assertLess(relief.print_plan(st, prm)['depth'], 6)

    def test_tipo_propuesto(self):
        import superficie
        rng = np.random.default_rng(4)
        sph = rng.normal(size=(60_000, 3))
        sph = sph / np.linalg.norm(sph, axis=1, keepdims=True) * [120, 90, 150]
        flat = np.c_[rng.uniform(0, 400, 80_000), rng.uniform(0, 300, 80_000), np.zeros(80_000)]
        flat[:, 2] = 3 * np.sin(flat[:, 0] / 9) * (np.abs(flat[:, 1] - 150) < 60)
        terrain = np.c_[rng.uniform(0, 30_000, 80_000), rng.uniform(0, 20_000, 80_000), np.zeros(80_000)]
        terrain[:, 2] = 800 * np.sin(terrain[:, 0] / 5000)
        cases = [(column()[0], 'curvo'), (rock(), 'grabado'), (relief_block()[0], 'bloque'), (sph, 'objeto'),
                 (flat, 'relieve'), (terrain, 'terreno')]
        for P, kind in cases:
            self.assertEqual(superficie.guess_kind(P)[0], kind, kind)


class Ejemplos(unittest.TestCase):
    """Los ejemplos de Ayuda › Abrir un ejemplo: se generan, se leen y la ventana propondría el tipo que anuncian."""

    def test_ejemplos(self):
        import superficie
        d = os.path.join(TMP, 'ejemplos')
        for key, fname, title, kind, desc, _ in ejemplos.EXAMPLES:
            p = ejemplos.ensure(key, d)
            xyz, rgb, _, info = entrada.load_points(p, relief.Params())
            self.assertGreater(len(xyz), 50_000, key)
            self.assertIsNotNone(rgb, key)
            self.assertEqual(superficie.guess_kind(xyz)[0], kind, key)


class Union(unittest.TestCase):
    """Unir otra nube a la abierta: ya alineada, por pares de puntos (Kabsch) y automática con solape."""

    def test_separacion(self):
        """Color «separación» de la ventana de unir: distancia con signo de la añadida a la principal en la zona
        común. Placa a z = 0; la añadida 0,5 mm por encima da +0,5; girada, un lado + y el otro −; lejos, NaN."""
        import unir
        rng = np.random.default_rng(2)
        A = np.c_[rng.uniform(0, 100, (40_000, 2)), np.zeros(40_000)]
        B = np.c_[rng.uniform(50, 150, (20_000, 2)), np.full(20_000, 0.5)]
        d = unir.separation(A, B, 3.0)
        fin = np.isfinite(d)
        self.assertGreater(fin.mean(), 0.2)
        self.assertFalse(fin[(B[:, 0] > 105) | (B[:, 1] > 105)].any())       # fuera de la principal
        self.assertAlmostEqual(abs(float(np.median(d[fin]))), 0.5, places=2)
        s0 = np.sign(np.median(d[fin]))
        Bt = B.copy()
        Bt[:, 2] = 0.02 * (B[:, 0] - 75)                                     # girada alrededor de x = 75
        dt = unir.separation(A, Bt, 3.0)
        lo, hi = np.isfinite(dt) & (B[:, 0] < 65), np.isfinite(dt) & (B[:, 0] > 85)
        self.assertLess(s0 * np.median(dt[lo]), 0)
        self.assertGreater(s0 * np.median(dt[hi]), 0)

    def test_igualar_color(self):
        """Igualar el color de la tanda añadida: la principal es la añadida con ganancia 1,2 / 0,9 / 1,0 y
        desplazamiento −10 / +5 / 0, más ruido y un 10 % de parejas que no cuadran (sombras): se recupera."""
        import unir
        rng = np.random.default_rng(6)
        b = rng.uniform(30, 200, (5000, 3))
        a = b * [1.2, 0.9, 1.0] + [-10, 5, 0] + rng.normal(0, 3, (5000, 3))
        a[:500] = rng.uniform(0, 255, (500, 3))
        g, o, n = unir.color_match(a, b)
        self.assertTrue(np.allclose(g, [1.2, 0.9, 1.0], atol=0.03))
        self.assertTrue(np.allclose(o, [-10, 5, 0], atol=4))
        self.assertIsNone(unir.color_match(a[:100], b[:100]))       # pocas parejas: no se toca
        out = unir.apply_color(np.array([[250, 10, 100]]), (g, o, n))
        self.assertEqual(out.dtype, np.uint8)
        self.assertLessEqual(int(out.max()), 255)

    def test_zona_comun_mejor_vista(self):
        """En la zona común se queda la nube con menos ruido: placa en dos tandas solapadas (x de 40 a 60); con la
        principal ruidosa (0,5 mm) y la añadida limpia (0,05 mm), en la zona común quedan los de la añadida y sin
        doble piel; al revés, los de la principal (como antes)."""
        import unir
        rng = np.random.default_rng(8)

        def plate(x0, x1, n, noise):
            P = np.c_[rng.uniform(x0, x1, n), rng.uniform(0, 50, n), np.zeros(n)]
            P[:, 2] += rng.normal(0, noise, n)
            return P
        for na, nb, want in ((0.5, 0.05, 'b'), (0.05, 0.5, 'a')):
            A, B = plate(0, 60, 60_000, na), plate(40, 100, 60_000, nb)
            pb = os.path.join(TMP, 'unir_ruido_b.ply')
            write_ply(pb, B)
            info = {'units': 'mm', 'factor': 1.0, 'n_main': len(A)}
            xyz, _, _ = unir.merge(A, None, None, info, [dict(path=pb, units='igual', M=unir.IDENTITY)])
            mid = (xyz[:, 0] > 43) & (xyz[:, 0] < 57)
            noise = float(np.std(xyz[mid, 2]))
            self.assertLess(noise, 0.15 if want == 'b' else 0.1)               # la de menos ruido
            self.assertLess(mid.sum(), 1.3 * 60_000 * 14 / 60)                 # sin doble piel
            self.assertEqual(info['n_main'], int((xyz[:info['n_main'], 0] < 60.001).sum()))

    def test_reverso_o_unir(self):
        """Al añadir un reverso, proponer unir si las dos nubes se solapan o traen el canto: losa de 80 × 50 × 20 mm
        escaneada por arriba y por abajo, cada vez con 14 mm del canto (se solapan 8). En las mismas coordenadas:
        'igual'; movida en el escáner: 'canto'; dos caras sin canto (tablillas) o una lápida: nada."""
        import unir
        rng = np.random.default_rng(3)

        def slab(n, top):
            nf = int(n * .7)
            x, y = rng.uniform(0, 80, nf), rng.uniform(0, 50, nf)
            face = np.c_[x, y, np.full(nf, 20.0 if top else 0.0)]
            ns = n - nf
            t = rng.uniform(0, 260, ns)
            z = rng.uniform(6, 20, ns) if top else rng.uniform(0, 14, ns)
            xs = np.where(t < 80, t, np.where(t < 130, 80, np.where(t < 210, 210 - t, 0)))
            ys = np.where(t < 80, 0, np.where(t < 130, t - 80, np.where(t < 210, 50, 260 - t)))
            return np.r_[face, np.c_[xs, ys, z]] + rng.normal(0, .03, (n, 3))
        A, B = slab(100_000, True), slab(100_000, False)
        self.assertEqual(unir.reverse_hint(A, B)[0], 'igual')
        self.assertEqual(unir.reverse_hint(A, B @ np.diag([1, -1, -1]) + [0, 300, 500])[0], 'canto')
        tA = np.c_[rng.uniform(0, 80, 60_000), rng.uniform(0, 50, 60_000), rng.normal(0, .02, 60_000)]
        tB = np.c_[rng.uniform(0, 80, 60_000), rng.uniform(0, 50, 60_000), rng.normal(0, .02, 60_000)] + [0, 0, -400]
        self.assertIsNone(unir.reverse_hint(tA, tB)[0])
        self.assertLess(unir.edge_fraction(ejemplos.lapida()[0]), 0.02)
        self.assertLess(unir.edge_fraction(ejemplos.terreno()[0]), 0.02)       # curvatura, no canto

    @staticmethod
    def _rot(seed):
        rng = np.random.default_rng(seed)
        a, b, c, d = (q := rng.normal(size=4)) / np.linalg.norm(q)
        M = np.eye(4)
        M[:3, :3] = [[a*a+b*b-c*c-d*d, 2*(b*c-a*d), 2*(b*d+a*c)], [2*(b*c+a*d), a*a-b*b+c*c-d*d, 2*(c*d-a*b)],
                     [2*(b*d-a*c), 2*(c*d+a*b), a*a-b*b-c*c+d*d]]
        M[:3, 3] = rng.uniform(-300, 300, 3)
        return M

    def _halves(self, frac):
        P = relief_block()[0]
        x = P[:, 0] - P[:, 0].min()
        L = float(np.ptp(P[:, 0]))
        return P, P[x < L * (0.5 + frac / 2)], P[x > L * (0.5 - frac / 2)]

    def test_ya_alineadas_y_sin_repetidos(self):
        import unir
        P, A, B = self._halves(0.3)
        pa, pb = os.path.join(TMP, 'unir_a.ply'), os.path.join(TMP, 'unir_b.ply')
        write_ply(pa, A)
        write_ply(pb, B)
        prm = relief.Params(units='mm', clean=False, merge=[dict(path=pb, units='igual', M=unir.IDENTITY)])
        xyz, _, _, info = entrada.load_points(pa, prm)
        self.assertEqual(info['n_main'], len(A))
        self.assertLess(len(xyz), len(A) + len(B) - 0.8 * (len(A) + len(B) - len(P)))   # la zona común no se duplica
        self.assertAlmostEqual(float(np.ptp(xyz[:, 0])), float(np.ptp(P[:, 0])), delta=1)
        prm.merge[0]['path'] = os.path.join('/no/existe', 'unir_b.ply')                 # carpeta movida
        self.assertEqual(len(entrada.load_points(pa, prm)[0]), len(xyz))

    def test_por_puntos(self):
        import unir
        _, A, B0 = self._halves(0.0)                                  # sin solape
        Mt = self._rot(3)
        B = unir.transform(Mt, B0)[0]
        idx = np.random.default_rng(1).choice(len(B0), 4, replace=False)
        M, rms = unir.kabsch(B[idx], B0[idx])
        self.assertLess(rms, 0.01)
        self.assertLess(float(np.abs(unir.transform(M, B)[0] - B0).max()), 0.05)
        q = unir.quality(A, B, M)
        txt, level = unir.verdict(q, rms)
        self.assertLess(q['overlap'], 0.05)
        self.assertEqual(level, 'aviso')                              # sin solape: hay que comprobarlo a ojo
        self.assertEqual(unir.verdict(unir.quality(A, B, np.eye(4)))[1], 'mal')
        self.assertEqual(unir.verdict(unir.quality(A, B0, np.eye(4)), how='igual')[1], 'aviso')

    def test_automatica(self):
        """Un bloque con aristas en la zona común: la automática acierta (pero siempre pide mirarla)."""
        import unir
        P, _, _ = self._halves(0.3)
        x = P[:, 0] - P[:, 0].min()
        L = float(np.ptp(P[:, 0]))
        half = np.random.default_rng(3).random(len(P)) < 0.5         # dos escaneos: puntos distintos
        A, B0 = P[half & (x < 0.65 * L)], P[~half & (x > 0.35 * L)]
        B = unir.transform(self._rot(7), B0)[0]
        M, amb = unir.auto_align(A, B)
        err = float(np.linalg.norm(unir.transform(M, B)[0] - B0, axis=1).mean())
        self.assertLess(err, 1.0)
        q = unir.quality(A, B, M)
        self.assertGreater(q['overlap'], 0.2)
        self.assertEqual(unir.verdict(q, flag=amb, how='auto')[1], 'aviso')       # la automática: siempre mirarla
        self.assertEqual(unir.verdict(q, flag='inestable', how='auto')[1], 'aviso')


class GuardarUnion(unittest.TestCase):
    """Lo unido se guarda: un PLY con todo encajado (en mm, también si es pequeño) y los ajustes con la nube."""

    def test_nube_unida(self):
        import nube2stl
        rng = np.random.default_rng(0)
        A = rng.uniform(0, 12, (20_000, 3)).astype(np.float32)          # pieza de 12 mm: «auto» diría metros
        A[:, 2] *= 0.1
        B = A + np.float32(30)
        pa, pb = os.path.join(TMP, 'u_a.ply'), os.path.join(TMP, 'u_b.ply')
        ejemplos.write_ply(pa, A)
        ejemplos.write_ply(pb, B)
        M = np.eye(4)
        M[:3, 3] = -30 + 12
        prm = relief.Params(units='mm', merge=[dict(path=pb, units='igual', M=list(M.ravel()))])
        out = os.path.join(TMP, 'u_unida.ply')
        nube2stl.save_merged_task(pa, prm, out, quiet)
        xyz, _, _, info = entrada.load_points(out, relief.Params(clean=False))
        self.assertEqual(info['units'], 'mm')
        self.assertEqual(len(xyz), 40_000)
        self.assertAlmostEqual(float(xyz[:, 0].max()), 24, delta=0.1)


class Novedades(unittest.TestCase):
    """Plano robusto y por 3 puntos, suavizado que respeta aristas, relleno suave, clases LiDAR, superficies de
    revolución (eje, cono, vasija), dos caras, comparación de escaneos e interiores."""

    def test_plano_de_la_cara(self):
        rng = np.random.default_rng(0)
        face = np.c_[rng.uniform(0, 300, 60_000), rng.uniform(0, 200, 60_000), rng.normal(0, 0.2, 60_000)]
        t = np.radians(25)                                            # una peana grande e inclinada debajo
        u, w = rng.uniform(0, 300, 40_000), rng.uniform(0, 120, 40_000)
        base = np.c_[u, -w * np.cos(t), -w * np.sin(t) - 5]
        P = np.r_[face, base]
        m = P.mean(0)
        _, _, vt = np.linalg.svd(P - m, full_matrices=False)
        n0 = vt[2] if vt[2][2] > 0 else -vt[2]
        n = relief._robust_normal(P - m, n0)
        self.assertGreater(np.degrees(np.arccos(min(1, abs(n0[2])))), 3)      # el plano medio se tuerce
        self.assertLess(np.degrees(np.arccos(min(1, abs(n[2])))), 0.5)       # el de la cara, no
        n3 = relief._plane_from_points([[0, 0, 0], [100, 0, 0], [0, 100, 0]])
        self.assertAlmostEqual(abs(n3[2]), 1, places=6)
        self.assertIsNone(relief._plane_from_points([[0, 0, 0], [1, 1, 1], [2, 2, 2]]))

    def test_aristas(self):
        rng = np.random.default_rng(1)
        x = np.arange(200)
        clean = np.where((x[None, :] % 40) < 20, 2.0, 0.0) * np.ones((120, 1))   # escalones de 2 mm
        g = clean + rng.normal(0, 0.1, clean.shape)
        e, noise = relief.edge_smooth(g, 2.0)
        gs = relief.ndimage.gaussian_filter(g, 2.0)
        edge = np.abs(np.diff(clean, axis=1, append=clean[:, -1:])) > 0
        edge = relief.ndimage.binary_dilation(edge, iterations=2)
        self.assertAlmostEqual(noise, 0.1, delta=0.05)
        self.assertLess(np.abs(e - clean)[~edge].std(), 0.07)                # quita el ruido
        self.assertLess(np.abs(e - clean)[edge].mean(), 0.5 * np.abs(gs - clean)[edge].mean())   # no los cantos

    def test_relleno_suave(self):
        y, x = np.mgrid[0:150, 0:200]
        true = 5 * np.sin(x / 25) * np.cos(y / 30)
        holes = np.zeros_like(true, bool)
        holes[50:100, 70:140] = True
        grid = np.where(holes, 0.0, true)
        g = relief._fill_smooth(grid, ~holes, holes)
        self.assertLess(float(np.abs(g - true)[holes].mean()), 0.3)

    def test_clases_lidar(self):
        rng = np.random.default_rng(2)
        n = 200_000
        xy = rng.uniform(0, 100_000, (n, 2))                         # 100 × 100 m, terreno en pendiente suave
        z = 0.02 * xy[:, 0] + 500 * np.sin(xy[:, 1] / 20_000)
        cls = np.full(n, 2)
        tree = rng.random(n) < 0.25
        z[tree] += rng.uniform(2000, 15_000, tree.sum())             # copas de 2 a 15 m
        cls[tree] = 5
        cls[rng.random(n) < 0.002] = 7
        P = np.c_[xy, z].astype(np.float32)
        info = {}
        out, *_ = entrada._filter_classes(P, None, None, cls, relief.Params(classes=[2]), info, quiet)
        self.assertEqual(len(out), int((cls == 2).sum()))
        self.assertEqual(info['classes'][5], int((cls == 5).sum()))
        out, *_ = entrada._filter_classes(P, None, None, cls, relief.Params(), {}, quiet)
        self.assertEqual(len(out), int((cls != 7).sum()))           # el ruido nunca
        g = entrada.ground_mask(P)
        self.assertGreater(g[~tree].mean(), 0.9)
        self.assertLess(g[tree].mean(), 0.05)

    def test_revolucion(self):
        import revolucion
        xyz, _, axis, point = ejemplos.fragmento(with_axis=True)
        ax = revolucion.fit_axis(xyz)
        self.assertGreater(abs(float(np.dot(ax['axis'], axis))), np.cos(np.radians(1)))
        d = np.cross(np.asarray(ax['point']) - point, axis)
        self.assertLess(float(np.linalg.norm(d)), 2.0)
        V, N, info = revolucion.vessel(xyz, ax)
        rr = np.hypot(V[:, 0], V[:, 1])
        self.assertAlmostEqual(float(rr.max()), float(ejemplos._pot_r(np.linspace(55, 150, 500)).max()), delta=2.5)
        # cono: la generatriz desarrollada mide lo que la real
        rng = np.random.default_rng(5)
        z, th = rng.uniform(0, 200, 80_000), rng.uniform(0, np.radians(150), 80_000)
        r = 120 - 0.3 * z
        C = np.c_[r * np.cos(th), r * np.sin(th), z]
        U = revolucion.unroll_cone(C)
        self.assertLess(float(np.ptp(U[:, 2])), 1.0)                 # sin deformar: queda plano
        slant = 200 * np.hypot(1, 0.3)                                # generatriz, más la flecha del arco interior
        self.assertAlmostEqual(float(np.ptp(U[:, 1])), slant + 15, delta=0.05 * slant)

    def test_comparar(self):
        import comparar
        rng = np.random.default_rng(6)
        A = np.c_[rng.uniform(0, 300, 150_000), rng.uniform(0, 200, 150_000), rng.normal(0, 0.05, 150_000)]
        B = np.c_[rng.uniform(0, 300, 150_000), rng.uniform(0, 200, 150_000), rng.normal(0, 0.05, 150_000)]
        lost = np.hypot(B[:, 0] - 80, B[:, 1] - 100) < 40
        crust = np.hypot(B[:, 0] - 220, B[:, 1] - 100) < 30
        B[lost, 2] -= 1.5
        B[crust, 2] += 1.0
        Bm, d, s, M = comparar.compare(A, B, outward=np.array([0, 0, 1.0]))
        self.assertAlmostEqual(float(np.nanmedian(d[lost])), -1.5, delta=0.15)
        self.assertAlmostEqual(float(np.nanmedian(d[crust])), 1.0, delta=0.15)
        self.assertLess(float(np.abs(np.nanmedian(d[~lost & ~crust]))), 0.05)
        self.assertLess(float(np.isnan(d).mean()), 0.02)                # casi todo tiene escaneo antiguo debajo

    def test_comparar_varias_fechas(self):
        """Serie de cuatro escaneos (2015, 2018, 2021, 2024) de una placa: una zona se va hundiendo 0,5 mm/año (0, 1,5,
        3 y 4,5 mm) y una costra aparece en 2021 (+1 mm). Cada fecha frente a la primera; el mismo umbral para todas;
        en los mismos lugares, la evolución, y el ritmo: −0,5 mm/año en la zona que se pierde, 0 en el resto. Más el
        informe PDF y la fecha sacada del nombre del fichero."""
        import comparar
        rng = np.random.default_rng(6)

        def scan(t):
            P = np.c_[rng.uniform(0, 300, 120_000), rng.uniform(0, 200, 120_000), rng.normal(0, 0.05, 120_000)]
            P[:, 2] += 3 * np.sin(P[:, 0] / 23) * np.cos(P[:, 1] / 17)  # forma: lisa, se deslizaría sobre sí misma
            P[np.hypot(P[:, 0] - 80, P[:, 1] - 100) < 40, 2] -= 0.5 * t
            if t >= 6:
                P[np.hypot(P[:, 0] - 220, P[:, 1] - 100) < 30, 2] += 1.0
            return P
        years = [2015, 2018, 2021, 2024]
        A, *Bs = [scan(y - 2015) for y in years]
        res = comparar.series(A, Bs, outward=np.array([0, 0, 1.0]), dates=years)
        loss = [st['s']['loss_pct'] for st in res['steps']]
        self.assertTrue(loss[0] < loss[1] + 0.5 and all(x > 7 for x in loss))       # la zona: 8 % de la placa
        self.assertLess(res['steps'][0]['s']['gain_pct'], 0.5)
        self.assertGreater(res['steps'][2]['s']['gain_pct'], 3)                     # la costra, desde 2021
        ref, ch = res['ref'], res['change']
        zone = np.hypot(ref[:, 0] - 80, ref[:, 1] - 100) < 35
        calm = (np.hypot(ref[:, 0] - 80, ref[:, 1] - 100) > 45) & (np.hypot(ref[:, 0] - 220, ref[:, 1] - 100) > 35)
        self.assertTrue(np.allclose(np.nanmedian(ch[zone], 0), [0, -1.5, -3, -4.5], atol=0.15))
        self.assertAlmostEqual(float(np.nanmedian(res['rate'][zone])), -0.5, delta=0.03)
        self.assertLess(abs(float(np.nanmedian(res['rate'][calm]))), 0.01)
        pdf = os.path.join(TMP, 'serie.pdf')
        comparar.report_series_pdf(pdf, res, [f'placa_{y}.ply' for y in years], [str(y) for y in years],
                                   outward=np.array([0, 0, 1.0]))
        self.assertGreater(os.path.getsize(pdf), 20_000)
        self.assertEqual(comparar.date_of('/x/lapida_2019-05-21.ply')[1], '2019-05-21')
        self.assertEqual(comparar.date_of('/x/escaneo 2021.ply')[1], '2021')
        self.assertAlmostEqual(comparar.parse_date('21/05/2019'), comparar.parse_date('2019-05-21'))

    def test_interior(self):
        import interior
        P = ejemplos.cisterna(120_000)[0]
        Q, N = interior.cutaway(P, relief.Params(interior='corte'))
        self.assertLess(float(Q[:, 2].max()), float(P[:, 2].min()) + 0.8 * float(np.ptp(P[:, 2])))
        self.assertEqual(len(Q), len(N))
        c = P.mean(0)
        floor = (np.abs(Q[:, 2] - 100) < 20) & (np.hypot(Q[:, 0] - c[0], Q[:, 1] - c[1]) < 600)
        self.assertGreater(float(N[floor, 2].mean()), 0.9)          # el suelo mira hacia dentro (arriba)
        Q2, N2 = interior.negative(P, relief.Params(interior='negativo'))
        floor2 = (np.abs(Q2[:, 2] - 100) < 20) & (np.hypot(Q2[:, 0] - c[0], Q2[:, 1] - c[1]) < 600)
        self.assertLess(float(N2[floor2, 2].mean()), -0.9)          # en negativo, hacia fuera del hueco

    def test_dos_caras(self):
        def tablet(n, f, seed):
            r = np.random.default_rng(seed)
            x, y = r.uniform(0, 80, n), r.uniform(0, 50, n)
            return np.c_[x, y, f(x, y) + r.normal(0, 0.02, n)]
        A = tablet(120_000, lambda x, y: 1.5 * (np.hypot(x - 25, y - 25) < 10), 1)
        Bf = tablet(120_000, lambda x, y: 1.0 * ((x > 50) & (x < 70) & (y > 10) & (y < 20)), 2)
        t = np.radians(90)                                           # el reverso, girado en el escáner
        Rz = np.array([[np.cos(t), -np.sin(t), 0], [np.sin(t), np.cos(t), 0], [0, 0, 1]])
        B = Bf @ Rz.T
        pa, pb = os.path.join(TMP, 'anverso.ply'), os.path.join(TMP, 'reverso.ply')
        write_ply(pa, A)
        write_ply(pb, B)
        prm = relief.Params(units='mm', clean=False, target_len=0, base=2.0, simplify=100, smooth=0,
                            reverse=[dict(path=pb)])
        prm._main_path = pa
        xyz = entrada.load_points(pa, prm)[0]
        r = relief.reconstruct(xyz, prm, None, quiet)
        self.assertEqual(r.bad_edges, 0)
        self.assertAlmostEqual(float(np.ptp(r.verts[:, 2])), 1.5 + 2.0 + 1.0, delta=0.5)



class Esquinas(unittest.TestCase):
    """Dos caras en ángulo: unirlas por la arista, reconocerlas al abrir y la pieza con un mapa de alturas por cara."""

    def test_unir_por_la_arista(self):
        import unir
        (A, _), (B, _), Mt = ejemplos.esquina(split=True)
        B0 = unir.transform(Mt, B)[0]
        rng = np.random.default_rng(1)
        back = np.linalg.inv(Mt)
        ea = [np.array(p, float) + rng.normal(0, 0.5, 3) for p in ([3, 0, 0], [237, 0, 0])]      # clics imprecisos
        eb = [unir.transform(back, np.array([p], float))[0][0] + rng.normal(0, 0.5, 3)          # en el mismo orden
              for p in ([6, 0, 0], [235, 0, 0])]
        M, info = unir.edge_align(A, B, ea, eb, 90)
        self.assertLess(float(np.linalg.norm(unir.transform(M, B)[0] - B0, axis=1).mean()), 2.0)
        M1, _ = unir.edge_align(A, B, ea, eb, 90, flip_b=True)        # aunque el lado de la añadida se adivine mal,
        self.assertLess(float(np.linalg.norm(unir.transform(M1, B)[0] - B0, axis=1).mean()), 2.0)   # no se da la vuelta
        self.assertNotEqual(unir.edge_verdict(info)[1], 'mal')
        M2, _ = unir.edge_align(A, B, ea, eb, 90, other_side=True)                    # la otra solución
        self.assertGreater(float(np.linalg.norm(unir.transform(M2, B)[0] - B0, axis=1).mean()), 50)
        self.assertTrue(unir.collinear(ea + [ea[0] * 0.5 + ea[1] * 0.5]))
        self.assertFalse(unir.collinear([[0, 0, 0], [100, 0, 0], [0, 100, 0]]))

    def test_franja_de_la_otra_cara(self):
        import unir
        for ang, strip in ((88.5, 20), (120, 15)):
            (A, _), (B, _), Mt = ejemplos.esquina(split=True, angle=ang, strip=strip)
            A, B = A.astype(np.float64), B.astype(np.float64)
            B0 = unir.transform(Mt, B)[0]
            rng = np.random.default_rng(2)
            back = np.linalg.inv(Mt)
            ea = [np.array(p, float) + rng.normal(0, 0.7, 3) for p in ([3, 0, 0], [237, 0, 0])]
            eb = [unir.transform(back, np.array([p], float))[0][0] + rng.normal(0, 0.7, 3)
                  for p in ([6, 0, 0], [235, 0, 0])]
            M, info = unir.edge_align_strip(A, B, ea, eb, 90)         # el ángulo escrito (90) no cuenta
            self.assertAlmostEqual(info['angle'], ang, delta=0.3)
            self.assertIsNotNone(info['refine'])
            self.assertLess(float(np.linalg.norm(unir.transform(M, B)[0] - B0, axis=1).mean()), 0.3)
            self.assertEqual(unir.edge_verdict(info)[1], 'ok')
        (A, _), (B, _), _ = ejemplos.esquina(split=True)               # sin franja: como antes
        f = unir.face_at_edge(A.astype(np.float64), [3, 0, 0], [237, 0, 0])
        self.assertIsNone(unir.other_face_strip(A.astype(np.float64), f))

    def test_esquina(self):
        import esquina
        import superficie
        P, _ = ejemplos.esquina()
        g = superficie.guess_kind(P)
        self.assertEqual(g[0], 'bloque')
        self.assertAlmostEqual(g[2]['corner'], 90, delta=1)
        self.assertIsNone(esquina.two_faces(rock()))                  # una roca curva no es una esquina
        prm = relief.Params(mode='bloque', corner=True, target_len=0, res=1.5, simplify=100)
        r = relief.reconstruct(P, prm, None, quiet)
        self.assertEqual(r.bad_edges, 0)
        ext = np.sort(np.ptp(r.verts, 0))
        for got, want in zip(ext, (113, 153, 240)):                    # 110 + relieve, 150 + moldura, 240
            self.assertAlmostEqual(float(got), want, delta=5)
        self.assertLess(float(np.nanmean(np.abs(r.dev))), 0.5)       # pegada al escaneo en las dos caras


class Realce(unittest.TestCase):
    """Realzar lo pequeño: solo los detalles de poco relieve, sin escalones junto a los cantos grandes."""

    def test_curva(self):
        a, f = relief.boost_curve(2, 0.1, 0.5)
        at = lambda x: float(np.interp(x, a, f))                     # noqa: E731
        self.assertTrue(np.all(np.diff(f) >= 0))                     # nunca adelanta a un detalle más alto
        self.assertAlmostEqual(at(0.04), 0.04, places=3)              # ruido: no se toca
        self.assertAlmostEqual(at(0.1), 0.2, places=3)                # el doble en el umbral
        self.assertAlmostEqual(at(0.5), 0.5, places=3)                # nada en «hasta»
        self.assertLess(at(0.3) - 0.3, at(0.2) - 0.2)                 # cada vez menos aumento

    def test_realce(self):
        n, cm = 400, 0.1
        h = np.zeros((n, n))
        h[:, 200:] += 3.0                                             # canto grande
        yy = np.arange(n)[:, None] + 0 * h
        h[np.abs(yy - 100) < 5] -= 0.15                               # surco de 0,15 mm
        h[np.abs(yy - 300) < 5] -= 0.04                               # ruido: no
        y, _ = relief.boost_small(h, np.ones_like(h, bool), cm)
        add = y - h
        self.assertLess(add[100, 100], -0.07)                         # se hunde casi 0,1 mm más
        self.assertAlmostEqual(add[300, 100], 0, places=3)
        self.assertLess(np.abs(add[200:280, 150:250]).max(), 0.02)   # junto al canto, nada
        P, _ = ejemplos.gastada(n=120_000)
        r = relief.reconstruct(P.astype(np.float64), relief.Params(target_len=150, boost=2, simplify=100), None, quiet)
        self.assertEqual(r.bad_edges, 0)

    def test_vista_de_detalle(self):
        """Orientación › Detalle de la pieza: el mapa de alturas de verdad, guardado; el realce se aplica sin rehacerlo."""
        import time
        P, _ = ejemplos.gastada(n=200_000)
        op = relief.OrientPreview(P.astype(np.float64))
        prm = relief.Params(target_len=150)
        d0 = op.compute_detail(prm)
        self.assertTrue(d0['detail'])
        self.assertEqual(d0['h'].shape, op._det.grid.shape)          # la rejilla de la pieza, la de heightmap()
        hm = op._det
        t = time.time()
        d1 = op.compute_detail(relief.Params(target_len=150, boost=2))
        self.assertIs(op._det, hm)                                    # no se ha rehecho el mapa de alturas
        self.assertLess(time.time() - t, 5.0)                         # (holgado: el equipo puede estar ocupado)
        diff = np.nan_to_num(d1['h'] - d0['h'])
        self.assertGreater(np.abs(diff).max(), 0.05)                  # las letras, realzadas
        op.compute_detail(relief.Params(target_len=150, angle=10))
        self.assertIsNot(op._det, hm)                                 # girar sí lo rehace

    def test_realce_en_malla(self):
        """Modos con Poisson: el realce sobre la malla sigue la curva de la pieza y no toca los cantos grandes."""
        R, nth, nz = 20.0, 1260, 600                                  # cilindro, paso de 0,1 mm
        TH, Z = np.meshgrid(np.linspace(0, 2 * np.pi, nth, endpoint=False), np.linspace(0, 60, nz))
        s = R * TH
        groove = -0.15 * np.clip(1 - np.abs(s % 6 - 3) / 0.5, 0, 1) * (Z > 10) * (Z < 25)   # surcos de 0,15 mm
        rad = R + groove + 2.0 * (Z > 40) * (np.abs(s - 60) < 20)                          # escalón de 2 mm
        V = np.stack([rad * np.cos(TH), rad * np.sin(TH), Z], -1).reshape(-1, 3)
        i, j = np.arange(nz - 1)[:, None] * nth, np.arange(nth)[None]
        a, b, c, d = (i + j).ravel(), (i + (j + 1) % nth).ravel(), (i + nth + j).ravel(), (i + nth + (j + 1) % nth).ravel()
        T = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])
        V2, _ = relief.boost_mesh(V, T, 2.0, 0.1, 0.5, 3.0)
        dr = np.hypot(V2[:, 0], V2[:, 1]).reshape(nz, nth) - rad
        self.assertLess(dr[(groove < -0.149)].mean(), -0.08)          # surco: 0,15 -> casi 0,25 mm
        self.assertLess(np.abs(dr[(Z > 28) & (Z < 37)]).max(), 0.005)  # la curvatura no es un detalle
        self.assertLess(np.abs(dr[Z > 40]).max(), 0.025)              # junto al canto grande, casi nada
        self.assertLess((np.abs(dr[Z > 40]) > 0.01).sum(), 5)          # (un vértice en la esquina de la caja)

class MemoriaEscalaReal(unittest.TestCase):
    """Piezas enormes a escala 1 (un acueducto de 37 m en metros con celda de 5 mm): lo que antes agotaba la memoria."""

    def test_paredes_de_la_base_acotadas(self):
        """«Abierto por abajo» pone paredes y base sintéticas. Con una celda mucho más fina que la nube (rala y
        alta), la planta salía salpicada de celdas sueltas con paredes de metros: 106 millones de puntos. Ahora
        nunca pasan del doble de los puntos de la nube, y la celda crece lo justo."""
        import objeto3d
        rng = np.random.default_rng(3)
        n = 20_000
        P = np.c_[rng.uniform(0, 37_000, n), rng.uniform(0, 8_000, n), rng.uniform(500, 4_000, n)]
        pts, nrm, nb, area, cell = objeto3d._ground_skirt(P, 5.0, 0.0)
        self.assertLessEqual(len(pts), max(2 * n, 200_000))
        self.assertGreater(cell, 5.0)
        self.assertEqual(len(pts), len(nrm))
        self.assertGreater(area, 0.5 * 37_000 * 8_000)            # la planta sigue cerrada entera
        dense = np.c_[rng.uniform(0, 100, n), rng.uniform(0, 100, n), rng.uniform(0, 5, n)]
        self.assertEqual(objeto3d._ground_skirt(dense, 2.0, -1.0)[4], 2.0)   # si cabe, a la celda pedida

    def test_estimacion_con_poisson_limitado(self):
        """La profundidad de Poisson llega como mucho a 11: en una pieza de 37 m la celda real es de unos 20 mm,
        y pedir 5 mm no cuadruplica la memoria (la estimación decía 113 GB para algo que gasta 2)."""
        rng = np.random.default_rng(4)
        xyz = np.c_[rng.uniform(0, 37_000, 200_000), rng.uniform(0, 15_000, 200_000), rng.uniform(0, 4_000, 200_000)]
        gb = []
        for r in (20.0, 5.0):
            p = relief.Params(mode='objeto', ground=True, ratio=1.0, res=r)
            gb.append(relief.memory_estimate(relief.face_stats(xyz, p), p, len(xyz), avail=20)['gb'])
        self.assertLess(gb[1], 1.2 * gb[0])

    def test_estimacion_abierto_por_abajo_y_leer(self):
        """Medido con un terreno de 35 × 15 m (una malla de 19 M triángulos): la estimación se quedaba hasta un 40 %
        corta porque no contaba la base y las paredes de «Abierto por abajo» (tanta superficie como la escaneada)
        ni volver a leer la malla en el proceso de cálculo. Y los procesos de simplificación se ajustan a la memoria
        libre en ese momento."""
        rng = np.random.default_rng(5)
        xyz = np.c_[rng.uniform(0, 8_000, 300_000), rng.uniform(0, 4_000, 300_000), rng.uniform(0, 2_000, 300_000)]
        p = relief.Params(mode='objeto', ratio=1.0, res=14.0)
        st = relief.face_stats(xyz, p)
        e0 = relief.memory_estimate(st, p, len(xyz), avail=20)
        e1 = relief.memory_estimate(st, relief.Params(mode='objeto', ratio=1.0, res=14.0, ground=True), len(xyz),
                                    avail=20)
        self.assertAlmostEqual(e1['cells'], relief.MEM_GROUND * e0['cells'])
        self.assertGreater(e1['gb'], e0['gb'])
        big = relief.memory_estimate(dict(st, mesh_tris=60e6), p, len(xyz), avail=20)
        self.assertAlmostEqual(big['gb'], relief.MEM_BASE_GB + 60e6 * relief.MEM_READ_PER_TRI, places=3)
        self.assertIn('leer la malla', big['detail'])
        old = relief.memory_available
        try:                                       # simplificar 5 M triángulos en 8 procesos: según lo que haya
            relief.memory_available = lambda: (20.0, 32.0)
            self.assertEqual(relief.dec_workers_fit(5_000_000, 8), 8)
            relief.memory_available = lambda: (4.0, 32.0)
            self.assertEqual(relief.dec_workers_fit(5_000_000, 8), 1)   # no caben ni dos: en un solo proceso
            relief.memory_available = lambda: (4.5, 32.0)          # 2,5 GB tras el margen: 2,1 de franjas y 3 Pythons
            self.assertEqual(relief.dec_workers_fit(5_000_000, 8), 3)
        finally:
            relief.memory_available = old


def _write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


class Actualizar(unittest.TestCase):
    """Buscar e instalar versiones nuevas (actualizar.py) sin salir a internet: GitHub se sustituye por respuestas
    preparadas, el .zip de la versión se hace aquí y el «repositorio remoto» es otra carpeta de git."""

    def test_versiones(self):
        import actualizar as a
        self.assertEqual(a.parse_version('v0.21.0'), (0, 21, 0, 1))
        self.assertEqual(a.parse_version('0.21'), (0, 21, 0, 1))
        self.assertTrue(a.newer('0.21.0', '0.20.0'))
        self.assertTrue(a.newer('v0.20.1', '0.20.0'))
        self.assertTrue(a.newer('1.0.0', '0.99.9'))
        self.assertFalse(a.newer('0.20.0', '0.20.0'))
        self.assertFalse(a.newer('0.21.0-beta', '0.21.0'))      # la preversión va antes que la final
        self.assertTrue(a.newer('0.21.0', '0.21.0-beta'))
        self.assertFalse(a.newer('basura', '0.20.0'))

    def test_ultima_version(self):
        import json
        import actualizar as a
        old = a._get

        def sin_releases(url, timeout=10):                        # repositorio solo con etiquetas
            if url.endswith('/releases/latest'):
                raise a.UpdateError(f'No se encuentra {url}')
            return json.dumps([{'name': 'v0.9.0'}, {'name': 'v0.21.0'}, {'name': 'prueba'},
                               {'name': 'v0.20.0'}]).encode()

        def con_release(url, timeout=10):
            return json.dumps({'tag_name': 'v0.22.0', 'html_url': 'https://x/r', 'body': '- novedad',
                               'published_at': '2026-12-01T10:00:00Z', 'zipball_url': 'https://x/z'}).encode()
        try:
            a._get = sin_releases
            d = a.latest('alguien/nube2stl')
            self.assertEqual((d['version'], d['tag']), ('0.21.0', 'v0.21.0'))
            self.assertIn('/zipball/v0.21.0', d['zip'])
            a._get = con_release
            d = a.latest('alguien/nube2stl')
            self.assertEqual((d['version'], d['notes'], d['date']), ('0.22.0', '- novedad', '2026-12-01'))
        finally:
            a._get = old
        with self.assertRaises(a.UpdateError):                     # sin repositorio configurado no sale a la red
            os.environ.pop('NUBE2STL_REPO', None)
            if not a.GITHUB_REPO:
                a.latest()
            else:
                raise a.UpdateError('configurado')

    def test_carpeta(self):
        """Código descomprimido de un .zip: los ficheros que cambian se guardan en .anterior/<versión> y se
        sustituyen; .venv y las rutas con «..» no se tocan nunca."""
        import io
        import zipfile
        import actualizar as a
        folder = tempfile.mkdtemp(dir=TMP)
        for name, text in [('nube2stl.py', 'viejo'), ('relief.py', 'igual'), ('.venv/x.py', 'entorno')]:
            os.makedirs(os.path.dirname(os.path.join(folder, name)), exist_ok=True)
            _write(os.path.join(folder, name), text)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:                        # como el «zipball» de GitHub: una carpeta raíz
            z.writestr('alguien-nube2stl-abc123/nube2stl.py', 'nuevo')
            z.writestr('alguien-nube2stl-abc123/relief.py', 'igual')
            z.writestr('alguien-nube2stl-abc123/iconos/nuevo.svg', '<svg/>')
            z.writestr('alguien-nube2stl-abc123/.venv/x.py', 'pisado')
            z.writestr('alguien-nube2stl-abc123/../fuera.txt', 'fuera')
        changed = a.update_folder({'zip': ''}, log=lambda s: None, folder=folder, data=buf.getvalue())
        self.assertEqual(sorted(changed), ['iconos/nuevo.svg', 'nube2stl.py'])
        self.assertEqual(_read(os.path.join(folder, 'nube2stl.py')), 'nuevo')
        self.assertEqual(_read(os.path.join(folder, '.anterior', a.__version__, 'nube2stl.py')), 'viejo')
        self.assertEqual(_read(os.path.join(folder, '.venv', 'x.py')), 'entorno')
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(folder), 'fuera.txt')))
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('otra-cosa/leeme.txt', 'x')
        with self.assertRaises(a.UpdateError):
            a.update_folder({'zip': ''}, log=lambda s: None, folder=folder, data=bad.getvalue())

    @unittest.skipUnless(shutil.which('git'), 'sin git')
    def test_git(self):
        """Copia hecha con git clone: «git pull» la pone al día, y se niega si hay cambios hechos a mano."""
        import actualizar as a
        base = tempfile.mkdtemp(dir=TMP)
        env = dict(os.environ, GIT_AUTHOR_NAME='p', GIT_AUTHOR_EMAIL='p@p', GIT_COMMITTER_NAME='p',
                   GIT_COMMITTER_EMAIL='p@p')

        def git(*args, cwd=base):
            subprocess.run(['git', *args], cwd=cwd, env=env, check=True, capture_output=True)
        remote, work, mine = (os.path.join(base, n) for n in ('remoto.git', 'trabajo', 'instalado'))
        git('init', '--bare', '-b', 'main', remote)
        git('clone', remote, work)
        _write(os.path.join(work, 'nube2stl.py'), 'v1')
        git('add', '.', cwd=work)
        git('commit', '-m', 'v1', cwd=work)
        git('push', 'origin', 'HEAD:main', cwd=work)
        git('clone', remote, mine)
        self.assertEqual(a.install_kind(mine), 'git')
        _write(os.path.join(work, 'nube2stl.py'), 'v2')    # sale una versión nueva
        git('commit', '-am', 'v2', cwd=work)
        git('push', 'origin', 'HEAD:main', cwd=work)
        old = dict(os.environ)
        os.environ.update(env)
        try:
            self.assertTrue(a.update_git(log=lambda s: None, folder=mine))
            self.assertEqual(_read(os.path.join(mine, 'nube2stl.py')), 'v2')
            self.assertFalse(a.update_git(log=lambda s: None, folder=mine))      # ya al día
            _write(os.path.join(mine, 'nube2stl.py'), 'tocado a mano')
            with self.assertRaises(a.UpdateError):
                a.update_git(log=lambda s: None, folder=mine)
            self.assertEqual(_read(os.path.join(mine, 'nube2stl.py')), 'tocado a mano')
        finally:
            os.environ.clear()
            os.environ.update(old)


class Coherencia(unittest.TestCase):
    """La ventana, la ayuda, el asistente y la ficha no se desincronizan al añadir o mover parámetros."""

    def test_ayuda_y_parametros(self):
        import re
        import nube2stl
        html = _read(os.path.join(HERE, 'ayuda.html'))
        ids = set(re.findall(r'id="([^"]+)"', html))
        self.assertFalse(set(re.findall(r'href="#([^"]+)"', html)) - ids, 'enlaces rotos en la ayuda')
        for name in nube2stl.SPEC_BY_NAME:
            self.assertIn(nube2stl.HELP_ANCHOR.get(name, f'p-{name}'), ids, f'{name} sin apartado en la ayuda')
        for f in relief.Params.__dataclass_fields__:       # todo ajuste tiene nombre legible (ficha, resúmenes)
            self.assertNotEqual(nube2stl.param_label(f), f, f'{f} sin etiqueta')
        tabs = {g for _, _, gs in nube2stl.PARAM_TABS for g in gs} | {'Tipo de pieza'}
        self.assertEqual(tabs, {g for g, _ in nube2stl.SPEC})

    def test_recetas(self):
        import recetas
        kinds = {k['key'] for k in recetas.KINDS}
        for k in recetas.KINDS:
            self.assertTrue(recetas.purposes_of(k['key']), k['key'])
            self.assertEqual(recetas.kind_of(*(k['params'][x] for x in recetas.KIND_PARAMS)), k['key'])
        for p in recetas.PURPOSES:
            self.assertIn(p['kind'], kinds)
            for n in list(p['params']) + p.get('ask', []):
                self.assertIn(n, relief.Params.__dataclass_fields__, f'{p["key"]}: {n}')

    def test_ajustes_en_la_ficha(self):
        import ficha
        p = relief.Params(crop=[{'M': [0.1234567] * 16, 'poly': [[0.1, 0.2]] * 4, 'inside': True}],
                          keep_groups=[[1.5, 2.5, 3.5]])
        txt = ''.join(ln.strip() for ln in ficha._json_lines(p))
        self.assertEqual(relief.Params.from_json(txt), p)



class ZonasDetalle(unittest.TestCase):
    """Zonas de detalle: una parte de la nube se hace con más resolución que el resto."""

    @staticmethod
    def zone():
        """Zona vista desde arriba (pantalla = x/150, y/150): un lazo en forma de triángulo, a la derecha."""
        M = np.eye(4)
        M[:3, :3] /= 150
        return {'M': M.ravel().tolist(), 'poly': [[0.2, -0.4], [0.9, 0.0], [0.2, 0.4]]}

    def test_mascara_del_lazo(self):
        xyz = np.random.default_rng(0).uniform(-150, 150, (50_000, 3))
        m = relief.zone_mask(xyz, [self.zone()])
        x, y = xyz[:, 0] / 150, xyz[:, 1] / 150
        ref = (x >= 0.2) & (np.abs(y) <= 0.4 * (0.9 - x) / 0.7)       # dentro del triángulo
        self.assertGreater((m == ref).mean(), 0.999)               # (salvo los que caen justo en el borde)
        self.assertFalse(relief.zone_mask(xyz, []).any())

    def test_en_que_tipos(self):
        z = [self.zone()]
        self.assertTrue(relief.has_detail(relief.Params(mode='objeto', detail=z)))
        self.assertTrue(relief.has_detail(relief.Params(mode='bloque', detail=z)))
        self.assertTrue(relief.has_detail(relief.Params(mode='alturas', detail=z)))
        self.assertFalse(relief.has_detail(relief.Params(mode='bloque', corner=True, detail=z)))   # esquina, no
        self.assertFalse(relief.has_detail(relief.Params(mode='objeto')))
        # en el mapa de alturas, la celda general dividida por un entero (2 ÷ 4 = 0,5; 2 ÷ 3 para pedir 0,7)
        p = relief.Params(mode='alturas', detail=z)
        self.assertEqual(relief.zone_res(p, 2.0, 0.5), 0.5)
        p.detail_res = 0.7
        self.assertAlmostEqual(relief.zone_res(p, 2.0, 0.5), 2 / 3)
        # resolución de las zonas: la del escaneo si no se da; nada si no es más fina que la general
        p = relief.Params(mode='objeto', detail=z)
        self.assertEqual(relief.zone_res(p, 2.0, 0.5), 0.5)
        self.assertIsNone(relief.zone_res(p, 0.5, 0.5))
        p.detail_res = 1.0
        self.assertEqual(relief.zone_res(p, 2.0, 0.5), 1.0)

    def test_pincel_y_borrar(self):
        """Pincel: los puntos a menos de r de los centros del trazo. Borrar quita de lo marcado antes."""
        xyz = np.random.default_rng(1).uniform(-100, 100, (40_000, 3))
        stroke = {'brush': [[x, 0.0, 0.0] for x in range(-50, 51, 5)], 'r': 10.0}
        m = relief.zone_mask(xyz, [stroke])
        x, d = xyz[:, 0], np.hypot(xyz[:, 1], xyz[:, 2])
        self.assertTrue(m[(np.abs(x) < 50) & (d < 9)].all())          # dentro del «tubo» del trazo
        self.assertFalse(m[(np.abs(x) > 61) | (d > 10)].any())        # fuera, nada
        rub = {'brush': [[0.0, 0.0, 0.0]], 'r': 20.0, 'erase': True}
        m2 = relief.zone_mask(xyz, [stroke, rub])
        self.assertFalse(m2[np.linalg.norm(xyz, axis=1) < 20].any())  # lo borrado
        self.assertTrue(m2[(x > 30) & (x < 50) & (d < 9)].all())      # el resto del trazo sigue
        # borrar solo, sin nada pintado: no hay zonas
        self.assertEqual(relief.zone_count([stroke, rub]), (1, 1))
        self.assertFalse(relief.has_detail(relief.Params(mode='objeto', detail=[rub])))
        self.assertTrue(relief.has_detail(relief.Params(mode='objeto', detail=[stroke, rub])))
        # un lazo también puede borrar de un pincel
        lazo = dict(self.zone(), erase=True)
        m3 = relief.zone_mask(xyz, [stroke, lazo])
        self.assertFalse((m3 & relief.zone_mask(xyz, [self.zone()])).any())

    def test_pincel_pinta_solo_lo_de_delante(self):
        """Del trazo en pantalla a los centros: con dos placas una detrás de otra, solo se pinta la de delante.
        Cámara en z = 500 mirando hacia −z, perspectiva con f = 2 y una vista de 800 × 800 píxeles."""
        import visor
        g = np.mgrid[-100:100:1.0, -100:100:1.0].reshape(2, -1).T
        front = np.c_[g, np.zeros(len(g))]
        back = np.c_[g, np.full(len(g), -50.0)]
        P = np.r_[front, back].astype(np.float32)
        f, n, fa = 2.0, 1.0, 2000.0
        proj = np.array([[f, 0, 0, 0], [0, f, 0, 0], [0, 0, (fa + n) / (n - fa), 2 * fa * n / (n - fa)],
                         [0, 0, -1, 0]])
        view = np.eye(4)
        view[2, 3] = -500
        stroke = [[300 + k, 400] for k in range(0, 201, 4)]           # de x = −50 a +50 mm (a 500 mm de la cámara)
        cen, r = visor.brush_centers(P, proj @ view, f, (800, 800), stroke, 14)
        self.assertAlmostEqual(r, 14 * 2 * 500 / (f * 800), places=3)  # 8,75 mm: 14 px a esa distancia
        self.assertTrue(np.allclose(cen[:, 2], 0))                     # todos en la placa de delante
        self.assertLess(cen[:, 0].min(), -45)
        self.assertGreater(cen[:, 0].max(), 45)
        m = relief.zone_mask(P.astype(np.float64), [{'brush': cen.tolist(), 'r': r}])
        self.assertTrue(m[:len(front)].any())
        self.assertFalse(m[len(front):].any())                         # la de detrás, sin pintar
        self.assertLess(np.abs(P[m, 1]).max(), r + 0.5)                # una franja del ancho del pincel

    def test_mapa_de_alturas(self):
        """Mapa de alturas con una zona: rejilla fina solo en la caja de la zona (hm.zone), la general sigue igual;
        dentro sigue a la superficie casi como una rejilla fina entera, fuera es la general. Losa con bultitos de
        0,8 mm cada 5 mm, celda general de 2 mm y zona (x > 0) a 0,5 mm."""
        rng = np.random.default_rng(0)
        x, y = rng.uniform(-100, 100, 400_000), rng.uniform(-60, 60, 400_000)
        f = lambda x, y: 0.8 * np.sin(2 * np.pi * x / 5) * np.sin(2 * np.pi * y / 5) + 0.02 * x   # noqa: E731
        xyz = np.c_[x, y, f(x, y)]
        M = np.eye(4)
        M[:3, :3] /= 100
        zone = {'M': M.ravel().tolist(), 'poly': [[0.0, -1], [1, -1], [1, 1], [0.0, 1]]}

        def error(detail, res):
            p = relief.Params(mode='alturas', target='real', res=res, detail=detail, detail_res=0.5, front_pct=50)
            hm = relief.heightmap(xyz, p, fine=relief.zone_mask(xyz, detail) if detail else None)
            T = np.linalg.inv(hm.to_grid)
            g, c = relief.zone_full_grid(hm)
            jj, ii = np.mgrid[0:g.shape[0], 0:g.shape[1]]
            G = np.c_[(ii.ravel() + 0.5) * c, (jj.ravel() + 0.5) * c, g.ravel()] @ T[:3, :3].T + T[:3, 3]
            e = np.abs(G[:, 2] - f(G[:, 0], G[:, 1]))
            return hm, np.median(e[G[:, 0] > 5]), np.median(e[G[:, 0] < -5])
        _, gin, gout = error([], 2.0)                              # todo a 2 mm
        _, fin, _ = error([], 0.5)                                 # todo a 0,5 mm
        hm, zin, zout = error([zone], 2.0)
        self.assertEqual(hm.res, 2.0)                              # la general no cambia
        self.assertEqual(hm.detail_res, 0.5)
        Z = hm.zone
        self.assertEqual(Z['k'], 4)
        self.assertLess((Z['I1'] - Z['I0']) * (Z['J1'] - Z['J0']), 0.65 * hm.grid.size)   # la caja: media pieza
        # medido: todo a 2 mm 0,30; todo a 0,5 mm 0,091; con la zona, dentro 0,097 y fuera 0,295
        self.assertLess(zin, 0.4 * gin)                            # dentro, mucho mejor que la general
        self.assertLess(zin, 1.15 * fin)                           # y casi como la fina entera
        self.assertLess(zout, 1.1 * gout)                          # fuera, como la general (interpolada)

    def test_alturas_cosida(self):
        """La malla de un relieve con una zona: celdas finas solo en la caja, cosidas con las generales (abanicos
        en el borde): cerrada con contorno, rectángulo, marco y molde, del mismo tamaño que sin zona y con menos de la
        mitad de triángulos que con toda la rejilla fina."""
        rng = np.random.default_rng(1)
        x, y = rng.uniform(-100, 100, 200_000), rng.uniform(-60, 60, 200_000)
        xyz = np.c_[x, y, 0.8 * np.sin(2 * np.pi * x / 5) * np.sin(2 * np.pi * y / 5)]
        M = np.eye(4)
        M[:3, :3] /= 100
        zone = {'M': M.ravel().tolist(), 'poly': [[0.1, -0.3], [0.5, -0.3], [0.5, 0.3], [0.1, 0.3]]}
        for extra in ({}, {'shape': 'rectangulo'}, {'frame': 5.0}, {'negative': True}):
            kw = dict(mode='alturas', target='real', res=2.0, base=3, simplify=100, **extra)
            r = relief.reconstruct(xyz, relief.Params(detail=[zone], detail_res=0.5, **kw), log=quiet)
            r0 = relief.reconstruct(xyz, relief.Params(**kw), log=quiet)
            rf = relief.reconstruct(xyz, relief.Params(**dict(kw, res=0.5)), log=quiet)
            self.assertEqual(r.bad_edges, 0, extra)
            self.assertTrue(np.allclose(np.ptp(r.verts[:, :2], 0), np.ptp(r0.verts[:, :2], 0), atol=0.01), extra)
            self.assertLess(len(r.tris), 0.5 * len(rf.tris), extra)
            self.assertGreater(len(r.tris), 2 * len(r0.tris), extra)

    def test_memoria_cuenta_las_zonas(self):
        st = relief.face_stats(rock(n=60_000), relief.Params(mode='objeto'))
        p = relief.Params(mode='objeto', target='real', res=4.0)
        base = relief.memory_estimate(st, p, 60_000)['cells']
        q = relief.Params(mode='objeto', target='real', res=4.0, detail=[self.zone()], detail_res=1.0)
        st['detail_frac'] = 0.1
        zoned = relief.memory_estimate(st, q, 60_000)['cells']
        self.assertGreater(zoned, 1.5 * base)                      # 10 % de la superficie con 16 veces más celdas
        self.assertLess(zoned, 3 * base)

    def test_simplificar_por_zonas(self):
        """Fuera de la zona se simplifica con la tolerancia general; dentro, con la fina (quedan más triángulos)."""
        if apart(self):
            return
        import open3d as o3d
        m = o3d.geometry.TriangleMesh.create_sphere(100.0, 120)
        V, T = np.asarray(m.vertices), np.asarray(m.triangles)
        r = relief.Result(V, T, None, None, 1.0, 1.0, 0, 0, relief._volume(V, T), mode='objeto')
        inside = lambda v: v[:, 0] > 50                                     # noqa: E731
        out = relief.simplify_zoned(r, 1.0, 0.05, inside, quiet)
        self.assertLess(len(out.tris), len(T))
        self.assertEqual(relief._bad_edges(out.tris, len(out.verts)), 0)    # sigue cerrada
        c, c0 = out.verts[out.tris].mean(1), V[T].mean(1)
        a_in = (c[:, 0] > 60).sum() / (c0[:, 0] > 60).sum()           # parte que queda de cada lado
        a_out = (c[:, 0] < 40).sum() / (c0[:, 0] < 40).sum()
        self.assertGreater(a_in, 3 * a_out)                        # en la zona se conserva mucho más

    def test_ajustes_y_plan(self):
        import nube2stl
        p = relief.Params(mode='objeto', detail=[self.zone()], detail_res=0.5)
        self.assertEqual(relief.Params.from_json(p.to_json()), p)
        st = relief.face_stats(rock(n=30_000), p)
        st['detail_frac'] = 0.2
        self.assertIn('Zonas de detalle', nube2stl.plan_html(st, p))
        p.mode, p.corner = 'bloque', True
        self.assertIn('no cuentan', nube2stl.plan_html(st, p))


    def test_nota_de_avanzado(self):
        """La nota bajo «Resolución de las zonas»: celda dentro y fuera y si Poisson llega a ella."""
        import nube2stl
        st = relief.face_stats(rock(n=30_000), relief.Params(mode='objeto'))
        p = relief.Params(mode='objeto', target='real', res=4.0)
        self.assertIn('no hay ninguna zona', nube2stl.zone_note(st, p))
        p.detail, p.detail_res = [self.zone()], 1.0
        t = nube2stl.zone_note(st, p)
        self.assertIn('celda de 1 mm', t)
        self.assertIn('automática', t)
        p.poisson_depth = 6                                         # a mano y demasiado baja: avisa
        self.assertIn('no llega a su celda', nube2stl.zone_note(st, p))
        p.poisson_depth, p.detail_res = 0, 8.0                      # más gruesa que la general: sin efecto
        self.assertIn('no cambian nada', nube2stl.zone_note(st, p))


class ZonasDetalle3D(unittest.TestCase):
    """Reconstrucción con una zona de detalle: dentro sale mucho más fiel, fuera igual."""

    def test_objeto_con_zona(self):
        if apart(self):
            return
        rng = np.random.default_rng(0)
        n = 300_000
        u, th = rng.uniform(0, 2 * np.pi, n), np.arccos(rng.uniform(-1, 1, n))
        R = 100 + 0.8 * np.sin(60 * u) * np.sin(60 * th)               # bultitos de 0,8 mm, cada ~5 mm
        xyz = np.c_[R * np.sin(th) * np.cos(u), R * np.sin(th) * np.sin(u), R * np.cos(th)].astype(np.float32)
        M = np.eye(4)
        M[:3, :3] /= 150
        zone = {'M': M.ravel().tolist(), 'poly': [[0.2, -0.3], [0.8, -0.3], [0.8, 0.3], [0.2, 0.3]]}
        zin = relief.zone_mask(xyz, [zone])
        import open3d as o3d
        err = []
        for det in ([], [zone]):
            p = relief.Params(mode='objeto', target='real', res=4.0, denoise=0, detail=det, detail_res=1.0)
            r = relief.reconstruct(xyz, p)
            self.assertEqual(r.bad_edges, 0)
            Ti = np.linalg.inv(r.xform)
            V = (r.verts @ Ti[:3, :3].T + Ti[:3, 3]).astype(np.float32)
            sc = o3d.t.geometry.RaycastingScene()
            sc.add_triangles(o3d.core.Tensor(V), o3d.core.Tensor(r.tris.astype(np.uint32)))
            d = sc.compute_distance(o3d.core.Tensor(xyz)).numpy()
            err.append((d[zin].mean(), d[~zin].mean(), len(r.tris)))
            if det:     # la pieza recuerda sus zonas (también al pasar por el .npz del proceso aparte) para pintarlas
                import proceso
                f = os.path.join(TMP, 'zona.npz')
                proceso.save_result(f, r)
                r2 = proceso.load_result(f)
                z = relief.zone_on_mesh(r2)
                self.assertTrue(np.array_equal(z, relief.zone_mask(V, [zone])))
                self.assertTrue(0.1 < z.mean() < 0.9)
            else:
                self.assertIsNone(relief.zone_on_mesh(r))
        (zin0, out0, n0), (zin1, out1, n1) = err
        self.assertLess(zin1, 0.4 * zin0)              # en la zona, el error baja a menos de la mitad (≈ 5 veces)
        self.assertLess(out1, 1.3 * out0)              # fuera, como sin zona
        self.assertLess(n1, 10 * n0)                   # y la malla no se dispara: solo crece en la zona


class ZonasDetalleAlturas(unittest.TestCase):
    """La pieza entera de un relieve (mapa de alturas) con una zona: cerrada, con más triángulos solo en la zona."""

    def test_relieve_con_zona(self):
        if apart(self):
            return
        rng = np.random.default_rng(1)
        x, y = rng.uniform(-100, 100, 300_000), rng.uniform(-60, 60, 300_000)
        xyz = np.c_[x, y, 0.8 * np.sin(2 * np.pi * x / 5) * np.sin(2 * np.pi * y / 5)]
        M = np.eye(4)
        M[:3, :3] /= 100
        zone = {'M': M.ravel().tolist(), 'poly': [[0.3, -0.5], [0.9, -0.5], [0.9, 0.5], [0.3, 0.5]]}
        p = relief.Params(mode='alturas', target='real', res=2.0, detail=[zone], detail_res=0.5, base=3)
        r0 = relief.reconstruct(xyz, relief.Params(mode='alturas', target='real', res=2.0, base=3), log=quiet)
        r = relief.reconstruct(xyz, p, log=quiet)
        self.assertEqual(r.bad_edges, 0)
        self.assertEqual(r.res, 2.0)                               # la general
        self.assertEqual(r.detail_res, 0.5)
        z = relief.zone_on_mesh(r)
        V = r.verts[r.tris]
        top = V[:, :, 2].min(1) > 3.5                              # la cara de arriba (sin base ni paredes)
        fz = z[r.tris].all(1) & top
        self.assertTrue(fz.any())
        area = np.linalg.norm(np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0]), axis=1) / 2
        a_in, a_out = np.median(area[fz]), np.median(area[top & ~z[r.tris].any(1)])
        self.assertLess(a_in, 0.5 * a_out)                         # triángulos más pequeños en la zona
        self.assertLess(len(r.tris), 15 * len(r0.tris))                    # y no en toda la pieza
        self.assertAlmostEqual(np.ptp(r.verts[:, 0]), np.ptp(r0.verts[:, 0]), delta=2.5)   # mismo tamaño


class TamanoReal(unittest.TestCase):
    """Destino «Tamaño real» (copia 1:1), «Profundidad máx.» automática y unidades dudosas."""

    def _fachada(self, L, n=60_000, seed=5):
        """Una fachada de L mm de ancho, con el terreno cayendo hacia atrás (la mitad de ella, a más de 250 mm)."""
        rng = np.random.default_rng(seed)
        x, y = rng.uniform(0, L, n), rng.uniform(0, L / 3, n)
        z = -0.3 * L * (x > L / 2) * rng.uniform(0, 1, n)      # media nube muy por detrás de la cara
        return np.c_[x, y, z].astype(np.float32)

    def test_profundidad_auto_crece_con_la_pieza(self):
        """auto (0): 250 mm en una pieza pequeña; en una de 37 m, la cuarta parte del lado (≈ 9 m)."""
        p = relief.Params()
        self.assertEqual(p.behind, 0.0)
        self.assertEqual(relief.behind_mm(p, 400), 250.0)
        self.assertAlmostEqual(relief.behind_mm(p, 37_000), 9_250.0)
        self.assertEqual(relief.behind_mm(relief.Params(behind=120), 37_000), 120.0)     # fijada, manda
        xyz = self._fachada(37_000)
        relief._project(xyz, relief.Params(view='frente'))
        auto = relief._project.dropped
        relief._project(xyz, relief.Params(view='frente', behind=250))
        self.assertLess(auto, relief._project.dropped)          # con 250 fijos se perdía mucho más

    def test_tamano_real_sin_impresora(self):
        """A tamaño real: escala 1 aunque haya lado mayor, sin cama, detalle del escaneo y tolerancia según el uso."""
        xyz = self._fachada(5_000)
        p = relief.Params(target='real', target_len=200, nozzle=0.4)
        st = relief.face_stats(xyz, p)
        pl = relief.print_plan(st, p)
        self.assertEqual(pl['scale'], 1.0)
        self.assertIsNone(pl['fits'])
        self.assertEqual(pl['why'], 'escaneo')
        self.assertAlmostEqual(pl['tol'], pl['res'] * relief.REAL_TOL['archivo'])
        medir = relief.print_plan(st, relief.Params(target='real', use='medir'))
        self.assertLess(medir['tol'], pl['tol'])
        imp = relief.print_plan(st, relief.Params(target_len=200))
        self.assertLess(imp['scale'], 1.0)                      # imprimir: lo de siempre

    def test_objeto_a_tamano_real_no_se_reescala(self):
        """Un objeto a tamaño real mide lo que la nube aunque el lado mayor diga 200 mm (salía de 200 mm), y se
        simplifica sin abrirse aunque mida metros (PyMeshLab dejaba aristas abiertas con coordenadas tan grandes)."""
        rng = np.random.default_rng(7)
        n = 60_000
        u, v = rng.uniform(0, 2 * np.pi, n), rng.uniform(-1, 1, n)
        xyz = np.c_[3000 * np.sqrt(1 - v ** 2) * np.cos(u), 3000 * np.sqrt(1 - v ** 2) * np.sin(u),
                    1500 * v].astype(np.float32)                       # un elipsoide de 6 × 6 × 3 m
        p = relief.Params(mode='objeto', target='real', target_len=200, denoise=0)
        r = relief.reconstruct(xyz, p)
        self.assertAlmostEqual(float(np.ptp(r.verts, 0).max()), 6000, delta=200)
        self.assertEqual(r.bad_edges, 0)
        self.assertLess(len(r.tris), 200_000)                         # se ha simplificado

    def test_malla_georreferenciada(self):
        """Una malla OBJ en UTM (440 000, 4 500 000 m): se resta el origen antes de pasar a float32, como en las
        nubes. Sin eso, a esas coordenadas float32 solo distingue medio metro y una copia a 1:1 sale en escalones."""
        import entrada
        g = np.mgrid[0:30, 0:30].reshape(2, -1).T * 0.05                   # rejilla de 5 cm, 1,5 m de lado
        V = np.c_[g + [440000.0, 4500000.0], 600 + 0.01 * np.sin(g[:, 0] * 20)]
        F = np.array([[i * 30 + j, i * 30 + j + 1, (i + 1) * 30 + j] for i in range(29) for j in range(29)])
        path = os.path.join(TMP, 'utm.obj')
        _write(path, ''.join(f'v {x:.4f} {y:.4f} {z:.4f}\n' for x, y, z in V)
               + ''.join(f'f {a + 1} {b + 1} {c + 1}\n' for a, b, c in F))
        P, _, _, kind = entrada.read_any(path)
        self.assertEqual(kind, 'malla')
        self.assertIsNotNone(entrada.read_any.origin)
        self.assertLess(float(np.abs(P).max()), 1e4)
        x = np.unique(np.round(P[:, 0], 3))
        self.assertGreater(len(x), 100)                 # sigue habiendo detalle de milímetros

    def test_malla_sin_alisado(self):
        """Los puntos sacados de una malla no tienen ruido de medida: no se alisan (a 1:1 el alisado se comía
        hasta 2 cm del detalle del acueducto)."""
        logs = []
        rng = np.random.default_rng(2)
        u, v = rng.uniform(0, 2 * np.pi, 30_000), rng.uniform(-1, 1, 30_000)
        xyz = np.c_[500 * np.sqrt(1 - v ** 2) * np.cos(u), 500 * np.sqrt(1 - v ** 2) * np.sin(u), 500 * v]
        relief.reconstruct(xyz.astype(np.float32), relief.Params(mode='objeto', target='real', simplify=100),
                           log=logs.append, mesh=True)
        self.assertTrue(any('puntos salen de una malla' in s for s in logs))

    def test_superficie_abierta(self):
        """Copia sin impresora como superficie abierta: de la lápida (mapa de alturas) y del bloque (Poisson) se queda
        solo lo escaneado: sin base ni paredes (la mitad de los triángulos fuera), ningún vértice a más de 2 celdas
        del escaneo, sin aristas de más de dos caras y sin volumen. Cerrada, como siempre."""
        from scipy.spatial import cKDTree
        for P, mode in ((ejemplos.lapida(120_000)[0], 'alturas'), (ejemplos.bloque()[0], 'bloque')):
            cl = relief.reconstruct(P, relief.Params(units='mm', target='real', mode=mode, simplify=100))
            op = relief.reconstruct(P, relief.Params(units='mm', target='real', mode=mode, simplify=100,
                                                     open_mesh=True))
            self.assertEqual((cl.bad_edges, cl.open_edges), (0, 0))
            self.assertEqual(op.bad_edges, 0)
            self.assertGreater(op.open_edges, 100)
            self.assertEqual(op.volume, 0.0)
            self.assertTrue(relief.mesh_state(op)[0])
            self.assertLess(len(op.tris), 0.9 * len(cl.tris))
            T = np.asarray(op.xform)
            d = cKDTree(P.astype(np.float64) @ T[:3, :3].T + T[:3, 3]).query(op.verts)[0]
            self.assertLess(float(d.max()), 2.01 * op.cell + 1e-6)
            self.assertGreater(float(np.percentile(cKDTree(P.astype(np.float64) @ T[:3, :3].T + T[:3, 3])
                                                   .query(cl.verts)[0], 95)), 3 * cl.cell)   # la cerrada tiene base
        prm = relief.Params(units='mm', mode='alturas', open_mesh=True)     # imprimiendo no cuenta
        self.assertEqual(relief.reconstruct(ejemplos.lapida(60_000)[0], prm).open_edges, 0)

    def test_copia_sin_impresora_a_escala(self):
        """Copia sin impresora a 1:10: la pieza mide la décima parte, la tolerancia va en mm de la pieza (la décima
        parte de la de 1:1) y la base proporcional se calcula con el tamaño de la pieza, no del original."""
        rng = np.random.default_rng(8)
        n = 40_000
        u, v = rng.uniform(0, 2 * np.pi, n), rng.uniform(-1, 1, n)
        xyz = np.c_[3000 * np.sqrt(1 - v ** 2) * np.cos(u), 3000 * np.sqrt(1 - v ** 2) * np.sin(u),
                    1500 * v].astype(np.float32)
        p1 = relief.Params(mode='objeto', target='real', denoise=0)
        p10 = relief.Params(mode='objeto', target='real', ratio=10, denoise=0)
        self.assertEqual(relief.piece_scale(p10, 6000), 0.1)
        self.assertAlmostEqual(relief.tolerance(p10, 40), relief.tolerance(p1, 40) / 10)
        self.assertLess(relief.real_base(6000 * 0.1), relief.real_base(6000))      # 5 mm frente a 30
        self.assertEqual(relief.scale_txt(0.1), '1:10')
        self.assertEqual(relief.scale_txt(2.0), '2:1')
        r = relief.reconstruct(xyz, p10)
        self.assertAlmostEqual(float(np.ptp(r.verts, 0).max()), 600, delta=20)
        self.assertEqual(r.bad_edges, 0)

    def test_ficha_de_una_copia(self):
        """La ficha PDF de una copia sin impresora (medidas en m, volumen sin PLA)."""
        import ficha
        rng = np.random.default_rng(9)
        u, v = rng.uniform(0, 2 * np.pi, 20_000), rng.uniform(-1, 1, 20_000)
        xyz = np.c_[2500 * np.sqrt(1 - v ** 2) * np.cos(u), 2500 * np.sqrt(1 - v ** 2) * np.sin(u),
                    800 * v].astype(np.float32)
        p = relief.Params(mode='objeto', target='real', denoise=0)
        r = relief.reconstruct(xyz, p)
        out = os.path.join(TMP, 'copia_ficha.pdf')
        ficha.make_pdf(out, r, p, {'path': 'sintetica.ply', 'points': len(xyz)}, '', lambda f: f)
        self.assertGreater(os.path.getsize(out), 10_000)

    def test_tamano_real_sin_realce(self):
        """El realce de lo pequeño no se aplica a una copia fiel."""
        h = np.random.default_rng(1).normal(0, 0.1, (40, 40))
        m = np.ones_like(h, bool)
        out, _ = relief._boost(h.copy(), None, m, 1.0, relief.Params(target='real', boost=2.0))
        self.assertTrue(np.array_equal(out, h))

    def test_unidades_dudosas(self):
        """Se pregunta si lo supuesto es dudoso: metros, una pieza diminuta o una malla de 20 a 5000 «mm»."""
        import entrada
        ask = entrada.units_doubt
        self.assertTrue(ask({'guessed': True, 'units': 'mm', 'kind': 'malla'}, [37, 15, 4]))      # el acueducto
        self.assertTrue(ask({'guessed': True, 'units': 'm', 'kind': 'nube'}, [3000, 1000, 500]))
        self.assertFalse(ask({'guessed': True, 'units': 'mm', 'kind': 'nube'}, [400, 300, 80]))   # una lápida
        self.assertFalse(ask({'guessed': False, 'units': 'mm', 'kind': 'malla'}, [37, 15, 4]))    # elegidas


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'rapidas':
        sys.argv.pop(1)
        suite = unittest.TestSuite()
        for c in (Formatos, Unidades, Limpieza, Escala, Acabado, Ficha, Desviacion, Corte, Formato3MF, Registro,
                  Recorte, Superficies, Ejemplos, Union, GuardarUnion, Novedades, Esquinas, Realce, MemoriaEscalaReal, TamanoReal, ZonasDetalle, Actualizar,
                  Coherencia):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(c))
        setUpModule()
        res = unittest.TextTestRunner(verbosity=2).run(suite)
        tearDownModule()
        sys.exit(0 if res.wasSuccessful() else 1)
    unittest.main(verbosity=2)
