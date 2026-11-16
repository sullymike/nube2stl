"""Versión, autoría e historial de cambios de nube2stl."""

__version__ = '0.22.0'
AUTHOR = 'Jorge Sánchez'
YEAR = '2026'
# repositorio de GitHub donde se publican las versiones («usuario/nube2stl»); '' = aún sin publicar
GITHUB_REPO = 'sullymike/nube2stl'

# (versión, fecha, cambios) — la más reciente primero
HISTORY = [
    ('0.22.0', 'noviembre 2026', [
        'Copia a tamaño real: dos botones arriba del panel Parámetros, «Imprimir una réplica» y «Copia a tamaño '
        'real». La segunda hace una copia 1:1 para archivo, fresado, gran formato o medir: sin impresora ni realce, '
        'detalle del escaneo en mm reales, simplificación según el uso (nuevo parámetro «Uso»), base proporcional, y '
        'en Tamaño la medida real y hasta dónde llega Poisson. Al guardar propone el formato del uso',
        'Asistente: empieza preguntando «¿Para qué es?»; la copia a tamaño real tiene su propio paso (unidades, uso '
        'y detalle) en lugar de Impresora y Tamaño',
        'Unidades dudosas: si las supuestas pueden estar mal (una malla de fotogrametría de 37 «mm», una pieza de '
        'menos de 5 cm, algo tomado por metros), al abrir la nube se pregunta, diciendo cuánto mediría en cada unidad',
        '«Profundidad máx.» automática (nuevo valor por defecto): 250 mm, o la cuarta parte del lado mayor de la cara '
        'en piezas grandes. Con 250 fijos una fachada de 37 m perdía la mitad de la nube',
    ]),
    ('0.21.1', 'noviembre 2026', [
        'Piezas enormes a escala real (un acueducto de 37 m): «Abierto por abajo» ya no llena la memoria. Sus paredes '
        'y su base no pasan del doble de los puntos de la nube (antes, con celda de 5 mm, 106 millones de puntos y '
        '18 GB; ahora 1,8 GB)',
        'Al cargar unos ajustes con otras unidades se vuelve a leer la nube: antes la ventana seguía midiéndola con '
        'las de antes (un acueducto en metros tomado por 36 mm) y la estimación de memoria salía mil veces corta',
        'La estimación de memoria cuenta la celda a la que de verdad llega Poisson (en piezas muy grandes, más gruesa '
        'que la resolución pedida)',
        'La reconstrucción se vigila mientras trabaja: si el equipo se queda sin memoria se para a tiempo (sin '
        'atascarlo) y el aviso dice en qué paso iba y cuánto gastaba; los ajustes del intento quedan en '
        'ultimo_fallo.json, en la carpeta de configuración',
        'Asistente: las descripciones de dos líneas ya no se montan sobre la opción siguiente (la página se desplaza '
        'si no cabe) y la ventana es más alta si la pantalla lo permite',
    ]),
    ('0.21.0', 'noviembre 2026', [
        'Ventana más despejada: los parámetros ocupan todo el alto (el fichero pasa a la derecha, cargar/guardar '
        'ajustes van en el botón «Ajustes» y la propuesta del tipo de pieza en una sola línea); cabe todo sin barra '
        'de desplazamiento en una pantalla de 1080 líneas salvo Avanzado',
        'Menú Ver: Parámetros, Fichero, Resultado y Registro son paneles que se ponen a la izquierda o a la '
        'derecha, en pestañas, sueltos en otra pantalla o se cierran; la disposición se recuerda',
        'Buscar actualizaciones: «Cerrar» cierra sin más (antes enseñaba «Actualizado… Reiniciar»); cerrar mientras '
        'busca ya no puede hacer caer el programa, y mientras instala espera a que acabe',
    ]),
    ('0.20.0', 'noviembre 2026', [
        '«Realzar lo pequeño» en todos los tipos de pieza: en los hechos con Poisson (objeto, fragmento, interior, '
        'terreno, bloque, socavados) se aplica a la malla, siguiendo las curvas de la pieza y sin tocar aristas vivas',
        'El realce mide cada detalle respecto a la mediana de su entorno: una letra gastada junto a una letra grande '
        'o una moldura ya se realza (antes el canto grande lo apagaba todo alrededor)',
        '«Realzar desde», «hasta» y «anchura» en mm de la piedra (lo que se mide en la nube): no cambian al cambiar el '
        'tamaño de la pieza; «auto» por defecto, y una nota dice cuánto es cada uno en la pieza',
        'Vista Orientación › «Detalle de la pieza»: el mapa de alturas de verdad con el realce, sin generar la malla '
        '(el realce se ve al momento), y «Capas» para ver lo que dejará la impresora',
        'Unir nubes: los puntos de la arista se marcan en orden (abajo y luego arriba), así nunca se da la vuelta; '
        'la unión se puede guardar en un solo PLY, y «Guardar ajustes» recuerda la nube para volver a abrirlo todo; '
        'la alineación reutiliza lo ya calculado y es unas tres veces más rápida',
        'Los parámetros que no cuentan para el tipo de pieza se quitan sin dejar huecos, y la barra de desplazamiento '
        'solo sale si hace falta',
        'Publicado en GitHub: Ayuda › «Buscar actualizaciones…» enseña las novedades de la última versión y la instala '
        '(git pull, o copiando los ficheros nuevos y guardando los de antes); al abrir avisa en la barra de estado si '
        'hay una nueva (como mucho una vez al día). En la consola: --actualizar',
    ]),
    ('0.18.0', 'noviembre 2026', [
        '«Por la arista» aprovecha una franja de la otra cara si un escaneo dobla un poco la esquina: mide el '
        'ángulo en ella (el escrito no cuenta), sabe de qué lado está la piedra y afina la unión con la zona común, '
        'también a lo largo de la arista (de 1 mm a 0,05–0,16 mm en las pruebas)',
        '«Realzar lo pequeño» (modo alturas): solo los detalles de poco relieve, hacia fuera y hacia dentro, con '
        'un aumento que baja según crecen (×2 a 0,1 mm, nada a 0,5 mm, ajustable) y sin tocar las formas grandes',
        'Ejemplos nuevos: lápida gastada (letras de menos de 1 mm); la esquina del friso lleva dentículos',
    ]),
    ('0.17.0', 'noviembre 2026', [
        'Unir dos caras escaneadas por separado que solo comparten una arista («Por la arista»): se marcan los '
        'extremos de la arista en cada nube y el ángulo entre las caras; el plano y el borde de cada cara se ajustan '
        'solos. «Por puntos» avisa si los puntos marcados están en línea',
        'Esquinas (tipo Bloque, «Esquina (dos caras)»): cada cara con su propio mapa de alturas y todo su '
        'tratamiento; la pieza es el bloque detrás de las dos, cerrada siempre (tetraedros en marcha)',
        'Al abrir una nube con dos caras grandes en ángulo se propone la esquina, y «Plano de la cara» avisa de que '
        '«nube» la miraría desde la esquina. Ejemplo nuevo: esquina de un friso',
        'Plano de la cara «cara» más fiable: la franja de búsqueda no depende de cómo esté girada la nube y puntúa '
        'lo cerca que están los puntos (MSAC)',
    ]),
    ('0.16.0', 'noviembre 2026', [
        'Tipo «Fragmento»: la vasija entera a partir de un fragmento de cerámica a torno (eje de revolución, sección '
        'y giro), con el perfil para el dibujo arqueológico en SVG',
        'Superficies cono y revolución en «Curvo»: desarrollo exacto del cono y desenrollado de vasijas abombadas a '
        'lo largo de su perfil',
        'Piezas de dos caras (monedas, medallas, tablillas): Archivo › Añadir el reverso; se encaja por el contorno',
        'Tipo «Interior» (salas, cuevas, cisternas): maqueta en corte sin techo o el hueco en negativo',
        'Comparar dos escaneos de la misma pieza (deterioro): alineación robusta, mapa de pérdidas y ganancias, '
        'informe PDF; también comparar.py en la consola',
        'LiDAR por clases (LAS/LAZ): elegir suelo, edificios…; sin clases, el suelo se calcula',
        'Calidad: relleno de huecos «suave» (biarmónico), suavizado que respeta las aristas, plano de la cara robusto '
        'o por tres puntos, aviso de valores de Avanzado cambiados y profundidad del grabado en Orientar',
    ]),
    ('0.15.0', 'noviembre 2026', [
        'Unir varias nubes en una pieza (Archivo › Unir otra nube…): ya alineadas (mismas coordenadas, también '
        'georreferenciadas), por pares de puntos marcados con clic y afinadas con la zona común (ICP), o automática con '
        'solape (FPFH + RANSAC + ICP, comprobada al revés). La ventana mide el solape y la separación y avisa de las '
        'alineaciones dudosas (piezas lisas que resbalan, piezas simétricas)',
        'Sin puntos repetidos en la zona común; la unión se guarda en los ajustes (Repetir, registro, consola) y se '
        'puede quitar. Consola: --unir para nubes ya alineadas',
        'Los visores 3D de varias ventanas comparten el contexto OpenGL',
    ]),
    ('0.14.0', 'noviembre 2026', [
        'Tipo «Curvo»: relieves e inscripciones sobre un cilindro (miliarios, columnas, aras). Se ajusta el cilindro '
        'y la cara se desenrolla como una placa plana, con las medidas reales a lo largo del arco',
        'Tipo «Grabado»: grabados sobre roca irregular (petroglifos, grafitos, icnitas). Se quita la forma general de '
        'la roca («Quitar forma de la roca») y queda solo el grabado; el cálculo de impresión ya lo tiene en cuenta',
        'Al abrir una nube se propone el tipo de pieza según su forma, con el motivo; si ya lo habías elegido, se '
        'ofrece como enlace',
        'Barra de pasos encima de las vistas (abrir, revisar, tipo y tamaño, orientar, vista previa, generar, '
        'guardar): dónde estás, lo hecho y lo pendiente; al cambiar parámetros, avisa de que hay que volver a generar',
        'Medir en el visor: distancia entre dos puntos en la nube (mm reales) y en la malla (mm de la pieza y reales)',
        'Nubes de ejemplo de cada tipo (Ayuda › Abrir un ejemplo y ejemplos.py) para aprender y enseñar; las mismas '
        'que usan las pruebas',
    ]),
    ('0.13.1', 'noviembre 2026', [
        'Ayuda: los parámetros van en el orden de la ventana (tipo de pieza y pestaña por pestaña), atajos de teclado, '
        'la casilla «Suave» del visor, ejemplos de consola para terrenos y fachadas, y la introducción y las mejoras '
        'al día',
        'La ficha PDF muestra cuántos recortes y grupos conservados hay (antes, la lista en bruto) y escribe los '
        'ajustes de forma compacta',
        'Textos de la portada y de los avisos actualizados a las pestañas nuevas',
        'Pruebas de coherencia entre la ventana, la ayuda, las recetas y la ficha',
    ]),
    ('0.13.0', 'noviembre 2026', [
        'Botones «¿Qué has escaneado?» encima de los parámetros (relieve, bloque, objeto, terreno): eligen el método '
        'y dejan a la vista solo los parámetros que cuentan con él; «Tiene partes que vuelan» pasa al método 3D',
        'Pestañas de parámetros reordenadas: Tamaño, Pieza, Orientar, Acabados y Avanzado (impresora, malla y '
        'suavizados, que lo normal es no tocar); línea con la impresora en la pestaña Tamaño',
        'Asistente: primero qué has escaneado y después qué quieres hacer, solo si hay donde elegir; recuerdo y pieza '
        'de exposición pasan a atajos del paso Tamaño, y el altorrelieve a «Estado del escaneo»',
        'La lista de impresoras del asistente ya no ensancha la ventana con nombres largos de los laminadores',
    ]),
    ('0.12.0', 'noviembre 2026', [
        'Recortar la nube en la propia vista (rectángulo, dejar o quitar lo de dentro, varios seguidos, deshacer); '
        'los recortes se guardan con los ajustes y en el registro',
        'Modo objeto «Abierto por abajo» para terrenos, ruinas y monumentos sacados por fotogrametría: paredes y base '
        'sintéticas en lugar de la burbuja que inventaba Poisson; los arcos y vanos siguen pasantes',
        'Aviso cuando Poisson inventa superficie lejos del escaneo',
        '«Mirar desde» (frente, arriba, lados…) en los modos alturas, 3D y bloque: fachadas en relieve',
        'Avisos de «Profundidad máx.» cuando deja fuera muchos puntos y de unidades en mallas de fotogrametría',
        'Recetas nuevas: maqueta de terreno o monumento y fachada en relieve',
    ]),
    ('0.11.1', 'noviembre 2026', [
        'Limpieza a la vista: la nube se abre entera y los grupos de puntos sueltos se pintan en rojo; activar o '
        'desactivar «Quitar grupos sueltos» se aplica al momento sin volver a leer el fichero',
        'Clic en un grupo suelto de la vista de la nube para conservarlo (verde) o volver a quitarlo',
        'Enlace «ver el mapa de desviación» en el resultado',
    ]),
    ('0.11.0', 'noviembre 2026', [
        'Mapa de desviación: cuánto se separa la pieza del escaneo en cada punto (color en el visor, resumen en el '
        'resultado y página propia en la ficha PDF)',
        'Vista de corte en el visor de la malla (eje y posición del plano, contorno en rojo)',
        'Exportación a 3MF (milímetros y metadatos dentro); el número de la pieza, la escala y la versión se graban '
        'también en la cabecera del STL',
        'Registro de piezas: cada pieza guardada se anota con un número, sus ajustes, la nube de origen y una '
        'miniatura; ventana para buscar, anotar y repetir; carpeta compartible entre ordenadores y registro.csv',
        'Normales de las mallas de entrada comprobadas (se invierten o se recalculan si vienen mal)',
        'Limpieza al abrir ajustada a la densidad de la nube (ya no se come partes de nubes poco densas)',
        'Aviso cuando las unidades se han supuesto o la pieza sale diminuta',
        'El hueco se resta con booleana si se acerca a la piel; peso de las piezas huecas bien rotulado',
        'Las nubes de texto georreferenciadas (XYZ/CSV) ya no pierden precisión',
    ]),
    ('0.10.0', 'noviembre 2026', [
        'Abre nubes LAS/LAZ, E57, XYZ/PTS/TXT/CSV y mallas OBJ/STL (y PLY con caras), además de PLY',
        'Unidades de la nube (auto, mm, cm, m, pulgadas): todo se pasa a mm al abrir; escala exacta 1:N',
        'Limpieza al abrir: quita los grupos de puntos separados de la pieza antes de nada',
        'Vista previa rápida (resolución 3 veces más gruesa) antes de la generación definitiva',
        'Pieza hueca con grosor de pared y agujeros de drenaje; rótulo grabado en la base',
        'Ficha PDF de la pieza (vistas, medidas, nube de origen, parámetros, registro y ajustes)',
        'Lotes: elegir ficheros o carpeta, ficha PDF por pieza y resumen lote_resumen.csv',
        'Pruebas automáticas (pruebas.py) con datos sintéticos',
    ]),
    ('0.9.9', 'noviembre 2026', [
        'Alisado (3D) en los modos bloque y objeto: quita el ruido de medida antes de Poisson; la superficie ya no '
        'sale granulada y la malla necesita la mitad de triángulos',
        'Visor de la malla: sombreado suave (casilla «Suave», marcada en bloque y objeto); los objetos altos se '
        'ven casi de lado',
        'Profundidad máx.: debajo del campo y en el asistente, las medidas de la nube (hasta dónde llegan los '
        'puntos por detrás de la cara) y el grosor del bloque que saldría',
        'Botón «Limpiar» en el registro; las pestañas de parámetros se desplazan si no caben',
    ]),
    ('0.9.8', 'noviembre 2026', [
        'Método «bloque»: la cara del relieve y los laterales del bloque que trae el escaneo, como superficie real, '
        'cerrado con una trasera plana a la profundidad máxima y paredes verticales donde no hay lateral',
        'Propósito «Bloque con sus laterales» en el asistente',
    ]),
    ('0.9.7', 'noviembre 2026', [
        'Método «objeto» para piezas escaneadas por todos los lados (estatuas, vasijas, capiteles): reconstrucción 3D '
        'completa, sin cara frontal ni mapa de alturas',
        'Normales orientadas hacia fuera por propagación y visibilidad, o las del propio PLY si las trae',
        'Base plana para imprimir de pie (Corte de la base) y elección del eje que va arriba, con vista de frente '
        'en tiempo real',
        'Propósito «Objeto completo» en el asistente y generador de una nube sintética de prueba '
        '(generar_objeto_sintetico.py)',
    ]),
    ('0.9.6', 'noviembre 2026', [
        'Ejecutable para Windows (y Linux) con PyInstaller: construir_windows.bat / construir_linux.sh',
        'Línea de comandos en su propio módulo (consola.py) y ejecutable de consola nube2stl-consola',
        'Textos del registro en UTF-8 también en Windows',
    ]),
    ('0.9.5', 'noviembre 2026', [
        'Pestaña «Orientación»: la pieza como saldrá (giro, espejo, forma, marco) a su tamaño en mm, en tiempo real, '
        'con guías en mm y ejes centrales',
        'Cuadrar el giro con una línea de referencia dibujada sobre la vista (queda horizontal o vertical)',
        'Alinear con las líneas talladas del relieve, enderezar por contorno, giros de ±90°, ±1°, ±0,1° y deshacer',
        'El giro admite centésimas de grado',
    ]),
    ('0.9.4', 'noviembre 2026', [
        'Visor 3D nuevo para nube y malla: doble clic fija el centro de giro, zoom hacia el cursor, desplazar con '
        'botón derecho, central o Mayús',
        'Vistas rápidas (inicial, encuadrar, de frente, rasante, perfil) y teclado (flechas, +/−, R, F)',
        'Malla: luz rasante que sigue a la cámara, luz frontal o fija, y aristas de los triángulos',
        'Nube: hasta un millón de puntos y tamaño de punto ajustable',
    ]),
    ('0.9.3', 'noviembre 2026', [
        'Aviso de memoria antes de generar, en una ventana que hay que aceptar, con los parámetros que bajan el '
        'consumo, valores sugeridos y la estimación al momento',
        'La misma ventana si el sistema corta la generación por falta de memoria, para reintentar',
        'Modelo de memoria medido con los ficheros de ejemplo (±6 %)',
    ]),
    ('0.9.2', 'noviembre 2026', [
        'El asistente carga las impresoras que ya tienes en OrcaSlicer, Bambu Studio, PrusaSlicer, SuperSlicer y Cura',
        'Impresoras propias: «Guardar como mía…» y «Quitar»',
    ]),
    ('0.9.1', 'noviembre 2026', [
        'Logo propio: icono de la aplicación, cabecera, Acerca de y portada de arranque',
        'Portada de arranque con nombre, versión y autor',
    ]),
    ('0.9.0', 'noviembre 2026', [
        'Asistente opcional: objetivo, impresora, escaneo y tamaño, con explicaciones y preguntas que cambian '
        'según lo elegido; rellena las mismas pestañas',
        'Recetas e impresoras en un único fichero (recetas.py) que usan el asistente y la ayuda',
        'El cálculo de impresión estima la altura del relieve y las capas antes de generar',
    ]),
    ('0.8.1', 'noviembre 2026', [
        'Detección de procesadores que respeta afinidad, contenedores y cuotas de CPU',
        'Núcleos físicos detectados también en Windows y macOS (con psutil)',
        'La simplificación en paralelo no trocea mallas pequeñas en franjas demasiado finas',
    ]),
    ('0.8.0', 'noviembre 2026', [
        'Ventana «Acerca de» con versión, autor e historial',
        'Ayuda con ejemplos medidos e imágenes de cómo cambia la pieza con cada parámetro',
        'Valores de ejemplo recomendados para cada parámetro',
    ]),
    ('0.7.0', 'noviembre 2026', [
        'Pestaña Impresión: tamaño final, boquilla, capa, cama y calidad',
        'Resolución y simplificación automáticas calculadas a partir de la impresora',
        'Aviso si la pieza no cabe en la cama o el relieve ocupa pocas capas',
    ]),
    ('0.6.0', 'noviembre 2026', [
        'Nuevo diseño: colores, parámetros en pestañas sin desplazamiento, panel de resultados',
    ]),
    ('0.5.0', 'noviembre 2026', [
        'Estimación del tamaño de los ficheros durante la generación',
        'Uso de todos los procesadores: ordenación y simplificación en paralelo',
    ]),
    ('0.4.0', 'noviembre 2026', [
        'Modo 3D (Poisson apantallado) que conserva socavados',
        'Simplificación adaptativa con medida del error',
        'Exportación PLY/OBJ con color, procesado por lotes y ajustes guardables',
    ]),
    ('0.3.0', 'noviembre 2026', [
        'Ayuda por parámetro, guía paso a paso y análisis de pérdidas',
        'Más opciones: limpieza, marco, molde, espejado y enderezado',
    ]),
    ('0.2.0', 'noviembre 2026', [
        'Primera interfaz gráfica en Qt: abrir nube, ver nube y relieve, generar y guardar',
    ]),
    ('0.1.0', 'noviembre 2026', [
        'Conversión de nube de puntos PLY a STL cerrado mediante mapa de alturas',
    ]),
]
