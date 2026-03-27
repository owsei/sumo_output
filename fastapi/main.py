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

# sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class BoundingBox(BaseModel):
    west: float
    south: float
    east: float
    north: float
    road_types: Optional[List[str]] = ["motorway", "primary", "secondary", "tertiary", "residential"]

class SimulationParams(BaseModel):
    num_vehicles: int = 50
    duration_sec: int = 300
    # Lista de IDs de "edges" (vías) prohibidas
    blocked_edges: List[str] = []

class  MensajeSocket(BaseModel):
    mensaje: str
    color: str
    tipo: str


class configuracionSumo(BaseModel):
    num_vehicles: int = 1000
    duration_sec: int = 3600
    fringe_factor: int = 10


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

async def download_osm_data(bbox: BoundingBox, output_path: str, websocket:WebSocket):
    """Descarga directa de Overpass API para evitar errores de osmGet.py"""
    print("Descargando datos de OSM")
    types_filter = "|".join(bbox.road_types)
    print("Filtros de tipos: ", types_filter)
    # Overpass usa el orden: south, west, north, east
    overpass_url = "https://overpass-api.de/api/interpreter"
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
            if response.status_code==403:
                await websocket.send_json({"mensaje": "Error en la descarga de carreteras 🚨:" + str(response.status_code + " " + response.text) })
            
     
    except Exception as e:
        await websocket.send_json({"mensaje": "Error en la descarga de carreteras 🚨:" + str(e) })
        await websocket.close()
        raise HTTPException(status_code=500, detail=str(e))

def getVelocityStyle(velocity):

    if (velocity>119):
        return "blue"
    elif (velocity>101 and velocity<=119):
        return "green"
    elif (velocity>80 and velocity<=101):
        return "yellow"
    elif (velocity>60 and velocity<=80):
        return "purple"
    elif (velocity>40 and velocity<=60):
        return "orange"
    elif (velocity>20 and velocity<=40):
        return "white"
    else:
        return "gray"

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

def convertirEmissionsXmlToParquet(ruta_emissions,ruta_parquet,rootLabel='interval',nestLabel='edge'):
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

    except Exception as e:
        print(f"Error al ejecutar SUMO: {e}")
        raise HTTPException(status_code=500, detail=f"Error al ejecutar SUMO: {e}")

def convertirTrafficXmlToParquet(ruta_emissions,ruta_parquet,rootLabel='interval',nestLabel='edge'):
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
    
# ******************FIN FUNCIONES DE PARSEO DE LOS RESULTADOS DE EMISIONES DE SUMO**********************#
# RUTA PARA EJECUTAR LA SIMULACION DE SUMO Y OBTENER LOS RESULTADOS DE EMISIONES Y TRAFICO EN CALLES Y CARRILES
@app.websocket("/ws/simulationEmissions")
async def simulationEmissions(websocket: WebSocket):
    await websocket.accept()
    sumo_home_windows = r"C:\Proyectos\01_SUMO\sumo-1.26.0"
    sumo_home_linux = "/usr/share/sumo"
    ruta_output_windows = r"C:\Proyectos\twin-sumo-output\output"
    ruta_output_linux = r"/tmp/output"
    ruta_windows = r"C:\Proyectos\twin-sumo-output\red_carreteras"
    ruta_linux = r"/tmp/"

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
            route_file = os.path.join(tmpdir, "mapa.rou.xml")
            print("Archivo ROUT creado correctamente", route_file)

            if operativeSytemIsLinux==1:
                net_file = os.path.join(ruta_linux, "pamplona.net.xml")
                route_file= os.path.join(ruta_linux, "mapa.rou.xml")
            else:
                net_file = os.path.join(ruta_windows, "pamplona.net.xml")
                route_file= os.path.join(ruta_windows, "mapa.rou.xml")

            random_trips = os.path.join(sumo_home, "tools", "randomTrips.py")
            if operativeSytemIsLinux==1:
                subprocess.run([
                    "python3", random_trips,
                    "-n", net_file,
                    "-r", route_file,
                    "-e", duration_sec,  # Simular duration_sec segundos de tráfico
                    "--period", trip_period, # Aparece un coche cada trip_period segundos
                    "--fringe-factor", fringe_factor
                ], check=True)  
            else:
                subprocess.run([
                    "python", random_trips,
                    "-n", net_file,
                    "-r", route_file,
                    "-e", duration_sec,  # Simular duration_sec segundos de tráfico
                    "--period", trip_period, # Aparece un coche cada trip_period segundos
                    "--fringe-factor", fringe_factor
                ], check=True)  

            # crea el archivo de configuración SUMO

            if operativeSytemIsLinux==1:
                config_file = os.path.join(ruta_linux, "simulation.sumocfg")
            else:
                config_file = os.path.join(ruta_windows, "simulation.sumocfg")
                route_file = os.path.join(ruta_windows, "mapa.rou.xml")
                
            print("Archivo de configuración SUMO creado correctamente", config_file)
            with open(config_file, 'w') as f:
                f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
                <configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.xsd">
                    <input>
                        <net-file value="pamplona.net.xml"/>
                        <route-files value="{route_file}"/>
                        <additional-files value="additional.add.xml"/>
                    </input>
                    <routing>
                        <device.rerouting.probability value="1.0"/>
                        <device.rerouting.period value="0"/>
                    </routing>
                </configuration>""")

            print("Archivos de configuración SUMO generados correctamente")

            if operativeSytemIsLinux==1:
                sumo = "sumo"
            else:
                sumo = os.path.join(sumo_home, "bin", "sumo")  # sin GUI
                
            print("Lanzando simulación con SUMO")
            await websocket.send_json({"mensaje": "Lanzando simulación con SUMO de Pamplona.🚀"})
            try:
                subprocess.run([
                        sumo,
                        "-c", config_file,
                        "-b", "0",
                        "-e", duration_sec,  # Simular duration_sec segundos
                        # "-n", net_file,
                        # "-r", route_file,
                        "-v", "true"
                    ], check=True,capture_output=True, text=True)

                await websocket.send_json({"mensaje": "Simulación con SUMO finalizada correctamente.✅"})   
                # SI EL FICHERO EXISTE, LO BORRA PARA EVITAR PROBLEMAS DE PARSEO
                ruta_edgeEmissions_p = os.path.join(ruta_output, "edgeEmissions.parquet")
                if os.path.exists(ruta_edgeEmissions_p):
                    print("Delete existing parquet file")
                    os.remove(ruta_edgeEmissions_p)

                convertirEmissionsXmlToParquet(os.path.join(ruta_output, "edgeEmissions.xml"),os.path.join(ruta_output, "edgeEmissions.parquet"))
                await websocket.send_json({"mensaje": "Creado fichero parquet de Emisiones de Sancho el Fuerte.🗄️"})   

                # SI EL FICHERO EXISTE, LO BORRA PARA EVITAR PROBLEMAS DE PARSEO
                ruta_traffic_p = os.path.join(ruta_output, "edgeTraffic.parquet")
                if os.path.exists(ruta_traffic_p):
                    print("Delete existing parquet file")
                    os.remove(ruta_traffic_p)

                convertirTrafficXmlToParquet(os.path.join(ruta_output, "edgeTraffic.xml"),os.path.join(ruta_output, "edgeTraffic.parquet"))
                print(" Fichero de edgeTraffic.parquet creado")
                await websocket.send_json({"mensaje": "Creado fichero parquet de Tráfico de Sancho el Fuerte.🗄️"})


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

def obtener_coords_calle(net,edge_id):
    # Esta función debe devolver las coordenadas de la calle (edge) dada su ID
    # Puedes usar sumolib para leer la red y obtener las coordenadas de cada edge
    # Ejemplo:
    edge = net.getEdge(edge_id)
    shape = edge.getShape()
    coords = [net.convertXY2LonLat(x, y) for x, y in shape]
    # Cesium espera un array plano de [lon, lat, alt, lon, lat, alt, ...]
    coords_planas = []
    for lon, lat in coords:
        coords_planas.extend([lon, lat, 0])  # Altura 0 para clamping to ground
    return coords_planas

@app.get("/getCzmlEmissions")
def generar_czml_emisiones():
    # 1. Leer datos del Parquet
    operativeSytemIsLinux= 1 if platform.system()=="Linux" else 0
    if operativeSytemIsLinux==1:
       ruta_output= r"/tmp/output"
       net_file = "/tmp/zona-sancho-el-fuerte.net.xml"
    else:
       ruta_output= r"C:\Proyectos\twin-sumo-output\output"
       net_file = r"C:\Proyectos\twin-sumo-output\red_carreteras\zona-sancho-el-fuerte.net.xml"

    net = sumolib.net.readNet(net_file)
    ruta_edgeEmissions_p = os.path.join(ruta_output, "edgeEmissions.parquet")
    if not os.path.exists(ruta_edgeEmissions_p):
        raise HTTPException(status_code=404, detail="File edgeEmissions.parquet not found")


    df = pd.read_parquet(ruta_edgeEmissions_p)
    # Aseguramos que el tiempo esté en formato datetime
    df['interval_begin'] = pd.to_numeric(df['interval_begin'])
    
    # 2. Configuración inicial del CZML
    inicio_sim = "2026-03-16T08:00:00Z" # Ajusta a tu fecha real
    final_sim = "2026-03-16T10:00:00Z"  # Ajusta a tu fecha real
    czml = [{
        "id": "document",
        "version": "1.0",
        "clock": {
            "interval": f"{inicio_sim}/{final_sim}",
            "currentTime": inicio_sim,
            "multiplier": 1,
                "range": "LOOP_STOP",
                "step": "SYSTEM_CLOCK_MULTIPLIER"
        }
    }]

    # 3. Procesar cada calle (edge)
    for edge_id, group in df.groupby('id'):
        rgba_list = []
        
        # Ordenar por tiempo para que la evolución sea correcta
        group = group.sort_values('interval_begin')
        
        for _, row in group.iterrows():
            # Definir el tiempo para este punto (ISO8601)
            # Sumamos los segundos de la simulación a la hora de inicio
            time_iso = f"2026-03-16T08:00:{int(row['interval_begin']):02d}Z"
            
            # Lógica de color según CO2 (Verde a Rojo/Púrpura)
            co2 = row['CO2_abs'] if row['CO2_abs'] is not None else 0 # Si no hay dato, asumimos 0
            r, g, b = 0, 255, 0 # Default Verde
            
            if co2 > 1000 and co2 <= 5000:
                r, g, b = 255, 165, 0 # Naranja
                size = 3
            elif co2 > 5000:
                r, g, b = 255, 0, 0   # Rojo
                size = 5
            
            # Añadir al array CZML: [tiempo, R, G, B, A]
            rgba_list.extend([time_iso, r, g, b, 200])

        # Crear el objeto de la calle
        # Nota: Necesitas las coordenadas de la calle (positions) de tu red
        calle_packet = {
            "id": f"{edge_id}",
            "name": f"Emisiones en {edge_id}",
            "polyline": {
                "positions": {
                    "cartographicDegrees": obtener_coords_calle(net, edge_id) # Función que saque las coordenadas
                },
                "material": {
                    "solidColor": {
                        "color": {
                            "rgba": rgba_list # AQUÍ está la magia del cambio de color
                        }
                    }
                },
                "width": size,
                "clampToGround": True
            }
        }
        czml.append(calle_packet)
    
    return czml

@app.get("/get-emission-data")
def get_emission_data():
    ruta_output_windows = r"C:\Proyectos\twin-sumo-output\output"
    ruta_output_linux = r"/tmp/"
    ruta_windows = r"C:\Proyectos\twin-sumo-output\red_carreteras"
    ruta_linux = r"/tmp/"


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

@app.get("/get-traffic-data")
def get_traffic_data():

    ruta_output_windows = r"C:\Proyectos\twin-sumo-output\output"
    ruta_output_linux = r"/tmp/"
    ruta_windows = r"C:\Proyectos\twin-sumo-output\red_carreteras"
    ruta_linux = r"/tmp/"


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
                bbox = BoundingBox(**json.loads(bbox_str))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid bbox format")
    
   
    #DETERMINA EL SISTEMA OPERATIVO SOBRE EL QUE SE EJECUTA LA APLICACION
    operativeSytemIsLinux= 0 if platform.system()=="Linux" else 1
    if operativeSytemIsLinux==0:
       sumo_home = "/usr/share/sumo"
    else:
        sumo_home = r"C:\Proyectos\01_SUMO\sumo-1.26.0"
    
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
        
        config_file =os.path.join(tmpdir,"simulation.sumocfg")
        print("Config file:", config_file)

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
                net_file = "/tmp/zona-sancho-el-fuerte.net.xml"
            else:
                net_file = "C:\\Proyectos\\twin-sumo-output\\red_carreteras\\zona-sancho-el-fuerte.net.xml"
            
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
                config_file = "/tmp/simulation.sumocfg"
            else:
                config_file = os.path.join(tmpdir, "simulation.sumocfg")
            

            print("Archivo de configuración SUMO creado correctamente", config_file)
            await websocket.send_json({"mensaje":"Archivo de configuración SUMO creado correctamente"})
            
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
                config_file = os.path.join(tmpdir, "simulation.sumocfg")
            else:
                config_file = os.path.join("/tmp/sancho-el-fuerte.sumocfg")
        # 4. Iniciar simulación con TraCI
        try:
            traciBinary = os.path.join(sumo_home, "bin", "sumo")  # sin GUI
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
        payload = BoundingBox(**json.loads(payload))
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
            await download_osm_data(payload, osm_file, websocket)
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
            await websocket.send_json({"mensaje": "Descarga de carreteras finalizada 👍"})
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