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
# Configuration et BDD
# ===========================================

app = FastAPI(title="Tracking Fusion Server")

DB_NAME = "tracking.db"

# Pour le test (affichage temps réel)
last_estimated_position = None

# ===========================================
# Algorithme de positionnement (k-NN)
# ===========================================

def calculate_position_knn(current_scan, k=3):
    """
    Compare le scan actuel avec l'historique de la base de donnée et retourne la position.
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

    fingerprints = defaultdict(lambda: {'coords': (0, 0), 'signals': {}})
    
    for row in rows:
        ts = row['timestamp']
        fingerprints[ts]['coords'] = (row['lat'], row['lon'])
        fingerprints[ts]['signals'][row['bssid']] = row['rssi_avg']

    distances = []
    current_signals = {item.bssid: float(item.rssi) for item in current_scan}
    
    for ts, fp_data in fingerprints.items():
        stored_signals = fp_data['signals']
        
        common_keys = set(current_signals.keys()) & set(stored_signals.keys())
        if not common_keys:
            continue

        sum_sq_diff = 0
        all_keys = set(current_signals.keys()) | set(stored_signals.keys())
        
        for bssid in all_keys:
            v1 = current_signals.get(bssid, -100)
            v2 = stored_signals.get(bssid, -100)
            sum_sq_diff += (v1 - v2) ** 2
            
        euclidean_dist = math.sqrt(sum_sq_diff)
        
        distances.append({
            'dist': euclidean_dist,
            'lat': fp_data['coords'][0],
            'lon': fp_data['coords'][1]
        })

    distances.sort(key=lambda x: x['dist'])
    nearest_neighbors = distances[:k]
    
    if not nearest_neighbors:
        return None

    avg_lat = sum(n['lat'] for n in nearest_neighbors) / len(nearest_neighbors)
    avg_lon = sum(n['lon'] for n in nearest_neighbors) / len(nearest_neighbors)
    
    print(f"📍 Position estimée (WiFi) basée sur {len(nearest_neighbors)} voisins: {avg_lat}, {avg_lon}")
    
    return {'lat': avg_lat, 'lon': avg_lon}


def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL;') 
    
    # Table pour l'apprentissage (Training)
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

    # [NOUVEAU] Table pour l'historique des estimations (Testing)
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
    
    conn.commit()
    return conn

# ===========================================
# Structures de données
# ===========================================

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

class PodoMeter(BaseModel):
    s: int

# ===========================================
# LOGIQUE de création de la BDD (Training)
# ===========================================

GPS_TIME_WINDOW_MAX = 30000 
MIN_DISTANCE_FOR_MOVEMENT = 5.0 

rssi_buffer = []
gps_buffer = []

def lerp(start, end, alpha):
    return start + (end - start) * alpha

def get_distance_haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def process_buffers():
    global rssi_buffer, gps_buffer

    if len(gps_buffer) < 4:
        return

    gps_buffer.sort(key=lambda x: x['timestamp'])
    rssi_buffer.sort(key=lambda x: x['timestamp'])

    unprocessed_rssi = []
    to_insert = []

    for rssi_data in rssi_buffer:
        t_target = rssi_data['timestamp']

        next_gps_index = -1
        for i, g in enumerate(gps_buffer):
            if g['timestamp'] >= t_target:
                next_gps_index = i
                break
        
        if next_gps_index == -1:
            unprocessed_rssi.append(rssi_data)
            continue
        if next_gps_index == 0:
            continue

        next_gps = gps_buffer[next_gps_index]
        prev_gps = gps_buffer[next_gps_index - 1]

        time_diff = next_gps['timestamp'] - prev_gps['timestamp']
        
        if time_diff > GPS_TIME_WINDOW_MAX:
            continue

        dist_meters = get_distance_haversine(prev_gps['lat'], prev_gps['lon'], next_gps['lat'], next_gps['lon'])
        
        final_lat = 0.0
        final_lon = 0.0
        method = ""

        if dist_meters < MIN_DISTANCE_FOR_MOVEMENT:
            final_lat = prev_gps['lat']
            final_lon = prev_gps['lon']
            method = 'static'
        else:
            alpha = (t_target - prev_gps['timestamp']) / time_diff
            final_lat = lerp(prev_gps['lat'], next_gps['lat'], alpha)
            final_lon = lerp(prev_gps['lon'], next_gps['lon'], alpha)
            method = 'interpolated'
            
        to_insert.append((
            rssi_data['timestamp'], rssi_data['bssid'], rssi_data['rssi_average'],
            rssi_data['rssi_median'], rssi_data['rssi_std'], rssi_data['channel'],
            final_lat, final_lon, method
        ))

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

    rssi_buffer = unprocessed_rssi
    
    if rssi_buffer:
        oldest_needed = rssi_buffer[0]['timestamp']
        boundary_index = 0
        for i, g in enumerate(gps_buffer):
            if g['timestamp'] > oldest_needed:
                boundary_index = i
                break
        if boundary_index > 1:
            del gps_buffer[:boundary_index - 1]
    elif len(gps_buffer) > 1:
        gps_buffer = gps_buffer[-1:]

# ===========================================
# ROUTES API
# ===========================================

@app.get("/map.js")
async def read_map_js():
    file_path = os.path.join(os.path.dirname(__file__), "map.js")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "File not found"}, 404

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

# ===========================================
# ROUTE DE TEST (POSITIONNEMENT + HISTORISATION)
# ===========================================
@app.post("/esp32_test")
async def receive_esp32_test(payload: EspBatchPayload):
    global last_estimated_position
    
    formatted_scan = []
    print(f"Reception d'un scan de test avec {len(payload.networks)} réseaux")

    for net in payload.networks:
        item = SimpleNamespace(bssid=net.bssid, rssi=net.rssi_avg)
        formatted_scan.append(item)
    
    # 1. Calcul de la position
    estimated_pos = calculate_position_knn(formatted_scan, k=3)
    
    if estimated_pos:
        # 2. Mise à jour de la variable globale (pour l'affichage "live" sur la carte)
        last_estimated_position = {
            "lat": estimated_pos['lat'],
            "lon": estimated_pos['lon'],
            "timestamp": payload.timestamp,
            "neighbors_count": len(payload.networks)
        }

        # 3. [NOUVEAU] Sauvegarde dans la BDD pour historique
        try:
            # On ouvre une connexion locale à la route pour l'insertion rapide
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
            print(f"💾 Position sauvegardée en DB: {estimated_pos}")
        except Exception as e:
            print(f"⚠️ Erreur sauvegarde historique: {e}")
    
    return {"status": "OK", "position": estimated_pos}

@app.get("/map_test")
async def read_map_test():
    file_path = os.path.join(os.path.dirname(__file__), "map_test.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "Créez le fichier map_test.html d'abord !"}, 404

@app.get("/api/last_position")
async def get_last_position():
    # Retourne toujours la dernière position connue en RAM
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