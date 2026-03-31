from time import time

import sumolib
# import traci
import os
import sys
import subprocess

import sumolib
import psycopg2
import xml.etree.ElementTree as ET
import pandas as pd
import pyarrow.parquet as pa



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


        cur.execute("""CREATE TABLE IF NOT EXISTS traffic_lights_pamplona (
                tls_id TEXT PRIMARY KEY,
                node_id TEXT REFERENCES nodos_pamplona(node_id),
                edge_id TEXT REFERENCES calles_pamplona(id),
                link_index INTEGER,
                geom GEOMETRY(Point, 4326)
            )""")
        print("Create: table traffic_lights_pamplona")


        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodos_geom ON nodos_pamplona USING GIST (point, shape)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_carriles_geom ON carriles_pamplona USING GIST (geom)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_calles_geom ON calles_pamplona USING GIST (geom)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_traffic_lights_geom ON traffic_lights_pamplona USING GIST (geom)")

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


        trafficlights_file = os.path.join(ruta_output, "trafficlights.txt")
        with open(trafficlights_file, 'w') as f_trafficlights:
            for tls in net.getTrafficLights():
                tls_id = tls.getID()
                
                # El TLS nos da las conexiones (el "puente" entre calles)
                for connection in tls.getConnections():
                    # connection[0] es el carril de entrada (Lane)
                    lane_entrada = connection[0]
                    link_index = connection[2] # Su posición en el código de luces (0, 1, 2...)
                    
                    # El semáforo físico está al final del carril
                    shape = lane_entrada.getShape()
                    punto_final = shape[-1] 
                    
                    lon, lat = net.convertXY2LonLat(punto_final[0], punto_final[1])
                    
                    query_tls = f"""
                        INSERT INTO nodos_pamplona (node_id, tipo_control,geom)
                        VALUES ('{tls_id}', 'traffic_light', ST_SetSRID(ST_Point({lon}, {lat}), 4326))
                        ON CONFLICT (node_id) DO NOTHING;
                    """
                    f_trafficlights.write(f"{query_tls}\n")
                    cur.execute(query_tls)


        
        conn.commit()
        cur.close()
        conn.close()
        print("Red completa (Nodos, Calles y Carriles) cargada en Postgres.")
    except Exception as e:
        print(f"Error al procesar la red: {e} {query}")



def simulation_to_postgres():
    # Aquí iría la lógica para cargar los datos de la simulación (vehículos, tiempos, etc.) en Postgres
    # Esto dependerá de cómo estés exportando esos datos desde SUMO (CSV, JSON, etc.)
    print("Función simulation_to_postgres() aún no implementada.")
    conn = psycopg2.connect("host=localhost port=5432 dbname=sumo user=admin password=admin")
    cur = conn.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS simulations (
                    id_simulation integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
                    date timestamp DEFAULT NOW(),
                    number_of_vehicles integer,
                    duration_sec double precision,
                    fringe_factor double precision,
                    trip_period double precision,
                    agregation_period double precision
                
                )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS edge_emissions_simulation (
            id_simulation integer,
            id text,
            sampled_seconds integer,
            co_abs double precision,
            co2_abs double precision,
            hc_abs double precision,
            pmx_abs double precision,
            nox_abs double precision,   
            fuel_abs double precision,
            electricity_abs double precision,
            co_normed double precision,
            co2_normed double precision,
            hc_normed double precision,
            pmx_normed double precision,
            nox_normed double precision,
            fuel_normed double precision,
            electricity_normed double precision,
            traveltime double precision,
            co_perveh double precision,
            co2_perveh double precision,
            hc_perveh double precision,
            pmx_perveh double precision,
            nox_perveh double precision,
            fuel_perveh double precision,
            electricity_perveh double precision,
            interval_begin integer,
            interval_end integer
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS edge_traffic_simulation (
            id_simulation integer,
            id text,
            sampled_seconds double precision,
            traveltime double precision,
            overlap_traveltime double precision,
            density double precision,
            overlap_density double precision,
            lane_density double precision,
            occupancy double precision,
            waiting_time double precision,
            time_loss double precision,
            speed double precision,
            speed_relative double precision,
            departed integer,
            arrived integer,
            entered integer,
            "left" integer,
            lane_changed_from integer,
            lane_changed_to integer,
            flow double precision,
            interval_begin double precision,
            interval_end double precision
        );
    """)


    df_emissions = pd.read_parquet(os.path.join(ruta_output, "edgeEmissions.parquet"))
    df_emissions.sort_values(['interval_begin'], inplace=True)
    df_emissions = df_emissions.fillna(0)
    columns=df_emissions.columns
    print(f"Columnas del DataFrame: {columns}")
    print(df_emissions.head(10))
    print(len(df_emissions))
    total_rows = len(df_emissions)

    df_traffic = pd.read_parquet(os.path.join(ruta_output, "edgeTraffic.parquet"))
    df_traffic.sort_values(['interval_begin'], inplace=True)
    df_traffic = df_traffic.fillna(0)
    columns_traffic = df_traffic.columns
    print(f"Columnas del DataFrame edgeTraffic: {columns_traffic}")
    print(df_traffic.head(10))
    print(len(df_traffic))
    total_rows_traffic = len(df_traffic)

    simulation = os.path.join(ruta_output, "simulation.txt")
    cur.execute("INSERT INTO simulations (date) VALUES (NOW()) RETURNING id_simulation;")
    id_simulation = cur.fetchone()[0]
    try:
        with open(simulation, 'a') as f_simulation:
            i=1
            for d in df_emissions.index:
                porcentaje = (i / total_rows) * 100
                print(f"\rProgreso: {porcentaje:.2f}% ({i}/{total_rows})", end="")
                df_row_emisions = df_emissions.loc[d]
                f_simulation.write(f"{df_row_emisions}\n")
                query = f"""
                    INSERT INTO edge_emissions_simulation ( id_simulation, id, sampled_seconds, co_abs, co2_abs, hc_abs, pmx_abs, nox_abs, fuel_abs, electricity_abs, co_normed, co2_normed, hc_normed, pmx_normed, nox_normed, fuel_normed, electricity_normed, traveltime, co_perveh, co2_perveh, hc_perveh, pmx_perveh, nox_perveh, fuel_perveh, electricity_perveh, interval_begin, interval_end)
                    VALUES ({id_simulation}, '{df_row_emisions["id"]}', {df_row_emisions["sampledSeconds"]}, {df_row_emisions["CO_abs"]}, {df_row_emisions["CO2_abs"]}, {df_row_emisions["HC_abs"]}, {df_row_emisions["PMx_abs"]}, {df_row_emisions["NOx_abs"]}, {df_row_emisions["fuel_abs"]}, {df_row_emisions["electricity_abs"]}, {df_row_emisions["CO_normed"]}, {df_row_emisions["CO2_normed"]}, {df_row_emisions["HC_normed"]}, {df_row_emisions["PMx_normed"]}, {df_row_emisions["NOx_normed"]}, {df_row_emisions["fuel_normed"]}, {df_row_emisions["electricity_normed"]}, {df_row_emisions["traveltime"]}, {df_row_emisions["CO_perVeh"]}, {df_row_emisions["CO2_perVeh"]}, {df_row_emisions["HC_perVeh"]}, {df_row_emisions["PMx_perVeh"]}, {df_row_emisions["NOx_perVeh"]}, {df_row_emisions["fuel_perVeh"]}, {df_row_emisions["electricity_perVeh"]}, {df_row_emisions["interval_begin"]}, {df_row_emisions["interval_end"]});
                """
                f_simulation.write(f"{query}\n")
                cur.execute(query)
                i+=1
            
            conn.commit()
            print("")
            f_simulation.write("\n--- EDGE TRAFFIC ---\n")
            j=1
            for d in df_traffic.index:
                porcentaje = (j / total_rows_traffic) * 100
                print(f"\rProgreso edgeTraffic: {porcentaje:.2f}% ({j}/{total_rows_traffic})", end="")
                df_row = df_traffic.loc[d]
                f_simulation.write(f"{df_row}\n")
                query = f"""
                    INSERT INTO edge_traffic_simulation (id_simulation, id, sampled_seconds, traveltime, overlap_traveltime, density, overlap_density, lane_density, occupancy, waiting_time, time_loss, speed, speed_relative, departed, arrived, entered, "left", lane_changed_from, lane_changed_to, flow, interval_begin, interval_end)
                    VALUES ({id_simulation}, '{df_row["id"]}', {df_row["sampledSeconds"]}, {df_row["traveltime"]}, {df_row["overlapTraveltime"]}, {df_row["density"]}, {df_row["overlapDensity"]}, {df_row["laneDensity"]}, {df_row["occupancy"]}, {df_row["waitingTime"]}, {df_row["timeLoss"]}, {df_row["speed"]}, {df_row["speedRelative"]}, {df_row["departed"]}, {df_row["arrived"]}, {df_row["entered"]}, {df_row["left"]}, {df_row["laneChangedFrom"]}, {df_row["laneChangedTo"]}, {df_row["flow"]}, {df_row["interval_begin"]}, {df_row["interval_end"]});
                """
                f_simulation.write(f"{query}\n")
                cur.execute(query)
                j += 1
            conn.commit()

        cur.close() 
        conn.close()
        f_simulation.close()
        print("\n¡Carga completada!")
        print("\nDatos de la simulación cargados en Postgres.")
    except Exception as e:
        cur.close()
        conn.close()
        f_simulation.close()
        print(f"Error al cargar datos de la simulación: {e} ")

    
if __name__ == "__main__":
    net_file = os.path.join(ruta, "pamplona.net.xml")
    # net_to_postgres(net_file)
    simulation_to_postgres()


