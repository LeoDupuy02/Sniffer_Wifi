const express = require("express");
const fs = require("fs");
const path = require("path");

const app = express();

// Parse JSON dans le body
app.use(express.json());

// Servir des fichiers statiques
app.use(express.static(__dirname));

/* ============================
   API : écriture lat/lon
   ============================ */
app.post("/data", (req, res) => {
  const { lat, lon, timestamp } = req.body;

  if (!lat || !lon || !timestamp) {
    return res.status(400).send("Champs manquants dans /data");
  }

  const line = `{"timestamp":${timestamp},"lat":${lat},"lon":${lon}}\n`;

  fs.appendFile("clicks.txt", line, (err) => {
    if (err) return res.status(500).send("Erreur d’écriture fichier");
    console.log("✔ Écrit :", line.trim());
    res.send("OK");
  });
});

/* ============================
   API : réception JSON ESP32
   ============================ */
app.post("/esp32", (req, res) => {
  const data = req.body;

  if (!data || !data.data) {
    return res.status(400).send("Champ 'data' manquant dans le JSON");
  }

  const payload = {
    timestamp: Date.now()/1000,   // utile pour tracer les paquets
    data: data.data
  };

  const line = JSON.stringify(payload) + "\n";

  fs.appendFile("pos.txt", line, (err) => {
    if (err) {
      console.error("Erreur écriture :", err);
      return res.status(500).send("Erreur d’écriture fichier");
    }

    console.log("✔ ESP32 →", payload);
    res.send("OK");
  });
});

// Fallback at the end !
app.use((req, res) => {
  res.sendFile(path.join(__dirname, "index.html"));
});

app.listen(5000, () => {
  console.log("Serveur Node running on http://localhost:5000");
});
