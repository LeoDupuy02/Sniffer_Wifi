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

  fetch("/data", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, timestamp: Date.now() })
  });
});
