import './style.css';
import {Map, View} from 'ol';
import TileLayer from 'ol/layer/Tile';
import OSM from 'ol/source/OSM';
import {toLonLat} from 'ol/proj.js';

const map = new Map({
  target: 'map',
  layers: [
    new TileLayer({
      source: new OSM()
    })
  ],
  view: new View({
    center: [0, 0],
    zoom: 2
  })
});


map.on('click', (event) => {
  const coords = toLonLat(event.coordinate);
  const data =  Date.now()
  console.log("Longitude:", coords[0], "Latitude:", coords[1], "Timestamps:", data);
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