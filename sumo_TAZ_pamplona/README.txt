**************************************************************************
*******                                                            *******
*******      TUTORIAL CREACIÓN DE ZONAS DE ANÁLISIS DE TRÁFICO     *******
*******    Y GENERACIÓN DE DEMANDA CON MATRICES ORIGEN - DESTINO   *******
*******                                                            *******
**************************************************************************


***************************************
***** 1. CONTENIDO DEL DIRECTORIO *****
***************************************

	==> Ficheros de Salida (Output) <==
	-----------------------------------
Estos ficheros son generados por SUMO como resultado de la ejecución de la simulación.

	==> stats.xml
Fichero de salida en formato XML que contiene estadísticas agregadas sobre la simulación de tráfico.
Proporciona un resumen del rendimiento general de la simulación. Incluye métricas como el número total de vehículos cargados e insertados, el número de vehículos teletransportados (lo que puede indicar problemas en la red), y estadísticas de viaje como la duración media, la longitud de la ruta y el tiempo de espera.
El fichero muestra que en la última ejecución de la simulación: se cargaron 1649 vehículos, se completaron 1009 viajes y hubo 28 teletransportes, principalmente por atascos o por estar en el carril incorrecto.

	==> tripinfos.xml
Fichero XML que registra información detallada sobre cada viaje individual realizado por los vehículos en la simulación.
Ofrece datos específicos para cada vehículo, como su ID, tiempo de salida y llegada, duración del viaje, longitud de la ruta, tiempo perdido (timeLoss), y el número de veces que el vehículo fue redirigido (rerouteNo). Es muy útil para análisis detallados del comportamiento de los vehículos y la eficiencia de las rutas. 
El fichero muestra que en la última ejecución de la simulación: el vehículo con id="46452" tuvo una pérdida de tiempo (timeLoss) muy alta de 306.09 segundos, principalmente debido a un tiempo de espera (waitingTime) de 301 segundos, lo que indica que se encontró con un gran atasco.



	==> Ficheros de Configuración y Ejecución <==
	---------------------------------------------
Estos ficheros se utilizan para definir y ejecutar la simulación.

	==> run.bat
Script de Windows (batch script) que ejecuta la simulación.

	==> osm.sumocfg
Fichero de configuración principal de la simulación de SUMO, en formato XML.
Define todos los parámetros y ficheros de entrada y salida para una simulación. Este fichero especifica:
Entradas (input): El mapa de la red (red_navarra.net.xml), los viajes a simular (odtrips.xml) y los ficheros adicionales como las zonas de tráfico (municipios_navarra.taz.xml).
Salidas (output): Dónde guardar los ficheros de resultados, como tripinfos.xml y stats.xml.
Procesamiento y Comportamiento: Configuraciones sobre cómo gestionar errores de ruta, umbrales de atasco para semáforos, y parámetros de recalculo de rutas para los vehículos.

	==> osm.view.xml
Fichero de configuración de la vista para la interfaz gráfica (GUI) de SUMO.
Almacena las configuraciones visuales de la simulación, como el nivel de zoom, la posición de la cámara, los colores de los vehículos o las calles, y el retardo de la simulación (delay). Esto permite que, cada vez que se abra la simulación en modo gráfico, la vista sea consistente. 
Actualmente, el fichero establece un esquema de color "real world" y un retardo de 20ms.



	==> Ficheros de Red y Demanda de Tráfico <==
	--------------------------------------------
Estos ficheros describen el mapa y los viajes que se realizarán sobre él.

	==> red_navarra.net.xml
Fichero de red de SUMO en formato XML.
Describe la infraestructura vial de la simulación. Contiene toda la información sobre las calles (edges), los cruces (junctions), los semáforos y las conexiones entre ellos. Es el mapa sobre el cual se moverán los vehículos. Este fichero es generado por la herramienta netconvert.

	==> municipios_navarra.taz.xml
Fichero de Zonas de Asignación de Tráfico (TAZ, por sus siglas en inglés), que son polígonos que agrupan un conjunto de calles.
Define las zonas geográficas utilizadas para la generación de la demanda de tráfico. En lugar de definir viajes desde una calle específica a otra, se definen entre zonas (por ejemplo, del barrio A al barrio B), lo que facilita la modelización de la movilidad a gran escala.

	==> osm.xml
Fichero XML que define una matriz Origen-Destino (O-D).
Especifica cuántos viajes se realizan entre las diferentes Zonas de Asignación de Tráfico (TAZ) definidas en municipios_navarra.taz.xml. 
Por ejemplo, una línea como <tazRelation from="ORKOIEN" to="PAMPLONA_04" count="13166" /> indica que se deben generar 13.166 viajes desde la zona "ORKOIEN" hasta la zona "PAMPLONA_04" a lo largo del intervalo de tiempo definido.

	==> odtrips.xml
Fichero de viajes generado a partir de una matriz O-D.
Este fichero es el resultado de la herramienta od2trips, que toma como entrada el fichero osm.xml (la matriz O-D) y el fichero de TAZ (municipios_navarra.taz.xml). od2trips genera viajes individuales, asignando de forma aleatoria una calle de origen y una de destino dentro de las zonas correspondientes para cada uno de los viajes definidos en la matriz.

	==> odtrips_valid.xml
Fichero de rutas y viajes validado, generado por la herramienta duarouter.
Toma como entrada el fichero odtrips.xml y la red (red_navarra.net.xml) y calcula la ruta más corta o rápida para cada viaje. El resultado es un fichero de rutas que SUMO puede utilizar directamente para la simulación, asegurando que todos los viajes tienen una ruta válida en la red.



	==> Ficheros Adicionales y Scripts <==
	--------------------------------------
Estos ficheros son complementarios o no se encuentran en tu contexto actual, pero tienen una función conocida en SUMO.

	==> edgeData.xml
Fichero de salida que contiene datos agregados por cada calle (edge) de la red.
Se utiliza para realizar análisis de tráfico a nivel de calle. Puede configurarse para que SUMO genere datos como la velocidad media, la densidad de vehículos, el número de vehículos que han pasado, o las emisiones de CO2 por cada calle en intervalos de tiempo definidos.

	==> build.bat
Script de Windows. Su propósito es automatizar el proceso de "construcción" de la simulación. Típicamente, contendría todos los comandos necesarios para generar los ficheros de simulación a partir de las fuentes originales: llamar a netconvert para crear la red, a od2trips para generar los viajes y a duarouter para encontrar las rutas, antes de poder ejecutar la simulación con run.bat.

	==> output.add.xml
Fichero XML "adicional" que se puede generar durante una simulación.
Se utiliza para guardar el estado de elementos dinámicos añadidos a la simulación, como detectores de tráfico (bucles de inducción). 
Por ejemplo, si se configura un detector en una calle, este fichero podría registrar cuántos vehículos han pasado por él, su velocidad media, y la ocupación del carril.







***************************************
*****     2. CREACIÓN DE TAZs     *****
***************************************

1. Convertimos geometrías en formatos externos (en esta DEMO un shapefile) a XML que SUMO reconoce:

	>> polyconvert --net-file [SUMO net file PATH] --shapefile-prefixes [shapefile con polígonos que queremos convertir a TAZ] --output-file [fichero donde guardar resultados (output)] --shapefile.id-column [campo/propiedad] --ignore-shapes-without-id

	PARÁMETROS:

--net-file .\red_navarra.net.xml: Especifica la red de SUMO. polyconvert usa la red para determinar qué calles (edges) están dentro de cada polígono del shapefile.

--shapefile-prefixes ...\Municipios_de_Navarra_barrios_de_Pamplona: Indica la ruta al fichero shapefile (.shp) que contiene los polígonos.

--output-file resultado.poly.xml: Define el nombre del fichero de salida. Este XML contendrá la definición de los polígonos.

--shapefile.id-column MUNICIPIO: Le dice a polyconvert que use la columna llamada MUNICIPIO de la tabla de atributos del shapefile como el identificador único (id) para cada polígono.

--ignore-shapes-without-id: Indica que si un polígono en el shapefile no tiene un valor en la columna MUNICIPIO, debe ser ignorado y no procesado.

	NOTAS: 
	Si el comando anterior falla, no se muestra ningún mensaje de error.
	Errores comunes: campos con NULL, no coge bien el UTF-8 (acentos, letra ñ, etc) y hace fallar el volcado de registros en concreto solamente (no de todo el volcado), el campo ID contiene valores repetidos que no permite identificar inequívocamente cada registro, etc.

2. Tomar los polígonos definidos en el paso anterior y convertirlos en TAZ funcionales, listando explícitamente qué calles servirán como origen o destino de los viajes.
El script lee cada polígono del fichero resultado.poly.xml y lo convierte en una <taz> (Zona de Análisis de Tráfico). Dentro de cada <taz>, añade una lista de todas las calles (edges) de la red que están contenidas en ese polígono. Estas calles son las que la herramienta od2trips usará más adelante para colocar el inicio y el fin de los viajes que se generen desde o hacia esa zona.

	>> python [PATH comando edgesInDistricts.py, ejemplo: 'C:\Program Files (x86)\Eclipse\Sumo\tools\edgesInDistricts.py'] -n [.\red_navarra.net.xml] -t [ouput resultado: resultado.poly.xml] -o [output, ej: TAZ.xml]


	PARÁMETROS:
	-n .\red_navarra.net.xml: red de SUMO.
	-t resultado.poly.xml: Usa como entrada el fichero de polígonos que generamos con polyconvert.
	-o TAZ.xml: Especifica el nombre del fichero de salida final.

	NOTAS:
	Tras establecer las relaciones entre TAZ y guardar cambios en SUMO config se genera el fichero con las relaciones osm.xml


IMPORTANTE: En este mismo momento, es en el que a través de la interfaz, se pueden establecer las relaciones entre los TAZ y especificar un flujo concreto, pero lo más RECOMENDABLE es elaborar con procesos automatizados (script python, herramientas SUMO que transforman matrices OD en trips etc) el fichero que establece las relaciones (ej: osm.xml).
Una vez disponemos de este fichero, podemos pasar al siguiente paso y generar la demanda.






***************************************
*****   3. GENERACIÓN DE DEMANDA  *****
***************************************

1. Herramienta od2trips para convertir una matriz Origen-Destino (O-D) en una lista de viajes individuales.
od2trips lee la matriz O-D, y por cada viaje que debe generar, elige aleatoriamente una calle de la TAZ de origen y una calle de la TAZ de destino, creando un fichero de viajes (odtrips.xml).

	>> od2trips -n municipios_navarra.taz.xml --tazrelation-files osm.xml --ignore-vehicle-type -o odtrips.xml

	PARÁMETROS:
	-n municipios_navarra.taz.xml: Especifica el fichero de Zonas de Análisis de Tráfico (TAZ). od2trips necesita este fichero para saber qué calles pertenecen a cada zona de origen y destino.
	--tazrelation-files osm.xml: Indica el fichero que contiene la matriz O-D.
	--ignore-vehicle-type: Es una opción para simplificar la generación. Indica a od2trips que no se preocupe por asignar diferentes tipos de vehículos (coche, camión, etc.) y que use el tipo por defecto (en este caso car).
	-o odtrips.xml: Define el nombre del fichero de salida. Este fichero contendrá una larga lista de elementos <trip>, donde cada uno especifica una calle de origen (from) y una calle de destino (to), pero aún no la ruta para llegar.


2. Encontrar la ruta óptima para cada uno de los viajes generados en el paso anterior.
duarouter toma cada viaje definido en odtrips.xml, calcula la mejor ruta a través de la red red_navarra.net.xml y genera un fichero final (odtrips_valid.xml) que SUMO puede usar directamente para la simulación, ya que contiene vehículos con rutas válidas y completas.

	>> duarouter -n red_navarra.net.xml -r odtrips.xml --ignore-errors --write-trips -o odtrips_valid.xml

	PARÁMETROS: 
	-n red_navarra.net.xml: Especifica el fichero de la red. duarouter necesita el mapa completo para poder calcular las rutas (normalmente, la más corta o la más rápida).
	-r odtrips.xml: Indica el fichero de entrada, que es la lista de viajes (<trip>) que generó od2trips.
	--ignore-errors: Es una opción de seguridad muy útil. Si duarouter no puede encontrar una ruta para un viaje (por ejemplo, porque el origen y el destino están en partes no conectadas de la red), simplemente ignorará ese viaje y continuará con los demás, en lugar de detener todo el proceso.
	--write-trips: Esta opción le indica a duarouter que mantenga el formato de "viaje" en el fichero de salida.
	-o odtrips_valid.xml: Define el nombre del fichero de salida.