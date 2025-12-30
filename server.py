import sqlite3
import math
import time
from typing import Optional, List
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from collections import defaultdict
import uvicorn
import os
from types import SimpleNamespace # Conversion de dictionnaires en objets simples

# ===========================================
# Configuration et BDD
# ===========================================

app = FastAPI(title="Tracking Fusion Server")

DB_NAME = "tracking.db"

# Pour le test
last_estimated_position = None

# ===========================================
# Algorithme de positionnement (k-NN)
# ===========================================

def calculate_position_knn(current_scan, k=3):
    """
    Compare le scan actuel avec l'historique de la base de donnée et retourne la position.
    
    Args:
        current_scan (list): Liste des réseaux détectés (avec rssi, bssid)
        k (int): Nombre de voisins à utiliser pour le calcul
        
    Returns:
        dict: {'lat': float, 'lon': float} ou None si pas assez de données.
    """
    
    # Récupérer les empruntes connues (Bdd)
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, bssid, rssi_avg, lat, lon FROM measurements WHERE lat IS NOT 0 AND lon IS NOT 0")
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Erreur lecture DB pour positionnement: {e}")
        return None

    # Reconstruit les empruntes 
    # Structure : fingerprints[timestamp] = { 'coords': (lat, lon), 'signals': {bssid: rssi, ...} }
    fingerprints = defaultdict(lambda: {'coords': (0, 0), 'signals': {}})
    
    for row in rows:
        ts = row['timestamp']
        fingerprints[ts]['coords'] = (row['lat'], row['lon'])
        fingerprints[ts]['signals'][row['bssid']] = row['rssi_avg']

    # Calculer la distance de Mahalanobis avec chaque emprunte de base
    distances = []
    
    # Transformer le scan actuel en dictionnaire pour accès rapide
    current_signals = {item.bssid: float(item.rssi) for item in current_scan}
    
    for ts, fp_data in fingerprints.items():
        stored_signals = fp_data['signals']
        
        # On ne compare que si on a au moins un BSSID en commun (Shortlisting) 
        common_keys = set(current_signals.keys()) & set(stored_signals.keys())
        if not common_keys:
            continue

        # Calcul de la distance Euclidienne (Signal Space)
        # Formule : sqrt( sum( (RSSI_mesuré - RSSI_stocké)^2 ) )
        sum_sq_diff = 0
        
        # On compare sur l'union des BSSID (ceux vus maintenant et ceux stockés)
        all_keys = set(current_signals.keys()) | set(stored_signals.keys())
        
        for bssid in all_keys:
            v1 = current_signals.get(bssid, -100) # -100 dBm si non détecté (pénalité)
            v2 = stored_signals.get(bssid, -100)  # -100 dBm si absent de la base
            sum_sq_diff += (v1 - v2) ** 2
            
        euclidean_dist = math.sqrt(sum_sq_diff)
        
        distances.append({
            'dist': euclidean_dist,
            'lat': fp_data['coords'][0],
            'lon': fp_data['coords'][1]
        })

    # 4. Trier par distance (le plus petit est le meilleur) [cite: 109]
    distances.sort(key=lambda x: x['dist'])
    
    # 5. Sélectionner les k plus proches voisins (kNN) [cite: 115]
    nearest_neighbors = distances[:k]
    
    if not nearest_neighbors:
        return None

    # 6. Calculer la moyenne des coordonnées (Barycentre) 
    avg_lat = sum(n['lat'] for n in nearest_neighbors) / len(nearest_neighbors)
    avg_lon = sum(n['lon'] for n in nearest_neighbors) / len(nearest_neighbors)
    
    print(f"📍 Position estimée (WiFi) basée sur {len(nearest_neighbors)} voisins: {avg_lat}, {avg_lon}")
    
    return {'lat': avg_lat, 'lon': avg_lon}


def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL;') 
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            bssid TEXT,
            rssi_avg REAL,
            rssi_med REAL,
            rssi_std REAL,
            channel INTEGER,
            lat REAL,
            lon REAL,
            method TEXT,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

# ===========================================
# Structures de données
# ===========================================

# Un réseau
class NetworkItem(BaseModel):
    bssid    : str
    ssid     : Optional[str] = None
    rssi_avg : float
    rssi_med : float
    rssi_std : float
    channel  : int

# Un payload transmis par l'ESP32
class EspBatchPayload(BaseModel):
    timestamp: int     # Timestamp en secondes envoyé par l'ESP
    networks: List[NetworkItem] # Listes de réseaux scannés en un point géographique par l'ESP32

# Une donnée GPS
class GpsData(BaseModel):
    lat: float
    lon: float
    timestamp: Optional[int] = None

# Une donnée de podomètre
class PodoMeter(BaseModel):
    s: int

# ===========================================
# LOGIQUE de création de la BDD
# fusion des données reçues depuis l'ESP32 et la carte du smartphone
# logique de bufferisant pour le stockage de données avant d'écrire dans la BDD
# ===========================================

GPS_TIME_WINDOW_MAX = 30000  # 30 sec
MIN_DISTANCE_FOR_MOVEMENT = 5.0  # mètres

rssi_buffer = []
gps_buffer = []

def lerp(start, end, alpha):
    return start + (end - start) * alpha

def get_distance_haversine(lat1, lon1, lat2, lon2):
    """
    Distance entre deux points gps (long, lat)
    Args :
        latitude et longitude des deux points gps,
    Returns :
        distance (int) : distance en mètres.
    """
    R = 6371000.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def process_buffers():
    """
    Gère l'écriture dans la BDD : si suffisemment de points ont été récoltés et si on est encore dans une fenêtre
    de temps (secondes) et de position acceptable (mètres) alors on traite les données des buffers
        
    Récupére les données depuis rssi_buffer (esp32) et gps_buffer (smartphone)
    """
    global rssi_buffer, gps_buffer

    # Si trop peu de positions ont été enregistrées on sort
    if len(gps_buffer) < 4:
        return

    # Tri des buffers par timestamp
    gps_buffer.sort(key=lambda x: x['timestamp'])
    rssi_buffer.sort(key=lambda x: x['timestamp'])

    # Debug
    for k in gps_buffer :
        print(f"GPS : {k}")

    unprocessed_rssi = []
    to_insert = []

    # On fait les associations RSSI - LOCALISATION en se basant sur le timestamp (sensor fusion)
    for rssi_data in rssi_buffer:
        t_target = rssi_data['timestamp']

        # Trouver le prochain GPS
        next_gps_index = -1
        for i, g in enumerate(gps_buffer):
            if g['timestamp'] >= t_target:
                next_gps_index = i
                break
        
        # Si aucun point gps n'a été pris avant ou après la donnée envoyée par l'esp32 on sort et on stock le rssi pour plus tard
        if next_gps_index == -1:
            unprocessed_rssi.append(rssi_data)
            continue
        if next_gps_index == 0:
            continue

        # Vérifie que les points GPS sont dans une fenêtre de temps raisonnable
        next_gps = gps_buffer[next_gps_index]
        prev_gps = gps_buffer[next_gps_index - 1]

        time_diff = next_gps['timestamp'] - prev_gps['timestamp']
        
        if time_diff > GPS_TIME_WINDOW_MAX:
            continue

        dist_meters = get_distance_haversine(prev_gps['lat'], prev_gps['lon'], next_gps['lat'], next_gps['lon'])
        print(f"Distance : {dist_meters}")
        
        final_lat = 0.0
        final_lon = 0.0
        method = ""

        # Si on a pas assez bougé alors ne garde que le point précédent
        if dist_meters < MIN_DISTANCE_FOR_MOVEMENT:
            final_lat = prev_gps['lat']
            final_lon = prev_gps['lon']
            method = 'static'
        # Si le mouvement est normal alors fait une interpolation et la latitude / longitude
        else:
            alpha = (t_target - prev_gps['timestamp']) / time_diff
            final_lat = lerp(prev_gps['lat'], next_gps['lat'], alpha)
            final_lon = lerp(prev_gps['lon'], next_gps['lon'], alpha)
            method = 'interpolated'
        # Ajout dans la liste des données à ajouter dans la BDD
        to_insert.append((
            rssi_data['timestamp'], rssi_data['bssid'], rssi_data['rssi_average'],
            rssi_data['rssi_median'], rssi_data['rssi_std'], rssi_data['channel'],
            final_lat, final_lon, method
        ))

    # Insertion dans la BDD une fois les données récupérées
    if to_insert:
        try:
            with db_conn:
                db_conn.executemany('''
                    INSERT INTO measurements 
                    (timestamp, bssid, rssi_avg, rssi_med, rssi_std, channel, lat, lon, method) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', to_insert)
            print(f"{len(to_insert)} mesures traitées et enregistrées.")
        except Exception as e:
            print(f"Erreur DB: {e}")

    # On vide le buffer et on écrit les données rssi non utilisées
    rssi_buffer = unprocessed_rssi
    
    # On nettoie les données avec un timestamp trop vieux (plus anciens que le plus vieux rssi)
    if rssi_buffer:
        oldest_needed = rssi_buffer[0]['timestamp']
        boundary_index = 0
        for i, g in enumerate(gps_buffer):
            if g['timestamp'] > oldest_needed:
                boundary_index = i
                break
        if boundary_index > 1:
            del gps_buffer[:boundary_index - 1]
    # On ne garde que le dernier point gps si on a vidé le buffer rssi
    elif len(gps_buffer) > 1:
        gps_buffer = gps_buffer[-1:]

# ===========================================
# ROUTES API
# /map.js            : route de test pour retourner un fichier js
# /esp32             : route pour la réception des données RSSI
# /gps               : route pour la réception des données GPS
# /points            : route pour récupérer les données de la BDD depuis la carte
# /esp32_test        : route pour estimer une position à partir de la BDD -> dans last_estimated_position
# /map_test          : route pour la carte de test (pour suivre ma position)
# /api/last_position : route pour que la map de test obtienne ma position
# /            : route de base : carte
# ===========================================

@app.get("/map.js")
async def read_map_js():
    file_path = os.path.join(os.path.dirname(__file__), "map.js")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "File not found"}, 404


@app.post("/esp32")
async def receive_esp32(payload: EspBatchPayload):
    
    # L'ESP envoie le timestamp en Secondes, le serveur travaille en ms
    batch_timestamp_ms = payload.timestamp * 1000
    
    # Debug
    print(f"Batch reçu de l'ESP32: {len(payload.networks)} réseaux (TS: {batch_timestamp_ms})")

    # Ajout des données au buffer rssi
    count = 0
    for net in payload.networks:
        rssi_buffer.append({
            'timestamp'   : int(batch_timestamp_ms),
            'bssid'       : net.bssid,
            'rssi_average': float(net.rssi_avg),
            'rssi_median' : float(net.rssi_med), 
            'rssi_std'    : float(net.rssi_std), 
            'channel'     : net.channel
        })
        count += 1
        print(f"   -> {net.ssid} ({net.bssid}): {net.rssi_avg} dBm")
    
    # Check si suffisement de données ont été enregistrées
    process_buffers()

    return {"status": "OK", "processed": count}


@app.post("/gps")
async def receive_gps(gps_data: GpsData):

    # Si pas de timestamps envoyé on prend celui du server
    current_time = gps_data.timestamp
    if current_time is None:
        current_time = int(time.time() * 1000)

    # Ajout des données au buffer gps
    gps_buffer.append({
        'timestamp': int(current_time),
        'lat': gps_data.lat,
        'lon': gps_data.lon
    })
    print(f"GPS reçu: {gps_data.lat}, {gps_data.lon}")
    
    # Check si suffisement de données ont été enregistrées
    process_buffers()
    
    return {"status": "OK"}

@app.get("/points")
async def get_points():

    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT lat, lon, timestamp, rssi_avg, method FROM measurements")
        rows = cursor.fetchall()
        conn.close()
        
        # Conversion en liste de dictionnaires
        return [dict(row) for row in rows]
    except Exception as e:

        print(f"Erreur lecture DB: {e}")
        return []

@app.post("/esp32_test")
async def receive_esp32_test(payload: EspBatchPayload):
    global last_estimated_position
    
    # Formatte les données reçues 
    formatted_scan = []
    
    print(f"Reception d'un scan de test avec {len(payload.networks)} réseaux")

    for net in payload.networks:
        item = SimpleNamespace(bssid=net.bssid, rssi=net.rssi_avg)
        formatted_scan.append(item)
    
    # Calcul de la position
    estimated_pos = calculate_position_knn(formatted_scan, k=3)
    
    # Mise à jour de la variable globale de test
    if estimated_pos:
        last_estimated_position = {
            "lat": estimated_pos['lat'],
            "lon": estimated_pos['lon'],
            "timestamp": payload.timestamp,
            "neighbors_count": len(payload.networks)
        }
    
    return {"status": "OK", "position": estimated_pos}

@app.get("/map_test")
async def read_map_test():
    file_path = os.path.join(os.path.dirname(__file__), "map_test.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "Créez le fichier map_test.html d'abord !"}, 404

@app.get("/api/last_position")
async def get_last_position():
    return last_estimated_position if last_estimated_position else {}


@app.get("/")
async def read_index():

    file_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    
    return {"message": "Serveur Tracking Fusion Actif"}

@app.post("/post_podo")
async def receive_podo(payload: PodoMeter):
    print(f"Steps : {payload}")

# ===========================================
# Start up
# ===========================================

if __name__ == "__main__":
    db_conn = init_db()
    print("🚀 Serveur FastAPI démarré sur http://localhost:5000")
    uvicorn.run(app, host="0.0.0.0", port=5000)