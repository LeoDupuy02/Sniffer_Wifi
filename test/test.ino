#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiMulti.h>
#include "time.h"
#include "esp_wifi.h"

WiFiMulti wifiMulti;

/* 
  Code de test 
*/

// Config local time
const char* ntpServer = "pool.ntp.org";
#define ledPin 2

// Requête HTTP
void request(String payload){

  // Serveur à distance via ngrok
  const char* serverUrl = "https://unrenovated-dishonorably-patience.ngrok-free.dev/esp32";
  WiFiClientSecure client;
  // Ignorer la vérification SSL
  client.setInsecure(); 
  
  HTTPClient http;
  
  if (http.begin(client, serverUrl)) {
    http.addHeader("Content-Type", "application/json");
    http.addHeader("ngrok-skip-browser-warning", "true");
    
    // Transmission des données et attente du code de retour
    int httpResponseCode = http.POST(payload);
    
    if (httpResponseCode > 0) {
       Serial.printf("Code HTTP: %d\n", httpResponseCode);
    } else {
      Serial.printf("Erreur HTTP: %s\n", http.errorToString(httpResponseCode).c_str());
    }
    http.end(); 
  } else {
    Serial.println("Impossible de se connecter au serveur");
  }

}

void setup() {

  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);

  // 1. CONFIGURATION WIFI
  wifiMulti.addAP("Pixel_7935", "Summit12"); 

  Serial.print("Connexion initiale");
  while (wifiMulti.run() != WL_CONNECTED) {
    Serial.print(".");
    delay(500);
  }
  Serial.println("\nConnecté !");
  Serial.println(WiFi.localIP());

  // 2. RECUPERATION DE L'HEURE (NTP)
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  
  // Attente de la synchro heure
  struct tm timeinfo;
  if(!getLocalTime(&timeinfo)){
    Serial.println("Echec heure NTP (sera réessayé automatiquement)");
  }
  
  // Configuration du scan
  WiFi.setScanMethod(WIFI_FAST_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);

  // COnfiguration de la led de perte de Wifi
  pinMode (ledPin, OUTPUT);

}

void loop() {

  // RECONNEXION AUTOMATIQUE SI LE LIEN EST PERDU
  if (wifiMulti.run() == WL_CONNECTED) {
    
    // RECUPERATION DU TIMESTAMP ACTUEL
    time_t now;
    time(&now);

    // SCAN (BLOQUANT)
    Serial.println("Scan en cours...");
    int n = WiFi.scanNetworks(false, true); // false = bloquant, true = inclure réseaux masqués
    
    // TRAITEMENT SI DES RESEAUX SONT PRESENTS
    if (n > 0) {
      int count = (n < 7) ? n : 10; // Max 10 networks (to take smartphones into account)
      
      // Build JSON
      String jsonPayload = "{\"timestamp\":" + String(now) + ", \"networks\":[";

      // NETTOYAGE (SMARTPHONES & WEAK NETWORKS) et formatage
      for (int i = 0; i < count; i++) {
        if (i > 0) jsonPayload += ",";

        String bssid = WiFi.BSSIDstr(i);
        int rssi = WiFi.RSSI(i);
        int channel = WiFi.channel(i);
        String ssid = WiFi.SSID(i);

        // Remove smartphones
        if(bssid[0] & 0x02){
          Serial.print("Note : ");
          Serial.print(ssid);
          Serial.println(" : Smartphone : removed !");
          continue;
        }

        // Remove weack networks
        if(rssi<-60){
          Serial.print("Note : ");
          Serial.print(ssid);
          Serial.print(" : RSSI trop faible : removed !");
          Serial.println(rssi);
          continue;
        }

        // Clean ssids (to protect JSON)
        ssid.replace("\"", "\\\""); 
        ssid.replace("\\", "\\\\");

        jsonPayload += "{\"bssid\":\"" + bssid + "\",";
        jsonPayload += "\"ssid\":\"" + ssid + "\","; 
        jsonPayload += "\"rssi\":" + String(rssi) + ",";
        jsonPayload += "\"channel\":" + String(channel) + "}";
        
        // Debug
        Serial.printf("#%d %s (%s) : %d dBm\n", i, ssid.c_str(), bssid.c_str(), rssi);
      }

      jsonPayload += "]}";

      // ENVOI au server pour le traitement
      Serial.println("Envoi des données...");
      request(jsonPayload);
      
      // NETTOYAGE de la RAM
      WiFi.scanDelete();
    } else {
      Serial.println("Aucun réseau trouvé.");
    }

  } else {
    Serial.println("WiFi perdu, tentative de reconnexion...");
  }

  // 7. ATTENTE DE 3 SECONDES
  Serial.println("Attente 3s...");
  delay(3000); 
}


