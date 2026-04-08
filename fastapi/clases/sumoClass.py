from pydantic import BaseModel
from typing import List, Optional

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

class connectionParams(BaseModel):
    def __init__(self, host="localhost", port=5432, dbname="sumo", user="admin", password="admin"):
        # Asignar argumentos a atributos
        self.host = host
        self.port = port
        self.dbname = dbname
        self.user = user
        self.password = password    
    
    

