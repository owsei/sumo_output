import sumolib
# import traci
import os
import sys
import subprocess

import sumolib
import psycopg2


sumo_home = r"C:\Proyectos\01_SUMO\sumo-1.26.0"
ruta= r"C:\Proyectos\twin-sumo-output\red_carreteras"
ruta_output= r"C:\Proyectos\twin-sumo-output\output"


def net_to_postgres(net_file):
    # Leer la red de SUMO
    try:
        net = sumolib.net.readNet(net_file)
        conn = psycopg2.connect("host=localhost port=5432 dbname=sumo user=admin password=admin")
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS nodos_pamplona (
                node_id TEXT PRIMARY KEY,
                tipo_control TEXT,
                geom GEOMETRY(Point, 4326)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS calles_pamplona (
                id VARCHAR PRIMARY KEY,
                velocidad FLOAT,
                longitud FLOAT,
                edge_name VARCHAR,
                geom GEOMETRY(LineString, 4326)
            )
        """)

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

        cur.execute("CREATE INDEX idx_nodos_geom ON nodos_pamplona USING GIST (geom)")
        cur.execute("CREATE INDEX idx_carriles_geom ON carriles_pamplona USING GIST (geom)")
        cur.execute("CREATE INDEX idx_calles_geom ON calles_pamplona USING GIST (geom)")

        # --- 1. CARGAR NODOS (Semáforos y Cruces) ---
        for node in net.getNodes():
            lon, lat = node.getCoord()
            lon, lat = net.convertXY2LonLat(lon, lat)
            cur.execute("""
                INSERT INTO nodos_pamplona (node_id, tipo_control, geom)
                VALUES (%s, %s, ST_SetSRID(ST_Point(%s, %s), 4326))
                ON CONFLICT (node_id) DO NOTHING;
            """, (node.getID(), node.getType(), lon, lat))


        # --- 2. CARGAR CALLES (Edges) ---
        for edge in net.getEdges():
            edge_id = edge.getID()
            # Primero insertamos la calle (como hicimos antes)
            shape = edge.getShape()
            coords = [net.convertXY2LonLat(x, y) for x, y in shape]
            wkt_edge = f"LINESTRING({', '.join([f'{p[0]} {p[1]}' for p in coords])})"
            
            cur.execute("""
                INSERT INTO calles_pamplona (id,velocidad,longitud, edge_name, geom)
                VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                ON CONFLICT (id) DO NOTHING;
            """, (edge_id, edge.getSpeed(), edge.getLength(), edge.getName(), wkt_edge))

            # Ahora insertamos cada carril de esta calle
            for lane in edge.getLanes():
                lane_id = lane.getID()
                lane_shape = lane.getShape()
                coords = [net.convertXY2LonLat(x, y) for x, y in lane_shape]
                wkt_lane = f"LINESTRING({', '.join([f'{p[0]} {p[1]}' for p in coords])})"
                # Convertimos lista de permisos a string
                permisos = ", ".join(lane.getPermissions())

                cur.execute("""
                    INSERT INTO carriles_pamplona (lane_id, edge_id, indice_carril, ancho, permisos, velocidad_max, geom)
                    VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                    ON CONFLICT (lane_id) DO NOTHING;
                """, (lane_id, edge_id, lane.getIndex(), lane.getWidth(), permisos, lane.getSpeed(), wkt_lane))
        
        
        
        conn.commit()
        cur.close()
        conn.close()
        print("Red completa (Nodos, Calles y Carriles) cargada en Postgres.")
    except Exception as e:
        print(f"Error al procesar la red: {e}")

if __name__ == "__main__":
    net_file = os.path.join(ruta, "pamplona.net.xml")
    net_to_postgres(net_file)


