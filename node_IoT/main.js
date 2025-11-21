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
