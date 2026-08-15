# Reunión de plataforma — revisión quincenal

Elena Vidal: Buenos días. Partimos con la revisión quincenal de plataforma. Yo coordino el equipo de infraestructura y voy a llevar la minuta hoy.

Marcos Iturra: Buenos días. Marcos, ingeniero de datos, a cargo del almacenamiento y de los respaldos desde el trimestre pasado.

Paula Cifuentes: Hola. Paula, encargada de seguridad de la información. Vengo del área de cumplimiento y me sumé al equipo en marzo.

Tomás Reyes: Buenos días a todos. Tomás, desarrollador del motor de búsqueda. Trabajo la parte de recuperación e índices.

Elena Vidal: Perfecto. El orden del día tiene cuatro puntos: el incidente de la semana pasada, la latencia de búsqueda, los respaldos y la rotación de credenciales. Partamos por el incidente.

Marcos Iturra: El martes pasado el servicio estuvo caído dos horas y cuarenta minutos, entre las nueve y las once cuarenta de la mañana. Afectó a todas las consultas de los usuarios, no solo a las escrituras.

Tomás Reyes: La causa fue el disco de la máquina que hospeda el índice. Se llenó porque los archivos temporales de la reconstrucción no se estaban borrando después de cada corrida.

Marcos Iturra: Confirmo. Encontramos ciento ochenta gigabytes de temporales acumulados de tres meses. El proceso los creaba y nunca los limpiaba, y nadie miraba ese volumen porque no estaba en el panel.

Paula Cifuentes: ¿Hubo pérdida de datos durante la caída?

Marcos Iturra: No. El índice se pudo reconstruir completo desde los documentos originales. Perdimos disponibilidad, no información. Pero estuvimos ciegos las dos horas porque las alertas tampoco llegaron.

Elena Vidal: Ese es el punto que más me preocupa del incidente. No fue solo el disco, fue que nos enteramos por un usuario y no por el monitoreo.

Tomás Reyes: Las alertas de disco estaban configuradas al noventa y cinco por ciento de ocupación. El disco pasó de ochenta y ocho a lleno en menos de veinte minutos, así que la alerta se disparó cuando ya estaba caído.

Paula Cifuentes: Entonces el umbral está mal elegido para la velocidad real de llenado. No es que faltara la alerta, es que llegaba tarde por diseño.

Elena Vidal: De acuerdo. Lo dejamos anotado como aprendizaje del incidente y lo tratamos en la revisión de monitoreo, que es otra reunión. Pasemos a la latencia.

Tomás Reyes: La búsqueda vectorial está tardando bastante más de lo que debería. La mediana de una consulta está en ochocientos milisegundos y el percentil noventa y cinco se va a los tres segundos.

Elena Vidal: ¿Contra qué lo comparas?

Tomás Reyes: Contra lo que medimos en enero, que era ciento veinte milisegundos de mediana con el mismo corpus. El corpus creció, sí, pero creció al doble y la latencia se multiplicó por siete.

Paula Cifuentes: Eso no escala linealmente entonces. ¿Sabes dónde se va el tiempo?

Tomás Reyes: Mayormente en la comparación de vectores. No estamos usando ningún índice aproximado, hacemos comparación exhaustiva contra todos los vectores del corpus. Con cien mil documentos eso ya no da.

Marcos Iturra: ¿Y cuánto costaría cambiar a un índice aproximado?

Tomás Reyes: El cambio en sí es acotado, un par de semanas. Lo que hay que decidir es cuánta pérdida de exactitud aceptamos, porque un índice aproximado por definición no devuelve siempre los mismos vecinos que la búsqueda exhaustiva.

Elena Vidal: Eso necesita una medición antes que una decisión. No podemos elegir un umbral de exactitud sin saber qué estamos perdiendo hoy.

Tomás Reyes: Estoy de acuerdo. Puedo armar el conjunto de consultas de control y medir ambos caminos sobre el mismo corpus.

Elena Vidal: Hagámoslo así. La latencia queda como problema abierto con una medición pendiente, no como decisión tomada hoy.

Paula Cifuentes: Quiero levantar algo relacionado pero distinto. El modelo de embeddings que usamos cambió de versión en junio y nadie volvió a generar los vectores antiguos.

Tomás Reyes: Es cierto. Tenemos vectores de dos versiones distintas del modelo conviviendo en la misma base.

Paula Cifuentes: Entonces las distancias entre un vector viejo y uno nuevo no significan lo mismo que entre dos de la misma versión. Estamos comparando cosas que no son comparables.

Tomás Reyes: Sí, y eso explicaría parte de las citas incorrectas que reportaron los usuarios en julio. No todo, pero parte.

Elena Vidal: ¿Cuántos documentos están con la versión antigua?

Tomás Reyes: Alrededor del cuarenta por ciento del corpus. Los que se ingirieron antes de junio.

Marcos Iturra: Regenerar el cuarenta por ciento no es gratis, pero tampoco es enorme. Estimo unas seis horas de cómputo.

Elena Vidal: Anotado. La deriva del modelo de embeddings es un problema real y separado de la latencia, aunque los dos se noten en la misma consulta. No lo mezclemos.

Paula Cifuentes: Además hay un tema de trazabilidad. Hoy la base no guarda con qué versión del modelo se generó cada vector, así que ni siquiera sabemos cuáles regenerar sin inferirlo por fecha de ingesta.

Tomás Reyes: Correcto. Inferirlo por fecha funciona esta vez porque el cambio fue limpio, pero no va a funcionar la próxima.

Elena Vidal: Antes de seguir, quiero cerrar el punto de la deriva con algo concreto, porque si no vuelve a aparecer en dos semanas igual que hoy.

Tomás Reyes: Lo concreto sería guardar la etiqueta del modelo junto a cada vector. Es una columna más en la tabla y resuelve el problema de saber cuáles regenerar.

Paula Cifuentes: Y permite detectar la próxima vez que alguien cambie de versión sin avisar, que es el fondo del asunto. Hoy el cambio fue deliberado; el riesgo es el cambio que nadie note.

Marcos Iturra: Con la etiqueta guardada, además, la regeneración puede ser incremental. Se regeneran solo los que tienen la etiqueta antigua en vez de rehacer todo por si acaso.

Elena Vidal: Entonces la deriva del modelo de embeddings tiene dos mitades: la limpieza de lo que ya está mal, que son seis horas de cómputo, y la prevención, que es guardar la versión. La segunda es la que evita que se repita.

Tomás Reyes: Yo llevaría las dos juntas, porque si regeneramos sin guardar la etiqueta quedamos exactamente igual de ciegos para la próxima.

Elena Vidal: De acuerdo, pero eso sigue siendo trabajo por estimar, no una decisión de hoy. Lo dejo abierto con esa nota.

Elena Vidal: Hay un tema más que no está en el orden del día y que Tomás me pidió incluir.

Tomás Reyes: Sí, gracias. Es sobre los documentos duplicados en el corpus. Cuando ingerimos un documento largo, el sistema lo parte en trozos y cada trozo se procesa por separado.

Tomás Reyes: El problema es que si dos trozos hablan del mismo asunto, cada uno crea su propio objeto con un nombre ligeramente distinto, y después quedan los dos en el corpus como si fueran cosas diferentes.

Paula Cifuentes: ¿Tienes ejemplos concretos?

Tomás Reyes: Varios. En la última ingesta quedaron por separado un objeto llamado "latencia del índice" y otro llamado "latencia en las consultas del índice", que son exactamente lo mismo dicho de dos maneras.

Marcos Iturra: ¿Y eso se detecta después?

Tomás Reyes: Se detecta, pero después, con una pasada de deduplicación que cuesta tiempo y que a veces junta cosas que no debía. Sale más barato no producir el duplicado que salir a cazarlo.

Elena Vidal: ¿Por qué se produce? ¿Es que el modelo se equivoca?

Tomás Reyes: No exactamente. Es que cada trozo se procesa a ciegas respecto de los demás. El que procesa el segundo trozo no sabe qué nombres puso el primero, así que inventa el suyo. Es estructural, no es ruido del modelo.

Paula Cifuentes: Entonces la solución sería pasarle al segundo trozo la lista de lo que ya se nombró.

Tomás Reyes: Esa es exactamente la idea. Cuesta unas pocas decenas de palabras por llamada y evita el duplicado en el origen.

Elena Vidal: Suena razonable, pero otra vez: hay que medirlo antes de cambiarlo. Anotado como trabajo propuesto, sin decisión.

Elena Vidal: Bien. Pasemos a respaldos, que es el punto de Marcos.

Marcos Iturra: Los respaldos se están tomando todos los días a las dos de la mañana y se guardan treinta días. Eso funciona. El problema es que se guardan sin cifrar.

Paula Cifuentes: Eso es un hallazgo de cumplimiento. Los respaldos contienen los mismos datos personales que la base productiva y la política dice que los datos personales en reposo van cifrados.

Marcos Iturra: No hay discusión de mi lado, hay que cifrarlos. La pregunta es dónde guardamos la llave, porque si la llave vive en la misma máquina que el respaldo no ganamos nada.

Paula Cifuentes: En el gestor de secretos corporativo. Ya está aprobado para este uso y tiene rotación y registro de acceso.

Elena Vidal: ¿Alguien ve un impedimento técnico?

Marcos Iturra: Ninguno. El costo es una hora de trabajo y un poco más de tiempo en cada respaldo.

Elena Vidal: Entonces queda decidido: los respaldos del bundle se cifran en reposo y la llave se custodia en el gestor de secretos corporativo, no en la máquina de respaldo. Marcos lo implementa esta semana.

Marcos Iturra: Anotado.

Paula Cifuentes: Aprovecho el punto para el segundo tema de cumplimiento, que es la retención de los registros de acceso.

Elena Vidal: Adelante.

Paula Cifuentes: Hoy guardamos los registros de acceso para siempre. Nunca se borran. Eso contradice el principio de minimización y además nos deja con datos personales que ya no tenemos motivo para conservar.

Marcos Iturra: ¿Cuál sería el plazo correcto?

Paula Cifuentes: Noventa días cubre lo que necesitamos para investigar un incidente y es lo que aplica el resto de la organización. Más allá de eso solo acumula riesgo.

Tomás Reyes: ¿Y si necesitamos investigar algo más antiguo?

Paula Cifuentes: Para eso están los registros agregados, que no llevan identificadores de persona. Esos sí se pueden conservar indefinidamente porque ya no son datos personales.

Elena Vidal: Me parece razonable y está alineado con lo que hace el resto de la organización. Queda decidido: los registros de acceso se conservan noventa días y después se eliminan, y los agregados sin identificadores se mantienen sin plazo.

Paula Cifuentes: Perfecto. Yo redacto el cambio en la política y lo circulo.

Elena Vidal: Último punto: rotación de credenciales. Paula, esto también es tuyo.

Paula Cifuentes: Sí. Hoy no tenemos un procedimiento escrito, y eso se nota: cuando rotamos la credencial de la base en abril, el servicio de ingesta se cayó porque nadie sabía que la tenía cacheada.

Marcos Iturra: Me acuerdo. Estuvimos cuarenta minutos buscando qué componente seguía usando la credencial vieja.

Paula Cifuentes: El procedimiento que propongo tiene cuatro pasos y el orden importa. Primero se genera la credencial nueva y se deja activa en paralelo con la anterior.

Paula Cifuentes: Segundo, se actualiza cada componente consumidor uno por uno, verificando después de cada uno que sigue respondiendo.

Paula Cifuentes: Tercero, se espera un ciclo completo de veinticuatro horas con las dos credenciales activas, para que cualquier proceso programado que corra una vez al día también se haya actualizado.

Paula Cifuentes: Y cuarto, recién ahí se revoca la credencial anterior. Nunca antes.

Tomás Reyes: El paso tres es el que nos faltó en abril. Revocamos de inmediato.

Paula Cifuentes: Exacto. Por eso lo pongo explícito y con la espera declarada, no como una recomendación.

Marcos Iturra: Una duda sobre el paso dos. ¿Qué hago si un componente no responde después de actualizarle la credencial?

Paula Cifuentes: Se revierte ese componente a la credencial anterior, que todavía está activa, y se sigue con los demás. Por eso el paso uno deja las dos en paralelo: para que revertir uno sea barato y no haya que detener la rotación entera.

Tomás Reyes: ¿Y el orden entre componentes importa?

Paula Cifuentes: Sí. Primero los que solo leen y al final los que escriben. Si algo va a fallar, que falle en un componente que no está modificando datos.

Elena Vidal: ¿Queda documentado dónde?

Paula Cifuentes: En el manual de operación, junto al procedimiento de respaldo. Lo dejo escrito esta semana.

Elena Vidal: Bien. Recapitulo los acuerdos: se cifran los respaldos con la llave en el gestor de secretos, se fija la retención de registros en noventa días, y se documenta el procedimiento de rotación de credenciales en cuatro pasos.

Elena Vidal: Y quedan dos cosas abiertas que no decidimos hoy: la latencia de la búsqueda vectorial, que necesita la medición de Tomás, y la deriva del modelo de embeddings, que necesita saber cuántos vectores regeneramos y cómo registramos la versión.

Tomás Reyes: Conforme.

Marcos Iturra: Conforme.

Paula Cifuentes: Conforme. Nos vemos en dos semanas.

Elena Vidal: Gracias a todos. Cerramos.
