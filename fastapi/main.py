import time
from xml.dom import minidom

import sumolib
# import traci
import os
import sys
import subprocess
import tempfile
import requests
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Dict
import uuid
# from constants import PREFIX, DOUBLE_ROWS, ROW_DIST, SLOTS_PER_ROW, SLOT_WIDTH
from sumolib import checkBinary
import traci
from urllib.parse import unquote
import platform
import asyncio
from pyproj import Geod
import math
import xml.etree.ElementTree as ET
import pandas as pd
from pathlib import Path
import pyarrow
import json
import psycopg2
import clases.sumoClass as sumoClass 


# sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

url_overpass = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"

sumo_home_windows = r"C:\Proyectos\01_SUMO\sumo-1.26.0"
sumo_home_linux = "/usr/share/sumo"

ruta_output_windows = r"c:\Proyectos\sumo_output_mia\output"
ruta_output_linux = r"/tmp/output/"

ruta_windows = r"c:\Proyectos\sumo_output_mia\red_carreteras"
ruta_linux = r"/tmp/"

sumoBD=sumoClass.connectionParams("localhost", "5432", "sumo", "admin", "admin")


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def operative_system_detect():
    operativeSytemIsLinux= 1 if platform.system()=="Linux" else 0
    if operativeSytemIsLinux==1:
       sumo_home = "/usr/share/sumo"
       ruta_output= r"/tmp/output"
    else:
       sumo_home = r"D:\Proyectos\01_SUMO"
       ruta= r"D:\Proyectos\sumo_output\red_carreteras"
       ruta_output= r"D:\Proyectos\sumo_output\output"

    return operativeSytemIsLinux,sumo_home,ruta,ruta_output

async def download_osm_data_overpass(bbox: sumoClass.BoundingBox, output_path: str, websocket:WebSocket):
    """Descarga directa de Overpass API para evitar errores de osmGet.py"""
    print("Descargando datos de OSM")
    types_filter = "|".join(bbox.road_types)
    print("Filtros de tipos: ", types_filter)
    # Overpass usa el orden: south, west, north, east
    overpass_url = url_overpass
    # Esta query descarga solo las vías (ways) que coincidan con los tipos
    # y también los nodos (nodes) que forman esas vías.
    try:
        
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
        if response.status_code == 200:
            with open(output_path, "w") as f:
                f.write(response.text)
            await websocket.send_json({"mensaje": "Fichero de carreteras guardado correctamente. "})
        else:
            error_msg=f"403 Forbidden: Probablemente has hecho demasiadas solicitudes a Overpass API. Intenta de nuevo más tarde. {response.status_code} {response.text}"
            await websocket.send_json({"mensaje": "Error en la descarga de carreteras 🚨:" + error_msg})
            
     
    except Exception as e:
        f.close()
        await websocket.send_json({"mensaje": "Error en la descarga de carreteras 🚨:" + str(e) })
        await websocket.close()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        f.close()

async def download_osm_data(bbox: sumoClass.BoundingBox, output_path: str, websocket:WebSocket):
    """Descarga directa de Overpass API para evitar errores de osmGet.py"""
    print("Descargando datos de OSM")
    types_filter = "|".join(bbox.road_types)
    print("Filtros de tipos: ", types_filter)
    # Overpass usa el orden: south, west, north, east
    overpass_url = "https://overpass.kumi.systems/api/interpreter"
    # Esta query descarga solo las vías (ways) que coincidan con los tipos
    # y también los nodos (nodes) que forman esas vías.
    try:
        query = f"""
        [out:xml][timeout:25];
        (
        way["highway"~"{types_filter}"]["access"!="private"]["motor_vehicle"!="no"]({bbox.south},{bbox.west},{bbox.north},{bbox.east});
        (._;>;);
        );
        out meta;
        """
        print("Query de OSM: ", query)
        response = requests.get(overpass_url, params={'data': query})
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            await websocket.send_json({"mensaje": "Error guardar fichero carreteras "})
        else:
            await websocket.send_json({"mensaje": "Error en la descarga de carreteras 🚨:" + str(response.status_code + " " + response.text) })

    except Exception as e:
        await websocket.send_json({"mensaje": "Error en la descarga de carreteras 🚨:" + str(e) })
        await websocket.close()
        raise HTTPException(status_code=500, detail=str(e))

def getVelocityStyle(velocity):

    if (velocity>119):
        return "#004EB0"
    elif (velocity>101 and velocity<=119):
        return "#3BB3C3"
    elif (velocity>80 and velocity<=101):
        return "#1D704C"
    elif (velocity>70 and velocity<=80):
        return "#278D5F"
    elif (velocity>60 and velocity<=70):
        return "#2A6B4E"
    elif (velocity>50 and velocity<=60):
        return "#007324"
    elif (velocity>40 and velocity<=50):
        return "#DA9C20"
    elif (velocity>30 and velocity<=40):
        return "#E6B71E"
    elif (velocity>20 and velocity<=30):
        return "#EED322"
    elif (velocity>5 and velocity<=20):
        return "#F2F12D"
    else:
        return "#FFFFFF"

def getTrafficLightColor(state):
    match state:
        case "r" | "R":
            return "red"
        case "y" | "Y":
            return "yellow"
        case "g" | "G":
            return "green"
        case _:
            return "gray"  

async def convert_net_to_geojson_net(websocket, net_file):
    """
    Usa sumolib para leer la red de SUMO y crear un GeoJSON 
    con nombre de calle, tipo y otros atributos.
    """
    # Cargamos la red
    net = sumolib.net.readNet(net_file)
    features = []

    for edge in net.getEdges():
        # Obtenemos la geometría (forma) de la carretera
        # Convertimos las coordenadas internas de SUMO a Lon/Lat
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y) for x, y in shape]
        # Extraemos las propiedades que queremos
        # Nota: edge.getName() devuelve el nombre de la calle de OSM
        properties = {
            "id": edge.getID(),
            "nombre": edge.getName() or "Calle sin nombre",
            "tipo": edge.getType(),
            "velocidad_max": edge.getSpeed() * 3.6, # Convertir m/s a km/h
            "carriles": edge.getLaneNumber()
        }

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": properties
        }
        features.append(feature)

        feature = {
            "type": "feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": properties
        }
        await websocket.send_json(feature)
    await websocket.send_json({"mensaje": "Descarga de carreteras finalizada correctamente👍"})    

def parse_edge_data(xml_file: str) -> pd.DataFrame:
    tree = ET.parse(xml_file)
    root = tree.getroot()

    rows = []

    for interval in root.findall("interval"):
        begin = float(interval.attrib.get("begin", 0))
        end = float(interval.attrib.get("end", 0))

        for edge in interval.findall("edge"):
            row = {
                "begin": begin,
                "end": end,
                "edge_id": edge.attrib.get("id")
            }

            for key, value in edge.attrib.items():
                if key != "id":
                    row[key] = value

            rows.append(row)

    df = pd.DataFrame(rows)

    # Intentar convertir columnas numéricas automáticamente
    for col in df.columns:
        if col not in ["edge_id"]:
            df[col] = pd.to_numeric(df[col], errors="ignore")

    return df

async def convertirEmissionsXmlToParquet(websocket,ruta_emissions,ruta_parquet,rootLabel='interval',nestLabel='edge'):
    try:
        tree = ET.parse(ruta_emissions)
        root = tree.getroot()

        lista_final = []

        # 2. Recorrer cada intervalo (el padre)
        for interval in root.findall(rootLabel):
            # Extraemos los datos del tiempo
            inicio = interval.get('begin')
            fin = interval.get('end')
            
            # 3. Recorrer cada edge dentro de ese intervalo (el hijo)
            for edge in interval.findall(nestLabel):
                # Copiamos todos los atributos del edge (id, CO2, fuel, etc.)
                datos_fila = edge.attrib.copy()
                
                # Añadimos la información del tiempo del padre a esta fila
                datos_fila['interval_begin'] = inicio
                datos_fila['interval_end'] = fin
                
                lista_final.append(datos_fila)

                # 4. Crear el DataFrame
        df = pd.DataFrame(lista_final)
        # 5. Limpieza de datos (Crucial para Cesium y análisis)
        # Convertimos a números lo que debe ser número
        cols_numericas = [c for c in df.columns if c not in ['id', 'interval_begin', 'interval_end']]
        for col in cols_numericas:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Aseguramos que los tiempos también sean numéricos para filtrar en el mapa
        df['interval_begin'] = pd.to_numeric(df['interval_begin'])
        df['interval_end'] = pd.to_numeric(df['interval_end'])

        # 6. Guardar a Parquet
        # Mantenemos el 'id' intacto para que Cesium pueda hacer el JOIN con tu red .js o .geojson
        df.to_parquet(ruta_parquet, engine='pyarrow', index=False)
        
        print(f"Éxito: Se han procesado {len(df)} registros de edges.")
        await websocket.send_json({"mensaje": f"Éxito: Se han procesado {len(df)} registros de edges de emisiones.✅"})

    except Exception as e:
        print(f"Error al ejecutar SUMO: {e}")
        raise HTTPException(status_code=500, detail=f"Error al ejecutar SUMO: {e}")

async def convertirTrafficXmlToParquet(websocket,ruta_emissions,ruta_parquet,rootLabel='interval',nestLabel='edge'):
    try:
        tree = ET.parse(ruta_emissions)
        root = tree.getroot()

        lista_final = []

        # 2. Recorrer cada intervalo (el padre)
        for interval in root.findall(rootLabel):
            # Extraemos los datos del tiempo
            inicio = interval.get('begin')
            fin = interval.get('end')
            
            # 3. Recorrer cada edge dentro de ese intervalo (el hijo)
            for edge in interval.findall(nestLabel):
                # Copiamos todos los atributos del edge (id, CO2, fuel, etc.)
                datos_fila = edge.attrib.copy()
                
                # Añadimos la información del tiempo del padre a esta fila
                datos_fila['interval_begin'] = inicio
                datos_fila['interval_end'] = fin
                
                lista_final.append(datos_fila)

                # 4. Crear el DataFrame
        df = pd.DataFrame(lista_final)
        # 5. Limpieza de datos (Crucial para Cesium y análisis)
        # Convertimos a números lo que debe ser número
        cols_numericas = [c for c in df.columns if c not in ['id', 'interval_begin', 'interval_end']]
        for col in cols_numericas:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Aseguramos que los tiempos también sean numéricos para filtrar en el mapa
        df['interval_begin'] = pd.to_numeric(df['interval_begin'])
        df['interval_end'] = pd.to_numeric(df['interval_end'])

        # 6. Guardar a Parquet
        # Mantenemos el 'id' intacto para que Cesium pueda hacer el JOIN con tu red .js o .geojson
        df.to_parquet(ruta_parquet, engine='pyarrow', index=False)
        
        print(f"Éxito: Se han procesado {len(df)} registros de edges.")
        await websocket.send_json({"mensaje": f"Éxito: Se han procesado {len(df)} registros de edges de trafico.✅"})
    except Exception as e:
        print(f"Error al ejecutar SUMO: {e}")
        raise HTTPException(status_code=500, detail=f"Error al ejecutar SUMO: {e}")

#---------------------------------------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "ok"}

@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    try:
        await websocket.accept()
        await websocket.send_json({"mensaje": "Conexión WebSocket establecida correctamente"})

    except Exception as e:
        print(f"Error en WebSocket: {e}")
    finally:
        await websocket.close()

# ******************FUNCIONES DE PARSEO DE LOS RESULTADOS DE EMISIONES DE SUMO**********************#
def parse_sumo_emissions_edge(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    data = []

    for interval in root.findall('interval'):
        time_begin=interval.get('begin')
        time_end=interval.get('end')
        for edge in interval.findall('edge'):
            # Guardamos el ID y la métrica que nos interese (ej. CO2)
            entry = {
                'time_begin': time_begin,
                'time_end': time_end,
                'id': edge.get('id'),
                'CO_abs': edge.get('CO_abs'),
                'HC_abs': edge.get('HC_abs'),
                'NOx_abs': edge.get('NOx_abs'),
                'PMx_abs': edge.get('PMx_abs'),
                'CO2_abs': edge.get('CO2_abs'),
                'fuel_abs': edge.get('fuel_abs'),
                'CO_normed': edge.get('CO_normed'),
                'CO2_normed': edge.get('CO2_normed'),
                'HC_normed': edge.get('HC_normed'),
                'PMx_normed': edge.get('PMx_normed'),
                'NOx_normed': edge.get('NOx_normed'),
                'fuel_normed': edge.get('fuel_normed'),
                'electricity_normed': edge.get('electricity_normed')
            }
            data.append(entry)
    
    return pd.DataFrame(data)

def parse_sumo_traffic_edge(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    data = []

    for interval in root.findall('interval'):
        for edge in interval.findall('edge'):
            entry = {
                'id': edge.get('id'),
                'density': edge.get('density'),
                'occupancy': edge.get('occupancy'),
                'speed': edge.get('speed'),
                'waiting_time': edge.get('waiting_time'),
                'time_loss': edge.get('time_loss'),
                'departed': edge.get('departed'),
                'arrived': edge.get('arrived'),
                'entered': edge.get('entered'),
                'left': edge.get('left'),
                'lane_changed_from': edge.get('laneChangedFrom'),
                'lane_changed_to': edge.get('laneChangedTo'),
                'flow': edge.get('flow')
            }
            data.append(entry)
    
# funcion de guardado en BD de la simulacion, con la idea de que se ejecute al finalizar la simulacion y el parseo de los resultados de emisiones 
# y trafico, para cargar esos datos en Postgres y poder hacer consultas SQL posteriormente
def simulation_to_postgres(ruta_file_emissions,ruta_file_traffic, connection_params: sumoClass.connectionParams, simulation_params: sumoClass.simulationParams):
    # Aquí iría la lógica para cargar los datos de la simulación (vehículos, tiempos, etc.) en Postgres
    # Esto dependerá de cómo estés exportando esos datos desde SUMO (CSV, JSON, etc.)
    conn = psycopg2.connect(
        host=connection_params.host,
        port=connection_params.port,
        dbname=connection_params.dbname,
        user=connection_params.user,
        password=connection_params.password
    )
    cur = conn.cursor()

    cur.execute(""" CREATE TABLE IF NOT EXISTS simulations (
                    id_simulation integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
                    date timestamp DEFAULT NOW(),
                    num_vehicles integer,
                    duration_sec double precision,
                    fringe_factor double precision,
                    trip_period double precision,
                    aggregation_period double precision,
                    file_emissions text,
                    file_traffic text
                )""")

    cur.execute(""" CREATE TABLE IF NOT EXISTS edge_emissions_simulation (
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

    cur.execute(""" CREATE TABLE IF NOT EXISTS edge_traffic_simulation (
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

    try:
        # df_emissions = pd.read_parquet(os. path.join(ruta_output, ruta_file_emissions))
        # df_emissions.sort_values(['interval_begin'], inplace=True)
        # df_emissions = df_emissions.fillna(0)
        # columns=df_emissions.columns
        # print(f"Columnas del DataFrame: {columns}")
        # print(df_emissions.head(10))
        # print(len(df_emissions))
        # total_rows = len(df_emissions)

        # df_traffic = pd.read_parquet(os. path.join(ruta_output, ruta_file_traffic)) 
        # df_traffic.sort_values(['interval_begin'], inplace=True)
        # df_traffic = df_traffic.fillna(0)
        # columns_traffic = df_traffic.columns
        # print(f"Columnas del DataFrame edgeTraffic: {columns_traffic}")
        # print(df_traffic.head(10))
        # print(len(df_traffic))
        # total_rows_traffic = len(df_traffic)

        # simulation = os.path.join(ruta_output, "simulation.txt")
        cur.execute(f"""INSERT INTO simulations (date, num_vehicles, duration_sec, fringe_factor, aggregation_period, trip_period,file_emissions,file_traffic) VALUES (NOW(), {simulation_params.num_vehicles}, {simulation_params.duration_sec}, {simulation_params.fringe_factor}, {simulation_params.aggregation_period_sec}, {simulation_params.trip_period}, '{ruta_file_emissions}', '{ruta_file_traffic}') RETURNING id_simulation;""")
        id_simulation = cur.fetchone()[0]
        conn.commit()
    

        # i=1
        # for d in df_emissions.index:
        #     porcentaje = (i / total_rows) * 100
        #     print(f"\rProgreso: {porcentaje:.2f}% ({i}/{total_rows})", end="")
        #     df_row_emisions = df_emissions.loc[d]
        #     query = f"""
        #         INSERT INTO edge_emissions_simulation ( id_simulation, id, sampled_seconds, co_abs, co2_abs, hc_abs, pmx_abs, nox_abs, fuel_abs, electricity_abs, co_normed, co2_normed, hc_normed, pmx_normed, nox_normed, fuel_normed, electricity_normed, traveltime, co_perveh, co2_perveh, hc_perveh, pmx_perveh, nox_perveh, fuel_perveh, electricity_perveh, interval_begin, interval_end)
        #         VALUES ({id_simulation}, '{df_row_emisions["id"]}', {df_row_emisions["sampledSeconds"]}, {df_row_emisions["CO_abs"]}, {df_row_emisions["CO2_abs"]}, {df_row_emisions["HC_abs"]}, {df_row_emisions["PMx_abs"]}, {df_row_emisions["NOx_abs"]}, {df_row_emisions["fuel_abs"]}, {df_row_emisions["electricity_abs"]}, {df_row_emisions["CO_normed"]}, {df_row_emisions["CO2_normed"]}, {df_row_emisions["HC_normed"]}, {df_row_emisions["PMx_normed"]}, {df_row_emisions["NOx_normed"]}, {df_row_emisions["fuel_normed"]}, {df_row_emisions["electricity_normed"]}, {df_row_emisions["traveltime"]}, {df_row_emisions["CO_perVeh"]}, {df_row_emisions["CO2_perVeh"]}, {df_row_emisions["HC_perVeh"]}, {df_row_emisions["PMx_perVeh"]}, {df_row_emisions["NOx_perVeh"]}, {df_row_emisions["fuel_perVeh"]}, {df_row_emisions["electricity_perVeh"]}, {df_row_emisions["interval_begin"]}, {df_row_emisions["interval_end"]});
        #     """
        #     cur.execute(query)
        #     i+=1
        
        # conn.commit()
        # print("")
        # j=1
        # for d in df_traffic.index:
        #     porcentaje = (j / total_rows_traffic) * 100
        #     print(f"\rProgreso edgeTraffic: {porcentaje:.2f}% ({j}/{total_rows_traffic})", end="")
        #     df_row = df_traffic.loc[d]
        #     query = f"""
        #         INSERT INTO edge_traffic_simulation (id_simulation, id, sampled_seconds, traveltime, overlap_traveltime, density, overlap_density, lane_density, occupancy, waiting_time, time_loss, speed, speed_relative, departed, arrived, entered, "left", lane_changed_from, lane_changed_to, flow, interval_begin, interval_end)
        #         VALUES ({id_simulation}, '{df_row["id"]}', {df_row["sampledSeconds"]}, {df_row["traveltime"]}, {df_row["overlapTraveltime"]}, {df_row["density"]}, {df_row["overlapDensity"]}, {df_row["laneDensity"]}, {df_row["occupancy"]}, {df_row["waitingTime"]}, {df_row["timeLoss"]}, {df_row["speed"]}, {df_row["speedRelative"]}, {df_row["departed"]}, {df_row["arrived"]}, {df_row["entered"]}, {df_row["left"]}, {df_row["laneChangedFrom"]}, {df_row["laneChangedTo"]}, {df_row["flow"]}, {df_row["interval_begin"]}, {df_row["interval_end"]});
        #     """
        #     cur.execute(query)
        #     j += 1
        # conn.commit()

        cur.close() 
        conn.close()
        print("\n¡Carga completada!")
        print("\nDatos de la simulación cargados en Postgres.")

    except Exception as e:
        cur.close()
        conn.close()
        print(f"Error al cargar datos de la simulación: {e} ")


def generar_rerouter_cierre(nombre_fichero, lista_calles_a_cerrar, calle_donde_detectar, inicio=0, fin=3600):
    """
    Genera un archivo .add.xml para cerrar calles en SUMO.
    
    :param nombre_fichero: Nombre del archivo de salida (ej: 'cierres.add.xml')
    :param lista_calles_a_cerrar: Lista con los IDs de las calles que se quieren prohibir
    :param calle_donde_detectar: ID de la calle donde los vehículos recalculan la ruta
    :param inicio: Segundo de la simulación donde empieza el cierre
    :param fin: Segundo de la simulación donde termina el cierre
    """
    
    # Crear el elemento raíz <additional>
    additional = ET.Element('additionals')
    
    # Crear el elemento <rerouter>
    # El atributo 'edges' es donde se coloca el sensor de decisión
    rerouter = ET.SubElement(additional, 'rerouter', {
        'id': 'rerouter_dinamico',
        'edges': calle_donde_detectar
    })
    
    # Crear el intervalo de tiempo <interval>
    interval = ET.SubElement(rerouter, 'interval', {
        'begin': str(inicio),
        'end': str(fin)
    })
    
    # Añadir cada calle de la lista como un <closingReroute>
    for calle_id in lista_calles_a_cerrar:
        ET.SubElement(interval, 'closingReroute', {'id': calle_id})
    
    # Convertir a string con formato bonito (indentación)
    xml_string = ET.tostring(additional, encoding='utf-8')
    reparsed = minidom.parseString(xml_string)
    xml_bonito = reparsed.toprettyxml(indent="    ")
    
    # Guardar en el archivo
    with open(nombre_fichero, "w", encoding="utf-8") as f:
        f.write(xml_bonito)
    
    print(f"Archivo {nombre_fichero} generado con éxito.")

# ******************FIN FUNCIONES DE PARSEO DE LOS RESULTADOS DE EMISIONES DE SUMO**********************#
# RUTA PARA EJECUTAR LA SIMULACION DE SUMO Y OBTENER LOS RESULTADOS DE EMISIONES Y TRAFICO EN CALLES Y CARRILES
@app.websocket("/ws/simulationEmissions")
async def simulationEmissions(websocket: WebSocket):
    await websocket.accept()
    uuid_simulation = str(uuid.uuid4())[:8]

    num_vehicles = websocket.query_params.get("num_vehicles")
    if num_vehicles is None:
        num_vehicles = 1000  # Valor por defecto
    duration_sec = websocket.query_params.get("duration_sec")
    if duration_sec is None:
        duration_sec = 3600  # Valor por defecto

    fringe_factor = websocket.query_params.get("fringe_factor")
    if fringe_factor is None:
        fringe_factor = 10  # Valor por defecto

    trip_period = websocket.query_params.get("trip_period_sec")
    if trip_period is None:
        trip_period = 10  # Valor por defecto

    aggregation_period_sec = websocket.query_params.get("aggregation_period_sec")
    if aggregation_period_sec is None:
        aggregation_period_sec = 10  # Valor por defecto

    banned_roads = websocket.query_params.get("bannedStreets")
    if banned_roads is not None:
        banned_roads = unquote(banned_roads).split(",")
        print("Carreteras a cerrar durante la simulación: ", banned_roads)
    else:
        banned_roads = []
        print("No se han cerrado carreteras para la simulación.")


    simulation_params = sumoClass.simulationParams(num_vehicles, duration_sec,fringe_factor, aggregation_period_sec, trip_period)
    await websocket.send_json({"mensaje": "Iniciando simulacion de Pamplona.🚩"})
    # DETERMINA EL SISTEMA OPERATIVO SOBRE EL QUE SE EJECUTA LA APLICACION
    operativeSytemIsLinux= 1 if platform.system()=="Linux" else 0
    if operativeSytemIsLinux==1:
       sumo_home = sumo_home_linux
       ruta_output= ruta_output_linux
       ruta= ruta_linux
    else:
       sumo_home = sumo_home_windows
       ruta= ruta_windows
       ruta_output= ruta_output_windows

    print("Ruta de SUMO encontrada correctamente", sumo_home,"Operative system",platform.system())

    # 1. Generar tráfico aleatorio sobre una red de ejemplo (sancho el fuerte)
    print("Generando tráfico aleatorio")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            if operativeSytemIsLinux==1:
                net_file = os.path.join(ruta_linux, "pamplona.net.xml")
                print("Archivo NET creado correctamente", net_file)
                
            else:
                net_file = os.path.join(ruta_windows, "pamplona.net.xml")
                print("Archivo NET creado correctamente", net_file)
            
            route_file= os.path.join(ruta_output, f"pamplona_{uuid_simulation}.rou.xml")
            print("Archivo ROUT creado correctamente", route_file)
            trips_file= os.path.join(ruta_output, f"trips_{uuid_simulation}.trip.xml")
            print("Archivo ROUT creado correctamente", trips_file)
            weight_file= os.path.join(ruta_output, f"weights_{uuid_simulation}.xml")
            print("Archivo weights.xml creado correctamente", weight_file)

            banned_roads_str= ", ".join(banned_roads)

            random_trips = os.path.join(sumo_home, "tools", "randomTrips.py")
            if operativeSytemIsLinux==1:
                subprocess.run([
                    "python3", random_trips,
                    "-n", net_file,
                    # "-r", route_file,
                    "-o", trips_file,
                    "-e", duration_sec,  # Simular duration_sec segundos de tráfico
                    "--period", trip_period, # Aparece un coche cada trip_period segundos
                    "--fringe-factor", fringe_factor
                ], check=True)  
            else:
                subprocess.run([
                    "python", random_trips,
                    "-n", net_file,
                    # "-r", route_file,
                    "-o", trips_file,
                    "-e", duration_sec,  # Simular duration_sec segundos de tráfico
                    "--period", trip_period, # Aparece un coche cada trip_period segundos
                    "--fringe-factor", fringe_factor
                ], check=True)  

            if operativeSytemIsLinux==1:
                duarouter = "duarouter"
            else:
                duarouter = os.path.join(sumo_home, "bin", "duarouter")  # sin G


            if len(banned_roads)>1:
                weight_file_content=f""" <weights>"""
                for road in banned_roads:
                    weight_file_content += f"""<edge id="{road}" traveltime="100000"/>"""
                weight_file_content+=f""" </weights>"""
                
                try:
                    with open(weight_file, 'w') as f_additional:
                        f_additional.write(weight_file_content)
                    f_additional.close()
                    print("Archivo weights.xml creado correctamente", weight_file)
                except Exception as e:
                    f_additional.close()
                    print(f"Error al crear el archivo weights_{uuid_simulation}.xml:", e)

            
                subprocess.run([
                    duarouter, 
                    "-n", net_file, 
                    "-r", trips_file, 
                    "-o", route_file,
                    "--weight-files", weight_file, 
                    "--ignore-errors", "true",
                    "--no-warnings", "true"
                    ], check=True)

            else:
                subprocess.run([
                    duarouter, 
                    "-n", net_file, 
                    "-r", trips_file, 
                    "-o", route_file,
                    "--ignore-errors", "true",
                    "--no-warnings", "true"
                    ], check=True)    

            
            
            additional_file_content = f"""<additional>  
                                        <edgeData id="edgeEmissions" type="emissions" freq="{aggregation_period_sec}" file="{os.path.join(ruta_output, f"edgeEmissions_{uuid_simulation}.xml")}" excludeEmpty="true"/>
                                        <edgeData id="edgeTraffic" freq="{aggregation_period_sec}" file="{os.path.join(ruta_output, f"edgeTraffic_{uuid_simulation}.xml")}" excludeEmpty="true"/>
                                        <vType id="turismo" 
                                            vClass="passenger" 
                                            accel="2.6" 
                                            decel="4.5" 
                                            sigma="0.5" 
                                            length="5.0" 
                                            minGap="2.5" 
                                            maxSpeed="33.33" 
                                            color="white"
                                            emissionClass="HBEFA3/PC_G_EU4"/>
                                        
                                        <vType id="bus" 
                                            vClass="bus" 
                                            accel="1.5" 
                                            decel="4.0" 
                                            sigma="0.5" 
                                            length="12.0" 
                                            minGap="3.0" 
                                            maxSpeed="15.0" 
                                            color="orange"
                                            emissionClass="HBEFA4/PC_petrol_Euro-4"/>

                                        <vType id="camion" 
                                            vClass="truck" 
                                            accel="1.5" 
                                            decel="4.0" 
                                            sigma="0.5" 
                                            length="12.0" 
                                            minGap="3.0" 
                                            maxSpeed="15.0" 
                                            color="orange"
                                            emissionClass="HBEFA4/PC_diesel_Euro-4"/>
                                    </additional>"""
            
            try:
                with open(os.path.join(ruta_output, f"additional_{uuid_simulation}.add.xml"), 'w') as f_additional:
                    f_additional.write(additional_file_content)
                f_additional.close()
                print("Archivo additional.add.xml creado correctamente", os.path.join(ruta_output, f"additional_{uuid_simulation}.add.xml"))
            except Exception as e:
                f_additional.close()
                print("Error al crear el archivo additional.add.xml:", e)

            print("Archivo additional.add.xml: ", os.path.join(ruta_output, f"additional_{uuid_simulation}.add.xml"))

            if len(banned_roads)>1:
                net = sumolib.net.readNet(net_file)
                # Buscar todas las calles que son "entradas" (no tienen calles que entren en ellas)
                calles_entrada = []
                for edge in net.getEdges():
                    if len(edge.getIncoming()) == 0:
                        calles_entrada.append(edge.getID())
                edges_str = " ".join(calles_entrada)

                # CIERRE DE CALLE ANTES DEL CORTE, PARA PROBAR EL REROUTING DE LOS VEHICULOS EN LA SIMULACION
                additional_closedEdge_file_content =f"""<additional>
                                    <rerouter id="rerouter1" edges="{edges_str}" probability="1.0" >
                                        <interval begin="0" end="{duration_sec}">"""
                for road in banned_roads:
                    additional_closedEdge_file_content += f"""
                                            <closingReroute id="{road}" disallow="all"/>"""
                additional_closedEdge_file_content += f"""</interval>
                                    </rerouter>
                                </additional>"""
                
                print("Contenido del archivo closedEdge.add.xml: ", additional_closedEdge_file_content)
                
                try:
                    with open(os.path.join(ruta_output, f"closedEdge_{uuid_simulation}.add.xml"), 'w') as f_additional:
                        f_additional.write(additional_closedEdge_file_content)
                    f_additional.close()
                    print("Archivo closedEdge.add.xml creado correctamente", os.path.join(ruta_output, f"closedEdge_{uuid_simulation}.add.xml"))
                except Exception as e:
                    f_additional.close()
                    print(f"Error al crear el archivo closedEdge_{uuid_simulation}.add.xml", e)

                closeEdge_file = os.path.join(ruta_output, f"closedEdge_{uuid_simulation}.add.xml")
                closedFile=f"closedEdge_{uuid_simulation}.add.xml"
                print("Archivos adicionales de SUMO creados correctamente")
                
                print("Archivo closedEdge.add.xml: ", closeEdge_file)


            # crea el archivo de configuración SUMO
            config_file = os.path.join(ruta_output, f"simulation_{uuid_simulation}.sumocfg")

            config_file_content= f"""<?xml version="1.0" encoding="UTF-8"?>
                <configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.xsd">
                    <input>
                        <net-file value="{net_file}"/>
                        <route-files value="{route_file}"/>"""
            if len(banned_roads)>1:    
                config_file_content+=f"""
                        <additional-files value="additional_{uuid_simulation}.add.xml, closedEdge_{uuid_simulation}.add.xml"/>"""
            else:
                config_file_content+=f"""
                            <additional-files value="additional_{uuid_simulation}.add.xml"/>"""

            config_file_content+=f"""</input>
                    <routing>
                        <device.rerouting.probability value="1"/>
                        <device.rerouting.period value="10"/>"""
            
            if len(banned_roads)>1:    
                config_file_content+=f"""
                        <weight-files value="weights_{uuid_simulation}.xml"/>"""
                    
            config_file_content+=f"""</routing> 
                        <processing>
                            <ignore-route-errors value="true"/>
                            <ignore-accidents value="true"/>
                        </processing>
                    </configuration>"""
            

            print("Archivo de configuración SUMO creado correctamente", config_file)
            with open(config_file, 'w') as f:
                f.write(config_file_content)    
            
            print("Archivos de configuración SUMO generados correctamente")
            print("Archivo de configuración SUMO: ", config_file)

            if operativeSytemIsLinux==1:
                sumo = "sumo"
            else:
                sumo = os.path.join(sumo_home, "bin", "sumo")  # sin GUI
                
            print("Lanzando simulación con SUMO")
            initial_time = time.time()

            await websocket.send_json({"mensaje": "Lanzando simulación con SUMO de Pamplona.🚀"})
            try:
                subprocess.run([
                        sumo,
                        "-c", config_file,
                        "-b", "0",
                        "-e", duration_sec,  # Simular duration_sec segundos
                        "--emission-output.geo", "true",
                        # "-n", net_file,
                        # "-r", route_file,
                        "-v", "true",
                        "--device.rerouting.probability", "1",
                        "--device.rerouting.period", "1",
                        "--no-warnings", "true",
                        "--ignore-route-errors", "true"
                    ], check=True,capture_output=True, text=True)

                await websocket.send_json({"mensaje": "Simulación con SUMO finalizada correctamente.✅"})   
                # SI EL FICHERO EXISTE, LO BORRA PARA EVITAR PROBLEMAS DE PARSEO
                ruta_edgeEmissions_p = os.path.join(ruta_output, f"edgeEmissions_{uuid_simulation}.parquet")
                if os.path.exists(ruta_edgeEmissions_p):
                    print("Delete existing parquet file")
                    os.remove(ruta_edgeEmissions_p)

                await convertirEmissionsXmlToParquet(websocket,os.path.join(ruta_output, f"edgeEmissions_{uuid_simulation}.xml"),os.path.join(ruta_output, f"edgeEmissions_{uuid_simulation}.parquet"))
                await websocket.send_json({"mensaje": "Creado fichero parquet de Emisiones de Sancho el Fuerte.🗄️"})   
                print(f" Fichero de edgeEmissions_{uuid_simulation}.parquet creado")
                
                # SI EL FICHERO EXISTE, LO BORRA PARA EVITAR PROBLEMAS DE PARSEO
                # if os.path.exists(os.path.join(ruta_output, f"edgeEmissions_{uuid_simulation}.xml")):
                #     print("Delete existing parquet file")
                #     os.remove(os.path.join(ruta_output, f"edgeEmissions_{uuid_simulation}.xml"))

                # SI EL FICHERO EXISTE, LO BORRA PARA EVITAR PROBLEMAS DE PARSEO
                ruta_traffic_p = os.path.join(ruta_output, f"edgeTraffic_{uuid_simulation}.parquet")
                if os.path.exists(ruta_traffic_p):
                    print("Delete existing parquet file")
                    os.remove(ruta_traffic_p)

                await convertirTrafficXmlToParquet(websocket, os.path.join(ruta_output, f"edgeTraffic_{uuid_simulation}.xml"),os.path.join(ruta_output, f"edgeTraffic_{uuid_simulation}.parquet"))
                
                # SI EL FICHERO EXISTE, LO BORRA PARA EVITAR PROBLEMAS DE PARSEO
                # if os.path.exists(os.path.join(ruta_output, f"edgeTraffic_{uuid_simulation}.xml")):
                #     print("Delete existing parquet file")
                #     os.remove(os.path.join(ruta_output, f"edgeTraffic_{uuid_simulation}.xml"))

                print(f" Fichero de edgeTraffic_{uuid_simulation}.parquet creado")
                await websocket.send_json({"mensaje": "Creado fichero parquet de Tráfico de Sancho el Fuerte.🗄️"})

                simulation_to_postgres(f"edgeEmissions_{uuid_simulation}.parquet", f"edgeTraffic_{uuid_simulation}.parquet", sumoBD, simulation_params)
                await websocket.send_json({"mensaje": "Datos de la simulación cargados en Postgres.🗄️"})

                final_time = time.time()
                elapsed_time = final_time - initial_time
                print(f"Tiempo total de simulación y procesamiento: {elapsed_time:.2f} segundos")
                await websocket.send_json({"mensaje": f"Tiempo total de simulación y procesamiento: {elapsed_time:.2f} segundos"})

            except Exception as e:
                print(f"Error al ejecutar SUMO: {e}")
                raise HTTPException(status_code=500, detail=f"Error al ejecutar SUMO: {e}")

        except Exception as e:
            print(f"Error en la simulación: {e}")
            raise HTTPException(status_code=500, detail=f"Error en la simulación: {e}")
        
        finally:
            print("Finalizada la simulación con SUMO")
            await websocket.send_json({"fin_simulacion": "Finalizada la simulación con SUMO de Pamplona.✅"})

@app.websocket("/ws/getRoadsSanchoElFuerte")
async def getRoadsSanchoElFuerte(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"mensaje": "Iniciando descarga de carreteras de Sancho el Fuerte.🚩"})
    
    operativeSytemIsLinux= 0 if platform.system()=="Linux" else 1
    if operativeSytemIsLinux==0:
       sumo_home = "/usr/share/sumo"
    else:
        sumo_home = r"D:\Proyectos\01_SUMO"

    print("Ruta de SUMO encontrada correctamente", sumo_home)
    await websocket.send_json({"mensaje": "Ruta de SUMO encontrada correctamente."+ sumo_home})
    if operativeSytemIsLinux==0:
        net_file = "/tmp/zona-sancho-el-fuerte.net.xml"
    else:
        net_file = r"C:\Proyectos\twin-sumo-output\red_carreteras\zona-sancho-el-fuerte.net.xml"

    await websocket.send_json({"mensaje": "Iniciando descarga de red de Sancho el fuerte."})
    try:
        await websocket.send_json({"mensaje": "Iniciando envio calles Sancho el fuerte."})
        await convert_net_to_geojson_net(websocket,net_file)
        await websocket.send_json({"mensaje": "Iniciando envio calles Sancho el fuerte."})
    except Exception as e:
        await websocket.send_json({"mensaje": "Error al enviar calles de Sancho el fuerte."})
        await websocket.close()
        raise HTTPException(status_code=500, detail=str(e))
    else:
        await websocket.send_json({"mensaje": "Envío de calles de Sancho el fuerte finalizado correctamente.👍"})
    
    finally:
        await websocket.send_json({"mensaje": "Enviando calles de Sancho el fuerte."})
        await websocket.close()

@app.get("/getPamplonaStreets")
def getPamplonaStreets():
    
    road_types = ["highway.motorway", "highway.motorway_link","highway.motorway_junction", "highway.primary", "highway.secondary", "highway.tertiary", "highway.residential", "highway.living_street","highway.trunk","highway.trunk_link", "highway.primary_link", "highway.secondary_link", "highway.tertiary_link","highway.service","highway.trafficlight"]
    operativeSytemIsLinux= 1 if platform.system()=="Linux" else 0
    if operativeSytemIsLinux==1:
       ruta_output= ruta_output_linux
       net_file = os.path.join(ruta_linux, "pamplona.net.xml")
    else:
       ruta_output= ruta_output_windows
       net_file = os.path.join(ruta_windows, "pamplona.net.xml")

    net = sumolib.net.readNet(net_file)
    # 1. Leer el parquet (ajusta la ruta a tu archivo)    # 4. Crear el JSON final con geometría y datos de tráfico
    features = []
    for edge in net.getEdges():
        tipo=edge.getType()
        if edge.getType() not in road_types:
            continue
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y) for x, y in shape]

        properties = {
            "id": edge.getID(),
            "nombre": edge.getName() or "Calle sin nombre",
            "tipo": edge.getType(),
            "velocidad_max": edge.getSpeed() * 3.6, # Convertir m/s a km/h
            "carriles": edge.getLaneNumber(),
        }
        
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": properties
        }
        features.append(feature)
    
    return {
        "type": "FeatureCollection",
        "features": features
    }

@app.get("/get-emission-data")
def get_emission_data():
    
    operativeSytemIsLinux= 1 if platform.system()=="Linux" else 0
    if operativeSytemIsLinux==1:
       ruta_output= ruta_output_linux
       net_file = os.path.join(ruta_linux, "pamplona.net.xml")
    else:
       ruta_output= ruta_output_windows
       net_file = os.path.join(ruta_windows, "pamplona.net.xml")

    net = sumolib.net.readNet(net_file)
    # 1. Leer el parquet (ajusta la ruta a tu archivo)
    df = pd.read_parquet(os.path.join(ruta_output, "edgeEmissions.parquet"))
    df.sort_values(['interval_begin'], inplace=True)
    
    pollutants = ['CO_abs', 'CO2_abs', 'HC_abs', 'PMx_abs', 'NOx_abs', 'fuel_abs']
    
    # Pivot for each pollutant
    pivots = {}
    for pollutant in pollutants:
        df_pivot = df.pivot(index='id', columns='interval_begin', values=pollutant)
        df_pivot.columns = [str(int(c)) for c in df_pivot.columns]
        pivots[pollutant] = df_pivot
    

    # 4. Crear el JSON final con geometría y datos de tráfico
    features = []

    for edge_id in df['id'].unique():
        edge = net.getEdge(edge_id)
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y) for x, y in shape]

        properties = {
            "id": edge_id,
            "nombre": edge.getName() or "Calle sin nombre",
            "tipo": edge.getType(),
            "velocidad_max": edge.getSpeed() * 3.6, # Convertir m/s a km/h
            "carriles": edge.getLaneNumber(),
        }
        
        for pollutant in pollutants:
            if edge_id in pivots[pollutant].index:
                row = pivots[pollutant].loc[edge_id]
                properties[f"{pollutant}_por_tiempo"] = row.dropna().to_dict()
            else:
                properties[f"{pollutant}_por_tiempo"] = {}

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": properties
        }
        features.append(feature)
    
    return {
        "type": "FeatureCollection",
        "features": features
    }

@app.get("/get-emission-data/{file_name}")
def get_emission_data_by_file(file_name: str):

    operativeSytemIsLinux = 1 if platform.system() == "Linux" else 0
    if operativeSytemIsLinux == 1:
        ruta_output = ruta_output_linux
        net_file = os.path.join(ruta_linux, "pamplona.net.xml")
    else:
        ruta_output = ruta_output_windows
        net_file = os.path.join(ruta_windows, "pamplona.net.xml")

    net = sumolib.net.readNet(net_file)
    # 1. Leer el parquet especificado
    parquet_path = os.path.join(ruta_output, file_name)
    if not os.path.exists(parquet_path):
        raise HTTPException(status_code=404, detail=f"Archivo {file_name} no encontrado")
    df = pd.read_parquet(parquet_path)
    df.sort_values(['interval_begin'], inplace=True)

    pollutants = ['CO_abs', 'CO2_abs', 'HC_abs', 'PMx_abs', 'NOx_abs', 'fuel_abs']

    # Pivot for each pollutant
    pivots = {}
    for pollutant in pollutants:
        df_pivot = df.pivot(index='id', columns='interval_begin', values=pollutant)
        df_pivot.columns = [str(int(c)) for c in df_pivot.columns]
        pivots[pollutant] = df_pivot

    # 4. Crear el JSON final con geometría y datos de tráfico
    features = []

    for edge_id in df['id'].unique():
        edge = net.getEdge(edge_id)
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y) for x, y in shape]

        properties = {
            "id": edge_id,
            "nombre": edge.getName() or "Calle sin nombre",
            "tipo": edge.getType(),
            "velocidad_max": edge.getSpeed() * 3.6,  # Convertir m/s a km/h
            "carriles": edge.getLaneNumber(),
        }

        for pollutant in pollutants:
            if edge_id in pivots[pollutant].index:
                row = pivots[pollutant].loc[edge_id]
                properties[f"{pollutant}_por_tiempo"] = row.dropna().to_dict()
            else:
                properties[f"{pollutant}_por_tiempo"] = {}

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": properties
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features
    }

@app.get("/get-traffic-data")
def get_traffic_data():


    operativeSytemIsLinux= 1 if platform.system()=="Linux" else 0
    if operativeSytemIsLinux==1:
       ruta_output= ruta_output_linux
       net_file = os.path.join(ruta_linux, "pamplona.net.xml")
    else:
       ruta_output= ruta_output_windows
       net_file = os.path.join(ruta_windows, "pamplona.net.xml")

    net = sumolib.net.readNet(net_file)
    # 1. Leer el parquet (ajusta la ruta a tu archivo)
    df = pd.read_parquet(os.path.join(ruta_output, "edgeTraffic.parquet"))
    df.sort_values(['interval_begin'])
    
    metrics = ['density', 'occupancy', 'speed', 'flow', 'waitingTime']
    
    # Pivot for each metric
    pivots = {}
    for metric in metrics:
        df_pivot = df.pivot(index='id', columns='interval_begin', values=metric)
        df_pivot.columns = [str(int(c)) for c in df_pivot.columns]
        pivots[metric] = df_pivot
    

    # 4. Crear el JSON final con geometría y datos de tráfico
    features = []
    for edge_id in df['id'].unique():
        edge = net.getEdge(edge_id)
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y) for x, y in shape]

        properties = {
            "id": edge_id,
            "nombre": edge.getName() or "Calle sin nombre",
            "tipo": edge.getType(),
            "velocidad_max": edge.getSpeed() * 3.6, # Convertir m/s a km/h
            "carriles": edge.getLaneNumber(),
        }
        
        for metric in metrics:
            if edge_id in pivots[metric].index:
                row = pivots[metric].loc[edge_id]
                properties[f"{metric}_por_tiempo"] = row.dropna().to_dict()
            else:
                properties[f"{metric}_por_tiempo"] = {}

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": properties
        }
        features.append(feature)
    
    return {
        "type": "FeatureCollection",
        "features": features
    }

@app.get("/get-traffic-data/{file_name}")
def get_traffic_data_by_file(file_name: str):
    operativeSytemIsLinux = 1 if platform.system() == "Linux" else 0
    if operativeSytemIsLinux == 1:
        ruta_output = ruta_output_linux
        net_file = os.path.join(ruta_linux, "pamplona.net.xml")
    else:
        ruta_output = ruta_output_windows
        net_file = os.path.join(ruta_windows, "pamplona.net.xml")

    net = sumolib.net.readNet(net_file)
    parquet_path = os.path.join(ruta_output, file_name)
    if not os.path.exists(parquet_path):
        raise HTTPException(status_code=404, detail=f"Archivo {file_name} no encontrado")

    df = pd.read_parquet(parquet_path)
    df.sort_values(['interval_begin'], inplace=True)

    metrics = ['density', 'occupancy', 'speed', 'flow', 'waiting_time']

    pivots = {}
    for metric in metrics:
        if metric in df.columns:
            df_pivot = df.pivot(index='id', columns='interval_begin', values=metric)
            df_pivot.columns = [str(int(c)) for c in df_pivot.columns]
            pivots[metric] = df_pivot

    features = []
    for edge_id in df['id'].unique():
        try:
            edge = net.getEdge(edge_id)
        except Exception:
            continue
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y) for x, y in shape]

        properties = {
            "id": edge_id,
            "nombre": edge.getName() or "Calle sin nombre",
            "tipo": edge.getType(),
            "velocidad_max": edge.getSpeed() * 3.6,
            "carriles": edge.getLaneNumber(),
        }

        for metric in metrics:
            if metric in pivots and edge_id in pivots[metric].index:
                row = pivots[metric].loc[edge_id]
                properties[f"{metric}_por_tiempo"] = row.dropna().to_dict()
            else:
                properties[f"{metric}_por_tiempo"] = {}

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": properties
        })

    return {"type": "FeatureCollection", "features": features}

@app.websocket("/ws/simulationTraci")
async def websocket_simulation(websocket: WebSocket):
    await websocket.accept()
    bbox_str = websocket.query_params.get("bbox")
    forbiddenRoads = websocket.query_params.get("forbiddenRoads")
    num_vehicles = int(websocket.query_params.get("num_vehicles"))
    print("Numero de vehiculos: ", num_vehicles)
    duration_sec = int(websocket.query_params.get("duration_sec"))
    print("Duracion de la simulacion: ", duration_sec)
    zonaSnachoFuerte = int(websocket.query_params.get("zonaSnachoFuerte"))
    print("Zona Snacho Fuerte: ", zonaSnachoFuerte)
    
    forbiddenRoads = unquote(forbiddenRoads)
    forbiddenRoadsArray = json.loads(forbiddenRoads)

    if zonaSnachoFuerte==0: 
        if not bbox_str:
            raise HTTPException(status_code=400, detail="Missing bbox parameter")
        else:
            try:
                bbox = sumoClass.BoundingBox(**json.loads(bbox_str))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid bbox format")
    
   
    #DETERMINA EL SISTEMA OPERATIVO SOBRE EL QUE SE EJECUTA LA APLICACION
    operativeSytemIsLinux= 0 if platform.system()=="Linux" else 1
    if operativeSytemIsLinux==0:
       sumo_home = sumo_home_linux
    else:
        sumo_home = sumo_home_windows
    
    print("Ruta de SUMO encontrada correctamente", sumo_home,"Operative system",platform.system())
    await websocket.send_json({"mensaje":"Ruta de SUMO encontrada correctamente"+ sumo_home +"| Operative system:"+platform.system()})

    with tempfile.TemporaryDirectory() as tmpdir:
        print("Directorio temporal creado correctamente", tmpdir)
        osm_file = os.path.join(tmpdir, "mapa.osm.xml")
        print("Archivo OSM creado correctamente", osm_file)
        net_file = os.path.join(tmpdir, "mapa.net.xml")
        print("Archivo NET creado correctamente", net_file)
        route_file = os.path.join(tmpdir, "mapa.rou.xml")
        print("Archivo ROUT creado correctamente", route_file)
        type_vehicles_file=os.path.join(tmpdir,"tipos_vehiculos.add.xml")
        print("Tipos de vehiculos", type_vehicles_file)
        
        detalles_viajes = os.path.join(tmpdir, "detalles_viajes.xml")
        print("Archivo detalles_viajes creado correctamente", detalles_viajes)

        informe_final = os.path.join(tmpdir, "informe_final.xml")
        print("Archivo informe_final creado correctamente", informe_final)
        
        emisiones_por_calle = os.path.join(tmpdir, "emisiones_por_calle.xml")
        print("Archivo emisiones_por_calle creado correctamente", emisiones_por_calle)

        # 1. Descarga (usando el método de requests que vimos antes)
        print("Descargando datos de OSM")
        await websocket.send_json({"mensaje":"Descargando datos OSM"})

        if zonaSnachoFuerte==1:
            if operativeSytemIsLinux==0:
                net_file = os.path.join(ruta_linux, "zona-sancho-el-fuerte.net.xml")
            else:
                net_file = os.path.join(ruta_windows, "zona-sancho-el-fuerte.net.xml")
                route_file = os.path.join(ruta_windows, "zona-sancho-el-fuerte_taz.net.rou.xml")
            
            sumo_types = ",".join([f"highway.{t}" for t in ["motorway", "motorway_link","motorway_junction", "primary", "secondary", "tertiary", "residential", "living_street","trunk","trunk_link", "primary_link", "secondary_link", "tertiary_link","service","trafficlight"]])
            await websocket.send_json({"mensaje":"Red de sancho el fuerte descargada correctamente"})
        else:
            await download_osm_data(bbox, osm_file, websocket)
            print("Datos de OSM descargados correctamente")
            await websocket.send_json({"mensaje":"Datos de OSM descargados correctamente"})

            sumo_types = ",".join([f"highway.{t}" for t in bbox.road_types])
        
            # 2. Generar red de SUMO
            print("Generando red de SUMO")
            await websocket.send_json({"mensaje":"Generando red de SUMO de OpenStreetMap"})
        
        
        if zonaSnachoFuerte==0:
            netconvert = None
            if operativeSytemIsLinux==0:
                netconvert= "netconvert"
            else:
                netconvert= os.path.join(sumo_home, "bin", "netconvert")

            subprocess.run([
                netconvert,
                "--osm-files", osm_file,
                "--output-file", net_file,
                "--geometry.remove", "true",
                "--proj.utm", "true",
                "--keep-edges.by-type", sumo_types, # <--- Mantiene solo estos tipos
                "--remove-edges.isolated", "true",
                "--tls.guess","true",
                "--tls.join", "true"
            ], check=True)

        print("Red generada correctamente")
        await websocket.send_json({"mensaje":"Red generada correctamente"})

        # 3. Generar tráfico aleatorio
        print("Generando tráfico aleatorio")
        await websocket.send_json({"mensaje":"Generando tráfico aleatorio"})

        period = duration_sec / num_vehicles if num_vehicles > 0 else 100
        print("Periodo: ", period)
        await websocket.send_json({"mensaje":"Periodo de aparicion de vehiculos: "+ str(period)})
    
        random_trips = os.path.join(sumo_home, "tools", "randomTrips.py")
        if operativeSytemIsLinux==0:
            procesoTraffic=subprocess.run([
                "python3", random_trips,
                "-n", net_file,
                "-r", route_file,
                "-e", str(duration_sec),  # Simular 3600 segundos de tráfico
                "--period", str(period), # Aparece un coche cada 0.5 segundos
                "--fringe-factor", "10"
            ], check=True)  
        else:
            procesoTraffic=subprocess.run([
                "python", random_trips,
                "-n", net_file,
                "-r", route_file,
                "-e", str(duration_sec),  # Simular 3600 segundos de tráfico
                "--period", str(period), # Aparece un coche cada 0.5 segundos
                "--fringe-factor", "10"
            ], check=True)  

            print("Tráfico generado correctamente para "+ str(num_vehicles) + " vehiculos") 
            await websocket.send_json({"mensaje":"Tráfico generado correctamente para "+ str(num_vehicles) + " vehiculos"})

            # Primero crea el archivo de configuración SUMO
            print("Creando archivo de configuración SUMO")
            await websocket.send_json({"mensaje":"Creando archivo de configuración SUMO"})
            
            if operativeSytemIsLinux==0:
                config_file = os.path.join(ruta_linux, "simulation.sumocfg")
            else:
                config_file = os.path.join(tmpdir, "simulation.sumocfg")

            print("Archivo de configuración SUMO creado correctamente", config_file)
            await websocket.send_json({"mensaje":f"Archivo de configuración SUMO creado correctamente {config_file}"})
            
            # Crear el archivo .sumocfg
            with open(config_file, 'w') as f:
                f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
                    <configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.xsd">
                        <input>
                            <net-file value="{net_file}"/>
                            <route-files value="{route_file}"/>
                        </input>
                        <time>
                            <begin value="0"/>
                            <end value="{str(duration_sec)}"/>
                        </time>
                        <output>
                            <tripinfo-output value="tripinfos.xml"/>
                        </output>
                        <routing>
                            <device.rerouting.probability value="1.0"/>
                            <device.rerouting.period value="10"/>
                        </routing>
                    </configuration>""")

            print("Archivo de configuración SUMO generados correctamente")

        if zonaSnachoFuerte==1:
            if operativeSytemIsLinux==1:
                config_file = os.path.join(ruta_linux, "simulation.sumocfg")
            else:
                config_file = os.path.join(tmpdir, "simulation.sumocfg")
        # 4. Iniciar simulación con TraCI
        try:
            if operativeSytemIsLinux==0:
                traciBinary = "sumo"  # sin GUI
            else:
                traciBinary = os.path.join(sumo_home, "bin", "sumo")  # sin GUI
        # 4. Iniciar simulación con TraCI
            
            print("Iniciando simulación")
            await websocket.send_json({"mensaje":"Iniciando simulación"})
            traci.start([
                traciBinary,
                "-c", config_file,
                "--step-length", "0.1",  # 1 segundo por paso
            ])
            
            # CALLES
            edges=traci.edge.getIDList()
            print("Total de calles: ", len(edges))
            await websocket.send_json({"mensaje":"Total de calles: "+ str(len(edges))})

            lanes=traci.lane.getIDList()
            print("Total de carriles: ", len(lanes))
            await websocket.send_json({"mensaje":"Total de carriles: "+ str(len(lanes))})

            # PROHIBIR CALLES
            for edge_id in edges:
                if (edge_id in forbiddenRoadsArray):
                    traci.edge.setAllowed(edge_id, [])
                    traci.edge.setEffort(edge_id, 999999)
                    print("Calle prohibida: ", edge_id)
                    await websocket.send_json({"mensaje":"Calle prohibida: "+ edge_id})

            for lane_id in lanes:
                if (lane_id in forbiddenRoadsArray):
                    traci.lane.setAllowed(lane_id, [])
                    print("Carril prohibido: ", lane_id)
                    await websocket.send_json({"mensaje":"Carril prohibido: "+ lane_id})

            #SEMAFOROS
            # Ejecutar la simulación paso a paso
            step = 0
            await websocket.send_json({"simulationState":"1"})
            total_co2_mg = 0.0
            step_co2 = 0.0

            # Inicializar el diccionario de semáforos
            trafficLightDictionary = []
            net = sumolib.net.readNet(net_file, withInternal=True, withPedestrianConnections=True,withLatestPrograms=True)

            while step < duration_sec and traci.simulation.getMinExpectedNumber() > 0:
                try:
                    traci.simulationStep()  # Avanzar un paso

                    try:
                        # Intentamos leer un mensaje sin bloquear la simulación (timeout corto)
                        data = await asyncio.wait_for(websocket.receive_json(), timeout=0.02)

                        if (data.get("action")=="insert_flow"):
                            numberOfCars=int(data.get("numberOfCars"))
                            origin=data.get("origin")
                            destination = data.get("destination")
                            route_calculada = traci.simulation.findRoute(data.get("origin"),data.get("destination"))
                            uuid_str=str(uuid.uuid4().int)
                            route_id = "route_" + uuid_str
                            traci.route.add(route_id, route_calculada.edges)
                            print(f"Creada ruta {route_id} de {origin} a {destination}")
                            i=0
                            while i<numberOfCars:
                                uuid_vehicle=str(uuid.uuid4().int)
                                traci.vehicle.add(uuid_vehicle,route_id)
                                traci.vehicle.rerouteTraveltime(uuid_vehicle)
                                print(f"Vehículo {uuid_vehicle} insertador en ruta {route_id}")
                                await websocket.send_json({"vehicle_inyect":"Vehículo "+uuid_vehicle +" insertado en ruta "+ route_id})
                                i+=1
                            
                        if data.get("action") == "close_edge":
                            edge_id = data.get("edge_id")
                            await websocket.send_json({"calle_cerrada":"Cerrando calle " + edge_id})
                            try:
                                traci.lane.setAllowed(edge_id, ["all"])  
                                traci.lane.setMaxSpeed(edge_id, 0.1)
                                await websocket.send_json({"calle_cerrada":"Calle cerrada correctamente " + edge_id})
                            except Exception as e:
                                await websocket.send_json({"calle_cerrada":"Error al cerrar la calle " + edge_id + " " + str(e)})
                            
                            # Confirmar al frontal
                            await websocket.send_json({"type": "status", "msg": f"Calle {edge_id} cerrada"})

                        if data.get("action") == "open_edge":
                            edge_id = data.get("edge_id")
                            await websocket.send_json({"calle_abierta":"Abriendo calle " + edge_id})
                            try:
                                traci.lane.setAllowed(edge_id,  ["all"])  # Prohibir paso
                                traci.lane.setMaxSpeed(edge_id, 13.89) # Avisar al GPS
                                await websocket.send_json({"calle_abierta":"Calle abierta correctamente " + edge_id})
                            except Exception as e:
                                await websocket.send_json({"calle_abierta":"Error al abrir la calle " + edge_id + " " + str(e)})
                            
                            # Confirmar al frontal
                            await websocket.send_json({"type": "status", "msg": f"Calle {edge_id} abierta"})
                    
                    except asyncio.TimeoutError:
                        # No hay mensajes nuevos, seguimos la simulación
                        pass
                    # Esto hace que los vehiculos que han terminado la ruta desaparezcan
                    for vehicleID in traci.simulation.getArrivedIDList():
                        vehiculo={
                            "id": vehicleID
                        }
                        await websocket.send_json({"vehiculo_finalizado":vehiculo})

                    vehicles_at_step = []
                    for veh in traci.vehicle.getIDList():
                        traci.vehicle.rerouteTraveltime(veh)
                        # Obtener posición (x, y) en la proyección de SUMO
                        x, y = traci.vehicle.getPosition(veh)
                        step_co2 += traci.vehicle.getCO2Emission(veh)
                        
                        # Convertir a lon/lat (SUMO usa coordenadas proyectadas)
                        lon, lat = traci.simulation.convertGeo(x, y)
                        
                        # Obtener otros datos útiles
                        speed = traci.vehicle.getSpeed(veh)
                        angle = traci.vehicle.getAngle(veh)
                        
                        vehiculo={
                            "id": veh,
                            "longitude": lon,
                            "latitude": lat,
                            "speed": speed,
                            "angle": angle,
                            "time": step
                        }

                        await websocket.send_json({"vehiculo":vehiculo})
                    
                    # Esto hace que los vehiculos que han terminado la ruta desaparezcan
                    for vehicleID in traci.simulation.getArrivedIDList():
                        vehiculo={
                            "id": vehicleID
                        }
                        await websocket.send_json({"vehiculo_finalizado":vehiculo})

                    await asyncio.sleep(0.01)
                    # INSERCION DE SEMAFOROS
                    
                    lista_semaforos = traci.trafficlight.getIDList()
                    # # Semaforos
                    for tflID in traci.trafficlight.getIDList():
                        position=None
                        if tflID.startswith("GS_"):
                            position = traci.junction.getPosition(tflID[3:len(tflID)])
                        else:
                            position = traci.junction.getPosition(tflID)
                        
                        # programs = traci.trafficlight.getAllProgramLogics(tflID)
                        lon, lat = traci.simulation.convertGeo(position[0], position[1])
                        state=traci.trafficlight.getRedYellowGreenState(tflID)

                        tfl ={
                            "id": tflID,
                            "longitude": lon,
                            "latitude": lat,
                            "state": state,
                            "color": getTrafficLightColor(state[0]),
                            # "programs": programs
                        }

                        await websocket.send_json({"trafficlight":tfl})

                    await asyncio.sleep(0.01)
                    
                    step += 1
                    await websocket.send_json({"step": step})
                except traci.TraCIException as e:
                    if "has no valid route" in str(e):
                        print("Detectado error de ruta, saltando vehículo conflictivo...")
                        # El parámetro --ignore-route-errors en el start suele bastar,
                        # pero aquí podrías manejar lógica extra.
                

            
            total_co2_kg = total_co2_mg / 1000000
            print(f"Total CO2: {total_co2_kg} kg")
            await websocket.send_json({
               "final_report": {
                    "total_co2_kg": round(total_co2_kg, 2),
                    "equivalent_trees_day": round(total_co2_kg / 0.06, 2) # Un árbol absorbe aprox 60g/día
                }
            })

            # 1.1. Obtener emisiones por calle
            emisiones_por_calle = {}
            for edge_id in traci.edge.getIDList():
                emisiones_por_calle[edge_id] = traci.edge.getCO2Emission(edge_id)

            # 1.2. Calcular emisiones totales
            total_co2_mg = sum(emisiones_por_calle.values())
            total_co2_kg = total_co2_mg / 1_000_000

            # 1.3. Preparar estadísticas
            stats = {
                "vehiculos_totales": traci.simulation.getArrivedNumber() + traci.simulation.getMinExpectedNumber(),
                "emisiones_co2_actuales": total_co2_kg
            }
            print(f"Resumen de la simulación: {stats}")
            await websocket.send_json({"stats":stats})
            
            # 2. Leer el archivo de emisiones generado
            try:
                with open(emisiones_por_calle, 'r') as f:
                    # Leemos todo el contenido
                    contenido = f.read()
                    # Enviamos el contenido crudo al frontend
                    # (El frontend tendrá que parsear este XML)
                    await websocket.send_json({"emisiones_xml": contenido})
                    print("Archivo de emisiones enviado al frontend")
            except Exception as e:
                print(f"Error leyendo el archivo de emisiones: {e}")

            # Cerrar TraCI
            traci.close()
            print("Simulación finalizada correctamente")
            await websocket.send_json({"mensaje":"Simulación finalizada correctamente"})
            await websocket.send_json({"simulationState":"0"})

            await websocket.close()
            
            
        except Exception as e:
            print(f"Error en la simulación:")
            await websocket.send_json({"mensaje":"Error en la simulación: "+str(e)})
            if traci.isLoaded():
                traci.close()
            raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/getRoads")
async def get_roads_websocket(websocket: WebSocket):
    await websocket.accept()
    payload = websocket.query_params.get("payload")
    if not payload:
        await websocket.send_json({"error": "No se proporcionó payload"})
        return
    try:
        payload = sumoClass.BoundingBox(**json.loads(payload))
    except json.JSONDecodeError:
        await websocket.send_json({"error": "payload inválido"})
        return
    
    await websocket.send_json({"mensaje": "payload recibido correctamente"})

    
    sumo_home = os.environ.get("SUMO_HOME")
    if not sumo_home:
        sumo_home = r"D:\Proyectos\01_SUMO"

    print("Ruta de SUMO encontrada correctamente", sumo_home)
    with tempfile.TemporaryDirectory() as tmpdir:
        osm_file = os.path.join(tmpdir, "mapa.osm.xml")
        net_file = os.path.join(tmpdir, "mapa.net.xml")

        try:
            # 1. Descarga (usando el método de requests que vimos antes)
            print("Descargando datos de OSM")
            await download_osm_data_overpass(payload, osm_file, websocket)
            print("Datos de OSM descargados correctamente")

            sumo_types = ",".join([f"highway.{t}" for t in payload.road_types])
            print("Tipos de carreteras seleccionados correctamente")
            # 2. Generar red de SUMO
            print("Generando red de SUMO")
            subprocess.run([
                os.path.join(sumo_home, "bin", "netconvert"),
                "--osm-files", osm_file,
                "--output-file", net_file,
                "--geometry.remove", "true",
                "--junctions.join", "true",
                "--proj.utm", "true",
                "--keep-edges.by-type", sumo_types, # <--- Mantiene solo estos tipos
                "--remove-edges.isolated", "true",
                "--output.street-names", "true",
                "--tls.guess","true",
                "--tls.join", "true"
            ], check=True)  
            print("Red de SUMO generada correctamente")
            
            # 3. FUNCION DE LEER LA RED GENERADA DE OSM
            net = sumolib.net.readNet(net_file, withInternal=True, withPedestrianConnections=True,withLatestPrograms=True)
            
            # INSERCION DE CARRETERAS
            for edge in net.getEdges():
                for lane in edge.getLanes():
                    # Obtenemos la geometría (forma) de la carretera
                    # Convertimos las coordenadas internas de SUMO a Lon/Lat
                    idLane=lane.getID()
                    shape = lane.getShape()
                    coords = [net.convertXY2LonLat(x, y) for x, y in shape]

                    velocityStyle = getVelocityStyle(lane.getSpeed() * 3.6)

                    # Extraemos las propiedades que queremos
                    # Nota: edge.getName() devuelve el nombre de la calle de OSM
                    print("Edge ID:", idLane)
                    properties = {
                        "type": "lane",
                        "id": idLane,
                        "nombre": edge.getName() or "Calle sin nombre",
                        "tipo": edge.getType(),
                        "velocidad_max": lane.getSpeed() * 3.6, # Convertir m/s a km/h
                        # "carriles": edge.getLaneNumber(),
                        "tamaño": lane.getLength(),
                        # "origen": edge,
                        # "destino": edge.getTo(),
                        # "prioridad": edge.getPriority(),    
                        "prohibida": False,
                        "color": velocityStyle,
                        # "orientation": lane.getAngle(lane.getEdgeID(),None),
                        "edgeID": edge.getID()
                    }

                    feature = {
                        "type": "feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": coords
                        },
                        "properties": properties
                    }
                    await websocket.send_json(feature)
            await websocket.send_json({"mensaje": "Descarga de carreteras finalizada correctamente👍"})
            
            # INSERCION DE SEMAFOROS
            # Obtener todas uniones de la red entre lanes
            for junction in net.getNodes():
                idJunction =junction.getID()
                # Obtener el polígono de la unión
                shape = junction.getShape()
                coordinates = []
                for x, y in shape:
                    lon, lat = net.convertXY2LonLat(x, y)
                    coordinates.append([lon, lat])

                if coordinates:
                    coordinates.append(coordinates[0])

                feature = {
                    "type": "feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [coordinates]
                    },
                    "properties": {
                        "id": idJunction,
                        "type": "junction",
                    }
                }
                
                await websocket.send_json(feature)
            await websocket.send_json({"mensaje": "Descarga de uniones finalizada correctamente👍"})

            # INSERCION DE SEMAFOROS
            semaforos_detallados = []

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
                    
                    # Calculamos la orientación para que en Cesium no miren a Cuenca
                    # angulo = lane_entrada.getAngle(relativePos=-1)
                    
                    semaforos_detallados.append({
                        "id": f"{tls_id}_{link_index}",
                        "lon": lon,
                        "lat": lat,
                        # "heading": angulo
                    })

                    feature = {
                        "type": "feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [lon, lat]
                        },
                        "properties": {
                            "id": f"{tls_id}_{link_index}",
                            "type": "trafficlight",
                        }
                    }
                    await websocket.send_json(feature)


            await websocket.send_json({"mensaje": "Descarga de semáforos finalizada correctamente👍 Nº:" + str(len(semaforos_detallados))})
            await websocket.close()
        except Exception as e:
            await websocket.send_json({"mensaje": "Error en la descarga de carreteras: "+str(e)})
            await websocket.close()
            raise HTTPException(status_code=500, detail=str(e))
        
        finally:

            await websocket.send_json({"mensaje": "Descarga de carreteras finalizada "})
            await websocket.close()

@app.get("/callesPamplona")
async def get_calles_geojson():
    # Esta query de PostGIS es la forma más rápida de generar un GeoJSON
    query = """
        SELECT jsonb_build_object(
            'type',     'FeatureCollection',
            'features', jsonb_agg(features.feature)
        )
        FROM (
          SELECT jsonb_build_object(
            'type',       'Feature',
            'id',         id,
            'max_speed', velocidad,
            'geometry',   ST_AsGeoJSON(geom)::jsonb,
            'properties', jsonb_build_object(
                'id', id,
                'max_speed', velocidad,
                'length', longitud,
                'name', edge_name
            )
          ) AS feature
          FROM calles_pamplona
        ) AS features;
    """
    print("Ejecutando query para obtener calles de Pamplona en GeoJSON")
    print("Query:", query)
    # Ejecuta la query en tu conexión de base de datos y devuelve el resultado
    conn = psycopg2.connect("host=duckdb port=5432 dbname=sumo user=admin password=admin")
    cur = conn.cursor()
    cur.execute(query)
    resultado = cur.fetchone()[0]
    cur.close()
    conn.close()
    return resultado

@app.get("/carrilesPamplona")
async def get_carriles_geojson():
    # Esta query de PostGIS es la forma más rápida de generar un GeoJSON
    query = """
        SELECT jsonb_build_object(
            'type',     'FeatureCollection',
            'features', jsonb_agg(features.feature)
        )
        FROM (
          SELECT jsonb_build_object(
            'type',       'Feature',
            'id',         lane_id,
            'max_speed', velocidad_max,
            'geometry',   ST_AsGeoJSON(geom)::jsonb,
            'properties', jsonb_build_object(
                'id', lane_id,
                'index', indice_carril,
                'max_speed', velocidad_max,
                'width', ancho,
                'permission', permisos,
                'calle_id', edge_id
            )
          ) AS feature
          FROM carriles_pamplona
        ) AS features;
    """
    print("Ejecutando query para obtener carriles de Pamplona en GeoJSON")
    print("Query:", query)
    # Ejecuta la query en tu conexión de base de datos y devuelve el resultado
    conn = psycopg2.connect("host=duckdb port=5432 dbname=sumo user=admin password=admin")
    cur = conn.cursor()
    cur.execute(query)
    resultado = cur.fetchone()[0]
    cur.close()
    conn.close()
    return resultado

@app.get("/nodosPamplona")
async def get_nodos_geojson():
    # Esta query de PostGIS es la forma más rápida de generar un GeoJSON
    query = """
        SELECT jsonb_build_object(
            'type',     'FeatureCollection',
            'features', jsonb_agg(features.feature)
        )
        FROM (
          SELECT jsonb_build_object(
            'type',       'Feature',
            'id',         node_id,
            'tipo_control', tipo_control,
            'geometry',   ST_AsGeoJSON(shape)::jsonb,
            'properties', jsonb_build_object(
                'id', node_id,
                'tipo_control', tipo_control
            )
          ) AS feature
          FROM nodos_pamplona
        ) AS features;
    """
    print("Ejecutando query para obtener nodos de Pamplona en GeoJSON")
    print("Query:", query)
    # Ejecuta la query en tu conexión de base de datos y devuelve el resultado
    conn = psycopg2.connect("host=duckdb port=5432 dbname=sumo user=admin password=admin")
    cur = conn.cursor()
    cur.execute(query)
    resultado = cur.fetchone()[0]
    cur.close()
    conn.close()
    return resultado

@app.get("/autobusesPamplona")
async def get_autobuses_geojson():
    
    try:
        url_get = "https://bi.plataformaciudad.pamplona.es/pentaho/plugin/cda/api/doQuery?path=/public/sc_pamplona_pro/verticals/sql/urbanmobility_vehicle.cda&dataAccessId=urbanmobility_vehicle_lastdata_geojson&_TRUST_USER_=opendata_sc_pamplona"
        response = requests.get(url_get)

        data = response.json()
        # 1. Extraemos el string de la primera fila y primera columna
        geojson_str = data['result']['data'][0][0]
        # 2. Convertimos el string a un objeto JSON
        geojson_data = json.loads(geojson_str)
        return geojson_data
    
    except Exception as e:
        print("Error al obtener datos de autobuses de Pamplona:", str(e))

# SIMULACIONES 
@app.get("/simulations")
async def get_simulations():
    
    query = """
        SELECT id_simulation, "date", num_vehicles, duration_sec, fringe_factor, trip_period, aggregation_period, file_emissions, file_traffic
        FROM public.simulations
        order by "date" desc;
    """
    try:
        connection=sumoBD
        conn = psycopg2.connect(
            host=connection.host,
            port=connection.port,
            dbname=connection.dbname,
            user=connection.user,
            password=connection.password
        )
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        # Get column names
        columns = [desc[0] for desc in cur.description]
        # Convert to list of dicts
        result = [dict(zip(columns, row)) for row in rows]
        cur.close()
        conn.close()
        return result
    except Exception as e:
        print("Error al obtener datos de simulaciones:", str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/simulations/{id_simulation}")
async def get_simulation(id_simulation: str):
    query = """
        SELECT id_simulation, "date", num_vehicles, duration_sec, fringe_factor, trip_period, aggregation_period, file_emissions, file_traffic
        FROM public.simulations
        WHERE id_simulation = %s;
    """
    try:
        connection=sumoBD
        conn = psycopg2.connect(
            host=connection.host,
            port=connection.port,
            dbname=connection.dbname,
            user=connection.user,
            password=connection.password
        )
        cur = conn.cursor()
        cur.execute(query, (id_simulation,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Simulación no encontrada")
        # Get column names
        columns = [desc[0] for desc in cur.description]
        # Convert to dict
        result = dict(zip(columns, row))
        cur.close()
        conn.close()
        return result
    except HTTPException:
        raise
    except Exception as e:
        print("Error al obtener datos de la simulación:", str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get-emission-data/{file_name}")
def get_emission_data_by_file(file_name: str):

    operativeSytemIsLinux = 1 if platform.system() == "Linux" else 0
    if operativeSytemIsLinux == 1:
        ruta_output = ruta_output_linux
        net_file = os.path.join(ruta_linux, "pamplona.net.xml")
    else:
        ruta_output = ruta_output_windows
        net_file = os.path.join(ruta_windows, "pamplona.net.xml")

    net = sumolib.net.readNet(net_file)
    # 1. Leer el parquet especificado
    parquet_path = os.path.join(ruta_output, file_name)
    if not os.path.exists(parquet_path):
        raise HTTPException(status_code=404, detail=f"Archivo {file_name} no encontrado")
    df = pd.read_parquet(parquet_path)
    df.sort_values(['interval_begin'], inplace=True)

    pollutants = ['CO_abs', 'CO2_abs', 'HC_abs', 'PMx_abs', 'NOx_abs', 'fuel_abs']

    # Pivot for each pollutant
    pivots = {}
    for pollutant in pollutants:
        df_pivot = df.pivot(index='id', columns='interval_begin', values=pollutant)
        df_pivot = df.dropna(subset=['NOx_perVeh'])
        df_pivot.columns = [str(int(c)) for c in df_pivot.columns]
        pivots[pollutant] = df_pivot

    # 4. Crear el JSON final con geometría y datos de tráfico
    features = []

    for edge_id in df['id'].unique():
        edge = net.getEdge(edge_id)
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y) for x, y in shape]

        properties = {
            "id": edge_id,
            "nombre": edge.getName() or "Calle sin nombre",
            "tipo": edge.getType(),
            "velocidad_max": edge.getSpeed() * 3.6,  # Convertir m/s a km/h
            "carriles": edge.getLaneNumber(),
        }

        for pollutant in pollutants:
            if edge_id in pivots[pollutant].index:
                row = pivots[pollutant].loc[edge_id]
                properties[f"{pollutant}_por_tiempo"] = row.dropna().to_dict()
            else:
                properties[f"{pollutant}_por_tiempo"] = {}

        
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": properties
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features
    }
    



