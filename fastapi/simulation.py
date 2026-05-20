# Archivo: simulation.py

# Descripción: 
# Este archivo contiene la lógica para ejecutar simulaciones de tráfico con SUMO, generar los archivos de salida de emisiones y tráfico, 
# convertirlos a formato Parquet y cargar los resultados en una base de datos Postgres. La función principal es un endpoint WebSocket que permite iniciar la 
# simulación y recibir actualizaciones en tiempo real sobre el progreso.

# VERAS QUE AL PRINCIPIO HAY UNA COSA ASI
# operativeSytemIsLinux= 1 if platform.system()=="Linux" else 0
# if operativeSytemIsLinux==1:
#     net_file = os.path.join(ruta_linux, "pamplona.net.xml")
#     print("Archivo NET creado correctamente", net_file)
# else:
#     net_file = os.path.join(ruta_windows, "pamplona.net.xml")
#     print("Archivo NET creado correctamente", net_file)


# VERAS QUE HAY UNAS FUNCIONES QUE EJECUTAN SUBPROCESOS DE PYTHON PARA GENERAR LOS ARCHIVOS DE RUTAS ALEATORIAS Y PARA EJECUTAR LA SIMULACION DE SUMO, 
# ESTO EJECUTA ESOS COMANDOS DE PYTHON COMO SI LOS EJECUTARAS EN LA TERMINAL, PERO DESDE PYTHON, ASI PUEDES CONTROLAR TODO EL PROCESO DE LA SIMULACION DESDE ESTE ARCHIVO
# EJEMPLO:   subprocess.run([...], check=True)






# uuid_simulation = str(uuid.uuid4())[:8]  # Genera un UUID corto para nombrar los archivos de la simulación, así evitamos conflictos entre simulaciones concurrentes



# IMPORTACIÓN DE LIBRERÍAS
import time
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

# AQUI PON LA RUTA DE DONDE TENGAS SUMO INSTALADO EN TU ORDENADOR, SI USAS LINUX PROBABLEMENTE SEA "/usr/share/sumo"(CONTENEDOR DOCKER) Y 
# SI USAS WINDOWS ALGO ASI ejemplo: "C:\RUTA\01_SUMO\sumo-1.26.0"
sumo_home_windows = r"C:\Proyectos\01_SUMO\sumo-1.26.0"
sumo_home_linux = "/usr/share/sumo"

# RUTA DE LOS FICHEROS DE SALIDA DE LAS SIMULACIONES DE SUMO, SI USAS LINUX PUEDE SER ALGO ASI "/tmp/output/" 
# Y SI USAS WINDOWS ALGO ASI "C:\Proyectos\sumo_output_mia\output"
ruta_output_windows = r"C:\Proyectos\sumo_output_mia\output"
ruta_output_linux = r"/tmp/output/"

# RUTA DE LOS ARCHIVOS DE ENTRADA DE LAS SIMULACIONES DE SUMO, SI USAS LINUX PUEDE SER ALGO ASI "/tmp/" (CONTENEDOR DOCKER)
# Y SI USAS WINDOWS ALGO ASI "C:\Proyectos\sumo_output_mia\red_carreteras"
ruta_windows = r"C:\Proyectos\sumo_output_mia\red_carreteras"
ruta_linux = r"/tmp/"


# CAMBIA AQUI LA CONEXION A LA BASE DE DATOS DONDE QUIERAS GUARDAR LOS RESULTADOS DE LAS SIMULACIONES DE SUMO
sumoBD=sumoClass.connectionParams("duckdb", "5432", "sumo", "admin", "admin")


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# CON ESTO CONVIERTO EL FICHERO XML DE RESULTADOS DE EMISIONES Y TRAFICO DE SUMO A FORMATO PARQUET, 
# QUE ES MAS FACIL DE MANEJAR DESDE PYTHON Y DESDE EL FRONT PARA MOSTRAR LOS RESULTADOS
async def convertirXmlToParquet(websocket,ruta_emissions,ruta_parquet,rootLabel='interval',nestLabel='edge'):
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

# ESTA FUNCION SE ENCARGA DE CARGAR LOS RESULTADOS DE LAS SIMULACIONES DE SUMO EN LA BASE DE DATOS POSTGRES,
# PARA LUEGO PODER CONSULTAR ESOS RESULTADOS DESDE EL FRONT Y MOSTRARLOS EN EL MAPA O EN LOS GRÁFICOS DE LA APLICACION

# HAY DOS CLASES QUE HAY QUE RELLENAR PARA PODER PASAR LOS PARAMETROS DE LA CONEXION A LA BASE DE DATOS Y LOS PARAMETROS DE LA SIMULACION, ESTAS CLASES ESTAN DEFINIDAS EN EL ARCHIVO sumoClass.py
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

    try:
        cur.execute(f"""INSERT INTO simulations (date, num_vehicles, duration_sec, fringe_factor, aggregation_period, trip_period,file_emissions,file_traffic) VALUES (NOW(), {simulation_params.num_vehicles}, {simulation_params.duration_sec}, {simulation_params.fringe_factor}, {simulation_params.aggregation_period_sec}, {simulation_params.trip_period}, '{ruta_file_emissions}', '{ruta_file_traffic}') RETURNING id_simulation;""")
        id_simulation = cur.fetchone()[0]
        conn.commit()

        cur.close() 
        conn.close()
        print("\n¡Carga completada!")
        print("\nDatos de la simulación cargados en Postgres.")

    except Exception as e:
        cur.close()
        conn.close()
        print(f"Error al cargar datos de la simulación: {e} ")

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
            duarouter = os.path.join(sumo_home, "bin", "duarouter")
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

            await convertirXmlToParquet(websocket,os.path.join(ruta_output, f"edgeEmissions_{uuid_simulation}.xml"),os.path.join(ruta_output, f"edgeEmissions_{uuid_simulation}.parquet"))
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

            await convertirXmlToParquet(websocket, os.path.join(ruta_output, f"edgeTraffic_{uuid_simulation}.xml"),os.path.join(ruta_output, f"edgeTraffic_{uuid_simulation}.parquet"))
            
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