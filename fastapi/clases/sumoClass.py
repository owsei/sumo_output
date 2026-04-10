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

class connectionParams:
    def __init__(self, host, port, dbname, user, password):
        # Asignar argumentos a atributos
        self.host = host
        self.port = port
        self.dbname = dbname
        self.user = user
        self.password = password    
    
class simulationParams:
    def __init__(self, num_vehicles, duration_sec, fringe_factor, aggregation_period_sec, trip_period):
        # Asignar argumentos a atributos
        self.num_vehicles = num_vehicles
        self.duration_sec = duration_sec
        self.fringe_factor = fringe_factor
        self.aggregation_period_sec = aggregation_period_sec
        self.trip_period = trip_period

