import Map from './ol/Map.js';
console.log("import OK");
import View from './ol/View.js';
console.log("import OK");
import TileLayer from './ol/layer/Tile.js';
console.log("import OK");
import OSM from './ol/source/OSM.js';
console.log("import OK");
import { fromLonLat, toLonLat } from './ol/proj.js';
console.log("import OK");

const map = new Map({
    target: 'map',
    layers: [
        new TileLayer({
            source: new OSM(),
        }),
    ],
    view: new View({
        center: fromLonLat([2.3522, 48.8566]),
        zoom: 12
    })
});

// Événement clic
map.on('click', (evt) => {
    const coords = toLonLat(evt.coordinate);
    const [lon, lat] = coords;
    console.log("Longitude:", lon);
    console.log("Latitude :", lat);
    sendToServer(lon, lat)
});


async function sendToServer(lat, lon) {
  await fetch("https://TON_DOMAINE_NGROK/data", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      lat,
      lon,
      timestamp: Date.now()
    })
  });
}
