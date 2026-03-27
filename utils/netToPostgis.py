import sumolib
# import traci
import os
import sys
import subprocess

import sumolib
import psycopg2


sumo_home = os.environ.get("SUMO_HOME")
ruta= r"C:\Proyectos\twin-sumo-output\red_carreteras"
ruta_output= r"C:\Proyectos\twin-sumo-output\output"


def net_to_postgres(net_file):
    # Leer la red de SUMO
    query=''
    try:
        net = sumolib.net.readNet(net_file, withInternal=True, withPedestrianConnections=True,withLatestPrograms=True)
        conn = psycopg2.connect("host=localhost port=5432 dbname=sumo user=admin password=admin")
        cur = conn.cursor()

        cur.execute("""
            create extension if not exists postgis
        """)
        
        print("Create: extension postgis")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS nodos_pamplona (
                node_id TEXT PRIMARY KEY,
                tipo_control TEXT,
                point GEOMETRY(Point, 4326),
                shape GEOMETRY(LINESTRING, 4326)
            )
        """)
        print("Create: table nodos_pamplona")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS calles_pamplona (
                id VARCHAR PRIMARY KEY,
                velocidad FLOAT,
                longitud FLOAT,
                edge_name VARCHAR,
                geom GEOMETRY(LineString, 4326)
            )
        """)
        print("Create: table calles_pamplona")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS carriles_pamplona (
                lane_id TEXT PRIMARY KEY,
                edge_id TEXT REFERENCES calles_pamplona(id),
                indice_carril INTEGER, 
                ancho FLOAT,
                permisos TEXT, -- "passenger, bus, taxi..."
                velocidad_max FLOAT,
                geom GEOMETRY(LineString, 4326))
        """)

        print("Create: table carriles_pamplona")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodos_geom ON nodos_pamplona USING GIST (point, shape)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_carriles_geom ON carriles_pamplona USING GIST (geom)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_calles_geom ON calles_pamplona USING GIST (geom)")

        # --- 1. CARGAR NODOS (Semáforos y Cruces) ---
        print("Cargando nodos...")
        for node in net.getNodes():
            lon, lat = node.getCoord()
            lon, lat = net.convertXY2LonLat(lon, lat)
            shape = node.getShape()
            coordinates = []
            for x, y in shape:
                lon, lat = net.convertXY2LonLat(x, y)
                coordinates.append([lon, lat])

            if coordinates[0] != coordinates[-1]:
                coordinates.append(coordinates[0])


            wkt_coords = ", ".join([f"{p[0]} {p[1]}" for p in coordinates])

            if len(coordinates) == 1 :
                wkt_linestring = f"LINESTRING({wkt_coords}, {wkt_coords})"
            else:
                wkt_linestring = f"LINESTRING({wkt_coords})"

            # 5. Ejecutar la query
            query = """
                INSERT INTO nodos_pamplona (node_id, tipo_control, point, shape)
                VALUES ('"""+ node.getID() +"""', 
                        '"""+ node.getType() +"""', 
                        ST_SetSRID(ST_Point("""+ str(lon) +""", """+ str(lat) +"""), 4326), 
                        ST_GeomFromText('"""+ wkt_linestring +"""', 4326))
                ON CONFLICT (node_id) DO NOTHING;
            """ 

            print(f"{query}")
            # Asegúrate de pasar los parámetros como una tupla al ejecutar (cursor.execute)
            cur.execute(query)

        print("Nodos cargados.")
        # --- 2. CARGAR CALLES (Edges) ---
        print("Cargando calles y carriles...")
        edges_file = os.path.join(ruta_output, "edges.txt")
        with open(edges_file, 'w') as f_edges:
            for edge in net.getEdges():
                edge_id = edge.getID()
                if (edge_id.find('250700883_0') != -1):
                    print(f"DEBUG: edge_id={edge_id}")
                # Primero insertamos la calle (como hicimos antes)
                shape = edge.getShape()
                coords = [net.convertXY2LonLat(x, y) for x, y in shape]
                wkt_edge = f"LINESTRING({', '.join([f'{p[0]} {p[1]}' for p in coords])})"
                
                query_edge = f"""
                    INSERT INTO calles_pamplona (id,velocidad,longitud, edge_name, geom)
                    VALUES ('{edge_id}', {edge.getSpeed()}, {edge.getLength()}, '{edge.getName()}', ST_GeomFromText('{wkt_edge}', 4326))
                    ON CONFLICT (id) DO NOTHING;    
                """
                f_edges.write(f"{query_edge}\n")

                cur.execute(query_edge)
                # conn.commit()

                # Ahora insertamos cada carril de esta calle
                lanes_file = os.path.join(ruta_output, "lanes.txt")
                with open(lanes_file, 'a') as f_lanes:
                    for lane in edge.getLanes():
                        lane_id = lane.getID()
                        if (lane_id.find('250700883_0_0') != -1):
                            print(f"DEBUG: edge_id={edge_id}, lane_id={lane_id}")

                        lane_shape = lane.getShape()
                        coords = [net.convertXY2LonLat(x, y) for x, y in lane_shape]
                        wkt_lane = f"LINESTRING({', '.join([f'{p[0]} {p[1]}' for p in coords])})"
                        # Convertimos lista de permisos a string
                        permisos = ", ".join(lane.getPermissions())

                        query_lane = f"""
                            INSERT INTO carriles_pamplona (lane_id, edge_id, indice_carril, ancho, permisos, velocidad_max, geom)
                            VALUES ('{lane_id}', '{edge_id}', {lane.getIndex()}, {lane.getWidth()}, '{permisos}', {lane.getSpeed()}, ST_GeomFromText('{wkt_lane}', 4326))
                            ON CONFLICT (lane_id) DO NOTHING;
                        """
                        f_lanes.write(f"{query_lane}\n")
                        cur.execute(query_lane)
                        # conn.commit()

                        # cur.execute("""
                        #     INSERT INTO carriles_pamplona (lane_id, edge_id, indice_carril, ancho, permisos, velocidad_max, geom)
                        #     VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                        #     ON CONFLICT (lane_id) DO NOTHING;
                        # """, (lane_id, edge_id, lane.getIndex(), lane.getWidth(), permisos, lane.getSpeed(), wkt_lane))
        
        print("Calles y carriles cargados.")
        
        conn.commit()
        cur.close()
        conn.close()
        print("Red completa (Nodos, Calles y Carriles) cargada en Postgres.")
    except Exception as e:
        print(f"Error al procesar la red: {e} {query}")

if __name__ == "__main__":
    net_file = os.path.join(ruta, "pamplona.net.xml")
    net_to_postgres(net_file)


