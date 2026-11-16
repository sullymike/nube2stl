"""Recetas e impresoras: una sola fuente de datos para el asistente y para la ayuda.

El asistente aplica estos valores sobre los parámetros normales (los mismos de las pestañas) y la ayuda genera
con ellos la tabla «Valores de ejemplo para casos habituales» y la lista de camas. Para añadir un caso o una
impresora basta con añadirlo aquí.
"""

# ------------------------------------------------------------------ qué se ha escaneado
# El tipo de escaneo decide el método. Es la primera pregunta del asistente y el selector de arriba de los parámetros
# (dos filas: las caras —plana, curva, roca— y lo que tiene volumen —bloque, objeto, terreno—).
# 'params' son los que pone el botón; 'auto': los que se calculan con la nube (ver auto_value).
KIND_PARAMS = ('mode', 'ground', 'surface', 'detrend', 'interior')
KINDS = [
    dict(key='relieve', title='Una cara en relieve', short='Relieve',
         desc='Lápida, relieve, inscripción, moneda o sello: se escaneó la cara de delante. Se mira de frente y se '
              'cierra con una base por detrás.',
         line='Se mira de frente y se cierra con una base por detrás.',
         params=dict(mode='alturas', ground=False, surface='plano', detrend=0.0, interior='no'),
         hint='Fachadas: «Mirar desde» en Orientar.'),
    dict(key='curvo', title='Un relieve sobre una superficie curva', short='Curvo',
         desc='Miliario, fuste de columna, ara, sello cilíndrico o vasija decorada: la inscripción o el relieve está '
              'sobre un cilindro. Se ajusta el cilindro y la cara se desenrolla como una placa plana, con las medidas '
              'reales a lo largo del arco.',
         line='Se ajusta un cilindro y la cara se desenrolla como una placa plana.',
         params=dict(mode='alturas', ground=False, surface='cilindro', detrend=0.0, interior='no'),
         hint='Mira la cara desenrollada en la pestaña Orientación.'),
    dict(key='grabado', title='Grabados sobre una roca', short='Grabado',
         desc='Petroglifos, grafitos, icnitas o marcas de cantero sobre una roca de forma irregular: se quita la forma '
              'general de la roca y queda solo el grabado, sobre una placa, que se puede exagerar.',
         line='Se quita la forma de la roca y queda solo el grabado.',
         params=dict(mode='alturas', ground=False, surface='plano', detrend=200.0, interior='no'), auto=['detrend'],
         hint='En Pieza: «Quitar forma de la roca» y la exageración.'),
    dict(key='bloque', title='Una cara y los laterales del bloque', short='Bloque',
         desc='El escaneo de la cara trae también parte de los laterales (las caras vecinas). Se conservan y el '
              'bloque se cierra por detrás.',
         line='Conserva los laterales escaneados y cierra el bloque por detrás.',
         params=dict(mode='bloque', ground=False, surface='plano', detrend=0.0, interior='no')),
    dict(key='objeto', title='Un objeto por todos los lados', short='Objeto',
         desc='Estatua, vasija, capitel o pieza suelta escaneada alrededor. Se reconstruye en 3D y se corta por '
              'abajo para que apoye.',
         line='Reconstrucción 3D completa, cortada por abajo para que apoye.',
         params=dict(mode='objeto', ground=False, surface='plano', detrend=0.0, interior='no')),
    dict(key='fragmento', title='Una vasija a partir de un fragmento', short='Fragmento',
         desc='Un fragmento de cerámica a torno (borde, panza o fondo): se busca el eje de giro y se reconstruye la '
              'vasija entera, en la altura que cubre el fragmento. El perfil se puede guardar para el dibujo '
              '(Archivo › Guardar el perfil de la vasija).',
         line='Se busca el eje de giro y se reconstruye la vasija entera.',
         params=dict(mode='objeto', ground=False, surface='revolucion', detrend=0.0, interior='no'),
         hint='Si sale boca abajo: «Arriba» = −z en Orientar.'),
    dict(key='interior', title='Un interior: sala, cueva, cisterna', short='Interior',
         desc='Escaneado desde dentro (una sala, una cueva, una cisterna, una tumba): como maqueta en corte, sin el '
              'techo y dentro de un bloque, como una casa de muñecas; o en negativo, el hueco como un sólido.',
         line='Maqueta en corte (sin techo, dentro de un bloque) o el hueco en negativo.',
         params=dict(mode='objeto', ground=False, surface='plano', detrend=0.0, interior='corte'),
         hint='En Pieza: «Interior» y «Altura del corte».'),
    dict(key='terreno', title='Un terreno, una ruina o un edificio', short='Terreno',
         desc='Fotogrametría desde el aire o desde el suelo (yacimiento, puente, acueducto): solo está la superficie '
              'de arriba y de los lados. Se hace una maqueta cerrada con paredes hasta una base plana, o una fachada '
              'en relieve.',
         line='Maqueta 3D cerrada con paredes hasta una base plana.',
         params=dict(mode='objeto', ground=True, surface='plano', detrend=0.0, interior='no'),
         hint='¿Una fachada en relieve? Elige «Relieve».'),
]


def kind_of(mode, ground=False, surface='plano', detrend=0.0, interior='no'):
    """Tipo de escaneo que corresponde a un método, a «Abierto por abajo», a la superficie, a quitar la forma y a
    si es un interior."""
    if mode == 'objeto' and interior not in ('no', '', None):
        return 'interior'
    if mode == 'objeto':
        if surface == 'revolucion' and not ground:
            return 'fragmento'
        return 'terreno' if ground else 'objeto'
    if mode == 'bloque':
        return 'bloque'
    if surface in ('cilindro', 'cono', 'revolucion'):
        return 'curvo'
    if mode == 'alturas' and detrend > 0:
        return 'grabado'
    return 'relieve'


def kind_of_params(p):
    """kind_of de unos Params."""
    return kind_of(*(getattr(p, k) for k in KIND_PARAMS))


def auto_value(name, stats):
    """Valor de un parámetro que depende de la nube (stats: relief.face_stats; None si no hay nube)."""
    if name == 'detrend':
        import superficie
        return superficie.detrend_default(stats['length']) if stats else 200.0
    raise KeyError(name)


# ------------------------------------------------------------------ qué se quiere hacer
# Valores comunes a todos los objetivos: al cambiar de objetivo se parte de aquí (y no de lo que dejó el anterior).
PURPOSE_BASE = dict(shape='contorno', negative=False, frame=0.0, base=3.0, mode='alturas', z_scale=1.0, ground=False,
                    view='auto', surface='plano', detrend=0.0, interior='no', corner=False,
                    boost=1.0)

# 'kind': a qué tipo de escaneo pertenece. Si un tipo tiene un solo objetivo, el asistente no lo pregunta.
PURPOSES = [
    dict(key='copia', kind='relieve', title='Copia de la piedra',
         desc='Reproducción fiel de la cara escaneada, con su contorno real. Lo habitual para exposición, '
              'docencia o archivo.',
         params={}, ask=['label'],
         why='Los valores por defecto: detalle de media boquilla y ficheros de 1–4 MB.'),
    dict(key='placa', kind='relieve', title='Placa para colgar',
         desc='Placa rectangular con un marco recto alrededor y una base gruesa para atornillar o pegar un colgador.',
         params=dict(shape='rectangulo', frame=8.0, base=4.0), ask=['frame', 'base', 'shape', 'label'],
         why='Borde limpio y recto, y grosor para atornillar o pegar un colgador.'),
    dict(key='molde', kind='relieve', title='Molde o sello',
         desc='Relieve invertido (negativo) para sacar copias en escayola, silicona o arcilla, o para usarlo de sello.',
         params=dict(negative=True, shape='rectangulo', frame=10.0, base=5.0), ask=['frame', 'base', 'mirror'],
         why='El marco da un borde perimetral continuo y la base, un fondo rígido.'),
    dict(key='desenrollar', kind='curvo', title='Cara desenrollada',
         desc='La inscripción o el relieve del miliario, la columna o el ara como una placa plana: se lee entera de '
              'un vistazo y se imprime sin soportes. Si el escaneo da la vuelta entera, se corta por donde hay menos '
              'puntos; gira la pieza en Orientar si la costura cae en mal sitio.',
         params=dict(surface='cilindro'), ask=['surface', 'shape', 'frame', 'base', 'label'],
         why='Desenrollada, la cara cabe en la cama de plano y las medidas a lo largo del arco son las reales.'),
    dict(key='grabado', kind='grabado', title='Calco del grabado',
         desc='La roca queda plana y solo se marca el grabado, exagerado para que se vea y se pueda tocar: para '
              'documentar, para exposición o para lectura táctil. «Quitar forma de la roca» debe ser bastante mayor '
              'que el trazo más ancho del grabado; se propone una cuarta parte del lado mayor.',
         params=dict(detrend=200.0, z_scale=3.0), auto=['detrend'],
         ask=['detrend', 'z_scale', 'negative', 'frame', 'base', 'label'],
         why='Sin la forma de la roca, unos pocos mm de grabado llenan todo el relieve de la pieza.'),
    dict(key='bloque', kind='bloque', title='Bloque con sus laterales',
         desc='El escaneo de una cara trae también parte de los laterales del bloque (las caras vecinas). Se '
              'conservan como superficie real y el bloque se cierra por detrás a la profundidad que elijas.',
         params=dict(mode='bloque'), ask=['behind', 'wall', 'drain', 'label'],
         why='Recupera los laterales escaneados, que el mapa de alturas descarta, sin tener que escanear el bloque entero.'),
    dict(key='esquina', kind='bloque', title='Esquina con dos caras decoradas',
         desc='Dos caras con relieve que se juntan en una arista (la esquina de un friso, un sillar de esquina con '
              'inscripción, un ara con dos caras labradas). Cada cara se trata con su propio mapa de alturas, con '
              'el mismo detalle; la pieza es el bloque que queda detrás de las dos. Si las caras están en ficheros '
              'distintos, únelos antes (Archivo › Unir otra nube…, «Por la arista»).',
         params=dict(mode='bloque', corner=True), ask=['fill', 'gauss', 'edges', 'wall', 'drain', 'label'],
         why='Con «Bloque» la segunda cara solo pasa por Poisson y sale más blanda que la principal.'),
    dict(key='objeto', kind='objeto', title='Objeto completo (estatua, vasija, capitel)',
         desc='Escaneado por todos los lados, no solo una cara. Se reconstruye en 3D y se corta por abajo para que '
              'apoye en la cama. Elige qué lado va arriba mirando la vista «Orientación».',
         params=dict(mode='objeto'), ask=['up', 'cut', 'wall', 'drain', 'label'],
         why='El objeto se reconstruye entero, con sus huecos y asas, y con una base plana para imprimirlo de pie.'),
    dict(key='vasija', kind='fragmento', title='Vasija entera',
         desc='Del fragmento se saca el eje de giro y la sección de la pared, y se gira 360°: la vasija en la altura '
              'que cubre el fragmento (con fondo si el fragmento llega a él). Cuanto más abarque el fragmento '
              'alrededor, más fiable el diámetro. Para el dibujo: Archivo › Guardar el perfil de la vasija (SVG).',
         params=dict(mode='objeto', surface='revolucion'), ask=['up', 'cut', 'wall', 'label'],
         why='Un fragmento basta para conocer la forma de una pieza a torno: todas sus secciones son iguales.'),
    dict(key='corte', kind='interior', title='Maqueta en corte',
         desc='Se quita el techo por encima de una altura y la sala queda como un hueco abierto por arriba dentro de '
              'un bloque: se ve la planta, los muros, los nichos y el suelo. «Altura del corte» decide por dónde se '
              'corta (por defecto, a tres cuartos del alto).',
         params=dict(mode='objeto', interior='corte'), ask=['ceiling', 'cut', 'label'],
         why='Sin el techo, el interior se ve y se toca desde arriba, y la pieza se imprime sin soportes.'),
    dict(key='negativo', kind='interior', title='El hueco en negativo',
         desc='El volumen vacío de la sala o la cueva como un sólido: la forma del «aire». Útil para entender la '
              'capacidad de una cisterna, la forma de una cueva o de una tumba.',
         params=dict(mode='objeto', interior='negativo'), ask=['cut', 'label'],
         why='La forma de un espacio se entiende mejor tocándola como un objeto.'),
    dict(key='maqueta', kind='terreno', title='Maqueta en 3D',
         desc='El terreno, la ruina o el monumento entero, en 3D, con los arcos y vanos pasantes, cerrado con paredes '
              'hasta una base plana. Si sobra terreno alrededor, recórtalo en la vista de la nube.',
         params=dict(mode='objeto', ground=True, base=3.0), ask=['units', 'base', 'label'],
         why='Sin «Abierto por abajo», Poisson inventaría una burbuja para cerrar lo que no se escaneó.'),
    dict(key='fachada', kind='terreno', title='Fachada en relieve',
         desc='Una fachada, un muro o la arcada de un acueducto como placa en relieve, vista de frente aunque el '
              'escaneo se hiciera desde el aire. Elige el lado en «Mirar desde» y recorta en la vista de la nube lo '
              'que no sea esa fachada. En la ventana quedará como «Relieve» mirado desde ese lado.',
         params=dict(mode='alturas', view='frente', behind=3000.0), ask=['units', 'view', 'behind', 'base', 'frame'],
         why='Se mira desde el lado elegido en lugar de desde el plano principal (que en un escaneo aéreo es el suelo).'),
]


def purposes_of(kind):
    return [p for p in PURPOSES if p['kind'] == kind]


# ------------------------------------------------------------------ tamaños rápidos (paso Tamaño del asistente)
# 'fdm': solo se aplica con impresoras de filamento; 'relief': solo en los modos con relieve (alturas, 3D).
SIZES = [
    dict(key='recuerdo', title='Recuerdo · 60 mm', desc='Llavero, imán o recuerdo de un yacimiento.',
         params=dict(target_len=60.0), relief=dict(base=2.0, z_scale=2.0), fdm=dict(layer=0.12),
         why='A esa escala el relieve ocuparía muy pocas capas sin exagerar.'),
    dict(key='normal', title='Normal · 150 mm', desc='Pieza de mesa o de vitrina.',
         params=dict(target_len=150.0), why='Un tamaño que cabe en cualquier cama.'),
    dict(key='grande', title='Exposición · 400 mm', desc='Se imprime con boquilla y capa gruesas para que no tarde '
                                                          'días. Si no cabe, divídela en el laminador.',
         params=dict(target_len=400.0), fdm=dict(nozzle=0.6, layer=0.3, quality='normal'),
         why='A esa escala el detalle sigue siendo bueno y se imprime mucho más rápido.'),
]


def size_params(it, mode, fdm):
    """Valores que pone un tamaño rápido según el método y la impresora."""
    out = dict(it['params'])
    if mode in ('alturas', '3d'):
        out.update(it.get('relief', {}))
    if fdm:
        out.update(it.get('fdm', {}))
    return out


# ------------------------------------------------------------------ estado del escaneo y del relieve
# 'modes': solo se ofrece si el objetivo elegido usa uno de esos métodos.
CONDITIONS = [
    dict(key='socavados', title='Tiene partes que vuelan (socavados)', modes=('alturas', '3d'),
         desc='Altorrelieve con figuras despegadas del fondo, brazos o pliegues que dejan huecos por debajo. El mapa '
              'de alturas los rellenaría; el método 3D los conserva, pero tarda minutos y necesita más memoria.',
         params=dict(mode='3d'),
         why='Conserva los socavados que el mapa de alturas rellena.'),
    dict(key='letras', title='Tiene letras o detalles finos',
         desc='Inscripciones, trazos incisos o motivos pequeños que no deben redondearse.',
         params=dict(quality='maxima', smooth=1, denoise=2.0),
         note='Si tu impresora lo permite, usa además boquilla de 0,25 mm y capa de 0,1 mm.',
         why='Menos suavizado para no redondear los trazos y una simplificación más fiel.'),
    dict(key='gastado', title='El relieve está gastado o es casi plano',
         desc='Relieve erosionado o de poca profundidad, que impreso apenas se notaría.',
         params=dict(z_scale=2.0, smooth=3, floor_pct=10.0),
         why='Hace visible el relieve en pocas capas; el recorte evita que la base crezca.'),
    dict(key='ruido', title='El escaneo tiene ruido',
         desc='Fotogrametría con móvil, escáner de mano o superficie brillante: «grano», pinchos o puntos sueltos.',
         params=dict(despike=3.0, smooth=5, front_pct=80.0, gauss=0.5, denoise=5.0),
         why='Elimina puntos sueltos y la textura falsa del ruido.'),
]

# ------------------------------------------------------------------ impresoras
# (nombre, tecnología, cama X, cama Y, boquilla o tamaño de detalle, capa típica)
PRINTERS = [
    ('Creality Ender 3 / K1 / K1C', 'fdm', 220, 220, 0.4, 0.2),
    ('Prusa MK4 / MK4S', 'fdm', 250, 210, 0.4, 0.2),
    ('Prusa CORE One', 'fdm', 250, 220, 0.4, 0.2),
    ('Prusa MINI+', 'fdm', 180, 180, 0.4, 0.2),
    ('Bambu Lab A1 mini', 'fdm', 180, 180, 0.4, 0.2),
    ('Bambu Lab A1 / P1S / X1C', 'fdm', 256, 256, 0.4, 0.2),
    ('Elegoo Neptune 4 Pro', 'fdm', 225, 225, 0.4, 0.2),
    ('Anycubic Kobra 2', 'fdm', 220, 220, 0.4, 0.2),
    ('Resina MSLA (genérica)', 'resina', 130, 80, 0.05, 0.05),
]
RESIN_RECIPE = dict(title='Resina (SLA/MSLA)', params=dict(nozzle=0.05, layer=0.05, quality='alta'),
                    why='La resolución quedará limitada por el escaneo; el fichero será mayor.')


def keys(items):
    """Todos los parámetros que toca una lista de recetas."""
    return sorted({k for it in items for k in it['params']})


# ------------------------------------------------------------------ texto para la ayuda
def fmt_value(v):
    if isinstance(v, bool):
        return 'sí' if v else 'no'
    if isinstance(v, float):
        return f'{v:g}'.replace('.', ',')
    return str(v)


def help_table(label):
    """Tabla HTML de recetas; label(nombre) da la etiqueta del parámetro (la misma que en las pestañas)."""
    rows = []
    for it in PURPOSES + CONDITIONS + [RESIN_RECIPE]:
        vals = ' · '.join(f'{label(k)} ' + ('¼ del lado mayor' if k in it.get('auto', ()) else fmt_value(v))
                          for k, v in it['params'].items()) or 'Valores por defecto'
        if it.get('note'):
            vals += f'<br><i>{it["note"]}</i>'
        rows.append(f'<tr><td>{it["title"]}</td><td>{vals}</td><td>{it["why"]}</td></tr>')
    for it in SIZES:
        vals = ' · '.join(f'{label(k)} {fmt_value(v)}' for k, v in it['params'].items())
        if it.get('relief'):
            vals += '; en relieves, ' + ' · '.join(f'{label(k)} {fmt_value(v)}' for k, v in it['relief'].items())
        if it.get('fdm'):
            vals += '; con filamento, ' + ' · '.join(f'{label(k)} {fmt_value(v)}' for k, v in it['fdm'].items())
        rows.append(f'<tr><td>Tamaño: {it["title"]}</td><td>{vals}</td><td>{it["why"]}</td></tr>')
    return '<table>\n<tr><th>Caso</th><th>Valores</th><th>Por qué</th></tr>\n' + '\n'.join(rows) + '\n</table>'


def printers_text():
    return ' · '.join(f'{n}: {x} × {y}' for n, t, x, y, _, _ in PRINTERS if t == 'fdm')
