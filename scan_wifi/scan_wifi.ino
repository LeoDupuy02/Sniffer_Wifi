#include <WiFi.h>
#include <HTTPClient.h>
#include <TimeLib.h>

const char* ssid = "Test4IoT";
const char* password = "remsch2024";

//const char* serverName = "http://10.149.168.115:5000/esp32";
const char* serverName = "https://unrenovated-dishonorably-patience.ngrok-free.dev/esp32";

unsigned long lastTime = 0;
unsigned long timerDelay = 5000;

uint64_t id = 0;
uint8_t mac = 0;
uint8_t list_scan[5];

uint8_t* bssid;

tmElements_t te;  //Time elements structure
time_t unixTime; // a time stamp

WiFiClient client;
HTTPClient http;

int sort_desc(const void *cmp1, const void *cmp2)
{
  // Need to cast the void * to int *
  int a = *((int *)cmp1);
  int b = *((int *)cmp2);
  // The comparison
  return a > b ? -1 : (a < b ? 1 : 0);
}

// Connect to Wifi
void initWiFi() {
  WiFi.mode(WIFI_STA);
  
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi ..");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print('.');
    delay(1000);
  }
  Serial.println(WiFi.localIP());
}

void setup() {
  Serial.begin(115200);
  initWiFi();
  Serial.print("RRSI: ");
  Serial.println(WiFi.RSSI());

  unixTime =  makeTime(te);
  setTime(unixTime);

  Serial.println("Setup done");
}

void loop() {

  if(WiFi.status() != WL_CONNECTED){
    Serial.println("0 - Reconnecting to Wifi !");
    WiFi.begin(ssid, password);
    Serial.print("Connecting to WiFi ..");
    while (WiFi.status() != WL_CONNECTED) {
      Serial.print('.');
      delay(2000);
    }
    Serial.println(WiFi.localIP());
  }

  Serial.println(("--------------------------------"));
  Serial.println("1 - Scan start");

  // WiFi.scanNetworks will return the number of networks found
  int n = WiFi.scanNetworks();
  Serial.println("2 - Scan done");

  // Fabrication de la trame
  String httpRequestData = "{ \"data\": [";
  bool first = true;
  for (int i = 0; i < n; i++) {

    // Récupérer la BSSID
    uint8_t* bssid = WiFi.BSSID(i);
    String macStr = "";
    if(bssid[0] & 0x02){
      Serial.print("Note : ");
      Serial.print(WiFi.SSID(i));
      Serial.println(" : Smartphone : removed !");
      continue;
    }
    if(WiFi.RSSI(i)<-60){
      Serial.print("Note : ");
      Serial.print(WiFi.SSID(i));
      Serial.println(" : RSSI trop faible : removed !");
      continue;
    }
    for (int j = 0; j < 6; j++) {
      if (bssid[j] < 16) macStr += "0";  // ajout d'un 0 si < 0x10
      macStr += String(bssid[j], HEX);
    }

    // Heure
    unixTime = now();

    if (!first) httpRequestData += ",";
    first = false;

    // Construire l’objet JSON
    httpRequestData += "{";
    httpRequestData += "\"mac\":\"" + macStr + "\",";
    httpRequestData += "\"ssid\":\"" + String(WiFi.SSID(i)) + "\",";
    httpRequestData += "\"rssi\":" + String(WiFi.RSSI(i)) + ",";
    httpRequestData += "\"timestamp\":" + String(hour(unixTime)*3600+minute(unixTime)*60+second(unixTime));
    httpRequestData += "}";
  }
  httpRequestData += "]}";

  // Transmission bloquante
  while((millis() - lastTime) > timerDelay) {
    //Check WiFi connection status
    if(WiFi.status()== WL_CONNECTED){

      if(httpRequestData == "{ \"data\": []}"){
        Serial.println("No values on the base !");
        break;
      }
    
      // Your Domain name with URL path or IP address with path
      http.begin(client, serverName);
      Serial.println(httpRequestData);
      http.addHeader("Content-Type", "application/json");
      int httpResponseCode = http.POST(httpRequestData);
      
      Serial.print("3 - HTTP Response code: ");
      Serial.println(httpResponseCode);
        
      // Free resources
      http.end();
    }
    else {
      Serial.println("4 - No responses !");
    }
    lastTime = millis();
  }

  // delay entre mesures
  delay(1000);

}

//

