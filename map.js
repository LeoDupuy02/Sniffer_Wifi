map = new ol.Map({
  target: "map",
  layers: [
    new ol.layer.Tile({
      source: new ol.source.OSM(),
    })
  ],
  view: new ol.View({
    center: ol.proj.fromLonLat([2.35, 48.85]),
    zoom: 15
  })
});

map.on("click", (evt) => {
  const [lon, lat] = ol.proj.toLonLat(evt.coordinate);
  console.log(lat, lon);

  fetch("/gps", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, timestamp: Date.now() })
  });
});

// 2. Création de la couche pour afficher vos points GPS
const vectorSource = new ol.source.Vector();

const vectorLayer = new ol.layer.Vector({
  source: vectorSource,
  style: new ol.style.Style({
    image: new ol.style.Circle({
      radius: 6,
      fill: new ol.style.Fill({color: 'red'}),
      stroke: new ol.style.Stroke({color: 'white', width: 2})
    })
  })
});

// Ajouter la couche à la carte
map.addLayer(vectorLayer);

// 3. Logique du bouton "Charger les points"
document.getElementById('loadPointsBtn').addEventListener('click', () => {
  console.log("Chargement des points...");

  fetch('/points')
    .then(response => response.json())
    .then(data => {
      console.log(`Reçu ${data.length} points.`);
      
      // Nettoyer les anciens points pour éviter les doublons
      vectorSource.clear();

      if (data.length === 0) {
        alert("Aucun point dans la base de données !");
        return;
      }

      // Pour chaque point reçu de la BDD
      data.forEach(point => {
        // Vérification de sécurité pour éviter les points vides (0,0)
        if (point.lat && point.lon) {
          
          // Création de la géométrie OpenLayers
          const feature = new ol.Feature({
            geometry: new ol.geom.Point(ol.proj.fromLonLat([point.lon, point.lat])),
            // On peut stocker des infos dans la feature pour les popups plus tard
            rssi: point.rssi_avg,
            timestamp: point.timestamp
          });

          vectorSource.addFeature(feature);
        }
      });

      // Zoomer automatiquement pour voir tous les points
      const extent = vectorSource.getExtent();
      if (!ol.extent.isEmpty(extent)) {
        map.getView().fit(extent, { padding: [50, 50, 50, 50], duration: 1000 });
      }
      
    })
    .catch(err => console.error("Erreur chargement points:", err));
});
