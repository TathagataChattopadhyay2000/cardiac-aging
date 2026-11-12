"use strict";

const $ = (id) => document.getElementById(id);
const theme = getComputedStyle(document.documentElement);
const themeColor = (name) => theme.getPropertyValue(name).trim();
const colors = [themeColor("--reference"), themeColor("--comparison")];
const chartPalette = {
  paper: themeColor("--surface"),
  rule: themeColor("--edge"),
  label: themeColor("--muted"),
  selection: themeColor("--accent"),
};
const state = {study: null, rows: [], direction: "up", sort: "gap", limit: 35,
  selected: null, view: null, detail: null, map: null, local: null, overlay: null,
  pins: [], busy: false, detailVersion: 0, rankVersion: 0, localVersion: 0};
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = (x, n = 4) => x == null || !Number.isFinite(Number(x)) ? "—" : Number(x).toFixed(n);
const signed = (x) => (x > 0 ? "+" : "") + fmt(x);
const interval = (d) => fmt(d.lo, 2) + "–" + fmt(d.hi, 2);
const option = (value, label = value) => "<option value=\"" + esc(value) + "\">" + esc(label) + "</option>";
const empty = (label) => '<div class="empty compact">' + esc(label) + "</div>";
const groups = () => ({study: state.study.key, reference: $("reference").value, comparison: $("comparison").value});
const traceColor = (group) => group === $("reference").value ? colors[0] : colors[1];

function notice(message = "", error = false) {
  $("notice").hidden = !message;
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
}

async function api(path, body) {
  const response = await fetch("/api/" + path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Local-Token": state.token},
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The local server returned an error.");
  return data;
}

function updateControls() {
  const active = Boolean(state.study);
  for (const id of ["reference", "comparison", "bin-width"]) $(id).disabled = !active;
  for (const id of ["section", "precursor", "map-lo", "map-hi", "reset-view", "interval-lo",
    "interval-hi", "compare-custom"]) $(id).disabled = !state.detail;
  $("build-map").disabled = !state.detail || state.busy;
  $("open-study").disabled = state.busy;
  $("pin").disabled = !state.local || !state.map || !state.detail;
}

async function job(kind, payload, title) {
  state.busy = true;
  updateControls();
  $("operation").hidden = false;
  $("operation-title").textContent = title;
  $("operation-message").textContent = "Starting…";
  $("progress").value = 0;
  $("cancel-job").disabled = false;
  notice();
  try {
    await api(kind, payload);
    for (;;) {
      const status = await api("job");
      $("operation-message").textContent = status.message || "";
      $("progress").value = status.progress || 0;
      if (status.status === "complete") return status.result;
      if (status.status === "failed") throw new Error(status.message);
      await new Promise((resolve) => setTimeout(resolve, 650));
    }
  } finally {
    state.busy = false;
    $("operation").hidden = true;
    updateControls();
  }
}

function clearMap() {
  state.localVersion += 1;
  state.map = state.local = state.overlay = null;
  $("ion-map").hidden = true;
  $("map-empty").hidden = false;
  $("map-readout").hidden = true;
  $("histology-tools").hidden = true;
  $("show-histology").checked = false;
  $("show-histology").disabled = true;
  $("histology-image").value = $("histology-transform").value = "";
  $("local-spectrum").innerHTML = empty("Build the map to see the spectrum at a selected spot.");
  $("local-peaks").textContent = "";
  $("coordinates").textContent = "";
  $("map-scale").textContent = "";
  $("isolation-label").textContent = "";
  updateControls();
}

async function loadStudy() {
  $("dialog-error").textContent = "";
  $("study-dialog").close();
  try {
    const study = await job("load", {
      data: $("data-path").value, sheet: $("sheet-path").value, legacy: $("legacy-path").value,
    }, "Loading study");
    state.study = study;
    state.rows = [];
    state.selected = state.detail = null;
    clearMap();
    $("study-name").textContent = study.label;
    $("animal-count").textContent = study.samples.length;
    $("pixel-count").textContent = study.samples.reduce((sum, s) => sum + s.pixels, 0).toLocaleString();
    $("mass-range").textContent = fmt(study.low, 1) + "–" + fmt(study.high, 1) + " m/z";
    const eligible = study.groups.filter((g) => study.samples.filter((s) => s.group === g).length >= 2);
    $("reference").innerHTML = $("comparison").innerHTML = eligible.map((g) => option(g)).join("");
    $("reference").value = eligible.includes("young") ? "young" : eligible[0];
    $("comparison").value = eligible.includes("old") ? "old" : eligible.find((g) => g !== $("reference").value);
    $("find-mass").value = "";
    try { state.pins = JSON.parse(localStorage.getItem("msms:" + study.key) || "[]"); }
    catch { state.pins = []; }
    renderPins();
    const cached = study.samples.filter((s) => s.cache !== "new").length;
    notice(study.samples.length + " exports ready · " + cached + " native spectra reused from cache.");
    await refreshRanking();
  } catch (error) {
    if (error.message === "Cancelled.") notice(error.message);
    else {
      $("dialog-error").textContent = error.message;
      $("study-dialog").showModal();
    }
  }
}

function filtered() {
  const query = $("find-mass").value.trim();
  const mass = query ? Number(query) : null;
  const rows = state.rows.filter((r) =>
    (!$("stable").checked || r.stable) &&
    (state.direction === "all" || (state.direction === "up" ? r.gap > 0 : r.gap < 0)) &&
    (mass === null || (r.lo <= mass && mass < r.hi)));
  const score = (r) => {
    const value = state.sort === "effect" ? r.effect : r.gap;
    return value == null ? -Infinity :
      (state.direction === "down" ? -value : state.direction === "all" ? Math.abs(value) : value);
  };
  return rows.sort((a, b) => score(b) - score(a));
}

function renderRanking() {
  const rows = filtered();
  $("rank-count").textContent = rows.length.toLocaleString() + " matches";
  $("rank-empty").hidden = rows.length > 0;
  $("rank-empty").textContent = state.study ? "No intervals match these filters." : "Open a study to rank mass intervals.";
  $("sort-gap").textContent = "Δ max" + (state.sort === "gap" ? (state.direction === "down" ? " ↑" : " ↓") : "");
  $("sort-effect").textContent = "Effect" + (state.sort === "effect" ? " ↓" : "");
  $("more-rows").hidden = rows.length <= state.limit;
  $("rank-rows").replaceChildren();
  for (const r of rows.slice(0, state.limit)) {
    const tr = document.createElement("tr");
    const active = state.selected && r.lo === state.selected.lo && r.hi === state.selected.hi;
    tr.classList.toggle("selected", Boolean(active));
    tr.innerHTML = '<td><button class="interval-pick" aria-pressed="' + Boolean(active) + '">' + interval(r) +
      '</button></td><td class="' + (r.gap > 0 ? "delta-up" : "delta-down") + '">' + signed(r.gap) +
      "</td><td>" + fmt(r.effect, 2) + "</td><td>" + esc(r.sample) + "</td>";
    tr.addEventListener("click", () => selectInterval(r));
    $("rank-rows").append(tr);
  }
}

async function refreshRanking() {
  if (!state.study) return;
  const version = ++state.rankVersion;
  state.detailVersion += 1;
  state.selected = state.detail = null;
  clearMap();
  $("spectrum").innerHTML = empty("Select a candidate to view its native traces.");
  $("points").innerHTML = empty("Each point represents one animal.");
  $("animal-values").replaceChildren();
  $("interval-title").textContent = "Native spectra";
  $("interval-gap").textContent = "—";
  $("spectrum-subtitle").textContent = "Select a candidate to compare its peak heights.";
  $("legend").replaceChildren();
  try {
    const rows = await api("rank", {...groups(), width: Number($("bin-width").value)});
    if (version !== state.rankVersion) return;
    state.rows = rows;
    $("window-count").textContent = rows.length.toLocaleString();
    state.limit = 35;
    renderRanking();
    const first = filtered()[0];
    if (first) await selectInterval(first);
  } catch (error) {
    if (version !== state.rankVersion) return;
    state.rows = [];
    $("window-count").textContent = "—";
    renderRanking();
    notice(error.message, true);
  }
}

function initialView(row) {
  const margin = Math.max(.35, (row.hi - row.lo) * .32);
  return [Math.max(state.study.low, row.lo - margin), Math.min(state.study.high, row.hi + margin)];
}

async function selectInterval(row, useCurrentView = false) {
  state.selected = {lo: row.lo, hi: row.hi};
  state.detail = null;
  if (!useCurrentView) state.view = initialView(row);
  clearMap();
  $("interval-lo").value = row.lo;
  $("interval-hi").value = row.hi;
  $("interval-title").textContent = interval(row) + " m/z";
  $("spectrum-subtitle").textContent = "Loading native traces…";
  $("interval-gap").textContent = "—";
  $("points").innerHTML = empty("Loading animal maxima…");
  $("animal-values").replaceChildren();
  renderRanking();
  updateControls();
  await loadDetail(true);
}

async function loadDetail(resetTarget = false) {
  const version = ++state.detailVersion;
  const [viewLo, viewHi] = state.view;
  try {
    const detail = await api("detail", {...groups(), ...state.selected, viewLo, viewHi});
    if (version !== state.detailVersion) return;
    state.detail = detail;
    const {stats, traces} = detail;
    $("interval-title").textContent = interval(stats) + " m/z";
    $("spectrum-subtitle").textContent = "Each dot on the traces marks an animal’s maximum.";
    $("interval-gap").textContent = signed(stats.gap) + " Δ mean maximum";
    $("legend").innerHTML = [$("reference").value, $("comparison").value].map((g, i) =>
      '<span><i style="background:' + colors[i] + '"></i>' + esc(g) + "</span>").join("");
    if (resetTarget) {
      $("section").innerHTML = traces.map((t) => option(t.id, t.id + " · " + t.group)).join("");
      $("section").value = stats.sample;
      setTarget();
    }
    renderSpectra();
    renderPoints();
    $("animal-values").innerHTML = '<table><thead><tr><th>Section</th><th>Group</th><th>Max m/z</th><th>Height</th></tr></thead><tbody>' +
      traces.map((t) => "<tr><td>" + esc(t.id) + "</td><td>" + esc(t.group) + "</td><td>" +
        fmt(t.apex) + "</td><td>" + fmt(t.height) + "</td></tr>").join("") + "</tbody></table>";
    updateControls();
  } catch (error) {
    if (version !== state.detailVersion) return;
    if (resetTarget) $("spectrum").innerHTML = empty(error.message);
    notice(error.message, true);
  }
}

function setTarget() {
  const sample = state.detail.traces.find((t) => t.id === $("section").value);
  if (!sample) return;
  $("precursor").value = fmt(sample.apex, 6);
  // A starting map interval. The operator's isolation width is configured separately.
  const bounds = [Math.max(state.study.low, sample.apex - .12),
    Math.min(state.study.high, sample.apex + .12)];
  $("map-lo").value = fmt(bounds[0], 6);
  $("map-hi").value = fmt(bounds[1], 6);
  clearMap();
}

const frame = {w: 580, h: 275, left: 55, right: 15, top: 15, bottom: 34};

function chart({traces, domain, shade, center, marks = [], local = false}) {
  const f = {...frame, h: local ? 225 : frame.h};
  const [lo, hi] = domain;
  let minY = 0, maxY = 0;
  for (const trace of traces) for (const y of trace.y) {
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  if (maxY === minY) maxY += 1;
  maxY += (maxY - minY) * .08;
  const x = (v) => f.left + (v - lo) / (hi - lo) * (f.w - f.left - f.right);
  const y = (v) => f.h - f.bottom - (v - minY) / (maxY - minY) * (f.h - f.top - f.bottom);
  const line = (x1, y1, x2, y2, color = chartPalette.rule, extra = "") =>
    '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + color + '" ' + extra + "/>";
  const text = (xx, yy, label, anchor = "middle") => '<text x="' + xx + '" y="' + yy +
    '" text-anchor="' + anchor + '" fill="' + chartPalette.label + '" font-size="12" font-family="monospace">' + esc(label) + "</text>";
  let body = '<rect width="100%" height="100%" fill="' + chartPalette.paper + '"/>';
  if (shade) {
    const start = Math.max(lo, shade[0]), end = Math.min(hi, shade[1]);
    if (end > start) body += '<rect x="' + x(start) + '" y="' + f.top + '" width="' + (x(end) - x(start)) +
      '" height="' + (f.h - f.top - f.bottom) + '" fill="' + chartPalette.selection + '" fill-opacity=".10"/>';
  }
  for (let i = 0; i <= 4; i++) {
    const xx = lo + (hi - lo) * i / 4;
    const yy = minY + (maxY - minY) * i / 4;
    body += line(x(xx), f.top, x(xx), f.h - f.bottom) + text(x(xx), f.h - 14, fmt(xx, hi - lo < .25 ? 3 : 2));
    body += line(f.left, y(yy), f.w - f.right, y(yy)) + text(f.left - 7, y(yy) + 4, fmt(yy, maxY < 2 ? 2 : 1), "end");
  }
  for (const t of traces) {
    if (!t.x.length) continue;
    const path = t.x.map((v, i) => (i ? "L" : "M") + x(v).toFixed(2) + " " + y(t.y[i]).toFixed(2)).join(" ");
    body += '<path d="' + path + '" fill="none" stroke="' + t.color + '" stroke-width="' +
      (local ? 2.2 : 1.5) + '" opacity="' + (local ? 1 : .67) + '"/>';
  }
  if (center >= lo && center <= hi) body += line(x(center), f.top, x(center), f.h - f.bottom, colors[1], 'stroke-dasharray="4 4"');
  for (const p of marks) if (p.apex >= lo && p.apex <= hi && p.height <= maxY && p.height >= minY) {
    body += '<circle cx="' + x(p.apex) + '" cy="' + y(p.height) + '" r="3.7" fill="' + p.color + '"><title>' +
      esc(p.id + " · " + fmt(p.apex) + " m/z · " + fmt(p.height)) + "</title></circle>";
  }
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + f.w + " " + f.h +
    '" role="img" aria-label="' + (local ? "Local spectrum" : "Native spectra") + '">' + body + "</svg>";
}

function renderSpectra() {
  const traces = state.detail.traces.map((t) => ({...t, color: traceColor(t.group)}));
  $("spectrum").innerHTML = chart({traces, marks: traces, domain: state.view,
    shade: [state.selected.lo, state.selected.hi]});
  const svg = $("spectrum").querySelector("svg");
  let down = null;
  const mouseMass = (event) => {
    const bounds = svg.getBoundingClientRect();
    const p = Math.max(0, Math.min(1, ((event.clientX - bounds.left) * frame.w / bounds.width - frame.left) /
      (frame.w - frame.left - frame.right)));
    return state.view[0] + p * (state.view[1] - state.view[0]);
  };
  svg.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    down = {mass: mouseMass(e), x: e.clientX};
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", (e) => {
    if (!down) return;
    const svgRect = svg.getBoundingClientRect();
    const x1 = (down.x - svgRect.left) * frame.w / svgRect.width;
    const x2 = (e.clientX - svgRect.left) * frame.w / svgRect.width;
    let rect = svg.querySelector(".brush");
    if (!rect) {
      rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("class", "brush");
      rect.setAttribute("fill", chartPalette.selection);
      rect.setAttribute("fill-opacity", ".22");
      rect.setAttribute("y", frame.top);
      rect.setAttribute("height", frame.h - frame.top - frame.bottom);
      svg.append(rect);
    }
    rect.setAttribute("x", Math.min(x1, x2));
    rect.setAttribute("width", Math.abs(x2 - x1));
  });
  svg.addEventListener("pointerup", (e) => {
    if (!down) return;
    const start = down;
    down = null;
    svg.querySelector(".brush")?.remove();
    if (Math.abs(e.clientX - start.x) < 5) return;
    const bounds = [start.mass, mouseMass(e)].sort((a, b) => a - b).map((n) => +fmt(n, 5));
    if (bounds[1] > bounds[0]) selectInterval({lo: bounds[0], hi: bounds[1]}, true);
  });
  svg.addEventListener("pointercancel", () => { down = null; svg.querySelector(".brush")?.remove(); });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const [a, b] = state.view;
    const scale = e.deltaY < 0 ? .79 : 1.26;
    const center = mouseMass(e);
    const range = [Math.max(state.study.low, center + (a - center) * scale),
      Math.min(state.study.high, center + (b - center) * scale)];
    if (range[1] - range[0] >= .025) {
      state.view = range;
      loadDetail();
    }
  }, {passive: false});
}

function renderPoints() {
  const traces = state.detail.traces;
  const min = Math.min(...traces.map((t) => t.height));
  const max = Math.max(...traces.map((t) => t.height));
  const pad = Math.max((max - min) * .13, Math.abs(max) * .03, .0001);
  const lo = min - pad, hi = max + pad;
  const x = (n) => 62 + (n - lo) / (hi - lo) * 490;
  let svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 580 120" role="img" aria-label="Individual maximum intensities"><rect width="100%" height="100%" fill="' + chartPalette.paper + '"/>';
  const label = (xx, yy, value, anchor = "middle") => '<text x="' + xx + '" y="' + yy +
    '" text-anchor="' + anchor + '" fill="' + chartPalette.label + '" font-size="12" font-family="monospace">' + esc(value) + "</text>";
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, xx = x(v);
    svg += '<path d="M' + xx + ' 12V91" stroke="' + chartPalette.rule + '"/>' + label(xx, 107, fmt(v, 2));
  }
  [$("reference").value, $("comparison").value].forEach((group, i) => {
    const yy = 32 + i * 36;
    svg += label(54, yy + 4, group.length > 8 ? group.slice(0, 7) + "…" : group, "end");
    const members = traces.filter((t) => t.group === group);
    const mean = members.reduce((sum, t) => sum + t.height, 0) / members.length;
    svg += '<path d="M' + x(mean) + " " + (yy - 12) + 'v24" stroke="' + colors[i] + '" stroke-width="2"/>';
    members.forEach((t, j) => {
      svg += '<circle cx="' + x(t.height) + '" cy="' + (yy + (j % 3 - 1) * 6) + '" r="4.5" fill="' + colors[i] +
        '"><title>' + esc(t.id + " · " + fmt(t.height) + " at " + fmt(t.apex) + " m/z") + "</title></circle>";
    });
  });
  $("points").innerHTML = svg + "</svg>";
}

async function generateMap() {
  const chosen = state.selected;
  const sample = $("section").value;
  const lo = Number($("map-lo").value), hi = Number($("map-hi").value);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo >= hi)
    return notice("Enter a Map from value smaller than Map to.", true);
  clearMap();
  try {
    const map = await job("map", {study: state.study.key, sample, lo, hi}, "Building signal map");
    if (chosen !== state.selected || sample !== $("section").value ||
        lo !== Number($("map-lo").value) || hi !== Number($("map-hi").value)) return;
    state.map = map;
    state.spot = map.spot;
    $("map-empty").hidden = true;
    $("ion-map").hidden = false;
    $("map-readout").hidden = false;
    $("histology-tools").hidden = false;
    $("map-scale").textContent = "Color scale " + fmt(map.low, 2) + "–" + fmt(map.high, 2) + " (2nd–98th percentile)";
    renderMap();
    await readSpot(map.spot);
  } catch (error) { notice(error.message, error.message !== "Cancelled."); }
}

const palette = [[23, 24, 65], [58, 50, 135], [43, 106, 159], [42, 167, 160], [118, 217, 140], [255, 235, 114]];
function renderMap() {
  const m = state.map;
  if (!m) return;
  const canvas = $("ion-map");
  const scale = Math.max(1, Math.floor(640 / Math.max(m.width, m.height)));
  canvas.width = m.width * scale;
  canvas.height = m.height * scale;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = chartPalette.paper;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const overlay = state.overlay && $("show-histology").checked;
  if (overlay) {
    const {image, matrix: a, shift: b} = state.overlay;
    const determinant = a[0][0] * a[1][1] - a[0][1] * a[1][0];
    const inv = [[a[1][1] / determinant, -a[0][1] / determinant],
      [-a[1][0] / determinant, a[0][0] / determinant]];
    const tx = -inv[0][0] * (b[0] + .5) - inv[0][1] * (b[1] + .5) - m.origin[0] + .5;
    const ty = -inv[1][0] * (b[0] + .5) - inv[1][1] * (b[1] + .5) - m.origin[1] + .5;
    ctx.save();
    ctx.setTransform(scale * inv[0][0], scale * inv[1][0], scale * inv[0][1],
      scale * inv[1][1], scale * tx, scale * ty);
    ctx.drawImage(image, 0, 0);
    ctx.restore();
  }
  const layer = document.createElement("canvas");
  layer.width = m.width; layer.height = m.height;
  const lc = layer.getContext("2d");
  const pixels = lc.createImageData(m.width, m.height);
  m.values.forEach((v, i) => {
    if (v === null) return;
    const t = Math.max(0, Math.min(1, (v - m.low) / (m.high - m.low || 1))) * (palette.length - 1);
    const j = Math.min(palette.length - 2, Math.floor(t));
    for (let k = 0; k < 3; k++) pixels.data[i * 4 + k] = palette[j][k] + (palette[j + 1][k] - palette[j][k]) * (t - j);
    pixels.data[i * 4 + 3] = 255;
  });
  lc.putImageData(pixels, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.globalAlpha = overlay ? .54 : 1;
  ctx.drawImage(layer, 0, 0, canvas.width, canvas.height);
  ctx.globalAlpha = 1;
  const spot = state.spot;
  if (!spot) return;
  const x = (spot.x - m.origin[0] + .5) * scale, y = (spot.y - m.origin[1] + .5) * scale;
  const r = Math.max(5, scale * 1.8);
  ctx.beginPath();
  ctx.moveTo(x - r, y); ctx.lineTo(x + r, y); ctx.moveTo(x, y - r); ctx.lineTo(x, y + r);
  ctx.lineWidth = 4;
  ctx.strokeStyle = "#2e261f";
  ctx.stroke();
  ctx.lineWidth = 1.9;
  ctx.strokeStyle = "#ffffff";
  ctx.stroke();
}

async function readSpot(spot) {
  if (!state.map) return;
  const version = ++state.localVersion;
  state.spot = spot;
  state.local = null;
  $("coordinates").textContent = "Selected spot · x " + spot.x + " · y " + spot.y;
  $("coordinates").title = spot.method || "User-selected measured pixel";
  $("local-spectrum").innerHTML = empty("Reading spectrum at this spot…");
  $("isolation-label").textContent = "";
  $("local-peaks").textContent = "";
  renderMap();
  updateControls();
  try {
    const center = Number($("precursor").value), isolation = Number($("isolation").value);
    if (!Number.isFinite(center) || !$("precursor").value || isolation < .05 || isolation > 50)
      throw new Error("Enter a target m/z and an isolation width of 0.05–50 m/z.");
    const radius = Math.max(.65, isolation * .65);
    const lo = center - radius, hi = center + radius;
    const local = await api("local", {study: state.study.key, sample: state.map.sample,
      x: spot.x, y: spot.y, center, isolation, lo, hi});
    if (version !== state.localVersion) return;
    state.local = {...local, center, width: isolation};
    $("isolation-label").textContent = "Shaded window " + local.isolation.map((m) => fmt(m, 2)).join("–") + " m/z";
    $("local-spectrum").innerHTML = chart({
      traces: [{x: local.mass, y: local.intensity, color: colors[0]}],
      domain: [lo, hi], shade: local.isolation, center, local: true,
    });
    $("local-peaks").textContent = local.peaks.length ?
      "Strongest peaks in the shaded window (m/z): " + local.peaks.map((p) => fmt(p.mz)).join(", ") :
      "No peaks detected inside the shaded window.";
    updateControls();
  } catch (error) {
    if (version === state.localVersion) $("local-spectrum").innerHTML = empty(error.message);
  }
}

async function loadOverlay() {
  const imageFile = $("histology-image").files[0], jsonFile = $("histology-transform").files[0];
  if (!imageFile || !jsonFile || !state.map) return;
  const mappedSection = state.map;
  try {
    const value = JSON.parse(await jsonFile.text());
    const matrix = value.matrix, shift = value.shift;
    if (!Array.isArray(matrix) || matrix.length !== 2 ||
        matrix.some((row) => !Array.isArray(row) || row.length !== 2 || row.some((v) => !Number.isFinite(v))) ||
        !Array.isArray(shift) || shift.length !== 2 || shift.some((v) => !Number.isFinite(v)) ||
        Math.abs(matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]) < 1e-10) {
      throw new Error("Registration JSON needs an invertible 2 × 2 matrix and two-value shift.");
    }
    const expectedMouse = String(value.measurement_id ?? value.mouse ?? "").split(" / ")[0];
    if (expectedMouse && expectedMouse !== mappedSection.sample) throw new Error("Registration belongs to a different section.");
    const filename = value.histology_file ?? value.filename;
    if (filename && filename !== imageFile.name) throw new Error("Select the image named in the registration JSON.");
    const image = await createImageBitmap(imageFile, {imageOrientation: "from-image"});
    if (mappedSection !== state.map) return;
    const size = value.histology_image_size_after_exif ?? value.image_size;
    if (size && (image.width !== size[0] || image.height !== size[1]))
      throw new Error("Image dimensions differ from the registration.");
    state.overlay = {image, matrix, shift};
    $("show-histology").disabled = false;
    $("show-histology").checked = true;
    renderMap();
  } catch (error) { notice(error.message, true); }
}

function renderPins() {
  $("pin-count").textContent = state.pins.length;
  $("export").disabled = state.pins.length === 0;
  $("shortlist").innerHTML = state.pins.length ? "" : empty("Saved spots and their target m/z will appear here.");
  state.pins.forEach((pin, index) => {
    const card = document.createElement("article");
    card.className = "pin-card";
    card.innerHTML = "<div><strong>m/z " + fmt(pin.center) +
      '</strong><button aria-label="Remove target">×</button></div><p>Section ' + esc(pin.sample.id) +
      " · x " + pin.x + " / y " + pin.y + "</p><p>" + interval(pin.stats) +
      " · Δ " + signed(pin.stats.gap) + "</p>";
    card.querySelector("button").addEventListener("click", () => {
      state.pins.splice(index, 1);
      savePins();
      renderPins();
    });
    $("shortlist").append(card);
  });
}

function savePins() {
  try { localStorage.setItem("msms:" + state.study.key, JSON.stringify(state.pins)); }
  catch { notice("Browser storage is full. Export the shortlist before closing this page.", true); }
}

function pinTarget() {
  if (!state.local || !state.map || !state.detail) return;
  const {x, y, center, width, peaks} = state.local;
  const sample = state.study.samples.find((s) => s.id === state.map.sample);
  const entry = {sample, x, y, center, width, peaks, stats: state.detail.stats,
    reference: $("reference").value, comparison: $("comparison").value,
    mapBounds: [state.map.lo, state.map.hi],
    map: $("ion-map").toDataURL("image/png"),
    localChart: $("local-spectrum").querySelector("svg").outerHTML,
    mouseChart: $("points").querySelector("svg").outerHTML,
    values: state.detail.traces.map(({id, group, apex, height}) => ({id, group, apex, height}))};
  const existing = state.pins.findIndex((p) => p.sample.id === sample.id && p.x === x && p.y === y &&
    p.center === center && p.stats.lo === entry.stats.lo && p.stats.hi === entry.stats.hi &&
    p.reference === entry.reference && p.comparison === entry.comparison &&
    p.mapBounds.every((v, i) => v === entry.mapBounds[i]));
  if (existing >= 0) state.pins[existing] = entry;
  else state.pins.push(entry);
  savePins();
  renderPins();
}

function exportPins() {
  if (!state.pins.length) return;
  const cards = state.pins.map((p, i) => "<article><h2>" + (i + 1) + " · Proposed precursor " + fmt(p.center) + " m/z</h2>" +
    "<p><b>Measurement:</b> " + esc(p.sample.id) + " · <b>Animal:</b> " + esc(p.sample.animal) +
    " · <b>Physical section:</b> " + esc(p.sample.section || "Confirm with operator") +
    " · <b>imzML coordinates:</b> x " + p.x + ", y " + p.y + "</p>" +
    "<p><b>Map range:</b> " + p.mapBounds.map((m) => fmt(m)).join("–") +
    " m/z · <b>Full isolation width:</b> " + fmt(p.width, 2) + " m/z · <b>Local maxima in range:</b> " +
    esc(p.peaks.map((peak) => fmt(peak.mz)).join(", ") || "none detected") + "</p>" +
    "<p><b>Group comparison:</b> " + esc(p.reference) + " " + fmt(p.stats.reference) + " vs. " +
    esc(p.comparison) + " " + fmt(p.stats.comparison) + " · Δ " + signed(p.stats.gap) +
    " · window " + interval(p.stats) + " m/z · direction held on removing any animal: " +
    (p.stats.stable ? "yes" : "no") + "</p>" +
    '<div class="plots"><figure><img src="' + esc(p.map) +
    '" alt="Ion map with crosshair at the proposed spot"><figcaption>Ion map and spot</figcaption></figure>' +
    "<figure>" + p.localChart + "<figcaption>Local spectrum and isolation range</figcaption></figure></div>" +
    "<figure>" + p.mouseChart + "<figcaption>One maximum per independent animal</figcaption></figure>" +
    "<details><summary>Individual values</summary><table><thead><tr><th>Measurement</th><th>Group</th><th>m/z</th><th>Maximum</th></tr></thead><tbody>" +
    p.values.map((v) => "<tr><td>" + esc(v.id) + "</td><td>" + esc(v.group) + "</td><td>" + fmt(v.apex) +
      "</td><td>" + fmt(v.height) + "</td></tr>").join("") + "</tbody></table></details></article>").join("");
  const html = '<!doctype html><html lang="en"><meta charset="utf-8"><title>MS/MS target sheet</title>' +
    '<style>body{font:15px/1.5 system-ui,sans-serif;max-width:1040px;margin:32px auto;padding:0 18px;color:#2e261f}' +
    'h1,h2{font-family:Georgia,serif;font-weight:500}article{border:1px solid #d9cdbb;border-radius:8px;padding:14px 20px;margin:16px 0;break-inside:avoid}' +
    '.plots{display:grid;grid-template-columns:1fr 1.4fr;gap:16px}figure{margin:8px 0}img,svg{display:block;width:100%;max-height:290px;object-fit:contain}' +
    'figcaption{color:#65584c;font-size:13px}th,td{text-align:left;padding:4px 12px;border-bottom:1px solid #eae1d4}' +
    '@media(max-width:600px){.plots{display:block}}</style><h1>MS/MS target sheet</h1><p>Study: ' + esc(state.study.label) +
    "</p><p>Review precursor identity, isolation settings, slide availability, and coordinate transfer with the instrument operator.</p>" +
    cards + "</html>";
  const url = URL.createObjectURL(new Blob([html], {type: "text/html;charset=utf-8"}));
  const a = document.createElement("a");
  a.href = url;
  a.download = "msms-target-sheet.html";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("open-study").addEventListener("click", () => $("study-dialog").showModal());
$("close-study").addEventListener("click", () => $("study-dialog").close());
$("study-form").addEventListener("submit", (e) => { e.preventDefault(); loadStudy(); });
document.querySelectorAll("[data-pick]").forEach((button) => button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    const result = await api("pick", {kind: button.dataset.pick});
    if (result.path) $(button.dataset.pick + "-path").value = result.path;
  } catch (error) { $("dialog-error").textContent = error.message; }
  finally { button.disabled = false; }
}));
$("cancel-job").addEventListener("click", async () => {
  $("cancel-job").disabled = true;
  try { await api("cancel", {}); } catch (error) { notice(error.message, true); }
});
for (const id of ["reference", "comparison", "bin-width"]) $(id).addEventListener("change", refreshRanking);
$("direction").addEventListener("click", (e) => {
  if (!e.target.dataset.direction) return;
  state.direction = e.target.dataset.direction;
  state.limit = 35;
  $("direction").querySelectorAll("button").forEach((b) => {
    const active = b.dataset.direction === state.direction;
    b.classList.toggle("active", active);
    b.setAttribute("aria-pressed", String(active));
  });
  renderRanking();
});
for (const sort of ["gap", "effect"]) $("sort-" + sort).addEventListener("click", () => {
  state.sort = sort;
  renderRanking();
});
$("stable").addEventListener("change", renderRanking);
$("find-mass").addEventListener("input", renderRanking);
$("more-rows").addEventListener("click", () => { state.limit += 35; renderRanking(); });
$("reset-view").addEventListener("click", () => {
  state.view = initialView(state.selected);
  loadDetail();
});
$("interval-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const lo = Number($("interval-lo").value), hi = Number($("interval-hi").value);
  if (lo < state.study.low || hi > state.study.high || lo >= hi)
    return notice("Use increasing bounds inside the shared mass range.", true);
  selectInterval({lo, hi});
});
$("section").addEventListener("change", setTarget);
for (const id of ["map-lo", "map-hi"]) $(id).addEventListener("change", clearMap);
$("build-map").addEventListener("click", generateMap);
for (const id of ["precursor", "isolation"]) $(id).addEventListener("change", () => {
  if (state.map && state.spot) readSpot(state.spot);
});
$("ion-map").addEventListener("click", (event) => {
  const map = state.map;
  if (!map) return;
  const rect = $("ion-map").getBoundingClientRect();
  const x = Math.floor((event.clientX - rect.left) * map.width / rect.width);
  const y = Math.floor((event.clientY - rect.top) * map.height / rect.height);
  if (x >= 0 && y >= 0 && x < map.width && y < map.height && map.values[y * map.width + x] !== null)
    readSpot({x: x + map.origin[0], y: y + map.origin[1]});
});
$("ion-map").addEventListener("keydown", (event) => {
  const delta = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[event.key];
  if (!delta || !state.map || !state.spot) return;
  event.preventDefault();
  const [dx, dy] = delta;
  const {width, height, origin, values} = state.map;
  const x = state.spot.x - origin[0] + dx, y = state.spot.y - origin[1] + dy;
  if (x >= 0 && y >= 0 && x < width && y < height && values[y * width + x] !== null)
    readSpot({x: x + origin[0], y: y + origin[1]});
});
for (const id of ["histology-image", "histology-transform"]) $(id).addEventListener("change", loadOverlay);
$("show-histology").addEventListener("change", renderMap);
$("pin").addEventListener("click", pinTarget);
$("export").addEventListener("click", exportPins);

async function initialize() {
  try {
    const defaults = await api("defaults");
    state.token = defaults.token;
    $("data-path").value = defaults.data;
    $("sheet-path").value = defaults.sheet;
    $("legacy-path").value = defaults.legacy;
    if (defaults.autoload) await loadStudy();
  } catch (error) { notice(error.message, true); }
}
initialize();
