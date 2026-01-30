#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiMulti.h>
#include "time.h"
#include "esp_wifi.h"
#include <algorithm> // Pour std::sort et std::copy

WiFiMulti wifiMulti;

// CONFIGURATION
const char* ntpServer = "pool.ntp.org";
const char* serverUrl = "https://unrenovated-dishonorably-patience.ngrok-free.dev/esp32";
const char* tel_AP = "Pixel_7935";
const char* tel_PASS = "Summit12";

const int NB_SCANS = 5;

const int MAX_NETWORKS = 10;   // Maximum de réseaux stockés après filtrage
const int MAX_SAMPLES = 10;    // Maximum de mesures RSSI par réseau
const int MAX_CHANNELS = 14;   // Nombre max de canaux WiFi (1-14)

// STRUCTURE DE DONNEES
struct NetworkMeasure {
  String bssid;
  String ssid;
  int channel;
  int rssi_values[MAX_SAMPLES]; 
  int rssi_count;               // Nombre de mesures stockées
};

// BUFFER D'ENVOI EN JSON
int stored_Jsons = 0;
String Jsons[5];

// ============================================
// GESTION HTTP
// ============================================

// Nettoyage du buffer
void remove_Json(int index){
  if(index < stored_Jsons && index >= 0){
    for(int i = index; i < stored_Jsons - 1; i++){
      Jsons[i] = Jsons[i+1];
    }
    stored_Jsons -= 1;
  }
  Serial.printf("Buffer (remove) : %d payloads en attente\n", stored_Jsons);
}

// requête http au serveur (transmission du buffer si possible)
// charger auparavant le json à transmettre dans le Buffer
void request(void){
  WiFiClientSecure client;
  client.setInsecure(); 
  HTTPClient http;
  http.setTimeout(5000); 

  for(int i = 0; i < stored_Jsons; i++){
    if (http.begin(client, serverUrl)) {
      http.addHeader("Content-Type", "application/json");
      http.addHeader("ngrok-skip-browser-warning", "true");
      
      int httpResponseCode = http.POST(Jsons[i]);
      
      if (httpResponseCode > 0 && httpResponseCode != 404) {
        Serial.printf("Code HTTP: %d\n", httpResponseCode);
        remove_Json(i);
        i--;
      } 
      else {
        Serial.printf("Erreur HTTP: %s\n", http.errorToString(httpResponseCode).c_str());
      }
      http.end(); 
    } 
    else {
      Serial.println("Impossible de se connecter au serveur");
    }
  }
}

// Sauvegarde d'un json dans le buffer
void save_Json(String payload){
  if(stored_Jsons == 5){
    // Décale tout pour écraser le premier si plein
    for(int i = 0; i < 4; i++){
      Jsons[i] = Jsons[i+1];
    }
    Jsons[4] = payload;
  }
  else {
    Jsons[stored_Jsons] = payload;
    stored_Jsons += 1;
  }
}

// ==========================================
// TRAITEMENT DU SCAN
// ==========================================

void processScanResult(int n, NetworkMeasure networks[], int &netCount, int activeChannels[], int &chCount, bool recordChannels) {
  // Args :
  //  - n - nombre de réseaux scannés
  //  - networks - liste de réseaux (données des scans pour chaque réseau sous la forme de la structure définie précédemment)
  //  - netCount - nombre de réseaux présents dans netCount (scannés et valides)
  //  - activeChannels - channels détectées lors du premier scan,
  //  - chCount - nombre de channels détectées lors du premier scan,
  //  - recordChannels - permet de différencier entre le scan initial récupérant les réseaux et channels d'intếrét (lent) et les scans rapides réalisés par la suite

  for (int i = 0; i < n; i++) {

    // RECUPERATION DES DONNEES DU RESEAU SCANNE
    String bssid = WiFi.BSSIDstr(i);
    int rssi = WiFi.RSSI(i);
    String ssid = WiFi.SSID(i);
    int channel = WiFi.channel(i);
    
    // FILTRE : SMARTPHONE ET RESEAUX FAIBLES
    uint8_t* bssid_bytes = WiFi.BSSID(i);
    if (bssid_bytes[0] & 0x02) continue; // Filtre les APs Virtuels
    if (rssi < -75) continue;            // Filtre les signaux faibles

    // ENREGISTRE CANAUX ACTIFS
    if (recordChannels) {
      bool chFound = false;

      // Check si le channel n'est pas déjà dans la liste
      for(int k = 0; k < chCount; k++) {
        if(activeChannels[k] == channel) {
          chFound = true; 
          break;
        }
      }
      // Ajout du channel s'il n'est pas déjà dans la liste
      if(!chFound && chCount < MAX_CHANNELS) {
        activeChannels[chCount] = channel;
        chCount++;
      }
    }

    // ENREGISTREMENT DU RESEAU
    int foundIndex = -1;
    
    // Check si le réseau est déjà dans notre liste
    for (int k = 0; k < netCount; k++) {
      if (networks[k].bssid.equals(bssid)) {
        foundIndex = k;
        break;
      }
    }
    
    // Si nouveau réseau on l'ajoute dans networks
    if (foundIndex == -1) {
      if (netCount < MAX_NETWORKS) {
        foundIndex = netCount;
        
        networks[foundIndex].bssid = bssid;
        networks[foundIndex].ssid = ssid;
        networks[foundIndex].channel = channel;
        networks[foundIndex].rssi_count = 0;
        
        netCount++; 
      } else {
        // Liste pleine, on ignore le reste
        continue; 
      }
    }

    // ENREGISTREMENT DU RSSI DANS LE BON RESEAU
    if (foundIndex != -1) {
      if (networks[foundIndex].rssi_count < MAX_SAMPLES) {
        int currentRssiIndex = networks[foundIndex].rssi_count;
        networks[foundIndex].rssi_values[currentRssiIndex] = rssi;
        networks[foundIndex].rssi_count++;
      }
    }
  }
}

// ==========================================
// SETUP
// ==========================================

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);

  // Connexion à l'AP perso
  wifiMulti.addAP(tel_AP, tel_PASS); 

  Serial.print("Connexion initiale");
  while(wifiMulti.run() != WL_CONNECTED){
    Serial.print(".");
    delay(500);
  }
  Serial.println("\nConnecté !");
  
  // Récupération de l'heure locale pour générer les timestamps
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  
  // Configuration des scans futurs
  WiFi.setScanMethod(WIFI_FAST_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
}

// ==========================================
// LOOP
// ==========================================

void loop() {

  if (wifiMulti.run() == WL_CONNECTED) {
    
    // DECLARATIONS DES STRUCTURES DE STOCKAGE DE DONNEES
    NetworkMeasure networks[MAX_NETWORKS];
    int netCount = 0;
    
    int activeChannels[MAX_CHANNELS];
    int chCount = 0;

    Serial.printf(">>>> DÉBUT DE LA SEQUENCE DE SCANS (%d tours) <<<\n", NB_SCANS);
    unsigned long startSeq = millis();

    // SCAN INITIAL
    // Scan de tous les channels (lent)

    Serial.println("1 : Scan Complet");
    int n = WiFi.scanNetworks(false, true, false, 300); 
    
    processScanResult(n, networks, netCount, activeChannels, chCount, true);
    WiFi.scanDelete();

    if (chCount == 0) {
       Serial.println("Aucun réseau valide trouvé -> Abandon du scan actuel !");
       delay(2000);
       return;
    }

    Serial.print("-> Canaux actifs : ");
    for(int c = 0; c < chCount; c++) Serial.printf("%d ", activeChannels[c]);
    Serial.println();

    // SCANS RAPIDES
    for (int s = 1; s < NB_SCANS; s++) {
      for (int k = 0; k < chCount; k++) {
        int targetCh = activeChannels[k];
        
        int n_ch = WiFi.scanNetworks(false, true, false, 110, targetCh);
        processScanResult(n_ch, networks, netCount, activeChannels, chCount, false);
        WiFi.scanDelete();
      }
      delay(50);
    }

    Serial.printf("Séquence terminée en %lu ms.\n", millis() - startSeq);

    // STATISTIQUES ET JSON
    
    // temps pour timestamp
    time_t now_ts;
    time(&now_ts);
    
    String jsonPayload = "{\"timestamp\":" + String(now_ts) + ", \"networks\":[";
    bool first = true;
    int countValid = 0;

    for (int i = 0; i < netCount; i++) {
      
      NetworkMeasure &net = networks[i]; 

      // si aucun rssi enregistré
      if (net.rssi_count == 0) continue;
      
      // Moyenne des rssis
      double sum = 0;
      for (int j = 0; j < net.rssi_count; j++) sum += net.rssi_values[j];
      double avg = sum / net.rssi_count;

      // Médiane (tri d'abord) des rssi
      int sortedBuffer[MAX_SAMPLES];
      std::copy(net.rssi_values, net.rssi_values + net.rssi_count, sortedBuffer);
      std::sort(sortedBuffer, sortedBuffer + net.rssi_count);

      double median = 0;
      if (net.rssi_count % 2 == 0) {
         median = (sortedBuffer[net.rssi_count/2 - 1] + sortedBuffer[net.rssi_count/2]) / 2.0;
      } else {
         median = sortedBuffer[net.rssi_count/2];
      }

      // Ecart type des rssi
      double sq_sum = 0;
      for (int j = 0; j < net.rssi_count; j++) {
        sq_sum += pow(net.rssi_values[j] - avg, 2);
      }
      double std_dev = sqrt(sq_sum / net.rssi_count);

      // Json
      if (!first) jsonPayload += ",";
      first = false;

      String safe_ssid = net.ssid;
      safe_ssid.replace("\"", "\\\""); 
      safe_ssid.replace("\\", "\\\\");

      jsonPayload += "{";
      jsonPayload += "\"bssid\":\"" + net.bssid + "\",";
      jsonPayload += "\"ssid\":\"" + safe_ssid + "\",";
      jsonPayload += "\"channel\":" + String(net.channel) + ",";
      jsonPayload += "\"rssi_avg\":" + String(avg) + ",";
      jsonPayload += "\"rssi_med\":" + String(median) + ",";
      jsonPayload += "\"rssi_std\":" + String(std_dev);
      jsonPayload += "}";
      
      // Debug
      Serial.printf("AP: %s | N:%d | Avg: %.1f\n", safe_ssid.c_str(), net.rssi_count, avg);
      
      countValid++;
    }

    jsonPayload += "]}";

    // TRANSMISSION
    if (countValid > 0) {
      save_Json(jsonPayload);
      request(); 
    } else {
      Serial.println("Aucune donnée à envoyer.");
    }

  } else {
    Serial.println("WiFi perdu...");
  }

  Serial.println("Pause 2s...");
  delay(2000); 
}


