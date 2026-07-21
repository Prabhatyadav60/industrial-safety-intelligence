// Industrial Safety Intelligence -- live dashboard client.
// No build step: talks directly to the engine's REST + WebSocket API.

const API_HOST = window.__ENGINE_HOST__ || "localhost:8000";
const API = `http://${API_HOST}`;
const WS_URL = `ws://${API_HOST}/ws/risk`;

const BOX_W = 130;
const BOX_H = 84;

const STATUS_ICON = { GREEN: "●", YELLOW: "▲", RED: "✖" }; // ● ▲ ✖

let zones = [];
let riskByZone = new Map();   // zone_id -> latest snapshot
let lastBand = new Map();     // zone_id -> previous band, for edge-detecting RED
let selectedZoneId = null;
let alerts = [];              // newest first: {zone_id, zone_name, score, triggers, timestamp, rag}
let ws = null;
let reconnectDelay = 1000;

function fmtTime(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour12: false });
}

// ---------- SVG plant layout ----------

function renderZones() {
  const svg = document.getElementById("plant-svg");
  svg.innerHTML = "";
  for (const zone of zones) {
    const snap = riskByZone.get(zone.zone_id);
    const band = snap ? snap.band : "GREEN";
    const score = snap ? snap.score : 0;

    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("transform", `translate(${zone.x - BOX_W / 2}, ${zone.y - BOX_H / 2})`);
    g.style.cursor = "pointer";
    g.addEventListener("click", () => selectZone(zone.zone_id));

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("width", BOX_W);
    rect.setAttribute("height", BOX_H);
    rect.setAttribute("rx", 10);
    rect.setAttribute("class", `zone-rect ${band}` + (zone.zone_id === selectedZoneId ? " selected" : ""));
    rect.setAttribute("fill", fillFor(band));
    g.appendChild(rect);

    const name = document.createElementNS("http://www.w3.org/2000/svg", "text");
    name.setAttribute("x", 10);
    name.setAttribute("y", 20);
    name.setAttribute("class", "zone-label");
    name.textContent = zone.name;
    g.appendChild(name);

    const sub = document.createElementNS("http://www.w3.org/2000/svg", "text");
    sub.setAttribute("x", 10);
    sub.setAttribute("y", 34);
    sub.setAttribute("class", "zone-sub");
    sub.textContent = zone.hazard_class.replace("_", " ");
    g.appendChild(sub);

    const scoreText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    scoreText.setAttribute("x", 10);
    scoreText.setAttribute("y", 62);
    scoreText.setAttribute("class", "zone-score");
    scoreText.setAttribute("fill", inkFor(band));
    scoreText.textContent = score;
    g.appendChild(scoreText);

    const bandText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    bandText.setAttribute("x", 40);
    bandText.setAttribute("y", 62);
    bandText.setAttribute("class", "zone-band");
    bandText.setAttribute("fill", inkFor(band));
    bandText.textContent = `${STATUS_ICON[band]} ${band}`;
    g.appendChild(bandText);

    svg.appendChild(g);
  }
}

function fillFor(band) {
  const v = getComputedStyle(document.documentElement);
  if (band === "RED") return v.getPropertyValue("--status-critical-bg").trim() || "rgba(208,59,59,0.14)";
  if (band === "YELLOW") return v.getPropertyValue("--status-warning-bg").trim() || "rgba(250,178,25,0.16)";
  return v.getPropertyValue("--status-good-bg").trim() || "rgba(12,163,12,0.12)";
}

function inkFor(band) {
  if (band === "RED") return "var(--status-critical)";
  if (band === "YELLOW") return "var(--status-warning)";
  return "var(--status-good)";
}

// ---------- Zone detail panel ----------

function selectZone(zoneId) {
  selectedZoneId = zoneId;
  renderZones();
  renderZoneDetail();
}

function renderZoneDetail() {
  const el = document.getElementById("zone-detail");
  const zone = zones.find((z) => z.zone_id === selectedZoneId);
  const snap = riskByZone.get(selectedZoneId);
  if (!zone) {
    el.innerHTML = '<div id="alert-empty">Select a zone…</div>';
    return;
  }
  const band = snap ? snap.band : "GREEN";
  const reading = snap && snap.latest_reading;
  const permits = (snap && snap.active_permits) || [];
  const maintenance = (snap && snap.active_maintenance) || [];
  const triggers = (snap && snap.triggers) || [];

  el.innerHTML = `
    <div class="zd-title">${zone.name}</div>
    <div class="zd-hazard">${zone.hazard_class.replace("_", " ")} &middot; ${zone.zone_id}</div>
    <span class="status-chip ${band}"><span class="ic">${STATUS_ICON[band]}</span>${band} &middot; score ${snap ? snap.score : 0}</span>

    <table class="kv-table">
      <tr><td>Gas (% LEL)</td><td>${reading ? reading.gas_pct_lel.toFixed(1) : "--"}</td></tr>
      <tr><td>Temperature (&deg;C)</td><td>${reading ? reading.temp_celsius.toFixed(1) : "--"}</td></tr>
      <tr><td>Pressure (kPa)</td><td>${reading ? reading.pressure_kpa.toFixed(1) : "--"}</td></tr>
      <tr><td>Last reading</td><td>${reading ? fmtTime(reading.timestamp) : "--"}</td></tr>
    </table>

    <div style="margin-top:10px; font-size:11px; color: var(--text-secondary);">Active permits</div>
    <div>${permits.length ? permits.map((p) => `<span class="permit-chip">${p.type}</span>`).join("") : '<span style="color: var(--text-muted); font-size:12px;">none</span>'}</div>

    <div style="margin-top:8px; font-size:11px; color: var(--text-secondary);">Active maintenance</div>
    <div>${maintenance.length ? maintenance.map((m) => `<span class="permit-chip">${m.description}</span>`).join("") : '<span style="color: var(--text-muted); font-size:12px;">none</span>'}</div>

    ${triggers.length ? `<ul class="trigger-list">${triggers.map((t) => `<li>${t}</li>`).join("")}</ul>` : ""}
  `;
}

// ---------- Alert feed ----------

function pushAlert(snap) {
  alerts.unshift({
    zone_id: snap.zone_id,
    zone_name: snap.zone_name,
    score: snap.score,
    triggers: snap.triggers,
    timestamp: snap.timestamp,
    rag: null,
  });
  alerts = alerts.slice(0, 50);
  renderAlertFeed();
}

function renderAlertFeed() {
  const el = document.getElementById("alert-feed");
  if (!alerts.length) {
    el.innerHTML = '<div id="alert-empty">No RED alerts yet.</div>';
    return;
  }
  el.innerHTML = alerts
    .map(
      (a) => `
    <div class="alert-card">
      <div class="a-head">
        <span class="a-zone">${a.zone_name}</span>
        <span class="a-time">${fmtTime(a.timestamp)}</span>
      </div>
      <div class="a-score">RED &middot; score ${a.score}</div>
      <div class="a-triggers">${(a.triggers || []).join("; ")}</div>
      ${
        a.rag
          ? `<div class="a-rag">
               <div class="lbl">Why this is dangerous</div>
               <div>${a.rag.explanation}</div>
               <div class="lbl" style="margin-top:6px;">Cited regulation</div>
               <div>${a.rag.cited_regulation}</div>
               ${a.rag.similar_past_incident ? `<div class="lbl" style="margin-top:6px;">Similar past incident</div><div>${a.rag.similar_past_incident}</div>` : ""}
             </div>`
          : `<div class="a-pending">Awaiting safety-knowledge explanation…</div>`
      }
    </div>`
    )
    .join("");
}

// Best-effort enrichment from the orchestrator's /alerts endpoint (added in
// a later phase). Silently no-ops until that endpoint exists.
async function pollOrchestratorAlerts() {
  try {
    const res = await fetch(`${API}/alerts?limit=50`);
    if (!res.ok) return;
    const docs = await res.json();
    for (const doc of docs) {
      const match = alerts.find(
        (a) => a.zone_id === doc.zone_id && Math.abs(new Date(a.timestamp) - new Date(doc.created_at)) < 15000
      );
      if (match && doc.rag_result) {
        match.rag = doc.rag_result;
      }
    }
    renderAlertFeed();
  } catch (e) {
    // engine/orchestrator not up yet -- ignore
  }
}

// ---------- WebSocket wiring ----------

function setConn(state, label) {
  const el = document.getElementById("conn");
  el.className = state;
  document.getElementById("conn-label").textContent = label;
}

function connect() {
  setConn("", "connecting…");
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setConn("live", "live");
    reconnectDelay = 1000;
  };

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type !== "risk_update") return;

    for (const snap of msg.zones) {
      riskByZone.set(snap.zone_id, snap);
      const prev = lastBand.get(snap.zone_id) || "GREEN";
      if (snap.band === "RED" && prev !== "RED") {
        pushAlert(snap);
      }
      lastBand.set(snap.zone_id, snap.band);
    }

    if (!selectedZoneId && zones.length) {
      const top = [...riskByZone.values()].sort((a, b) => b.score - a.score)[0];
      selectedZoneId = top ? top.zone_id : zones[0].zone_id;
    }

    renderZones();
    renderZoneDetail();
  };

  ws.onclose = () => {
    setConn("down", "reconnecting…");
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
  };

  ws.onerror = () => ws.close();
}

// ---------- Boot ----------

async function boot() {
  const res = await fetch(`${API}/zones`);
  zones = await res.json();
  renderZones();
  connect();
  setInterval(pollOrchestratorAlerts, 5000);
}

boot();
