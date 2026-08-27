"""Full-length `Source` transcripts for the LARGE context rung of
`evals/query_attribution/` (#887).

Same three meetings as the small rung, same speakers, same decisions, same
proposers, same conditions -- the ONLY variable is document size. A question
grounded in the small bundle is grounded in the large one, so a compliance
difference between the two rungs is attributable to the size of the retrieved
documents rather than to what the bundle knows.

## Why these are shaped the way they are

`prompt_budget.bounded_text` takes an EVEN-COVERAGE excerpt and always keeps
the first and the last window whenever at least two fit. Real meeting
transcripts open with an agenda and close with a recap, so these do too --
and that is a deliberate CONTROL, not decoration. Without a surviving recap
the excerpt would drop the decisive turns, every large cell would collapse
into refusals, and the probe would measure groundedness instead of
attribution. The runner's `--self-test` asserts that every decisive fact
still survives the bound, so an edit that breaks the control fails loudly
instead of silently re-pointing the experiment.

The cost of that control is worth stating: this probe measures attribution
under a thin, elided excerpt WHOSE ANSWER IS PRESENT. Whether even-coverage
excerpting preserves the answer in the wild is a separate question and this
harness does not answer it.

## Why ~7-9 KB and not the wild 55 KB

Because the excerpt CONVERGES. Measured on this corpus through the production
bound: each block's excerpt length and elision-marker count stop moving at 8x
document size and are then identical at 16x, 32x and 64x. The wild sources
are 55,403 and 57,116 chars against 7.4-9.6 KB here -- 6x to 7.7x -- so they
sit in that converged regime, and authoring 55 KB of distinct transcript per
document per language would have bought a different discard fraction and the
same prompt.

The transfer is not free, and the runner states its price rather than waving
it away: at 1x these documents are just BELOW the settling point, so the
authored rung sends 1.2% more context than the converged prompt in Spanish
and 19.3% more in English (English loses a picked window per block between
1x and 2x). `--self-test` pins both halves -- the convergence itself and that
remaining drift, against `_CONVERGENCE_MARGIN`.

A first draft of this note claimed the excerpt was size-INVARIANT. The
self-test refuted it: window boundaries move as a document grows, so which
windows the even-coverage picker can afford moves too. Convergence is the
weaker, true statement.

## The leading agreements summary

Each transcript opens with a one-line `Acuerdos alcanzados:` /
`Agreements reached:` summary, before the attendee and agenda lines. Minutes
are written that way, and here it is also load-bearing: `chunk_lines` packs
windows greedily from the START, so only the FINAL window of a closing recap
is guaranteed to survive -- a pre-flight over the real budget showed two of
three closing anchors being excerpted away while the third survived by
accident of line packing. Window 0, by contrast, is picked for every excerpt
that keeps at least two windows. Putting the decisive facts there makes the
control structural instead of lucky.
"""

from __future__ import annotations

from typing import Final

_ES_01: Final = """\
Reunión 01 — trazabilidad del compilador de conocimiento
Acuerdos alcanzados: (1) el compilador guarda el historial secuencial de cada decisión tomada, no sólo el estado final, propuesto por Gustavo Martínez; (2) toda respuesta del motor lleva citas textuales obligatorias a los documentos que la sustentan, condición de Jason Sepúlveda, porque sin ellas no se distingue una respuesta fundamentada de una alucinación; (3) el bundle de archivos es la fuente canónica y los índices derivados son caché reconstruible, nunca la verdad.
Asistentes: Ana Ríos (facilitadora), Gustavo Martínez (ingeniería de datos), Jason Sepúlveda (arquitectura).
Agenda: (1) el problema de la trazabilidad, (2) historial de decisiones, (3) citas textuales en las respuestas, (4) qué objeto es la fuente de verdad, (5) acuerdos.

Ana Ríos: Abro la primera sesión. El tema es el compilador de conocimiento y quiero que salgamos con acuerdos, no con una lista de deseos.
Gustavo Martínez: Antes de entrar, dejo el contexto de por qué pedí la reunión. La semana pasada me preguntaron por qué habíamos elegido guardar los adjuntos fuera del repositorio, y no supe responder.
Gustavo Martínez: Encontré el resultado de la decisión, sí. Está en la configuración, está en el código, está funcionando. Lo que no encontré fue el razonamiento.
Ana Ríos: ¿Y en las notas de la reunión donde se decidió?
Gustavo Martínez: Las notas dicen "se acuerda mover los adjuntos". Tres palabras. No dicen quién lo propuso, qué alternativa se descartó, ni qué condición se puso.
Jason Sepúlveda: Eso es exactamente lo que a mí me preocupa del compilador tal como está planteado. Compila el estado final del conocimiento y descarta el camino.
Jason Sepúlveda: Y el camino es la mitad del valor. Un equipo que no puede reconstruir por qué decidió algo vuelve a discutirlo cada seis meses.
Ana Ríos: Estoy de acuerdo con el diagnóstico. Hablemos de la forma concreta.
Gustavo Martínez: Mi propuesta es que el compilador guarde el historial secuencial de cada decisión, no solamente el estado final. Cada decisión con su fecha, su proponente y sus condiciones.
Ana Ríos: Secuencial en el sentido de que se puede leer en orden y ver la evolución.
Gustavo Martínez: Exacto. Si una decisión reemplaza a otra, la anterior no se borra: queda marcada como superada, con un puntero a la que la reemplazó.
Jason Sepúlveda: Ahí tengo una objeción práctica. Si nada se borra, el bundle crece sin techo y la búsqueda se llena de ruido histórico.
Gustavo Martínez: Es una objeción real. Pero el crecimiento es lineal en decisiones, no en documentos. Estamos hablando de unas decenas de registros por trimestre, no de miles.
Jason Sepúlveda: ¿Y la búsqueda?
Gustavo Martínez: La búsqueda filtra por estado. Una decisión superada no aparece en los resultados por defecto; hay que pedirla explícitamente.
Ana Ríos: Eso me deja tranquila. La auditoría es un modo, no el modo normal.
Jason Sepúlveda: Con ese filtro retiro la objeción. Prefiero pagar el crecimiento a perder el porqué.
Ana Ríos: Entonces propongo que el compilador guarde el historial secuencial de cada decisión, no sólo el estado final. ¿Objeciones?
Gustavo Martínez: Ninguna.
Jason Sepúlveda: De acuerdo, pero agrego una condición y quiero que quede registrada como condición, no como comentario.
Ana Ríos: Adelante.
Jason Sepúlveda: Toda respuesta que dé el motor tiene que llevar citas textuales a los documentos que la sustentan. Sin eso no podemos distinguir una respuesta fundamentada de una alucinación.
Ana Ríos: Explica por qué lo pones como condición de esta decisión y no como un tema aparte.
Jason Sepúlveda: Porque el historial sólo sirve si se puede verificar. Si el motor me dice "esto se decidió en marzo" y no me muestra de dónde lo sacó, el historial es una segunda fuente de afirmaciones sin respaldo.
Gustavo Martínez: Es decir, la trazabilidad de la decisión y la trazabilidad de la respuesta son el mismo problema.
Jason Sepúlveda: Son el mismo problema visto desde dos lados. Uno es "por qué el conocimiento quedó así". El otro es "de dónde salió lo que me acabas de decir".
Ana Ríos: ¿Textuales en qué sentido? ¿Un identificador basta?
Jason Sepúlveda: No. Un identificador es una promesa. Quiero el fragmento, el texto tal cual está en el documento, para poder compararlo con la afirmación.
Gustavo Martínez: Eso encarece la respuesta. El fragmento ocupa espacio en la pantalla y en el prompt.
Jason Sepúlveda: Lo asumo. Es más barato que una decisión tomada sobre una cita inventada.
Ana Ríos: ¿Qué pasa cuando la respuesta no se apoya en ningún documento?
Jason Sepúlveda: Entonces la respuesta tiene que decirlo. "No hay nada en el bundle sobre esto" es una respuesta correcta. Inventar no lo es.
Gustavo Martínez: Y si el modelo responde de su propio conocimiento sin darse cuenta, ¿cómo lo detectamos?
Jason Sepúlveda: Por eso las citas tienen que ser una declaración explícita del modelo, no una lista que arma el sistema por su cuenta. El sistema puede recuperar cinco documentos y el modelo usar dos.
Ana Ríos: Entonces el modelo declara cuáles usó.
Jason Sepúlveda: El modelo declara cuáles usó, y el sistema cita exactamente esos. Si el modelo no declara nada, caemos al comportamiento conservador y citamos todo lo recuperado, marcándolo como no verificado.
Gustavo Martínez: Me gusta que el caso degradado sea ruidoso y no silencioso.
Ana Ríos: Queda anotado. Pasemos al cuarto punto de la agenda.
Gustavo Martínez: Quiero dejar algo asentado que hoy es implícito y mañana va a ser una discusión: el bundle de archivos es la fuente canónica. Ningún índice derivado puede convertirse en la verdad.
Ana Ríos: ¿Qué te preocupa exactamente?
Gustavo Martínez: Me preocupa el patrón habitual. Se construye un índice para acelerar la búsqueda, alguien le agrega un campo que no está en los archivos, y a los tres meses el índice tiene información que no existe en ningún otro lado.
Jason Sepúlveda: Y entonces borrarlo deja de ser seguro.
Gustavo Martínez: Y entonces borrarlo deja de ser seguro, que es justamente lo que un caché tiene que ser: borrable.
Ana Ríos: ¿Qué índices tenemos hoy?
Gustavo Martínez: Búsqueda léxica, vectores y grafo. Los tres son reconstruibles a partir de los archivos. Quiero que eso sea una regla, no una casualidad.
Jason Sepúlveda: La prueba operativa es simple: si borro el directorio de índices y reindexo, ¿pierdo algo?
Gustavo Martínez: Hoy no pierdo nada. Quiero que mañana tampoco.
Ana Ríos: ¿Cómo lo garantizamos?
Jason Sepúlveda: Con una prueba que lo ejercite. Borrar, reconstruir y comparar. Si algo cambió, el índice tenía estado propio.
Gustavo Martínez: Y con una regla de revisión: cualquier campo nuevo en el índice tiene que tener un origen en los archivos.
Ana Ríos: Ambas cosas. La prueba detecta y la regla previene.
Jason Sepúlveda: Hay un caso borde que quiero nombrar. Los embeddings dependen del modelo. Si cambio el modelo, el índice reconstruido no es byte a byte igual al anterior.
Gustavo Martínez: Correcto, pero es igual en contenido recuperable. La regla es sobre información, no sobre bytes.
Jason Sepúlveda: Entonces digámoslo así: ningún índice contiene información que no se pueda derivar de los archivos.
Ana Ríos: Esa formulación me sirve. Volviendo a las citas, tengo una pregunta de forma.
Ana Ríos: Si el modelo declara los documentos que usó, ¿cómo los nombra? Los identificadores internos son largos y feos.
Jason Sepúlveda: Que no los nombre. Numeramos los bloques de contexto y el modelo declara números.
Gustavo Martínez: Eso además evita que el identificador se filtre a la prosa de la respuesta, que es un problema que ya vimos.
Ana Ríos: ¿Lo vimos dónde?
Gustavo Martínez: En las primeras pruebas. El modelo copiaba la etiqueta del bloque dentro del párrafo porque la instrucción le pedía citar por identificador. Hacía exactamente lo que se le pedía.
Jason Sepúlveda: Con números el vocabulario de la atribución queda separado del vocabulario de la prosa. No hay forma de confundirlos.
Ana Ríos: Bien. Un último punto antes de cerrar: ¿qué hacemos si la respuesta es larga y el modelo omite la línea de atribución?
Jason Sepúlveda: Caemos al comportamiento conservador y lo registramos. Pero quiero que quede claro que esa caída es un defecto a medir, no un estado aceptable.
Gustavo Martínez: ¿Medirlo cómo?
Jason Sepúlveda: Con una medición periódica sobre preguntas conocidas. Si la tasa de omisión sube, es una regresión.
Ana Ríos: De acuerdo. Cierro con los acuerdos.

Cierre y acuerdos de la reunión 01:
Ana Ríos: Primer acuerdo: el compilador guarda el historial secuencial de cada decisión tomada, no sólo el estado final del bundle. Lo propuso Gustavo Martínez y no hubo objeciones tras aclarar que las decisiones superadas quedan fuera de la búsqueda por defecto.
Ana Ríos: Segundo acuerdo: toda respuesta que dé el motor lleva citas textuales obligatorias a los documentos que la sustentan. Es la condición que puso Jason Sepúlveda en esta reunión, y su razón es que sin citas textuales no se puede distinguir una respuesta fundamentada de una alucinación. El modelo declara qué bloques usó y el sistema cita exactamente esos; si no declara nada, se cita todo lo recuperado y se marca como no verificado.
Ana Ríos: Tercer acuerdo: el bundle de archivos es la fuente canónica. Los índices derivados —búsqueda léxica, vectores y grafo— son caché reconstruible y nunca la verdad. Ningún índice contiene información que no se pueda derivar de los archivos, y eso se verifica borrando y reconstruyendo.
Ana Ríos: Queda pendiente para la próxima sesión la ingesta y la curación. Gustavo trae el estado de las transcripciones.

"""

_ES_02: Final = """\
Reunión 02 — ingesta y curación
Acuerdos alcanzados: (1) la curación es un paso explícito con consentimiento por ítem antes de publicar cualquier objeto derivado; (2) la ingesta es idempotente por origen, así que ingerir el mismo archivo dos veces no duplica objetos y la identidad del origen decide si una ingesta es nueva o una repetición, condición de Jason Sepúlveda; (3) Bruno queda como responsable de la migración del corpus antiguo.
Asistentes: Ana Ríos (facilitadora), Gustavo Martínez (ingeniería de datos), Jason Sepúlveda (arquitectura).
Agenda: (1) estado de las transcripciones, (2) revisión de calidad, (3) curación explícita, (4) idempotencia de la ingesta, (5) responsables, (6) acuerdos.

Ana Ríos: Segunda sesión. Gustavo, empieza con el estado.
Gustavo Martínez: Procesé las transcripciones de las últimas cuatro sesiones y están cargadas en el bundle. Son cuatro archivos, unos doscientos kilobytes en total.
Ana Ríos: ¿Y la revisión de calidad?
Gustavo Martínez: Falta. Y no falta por descuido: falta porque no existe el paso. Hoy la ingesta escribe directo.
Jason Sepúlveda: Directo significa que si la extracción se equivoca, el error queda en el bundle como si fuera conocimiento verificado.
Gustavo Martínez: Exacto. Y con el acuerdo de la reunión pasada eso es peor, porque ese objeto erróneo ahora también puede ser citado como respaldo de una respuesta.
Ana Ríos: Entonces la trazabilidad amplifica el problema de calidad en vez de resolverlo.
Jason Sepúlveda: La trazabilidad te dice de dónde salió. No te dice si lo que salió está bien.
Gustavo Martínez: Mi propuesta es que la curación sea un paso explícito, con consentimiento por ítem, antes de publicar cualquier objeto derivado.
Ana Ríos: Por ítem. Definilo con precisión, porque ahí está toda la discusión.
Gustavo Martínez: Cada escritura propuesta se confirma por separado. Si la extracción propone doce conceptos, el usuario ve doce propuestas y acepta o rechaza cada una.
Jason Sepúlveda: ¿Y no hay un "aceptar todo"?
Gustavo Martínez: Puede haberlo como comodidad, pero no puede ser el camino por defecto ni puede ser silencioso. La aplicación masiva silenciosa de cambios al bundle es exactamente lo que quiero prohibir.
Ana Ríos: Mi preocupación es la fricción. Doce confirmaciones por archivo es mucho.
Gustavo Martínez: Es mucho la primera vez. Después de la primera vez el usuario sabe qué tipo de propuesta suele estar bien y cuál no.
Jason Sepúlveda: Y hay un punto de diseño que baja la fricción sin romper la regla: la propuesta tiene que mostrar el fragmento de origen al lado. Si veo de dónde salió, decido en dos segundos.
Ana Ríos: Eso me convence. La fricción cara es leer el documento entero para verificar una propuesta.
Gustavo Martínez: Anoto entonces que la propuesta muestra su origen.
Jason Sepúlveda: Quiero agregar una segunda condición sobre la ingesta, distinta de la curación.
Ana Ríos: Adelante.
Jason Sepúlveda: La ingesta tiene que ser idempotente. Si corro el mismo archivo dos veces no puede duplicar objetos.
Gustavo Martínez: Hoy duplica.
Jason Sepúlveda: Hoy duplica, y lo descubrí de la peor manera. Reingesté un archivo para probar un cambio de extracción y terminé con diecisiete objetos donde debía haber ocho.
Ana Ríos: ¿Y qué pasó con los ocho originales?
Jason Sepúlveda: Siguen ahí. La segunda corrida no los reemplazó, los acompañó. El bundle acumula, no sustituye.
Gustavo Martínez: Eso es peor que un duplicado exacto, porque los dos conjuntos difieren un poco y no sabés cuál es el bueno.
Ana Ríos: ¿Qué define que sea "el mismo archivo"?
Jason Sepúlveda: Ahí está la parte difícil. Puede ser la ruta, pueden ser los bytes, puede ser un identificador declarado en el documento.
Gustavo Martínez: La ruta es frágil. Muevo el archivo de carpeta y se convierte en un origen nuevo.
Jason Sepúlveda: Los bytes son demasiado estrictos. Corrijo una falta de ortografía en la transcripción y ya es otro origen.
Ana Ríos: Entonces ninguna de las dos sola.
Gustavo Martínez: Propongo que la identidad del origen sea explícita: el documento declara de qué origen viene, y esa declaración es lo que decide si una ingesta es nueva o una repetición.
Jason Sepúlveda: Con un problema: los documentos que ya están cargados no la declaran.
Gustavo Martínez: Para esos hay un camino heredado. Si no hay declaración, se compara por nombre de archivo, y si los bytes difieren se pide desambiguar en vez de adivinar.
Ana Ríos: Pedir en vez de adivinar. Me sirve como regla general.
Jason Sepúlveda: Hay un caso más que quiero cubrir. Dos archivos distintos que contienen la misma reunión, por ejemplo una transcripción automática y una corregida a mano.
Gustavo Martínez: Ese caso no lo resuelve la idempotencia. Ese caso es fusión, y es otro tema.
Ana Ríos: Lo dejamos fuera de esta reunión y lo anoto como pendiente.
Gustavo Martínez: Volviendo a la curación: quiero que quede claro que el consentimiento es sobre la escritura, no sobre la lectura. Extraer no publica.
Jason Sepúlveda: Es una distinción importante. La extracción puede correr sola, de noche, sobre cien archivos. Lo que no puede correr sola es la publicación.
Ana Ríos: ¿Y el costo? Si la extracción corre sobre cien archivos y después nadie cura, gastamos por nada.
Gustavo Martínez: Por eso la cola de curación tiene que ser visible. Si hay trescientas propuestas sin revisar, eso se ve.
Jason Sepúlveda: Y el estimador de costo tiene que decir cuánto va a costar antes de correr, no después.
Ana Ríos: Anotado como requisito, aunque no como acuerdo de hoy.
Gustavo Martínez: Un detalle operativo: ¿qué pasa con una propuesta rechazada? ¿Se vuelve a proponer la próxima vez?
Jason Sepúlveda: No debería. Un rechazo es información y hay que guardarla.
Gustavo Martínez: Entonces el rechazo también se registra. De acuerdo.
Ana Ríos: Último punto de la agenda: responsables.
Gustavo Martínez: Tenemos el corpus antiguo, unas ochenta transcripciones de los dos años previos, que están en un formato que ya no usamos.
Ana Ríos: ¿Alguien lo está mirando?
Gustavo Martínez: Nadie, y por eso quiero asignarlo explícitamente. Bruno queda como responsable de la migración del corpus antiguo.
Ana Ríos: ¿Bruno está de acuerdo?
Gustavo Martínez: Lo hablé con él ayer. Está de acuerdo y pidió dos semanas para el diagnóstico antes de comprometer una fecha de migración.
Jason Sepúlveda: Razonable. Que el diagnóstico incluya cuántos de esos archivos son duplicados entre sí, porque sospecho que son muchos.
Gustavo Martínez: Se lo paso.
Ana Ríos: Cierro con los acuerdos.

Cierre y acuerdos de la reunión 02:
Ana Ríos: Primer acuerdo: la curación es un paso explícito con consentimiento por ítem antes de publicar cualquier objeto derivado. Cada escritura propuesta se confirma por separado y no hay aplicación masiva silenciosa de cambios al bundle. La propuesta muestra el fragmento de origen para que la confirmación no obligue a leer el documento entero.
Ana Ríos: Segundo acuerdo: la ingesta es idempotente por origen. Ingerir el mismo archivo dos veces no duplica objetos: la identidad del origen decide si una ingesta es nueva o es una repetición. Lo pidió Jason Sepúlveda tras encontrar diecisiete objetos donde debía haber ocho. Para los documentos heredados que no declaran su origen se compara por nombre de archivo y, si los bytes difieren, se pide desambiguar en vez de adivinar.
Ana Ríos: Tercer acuerdo: Bruno queda como responsable de la migración del corpus antiguo, con dos semanas de diagnóstico antes de comprometer fecha.
Ana Ríos: Pendientes anotados: la fusión de dos archivos que describen la misma reunión, y el estimador de costo previo a la extracción. Próxima sesión: sensibilidad y borrado.

"""

_ES_03: Final = """\
Reunión 03 — sensibilidad y borrado
Acuerdos alcanzados: (1) cada documento lleva su nivel de sensibilidad y un documento confidencial no sale hacia un backend remoto salvo excepción local explícita; (2) el borrado barre también los snapshots de fusión, porque los registros de fusión guardan copias del cuerpo absorbido; (3) el borrado es irreversible y queda registrado, y el registro guarda el acto pero nunca el contenido borrado.
Asistentes: Ana Ríos (facilitadora), Gustavo Martínez (ingeniería de datos), Jason Sepúlveda (arquitectura).
Agenda: (1) datos personales en las transcripciones, (2) nivel de sensibilidad, (3) qué significa borrar de verdad, (4) snapshots de fusión, (5) registro e irreversibilidad, (6) acuerdos.

Ana Ríos: Tercera sesión. Jason pidió el tema, así que empieza él.
Jason Sepúlveda: Tenemos datos personales en las transcripciones y hasta ahora los hemos tratado como si fueran texto cualquiera.
Ana Ríos: ¿Qué tipo de datos?
Jason Sepúlveda: Nombres, sí, pero eso es lo menos. Hay evaluaciones de desempeño mencionadas al pasar, hay una discusión sobre una salida del equipo, hay números de contrato.
Gustavo Martínez: Y todo eso está indexado y es recuperable por cualquiera que haga una pregunta cercana.
Jason Sepúlveda: Necesitamos un nivel de sensibilidad por documento.
Ana Ríos: ¿Por documento y no por fragmento?
Jason Sepúlveda: Por documento, para empezar. Por fragmento es lo correcto a largo plazo y es mucho más caro de hacer bien.
Gustavo Martínez: Estoy de acuerdo en empezar por documento. Un nivel por fragmento que se equivoca es peor que un nivel por documento que es conservador.
Ana Ríos: ¿Qué niveles?
Jason Sepúlveda: Con dos alcanza al principio: privado y confidencial. Privado es el caso normal, no sale del equipo. Confidencial no sale de la máquina.
Gustavo Martínez: ¿Y qué significa "no sale de la máquina" en la práctica?
Jason Sepúlveda: Que un documento confidencial no se envía a un backend remoto. Si el modelo corre local, no hay problema. Si corre en un servicio externo, ese documento queda fuera del contexto.
Ana Ríos: Eso puede degradar la respuesta sin que el usuario se entere.
Jason Sepúlveda: Tiene que enterarse. Si algo quedó fuera por sensibilidad, la respuesta lo dice.
Gustavo Martínez: ¿Y si el usuario sabe que su backend es local y quiere incluirlos igual?
Jason Sepúlveda: Entonces hay una excepción local explícita. El usuario afirma que el backend que se va a alcanzar es verificablemente esta máquina, y con esa afirmación el filtro no tiene nada que proteger.
Ana Ríos: Explícita quiere decir que la pide cada vez, no que se configura y se olvida.
Jason Sepúlveda: Explícita quiere decir que es una decisión visible. Puede vivir en la configuración del espacio de trabajo, pero tiene que estar escrita en algún lado que alguien pueda revisar.
Gustavo Martínez: Anotado. Pasemos al borrado, que es el punto que más me preocupa.
Ana Ríos: A mí también, y quiero plantearlo como lo va a plantear un usuario. Si alguien pide que lo olviden, ¿qué hacemos hoy?
Gustavo Martínez: Hoy borramos el archivo.
Ana Ríos: Y eso no alcanza.
Gustavo Martínez: No alcanza ni de cerca. El archivo es una de las copias.
Jason Sepúlveda: Enumeremos las copias, porque si no las enumeramos vamos a olvidar una.
Gustavo Martínez: Está el archivo original en el bundle. Está el índice léxico. Está el índice vectorial, que guarda el texto además del vector. Está el grafo.
Jason Sepúlveda: Y están los registros de fusión.
Ana Ríos: Explicá esos, porque son los que nadie ve.
Jason Sepúlveda: Cuando dos conceptos se fusionan, el registro de la fusión guarda un snapshot del cuerpo absorbido para poder deshacerla. Ese snapshot es una copia completa del texto.
Gustavo Martínez: Y vive en un archivo distinto, con otro nombre, que nadie asocia con el documento original.
Ana Ríos: Entonces el dato sobrevive exactamente donde nadie mira.
Jason Sepúlveda: Esa es la frase. El barrido tiene que llegar a los snapshots o el dato sobrevive donde nadie mira.
Gustavo Martínez: Hay un nivel más. La base de datos del índice vectorial es SQLite. Un DELETE en SQLite marca la fila como libre pero no reescribe la página.
Ana Ríos: ¿Los bytes siguen en el archivo?
Gustavo Martínez: Los bytes siguen en el archivo hasta que se compacta. Y si hay un write-ahead log, siguen también ahí.
Jason Sepúlveda: Entonces el borrado real incluye compactar y hacer checkpoint del log, y verificar que el checkpoint efectivamente ocurrió.
Gustavo Martínez: Verificar es la palabra clave. El checkpoint puede fallar porque la base está ocupada y no siempre avisa.
Jason Sepúlveda: Si no verificamos, tenemos un borrado que reporta éxito y deja los bytes.
Ana Ríos: Eso es peor que no borrar, porque le decimos a la persona que ya está.
Gustavo Martínez: De acuerdo. Verificación obligatoria.
Jason Sepúlveda: Y el borrado tiene que ser irreversible y quedar registrado.
Ana Ríos: Las dos cosas juntas suenan contradictorias. Si queda registro, ¿no queda el dato?
Jason Sepúlveda: Queda el registro del acto, no del contenido. Fecha, qué documento, quién lo pidió, qué almacenes se barrieron. Nunca el texto borrado.
Gustavo Martínez: Y el registro es lo que te permite responder si alguien pregunta más adelante si el pedido se cumplió.
Ana Ríos: ¿Irreversible significa que no hay papelera?
Jason Sepúlveda: Significa que no hay papelera. Una papelera es una copia más y estaríamos otra vez en la misma discusión.
Gustavo Martínez: Entonces la confirmación previa tiene que ser fuerte, porque no hay vuelta atrás.
Jason Sepúlveda: Fuerte y específica: que diga qué se va a borrar y de qué almacenes, no un "¿estás seguro?" genérico.
Ana Ríos: Que nombre cada almacén. Si barre tres y sólo nombra uno, el usuario no sabe qué perdió.
Gustavo Martínez: Anotado, y creo que hoy nombramos uno solo. Lo reviso.
Jason Sepúlveda: Un último caso: el documento borrado puede estar citado como respaldo de un concepto derivado.
Ana Ríos: ¿Y entonces el concepto queda con una cita rota?
Jason Sepúlveda: Queda con una cita rota, que es visible, o lo borramos también, que es invisible. Prefiero la cita rota.
Gustavo Martínez: Coincido. Una cita rota es un problema que se ve.
Ana Ríos: Cierro con los acuerdos.

Cierre y acuerdos de la reunión 03:
Ana Ríos: Primer acuerdo: cada documento lleva su nivel de sensibilidad. Privado es el caso normal; un documento confidencial no sale hacia un backend remoto salvo excepción local explícita, y si algo queda fuera del contexto por sensibilidad la respuesta lo dice.
Ana Ríos: Segundo acuerdo: el borrado barre también los snapshots de fusión. Borrar un concepto no es borrar su archivo: los registros de fusión guardan snapshots del cuerpo absorbido y el barrido tiene que llegar a ellos o el dato sobrevive donde nadie mira. Incluye compactar la base y verificar que el checkpoint del log ocurrió, porque un checkpoint fallido puede no avisar.
Ana Ríos: Tercer acuerdo: el borrado es irreversible y queda registrado. El registro guarda el acto —fecha, documento, quién lo pidió, qué almacenes se barrieron— y nunca el contenido borrado. No hay papelera, así que la confirmación previa nombra cada almacén que se va a barrer.
Ana Ríos: Pendiente: qué hacer con las citas rotas que deja un documento borrado; la preferencia expresada es dejarlas visibles antes que borrar en cascada.

"""

_EN_01: Final = """\
Meeting 01 — knowledge compiler traceability
Agreements reached: (1) the compiler keeps the sequential history of every decision taken, not only the final state, proposed by Gustavo Martínez; (2) every answer the engine gives carries mandatory verbatim citations to the documents that support it, a condition set by Jason Sepúlveda, because without them a grounded answer cannot be told from a hallucination; (3) the file bundle is the canonical source and derived indexes are rebuildable cache, never the truth.
Attending: Ana Ríos (facilitator), Gustavo Martínez (data engineering), Jason Sepúlveda (architecture).
Agenda: (1) the traceability problem, (2) decision history, (3) verbatim citations in answers, (4) which object is the source of truth, (5) agreements.

Ana Ríos: Opening the first session. The topic is the knowledge compiler, and I want us to leave with agreements, not with a wish list.
Gustavo Martínez: Before we start, let me give the context for why I asked for this meeting. Last week someone asked me why we had chosen to keep attachments outside the repository, and I could not answer.
Gustavo Martínez: I found the outcome of the decision, yes. It is in the configuration, it is in the code, it works. What I did not find was the reasoning.
Ana Ríos: What about the notes from the meeting where it was decided?
Gustavo Martínez: The notes say "agreed to move the attachments". Five words. They do not say who proposed it, what alternative was discarded, or what condition was attached.
Jason Sepúlveda: That is exactly what worries me about the compiler as it is designed. It compiles the final state of the knowledge and discards the path.
Jason Sepúlveda: And the path is half the value. A team that cannot reconstruct why it decided something re-argues it every six months.
Ana Ríos: I agree with the diagnosis. Let us talk about the concrete shape.
Gustavo Martínez: My proposal is that the compiler keep the sequential history of every decision, not only the final state. Every decision with its date, its proposer and its conditions.
Ana Ríos: Sequential in the sense that you can read it in order and see the evolution.
Gustavo Martínez: Exactly. If a decision replaces another, the earlier one is not deleted: it is marked superseded, with a pointer to the one that replaced it.
Jason Sepúlveda: I have a practical objection there. If nothing is ever deleted, the bundle grows without a ceiling and search fills up with historical noise.
Gustavo Martínez: That is a real objection. But the growth is linear in decisions, not in documents. We are talking about a few dozen records per quarter, not thousands.
Jason Sepúlveda: And search?
Gustavo Martínez: Search filters by status. A superseded decision does not appear in results by default; you have to ask for it explicitly.
Ana Ríos: That settles it for me. Auditing is a mode, not the normal mode.
Jason Sepúlveda: With that filter I withdraw the objection. I would rather pay the growth than lose the why.
Ana Ríos: Then I propose the compiler keep the sequential history of every decision, not just the final state. Objections?
Gustavo Martínez: None.
Jason Sepúlveda: Agreed, but I am adding a condition and I want it recorded as a condition, not as a comment.
Ana Ríos: Go ahead.
Jason Sepúlveda: Every answer the engine gives must carry verbatim citations to the documents that support it. Without that we cannot tell a grounded answer from a hallucination.
Ana Ríos: Explain why you put it as a condition of this decision rather than as a separate topic.
Jason Sepúlveda: Because the history is only useful if it can be verified. If the engine tells me "this was decided in March" and does not show me where it got that, the history is a second source of unsupported claims.
Gustavo Martínez: So the traceability of the decision and the traceability of the answer are the same problem.
Jason Sepúlveda: They are the same problem seen from two sides. One is "why did the knowledge end up like this". The other is "where did what you just told me come from".
Ana Ríos: Verbatim in what sense? Is an identifier enough?
Jason Sepúlveda: No. An identifier is a promise. I want the fragment, the text as it stands in the document, so I can compare it against the claim.
Gustavo Martínez: That makes the answer more expensive. The fragment takes room on the screen and in the prompt.
Jason Sepúlveda: I accept that. It is cheaper than a decision taken on an invented citation.
Ana Ríos: What happens when the answer rests on no document at all?
Jason Sepúlveda: Then the answer has to say so. "There is nothing in the bundle about this" is a correct answer. Inventing is not.
Gustavo Martínez: And if the model answers from its own knowledge without noticing, how do we detect it?
Jason Sepúlveda: That is why the citations have to be an explicit declaration by the model, not a list the system assembles on its own. The system may retrieve five documents and the model use two.
Ana Ríos: So the model declares which ones it used.
Jason Sepúlveda: The model declares which ones it used, and the system cites exactly those. If the model declares nothing, we fall back to the conservative behavior and cite everything retrieved, marking it as unverified.
Gustavo Martínez: I like that the degraded case is loud rather than silent.
Ana Ríos: Noted. Let us move to the fourth item on the agenda.
Gustavo Martínez: I want to put on record something that is implicit today and will be an argument tomorrow: the file bundle is the canonical source. No derived index gets to become the truth.
Ana Ríos: What exactly worries you?
Gustavo Martínez: The usual pattern worries me. An index is built to speed up search, someone adds a field to it that is not in the files, and three months later the index holds information that exists nowhere else.
Jason Sepúlveda: And then deleting it stops being safe.
Gustavo Martínez: And then deleting it stops being safe, which is precisely what a cache has to be: deletable.
Ana Ríos: Which indexes do we have today?
Gustavo Martínez: Lexical search, vectors and the graph. All three are rebuildable from the files. I want that to be a rule, not a coincidence.
Jason Sepúlveda: The operational test is simple: if I delete the index directory and reindex, do I lose anything?
Gustavo Martínez: Today I lose nothing. I want that to hold tomorrow too.
Ana Ríos: How do we guarantee it?
Jason Sepúlveda: With a test that exercises it. Delete, rebuild and compare. If anything changed, the index held state of its own.
Gustavo Martínez: And with a review rule: any new field in the index must have an origin in the files.
Ana Ríos: Both. The test detects and the rule prevents.
Jason Sepúlveda: There is an edge case I want to name. Embeddings depend on the model. If I change the model, the rebuilt index is not byte-for-byte identical to the previous one.
Gustavo Martínez: Correct, but it is identical in retrievable content. The rule is about information, not about bytes.
Jason Sepúlveda: Then let us word it this way: no index holds information that cannot be derived from the files.
Ana Ríos: That wording works for me. Coming back to citations, I have a question about form.
Ana Ríos: If the model declares the documents it used, how does it name them? Internal identifiers are long and ugly.
Jason Sepúlveda: Let it not name them. We number the context blocks and the model declares numbers.
Gustavo Martínez: That also stops the identifier from leaking into the prose of the answer, which is a problem we have already seen.
Ana Ríos: Seen where?
Gustavo Martínez: In the first trials. The model copied the block label into the paragraph because the instruction asked it to cite by identifier. It was doing exactly what it was told.
Jason Sepúlveda: With numbers, the vocabulary of attribution stays separate from the vocabulary of prose. There is no way to confuse them.
Ana Ríos: Good. One last point before closing: what do we do if the answer is long and the model omits the attribution line?
Jason Sepúlveda: We fall back to the conservative behavior and we record it. But I want it clear that the fallback is a defect to be measured, not an acceptable state.
Gustavo Martínez: Measured how?
Jason Sepúlveda: With a periodic measurement over known questions. If the omission rate rises, that is a regression.
Ana Ríos: Agreed. I will close with the agreements.

Closing agreements of meeting 01:
Ana Ríos: First agreement: the compiler keeps the sequential history of every decision taken, not only the bundle's final state. Gustavo Martínez proposed it and there were no objections once it was clarified that superseded decisions stay out of search by default.
Ana Ríos: Second agreement: every answer the engine gives carries mandatory verbatim citations to the documents that support it. This is the condition Jason Sepúlveda set in this meeting, and his reason is that without verbatim citations you cannot tell a grounded answer from a hallucination. The model declares which blocks it used and the system cites exactly those; if it declares nothing, everything retrieved is cited and marked unverified.
Ana Ríos: Third agreement: the file bundle is the canonical source. Derived indexes — lexical search, vectors and the graph — are rebuildable cache and never the truth. No index holds information that cannot be derived from the files, and that is verified by deleting and rebuilding.
Ana Ríos: Left for the next session: ingestion and curation. Gustavo brings the state of the transcripts.

"""

_EN_02: Final = """\
Meeting 02 — ingestion and curation
Agreements reached: (1) curation is an explicit step with per-item consent before publishing any derived object; (2) ingestion is idempotent per origin, so ingesting the same file twice does not duplicate objects and the origin's identity decides whether an ingestion is new or a repetition, a condition set by Jason Sepúlveda; (3) Bruno is assigned as the owner of the old corpus migration.
Attending: Ana Ríos (facilitator), Gustavo Martínez (data engineering), Jason Sepúlveda (architecture).
Agenda: (1) state of the transcripts, (2) quality review, (3) explicit curation, (4) ingestion idempotency, (5) owners, (6) agreements.

Ana Ríos: Second session. Gustavo, start with the state.
Gustavo Martínez: I processed the transcripts from the last four sessions and they are loaded into the bundle. Four files, about two hundred kilobytes in total.
Ana Ríos: And the quality review?
Gustavo Martínez: Missing. And it is not missing by oversight: it is missing because the step does not exist. Today ingestion writes straight through.
Jason Sepúlveda: Straight through means that if extraction gets something wrong, the error lands in the bundle as if it were verified knowledge.
Gustavo Martínez: Exactly. And with last session's agreement that is worse, because the erroneous object can now also be cited as support for an answer.
Ana Ríos: So traceability amplifies the quality problem instead of solving it.
Jason Sepúlveda: Traceability tells you where something came from. It does not tell you whether what came out is right.
Gustavo Martínez: My proposal is that curation be an explicit step, with per-item consent, before publishing any derived object.
Ana Ríos: Per item. Define it precisely, because that is where the whole argument lives.
Gustavo Martínez: Every proposed write is confirmed separately. If extraction proposes twelve concepts, the user sees twelve proposals and accepts or rejects each one.
Jason Sepúlveda: And there is no "accept all"?
Gustavo Martínez: There may be one as a convenience, but it cannot be the default path and it cannot be silent. Silent mass application of changes to the bundle is exactly what I want to forbid.
Ana Ríos: My concern is friction. Twelve confirmations per file is a lot.
Gustavo Martínez: It is a lot the first time. After the first time the user knows which kind of proposal is usually right and which is not.
Jason Sepúlveda: And there is a design point that lowers friction without breaking the rule: the proposal has to show the source fragment beside it. If I can see where it came from, I decide in two seconds.
Ana Ríos: That convinces me. The expensive friction is reading the whole document to verify one proposal.
Gustavo Martínez: I will note then that the proposal shows its origin.
Jason Sepúlveda: I want to add a second condition about ingestion, separate from curation.
Ana Ríos: Go ahead.
Jason Sepúlveda: Ingestion has to be idempotent. If I run the same file twice it must not duplicate objects.
Gustavo Martínez: Today it duplicates.
Jason Sepúlveda: Today it duplicates, and I found out the worst way. I re-ingested a file to test an extraction change and ended up with seventeen objects where there should have been eight.
Ana Ríos: And what happened to the original eight?
Jason Sepúlveda: They are still there. The second run did not replace them, it joined them. The bundle accumulates, it does not substitute.
Gustavo Martínez: That is worse than an exact duplicate, because the two sets differ slightly and you cannot tell which is the good one.
Ana Ríos: What defines "the same file"?
Jason Sepúlveda: That is the hard part. It could be the path, it could be the bytes, it could be an identifier declared in the document.
Gustavo Martínez: The path is fragile. I move the file to another folder and it becomes a new origin.
Jason Sepúlveda: The bytes are too strict. I fix a typo in the transcript and it is already another origin.
Ana Ríos: So neither one alone.
Gustavo Martínez: I propose the origin's identity be explicit: the document declares which origin it comes from, and that declaration is what decides whether an ingestion is new or a repetition.
Jason Sepúlveda: With one problem: the documents already loaded do not declare it.
Gustavo Martínez: For those there is a legacy path. With no declaration, compare by file name, and if the bytes differ ask to disambiguate rather than guess.
Ana Ríos: Ask rather than guess. That works for me as a general rule.
Jason Sepúlveda: There is one more case I want to cover. Two different files holding the same meeting, for instance an automatic transcript and a hand-corrected one.
Gustavo Martínez: Idempotency does not solve that case. That case is merging, and it is another topic.
Ana Ríos: We leave it out of this meeting and I note it as pending.
Gustavo Martínez: Back to curation: I want it clear that consent is about the write, not about the read. Extracting does not publish.
Jason Sepúlveda: That is an important distinction. Extraction can run alone, overnight, over a hundred files. What cannot run alone is publication.
Ana Ríos: And the cost? If extraction runs over a hundred files and then nobody curates, we spend for nothing.
Gustavo Martínez: That is why the curation queue has to be visible. If there are three hundred unreviewed proposals, that shows.
Jason Sepúlveda: And the cost estimator has to say what it will cost before running, not after.
Ana Ríos: Noted as a requirement, though not as an agreement today.
Gustavo Martínez: An operational detail: what happens to a rejected proposal? Is it proposed again next time?
Jason Sepúlveda: It should not be. A rejection is information and it has to be kept.
Gustavo Martínez: Then the rejection is recorded too. Agreed.
Ana Ríos: Last item on the agenda: owners.
Gustavo Martínez: We have the old corpus, some eighty transcripts from the previous two years, in a format we no longer use.
Ana Ríos: Is anybody looking at it?
Gustavo Martínez: Nobody, and that is why I want it assigned explicitly. Bruno is assigned as the owner of the old corpus migration.
Ana Ríos: Does Bruno agree?
Gustavo Martínez: I spoke with him yesterday. He agrees and asked for two weeks of diagnosis before committing to a migration date.
Jason Sepúlveda: Reasonable. Let the diagnosis include how many of those files are duplicates of each other, because I suspect there are many.
Gustavo Martínez: I will pass that on.
Ana Ríos: I will close with the agreements.

Closing agreements of meeting 02:
Ana Ríos: First agreement: curation is an explicit step with per-item consent before publishing any derived object. Every proposed write is confirmed separately and there is no silent mass application of changes to the bundle. The proposal shows the source fragment so that confirming does not require reading the whole document.
Ana Ríos: Second agreement: ingestion is idempotent per origin. Ingesting the same file twice does not duplicate objects: the origin's identity decides whether an ingestion is new or a repetition. Jason Sepúlveda asked for it after finding seventeen objects where there should have been eight. For legacy documents that do not declare their origin, compare by file name and, if the bytes differ, ask to disambiguate rather than guess.
Ana Ríos: Third agreement: Bruno is assigned as the owner of the old corpus migration, with two weeks of diagnosis before committing to a date.
Ana Ríos: Pending items noted: merging two files that describe the same meeting, and the cost estimator ahead of extraction. Next session: sensitivity and deletion.

"""

_EN_03: Final = """\
Meeting 03 — sensitivity and deletion
Agreements reached: (1) every document carries its sensitivity level and a confidential document does not leave toward a remote backend except by explicit local exemption; (2) deletion also sweeps the merge snapshots, because merge records keep copies of the absorbed body; (3) deletion is irreversible and logged, and the log keeps the act but never the deleted content.
Attending: Ana Ríos (facilitator), Gustavo Martínez (data engineering), Jason Sepúlveda (architecture).
Agenda: (1) personal data in the transcripts, (2) sensitivity level, (3) what real deletion means, (4) merge snapshots, (5) logging and irreversibility, (6) agreements.

Ana Ríos: Third session. Jason asked for the topic, so he starts.
Jason Sepúlveda: We have personal data in the transcripts and so far we have treated it like any other text.
Ana Ríos: What kind of data?
Jason Sepúlveda: Names, yes, but that is the least of it. There are performance reviews mentioned in passing, there is a discussion about someone leaving the team, there are contract numbers.
Gustavo Martínez: And all of that is indexed and retrievable by anyone who asks a nearby question.
Jason Sepúlveda: We need a sensitivity level per document.
Ana Ríos: Per document and not per fragment?
Jason Sepúlveda: Per document, to begin with. Per fragment is the right thing long term and it is far more expensive to do well.
Gustavo Martínez: I agree with starting per document. A per-fragment level that gets it wrong is worse than a per-document level that is conservative.
Ana Ríos: Which levels?
Jason Sepúlveda: Two are enough at first: private and confidential. Private is the normal case, it does not leave the team. Confidential does not leave the machine.
Gustavo Martínez: And what does "does not leave the machine" mean in practice?
Jason Sepúlveda: That a confidential document is not sent to a remote backend. If the model runs locally, there is no problem. If it runs on an external service, that document stays out of the context.
Ana Ríos: That can degrade the answer without the user noticing.
Jason Sepúlveda: They have to notice. If something was left out for sensitivity, the answer says so.
Gustavo Martínez: And if the user knows their backend is local and wants them included anyway?
Jason Sepúlveda: Then there is an explicit local exemption. The user asserts that the backend to be reached is verifiably this machine, and with that assertion the filter has nothing to protect.
Ana Ríos: Explicit means it is asked for each time, not configured and forgotten.
Jason Sepúlveda: Explicit means it is a visible decision. It may live in the workspace configuration, but it has to be written somewhere a person can review.
Gustavo Martínez: Noted. Let us move to deletion, which is the point that worries me most.
Ana Ríos: Me too, and I want to frame it the way a user will frame it. If someone asks to be forgotten, what do we do today?
Gustavo Martínez: Today we delete the file.
Ana Ríos: And that is not enough.
Gustavo Martínez: It is nowhere near enough. The file is one of the copies.
Jason Sepúlveda: Let us enumerate the copies, because if we do not enumerate them we will forget one.
Gustavo Martínez: There is the original file in the bundle. There is the lexical index. There is the vector index, which stores the text as well as the vector. There is the graph.
Jason Sepúlveda: And there are the merge records.
Ana Ríos: Explain those, because they are the ones nobody sees.
Jason Sepúlveda: When two concepts are merged, the merge record keeps a snapshot of the absorbed body so the merge can be undone. That snapshot is a complete copy of the text.
Gustavo Martínez: And it lives in a different file, under another name, that nobody associates with the original document.
Ana Ríos: So the data survives exactly where nobody looks.
Jason Sepúlveda: That is the sentence. The sweep has to reach the snapshots or the data survives where nobody looks.
Gustavo Martínez: There is one more level. The vector index database is SQLite. A DELETE in SQLite marks the row free but does not rewrite the page.
Ana Ríos: The bytes are still in the file?
Gustavo Martínez: The bytes are still in the file until it is compacted. And if there is a write-ahead log, they are still there too.
Jason Sepúlveda: So real deletion includes compacting and checkpointing the log, and verifying that the checkpoint actually happened.
Gustavo Martínez: Verifying is the key word. The checkpoint can fail because the database is busy, and it does not always say so.
Jason Sepúlveda: If we do not verify, we have a deletion that reports success and leaves the bytes.
Ana Ríos: That is worse than not deleting, because we tell the person it is done.
Gustavo Martínez: Agreed. Verification is mandatory.
Jason Sepúlveda: And deletion has to be irreversible and logged.
Ana Ríos: Those two together sound contradictory. If a log remains, does the data not remain?
Jason Sepúlveda: What remains is the record of the act, not of the content. Date, which document, who asked, which stores were swept. Never the deleted text.
Gustavo Martínez: And the log is what lets you answer later if someone asks whether the request was carried out.
Ana Ríos: Does irreversible mean there is no recycle bin?
Jason Sepúlveda: It means there is no recycle bin. A recycle bin is one more copy and we would be back in the same argument.
Gustavo Martínez: Then the prior confirmation has to be strong, because there is no way back.
Jason Sepúlveda: Strong and specific: it should say what is going to be deleted and from which stores, not a generic "are you sure?".
Ana Ríos: It should name each store. If it sweeps three and names only one, the user does not know what they lost.
Gustavo Martínez: Noted, and I believe today we name only one. I will check.
Jason Sepúlveda: One last case: the deleted document may be cited as support for a derived concept.
Ana Ríos: And then the concept is left with a broken citation?
Jason Sepúlveda: Left with a broken citation, which is visible, or we delete it too, which is invisible. I prefer the broken citation.
Gustavo Martínez: I agree. A broken citation is a problem you can see.
Ana Ríos: I will close with the agreements.

Closing agreements of meeting 03:
Ana Ríos: First agreement: every document carries its sensitivity level. Private is the normal case; a confidential document does not leave toward a remote backend except by explicit local exemption, and if something is left out of the context for sensitivity the answer says so.
Ana Ríos: Second agreement: deletion also sweeps the merge snapshots. Deleting a concept is not deleting its file: merge records keep snapshots of the absorbed body and the sweep has to reach them or the data survives where nobody looks. It includes compacting the database and verifying that the log checkpoint happened, because a failed checkpoint may not report itself.
Ana Ríos: Third agreement: deletion is irreversible and logged. The log keeps the act — date, document, who asked, which stores were swept — and never the deleted content. There is no recycle bin, so the prior confirmation names every store that will be swept.
Ana Ríos: Pending: what to do with the broken citations a deleted document leaves behind; the preference expressed is to leave them visible rather than delete in cascade.

"""

LARGE_SOURCES_ES: Final[dict[str, tuple[str, str]]] = {
    "sources/reunion-01-trazabilidad": (
        "Reunión 01 — trazabilidad del compilador",
        _ES_01,
    ),
    "sources/reunion-02-ingesta": (
        "Reunión 02 — ingesta y curación",
        _ES_02,
    ),
    "sources/reunion-03-privacidad": (
        "Reunión 03 — sensibilidad y borrado",
        _ES_03,
    ),
}

LARGE_SOURCES_EN: Final[dict[str, tuple[str, str]]] = {
    "sources/meeting-01-traceability": (
        "Meeting 01 — compiler traceability",
        _EN_01,
    ),
    "sources/meeting-02-ingestion": (
        "Meeting 02 — ingestion and curation",
        _EN_02,
    ),
    "sources/meeting-03-privacy": (
        "Meeting 03 — sensitivity and deletion",
        _EN_03,
    ),
}

LARGE_SOURCES_BY_LANGUAGE: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "es": LARGE_SOURCES_ES,
    "en": LARGE_SOURCES_EN,
}
"""Keyed by language. Replaces the small rung's `sources/*` documents
one-for-one -- same ids, same titles -- so the two rungs differ in the
BODY of three documents and in nothing else."""
