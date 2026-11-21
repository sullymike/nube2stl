# nube2stl: propuestas

Lista de trabajo de mejoras y de tipos de objetos nuevos, para retomarlas cuando se decida. Cada propuesta tiene qué
es, para qué sirve, cómo se haría con lo que ya tiene el programa, el esfuerzo estimado y los riesgos.

- **Estado:** noviembre de 2026, versión 0.22.0: **todas las propuestas de A a F están hechas** (la 0.21 reorganiza la ventana: paneles acoplables, menú Ver; la 0.22 añade la copia a tamaño real). Quedan anotadas con
  lo que se hizo, lo medido en las pruebas y sus límites, para cuando lleguen datos reales.
- **Ya hechas** (en la ayuda, «Mejoras ya incorporadas»): propuesta automática del tipo, barra de pasos, medir en el
  visor, superficies curvas (cilindro), grabados sobre roca, nubes de ejemplo, unir varias nubes (A1, 0.15.0) y el resto de A y B (0.16.0).
- **Descartadas por ser cosa del laminador:**
  - partir piezas grandes en trozos con espigas;
  - agujero o ranura de colgar;
  - 3MF a color.

**Esfuerzo:** S = un rato (menos de un día), M = uno o dos días, L = varios días.

---

## A. Tipos de objetos nuevos

### A1. Unir dos (o más) nubes en un solo modelo — *HECHA en 0.15.0*

**Lo que se hizo** (`unir.py`, `ventana_unir.py`; ayuda `#unir`): los tres casos, la deduplicación de la zona común,
la matriz en `Params.merge` y `--unir` en la consola para nubes ya alineadas.

**Lo que se aprendió al probarla** (muestreos independientes de los ejemplos, solapes del 15 y el 30 %):
- Por puntos + ICP es fiable: de 0,01 a 1 mm partiendo de clics con 1 mm de error. La excepción es un cilindro, que
  puede girar sobre su eje (3,6 mm).
- La automática solo acierta con forma marcada en la zona común (bloque con aristas). En lápidas y rocas resbala de
  forma consistente, con una separación igual de pequeña que la buena; en vasijas y columnas queda girada.
- No sirvieron para detectarlo: la separación media (1,2 frente a 1,4 veces la distancia entre puntos), la estabilidad
  geométrica (autovalores de [p×n, n]) ni RANSAC a escala más fina. La repetición al revés detecta parte de los fallos,
  pero también marca algunos aciertos. Por eso «Por puntos» es la opción recomendada y la automática siempre pide
  mirarla.

**Pendiente de A1:**
- igualar el color entre tandas — *HECHO el 18-11-2026 (0.25.0)*: `unir.color_match` (por canal, a ≈ g·b + o con
  las parejas de la zona común, dos reajustes quitando las que se salen 3 MAD; g en 0,5–2), en `merge` si la entrada
  tiene `'color'` (por defecto sí) y casilla «Igualar el color con la principal» en la ventana de unir (se ve con
  «colores reales»). Prueba `Union.test_igualar_color`. Mitad de la lápida con ganancias 0,75/0,8/0,95 y +0/+5/+25:
  recupera 1,33/1,25/1,05 y +1/−6/−26;
- en la zona común, quedarse con los puntos mejor vistos — *HECHO el 18-11-2026 (0.25.0)*: `unir.best_seen`, por
  cubitos de 20 separaciones (mayores que 3 veces el ruido) que las dos nubes llenan, la de menos ruido respecto al
  plano del cubito; la añadida gana si su ruido es < 0,6 veces el de la principal, y entonces se quitan los puntos de
  la principal que están sobre los suyos (`_lying_on`: < 2 separaciones a lo largo de la superficie y < 4σ a lo largo
  de la normal). Donde no gana, se quitan con la misma prueba los de la añadida que la de punto a punto no veía (doble
  piel con ruido). Por defecto (`'best'` en la entrada). Prueba `Union.test_zona_comun_mejor_vista`: placa con ruido
  0,5 / 0,05 mm, en la zona común queda 0,07 mm (antes 0,39); al revés, se queda la principal. Con dos mitades de la
  lápida con el mismo ruido no gana ninguna (con cubitos de 5 separaciones ganaban 632 puntos por azar: un cubito
  necesita unos 100 puntos para medir el ruido) y se quitan 4.700 puntos más de doble piel. Pendiente: «mejor vista»
  por el ángulo de la vista o la distancia al escáner, que las nubes no suelen traer;
- la vista de comprobación coloreada por separación — *HECHO el 18-11-2026 (0.25.0)*: color «separación» en la
  ventana de unir (`unir.separation`: distancia con signo a la superficie de la principal, normales PCA orientadas
  como la cara si es casi plana o hacia fuera si es un objeto; escala ± p95). Prueba `Union.test_separacion`. Dos
  mitades de la lápida: bien, p95 0,15 mm; giradas 1°, 1,1 mm y un degradado de un lado a otro.

**Propuesta original:**

**Qué es.** Abrir una segunda nube y juntarla con la primera antes de generar.

**Casos reales:**
- un objeto escaneado en dos vueltas: de pie y después tumbado para sacar la base;
- el anverso y el reverso de una pieza;
- dos tandas de fotogrametría de un mismo monumento;
- un relieve escaneado en dos mitades.

**Tres situaciones, de más fácil a más difícil:**
1. **Ya están alineadas.** Tienen las mismas coordenadas: el mismo proyecto de fotogrametría, georreferenciadas, o
   exportadas juntas del programa del escáner. Basta con juntarlas, pasando cada una a mm con sus unidades.
   Esfuerzo S.
2. **Con solape pero sin alinear.** Hay que alinearlas en dos pasos:
   - **Alineación gruesa**, de una de estas dos formas:
     - automática: características FPFH + RANSAC de Open3D, ya probadas en el programa;
     - por pares de puntos homólogos marcados con clic en el visor. Se aprovecha el picking de «Medir»: 3 o más
       pares por nube.
   - **Afinado con ICP**, después de la alineación gruesa.
   - Esfuerzo M.
3. **Sin solape** (como `lateral1`–`3`). No se pueden alinear de forma automática. Ya se probó: RANSAC da alineaciones
   falsas con la apariencia de buenas. Solo se puede con:
   - dianas comunes;
   - coordenadas comunes;
   - una geometría conocida que el usuario imponga (p. ej., «esta cara es perpendicular a esta otra en esta arista»).

   Fuera de alcance salvo el modo manual por puntos, si el usuario conoce puntos comunes (las esquinas).

**Cómo se vería:**
- *Archivo › Añadir otra nube…*.
- En la vista de la nube, cada fichero en un color.
- Un desplegable de alineación: «ya están alineadas», «automática (necesitan solape)», «por puntos».
- Tras alinear, el registro da:
  - la calidad: separación media en la zona común y % de solape;
  - un aviso claro si el solape es menor del 20 % o la separación es grande, que es el síntoma de una alineación
    falsa.
- Vista de comprobación con la zona común coloreada por separación. Se puede reutilizar el mapa de desviación.
- La transformación de cada nube se guarda en los ajustes, como los recortes. Así funcionan el registro de piezas,
  «Repetir», los lotes y la consola (`--unir otra.ply` con `--ajustes`).

**Al juntar:**
- Submuestrear por vóxel la zona común, para que no haya doble densidad ni «doble piel».
- Quitar los puntos de una nube que estén peor vistos que los de la otra (la normal más oblicua) en la zona común.
- Poisson y el mapa de alturas ya promedian lo que queda.

**Riesgos:**
- La alineación falsa: hay que medirla y enseñarla siempre.
- El color distinto entre tandas: se puede igualar por la media en la zona común.
- La memoria: dos nubes grandes.

**Esfuerzo:** S para el caso 1, M para los casos 1 y 2, L con la interfaz de pares de puntos bien hecha.

**Encaja con:** A2 (dos caras), objetos con base escaneada aparte y maquetas de monumentos por tandas.

### A2. Moneda, medalla o tablilla de dos caras — *HECHA en 0.16.0*

- **Hecho.** Archivo › Añadir el reverso. El mapa de alturas del reverso se calcula con la misma rejilla, se mira
  desde su lado (si p98 − p50 < p50 − p2, se da la vuelta: el fondo de un relieve es lo más bajo), se espeja y se
  gira para que los contornos coincidan (IoU de máscaras, 3° y luego 0,5°). «Grosor base» es el alma. Nota con
  giro a mano, darle la vuelta y quitar. Consola: `--reverso`, `--giro_reverso`.
- **Medido.** Correlación del reverso colocado 0,975 en cuatro posturas; malla cerrada.
- **Límite.** Piezas redondas: el contorno no decide el giro (aviso en el registro; giro a mano).

### A3. Vasija a partir de un fragmento (revolución) — *HECHA en 0.16.0*

- **Hecho.** Tipo «Fragmento». Eje por el complejo lineal de rectas normales (condición de Plücker, problema de
  autovalores 6×6 con IRLS); sección en el plano (altura, ±radio) rasterizada, cerrada y rellena; giro de 360° y
  Poisson. Perfil para el dibujo (SVG a tamaño real: sección en negro, silueta, escala). Ejemplo «fragmento».
- **Medido.** Eje: 0,14° y 0,6 mm de error; radio máximo 89,5 frente a 90 reales.
- **Límites.** Un fragmento no dice qué lado es la boca (heurística por el fondo; si no, Arriba = −z). Por debajo de
  30° de arco, el diámetro es poco fiable (se avisa).

### A4. Desenrollar sobre un cono o una superficie de revolución — *HECHA en 0.16.0*

- **Hecho.** Superficie cono (desarrollo exacto: ρ desde el vértice, φ = θ·sen α) y revolución (U = r·θ, V = arco
  del perfil, H = separación normal al perfil). Desplegable bajo «Curvo».
- **Medido.** Revolución: relieve de 0,84 mm (acanaladuras de 0,8) frente a 22,8 mm con el cilindro. Cono:
  generatriz 215,4 = teoría.
- **Límite.** En piezas muy abombadas la decoración se estira algo en horizontal; los tramos casi horizontales del
  perfil se deforman.

### A5. LiDAR aéreo (LAS) por clases — *HECHA en 0.16.0*

- **Hecho.** Nota al abrir un LAS con el recuento por clase (nombres ASPRS), atajos (todas, solo suelo, suelo y
  edificios) y «elegir…»; el ruido (7, 18) nunca. Sin clases: suelo calculado con filtro morfológico progresivo
  (Zhang 2003), no CSF. Consola: `--clases 2,6` o `--clases suelo`.
- **Medido.** Suelo calculado: 96 % del suelo y 0 % de árboles o edificios tomados por suelo (sintético).
- **Límite.** Falta probar con un LAS real del PNOA (pendientes fuertes, edificios muy grandes).

### A6. Comparar dos escaneos de la misma pieza (deterioro) — *HECHA en 0.16.0*

- **Hecho.** Archivo › Comparar con otro escaneo: alineación con la ventana de unir, afinado con ICP robusto
  (pérdida de Tukey, 4-2-1 × vóxel), distancia con signo a lo largo de las normales hacia fuera, umbral = 3 × ruido
  de la comparación. Vista coloreada, resumen, informe PDF y nube coloreada. `comparar.py` en la consola.
- **Medido.** Zona erosionada −1,41 (real −1,5), costra +0,94 (real +1), resto 0,07.
- **Límite.** Si cambia más de la mitad de la superficie, el ICP robusto no tiene dónde apoyarse.

### A7. Interiores y cuevas — *HECHA en 0.16.0*

- **Hecho.** Tipo «Interior»: normales hacia dentro (puntos de vista interiores); «corte» quita el techo por encima
  de una altura y mete la sala en un bloque; «negativo» invierte las normales (el volumen del hueco). Ejemplo
  «cisterna».
- **Límites.** La propuesta toma por interior una superficie cerrada de más de 1,5 m (un objeto grande escaneado
  por fuera puede confundirse). Puntos de vista en una rejilla de 3×3 a media altura: falta probar con cuevas
  ramificadas reales.

---

## B. Mejoras de calidad

### B1. Filtros que respetan las aristas — *HECHA en 0.16.0*

- **Hecho.** «Respetar las aristas» (filtro guiado de He et al., ruido medido en la propia rejilla). Va con el
  suavizado gaussiano: no añade parámetros.
- **Medido.** Ruido en zona lisa 0,052 frente a 0,103 con el gaussiano; error en los cantos 0,136 frente a 0,535.

### B2. Mejor relleno de huecos — *HECHA en 0.16.0*

- **Hecho.** Relleno «suave»: biarmónico por mínimos cuadrados dispersos por hueco (hasta 400 000 celdas; los
  mayores, interpolado). Sin scikit-image.
- **Medido.** Error en el hueco 0,10 frente a 0,80 (interpolado) y 3,55 (vecino).

### B3. Plano más robusto — *HECHA en 0.16.0*

- **Hecho.** «Plano de la cara»: nube / cara (RANSAC con franja del 0,5 %, afinado con sus puntos y Tukey) /
  puntos (tres clics en la vista de la nube).
- **Medido.** Cara con peana: 10,2° → 0,1°. Con una franja del 2 % ganaba un plano intermedio entre la cara y una
  peana poco inclinada (10°): por eso el 0,5 %.
- **Límite.** Si la peana tiene tantos puntos como el fondo de la cara, puede ganar ella: entonces, tres puntos.

### B4. Avisar de parámetros ocultos con valores no habituales — *HECHA en 0.16.0*

- **Hecho.** Línea bajo las pestañas con los valores cambiados de Malla y de Limpieza y suavizado, con «ver» y
  «dejarlos por defecto».

### B5. «Profundidad máx.» en los grabados — *HECHA en 0.16.0*

- **Hecho.** La nota de Orientar da también la profundidad del grabado cuando se quita la forma de la roca.

---

## C. Dos caras en ángulo (esquinas) — *C1–C3 HECHAS en 0.17.0, C4 en 0.18.0*

Pedido: dos ficheros, uno por cara, que van a 90° y solo comparten la arista.

### C1. Unir por la arista y el ángulo

- **Hecho.** Modo «Por la arista» en la ventana de unir: dos clics por nube en los extremos de la arista y el
  ángulo entre las caras (por dentro de la piedra). Plano de cada cara robusto; «fuera» por el lado al que sale el
  relieve; la arista se ajusta al borde de cada nube (recta por los puntos más cercanos en 24 tramos). Dos
  soluciones: «Al otro lado». «Por puntos» avisa si los puntos están en línea.
- **Medido.** Sin error en los clics: 0,09 mm. Con clics de ±0,5–0,7 mm: 0,4–1,2 mm (lo que queda es la posición a
  lo largo de la arista, que solo dan los clics).
- **Aprendido.** El RANSAC del plano usaba el tamaño en ejes x, y, z (dependía del giro de la nube) y contaba
  puntos dentro de la franja: con un relieve ancho ganaba un plano torcido 0,9°. Ahora, tamaño a lo largo de la
  nube y puntuación MSAC.

### C2. Modo esquina: un mapa de alturas por cara

- **Hecho.** «Esquina (dos caras)» en el tipo Bloque (esquina.py). Cada cara con su mapa (el tratamiento de
  relief.grid_from, sacado de heightmap); sólido = bajo las dos caras, delante de dos traseras paralelas a ellas;
  malla con tetraedros en marcha (6 por cubo alrededor de la diagonal, por tandas) sin librerías nuevas.
- **Medido.** Esquina sintética: cerrada, desviación media 0,06 mm, 15 s a 1 mm de celda.
- **Límites.** La cara más corta a lo largo de la arista se alarga con su borde; sin colores; sin exageración ni
  marco.

### C3. Reconocer dos caras en ángulo

- **Hecho.** esquina.two_faces: dos planos (el segundo, a 30° o más del primero) con al menos el 15 % de los puntos
  cada uno, que lleguen a la arista y planos de verdad (mediana de la separación < 0,2 de la franja: una roca
  curva se deja cubrir por dos planos anchos, pero no pasa). La propuesta pone Bloque + Esquina; la nota de
  «Plano de la cara» avisa si con «nube» se miraría desde la esquina.

### C4. La franja de la otra cara — *HECHA en 0.18.0*

- **Qué es.** Cuando un escaneo dobla un poco la esquina y trae una franja de la otra cara, «Por la arista» la usa:
  mide el ángulo en ella, decide con ella el lado de la piedra y afina con la zona común.
- **Hecho.** unir.other_face_strip (puntos que bajan del plano de la cara más que su relieve, a lo largo de la
  arista; plano robusto, que arranque en la arista; se busca a los dos lados), unir.overlap_refine (búsqueda en
  rejilla del deslizamiento dentro del plano de la zona común, ICP punto a plano amortiguado, otra búsqueda fina),
  unir.edge_align_strip. Casilla «Usar la franja de la otra cara, si la hay» (marcada).
- **Lo que se aprendió.** Con franja, el «Por la arista» de 0.17 se equivocaba de lado (la franja parece un relieve
  muy hondo) y dejaba la añadida a 175 mm. El ICP normal recorre muy despacio lo largo de una franja casi lisa
  (300 vueltas para 0,5 mm) y el punto a punto mete un sesgo junto a la arista: de ahí la búsqueda en rejilla.
- **Medido.** Ejemplo de la esquina (ahora con dentículos junto a la arista) con franja de 10–20 mm, clics con
  ±0,7–1,5 mm de error, caras a 88,5°, 90°, 95° y 120°: ángulo con < 0,05° de error, unión a 0,05–0,16 mm (solo
  con los clics, 1 mm). Igual con la franja en la principal o en la añadida. 4–5 s.
- **Límites.** La franja necesita algo de relieve para fijar lo largo de la arista; si es lisa, eso sigue
  dependiendo de los clics (el ángulo y el resto sí mejoran).

## D. Realzar lo pequeño — *HECHO en 0.18.0, ampliado en 0.20.0*

- **Qué es.** (Propuesta del usuario.) Realzar solo los detalles de poco relieve, positivos y negativos: ×2 a
  0,1 mm, cada vez menos aumento, nada a 0,5 mm (mm de la pieza).
- **Hecho.** relief.boost_small y boost_curve; campos «Realzar lo pequeño», «Realzar desde», «Realzar hasta»,
  «Anchura del detalle» (solo modo alturas, cara y reverso, también en la vista Orientación). Lo añadido sube
  suave de lo/2 a lo (un salto en el umbral haría un escalón en el borde de cada detalle) y baja suave hasta hi;
  la curva se fuerza creciente. El entorno de cada detalle es un filtro guiado (umbral hi/2) y el realce se apaga
  donde el filtro lo deja casi todo: junto a un canto grande no sale un escalón falso (0,07 mm sin esto, 0,006
  con esto).
- **Medido.** Surcos sintéticos de 0,1, 0,15 y 0,2 mm: +0,09 mm; 0,04 mm (ruido): nada. Ejemplo nuevo «Lápida
  gastada»: letras de 0,15–0,45 mm en la pieza, solo cambian las letras y la palma (19 % de la cara), los cantos
  no; la simplificación las conserva.
- **Límites.** Mide cada detalle respecto a su entorno de «Anchura del detalle»: un trazo más ancho que eso solo
  se realza por los bordes. El mapa de desviación muestra lo añadido como diferencia con el escaneo.
- **Ampliado (0.20.0): todos los tipos de pieza.** relief.boost_mesh sobre la malla terminada de los métodos con
  Poisson (objeto, fragmento, interior, terreno, bloque, 3D). Superficie de referencia: cuádricas ajustadas con peso
  gaussiano en centros cada «anchura»/3, con puntos cada «anchura»/8 cuya normal coincide (la otra cara de una pared
  fina no cuenta); cada vértice se mide respecto a las 4 más cercanas y se mueve a lo largo de su normal. Se apaga
  donde en el entorno hay algo mayor que «hasta» (canto grande) o más de un 3 % de superficie con otra orientación
  (arista viva, corte de la base). Medido: cilindro de 20 mm de radio con surcos de 0,15 mm → 0,25 mm (igual que en
  el mapa de alturas), la curvatura no cambia (0,000 mm) ni junto a un escalón de 2 mm (0,000; con la varianza en
  vez del máximo quedaban 0,05). Lápida gastada en modo objeto, 3D y bloque: se mueven las letras, como mucho
  0,10 mm, sin romper la malla; +1–3 s. Límite: no se ve en la vista previa; quedan toques sueltos en las esquinas
  de tres caras.
- **Corregido con lateral1 (0.20.0).** Una «F» hundida ~1 mm en la piedra, rodeada de letras grandes y del
  marco, no salía con ningún ajuste: queda en 0,06 mm de la pieza (3,5 m a 200 mm) y el filtro guiado apagaba el
  realce en todo el entorno de cualquier canto grande (subir la anchura lo empeoraba). Ahora la referencia es la
  mediana del entorno (en una rejilla reducida, 0,08 s para 200 000 celdas): lo grande no cuenta si ocupa menos de
  la mitad; el realce solo se apaga a unas celdas de lo que pasa de «hasta». En malla, igual: ajuste de la cuádrica
  robusto (arranca en la mediana, no cuenta lo que se aparta más de «hasta»), centros del otro lado de un canto
  fuera, y se apaga junto a lo grande, a las paredes y a las aristas vivas (normales de vértices vecinos a más de
  45°). Con ×4, 0,05–0,3 mm y anchura 4 mm la «F» se ve en la pieza. El registro dice cuánto es 1 mm de la nube en
  la pieza, para elegir los umbrales.

- **Umbrales en mm de la piedra (0.20.0, propuesta del usuario).** «Desde», «hasta» y «anchura» se escriben en mm
  de la nube: se eligen midiendo el escaneo y no cambian al cambiar el tamaño de la pieza. 0 = auto (lo que en la
  pieza son 0,1 / 0,5 / 3 mm, como antes). relief.boost_limits y boost_text; nota bajo los campos con lo que son en
  la pieza y la capa; el registro da las dos cosas.

## E. Ver el detalle antes de generar — *HECHO en 0.20.0*

- **Qué es.** (Propuesta del usuario.) Ver lo que se verá en la pieza, deprisa y sin ninguna generación.
- **Hecho.** Vista Orientación, desplegable «Vista rápida | Detalle de la pieza» y casilla «Capas».
  OrientPreview.compute_detail: heightmap() con la nube entera y la celda de la pieza, guardado con la clave de
  todos los parámetros salvo los que se aplican después (AFTER_GRID: realce, exageración, molde, placa, marco…);
  _piece_view (sacado de compute) aplica esos en décimas de segundo. «Capas» redondea las alturas a la capa de la
  impresora. Sin malla, simplificación ni desviación.
- **Medido.** lateral1 (10,7 M puntos): 2,7 s la primera vez, 0,10 s al cambiar el realce; la «F» se ve con ×4,
  0,05–0,3 mm, anchura 4.
- **Límites.** El mapa se rehace (segundos) al girar o cambiar tamaño, resolución o suavizados; el reverso de una
  pieza de dos caras no sale; en los tipos con Poisson es solo un alzado más fino, sin realce (el de la malla se ve
  al generar).

## F. Copia sin impresora (tamaño real o a escala) — *HECHO en 0.22.0*

- **Qué es.** (Pregunta del usuario: «¿cómo se podría hacer un objeto STL sin reducir la escala?».) Antes se hacía
  con Escala 1:1, pero el resto del programa seguía pensando en la impresora: detalle de media boquilla, tolerancia
  de 0,1 mm, base de 3 mm, cama, capas. Con el acueducto (37 m) eso daba fallos de memoria y una base de papel.
- **Hecho.**
  - Parámetro `target` (Destino: imprimir | real), con dos botones arriba del panel Parámetros. En «real»:
    `relief.is_real`, escala 1, sin cama ni boquilla (`res_print` = la del escaneo), sin realce, y tolerancia de
    simplificación = celda × `REAL_TOL[use]` (¼; al medir, 1/20). Se ocultan los parámetros de la impresora
    (`REAL_OFF`).
  - Parámetro `use` (archivo | cnc | gran formato | medir): fija la tolerancia y el formato que propone Guardar
    (PLY / STL / 3MF), con «_1a1» en el nombre.
  - Plan de Tamaño propio (`plan_html_real`): medidas en m o cm, detalle, **hasta dónde llega Poisson**
    (`relief.poisson_cell`) y aviso si la celda pedida es más fina.
  - Base proporcional al pasar a tamaño real: 0,5 % del lado mayor, entre 3 y 200 mm.
  - Asistente: primer paso «¿Para qué es?»; la rama de tamaño real cambia Impresora y Tamaño por «Tamaño real»
    (unidades con lo que mediría en cada una, uso, resolución, base y el plan).
  - Con ella, dos cosas que salieron al estudiarla:
    - **Unidades dudosas**: si «auto» supone metros, una pieza de menos de 5 cm o una malla de 20 a 5000 «mm»
      (`entrada.units_doubt`), al abrir la nube se pregunta con un cuadro que dice cuánto mediría en cada unidad.
      En tamaño real se pregunta siempre que estén en auto.
    - **Profundidad máx. automática** (0 = auto, por defecto): 250 mm o ¼ del lado mayor de la cara si es más
      (`relief.behind_mm`). Con 250 fijos, una fachada de 37 m perdía media nube.
- **Medido.** Acueducto (830 000 puntos): en metros, objeto con base, «real»: 35,9 × 15,1 × 4,0 m, celda de
  44 mm (la del escaneo), Poisson profundidad 10 → 38,5 mm, base 180 mm, tolerancia 11 mm.
- **Fallos que salieron al probarlo con el acueducto a 1:1** (corregidos en la misma versión):
  - El modo objeto (y el marco y el reverso) seguían escalando al lado mayor de 200 mm: `relief.by_length`.
  - PyMeshLab dejaba aristas abiertas al simplificar con coordenadas de decenas de metros en mm (16 aristas a 1:1,
    ninguna a escala reducida): se simplifica con la malla llevada a 100 de lado (`DECIMATE_SIZE`).
  - La fidelidad al escaneo medía hasta 6 separaciones de punto (38 cm), contra otra superficie: ahora pide un
    punto del escaneo a menos de 3 separaciones o 2 celdas. El acueducto pasa de «95 % a menos de 220 mm» a 14 mm.
  - Máximo detalle del acueducto: resolución 20 y alisado 0 → mediana de 3 mm a la malla original (7 mm con los
    automáticos), 4 min y 7 GB.
- **Revisión de todo el proceso a 1:1** (después):
  - A 1:1 la resolución automática es la del escaneo, así que el alisado del ruido se aplicaba siempre con su radio
    entero (a escala de impresora casi nunca, porque el submuestreo ya promedia). En una malla no hay ruido de
    medida: ya no se alisa (`mesh=True` en `relief.reconstruct`). Acueducto automático: de 7,7 a 5,6 mm de mediana.
  - Las mallas georreferenciadas no restaban el origen antes de pasar a float32 (las nubes sí).
  - Base: en la consola o con ajustes guardados quedaba en 3 mm; aviso con el valor proporcional (`real_base`).
  - Volúmenes y medidas legibles (`fmt_vol`, `fmt_len`, `fmt_dims`) en registro, ficha, consola y resultado.
- **Otra escala** (petición del usuario): el destino pasa a ser «Copia sin impresora», a 1:1 o a la escala de
  «Escala 1:» (`relief.real_scale`). La tolerancia (`REAL_TOL` × celda) se pasa a mm de la pieza; la base proporcional
  se calcula con la pieza escalada y sigue a la escala mientras no se toque a mano; el plan da el detalle en la pieza
  y en el original. Acueducto a 1:50: 71,8 × 26,4 × 6,1 cm, celda de 0,76 mm (38 en el original), base 5 mm.
- **Impresora en Avanzado** (petición del usuario): lista desplegable con las mismas impresoras que el asistente
  (`impresoras.groups`); marca la que coincide con los valores o «A mano».
- **Límites.**
  - La malla sigue saliendo cerrada (con base o trasera): una superficie abierta tal cual (sin base) para
    archivo no está hecha.
  - Trocear para gran formato sigue siendo cosa del laminador.
  - La profundidad de Poisson automática no pasa de 11: en piezas de decenas de metros el detalle tiene un suelo de
    centímetros, salvo que se suba a mano (y la memoria crece unas 4 veces por nivel).

## G. Solo una parte de la nube, y más detalle en una zona — *propuesta 17-11-2026; lazo y zonas con Poisson HECHOS en 0.23.0; pincel (G3) hecho después*

Dos supuestos del usuario: (1) no pasar toda la nube a malla, solo una parte; (2) una parte con más resolución
que el resto.

### G1. Elegir la parte — *lazo HECHO en 0.23.0; el resto, propuesto*

- **Lo que hay.** Nube › ✂ Recortar: un rectángulo en pantalla que atraviesa la nube en la dirección de la vista;
  quedarse con lo de dentro o quitarlo; varios encadenados desde vistas distintas; Deshacer / Sin recortes; se
  guardan en los ajustes y en el registro. Por dentro (`entrada.crop_mask`) cada recorte ya es un **polígono** en
  coordenadas de pantalla con su matriz, no un rectángulo.
- **Mejoras.**
  - **Lazo — HECHO (0.23.0).** Junto a ✂ Recortar, un desplegable «▭ rectángulo / ◌ lazo». El lazo se dibuja a
    mano alzada con el botón pulsado (un punto cada 3 px) y se cierra al soltar; se guarda como el polígono del
    recorte, sin tocar `crop_mask`. Se descartó el lazo por clics: con el arrastre basta y no choca con el doble
    clic (centrar la vista).
  - **Caja 3D con medidas** — *HECHA el 18-11-2026 (0.25.0)*. ✂ Recortar › «▣ caja con medidas…» abre
    `BoxDialog` (x, y, z mín./máx. en mm de la nube, rellenos con lo que ocupa la nube preparada; dejar o quitar);
    la caja se dibuja en la vista mientras se cambia (`_box_items`, en `extra_items`). Recorte
    `{'box': [x0, x1, y0, y1, z0, z1], 'inside'}` en `crop_mask`. Prueba `Recorte.test_caja`. Sin hacer: «alrededor
    de un punto con un radio» (con la caja y «una parte» no ha hecho falta).
  - **Quedarse con un grupo** con un clic — *HECHO el 18-11-2026 (0.25.0)*. ✂ Recortar › «👆 una parte (clic)»:
    el recorte `{'part': [x, y, z], 'inside'}` deja (o quita) la parte conectada con el punto del clic, entre lo que
    han dejado los recortes anteriores (`entrada.part_mask`, con la misma rejilla de vóxeles que los grupos sueltos:
    `part_cell` y `part_labels`, sacados de `clean_mask`). Con esta forma el botón izquierdo sigue girando.
    Prueba `Recorte.test_una_parte`: tres esferas separadas; dos que se tocan son una parte, y se separan
    quitando la unión con un rectángulo. En la ventana: tres vasijas, un clic en la del medio la deja sola.

### G2. Zona de detalle — *HECHA en 0.23.0 para los tipos con Poisson; mapa de alturas en 0.25.0 (cosida)*

- **Qué es.** Marcar una o varias zonas (con el mismo gesto que el recorte: rectángulo o lazo) y darles su
  resolución: la del escaneo o un valor; el resto, a la resolución normal (o más gruesa). Ejemplo: la inscripción de
  una lápida a 0,3 mm y el resto a 1 mm; los capiteles de un acueducto a 1:1 y el resto a 5 cm.
- **Cómo se haría** (datos: `Params.detail` = lista de zonas {M, poly, res}; la máscara, con `crop_mask`).
  - **Tipos con Poisson (objeto, 3D, bloque, terreno).** Poisson es un octree adaptativo: refina solo donde hay
    puntos densos. Se submuestrea la nube a la celda fina dentro de la zona y a la normal fuera, y la profundidad se
    fija para la celda fina. La memoria crece con el área de la zona, no con la de toda la pieza.
  - **Mapa de alturas (alturas).** La rejilla es uniforme: se haría a la celda fina en todas partes y fuera de la
    zona se suavizaría a la normal (o, si la memoria no da, una rejilla fina solo en la caja de la zona, cosida con la
    gruesa). Primera versión: rejilla fina entera, solo si cabe en memoria (la estimación ya lo dice).
  - **Simplificación con dos tolerancias.** PyMeshLab simplifica solo las caras seleccionadas (`selected=True`):
    primero fuera de la zona con la tolerancia normal, después dentro con la fina. Así el fichero solo crece por la
    zona.
  - **Vista.** Las zonas se pintan sobre la nube (como los recortes) y en la malla, con «Triángulos», se ve la
    diferencia. El plan de Tamaño y la estimación de memoria cuentan las celdas de cada zona.
  - **Transición.** Poisson y la simplificación ya pasan de una celda a otra sin escalones; en el mapa de alturas,
    una franja de unas celdas en la que se mezclan las dos.
- **Esfuerzo.** Medio–alto: Poisson y simplificación por zonas, medio; mapa de alturas, alto (por la memoria).
- **Riesgos.**
  - En impresión, la zona fina no puede bajar de media boquilla: tiene sentido sobre todo en copias sin impresora o
    en piezas grandes donde la resolución la limita la memoria.
  - La simplificación en franjas paralelas tiene que respetar la selección (cada franja con su parte de zona).
- **Lo que se ha hecho (0.23.0).**
  - Vista: tercera opción del desplegable del recorte, «zona de detalle» (rectángulo o lazo). No quita puntos: los
    tiñe de naranja. Deshacer quita lo último hecho, zona o recorte (`_marks`).
  - Datos: `Params.detail` (lista de {M, poly}, como los recortes) y **una sola** resolución para todas las zonas,
    `Params.detail_res` (Avanzado › Malla, «Resolución de las zonas»; 0 = la del escaneo). Una resolución por zona
    complicaba la interfaz sin un caso claro; se puede añadir guardando `res` en cada zona.
  - `relief.zone_mask`, `zone_res`, `zoned_voxels`: submuestreo con dos celdas en objeto/terreno (`clean_cloud`),
    3d (`recon3d`) y bloque. La profundidad automática de Poisson se calcula para la celda de la zona, con máximo 12
    (11 sin zonas).
  - `relief.simplify_zoned`: dos pasadas con `selected=True`; el error de cada pasada se mide solo con los puntos de
    su parte. Detalle de PyMeshLab: con `selected=True`, `targetfacenum` cuenta **solo las caras seleccionadas**
    (la primera versión pedía el total y no simplificaba nada). Va en un solo proceso (sin franjas): el problema de
    las franjas desaparece.
  - Plan (Tamaño): nº de zonas, celda (nube y pieza), % de la nube; avisa si no tienen efecto (la general ya es la
    del escaneo) o si en impresión son más finas que media boquilla. La memoria prevista suma las celdas de las
    zonas (`stats['detail_frac']`, medido sobre la nube).
  - Medido (prueba `ZonasDetalle3D`): esfera de 100 mm con bultitos de 0,8 mm, celda general 4 mm y zona a 1 mm:
    error en la zona 0,38 → 0,08 mm, fuera igual (0,40), 2.000 → 8.400 triángulos.
  - En la malla: Color › «zonas de detalle» (naranja; se elige sola la primera vez). La pieza guarda sus zonas y la
    matriz nube → pieza de antes de los acabados (`detail_zones`, `detail_xform`, también en el .npz del proceso
    aparte); `relief.zone_on_mesh` lleva los vértices a la nube y aplica la misma máscara. Comprobado en objeto, 3d
    y bloque (los vértices vuelven a la nube a < 0,5 mm de mediana).
  - Mapa de alturas — *primera versión HECHA en 0.25.0* (`relief._zone_refine`, desde `heightmap` en modo alturas):
    - La celda fina es la general ÷ k (`zone_k`), para que cada celda general sea k × k finas y las rejillas
      queden alineadas. La rejilla final es fina en toda la pieza (límite 60 M celdas, como siempre); fuera, la
      general interpolada (`ndimage.zoom`, bilineal por áreas).
    - Dentro: `grid_from` solo con los puntos de la caja de las zonas (+3 celdas generales), alineada con la final
      (`origin`) y con la forma de la roca de la general (`like`; calculada con un trozo salía otra y había
      escalón). El suelo (`floor_pct`) no se copia: el de la general, con los bultos aplastados, es más alto y se
      comía los valles (error 0,12 → 0,095).
    - Peso: 1 en las celdas con mayoría de puntos de la zona, baja a 0 en 2 celdas generales; también a 0 donde
      la rejilla fina no tiene pieza (su máscara, no las celdas sin puntos: con ellas el peso bajaba alrededor de
      cada hueco y el error subía a 0,125).
    - Primer intento descartado: sumar a la general solo el detalle fino (fina − fina suavizada). La general toma
      el percentil 90 de celdas grandes y queda más alta en los relieves con bultos: el error solo bajaba de 0,54 a
      0,46.
    - Medido (losa con bultitos de 0,8 mm cada 5 mm, celda de 2 mm, zona a 0,5 mm, front_pct 50): error en la
      rejilla, todo a 2 mm 0,30; todo a 0,5 mm 0,091; con la zona, dentro 0,097 y fuera 0,295. Pieza: 2.652 →
      15.834 triángulos, triángulos de la cara de 0,62 mm² dentro y 3,2 mm² fuera (mediana), cerrada.
    - La base va bajo el punto más bajo, que con la zona está en un valle más hondo: la losa sale ~0,6 mm más
      gruesa (+20 % de volumen en la prueba). Es el relieve de verdad.
    - De paso: `simplify_zoned` exige también error máximo ≤ 2 × tolerancia; con la pared en celdas finas, la
      simplificación metía las esquinas 1,5 mm (p99,9 no lo veía).
    - Pruebas: `ZonasDetalle.test_mapa_de_alturas`, `ZonasDetalleAlturas.test_relieve_con_zona`.
    - Pendiente: ~~rejilla fina solo en la caja de las zonas, cosida con la general~~ (*HECHO el 18-11-2026*, ver
      abajo); el reverso: una pieza de dos caras no lleva zonas (se avisa).
  - Mapa de alturas, **segunda versión — HECHA el 18-11-2026 (0.25.0)**: la rejilla general no cambia y la fina va
    aparte, solo en la caja de las zonas (`hm.zone`: J0, J1, I0, I1 en celdas generales, k, grid, mask, cgrid),
    alineada con las celdas generales. En los lados de la caja que lindan con celdas generales el peso es 0 (la
    general interpolada justa). `_mesh_zoned`: vértices en la retícula fina con claves enteras (`np.unique`, sin
    matrices de toda la pieza); las celdas generales que lindan con la caja se triangulan en abanico desde su centro
    pasando por los vértices finos de ese lado (sin vértices en T); paredes y base salen de las aristas de borde de
    la cara de arriba, sin suponer rejilla. `_reconstruct_25d` aplica a las dos rejillas el mismo origen de alturas,
    molde, exageración, realce, rectángulo y marco. `zone_full_grid` rehace la rejilla fina entera para medir.
    Medido (la misma losa): error dentro 0,092 (antes 0,097), fuera 0,296; malla sin simplificar 141.000 triángulos
    (antes 336.000), simplificada 14.900 (antes 15.900), 6,2 s y 407 MB (antes 22,7 s y 517 MB). Cerrada con
    contorno, rectángulo, marco y molde. Con reverso, sin zonas (aviso).

- **Zonas con Poisson sin subir la profundidad** (*HECHO el 21-11-2026, 0.26.0*). Medido en el terreno de 35 × 15 m
  del usuario (malla de 19 M triángulos, 60 zonas, 30 % de la nube): subir Poisson de 11 a 12 por las zonas daba una
  estimación de 23–35 GB; el octree se afina en toda la pieza, no solo «donde hay puntos densos» (lo que decía la
  ayuda), porque la nube tiene casi la misma densidad dentro y fuera. Ahora `relief.refine_zones`: Poisson con la
  resolución general y después, en la malla cerrada, bisección por la arista más larga (solo si es la más larga de
  sus dos triángulos: la malla sigue conforme) hasta 1,2·celda fina, y cada vértice de la zona a lo largo de su
  normal hasta la mediana de los puntos de la zona a menos de una celda de esa recta (3 pasadas, como mucho 1,5
  celdas de Poisson, normales del mismo lado). Esfera con bultos: 0,069–0,08 mm frente a 0,068 subiendo dos
  niveles; terreno a profundidad 10 con las 60 zonas: 4,4 GB (3,9 sin zonas), desviación media 2,8 mm (3,3 sin).
  `relief.zone_subset` para los modos 3d y bloque. Al probarlo salió que la simplificación por zonas exigía error
  máximo ≤ 2× tolerancia: un vértice a 7,07 mm (tolerancia 3,5) dejaba 4,4 M triángulos sin simplificar; ahora 3×.
  Alisar el desplazamiento con los vecinos no mejoró nada (`REFINE_SMOOTH = 0`).

### G3. Pincel para las zonas de detalle — *HECHO el 18-11-2026 (después de 0.23.0)*
- **Por qué.** Al usuario le parecía que «pintar la zona» no estaba hecho: la zona solo se marcaba con la tercera
  opción del desplegable del recorte, que no se veía, y con lazo o rectángulo, no con un pincel.
- **Hecho.**
  - Botón propio en la vista de la nube, **🖌 Zona de detalle**, aparte de ✂ Recortar. Al marcarlo salen sus
    opciones: pincel / lazo / rectángulo, tamaño del pincel (fino, mediano, grueso, muy grueso: 6–60 px de radio
    en pantalla) y pintar / borrar. Recortar, Zona de detalle y Medir se excluyen; las opciones de cada uno solo se
    ven con él marcado. El desplegable del recorte vuelve a ser solo dejar / quitar.
  - Pincel: un círculo sigue al cursor y el trazo se dibuja encima mientras se arrastra. Al soltar,
    `visor.brush_centers` remuestrea el trazo cada medio radio y en cada paso toma el punto visible más cercano al
    cursor (el de delante entre los del círculo: los que están a más de un radio de profundidad no cuentan). La
    zona se guarda como `{'brush': centros, 'r': radio}` (mm de la nube; radio = los píxeles pasados a mm a esa
    profundidad) y `relief.brush_mask` marca los puntos a menos de r de algún centro. Así se pinta la
    superficie que se ve, no lo que queda detrás (el lazo sí atraviesa).
  - Borrar (`'erase': True`, con el pincel o el lazo): `zone_mask` aplica las zonas en orden y las de borrar quitan
    de lo anterior. Sin nada pintado, no deja borrar. `zone_count` → (pintadas, borradas) para el plan.
  - Ctrl + botón izquierdo gira la vista sin soltar la herramienta (también en el recorte). «Sin recortes» pasa a
    «Quitar todo».
  - Pruebas: `ZonasDetalle.test_pincel_y_borrar` y `test_pincel_pinta_solo_lo_de_delante` (dos placas a 50 mm una
    de otra: solo se pinta la de delante, en una franja del ancho del pincel). En la ventana, un trazo grueso sobre
    el bloque de ejemplo y uno de borrar que lo corta: 9,9 % de la nube en la zona; en la malla, triángulos de
    1,7 mm dentro y 3,3 mm fuera.
  - Después (a petición del usuario: los ajustes se quedan en el panel de la izquierda, no en la barra del
    pincel): «Resolución de las zonas» va justo debajo de «Resolución» y lleva una nota (`nube2stl.zone_note`)
    con la celda dentro y fuera, y la profundidad de Poisson (`relief.zone_poisson`: la automática sale de la celda
    de las zonas, máximo 12; con una a mano demasiado baja avisa y propone la automática). Arreglado de paso: el
    desplegable de impresora ensanchaba Avanzado a 739 px en un panel de 338 y los «?» quedaban fuera.

---

## Ideas para después (sin estudiar)

- **Fallo visto y ARREGLADO el 17-11-2026 (0.23.0; venía de antes):** el bloque de ejemplo con «Resolución» manual
  2,5 (el escaneo da 0,92) salía de 200 × 200 en vez de 200 × 142 y con 6 aristas abiertas; con 4, de 107 mm de
  alto y 24 abiertas. Causa: los puntos de los laterales caen en el borde de la silueta y muchos **fuera de la
  rejilla** del mapa de alturas, que se ajusta a la cara; `bloque3d` ensanchaba la silueta 3 celdas pero antes
  descartaba lo que caía fuera de la rejilla. Con celdas de 2 mm o más no quedaba ningún lateral (9 de 18.793): el
  bloque se quedaba en 7 mm de grueso, con muy pocos puntos sintéticos, y Poisson se iba a −100 mm. Arreglo: la
  silueta ensanchada se mira sobre la rejilla con un margen de 3 celdas. Ahora cualquier celda da 200 × 142 × 29 mm
  y en automático entran el doble de puntos de los laterales (18.566 frente a 8.614). Prueba
  `Modos3D.test_bloque_celda_gruesa`. El modo 3d tiene el mismo filtro, pero allí es a propósito (solo la cara).

- Copia a tamaño real como superficie abierta (sin base), y por trozos para piezas que ni Poisson con 12 niveles
  cubre al detalle del escaneo. — *Superficie abierta HECHA el 18-11-2026 (0.25.0)*: parámetro `open_mesh` (solo con
  «Copia sin impresora»), `relief.open_surface` sobre la malla ya hecha (cualquier tipo): se quedan los triángulos con
  los tres vértices a menos de 2 celdas o 3 separaciones del escaneo, y en el borde se quita en tres pasadas el
  flequillo cuyo centro se sale más de 1 celda (con 1 celda para todo, la vasija se agujereaba: 26.400 aristas de
  borde frente a 1.300); fuera los trozos de menos del 1 %. `Result.open_edges` (aristas de borde; `bad_edges` pasa a
  contar las de más de dos caras), `mesh_state`, `vol_text`; la simplificación acepta las aristas que ya traía la
  malla (las de borde se conservan). Lápida: 120.000 de 238.000 triángulos fuera; bloque, 37.000; vasija, 21.000.
  Prueba `TamanoReal.test_superficie_abierta`. Pendiente: por trozos.

- Probar con datos reales: A5 (un LAS del PNOA), A7 (una cueva o una cisterna escaneadas) y C (dos escaneos de una esquina).
- Dos caras con canto escaneado: proponer unir (A1) en vez de reverso cuando las nubes se solapan — *HECHO el
  18-11-2026 (0.25.0)*: `unir.reverse_hint` al añadir el reverso. 'igual' si ya comparten coordenadas y se tocan
  (`quality` con la identidad: > 2 % y rms < 3 separaciones); 'canto' si las dos traen canto (`edge_fraction`:
  normal a más de 60° de la de la cara y en el borde de la silueta de frente; cara con canto 0,2–0,3, lápida y roca 0,
  terreno 0 aunque tiene el 29 % de puntos inclinados). Ventana «Reverso o unir» con «Unir las nubes» (abre la de
  unir, que ya elige «ya alineadas») o «Reverso igualmente». Prueba `Union.test_reverso_o_unir` (losa de dos caras).
- Comparar más de dos fechas (serie temporal) en un mismo informe. — *HECHO el 18-11-2026 (0.25.0)*:
  `comparar.series` (cada fecha alineada con la primera, umbral común, evolución en 60.000 lugares de la primera con
  `signed_distances` al revés, ritmo en mm/año por mínimos cuadrados desde la referencia), `date_of`/`parse_date`,
  `report_series_pdf` (gráficas, un mapa por fecha a la misma escala, mapa del ritmo), Archivo › Comparar varias
  fechas (ventana de unir para cada una, fechas corregibles, vista con selector) y `comparar.py a b c … informe.pdf
  --fechas`. Prueba `Novedades.test_comparar_varias_fechas`: −1,5/−3/−4,5 mm y −0,50 mm/año. **Salió al probarlo**:
  en una placa lisa el ICP robusto se desliza sobre sí misma (mapas girados unos grados y desplazados 10 mm, con las
  cifras bien porque las distancias no cambian): ahora avisa si el afinado mueve el escaneo más de 10 vóxeles desde
  la alineación de partida. Y en una pieza simétrica, qué es «fuera» lo decide «Mirar desde»: colores al revés →
  darle la vuelta (en la ayuda).
- «Mirar desde: auto» se equivoca de lado en una lápida con un marco alto y ancho (un listel de 6 mm en un
  cuarto de la superficie): el marco parece la cola de los laterales de un bloque. Lo resuelve «Darle la vuelta»;
  se podría distinguir una meseta (marco) de una cola repartida (laterales). — *HECHO el 18-11-2026 (0.25.0)*:
  `relief._tail_faces_front`. La mitad exterior de la cola larga es un marco si (1) está junto al contorno (distancia
  mediana al borde de la silueta < 0,4 la del resto: el fondo de unas letras incisas, que visto desde detrás es igual
  que un marco, está dentro), (2) es una meseta (desviación de alturas < 10 % de la cola: la moldura de la lápida 8 %,
  el canto redondeado de la gastada 15 %) y (3) mira de frente (más de la mitad de las normales a < 45°). Entonces el
  frente es el lado de la cola. Prueba `Recorte.test_mirar_desde_auto_con_marco`: listel de 6 mm de 8, 14 y 20 mm
  (18–43 % de la superficie), bien. **Sorpresa: la lápida de ejemplo (moldura de 3 mm) también se leía desde
  detrás** (moldura hundida, letras en relieve) y nadie lo había notado; ahora sale bien. Los demás ejemplos, igual.
