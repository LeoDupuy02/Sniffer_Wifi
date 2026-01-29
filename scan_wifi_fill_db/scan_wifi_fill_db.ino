#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiMulti.h> // Initialement pour gérer plusieurs clients
#include "time.h"
#include "esp_wifi.h"
#include <vector>     // Pour les listes dynamiques
#include <algorithm>  // Pour le tri (calcul médiane)
#include <cmath>      // Pour sqrt et pow (écart-type)

WiFiMulti wifiMulti;

/*
 Récote des données pour l'établissement de la base de donnée de réseau.
 Pour chaque réseau détecté on effectue N mesures et l'on en retire : moyenne, médiane, écart-type du RSSI et BSSID 
 On retourne les données au serveur en y ajoutant un timestamp.
*/

// Configuration
const char* ntpServer = "pool.ntp.org"; // serveur pour obtenir l'heure
const char* serverUrl = "https://unrenovated-dishonorably-patience.ngrok-free.dev/esp32"; // url du serveur distant accessible via ngrok
const int NB_SCANS = 5; // Nombre de mesures à un point GPS donné

// Structure de stockage des mesures
struct NetworkMeasure {
  String bssid;
  String ssid;
  int channel;
  std::vector<int> rssi_values; // Liste dynamique des RSSI mesurés
};

// Listes des Jsons en attente d'être envoyés : limite à 5 Jsons
int stored_Jsons = 0;
String Jsons[5];

// Requête HTTP
void request(void){

  // Connexion et timeout pour l'envoi de grosses données
  WiFiClientSecure client;
  // Ignorer la vérification SSL
  client.setInsecure(); 
  HTTPClient http;
  http.setTimeout(5000); 

  // Transmission des données depuis la liste des Jsons non envoyés
  for(int i = 0; i<stored_Jsons; i++){
    if (http.begin(client, serverUrl)) {
      // type de donnée transmise
      http.addHeader("Content-Type", "application/json");
      // Evite un message d'erreur (abus) de la part du serveur)
      http.addHeader("ngrok-skip-browser-warning", "true");
      
      // Ecriture des donnees sur le serveur
      int httpResponseCode = http.POST(Jsons[i]);
      
      // Vérification du code retourné par le serveur 
      if (httpResponseCode > 0 && httpResponseCode != 404) {
        Serial.printf("Code HTTP: %d\n", httpResponseCode);
        String response = http.getString();
        Serial.println(response);

        // Supprime le Json venant d'être transmis de la liste des Jsons à transmettre
        remove_Json(i);
      } else {
        // c_str retourne un pointeur vers un tableau contenant un caractère de fin de chaine : permet d'utiliser %s (code C)
        Serial.printf("Erreur HTTP: %s\n", http.errorToString(httpResponseCode).c_str());

      }
      http.end(); 
    } else {
      Serial.println("Impossible de se connecter au serveur");
    }
  }
}

// Ajout d'un Json à envoyer à un buffer
void save_Json(String payload){
  // Si buffer Json plein on écrase le plus ancien
  if(stored_Jsons == 5){
    for(int i = 0; i<4; i++){
      Jsons[i] = Jsons[i+1];
    }
    Jsons[4] = payload;
  }
  // Sinon on ajoute à la fin
  else{
    Jsons[stored_Jsons] = payload;
    stored_Jsons += 1;
  }
}

// Enlève l'élément d'index 'index'
void remove_Json(int index){
  if(index<5 && index>=0){
    for(int i = index; i<5; i++){
      Jsons[i] = Jsons[i+1];
    }
    stored_Jsons -= 1;
  }
  Serial.printf("Nettoyage : Jsons comprend maintenant %d payload\n", stored_Jsons);
}

// SETUP
void setup() {

  Serial.begin(115200);

  // Mode station  : esp32 comme client du smartphone configuré en point d'accès
  WiFi.mode(WIFI_STA);
  // Efface les configurations Wifi sauvegardées
  WiFi.disconnect(true);
  // Configuration du wifi (smartphone perso comme Access Point)
  wifiMulti.addAP("Pixel_7935", "Summit12"); 


  Serial.print("Connexion initiale");
  while (wifiMulti.run() != WL_CONNECTED) {
    Serial.print(".");
    delay(500);
  }
  Serial.println("\nConnecté !");
  
  // HEURE NTP (pour timestamps)
  // décalage horaire, décalage heure d'été, deux serveurs
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  // Structure C++ pour gérer les heures
  struct tm timeinfo;
  getLocalTime(&timeinfo);
  
  // Optimisation du scan
  WiFi.setScanMethod(WIFI_FAST_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
}

void loop() {

  // Si on est connecté à l'AP
  if (wifiMulti.run() == WL_CONNECTED) {
    
    // Liste pour stocker tous les réseaux uniques rencontrés
    std::vector<NetworkMeasure> networks;

    Serial.printf("Début de la séquence de %d scans...\n", NB_SCANS);
    unsigned long startSeq = millis();

    // Scans multiples : NB_SCANS scans
    int nb_networks = 0;
    for (int s = 0; s < NB_SCANS; s++) {
      Serial.printf("Scan %d/%d\n", s+1, NB_SCANS);
      
      // Scan
      int n = WiFi.scanNetworks(false, true); // Scan bloquant, inclut les réseaux masqués, envoie des probe-requests

      // Etude des données
      for (int i = 0; i < n; i++) {
        String bssid = WiFi.BSSIDstr(i);
        int rssi = WiFi.RSSI(i);
        String ssid = WiFi.SSID(i);
        int channel = WiFi.channel(i);
        
        // FILTRES 
        // Filtre Smartphone (Bit Locally Administered)
        uint8_t* bssid_bytes = WiFi.BSSID(i);
        if (bssid_bytes[0] & 0x02) continue; 

        // Filtre signal faible
        if (rssi < -75) continue; 

        // AJOUT A LA LISTE DU SCAN

        // Chercher si ce réseau est deja present
        bool found = false;
        for (int i = 0; i < nb_networks; i++) {
          // S'il existe déjà
          if (networks[i].bssid.equals(bssid)) {
            networks[i].rssi_values.push_back(rssi); // On ajoute la nouvelle mesure
            found = true;
            break;
          }
        }
        // Si le réseau n'était pas présent on l'ajoute
        if (!found) {
          NetworkMeasure newNet;
          newNet.bssid = bssid;
          newNet.ssid = ssid;
          newNet.channel = channel;
          newNet.rssi_values.push_back(rssi);
          networks.push_back(newNet);
          nb_networks += 1;
        }
      }

      // Nettoyage du buffer wifi
      WiFi.scanDelete();
      delay(100);

    }
    Serial.printf("Séquence terminée en %lu ms. Calcul des stats...\n", millis() - startSeq);

    // STATISTIQUE ET JSON
    time_t now;
    time(&now);
    
    String jsonPayload = "{\"timestamp\":" + String(now) + ", \"networks\":[";
    bool first = true;
    int countValid = 0;

    for (int i = 0; i < nb_networks; i++){

      // On passe si aucune valeurs dans les rssi
      if (networks[i].rssi_values.empty()) continue;

      // STATISTIQUES
      // Moyenne
      double sum = 0;
      for (int val : networks[i].rssi_values) sum += val;
      double avg = sum / networks[i].rssi_values.size();

      // Mediane
      // Note : en C++ le = fait une copie totale des valeurs : ce n'est pas un simple pointeur
      // Note : trie de l'adresse du début de la liste jusqu'à celle de fin
      std::vector<int> sorted = networks[i].rssi_values;
      std::sort(sorted.begin(), sorted.end());
      double median = 0;
      if (sorted.size() % 2 == 0) {
         median = (sorted[sorted.size()/2 - 1] + sorted[sorted.size()/2]) / 2.0;
      } else {
         median = sorted[sorted.size()/2];
      }

      // Ecart type (Std)
      double sq_sum = 0;
      for (int val : networks[i].rssi_values) {
        sq_sum += pow(val - avg, 2);
      }
      double std_dev = sqrt(sq_sum / networks[i].rssi_values.size());

      // Séparation des réseaux
      if (!first) jsonPayload += ",";
      first = false;

      // Nettoyage SSID : protège la structure du JSON
      String safe_ssid = networks[i].ssid;
      safe_ssid.replace("\\", "\\\\"); 
      safe_ssid.replace("\"", "\\\"");

      // Construction
      jsonPayload += "{";
      jsonPayload += "\"bssid\":\"" + networks[i].bssid + "\",";
      jsonPayload += "\"ssid\":\"" + safe_ssid + "\",";
      jsonPayload += "\"channel\":" + String(networks[i].channel) + ",";
      // Nouveaux champs stats :
      jsonPayload += "\"rssi_avg\":" + String(avg) + ",";
      jsonPayload += "\"rssi_med\":" + String(median) + ",";
      jsonPayload += "\"rssi_std\":" + String(std_dev);
      jsonPayload += "}";
      
      Serial.printf("AP: %s | N:%d | Avg: %.1f | Std: %.2f\n", 
                    safe_ssid.c_str(), networks[i].rssi_values.size(), avg, std_dev);
      
      countValid += 1;
      // Limite la taille du JSON
      if(countValid >= 15) break; 
    }

    jsonPayload += "]}";

    // ENVOI si des données ont été récupérées
    Serial.printf("Transmission des données\n");
    if (countValid > 0) {
      Serial.println("Envoi des données moyennées...");
      save_Json(jsonPayload);
      request();
      Serial.printf("La liste Jsons comprend actuellement %d payload à transmettre !\n", stored_Jsons);

    } else {
      Serial.println("Aucun réseau valide après filtrage...");
    }

  } else {
    Serial.println("WiFi perdu...");
  }

  Serial.println("Pause 5s...");
  delay(5000); 
}

