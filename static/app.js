const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { user: null, regions: [], events: [], assessments: [], selectedRegion: null, eventId: null, map: null, layers: {}, geographyRequestId: 0 };
const riskColors = { Baja: '#57906c', Moderada: '#c3a04e', Alta: '#d17a43', Crítica: '#b94f4d' };

function icon(name, size = 16) {
  return `<i data-lucide="${name}" width="${size}" height="${size}"></i>`;
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'No se pudo completar la solicitud.');
  return data;
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('visible');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove('visible'), 2800);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
}

function setAuthMode(mode) {
  const isRegister = mode === 'register';
  $$('.auth-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.auth === mode));
  $$('.register-only').forEach((field) => {
    field.classList.toggle('hidden', !isRegister);
    field.querySelector('input').required = isRegister;
  });
  $('#auth-submit-label').textContent = isRegister ? 'Crear cuenta y entrar' : 'Entrar al observatorio';
  $('#auth-form input[name="password"]').autocomplete = isRegister ? 'new-password' : 'current-password';
  $('#auth-error').textContent = '';
}

async function loadApplication(user) {
  state.user = user;
  $('#auth-screen').classList.add('hidden');
  $('#app-shell').classList.remove('hidden');
  $('#profile-name').textContent = user.username;
  $('#profile-role').textContent = user.role === 'admin' ? 'Administración' : 'Analista';
  $('#avatar-initial').textContent = user.username.slice(0, 1).toUpperCase();
  $$('.admin-only').forEach((item) => item.classList.toggle('hidden', user.role !== 'admin'));
  $('#today-label').textContent = new Intl.DateTimeFormat('es-AR', { dateStyle: 'medium' }).format(new Date());
  if (!state.map) initializeMap();
  await Promise.all([loadRegions(), loadEvents(), loadAssessments(), loadDashboard(), loadProfile(), loadLayerCatalog()]);
  if (user.role === 'admin') await loadUsers();
  setView('dashboard');
  refreshIcons();
  setTimeout(() => state.map.invalidateSize(), 50);
}

function initializeMap() {
  state.map = L.map('map', { zoomControl: false, scrollWheelZoom: true, minZoom: 2 }).setView([18, 0], 2.2);
  L.control.zoom({ position: 'bottomright' }).addTo(state.map);
  const ignAttribution = '&copy; <a href="https://www.ign.gob.ar/NuestrasActividades/InformacionGeoespacial/ServiciosOGC" target="_blank" rel="noreferrer">Instituto Geográfico Nacional (IGN)</a>';
  state.baseLayers = {
    osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>' }),
    esri: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noreferrer">Esri</a>, Maxar, Earthstar Geographics, and the GIS User Community' }),
    topo: L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { maxZoom: 17, attribution: 'Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; <a href="https://opentopomap.org/" target="_blank" rel="noreferrer">OpenTopoMap</a> (CC-BY-SA)' }),
    'ign-topo': L.tileLayer('https://wms.ign.gob.ar/geoserver/gwc/service/tms/1.0.0/mapabase_topo@EPSG%3A3857@png/{z}/{x}/{-y}.png', { maxZoom: 18, attribution: ignAttribution })
  };
  state.baseLayers.osm.addTo(state.map);
  state.layers.nasa = L.tileLayer.wms('https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi', {
    layers: 'MODIS_Terra_CorrectedReflectance_TrueColor',
    format: 'image/jpeg',
    transparent: false,
    version: '1.1.1',
    attribution: '&copy; <a href="https://www.earthdata.nasa.gov/data/tools/gibs" target="_blank" rel="noreferrer">NASA EOSDIS GIBS</a>'
  });
  state.layers.conae = L.tileLayer.wms('https://geoportal.conae.gov.ar/geoserver/ows', {
    layers: 'saocom_mosaicos:BuenosAires_2022',
    format: 'image/png',
    transparent: true,
    version: '1.1.1',
    opacity: 0.72,
    attribution: '&copy; <a href="https://www.argentina.gob.ar/ciencia/conae/productos-saocom" target="_blank" rel="noreferrer">CONAE · SAOCOM Buenos Aires 2022</a>'
  });
  const elevationPane = state.map.createPane('elevationPane');
  elevationPane.style.zIndex = '250';
  elevationPane.style.pointerEvents = 'none';
  state.layers.elevation = createElevationLayer();
  state.layers.risk = L.geoJSON(null, {
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
      radius: 11 + feature.properties.score / 13,
      color: '#ffffff', weight: 2, fillColor: riskColors[feature.properties.category], fillOpacity: 0.78
    }),
    onEachFeature: (feature, layer) => {
      layer.bindTooltip(`${feature.properties.name} · ${feature.properties.score}/100 · ${feature.properties.category}`);
      layer.on('click', () => selectRegion(feature.properties.id));
    }
  }).addTo(state.map);
  state.layers.events = L.layerGroup().addTo(state.map);
  state.layers.flood = L.geoJSON(null, {
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
      radius: 7 + feature.properties.exposure * 7, color: '#ffffff', weight: 1.5,
      fillColor: feature.properties.exposure >= 0.7 ? '#d17a43' : '#e1bd60', fillOpacity: 0.82
    }),
    onEachFeature: (feature, layer) => layer.bindTooltip(`${feature.properties.name} · exposición a inundación ${feature.properties.percent}% · DEMO`)
  });
  state.layers.erosion = L.geoJSON(null, {
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
      radius: 7 + feature.properties.exposure * 7, color: '#ffffff', weight: 1.5,
      fillColor: feature.properties.exposure >= 0.65 ? '#b94f4d' : '#c28a36', fillOpacity: 0.82
    }),
    onEachFeature: (feature, layer) => layer.bindTooltip(`${feature.properties.name} · exposición a erosión ${feature.properties.percent}% · DEMO`)
  });
  state.measureLayer = L.featureGroup().addTo(state.map);
  L.control.scale({ metric: true, imperial: true, position: 'bottomleft' }).addTo(state.map);
  $('#layer-risk').addEventListener('change', (event) => toggleLayer('risk', event.target.checked));
  $('#layer-events').addEventListener('change', (event) => toggleLayer('events', event.target.checked));
  $('#layer-flood').addEventListener('change', (event) => toggleLayer('flood', event.target.checked));
  $('#layer-erosion').addEventListener('change', (event) => toggleLayer('erosion', event.target.checked));
  $('#layer-elevation').addEventListener('change', (event) => {
    toggleLayer('elevation', event.target.checked);
    $('#elevation-legend').classList.toggle('hidden', !event.target.checked);
  });
  $('#layer-nasa').addEventListener('change', (event) => toggleLayer('nasa', event.target.checked));
  $('#layer-conae').addEventListener('change', (event) => toggleLayer('conae', event.target.checked));
  $('#base-map-select').addEventListener('change', (event) => {
    Object.values(state.baseLayers).forEach((layer) => state.map.removeLayer(layer));
    state.baseLayers[event.target.value].addTo(state.map);
    const baseNames = { osm: 'OpenStreetMap', esri: 'Esri satélite', topo: 'OpenTopoMap', 'ign-topo': 'IGN topográfico (Argentina)' };
    const baseName = baseNames[event.target.value];
    $('#map-credit').firstChild.textContent = `Base: ${baseName} · `;
  });
  $('#world-button').addEventListener('click', () => state.map.setView([18, 0], 2.2));
  $('#locate-button').addEventListener('click', locateUser);
  $('#place-search').addEventListener('submit', searchPlaces);
  $('#place-results').addEventListener('click', (event) => {
    const result = event.target.closest('[data-lat]');
    if (result) showPlace(Number(result.dataset.lat), Number(result.dataset.lon), result.dataset.label);
  });
  $('#measure-distance-button').addEventListener('click', () => startMeasurement('distance'));
  $('#measure-area-button').addEventListener('click', () => startMeasurement('area'));
  $('#measure-finish-button').addEventListener('click', finishMeasurement);
  $('#measure-clear-button').addEventListener('click', clearMeasurement);
  $('#map-fullscreen-button').addEventListener('click', toggleMapFullscreen);
  $('#copy-coordinates').addEventListener('click', copyCoordinates);
  $('#analysis-form').addEventListener('submit', runSelectedAnalysis);
  $('#history-filter-button').addEventListener('click', loadAssessments);
  $('#profile-form').addEventListener('submit', saveProfile);
  $('#add-user-button').addEventListener('click', () => $('#user-form').classList.remove('hidden'));
  $('#cancel-user-button').addEventListener('click', () => $('#user-form').classList.add('hidden'));
  $('#user-form').addEventListener('submit', createManagedUser);
  $('#users-table').addEventListener('click', handleUserAction);
  $('#history-list').addEventListener('click', handleAssessmentAction);
  $('#reports-list').addEventListener('click', handleAssessmentAction);
  $('#compare-analyses-button').addEventListener('click', compareSelectedAnalyses);
  $$('[data-open-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.openView)));
  state.map.on('click', handleMapClick);
  state.map.on('dblclick', finishMeasurement);
  state.map.on('zoomend', renderEventMarkers);
}

function toggleLayer(name, enabled) {
  if (enabled) state.layers[name].addTo(state.map);
  else state.map.removeLayer(state.layers[name]);
}

function elevationColor(elevation) {
  const stops = [
    [-11000, [8, 42, 105]], [-6000, [27, 105, 166]], [-1000, [78, 162, 190]],
    [0, [175, 215, 195]], [200, [164, 198, 125]], [1000, [224, 207, 130]],
    [2500, [174, 117, 70]], [4500, [230, 226, 211]], [8900, [255, 255, 255]]
  ];
  const upperIndex = stops.findIndex(([height]) => height >= elevation);
  if (upperIndex <= 0) return stops[0][1];
  if (upperIndex === -1) return stops[stops.length - 1][1];
  const [lowerHeight, lowerColor] = stops[upperIndex - 1];
  const [upperHeight, upperColor] = stops[upperIndex];
  const ratio = (elevation - lowerHeight) / (upperHeight - lowerHeight);
  return lowerColor.map((channel, index) => Math.round(channel + (upperColor[index] - channel) * ratio));
}

function createElevationLayer() {
  const layer = L.gridLayer({
    tileSize: 256,
    maxZoom: 15,
    attribution: 'Elevation tiles: <a href="https://registry.opendata.aws/terrain-tiles/" target="_blank" rel="noreferrer">AWS Terrain Tiles / Mapzen</a>, USGS, NOAA and data contributors',
    pane: 'elevationPane'
  });
  layer.createTile = function createElevationTile(coords, done) {
    const tile = document.createElement('canvas');
    const size = this.getTileSize();
    tile.width = size.x;
    tile.height = size.y;
    const context = tile.getContext('2d', { willReadFrequently: true });
    const worldTiles = 2 ** coords.z;
    if (coords.y < 0 || coords.y >= worldTiles) {
      done(null, tile);
      return tile;
    }
    const wrappedX = ((coords.x % worldTiles) + worldTiles) % worldTiles;
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => {
      try {
        context.drawImage(image, 0, 0, size.x, size.y);
        const pixels = context.getImageData(0, 0, size.x, size.y);
        for (let offset = 0; offset < pixels.data.length; offset += 4) {
          if (pixels.data[offset + 3] === 0) continue;
          const height = pixels.data[offset] * 256 + pixels.data[offset + 1] + pixels.data[offset + 2] / 256 - 32768;
          const [red, green, blue] = elevationColor(height);
          pixels.data[offset] = red;
          pixels.data[offset + 1] = green;
          pixels.data[offset + 2] = blue;
          pixels.data[offset + 3] = 184;
        }
        context.putImageData(pixels, 0, 0);
        done(null, tile);
      } catch (error) {
        done(error, tile);
      }
    };
    image.onerror = (error) => done(error, tile);
    image.src = `https://elevation-tiles-prod.s3.amazonaws.com/terrarium/${coords.z}/${wrappedX}/${coords.y}.png`;
    return tile;
  };
  return layer;
}

async function searchPlaces(event) {
  event.preventDefault();
  const query = $('#place-query').value.trim();
  if (!query) return;
  const results = $('#place-results');
  results.classList.remove('hidden');
  results.innerHTML = '<div class="place-result-message">Buscando lugares…</div>';
  try {
    const response = await fetch(`https://photon.komoot.io/api/?q=${encodeURIComponent(query)}&limit=6`);
    if (!response.ok) throw new Error('El buscador no respondió.');
    const data = await response.json();
    const places = data.features || [];
    if (!places.length) {
      results.innerHTML = '<div class="place-result-message">No se encontraron lugares.</div>';
      return;
    }
    results.innerHTML = places.map((feature) => {
      const properties = feature.properties;
      const label = [properties.name, properties.city, properties.state, properties.country].filter((value, index, list) => value && list.indexOf(value) === index).join(', ');
      const [longitude, latitude] = feature.geometry.coordinates;
      return `<button type="button" class="place-result" data-lat="${latitude}" data-lon="${longitude}" data-label="${escapeHtml(label)}"><strong>${escapeHtml(properties.name || label)}</strong><small>${escapeHtml([properties.city, properties.state, properties.country].filter((value, index, list) => value && list.indexOf(value) === index).join(' · '))}</small></button>`;
    }).join('');
  } catch (error) {
    results.innerHTML = `<div class="place-result-message">${escapeHtml(error.message)} Revisá tu conexión e intentá de nuevo.</div>`;
  }
}

function showPlace(latitude, longitude, label) {
  state.map.flyTo([latitude, longitude], 12, { duration: 0.8 });
  $('#place-query').value = label;
  $('#place-results').classList.add('hidden');
  L.popup().setLatLng([latitude, longitude]).setContent(`<strong>${escapeHtml(label)}</strong><br>${latitude.toFixed(5)}, ${longitude.toFixed(5)}`).openOn(state.map);
  updateCoordinateLabel(latitude, longitude);
  loadGeography(latitude, longitude);
}

function locateUser() {
  if (!navigator.geolocation) return toast('Este navegador no ofrece geolocalización.');
  navigator.geolocation.getCurrentPosition(({ coords }) => {
    showPlace(coords.latitude, coords.longitude, 'Mi ubicación');
    state.map.setZoom(14);
  }, () => toast('No se pudo acceder a tu ubicación.'), { enableHighAccuracy: true, timeout: 10000 });
}

function updateCoordinateLabel(latitude, longitude) {
  state.lastCoordinates = `${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
  $('#map-coordinate-text').textContent = `${latitude.toFixed(5)}°, ${longitude.toFixed(5)}°`;
  $('#copy-coordinates').disabled = false;
}

async function loadGeography(latitude, longitude) {
  const requestId = ++state.geographyRequestId;
  $('#geography-name').textContent = 'Consultando el punto…';
  $('#geography-details').className = 'region-details empty-state';
  $('#geography-details').textContent = `${latitude.toFixed(5)}°, ${longitude.toFixed(5)}°`;
  try {
    const response = await api(`/api/geography?latitude=${encodeURIComponent(latitude)}&longitude=${encodeURIComponent(longitude)}`);
    if (requestId !== state.geographyRequestId) return;
    const point = response.geography;
    const location = [point.place, point.country].filter(Boolean).join(' · ');
    const latitudeDirection = latitude < 0 ? 'S' : 'N';
    const longitudeDirection = longitude < 0 ? 'O' : 'E';
    const fields = [
      ['Latitud', `${Math.abs(latitude).toFixed(5)}° ${latitudeDirection}`],
      ['Longitud', `${Math.abs(longitude).toFixed(5)}° ${longitudeDirection}`],
      ['Altura s. n. m.', point.elevation_m == null ? 'Sin dato' : `${Math.round(point.elevation_m)} m`],
      ['Temperatura del aire', point.temperature_c == null ? 'Sin dato' : `${point.temperature_c} °C`],
      ['Temperatura del mar · modelo', point.sea_surface_temperature_c == null ? 'Sin dato' : `${point.sea_surface_temperature_c} °C`],
      ['Humedad relativa', point.relative_humidity_pct == null ? 'Sin dato' : `${point.relative_humidity_pct}%`],
      ['Viento a 10 m', point.wind_speed_kmh == null ? 'Sin dato' : `${point.wind_speed_kmh} km/h`],
      ['Océano / mar', point.water_body || 'No identificado']
    ];
    $('#geography-name').textContent = location || point.water_body || 'Punto consultado';
    $('#geography-details').className = 'region-details';
    $('#geography-details').innerHTML = `<div class="indicator-list">${fields.map(([label, value]) => `<div class="indicator-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}</div><small class="geography-meta">Modelo actual${point.time ? ` · ${escapeHtml(point.time)}${point.timezone ? ` · ${escapeHtml(point.timezone)}` : ''}` : ''}. Elevación Copernicus GLO-90; meteorología y malla marina Open-Meteo; topónimos OpenStreetMap / BigDataCloud.</small>`;
  } catch (error) {
    if (requestId !== state.geographyRequestId) return;
    $('#geography-name').textContent = 'Datos geográficos no disponibles';
    $('#geography-details').className = 'region-details empty-state';
    $('#geography-details').textContent = error.message;
  }
}

async function copyCoordinates() {
  if (!state.lastCoordinates) return;
  try {
    await navigator.clipboard.writeText(state.lastCoordinates);
    toast('Coordenadas copiadas.');
  } catch {
    toast(`Coordenadas: ${state.lastCoordinates}`);
  }
}

function handleMapClick(event) {
  const { lat, lng } = event.latlng;
  updateCoordinateLabel(lat, lng);
  loadGeography(lat, lng);
  if (state.measureMode) {
    state.measurePoints.push(event.latlng);
    renderMeasurement();
    if (state.measureMode === 'distance' && state.measurePoints.length === 2) finishMeasurement();
    else if (state.measureMode === 'area' && state.measurePoints.length >= 3) {
      $('#map-coordinate-text').textContent = `Superficie ${formatArea(geodesicArea(state.measurePoints))} · doble clic para terminar`;
    }
    return;
  }
  if (typeof openSeismicPopup === 'function') openSeismicPopup(event.latlng);
  else L.popup().setLatLng(event.latlng).setContent(`Coordenadas<br><strong>${lat.toFixed(5)}°, ${lng.toFixed(5)}°</strong>`).openOn(state.map);
}

function startMeasurement(mode) {
  clearMeasurement();
  state.measureMode = mode;
  state.measurePoints = [];
  state.map.doubleClickZoom.disable();
  $('#map-coordinate-text').textContent = mode === 'distance' ? 'Medir distancia: elegí dos puntos.' : 'Medir superficie: marcá el perímetro y hacé doble clic para terminar.';
}

function renderMeasurement() {
  state.measureLayer.clearLayers();
  state.measurePoints.forEach((point) => L.circleMarker(point, { radius: 4, color: '#ffffff', weight: 2, fillColor: '#bd584c', fillOpacity: 1 }).addTo(state.measureLayer));
  if (state.measureMode === 'distance' && state.measurePoints.length > 1) {
    L.polyline(state.measurePoints, { color: '#bd584c', weight: 3, dashArray: '6 5' }).addTo(state.measureLayer);
  }
  if (state.measureMode === 'area' && state.measurePoints.length > 1) {
    L.polygon(state.measurePoints, { color: '#bd584c', weight: 2, fillOpacity: 0.16 }).addTo(state.measureLayer);
  }
}

function finishMeasurement() {
  if (!state.measureMode) return;
  if (state.measureMode === 'distance' && state.measurePoints.length === 2) {
    const distance = state.measurePoints[0].distanceTo(state.measurePoints[1]);
    $('#map-coordinate-text').textContent = `Distancia: ${distance >= 1000 ? `${(distance / 1000).toFixed(2)} km` : `${Math.round(distance)} m`}`;
  } else if (state.measureMode === 'area' && state.measurePoints.length >= 3) {
    $('#map-coordinate-text').textContent = `Superficie: ${formatArea(geodesicArea(state.measurePoints))}`;
  } else if (state.measureMode === 'area') {
    return;
  } else {
    $('#map-coordinate-text').textContent = 'Marcá dos puntos para medir la distancia.';
    return;
  }
  state.measureMode = null;
  state.map.doubleClickZoom.enable();
}

function clearMeasurement() {
  if (state.measureLayer) state.measureLayer.clearLayers();
  if (state.map) state.map.doubleClickZoom.enable();
  state.measureMode = null;
  state.measurePoints = [];
}

function geodesicArea(points) {
  const earthRadius = 6371008.8;
  let total = 0;
  for (let index = 0; index < points.length; index += 1) {
    const current = points[index];
    const next = points[(index + 1) % points.length];
    total += (next.lng - current.lng) * Math.PI / 180
      * (2 + Math.sin(current.lat * Math.PI / 180) + Math.sin(next.lat * Math.PI / 180));
  }
  return Math.abs(total * earthRadius * earthRadius / 2);
}

function formatArea(squareMeters) {
  return squareMeters >= 1000000 ? `${(squareMeters / 1000000).toFixed(2)} km²` : `${Math.round(squareMeters)} m²`;
}

function toggleMapFullscreen() {
  const frame = $('.map-frame');
  frame.classList.toggle('map-expanded');
  document.body.classList.toggle('map-fullscreen-open', frame.classList.contains('map-expanded'));
  $('#map-fullscreen-button').setAttribute('aria-pressed', String(frame.classList.contains('map-expanded')));
  $('#map-fullscreen-button').title = frame.classList.contains('map-expanded') ? 'Salir de pantalla completa' : 'Pantalla completa';
  setTimeout(() => state.map.invalidateSize(), 80);
}

function regionFeatures(property) {
  return {
    type: 'FeatureCollection',
    features: state.regions.map((region) => ({
      type: 'Feature',
      properties: property(region),
      geometry: { type: 'Point', coordinates: [region.longitude, region.latitude] }
    }))
  };
}

async function loadRegions() {
  const data = await api('/api/regions');
  state.regions = data.regions;
  const options = state.regions.map((region) => `<option value="${region.id}">${escapeHtml(region.name)}</option>`).join('');
  $('#region-select').innerHTML = '<option value="">Seleccionar región</option>' + options;
  $('#analysis-region').innerHTML = '<option value="">Seleccionar región</option>' + options;
  $('#event-region').innerHTML = options;
  $('#event-region-filter').innerHTML = '<option value="">Todas las regiones</option>' + options;
  $('#metric-regions').textContent = String(state.regions.length).padStart(2, '0');
  const highest = [...state.regions].sort((a, b) => b.score - a.score)[0];
  $('#metric-highest').textContent = highest ? `${highest.score}/100` : '—';
  $('#metric-highest-note').textContent = highest ? `${highest.name} · ${highest.category}` : 'Índice compuesto local';
  state.layers.risk.clearLayers().addData(regionFeatures((region) => ({ id: region.id, name: region.name, score: region.score, category: region.category })));
  state.layers.flood.clearLayers().addData(regionFeatures((region) => ({ name: region.name, exposure: region.flood_exposure, percent: Math.round(region.flood_exposure * 100) })));
  state.layers.erosion.clearLayers().addData(regionFeatures((region) => ({ name: region.name, exposure: region.erosion_exposure, percent: Math.round(region.erosion_exposure * 100) })));
  renderRegionTable();
  renderEventMarkers();
  $('#region-select').onchange = (event) => selectRegion(Number(event.target.value) || null, true);
  $('#event-region-filter').onchange = renderEvents;
}

function selectRegion(regionId, zoom = false) {
  const region = state.regions.find((item) => item.id === regionId);
  state.selectedRegion = region || null;
  $('#assess-button').disabled = !region;
  $('#risk-result').classList.add('hidden');
  if (!region) {
    $('#region-name').textContent = 'Seleccioná una región';
    $('#region-details').className = 'region-details empty-state';
    $('#region-details').textContent = 'Elegí una región en el mapa o en el selector para consultar sus indicadores.';
    $('#region-select').value = '';
    return;
  }
  $('#region-select').value = String(region.id);
  $('#region-name').textContent = region.name;
  $('#region-details').className = 'region-details';
  const badgeClass = region.category === 'Crítica' ? 'critical' : region.category === 'Alta' ? 'high' : '';
  const floodPercent = Math.round(region.flood_exposure * 100);
  const erosionPercent = Math.round(region.erosion_exposure * 100);
  $('#region-details').innerHTML = `
    <div class="region-location">${escapeHtml(region.province)} · ${region.latitude.toFixed(2)}°, ${Math.abs(region.longitude).toFixed(2)}° O</div>
    <div class="risk-score"><strong>${region.score}</strong><span>/ 100</span><span class="risk-badge ${badgeClass}">${escapeHtml(region.category)}</span></div>
    <div class="indicator-list">
      <div class="indicator-row"><span>Elevación · demo</span><strong>${region.elevation_m} m</strong></div>
      <div class="indicator-row"><span>Pendiente · demo</span><strong>${region.slope_pct}%</strong></div>
      <div class="indicator-row"><span>Lluvia · demo</span><strong>${region.annual_rainfall_mm} mm/año</strong></div>
      <div class="indicator-row"><span>Exposición a inundación</span><strong>${floodPercent}%</strong></div>
      <div class="indicator-row"><span>Exposición a erosión</span><strong>${erosionPercent}%</strong></div>
      <div class="indicator-row"><span>Eventos registrados</span><strong>${region.event_count}</strong></div>
    </div>`;
  if (zoom) state.map.flyTo([region.latitude, region.longitude], 7, { duration: 0.8 });
  renderRegionTable();
  loadGeography(region.latitude, region.longitude);
}

function renderRegionTable() {
  $('#region-table').innerHTML = state.regions.map((region) => {
    const categoryClass = region.category === 'Crítica' ? 'critical' : region.category === 'Alta' ? 'high' : '';
    return `<tr data-region="${region.id}"><td class="table-region">${escapeHtml(region.name)}</td><td>${region.elevation_m} m</td><td>${region.slope_pct}%</td><td>${Math.round(region.flood_exposure * 100)}%</td><td>${Math.round(region.erosion_exposure * 100)}%</td><td><span class="table-category ${categoryClass}">${region.score} · ${escapeHtml(region.category)}</span></td></tr>`;
  }).join('');
  $$('#region-table tr').forEach((row) => row.addEventListener('click', () => selectRegion(Number(row.dataset.region), true)));
}

async function loadEvents() {
  const data = await api('/api/events');
  state.events = data.events;
  $('#event-count').textContent = String(state.events.length);
  $('#metric-events').textContent = String(state.events.length).padStart(2, '0');
  renderEvents();
  renderEventMarkers();
}

function renderEventMarkers() {
  state.layers.events.clearLayers();
  const locatedEvents = state.events.map((event) => {
    const region = state.regions.find((item) => item.id === event.region_id);
    return {
      ...event,
      latitude: event.latitude ?? region?.latitude,
      longitude: event.longitude ?? region?.longitude
    };
  }).filter((event) => Number.isFinite(event.latitude) && Number.isFinite(event.longitude));
  const clusters = [];
  locatedEvents.forEach((event) => {
    const point = state.map.latLngToLayerPoint([event.latitude, event.longitude]);
    let cluster = clusters.find((item) => point.distanceTo(item.point) < 22);
    if (!cluster) {
      cluster = { point, events: [], latitude: event.latitude, longitude: event.longitude };
      clusters.push(cluster);
    }
    cluster.events.push(event);
    cluster.latitude = cluster.events.reduce((sum, item) => sum + item.latitude, 0) / cluster.events.length;
    cluster.longitude = cluster.events.reduce((sum, item) => sum + item.longitude, 0) / cluster.events.length;
    cluster.point = state.map.latLngToLayerPoint([cluster.latitude, cluster.longitude]);
  });
  clusters.forEach((cluster) => {
    const marker = cluster.events.length > 1
      ? L.marker([cluster.latitude, cluster.longitude], {
        icon: L.divIcon({ className: 'event-cluster-icon', html: `<span class="event-cluster">${cluster.events.length}</span>`, iconSize: [28, 28], iconAnchor: [14, 14] })
      })
      : L.circleMarker([cluster.latitude, cluster.longitude], { radius: 8, color: '#ffffff', weight: 2, fillColor: '#276f9d', fillOpacity: 1 });
    const popup = document.createElement('div');
    cluster.events.forEach((event) => {
      const entry = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = event.event_type;
      const detail = document.createElement('div');
      detail.textContent = `${event.region_name} · ${event.occurred_on} · severidad ${event.severity}/5`;
      entry.append(title, detail);
      popup.append(entry);
    });
    marker.bindPopup(popup).addTo(state.layers.events);
  });
}

function renderEvents() {
  const query = ($('#event-search')?.value || '').trim().toLocaleLowerCase('es');
  const regionFilter = $('#event-region-filter')?.value || '';
  const visible = state.events.filter((event) => {
    const text = `${event.region_name} ${event.event_type} ${event.description}`.toLocaleLowerCase('es');
    return text.includes(query) && (!regionFilter || String(event.region_id) === regionFilter);
  });
  if (!visible.length) {
    $('#events-list').innerHTML = '<div class="empty-list">Todavía no hay eventos que coincidan. Registrá una observación para empezar.</div>';
    return;
  }
  $('#events-list').innerHTML = visible.map((event) => `
    <article class="event-item"><div class="event-icon">${icon(event.event_type === 'Inundación' ? 'waves' : event.event_type === 'Incendio' ? 'flame' : 'cloud-lightning')}</div>
      <div class="event-copy"><h3>${escapeHtml(event.title || event.event_type)}<span class="severity-pill">Impacto ${event.impact || event.severity}/5</span></h3><p>${escapeHtml(event.description)}</p><div class="event-meta">${escapeHtml(event.region_name)} · ${escapeHtml(event.occurred_on)} · ${escapeHtml(event.source || 'Fuente no especificada')} · registrado por ${escapeHtml(event.created_by_name)}</div>${event.photo_path ? `<a href="${escapeHtml(event.photo_path)}" target="_blank" rel="noreferrer">Ver foto adjunta</a>` : ''}</div>
      <div class="event-actions"><button class="icon-button edit-event" data-id="${event.id}" title="Editar evento" aria-label="Editar evento">${icon('pencil')}</button><button class="icon-button delete-event" data-id="${event.id}" title="Eliminar evento" aria-label="Eliminar evento">${icon('trash-2')}</button></div></article>`).join('');
  refreshIcons();
  $$('.edit-event').forEach((button) => button.addEventListener('click', () => openEventDialog(Number(button.dataset.id))));
  $$('.delete-event').forEach((button) => button.addEventListener('click', () => deleteEvent(Number(button.dataset.id))));
}

async function loadAssessments() {
  const query = new URLSearchParams({ page: '1', per_page: '100' });
  const category = $('#history-category')?.value;
  if (category) query.set('category', category);
  const data = await api(`/api/assessments?${query}`);
  state.assessments = data.assessments;
  renderAssessments();
}

function renderAssessments() {
  const html = state.assessments.length ? state.assessments.map((item) => `
    <article class="report-item"><label class="compare-choice"><input type="checkbox" value="${item.id}" class="compare-analysis"><span>Comparar</span></label><div class="report-score">${item.score}<small style="font:10px var(--font-mono);color:#87928b">/100</small></div><div class="report-copy"><h3>${escapeHtml(item.region_name)} · ${escapeHtml(item.category)}</h3><p>${escapeHtml(item.report)}</p><div class="event-meta">${escapeHtml(new Date(item.created_at).toLocaleString('es-AR'))} · ${escapeHtml(item.created_by_name)} · ${escapeHtml(item.model_source)}</div></div><div class="report-actions"><button class="button button-small" data-analysis-id="${item.id}">Detalle</button><a class="button button-small" href="/api/assessments/${item.id}/pdf" target="_blank" rel="noreferrer">PDF</a></div></article>`).join('') : '<div class="empty-list">Todavía no hay evaluaciones guardadas.</div>';
  $('#history-list').innerHTML = html;
  $('#reports-list').innerHTML = html;
}

async function loadDashboard() {
  const data = await api('/api/dashboard');
  $('#dashboard-regions').textContent = String(data.region_count);
  $('#dashboard-events').textContent = String(data.event_count);
  $('#dashboard-assessments').textContent = String(data.assessment_count);
  const [highestScore, highestName, highestCategory] = data.highest_risk;
  $('#dashboard-highest').textContent = `${highestScore}/100`;
  $('#dashboard-highest-note').textContent = `${highestName} · ${highestCategory} · demo`;
  const labels = { Baja: 'Baja', Moderada: 'Media', Alta: 'Alta', Crítica: 'Muy alta' };
  const colors = { Baja: '#57906c', Moderada: '#c3a04e', Alta: '#d17a43', Crítica: '#b94f4d' };
  const maxCount = Math.max(1, ...Object.values(data.risk_distribution));
  $('#risk-chart').innerHTML = Object.entries(data.risk_distribution).map(([category, count]) => `<div class="risk-chart-row"><span>${labels[category]}</span><div class="risk-chart-track"><i style="width:${Math.max(count ? 6 : 0, count / maxCount * 100)}%;background:${colors[category]}"></i></div><strong>${count}</strong></div>`).join('');
  $('#dashboard-latest-analyses').innerHTML = data.latest_analyses.length ? data.latest_analyses.map((item) => `<article class="compact-item"><strong>${escapeHtml(item.region_name)} · ${item.score}/100</strong><small>${escapeHtml(item.category)} · ${escapeHtml(new Date(item.created_at).toLocaleDateString('es-AR'))}</small></article>`).join('') : '<div class="compact-empty">Todavía no hay análisis.</div>';
  $('#dashboard-latest-events').innerHTML = data.latest_events.length ? data.latest_events.map((item) => `<article class="compact-item"><strong>${escapeHtml(item.title || item.event_type)} · ${escapeHtml(item.region_name)}</strong><small>${escapeHtml(item.occurred_on)} · impacto ${item.impact}/5</small></article>`).join('') : '<div class="compact-empty">Todavía no hay eventos.</div>';
}

async function loadProfile() {
  const data = await api('/api/profile');
  $('#profile-username').value = data.user.username;
  $('#profile-full-name').value = data.user.full_name || '';
  $('#profile-email').value = data.user.email || '';
}

async function saveProfile(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  $('#profile-error').textContent = '';
  try {
    await api('/api/profile', { method: 'PUT', body: JSON.stringify(payload) });
    $('#profile-new-password').value = '';
    toast('Perfil actualizado.');
  } catch (error) {
    $('#profile-error').textContent = error.message;
  }
}

async function loadLayerCatalog() {
  const data = await api('/api/layers');
  $('#layer-catalog').innerHTML = data.layers.map((layer) => `<article class="compact-item"><strong>${escapeHtml(layer.name)}${layer.is_demo ? ' · DEMO' : ''}</strong><small>${escapeHtml(layer.layer_type)} · ${escapeHtml(layer.source)}${layer.data_date ? ` · ${escapeHtml(layer.data_date)}` : ''}</small><p>${escapeHtml(layer.description)}</p></article>`).join('');
}

async function loadUsers() {
  const data = await api('/api/users');
  $('#users-table').innerHTML = data.users.map((user) => `<tr><td>${escapeHtml(user.username)}</td><td>${escapeHtml(user.full_name || '—')}</td><td>${escapeHtml(user.email || '—')}</td><td><select class="user-role-select" data-id="${user.id}" aria-label="Rol de ${escapeHtml(user.username)}"><option value="analyst" ${user.role === 'analyst' ? 'selected' : ''}>Usuario</option><option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Administrador</option></select></td><td>${escapeHtml(new Date(user.created_at).toLocaleDateString('es-AR'))}</td><td><button class="icon-button delete-user" data-id="${user.id}" title="Eliminar usuario" aria-label="Eliminar ${escapeHtml(user.username)}">${icon('trash-2')}</button></td></tr>`).join('');
  refreshIcons();
}

async function createManagedUser(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    await api('/api/users', { method: 'POST', body: JSON.stringify(payload) });
    event.currentTarget.reset();
    $('#user-form').classList.add('hidden');
    await loadUsers();
    toast('Usuario creado.');
  } catch (error) {
    $('#user-error').textContent = error.message;
  }
}

async function handleUserAction(event) {
  const roleSelect = event.target.closest('.user-role-select');
  const deleteButton = event.target.closest('.delete-user');
  try {
    if (roleSelect) {
      await api(`/api/users/${roleSelect.dataset.id}`, { method: 'PUT', body: JSON.stringify({ role: roleSelect.value }) });
      toast('Permisos actualizados.');
    } else if (deleteButton && window.confirm('¿Eliminar este usuario y sus registros asociados?')) {
      await api(`/api/users/${deleteButton.dataset.id}`, { method: 'DELETE' });
      await Promise.all([loadUsers(), loadDashboard()]);
      toast('Usuario eliminado.');
    }
  } catch (error) {
    toast(error.message);
    await loadUsers();
  }
}

async function handleAssessmentAction(event) {
  const button = event.target.closest('[data-analysis-id]');
  if (!button) return;
  try {
    const data = await api(`/api/assessments/${button.dataset.analysisId}`);
    const item = data.assessment;
    $('#analysis-detail-title').textContent = `${item.region_name} · ${item.category} · ${item.score}/100`;
    $('#analysis-detail-body').innerHTML = `<p>${escapeHtml(item.report)}</p><p><strong>Riesgos:</strong> ${escapeHtml(item.risks.join(', '))}</p><p><strong>Ubicación:</strong> ${item.latitude}, ${item.longitude}</p><p><strong>Responsable:</strong> ${escapeHtml(item.created_by_name)} · ${escapeHtml(item.created_at)}</p><a class="button button-green" href="/api/assessments/${item.id}/pdf" target="_blank" rel="noreferrer">Descargar PDF</a>`;
    $('#analysis-detail-dialog').showModal();
  } catch (error) {
    toast(error.message);
  }
}

async function compareSelectedAnalyses() {
  const ids = $$('.compare-analysis:checked').map((input) => input.value);
  if (ids.length !== 2) return toast('Seleccioná exactamente dos análisis.');
  try {
    const data = await api(`/api/assessments/compare?ids=${ids.join(',')}`);
    const [first, second] = data.analyses;
    $('#analysis-detail-title').textContent = `Comparación · ${data.score_delta > 0 ? '+' : ''}${data.score_delta} puntos`;
    $('#analysis-detail-body').innerHTML = data.analyses.map((item) => `<article class="comparison-column"><h3>${escapeHtml(item.region_name)} · ${item.score}/100 · ${escapeHtml(item.category)}</h3><small>${escapeHtml(new Date(item.created_at).toLocaleString('es-AR'))} · ${escapeHtml(item.created_by_name)}</small><p>Riesgos: ${escapeHtml(item.risks.join(', '))}</p><p>${escapeHtml(item.report)}</p><a class="button button-small" href="/api/assessments/${item.id}/pdf" target="_blank" rel="noreferrer">PDF</a></article>`).join('');
    $('#analysis-detail-dialog').showModal();
  } catch (error) {
    toast(error.message);
  }
}

async function runSelectedAnalysis(event) {
  event.preventDefault();
  const button = $('#run-analysis-button');
  const regionId = Number($('#analysis-region').value);
  const risks = $$('input[name="risks"]:checked').map((input) => input.value);
  $('#analysis-error').textContent = '';
  if (!regionId || !risks.length) {
    $('#analysis-error').textContent = 'Elegí una región y al menos un tipo de riesgo.';
    return;
  }
  button.disabled = true;
  button.textContent = 'Analizando…';
  try {
    const result = await api(`/api/assessments/${regionId}`, { method: 'POST', body: JSON.stringify({ risks }) });
    const region = state.regions.find((item) => item.id === regionId);
    const factors = Object.entries(result.risk_factors).map(([name, score]) => `<div class="indicator-row"><span>${escapeHtml(name)}</span><strong>${score}/100</strong></div>`).join('');
    $('#analysis-output').innerHTML = `<div class="analysis-result-heading"><div><div class="eyebrow">${escapeHtml(region?.name || 'Región')}</div><h2>${escapeHtml(result.category)} · ${result.score}/100</h2></div><a class="button button-outline" href="/api/assessments/${result.id}/pdf" target="_blank" rel="noreferrer"><i data-lucide="download"></i> PDF</a></div><div class="indicator-list">${factors}</div><p>${escapeHtml(result.report)}</p><p class="evidence-warning">${escapeHtml(result.data_quality)}. Los datos indicados como demostración no permiten emitir alertas ni afirmar peligro.</p>`;
    $('#analysis-output').classList.remove('hidden');
    refreshIcons();
    await Promise.all([loadAssessments(), loadDashboard()]);
    toast('Análisis guardado en el historial.');
  } catch (error) {
    $('#analysis-error').textContent = error.message;
  } finally {
    button.disabled = false;
    button.innerHTML = `${icon('sparkles')} Ejecutar análisis`;
    refreshIcons();
  }
}

async function submitAssessment() {
  const button = $('#assess-button');
  button.disabled = true;
  button.innerHTML = `${icon('loader-circle')} Analizando…`;
  refreshIcons();
  try {
    const regionId = state.selectedRegion.id;
    const selectedRisks = $$('input[name="risks"]:checked').map((input) => input.value);
    const data = await api(`/api/assessments/${regionId}`, { method: 'POST', body: JSON.stringify({ risks: selectedRisks }) });
    $('#metric-model').textContent = data.model_source.startsWith('Gemini') ? 'Gemini + modelo local' : 'Clasificador local';
    await Promise.all([loadRegions(), loadAssessments()]);
    selectRegion(regionId);
    const factors = Object.entries(data.risk_factors).map(([name, score]) => `<div class="indicator-row"><span>${escapeHtml(name)}</span><strong>${score}/100</strong></div>`).join('');
    $('#risk-result').innerHTML = `<strong>${escapeHtml(data.category)} · ${data.score}/100</strong>${escapeHtml(data.report)}<div class="indicator-list">${factors}</div><small>${escapeHtml(data.data_quality)}. No es una alerta oficial.</small><a class="button button-small" href="/api/assessments/${data.id}/pdf" target="_blank" rel="noreferrer">PDF</a>`;
    $('#risk-result').classList.remove('hidden');
    toast('Evaluación generada y guardada.');
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = !state.selectedRegion;
    button.innerHTML = `${icon('sparkles')} Generar análisis`;
    refreshIcons();
  }
}

function ensureCoordinateFields() {
  if ($('#event-latitude')) return;
  const form = $('#event-form');
  const descriptionLabel = form.elements.description.closest('label');
  const wrapper = document.createElement('div');
  wrapper.className = 'form-grid';
  [['latitude', 'Latitud', '-33.45', '-90', '90'], ['longitude', 'Longitud', '-60.65', '-180', '180']].forEach(([name, label, placeholder, min, max]) => {
    const field = document.createElement('label');
    field.textContent = `${label} (opcional)`;
    const input = document.createElement('input');
    input.id = `event-${name}`;
    input.name = name;
    input.type = 'number';
    input.step = 'any';
    input.min = min;
    input.max = max;
    input.placeholder = placeholder;
    field.append(input);
    wrapper.append(field);
  });
  form.insertBefore(wrapper, descriptionLabel);
}

function openEventDialog(eventId = null) {
  ensureCoordinateFields();
  const form = $('#event-form');
  form.reset();
  state.eventId = eventId;
  $('#event-error').textContent = '';
  form.elements.event_id.value = eventId || '';
  form.elements.occurred_on.value = new Date().toISOString().slice(0, 10);
  $('#event-dialog-title').textContent = eventId ? 'Editar evento' : 'Registrar evento';
  if (eventId) {
    const event = state.events.find((item) => item.id === eventId);
    if (!event) return;
    form.elements.region_id.value = event.region_id;
    form.elements.event_type.value = event.event_type;
    form.elements.title.value = event.title || '';
    form.elements.severity.value = event.severity;
    form.elements.impact.value = event.impact || event.severity;
    form.elements.occurred_on.value = event.occurred_on;
    form.elements.source.value = event.source || '';
    form.elements.photo_url.value = event.photo_path || '';
    form.elements.description.value = event.description;
    form.elements.latitude.value = event.latitude ?? '';
    form.elements.longitude.value = event.longitude ?? '';
  }
  $('#event-dialog').showModal();
}

async function saveEvent(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  delete payload.event_id;
  payload.region_id = Number(payload.region_id);
  payload.severity = Number(payload.severity);
  payload.impact = Number(payload.impact);
  const id = state.eventId;
  try {
    await api(id ? `/api/events/${id}` : '/api/events', { method: id ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    $('#event-dialog').close();
    await Promise.all([loadEvents(), loadRegions()]);
    if (state.selectedRegion) selectRegion(state.selectedRegion.id);
    toast(id ? 'Evento actualizado.' : 'Evento registrado.');
  } catch (error) {
    $('#event-error').textContent = error.message;
  }
}

async function deleteEvent(id) {
  if (!window.confirm('¿Eliminar este evento del registro?')) return;
  try {
    await api(`/api/events/${id}`, { method: 'DELETE' });
    await Promise.all([loadEvents(), loadRegions()]);
    if (state.selectedRegion) selectRegion(state.selectedRegion.id);
    toast('Evento eliminado.');
  } catch (error) {
    toast(error.message);
  }
}

function setView(name) {
  $$('.view-panel').forEach((panel) => panel.classList.toggle('hidden', panel.id !== `${name}-view`));
  $$('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === name));
  const labels = { dashboard: 'Dashboard', map: 'Mapa interactivo', analysis: 'Nuevo análisis', events: 'Eventos ambientales', history: 'Historial', reports: 'Informes', users: 'Usuarios', settings: 'Configuración' };
  $('#breadcrumb-view').textContent = labels[name];
  $('#page-title').textContent = name === 'map' ? 'Visor geográfico mundial' : labels[name];
  if (name === 'map' && state.map) setTimeout(() => state.map.invalidateSize(), 40);
  if (name === 'dashboard') loadDashboard();
  if (name === 'map' && typeof loadSeismicHistory === 'function') loadSeismicHistory();
  if (name === 'history' || name === 'reports') loadAssessments();
  if (name === 'users' && state.user?.role === 'admin') loadUsers();
  if (name === 'settings') Promise.all([loadProfile(), loadLayerCatalog()]);
}

function exportCsv() {
  if (!state.assessments.length) return toast('No hay evaluaciones para exportar.');
  const quote = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;
  const rows = [['Región', 'Puntaje', 'Categoría', 'Motor', 'Usuario', 'Fecha', 'Informe'], ...state.assessments.map((item) => [item.region_name, item.score, item.category, item.model_source, item.created_by_name, item.created_at, item.report])];
  const csv = '\ufeff' + rows.map((row) => row.map(quote).join(';')).join('\r\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  link.download = 'evaluaciones-georiesgo.csv';
  link.click();
  URL.revokeObjectURL(link.href);
}

$$('.auth-tab').forEach((tab) => tab.addEventListener('click', () => setAuthMode(tab.dataset.auth)));
$('#auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const mode = $('.auth-tab.active').dataset.auth;
  const payload = Object.fromEntries(new FormData(form).entries());
  $('#auth-error').textContent = '';
  try {
    const data = await api(mode === 'register' ? '/api/register' : '/api/login', { method: 'POST', body: JSON.stringify(payload) });
    await loadApplication(data.user);
  } catch (error) {
    $('#auth-error').textContent = error.message;
  }
});
$('#logout-button').addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST' }); } catch { /* Continue to clear the local session view. */ }
  state.user = null;
  $('#app-shell').classList.add('hidden');
  $('#auth-screen').classList.remove('hidden');
  setAuthMode('login');
});
$$('.nav-item').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
$('#region-select').addEventListener('change', (event) => selectRegion(Number(event.target.value) || null, true));
$('#assess-button').addEventListener('click', submitAssessment);
$('#add-event-top').addEventListener('click', () => openEventDialog());
$('#add-event-main').addEventListener('click', () => openEventDialog());
$('#event-form').addEventListener('submit', saveEvent);
$('#close-dialog').addEventListener('click', () => $('#event-dialog').close());
$('#cancel-dialog').addEventListener('click', () => $('#event-dialog').close());
$('#event-search').addEventListener('input', renderEvents);
$('#export-reports').addEventListener('click', exportCsv);
$('#close-analysis-detail').addEventListener('click', () => $('#analysis-detail-dialog').close());

(async function start() {
  refreshIcons();
  try {
    const data = await api('/api/session');
    if (data.user) await loadApplication(data.user);
  } catch (error) {
    toast(error.message);
  }
})();
