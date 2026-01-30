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
from types import SimpleNamespace 

# ===========================================
# Configuration and DB
# ===========================================

# Globals
GPS_TIME_WINDOW_MAX = 30000 
MIN_DISTANCE_FOR_MOVEMENT = 5.0 

# For testing (real time position estimation)
last_estimated_position = None

# Training buffers
rssi_buffer = []
gps_buffer = []

app = FastAPI(title="Tracking Fusion Server")

# DB
DB_NAME = "tracking.db"

def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    # Allow user to read and write at same time
    conn.execute('PRAGMA journal_mode=WAL;') 
    
    # Training table
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

    # Testing table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS estimations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            lat REAL,
            lon REAL,
            nb_neighbors INTEGER,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Write in .db
    conn.commit()
    return conn

# ===========================================
# Data structures
# ===========================================
# BaseModel comes from Pydantic to assert data types (also do Json parsing)

class NetworkItem(BaseModel):
    bssid    : str
    ssid     : Optional[str] = None
    rssi_avg : float
    rssi_med : float
    rssi_std : float
    channel  : int

class EspBatchPayload(BaseModel):
    timestamp: int     
    networks: List[NetworkItem] 

class GpsData(BaseModel):
    lat: float
    lon: float
    timestamp: Optional[int] = None

# ===========================================
# Algorithm
# ===========================================

def calculate_position_knn(current_scan, k=3):
    """
    Compare le scan actuel avec les données en utilisant la distance de Mahalanobis : sqrt( sum( (diff^2) / variance ) )
    """
    
    # Récupérer les empreintes sauvegardées avec l'écart-type (rssi_std)
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, bssid, rssi_avg, rssi_std, lat, lon FROM measurements WHERE lat IS NOT 0 AND lon IS NOT 0")
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Erreur lecture DB pour positionnement: {e}")
        return None

    # Reconstruire les empreintes
    # Structure : fingerprints[timestamp] = { 'coords': (lat, lon), 'signals': {bssid: {'avg': -60, 'std': 2.5}, ...} }
    fingerprints = defaultdict(lambda: {'coords': (0, 0), 'signals': {}})
    
    for row in rows:
        ts = row['timestamp']
        fingerprints[ts]['coords'] = (row['lat'], row['lon'])
        # On stocke l'objet complet avec avg et std
        fingerprints[ts]['signals'][row['bssid']] = {
            'avg': row['rssi_avg'],
            'std': row['rssi_std']
        }

    distances = []
    
    # Transformer le scan actuel en dictionnaire pour accès rapide
    # On suppose que item.rssi contient la valeur moyennée envoyée par l'ESP32
    current_signals = {item.bssid: float(item.rssi) for item in current_scan}
    
    # Constantes pour pénalités
    PENALTY_RSSI = -100.0   # Valeur si signal absent
    DEFAULT_STD = 5.0       # Écart-type par défaut si absent ou inconnu
    MIN_STD = 0.5           # Écart-type minimum pour éviter division par zéro

    for ts, fp_data in fingerprints.items():
        stored_signals = fp_data['signals']
        
        # On ignore si aucun BSSID commun
        common_keys = set(current_signals.keys()) & set(stored_signals.keys())
        if not common_keys:
            continue

        sum_weighted_diff = 0
        
        # On compare sur l'union des réseaux
        all_keys = set(current_signals.keys()) | set(stored_signals.keys())
        
        for bssid in all_keys:
            # Récupération des valeurs
            rssi_curr = current_signals.get(bssid, PENALTY_RSSI)
            
            if bssid in stored_signals:
                rssi_stored = stored_signals[bssid]['avg']
                sigma = stored_signals[bssid]['std']
            else:
                rssi_stored = PENALTY_RSSI
                sigma = DEFAULT_STD 

            # Si le std est 0 (cas rare) ou très faible, on force un minimum
            if sigma < MIN_STD: 
                sigma = MIN_STD
                
            # Calcul de la distance de Mahalanobis (au carré) pour ce point
            diff = rssi_curr - rssi_stored
            variance = sigma ** 2
            
            term = (diff ** 2) / variance
            sum_weighted_diff += term
            
        # Distance finale (racine carrée de la somme)
        mahalanobis_dist = math.sqrt(sum_weighted_diff)
        
        distances.append({
            'dist': mahalanobis_dist,
            'lat': fp_data['coords'][0],
            'lon': fp_data['coords'][1]
        })

    # Trier par distance
    distances.sort(key=lambda x: x['dist'])
    
    # Sélectionner les k voisins
    nearest_neighbors = distances[:k]
    if not nearest_neighbors:
        return None

    # Barycentre
    avg_lat = sum(n['lat'] for n in nearest_neighbors) / len(nearest_neighbors)
    avg_lon = sum(n['lon'] for n in nearest_neighbors) / len(nearest_neighbors)
    
    print(f"Position estimée (Mahalanobis) basée sur {len(nearest_neighbors)} voisins: {avg_lat:.5f}, {avg_lon:.5f}")
    
    return {'lat': avg_lat, 'lon': avg_lon}


def lerp(start, end, alpha):
    """
    Linear interpolation
    """
    return start + (end - start) * alpha

def get_distance_haversine(lat1, lon1, lat2, lon2):

    """
    Distance between two lat/long positions
    """
    R = 6371000.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# ===========================================
# Database filling
# ===========================================

def process_buffers():

    """"
    Combine rssi data coming from the ESP32 with localisation data coming from Smartphone.
    Buffers are filled with two routes : /esp32 /gps.
    It combines data using timestamps.
    Old data are automatically removed.
    """
    global rssi_buffer, gps_buffer

    if len(gps_buffer) < 4:
        return

    # Sort by timestamp
    gps_buffer.sort(key=lambda x: x['timestamp'])
    rssi_buffer.sort(key=lambda x: x['timestamp'])

    unprocessed_rssi = []
    to_insert = []

    # From a rssi measure look from the next and previous gps value according to the timestamp
    for rssi_data in rssi_buffer:
        t_target = rssi_data['timestamp']

        # Find next
        next_gps_index = -1
        for i, g in enumerate(gps_buffer):
            if g['timestamp'] >= t_target:
                next_gps_index = i
                break
        
        # No gps position after the rssi point : no possible interpolation
        # add to unprocessed list
        if next_gps_index == -1:
            unprocessed_rssi.append(rssi_data)
            continue
        if next_gps_index == 0:
            continue

        # Gps data for interpolation : get time difference
        next_gps = gps_buffer[next_gps_index]
        prev_gps = gps_buffer[next_gps_index - 1]
        time_diff = next_gps['timestamp'] - prev_gps['timestamp']
        if time_diff > GPS_TIME_WINDOW_MAX:
            continue

        # Estimate walking between prev and next gps points
        dist_meters = get_distance_haversine(prev_gps['lat'], prev_gps['lon'], next_gps['lat'], next_gps['lon'])
        
        final_lat = 0.0
        final_lon = 0.0
        method = ""

        # If small distance no interpolation (static) else interpolation
        if dist_meters < MIN_DISTANCE_FOR_MOVEMENT:
            final_lat = prev_gps['lat']
            final_lon = prev_gps['lon']
            method = 'static'
        else:
            # Pourcentage of progression between the two gps points
            alpha = (t_target - prev_gps['timestamp']) / time_diff
            final_lat = lerp(prev_gps['lat'], next_gps['lat'], alpha)
            final_lon = lerp(prev_gps['lon'], next_gps['lon'], alpha)
            method = 'interpolated'
            
        # Add the point for which the coordinates have been calculated
        to_insert.append((
            rssi_data['timestamp'], rssi_data['bssid'], rssi_data['rssi_average'],
            rssi_data['rssi_median'], rssi_data['rssi_std'], rssi_data['channel'],
            final_lat, final_lon, method
        ))

    # If rssi points have been linked to gps coordinates then insert them into SQL DB
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

    # Update Rssi buffer
    rssi_buffer = unprocessed_rssi
    
    # Take oldest rssi value and delete older gps values (no possible interpolation)
    if rssi_buffer:
        oldest_needed = rssi_buffer[0]['timestamp']
        boundary_index = 0
        for i, g in enumerate(gps_buffer):
            if g['timestamp'] > oldest_needed:
                boundary_index = i
                break
        if boundary_index > 1:
            del gps_buffer[:boundary_index - 1]
    # If rssi buffer is empty (all points have been processed) only keep last gps point
    elif len(gps_buffer) > 1:
        gps_buffer = gps_buffer[-1:]

# ===========================================
# ROUTES API
# ===========================================
# FastApi fonctionne sur une event loop (boucle infinie)
# async évite la création de Threads pour des tâches simples

# FILL route
# Base route : used for client to place gps points 
@app.get("/map.js")
async def read_map_js():
    file_path = os.path.join(os.path.dirname(__file__), "map.js")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "File not found"}, 404

# Used by web client to display DB points
@app.get("/points")
async def get_points():
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT lat, lon, timestamp, rssi_avg, method FROM measurements")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Erreur lecture DB: {e}")
        return []
    
# TEST routes 
# Route to get test map
@app.get("/map_test")
async def read_map_test():
    file_path = os.path.join(os.path.dirname(__file__), "map_test.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "Créez le fichier map_test.html d'abord !"}, 404

# Used by web client to display last position 
@app.get("/api/last_position")
async def get_last_position():
    # Retourne toujours la dernière position connue en RAM
    return last_estimated_position if last_estimated_position else {}
    
# ===========================================
# ROUTE DE REMPLISSAGE DE DB
# ===========================================
# Each received message fill a buffer and start process_buffer treatment

# Used by esp32 to send rssi measured values
@app.post("/esp32")
async def receive_esp32(payload: EspBatchPayload):
    batch_timestamp_ms = payload.timestamp * 1000
    print(f"Batch reçu de l'ESP32: {len(payload.networks)} réseaux (TS: {batch_timestamp_ms})")

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
    
    process_buffers()
    return {"status": "OK", "processed": count}

# Used by esp32 to send gps values
@app.post("/gps")
async def receive_gps(gps_data: GpsData):
    current_time = gps_data.timestamp
    if current_time is None:
        current_time = int(time.time() * 1000)

    gps_buffer.append({
        'timestamp': int(current_time),
        'lat': gps_data.lat,
        'lon': gps_data.lon
    })
    print(f"GPS reçu: {gps_data.lat}, {gps_data.lon}")
    process_buffers()
    return {"status": "OK"}

# ===========================================
# ROUTE DE TEST 
# ===========================================

# Measured rssi sent by esp32
@app.post("/esp32_test")
async def receive_esp32_test(payload: EspBatchPayload):
    global last_estimated_position
    
    formatted_scan = []
    print(f"Reception d'un scan de test avec {len(payload.networks)} réseaux")

    for net in payload.networks:
        # SimpleNamespace permet d'utiliser la notation pointée (item.rssi) au lieu des crochets. Objet nettoyé et renommé
        item = SimpleNamespace(bssid=net.bssid, rssi=net.rssi_avg)
        formatted_scan.append(item)
    
    # Calculation of the position
    estimated_pos = calculate_position_knn(formatted_scan, k=3)
    
    if estimated_pos:
        # Global variable update to display on the client map
        last_estimated_position = {
            "lat": estimated_pos['lat'],
            "lon": estimated_pos['lon'],
            "timestamp": payload.timestamp,
            "neighbors_count": len(payload.networks)
        }

        # History of client gps coordinates
        try:
            # Connexion and insertion into estimation table in SQL DB
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute('''
                    INSERT INTO estimations (timestamp, lat, lon, nb_neighbors)
                    VALUES (?, ?, ?, ?)
                ''', (
                    payload.timestamp, 
                    estimated_pos['lat'], 
                    estimated_pos['lon'], 
                    len(payload.networks)
                ))
            conn.commit()
            print(f"Position sauvegardée dans la DB: {estimated_pos}")
        except Exception as e:
            print(f"Erreur de sauvegarde dans l'historique des positions : {e}")
    
    return {"status": "OK", "position": estimated_pos}

@app.get("/")
async def read_index():
    file_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"message": "Serveur Tracking Fusion Actif"}

# ===========================================
# Start up
# ===========================================

if __name__ == "__main__":
    db_conn = init_db()
    print("Serveur FastAPI démarré sur http://localhost:5000")
    uvicorn.run(app, host="0.0.0.0", port=5000)