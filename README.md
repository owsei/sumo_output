# SUMO traffic simulation 
## Creacion de simulaciones de trafico con SUMO
SUMO es un sofware de creación de simulacion de trafico macroscopico, mesoscópico y microscopico
(https://sumo.dlr.de/docs/index.html)


SUMO tiene dos cosas basicas, la red por la que circulan los vehiculos y las rutas que van hacer los vehiculos.

# CREACIÓN DE LA RED
Para crear la red SUMO tiene la posibilidad de crear una red desde los mapas de OpenStreetMap con una herramienta que las convierte llamada **netconvert**

### netconvert
Para la creación de la red y usar los mapas de OpenStreetMap primero hay que descargar el segmento de red que queramos.

Esto se hace con un endpoint que ofrece OpenStreetMap llamado Overpass:
(https://wiki.openstreetmap.org/wiki/Overpass_API)

Existen varios endpoint que podemos usar en caso de que el elegido no este disponible(endpoint tiene **limitaciones** de número de llamadas/tamaño por dia).

Este ejemplo es una llamada hecha con **Fastapi(python)** a el endpoint de Overpass:
```
query = f"""
[out:xml][timeout:25];
(
way["highway"~"{types_filter}"]["access"!="private"]["motor_vehicle"!="no"]({bbox.south},{bbox.west},{bbox.north},{bbox.east});
(._;>;);
);
out meta;
"""
headers = {
    "User-Agent": "TrafficSim/1.0 (contacto: pmesparza@itracasa.es)"
}
print("Query de OSM: ", query)
response = requests.get("https://overpass.kumi.systems/api/interpreter", params={'data': query},headers=headers)
```

Una vez obtenida la red se guarda en un fichero :
```
output_path= 'mapa.osm.xml' -> Nombre del fichero que almacenara la red descargada
if response.status_code == 200:
    with open(output_path, "wb") as f:
        f.write(response.content)
```

Tras guardar el fichero de la red netcovert los usa como parametro de entrada y la convierte al formato que necesita SUMO:
OSM-data siempre usa coordenadas WGS84

```
netconvert
    -osm-files nombre_fichero_red_openstreetmap
    -o nombre_fichero_salida_red_convertida
```

Opciones recomendadas para **netconvert**
```
--geometry.remove: Simplifica la red (ahorrando espacio) sin modificar la topología.
--ramps.guess: Los carriles de aceleración/desaceleración a menudo no se incluyen en los datos de OSM. Esta opción identifica las carreteras que probablemente tengan estos carriles adicionales y los añade.
--junctions.join: Uniones entre vias

Luces de trafico
--tls.guess-signals 
--tls.discard-simple 
--tls.join 
--tls.default-type actuated: Los semáforos estáticos predeterminados se definen sin tener en cuenta los patrones de tráfico y pueden funcionar mal en zonas de mucho tráfico.
```











CREATE ROLE web_anon nologin;
GRANT USAGE ON SCHEMA api TO web_anon;
GRANT USAGE ON SCHEMA public TO web_anon;
GRANT SELECT ON api.productos TO web_anon;
GRANT SELECT ON api.productos TO web_anon;