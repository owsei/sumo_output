import uuid
i=0
while i<5:
    print("Iteración:", i)
    uuid_simulation = str(uuid.uuid4())[:10]
    print("UUID de la simulación:", uuid_simulation)
    i += 1

