# Introduction 
Generación de una simulacion de SUMO y obtencion de los ficheros generados de salida para la su análisis y creacion en un mapa de carreteras con los valores de ocupacion, densidad y contaminación.

# Getting Started
1. Instalación de FastApi (https://fastapi.tiangolo.com/#requirements)
    - pip install "fastapi[standard]"
    - fastapi dev 
2. Instalación de dependencias necesarias python



CREATE ROLE web_anon nologin;
GRANT USAGE ON SCHEMA api TO web_anon;
GRANT USAGE ON SCHEMA public TO web_anon;
GRANT SELECT ON api.productos TO web_anon;
GRANT SELECT ON api.productos TO web_anon;