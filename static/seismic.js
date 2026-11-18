/* Riesgo sísmico por click en el mapa. Depende de app.js (state, api, $, escapeHtml, toast, riskColors). */
state.seismic = { point: null, layer: null, running: false, currentId: null };

const REPORT_TITLES = ['Resumen', 'Sismicidad histórica', 'Magnitud máxima plausible', '¿Es recomendable vivir ahí?', 'Recomendaciones', 'Fuentes y límites'];
const DEPTH_COLORS = { shallow: '#c8463c', intermediate: '#d99a2b', deep: '#3b6ea8' };

function safeUrl(url) {
  return /^https?:\/\//i.test(url || '') ? url : '';
}

function fmtYears(value) {
  if (value == null) return 'sin datos';
  if (value > 10000) return 'más de 10.000 años';
  return `~${Math.round(value).toLocaleString('es-AR')} años`;
}

function fmtEvent(event) {
  if (!event) return 'Sin registros en el radio';
  return `M${event.magnitude} · ${event.year ?? '—'} · a ${Math.round(event.distance_km)} km`;
}

function openSeismicPopup(latlng) {
  state.seismic.point = { latitude: latlng.lat, longitude: latlng.lng };
  $('#seismic-run-button').disabled = false;
  $('#seismic-status').className = 'region-details empty-state';
  $('#seismic-status').textContent = `Punto elegido: ${latlng.lat.toFixed(5)}°, ${latlng.lng.toFixed(5)}°. Presioná “Analizar riesgo sísmico”.`;
  const box = document.createElement('div');
  box.className = 'seismic-popup';
  box.innerHTML = `Coordenadas<br><strong>${latlng.lat.toFixed(5)}°, ${latlng.lng.toFixed(5)}°</strong><br><button type="button" class="button button-green button-small">Analizar riesgo sísmico</button>`;
  box.querySelector('button').addEventListener('click', () => {
    state.map.closePopup();
    runSeismicAnalysis();
  });
  L.popup().setLatLng(latlng).setContent(box).openOn(state.map);
}

function setSeismicBusy(busy, message) {
  state.seismic.running = busy;
  $('#seismic-run-button').disabled = busy || !state.seismic.point;
  const status = $('#seismic-status');
  status.classList.remove('hidden');
  if (message) {
    status.className = busy ? 'region-details seismic-progress' : 'region-details empty-state';
    status.textContent = message;
  }
}

async function runSeismicAnalysis() {
  const point = state.seismic.point;
  if (!point || state.seismic.running) return;
  setSeismicBusy(true, 'Consultando el catálogo sísmico oficial (USGS), calculando la peligrosidad y pidiendo a la IA que investigue el punto… puede tardar hasta un minuto.');
  $('#seismic-result').classList.add('hidden');
  $('#seismic-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  try {
    const data = await api('/api/seismic/analyze', { method: 'POST', body: JSON.stringify(point) });
    showSeismicAnalysis(data.id, data.analysis);
    setSeismicBusy(false);
    $('#seismic-status').classList.add('hidden');
    loadSeismicHistory();
  } catch (error) {
    setSeismicBusy(false, `No se pudo completar el análisis: ${error.message}`);
  }
}

function showSeismicAnalysis(id, analysis) {
  state.seismic.currentId = id;
  drawSeismicLayer(analysis);
  $('#seismic-result').innerHTML = renderSeismicResult(analysis);
  $('#seismic-result').classList.remove('hidden');
  const link = $('#seismic-pdf-link');
  link.href = `/api/seismic/${id}/pdf`;
  link.classList.remove('hidden');
  const name = [analysis.point.place, analysis.point.country].filter(Boolean).join(' · ');
  $('#seismic-title').textContent = name || `${analysis.point.latitude}°, ${analysis.point.longitude}°`;
  if (typeof refreshIcons === 'function') refreshIcons();
}

function drawSeismicLayer(analysis) {
  if (state.seismic.layer) state.map.removeLayer(state.seismic.layer);
  const { latitude, longitude } = analysis.point;
  const group = L.layerGroup();
  const radius = L.circle([latitude, longitude], { radius: analysis.catalog.radius_km * 1000, color: '#31795a', weight: 1.5, dashArray: '6 6', fillOpacity: 0.03, interactive: false });
  radius.addTo(group);
  analysis.catalog.all_events.forEach(([lat, lon, magnitude, depth, year]) => {
    const kind = depth == null || depth < 35 ? 'shallow' : depth < 70 ? 'intermediate' : 'deep';
    L.circleMarker([lat, lon], { radius: Math.max(3, 2 + (magnitude - 4) * 2.6), color: DEPTH_COLORS[kind], weight: 1, fillColor: DEPTH_COLORS[kind], fillOpacity: 0.45 })
      .bindTooltip(`M${magnitude} · ${year ?? '—'} · prof. ${depth == null ? '?' : Math.round(depth)} km`)
      .addTo(group);
  });
  L.circleMarker([latitude, longitude], { radius: 7, color: '#1d2b24', weight: 2, fillColor: '#fff', fillOpacity: 1 }).bindTooltip('Punto analizado').addTo(group);
  group.addTo(state.map);
  state.seismic.layer = group;
  state.map.fitBounds(radius.getBounds(), { padding: [20, 20] });
}

function formatSeismicReport(text) {
  return String(text || '').split(/\r?\n/).map((raw) => {
    const line = raw.replace(/^#+\s*/, '').replace(/\*\*/g, '').trim();
    if (!line) return '';
    if (REPORT_TITLES.includes(line.replace(/:$/, ''))) return `<h4>${escapeHtml(line.replace(/:$/, ''))}</h4>`;
    if (/^[-•*]\s+/.test(line)) return `<p class="report-bullet">• ${escapeHtml(line.replace(/^[-•*]\s+/, ''))}</p>`;
    return `<p>${escapeHtml(line)}</p>`;
  }).join('');
}

function renderSeismicResult(a) {
  const c = a.catalog;
  const h = c.hazard;
  const color = riskColors[a.category] || '#52645a';
  const metric = (label, value, note = '') => `<div class="seismic-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ''}</div>`;
  const terrain = a.terrain || {};
  const bars = Object.entries(a.components).map(([name, value]) => `<div class="seismic-bar"><span>${escapeHtml(name)}</span><div><i style="width:${Math.max(2, value)}%;background:${color}"></i></div><b>${value}</b></div>`).join('');
  const trace = (a.agent_trace || []).length
    ? `<div class="seismic-trace"><strong>La IA investigó por su cuenta:</strong><ul>${a.agent_trace.map((t) => `<li><code>${escapeHtml(t.tool)}</code> → ${escapeHtml(t.summary)}</li>`).join('')}</ul></div>` : '';
  const events = c.top_events.map((e) => {
    const url = safeUrl(e.url);
    const when = escapeHtml(String(e.time || '').slice(0, 10));
    return `<tr><td>${when}</td><td><b>M${e.magnitude}</b></td><td>${e.depth_km == null ? '—' : Math.round(e.depth_km) + ' km'}</td><td>${Math.round(e.distance_km)} km</td><td>${escapeHtml(e.place)}${url ? ` · <a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">ficha</a>` : ''}</td></tr>`;
  }).join('');
  const source = (s) => {
    const url = safeUrl(s.url);
    const label = escapeHtml(s.name);
    const link = url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${label}</a>` : label;
    return `<li>${link} <em>${s.consulted ? 'consultada en este análisis' : 'referencia oficial a verificar'}</em>${s.role ? ` — ${escapeHtml(s.role)}` : ''}</li>`;
  };
  return `
    <div class="seismic-hero" style="--risk:${color}">
      <div class="seismic-score"><b>${a.score}</b><span>/100</span></div>
      <div><div class="eyebrow">RIESGO SÍSMICO</div><h3>${escapeHtml(a.category)}</h3><p>Clase de peligrosidad GSHAP: <b>${escapeHtml(h.class_gshap)}</b> · Calidad de datos ${escapeHtml(a.data_quality)}</p></div>
    </div>
    <div class="seismic-grid">
      ${metric('Aceleración esperada (10 % en 50 años)', `${h.pga_475_g} g`, `Intensidad Mercalli ~${h.mmi_475} · con 2 % en 50 años: ${h.pga_2475_g} g`)}
      ${metric('Mayor sismo cercano (300 km)', fmtEvent(c.max_magnitude_local), c.max_magnitude_local ? c.max_magnitude_local.place : '')}
      ${metric('Magnitud máxima plausible', `M${c.plausible_max_magnitude}`, 'Orientativa, no es un tope físico')}
      ${metric(`Sismos M≥${c.main_min_magnitude} desde 1973`, c.n_events_main, `${c.events_last_10_years} en los últimos 10 años`)}
      ${metric('Retorno de un M6 / M7 / M8', `${fmtYears(c.recurrence_years.M6)} / ${fmtYears(c.recurrence_years.M7)} / ${fmtYears(c.recurrence_years.M8)}`, `Gutenberg-Richter, b=${c.b_value}${c.b_value_estimated ? '' : ' (supuesto)'}`)}
      ${metric('Sismo M≥6 más cercano', fmtEvent(c.nearest_m6), 'Catálogo desde 1900')}
      ${metric('Profundidad típica', c.depth.median_km == null ? 'Sin datos' : `${Math.round(c.depth.median_km)} km`, c.depth.deep_pct == null ? '' : `${c.depth.shallow_pct}% superficiales · ${c.depth.deep_pct}% profundos`)}
      ${metric('Altura y pendiente local', `${terrain.elevation_m == null ? '—' : Math.round(terrain.elevation_m) + ' m'} · ${terrain.slope_pct == null ? '—' : terrain.slope_pct + ' %'}`, 'Copernicus DEM 90 m (aprox.)')}
    </div>
    <div class="seismic-columns">
      <div>
        <h4>Cómo se calculó el puntaje</h4>${bars}
        <p class="seismic-note">${escapeHtml(c.tectonic_context)}</p>
        <h4>¿Es recomendable vivir ahí?</h4>
        <ul class="seismic-guidance">${a.guidance.map((g) => `<li>${escapeHtml(g)}</li>`).join('')}</ul>
      </div>
      <div class="seismic-report"><div class="eyebrow">INFORME · ${escapeHtml(a.model_source)}</div>${formatSeismicReport(a.report)}${trace}${a.ai_error ? `<p class="seismic-note">${escapeHtml(a.ai_error)} Se muestra el informe local.</p>` : ''}</div>
    </div>
    <h4>Mayores sismos del entorno <small>(círculos en el mapa: rojo superficial · naranja intermedio · azul profundo)</small></h4>
    <div class="table-wrap"><table><thead><tr><th>FECHA</th><th>MAGNITUD</th><th>PROF.</th><th>DISTANCIA</th><th>LUGAR</th></tr></thead><tbody>${events}</tbody></table></div>
    <h4>Fuentes</h4>
    <ul class="seismic-sources">${a.sources.map(source).join('')}${a.official_sources.map(source).join('')}</ul>
    <p class="seismic-note"><b>Método:</b> ${escapeHtml(a.method)}</p>
    <p class="seismic-note"><b>Límites:</b> ${escapeHtml(a.limitations)} No es una alerta ni un pronóstico.</p>`;
}

async function loadSeismicHistory() {
  const box = $('#seismic-history');
  if (!box) return;
  try {
    const { analyses } = await api('/api/seismic');
    box.innerHTML = analyses.length ? analyses.map((item) => {
      const place = escapeHtml(item.place || `${item.latitude.toFixed(3)}°, ${item.longitude.toFixed(3)}°`);
      return `<button type="button" class="seismic-history-item" data-seismic-id="${item.id}"><span><b>${place}</b> ${escapeHtml(item.country)}</span><span>${escapeHtml(item.category)} · ${item.score}/100 · PGA ${item.pga_475} g · ${escapeHtml(String(item.created_at).slice(0, 10))}</span></button>`;
    }).join('') : '<div class="empty-state">Todavía no hay análisis sísmicos.</div>';
  } catch (error) {
    box.textContent = error.message;
  }
}

async function openSeismicHistoryItem(event) {
  const button = event.target.closest('[data-seismic-id]');
  if (!button) return;
  try {
    const data = await api(`/api/seismic/${button.dataset.seismicId}`);
    $('#seismic-status').classList.add('hidden');
    showSeismicAnalysis(data.id, data.analysis);
    $('#seismic-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    toast(error.message);
  }
}

$('#seismic-run-button').addEventListener('click', runSeismicAnalysis);
$('#seismic-history').addEventListener('click', openSeismicHistoryItem);
