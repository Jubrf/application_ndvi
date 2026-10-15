/* NDVI parcellaire — interface React (v5). Les calculs sont faits par le moteur Python (api/). */
const { useState, useMemo, useRef, useEffect, useCallback } = React;
const html = htm.bind(React.createElement);

/* Données de l'analyse ouverte, renseignées par setData() à chaque réponse du serveur.
   Variables de module (lues au rendu) : D = résultats interprétés, PROJ / BBOX = contours projetés. */
let D = null, day0 = new Date(), LAST_DATE = 0, PROJ = [], BBOX = [0, 0, 1, 1], GEO_KEY = null;

const PH = { nu: "#d73027", peu: "#a6d96a", bien: "#1a9850", none: "#c3c9c2" };
const PH_LABEL = { nu: "Sol nu", peu: "Couvert peu développé", bien: "Couvert bien développé", none: "Pas de courbe" };
const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
const dayDate = (k) => new Date(day0.getTime() + k * 86400000);
const pad2 = (n) => String(n).padStart(2, "0");
const fmtDay = (k) => { const d = dayDate(k); return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}/${d.getFullYear()}`; };
const isoDay = (k) => { const d = dayDate(k); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
const fmtShort = (k) => { const d = dayDate(k); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };
const fr = (v, n = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(n).replace(".", ","));
const H = (col) => (D && D.help && D.help[col]) || "";

function setData(payload, geometry) {
  const polys = Object.fromEntries(geometry.parcels.map((p) => [p.id, p.poly]));
  payload.parcels.forEach((p) => { p.poly = polys[p.id] || []; });
  D = payload;
  day0 = new Date(D.start + "T00:00:00");
  LAST_DATE = D.dates.length ? D.dates[D.dates.length - 1] : D.nDays - 1;
  if (GEO_KEY !== D.key) {
    GEO_KEY = D.key;
    PROJ = D.parcels.map((p) => p.poly.map((ring) => ring.map(merc)));
    let a = [1, 1, 0, 0];
    PROJ.forEach((rs) => rs.forEach((r) => r.forEach(([x, y]) => { a = [Math.min(a[0], x), Math.min(a[1], y), Math.max(a[2], x), Math.max(a[3], y)]; })));
    BBOX = a;
  }
}

/* ---------------------------------------------------------------- appels au serveur */
class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}
async function call(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText || "erreur " + r.status;
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { /* réponse non JSON */ }
    throw new ApiError(msg, r.status);
  }
  return r;
}
const postJSON = (path, body) => call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}
function fileName(r, fallback) {
  const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "");
  return m ? m[1] : fallback;
}
/* Réglages envoyés au serveur (format de api.main.Settings) */
const serverSettings = (s) => ({ low: s.low, high: s.high, min_clear: s.min_clear, min_pixels: s.min_pixels,
  min_weight: s.min_weight, smooth_days: s.smooth_days, grey: s.greyOn ? [...s.grey] : null });
/* Actions de l'application (renseignées par App), appelées depuis les pages */
const ACTIONS = { busy: () => false };

function curveAt(p, k) {
  const c = p.curve;
  if (!c || k < c.s || k >= c.s + c.v.length) return null;
  return c.v[k - c.s];
}
function phaseOf(v, s) {
  if (v == null) return "none";
  return v < s.low ? "nu" : v < s.high ? "peu" : "bien";
}
function lastRetained(p) {
  for (let i = p.meas.length - 1; i >= 0; i--) if (p.meas[i][2]) return p.meas[i];
  return null;
}
function inGrey(p, s) {
  const v = p.curve ? p.curve.v[p.curve.v.length - 1] : null;   // comme le moteur : courbe à la dernière mesure
  if (!s.greyOn || v == null) return false;
  return v >= s.grey[0] && v <= s.grey[1];
}

/* ---------------------------------------------------------------- carte */
const TILE = {
  ortho: (z, x, y) => `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX=${z}&TILEROW=${y}&TILECOL=${x}`,
  plan: (z, x, y) => `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/png&TILEMATRIX=${z}&TILEROW=${y}&TILECOL=${x}`,
};
const merc = ([lon, lat]) => {
  const s = Math.sin((lat * Math.PI) / 180);
  return [(lon + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
};

function MapView({ settings, day, sel, setSel, hover, setHover, visible }) {
  const box = useRef(null);
  const [size, setSize] = useState(null);
  const [view, setView] = useState(null); // {cx, cy, z}
  const [base, setBase] = useState("ortho");
  const drag = useRef(null);

  const fit = useCallback((sz = size) => {
    const [w, h] = sz;
    const bw = BBOX[2] - BBOX[0], bh = BBOX[3] - BBOX[1];
    const z = Math.log2(Math.min((w * 0.86) / (bw * 256), (h * 0.86) / (bh * 256)));
    setView({ cx: (BBOX[0] + BBOX[2]) / 2, cy: (BBOX[1] + BBOX[3]) / 2, z: Math.min(z, 17) });
  }, [size]);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => {
      const sz = [e.contentRect.width, e.contentRect.height];
      setSize(sz);
      setView((v) => v || null);
    });
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  useEffect(() => { if (!view && size && size[0] > 10) fit(size); }, [size]);

  // centrer sur la parcelle sélectionnée si elle est hors champ
  useEffect(() => {
    if (!view || !size || sel == null) return;
    const i = D.parcels.findIndex((p) => p.id === sel);
    const pts = PROJ[i].flat();
    const ws = 256 * 2 ** view.z;
    const out = pts.some(([x, y]) => {
      const sx = (x - view.cx) * ws + size[0] / 2, sy = (y - view.cy) * ws + size[1] / 2;
      return sx < 30 || sy < 30 || sx > size[0] - 30 || sy > size[1] - 30;
    });
    if (out) {
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      setView((v) => ({ ...v, cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: (Math.min(...ys) + Math.max(...ys)) / 2 }));
    }
  }, [sel]);

  if (!view || !size) return html`<div class="map" ref=${box}></div>`;
  const [w, h] = size;
  const ws = 256 * 2 ** view.z;
  const toScreen = ([x, y]) => [(x - view.cx) * ws + w / 2, (y - view.cy) * ws + h / 2];

  const onWheel = (e) => {
    e.preventDefault();
    const r = box.current.getBoundingClientRect();
    const mx = e.clientX - r.left - w / 2, my = e.clientY - r.top - h / 2;
    const nz = Math.max(10, Math.min(18.5, view.z - e.deltaY * 0.0022));
    const k = 2 ** view.z, nk = 2 ** nz;
    setView({ z: nz, cx: view.cx + mx / (256 * k) - mx / (256 * nk), cy: view.cy + my / (256 * k) - my / (256 * nk) });
  };
  const onDown = (e) => { drag.current = { x: e.clientX, y: e.clientY, v: view, moved: false }; };
  const onMove = (e) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    setView({ ...d.v, cx: d.v.cx - dx / ws, cy: d.v.cy - dy / ws });
  };
  const onUp = () => { setTimeout(() => (drag.current = null), 0); };

  // tuiles
  const tiles = [];
  if (base !== "aucun") {
    const zi = Math.min(18, Math.floor(view.z)), sc = 2 ** (view.z - zi), ts = 256 * sc, n = 2 ** zi;
    const x0 = Math.floor((view.cx * n * 256 - w / 2 / sc) / 256), y0 = Math.floor((view.cy * n * 256 - h / 2 / sc) / 256);
    const x1 = Math.floor((view.cx * n * 256 + w / 2 / sc) / 256), y1 = Math.floor((view.cy * n * 256 + h / 2 / sc) / 256);
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
      const left = (x * 256 - view.cx * n * 256) * sc + w / 2, top = (y * 256 - view.cy * n * 256) * sc + h / 2;
      tiles.push(html`<img key=${base + zi + "_" + x + "_" + y} class="tile" src=${TILE[base](zi, x, y)} alt=""
        onError=${(e) => (e.currentTarget.style.visibility = "hidden")}
        style=${{ left: left + "px", top: top + "px", width: ts + 0.5 + "px", height: ts + 0.5 + "px" }} />`);
    }
  }

  const polys = D.parcels.map((p, i) => {
    const ph = phaseOf(curveAt(p, day), settings);
    const d = PROJ[i].map((r) => "M" + r.map(toScreen).map(([x, y]) => x.toFixed(1) + "," + y.toFixed(1)).join("L") + "Z").join("");
    const isSel = p.id === sel, isHov = p.id === hover, dim = !visible.has(p.id);
    return html`<path key=${p.id} d=${d} fill=${PH[ph]} fill-opacity=${dim ? 0.18 : 0.78}
      stroke=${isSel ? "#10201a" : isHov ? "#2f6f8f" : "#ffffff"} stroke-width=${isSel ? 3 : isHov ? 2.5 : 1}
      class="parcel" onMouseEnter=${() => setHover(p.id)} onMouseLeave=${() => setHover(null)}
      onClick=${() => { if (!drag.current?.moved) setSel(p.id); }} />`;
  });
  const hp = hover ? D.parcels.find((p) => p.id === hover) : null;
  const hv = hp ? curveAt(hp, day) : null;

  return html`<div class="map" ref=${box} onWheel=${onWheel} onPointerDown=${onDown} onPointerMove=${onMove}
      onPointerUp=${onUp} onPointerLeave=${onUp}>
    <div class="tiles">${tiles}</div>
    <svg class="overlay" width=${w} height=${h}>${polys}</svg>
    <div class="map-date"><strong>${fmtDay(day)}</strong><span>phase de la courbe lissée ce jour-là</span></div>
    ${hp && html`<div class="map-tip"><b>${hp.id}</b> · ${fr(hp.ha)} ha<br/>NDVI lissé ${fr(hv)} — ${PH_LABEL[phaseOf(hv, settings)]}</div>`}
    <div class="map-tools">
      <div class="seg">
        ${[["ortho", "Photo"], ["plan", "Plan"], ["aucun", "Sans fond"]].map(([k, l]) =>
          html`<button key=${k} class=${base === k ? "on" : ""} onClick=${() => setBase(k)}>${l}</button>`)}
      </div>
      <button class="tool" title="Voir toutes les parcelles" onClick=${() => fit()}>Recentrer</button>
    </div>
    <div class="attrib">${base !== "aucun" ? "Fond : © IGN Géoplateforme" : ""}</div>
  </div>`;
}

/* ---------------------------------------------------------------- fiche parcelle */
function ParcelChart({ p, settings, day, setDay, W = 400 }) {
  const H = 230, L = 30, R = 8, T = 8, B = 32;
  const n = D.nDays, X = (k) => L + (k / (n - 1)) * (W - L - R), Y = (v) => T + (1 - v) * (H - T - B);
  const [hov, setHov] = useState(null);
  const c = p.curve;
  const segs = [];
  if (c) {
    let cur = null;
    c.v.forEach((v, i) => {
      const k = c.s + i, unc = c.u.some(([a, b]) => k >= a && k <= b);
      if (v == null) return;
      const pt = X(k).toFixed(1) + "," + Y(Math.max(-0.05, v)).toFixed(1);
      if (!cur || cur.unc !== unc) { if (cur) cur.pts.push(pt); cur = { unc, pts: [pt] }; segs.push(cur); } else cur.pts.push(pt);
    });
  }
  const months = [];
  for (let k = 0; k < n; k++) if (dayDate(k).getDate() === 1) months.push(k);
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const k = Math.round(((e.clientX - r.left) * (W / r.width) - L) / (W - L - R) * (n - 1));
    setHov(Math.max(0, Math.min(n - 1, k)));
  };
  const hm = hov != null ? p.meas.filter((m) => m[1] != null).reduce((best, m) => (Math.abs(m[0] - hov) < 4 && (!best || Math.abs(m[0] - hov) < Math.abs(best[0] - hov)) ? m : best), null) : null;
  return html`<div class="chartwrap">
    <svg viewBox=${`0 0 ${W} ${H}`} class="chart" onMouseMove=${onMove} onMouseLeave=${() => setHov(null)}
        onClick=${() => hov != null && setDay(hov)}>
      <rect x=${L} y=${Y(settings.low)} width=${W - L - R} height=${Y(-0.05) - Y(settings.low)} fill="#d73027" opacity="0.07" />
      <rect x=${L} y=${Y(settings.high)} width=${W - L - R} height=${Y(settings.low) - Y(settings.high)} fill="#a6d96a" opacity="0.13" />
      <rect x=${L} y=${Y(1)} width=${W - L - R} height=${Y(settings.high) - Y(1)} fill="#1a9850" opacity="0.08" />
      ${[0, 0.25, 0.5, 0.75, 1].map((v) => html`<text key=${v} x=${L - 5} y=${Y(v) + 3} class="ax" text-anchor="end">${fr(v, v % 0.5 ? 2 : 1)}</text>`)}
      ${[settings.low, settings.high].map((t) => html`<line key=${t} x1=${L} x2=${W - R} y1=${Y(t)} y2=${Y(t)} class="thr" />`)}
      ${months.map((k) => html`<g key=${k}><line x1=${X(k)} x2=${X(k)} y1=${T} y2=${H - B} class="grid" />
        <text x=${X(k) + 3} y=${H - 6} class="ax">${MONTHS[dayDate(k).getMonth()]}</text></g>`)}
      ${segs.map((s, i) => html`<polyline key=${i} points=${s.pts.join(" ")} fill="none" stroke="#2a78d6"
          stroke-width="2.2" stroke-dasharray=${s.unc ? "4 3" : ""} />`)}
      ${p.meas.filter((m) => m[1] != null).map((m) => m[2]
        ? html`<circle key=${m[0]} cx=${X(m[0])} cy=${Y(Math.max(-0.05, m[1]))} r="3.6" fill="#2a78d6" stroke="#fff" stroke-width="1" />`
        : html`<circle key=${m[0]} cx=${X(m[0])} cy=${Y(Math.max(-0.05, m[1]))} r="3.2" fill="#fff" stroke="#8a8f88" stroke-width="1.2" />`)}
      <line x1=${X(day)} x2=${X(day)} y1=${T} y2=${H - B} class="cursor" />
      ${hov != null && html`<line x1=${X(hov)} x2=${X(hov)} y1=${T} y2=${H - B} class="hovline" />`}
    </svg>
    ${hov != null && html`<div class="ctip" style=${{ left: (X(hov) / W) * 100 + "%" }}>
      <b>${fmtDay(hov)}</b><br/>NDVI lissé ${fr(curveAt(p, hov))}
      ${hm && html`<br/>Mesure du ${fmtShort(hm[0])} : ${fr(hm[1])} ${hm[2] ? "(retenue" + (hm[6] ? " : " + D.motifs[hm[6] - 1].toLowerCase() : "") + ")" : "(écartée" + (hm[6] ? " : " + D.motifs[hm[6] - 1].toLowerCase() : "") + ")"}`}
      <br/><span class="muted">Cliquer pour placer le curseur ici</span></div>`}
  </div>`;
}

function WeatherStrip({ day, W = 400 }) {
  const H = 70, L = 30, R = 8, n = D.nDays, X = (k) => L + (k / (n - 1)) * (W - L - R);
  const wx = D.weather, maxR = Math.max(10, ...wx.rain.filter((v) => v != null));
  const sm = wx.sm.map((v, k) => (v == null ? null : [X(k), 66 - ((v - 0.1) / 0.4) * 34])).filter(Boolean);
  return html`<svg viewBox=${`0 0 ${W} ${H}`} class="chart wx">
    <text x=${L - 5} y="12" class="ax" text-anchor="end">pluie</text>
    <text x=${L - 5} y="56" class="ax" text-anchor="end">sol</text>
    ${wx.rain.map((v, k) => v ? html`<rect key=${k} x=${X(k) - 1} y=${28 - (v / maxR) * 26} width="2" height=${(v / maxR) * 26} fill="#3b7dd8" />` : null)}
    ${wx.tmin.map((v, k) => v != null && v <= -2 ? html`<text key=${"g" + k} x=${X(k)} y="8" class="frost">▼</text>` : null)}
    <polyline points=${sm.map((q) => q.join(",")).join(" ")} fill="none" stroke="#8a5a2b" stroke-width="1.6" />
    <line x1=${X(day)} x2=${X(day)} y1="0" y2=${H} class="cursor" />
  </svg>`;
}

/* ================================================================ version 2 : organisation de la v4 Streamlit */
const lastCurve = (p) => (p.curve ? p.curve.v[p.curve.v.length - 1] : null);
const phaseFin = (p, s) => phaseOf(lastCurve(p), s);
const MOTIF = (m) => (m[6] ? D.motifs[m[6] - 1] : "");
const CONF_ORDER = ["Bonne", "Moyenne", "Faible", "Aucune mesure"];

/* Image « NDVI et phases » (comme la v4.1) : courbe au-dessus de la barre, mois repérés */
function EvoCell({ p, s, w = 300, h = 58 }) {
  const n = D.nDays, X = (k) => (k / (n - 1)) * w, c = p.curve;
  const months = [];
  for (let k = 1; k < n; k++) if (dayDate(k).getDate() === 1) months.push(k);
  const Yc = (v) => 2 + (1 - Math.min(1, Math.max(0, v))) * 30;
  let path = "", bars = [];
  if (c) {
    path = c.v.map((v, i) => (v == null ? null : X(c.s + i).toFixed(1) + "," + Yc(v).toFixed(1))).filter(Boolean).join(" ");
    let a = 0;
    for (let i = 1; i <= c.v.length; i++) {
      if (i === c.v.length || phaseOf(c.v[i], s) !== phaseOf(c.v[a], s)) {
        const unc = c.u.some(([u0, u1]) => c.s + a >= u0 && c.s + a <= u1);
        bars.push(html`<rect key=${a} x=${X(c.s + a)} y="36" width=${Math.max(0.8, X(c.s + i) - X(c.s + a))} height="11"
          fill=${PH[phaseOf(c.v[a], s)]} opacity=${unc ? 0.5 : 1} />`);
        a = i;
      }
    }
  }
  return html`<svg width=${w} height=${h} class="evo">
    ${[s.low, s.high].map((t) => html`<line key=${t} x1="0" x2=${w} y1=${Yc(t)} y2=${Yc(t)} stroke="#c9cfc8" stroke-dasharray="2 3" />`)}
    ${months.map((k) => html`<line key=${k} x1=${X(k)} x2=${X(k)} y1="2" y2="47" stroke="#fff" stroke-opacity="0.9" />`)}
    <rect x="0" y="36" width=${w} height="11" fill=${PH.none} />${bars}
    ${months.map((k) => html`<line key=${"t" + k} x1=${X(k)} x2=${X(k)} y1="2" y2="34" stroke="#e1e6e0" />`)}
    <polyline points=${path} fill="none" stroke="#1a6f45" stroke-width="1.5" />
    ${[0, ...months].map((k, i, arr) => {
      const e = i + 1 < arr.length ? arr[i + 1] : n;
      return e - k > 12 ? html`<text key=${"m" + k} x=${(X(k) + X(e)) / 2} y="57" class="evo-m">${MONTHS[dayDate(k).getMonth()].replace(".", "")}</text>` : null;
    })}
  </svg>`;
}

/* ---------------------------------------------------------------- réglages (panneau) */
const SMOOTH_OPTS = [3, 4, 6, 8, 10, 15];
function Sidebar({ s, setS, info, reset, busy }) {
  const set = (patch) => setS({ ...s, ...patch });
  const setLow = (v) => set({ low: Math.min(v, s.high - 0.01) });
  const setHigh = (v) => set({ high: Math.max(v, s.low + 0.01) });
  const si = Math.max(0, SMOOTH_OPTS.indexOf(s.smooth_days));
  const L = info.locked;
  return html`<aside class="side">
    <h3>Interprétation</h3>
    <p class="note">Seuils et zone grise : couleurs mises à jour aussitôt. Tous les réglages sont recalculés par le moteur Python (une à quelques secondes)${busy ? " — recalcul en cours…" : "."}</p>
    <label title="Sous ce seuil : sol nu.">Seuil sol nu / couvert <b>${fr(s.low)}</b>
      <input type="range" min="0" max="1" step="0.01" value=${s.low} onInput=${(e) => setLow(+e.target.value)} /></label>
    <label title="Au-dessus de ce seuil : couvert bien développé.">Seuil couvert bien développé <b>${fr(s.high)}</b>
      <input type="range" min="0" max="1" step="0.01" value=${s.high} onInput=${(e) => setHigh(+e.target.value)} /></label>
    ${s.high - s.low < 0.05 && html`<p class="note warn">Les deux seuils sont très proches.</p>`}
    <label class="check" title="Repère les parcelles dont le NDVI en fin de période tombe dans une plage de doute.">
      <input type="checkbox" checked=${s.greyOn} onChange=${(e) => set({ greyOn: e.target.checked })} /> Zone grise « à vérifier »</label>
    <label class=${s.greyOn ? "" : "off"} title="NDVI lissé à la dernière mesure retenue compris dans cette plage (bornes incluses).">Bornes <b>${fr(s.grey[0])} – ${fr(s.grey[1])}</b>
      <input type="range" min="0" max="1" step="0.01" value=${s.grey[0]} disabled=${!s.greyOn} onInput=${(e) => set({ grey: [Math.min(+e.target.value, s.grey[1]), s.grey[1]] })} />
      <input type="range" min="0" max="1" step="0.01" value=${s.grey[1]} disabled=${!s.greyOn} onInput=${(e) => set({ grey: [s.grey[0], Math.max(+e.target.value, s.grey[0])] })} /></label>
    <label title="Mesure non exploitable si la part de pixels clairs de la parcelle est inférieure.">Part minimale de pixels clairs <b>${s.min_clear} %</b>
      <input type="range" min="0" max="100" step="5" value=${s.min_clear} onInput=${(e) => set({ min_clear: +e.target.value })} /></label>
    <label>Nombre minimal de pixels utilisés
      <input type="number" class="num" min="1" max="500" value=${s.min_pixels}
        onChange=${(e) => { const v = Math.round(+e.target.value); if (v >= 1 && v <= 500) set({ min_pixels: v }); }} /></label>
    <label title="Les mesures sous ce poids sont écartées de la courbe.">Poids de fiabilité minimal <b>${fr(s.min_weight)}</b>
      <input type="range" min="0" max="1" step="0.05" value=${s.min_weight} onInput=${(e) => set({ min_weight: +e.target.value })} /></label>
    <label title="Plus la valeur est grande, plus la courbe est lisse, mais plus elle réagit tard aux changements.">Lissage de la courbe <b>${s.smooth_days} jours</b>
      <input type="range" min="0" max=${SMOOTH_OPTS.length - 1} step="1" value=${si} onInput=${(e) => set({ smooth_days: SMOOTH_OPTS[+e.target.value] })} /></label>
    <button class="tool" onClick=${reset}>Revenir aux réglages du fichier</button>
    <h3 class="lock">Calcul, verrouillé</h3>
    <p class="note">Fixé par l'analyse ouverte : buffer ${L.buffer_m} m, Cloud Score+ ${fr(L.cs_threshold)}, marge nuages ${L.cloud_buffer_m} m, valeurs aberrantes ${fr(L.iqr_k, 1)} × IQR.</p>
    <p class="note version">Interface React v5 (étape 1) · moteur ${info.server_version}</p>
  </aside>`;
}

/* ---------------------------------------------------------------- image satellite (étape 2) */
function ImageBlock() {
  return html`<div class="box">
    <h2>Image satellite</h2>
    <div class="imgph small"><span>Vignettes Sentinel-2 (couleurs naturelles et infrarouge) : étape 2, avec la connexion Earth Engine.</span></div>
  </div>`;
}

/* ---------------------------------------------------------------- tableau triable générique */
function SortTable({ cols, rows, onRow, sel, rowH, sticky = 1 }) {
  const [sort, setSort] = useState({ k: null, dir: 1 });
  const sorted = useMemo(() => {
    if (!sort.k) return rows;
    const c = cols.find((x) => x.k === sort.k);
    const val = c.sort || ((r) => r[c.k]);
    return [...rows].sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (va > vb ? 1 : va < vb ? -1 : 0) * sort.dir;
    });
  }, [rows, sort, cols]);
  return html`<div class="tablewrap"><table class="tbl">
    <thead><tr>${cols.map((c, i) => html`<th key=${c.k} class=${(i < sticky ? "stk " : "") + (c.num ? "num" : "")}
        title=${c.help || ""} onClick=${() => c.nosort || setSort({ k: c.k, dir: sort.k === c.k ? -sort.dir : 1 })}>
        ${c.label}${sort.k === c.k ? (sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`)}</tr></thead>
    <tbody>${sorted.map((r) => html`<tr key=${r.id} class=${r.id === sel ? "sel" : ""} onClick=${() => onRow && onRow(r.id)}
        style=${rowH ? { height: rowH + "px" } : null}>
      ${cols.map((c, i) => html`<td key=${c.k} class=${(i < sticky ? "stk " : "") + (c.num ? "num " : "") + (c.cls ? c.cls(r) : "")}
          style=${c.style ? c.style(r) : null}>${c.render ? c.render(r) : r[c.k]}</td>`)}</tr>`)}</tbody>
  </table></div>`;
}

/* ---------------------------------------------------------------- vue d'ensemble */
function Overview({ s, go, sel }) {
  const [phase, setPhase] = useState("all");
  const [conf, setConf] = useState(new Set(CONF_ORDER));
  const [q, setQ] = useState("");
  const [onlyGrey, setOnlyGrey] = useState(false);
  const [view, setView] = useState("evo");
  const counts = useMemo(() => {
    const c = { nu: 0, peu: 0, bien: 0, none: 0 };
    D.parcels.forEach((p) => c[phaseFin(p, s)]++);
    return c;
  }, [s]);
  const nGrey = s.greyOn ? D.parcels.filter((p) => inGrey(p, s)).length : 0;
  const shown = D.parcels.filter((p) => (phase === "all" || phaseFin(p, s) === phase) && conf.has(p.conf)
    && (!q || p.id.toLowerCase().includes(q.toLowerCase())) && (!onlyGrey || inGrey(p, s)));
  const rows = shown.map((p) => ({ id: p.id, p }));
  const phCell = (r) => { const k = phaseFin(r.p, s); return html`<span class="phase"><i style=${{ background: PH[k] }}></i>${PH_LABEL[k]}</span>`; };
  const base = [
    { k: "id", label: "Parcelle", render: (r) => html`<b>${r.id}</b>${inGrey(r.p, s) ? html`<i class="flag" title="À vérifier">?</i>` : ""}` },
    { k: "ha", label: "Surface (ha)", num: 1, sort: (r) => r.p.ha, render: (r) => fr(r.p.ha) },
  ];
  const evoCols = [...base,
    { k: "evo", label: `NDVI et phases (${fmtDay(0)} → ${fmtDay(D.nDays - 1)})`, nosort: 1, render: (r) => html`<${EvoCell} p=${r.p} s=${s} />` },
    { k: "nf", label: "NDVI fin", num: 1, sort: (r) => r.p.ndvi_fin, render: (r) => fr(r.p.ndvi_fin) },
    { k: "pf", label: "Phase fin", sort: (r) => phaseFin(r.p, s), render: phCell },
    { k: "lf", label: "Lecture fin", sort: (r) => r.p.lect_fin, render: (r) => r.p.lect_fin },
    { k: "cf", label: "Confiance", sort: (r) => CONF_ORDER.indexOf(r.p.conf), render: (r) => r.p.conf },
    { k: "br", label: "Baisse rapide", sort: (r) => r.p.drops, render: (r) => r.p.drops },
  ];
  const synthCols = [...base,
    { k: "pf", label: "Phase fin", sort: (r) => phaseFin(r.p, s), render: phCell },
    { k: "nf", label: "NDVI fin", num: 1, sort: (r) => r.p.ndvi_fin, render: (r) => fr(r.p.ndvi_fin) },
    { k: "lf", label: "Lecture fin", sort: (r) => r.p.lect_fin, render: (r) => r.p.lect_fin },
    { k: "es", label: "État du sol fin", sort: (r) => r.p.etat_fin, render: (r) => r.p.etat_fin },
    { k: "cf", label: "Confiance", sort: (r) => CONF_ORDER.indexOf(r.p.conf), render: (r) => r.p.conf },
    { k: "ch", label: "Chronologie", nosort: 1, cls: () => "wide", render: (r) => r.p.chrono },
    { k: "br", label: "Baisses rapides", sort: (r) => r.p.drops, render: (r) => r.p.drops },
    { k: "nr", label: "Mesures retenues", num: 1, sort: (r) => r.p.n_ret, render: (r) => r.p.n_ret },
    { k: "ne", label: "Mesures écartées", num: 1, sort: (r) => r.p.n_ecart, render: (r) => r.p.n_ecart },
    { k: "tr", label: "Plus long trou (j)", num: 1, sort: (r) => r.p.trou, render: (r) => r.p.trou },
    { k: "dm", label: "Dernière mesure", sort: (r) => r.p.last, render: (r) => r.p.last ? r.p.last.split("-").reverse().join("/") : "—" },
    { k: "js", label: "Jours sans mesure fin", num: 1, sort: (r) => r.p.jsm, render: (r) => r.p.jsm ?? "—" },
  ];
  const HELP_COL = { nf: "NDVI_fin", pf: "Phase_fin", lf: "Lecture_fin", es: "Etat_sol_fin", cf: "Confiance",
    ch: "Chronologie", br: "Baisses_rapides", nr: "Mesures_retenues", ne: "Mesures_ecartees", tr: "Plus_long_trou_j",
    dm: "Derniere_mesure", js: "Jours_sans_mesure_fin", ha: "Surface_ha" };
  [...evoCols, ...synthCols].forEach((c) => { if (HELP_COL[c.k]) c.help = H(HELP_COL[c.k]); });
  if (s.greyOn) synthCols.splice(4, 0, { k: "av", label: "À vérifier", help: H("A_verifier"),
    sort: (r) => (inGrey(r.p, s) ? 0 : 1), render: (r) => (r.p.curve ? (inGrey(r.p, s) ? "Oui" : "Non") : "—") });
  const tint = (v) => v == null ? { color: "#9aa39c" } : v < s.low ? { background: "#f6d5d2", color: "#7a1c14" }
    : v < s.high ? { background: "#e4f2d0", color: "#3d5a12" } : { background: "#cdebd8", color: "#0e5a31" };
  const valCols = [base[0], ...D.dates.map((k) => ({
    k: "d" + k, label: fmtShort(k), num: 1,
    sort: (r) => { const m = r.p.meas.find((x) => x[0] === k && x[2]); return m ? m[1] : null; },
    style: (r) => { const m = r.p.meas.find((x) => x[0] === k && x[2]); return tint(m ? m[1] : null); },
    render: (r) => { const m = r.p.meas.find((x) => x[0] === k && x[2]); return m ? fr(m[1]) : "—"; },
  }))];
  const cards = [["all", "Toutes les parcelles", D.parcels.length], ["nu", PH_LABEL.nu, counts.nu], ["peu", PH_LABEL.peu, counts.peu],
    ["bien", PH_LABEL.bien, counts.bien], ["none", "Aucune courbe", counts.none]];
  return html`<div class="page">
    <h1>Vue d'ensemble des parcelles</h1>
    <p class="cap">Du ${fmtDay(0)} au ${fmtDay(D.nDays - 1)}. Phase de la courbe lissée à la dernière mesure retenue de chaque parcelle. Cliquer sur une carte filtre le tableau.</p>
    <div class="cards">
      ${cards.map(([k, l, n]) => html`<button key=${k} class=${"card " + (phase === k ? "on" : "")} onClick=${() => setPhase(k)}>
        <span>${l}</span><b style=${{ color: k === "all" || k === "none" ? "var(--ink)" : PH[k] === "#a6d96a" ? "#5f8f22" : PH[k] }}>${n}</b>
        ${k !== "all" && html`<i class="cardbar" style=${{ width: (n / D.parcels.length) * 100 + "%", background: PH[k] }}></i>`}</button>`)}
      ${s.greyOn && html`<button class=${"card " + (onlyGrey ? "on" : "")} onClick=${() => setOnlyGrey(!onlyGrey)}>
        <span>À vérifier</span><b style=${{ color: "var(--warn)" }}>${nGrey}</b></button>`}
    </div>
    <div class="filters">
      <div class="pills"><span>Confiance</span>${CONF_ORDER.filter((c) => D.parcels.some((p) => p.conf === c)).map((c) =>
        html`<button key=${c} class=${"pill2 " + (conf.has(c) ? "on" : "")} onClick=${() => {
          const n = new Set(conf); n.has(c) ? n.delete(c) : n.add(c); setConf(n); }}>${c}</button>`)}</div>
      <input class="search" placeholder="Rechercher une parcelle" value=${q} onInput=${(e) => setQ(e.target.value)} />
    </div>
    <div class="segrow">
      <div class="seg big">${[["evo", "Évolution des phases"], ["val", "Valeurs NDVI par date"], ["syn", "Synthèse détaillée"]].map(([k, l]) =>
        html`<button key=${k} class=${view === k ? "on" : ""} onClick=${() => setView(k)}>${l}</button>`)}</div>
      <span class="cap">${shown.length} parcelle(s) sur ${D.parcels.length}. Cliquer sur une ligne ouvre la fiche ; cliquer sur un en-tête trie.</span>
    </div>
    <${SortTable} key=${view} cols=${view === "evo" ? evoCols : view === "val" ? valCols : synthCols} rows=${rows}
      onRow=${(id) => go("fiche", id)} sel=${sel} rowH=${view === "evo" ? 66 : null} />
  </div>`;
}

function ReadingBox({ p, s }) {
  const last = lastRetained(p);
  const lect = last ? D.lectures[last[5]] : "—";
  const key = !last ? null : last[1] >= s.low ? "couvert" : { "Levée possible": "levee", "Indéterminé (sol humide)": "humide",
    "Indéterminé (humidité à vérifier)": "humide", "Résidus probables": "residus", "Sol nu probable": "solnu", "Indéterminé": "indet" }[lect];
  const L = fr(s.low);
  const lines = [["couvert", html`NDVI ≥ ${L} → <b>couvert ou culture présent</b> (maïs jaunissant compris)`]];
  const sub = [["levee", html`en hausse (+ 0,05 en 14 j) → <b>levée possible</b>`],
    ["humide", html`sol humide ¹ → <b>indéterminé</b>, NDTI non lisible`],
    ["residus", html`sol sec, NDTI ≥ 0,10 → <b>résidus probables</b> (chaumes, cannes)`],
    ["solnu", html`sol sec, NDTI ≤ 0,05 → <b>sol nu probable</b>`],
    ["indet", html`sol sec, NDTI entre les deux → <b>indéterminé</b>`]];
  const li = ([k, t]) => html`<li key=${k} class=${k === key ? "hit" : ""}>${t}</li>`;
  return html`<div class="box">
    <h2>Aide à la lecture</h2>
    <ul class="rules">
      ${s.greyOn && html`<li class=${inGrey(p, s) ? "hitw" : ""}>${fr(s.grey[0])} ≤ NDVI fin ≤ ${fr(s.grey[1])} → <b>à vérifier</b> (zone grise)</li>`}
      ${lines.map(li)}<li>NDVI ${"<"} ${L} :<ul>${sub.map(li)}</ul></li>
    </ul>
    ${last && html`<p class="note">Dernière mesure retenue (${fmtDay(last[0])}) : NDVI ${fr(last[1], 3)}, NDTI ${fr(last[3], 3)}, B11 ${fr(last[7], 3)}${last[10] != null ? ", pluie 3 j " + fr(last[10], 1) + " mm" : ""}${last[4] ? ", sol " + D.etats[last[4]].toLowerCase() : ""}.</p>`}
    <p class="note">¹ Sol humide : B11 ${"<"} 0,15, ou pluie des 3 jours ≥ 10 mm, de la veille ≥ 5 mm, humidité du sol ≥ 0,40. Repères indicatifs, à calibrer sur le terrain.</p>
  </div>`;
}

/* ---------------------------------------------------------------- carte */
function MapPage({ s, sel, setSel, go, dl }) {
  const [day, setDay] = useState(LAST_DATE);
  const [hover, setHover] = useState(null);
  const all = useMemo(() => new Set(D.parcels.map((p) => p.id)), []);
  const counts = { nu: 0, peu: 0, bien: 0, none: 0 };
  D.parcels.forEach((p) => counts[phaseOf(curveAt(p, day), s)]++);
  const i = D.dates.indexOf(day);
  const P = D.parcels.find((p) => p.id === sel);
  return html`<div class="page mappage">
    <div class="maphead">
      <h1>Carte des phases au ${fmtDay(day)}</h1>
      <div class="legend">${["nu", "peu", "bien", "none"].map((k) => html`<span key=${k}><i style=${{ background: PH[k] }}></i>${PH_LABEL[k]} (${counts[k]})</span>`)}</div>
      <div class="actions"><button class="tool" disabled=${!!dl} title="Pour Google My Maps ou Google Earth" onClick=${() => ACTIONS.mapFile("kml", day)}>${dl === "Carte KML" ? "Préparation…" : "Carte KML"}</button><button class="tool" disabled=${!!dl} title="S'ouvre dans un navigateur" onClick=${() => ACTIONS.mapFile("html", day)}>${dl === "Carte HTML" ? "Préparation…" : "Carte HTML"}</button></div>
    </div>
    <div class="dates">
      <button class="tool" onClick=${() => i > 0 && setDay(D.dates[i - 1])}>◀</button>
      <input type="range" min="0" max=${D.dates.length - 1} value=${i < 0 ? D.dates.length - 1 : i} onInput=${(e) => setDay(D.dates[+e.target.value])} />
      <button class="tool" onClick=${() => i < D.dates.length - 1 && setDay(D.dates[i + 1])}>▶</button>
      <span class="cap">Image ${i + 1} sur ${D.dates.length}. Le curseur recolore la carte instantanément.</span>
    </div>
    <div class="mapgrid">
      <${MapView} settings=${s} day=${day} sel=${sel} setSel=${setSel} hover=${hover} setHover=${setHover} visible=${all} />
      ${P && html`<div class="box mini">
        <h2>${P.id}</h2>
        <p class="cap">${fr(P.ha)} ha, NDVI lissé au ${fmtShort(day)} : <b>${fr(curveAt(P, day))}</b></p>
        <${EvoCell} p=${P} s=${s} w=${300} />
        <button class="primary" onClick=${() => go("fiche", P.id)}>Ouvrir la fiche</button>
      </div>`}
    </div>
  </div>`;
}

/* ================================================================ version 3 : liste + fiche côte à côte */
const SORTS = [
  ["nom", "Nom", (p) => p.id],
  ["nfa", "NDVI fin croissant", (p) => p.ndvi_fin],
  ["nfd", "NDVI fin décroissant", (p) => (p.ndvi_fin == null ? null : -p.ndvi_fin)],
  ["ha", "Surface décroissante", (p) => -p.ha],
  ["conf", "Confiance", (p) => CONF_ORDER.indexOf(p.conf)],
  ["lect", "Lecture fin", (p) => p.lect_fin],
];

function useWidth(ref) {
  const [w, setW] = useState(0);
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return w;
}

function ParcelList({ s, ids, all, sel, setSel, f, setF, curves, setCurves, collapsed, setCollapsed }) {
  const box = useRef(null);
  const w = useWidth(box);
  const counts = useMemo(() => {
    const c = { nu: 0, peu: 0, bien: 0, none: 0 };
    D.parcels.forEach((p) => c[phaseFin(p, s)]++);
    return c;
  }, [s]);
  const nGrey = s.greyOn ? D.parcels.filter((p) => inGrey(p, s)).length : 0;
  useEffect(() => {
    const el = box.current && box.current.querySelector(".li.on");
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [sel, collapsed, w > 0, curves]);
  if (collapsed) return html`<aside class="list collapsed" ref=${box}>
    <button class="tool icon" title="Afficher la liste" onClick=${() => setCollapsed(false)}>☰</button>
    <span class="vert">${ids.indexOf(sel) + 1} / ${ids.length}</span>
  </aside>`;
  const chips = [["all", "Toutes", D.parcels.length], ["nu", "Sol nu", counts.nu], ["peu", "Peu dév.", counts.peu], ["bien", "Bien dév.", counts.bien]];
  if (counts.none) chips.push(["none", "Sans courbe", counts.none]);
  return html`<aside class="list" ref=${box}>
    <div class="lhead">
      <div class="lrow">
        <input class="search" placeholder="Rechercher" value=${f.q} onInput=${(e) => setF({ ...f, q: e.target.value })} />
        <button class="tool icon" title="Replier la liste" onClick=${() => setCollapsed(true)}>⟨</button>
      </div>
      <div class="chips">
        ${chips.map(([k, l, n]) => html`<button key=${k} class=${"chip " + (f.phase === k ? "on" : "")} onClick=${() => setF({ ...f, phase: k })}>
          ${k !== "all" && html`<i style=${{ background: PH[k] }}></i>`}${l} <b>${n}</b></button>`)}
        ${s.greyOn && html`<button class=${"chip " + (f.grey ? "on" : "")} onClick=${() => setF({ ...f, grey: !f.grey })}>
          <i class="q">?</i>À vérifier <b>${nGrey}</b></button>`}
      </div>
      <div class="lrow small">
        <span>Confiance</span>
        ${CONF_ORDER.filter((c) => D.parcels.some((p) => p.conf === c)).map((c) => html`<button key=${c}
          class=${"pill2 " + (f.conf.has(c) ? "on" : "")} onClick=${() => { const n = new Set(f.conf); n.has(c) ? n.delete(c) : n.add(c); setF({ ...f, conf: n }); }}>${c}</button>`)}
      </div>
      <div class="lrow small">
        <select class="sel" value=${f.sort} onChange=${(e) => setF({ ...f, sort: e.target.value })}>
          ${SORTS.map(([k, l]) => html`<option key=${k} value=${k}>Tri : ${l}</option>`)}
        </select>
        <label class="check"><input type="checkbox" checked=${curves} onChange=${(e) => setCurves(e.target.checked)} /> Courbes</label>
      </div>
      <div class="cap">${ids.length} parcelle(s) sur ${D.parcels.length} · ↑ ↓ pour naviguer</div>
    </div>
    <div class="lbody">
      ${ids.map((id) => {
        const p = all[id], k = phaseFin(p, s);
        return html`<button key=${id} class=${"li " + (id === sel ? "on" : "")} onClick=${() => setSel(id)}>
          <div class="l1">
            <i class="dot" style=${{ background: PH[k] }}></i>
            <b>${id}</b>${inGrey(p, s) ? html`<i class="flag" title="À vérifier">?</i>` : ""}
            <span class="ha">${fr(p.ha)} ha</span>
            <span class="nf" title="NDVI lissé en fin de période">${fr(p.ndvi_fin)}</span>
          </div>
          ${curves && w > 0 && html`<${EvoCell} p=${p} s=${s} w=${Math.max(160, w - 30)} />`}
          <div class="l2">${p.lect_fin && p.lect_fin !== "—" ? p.lect_fin : PH_LABEL[k]} · ${p.conf.toLowerCase()}${p.drops !== "—" ? " · baisse rapide" : ""}</div>
        </button>`;
      })}
      ${ids.length === 0 && html`<p class="cap pad">Aucune parcelle ne correspond aux filtres.</p>`}
    </div>
  </aside>`;
}

function Detail({ s, sel, setSel, ids, showMap, setShowMap, visible }) {
  const p = D.parcels.find((x) => x.id === sel);
  const i = ids.indexOf(sel);
  const step = (d) => ids.length && setSel(ids[((i < 0 ? 0 : i) + d + ids.length) % ids.length]);
  const [day, setDay] = useState(LAST_DATE);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    const k = (e) => {
      if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); step(1); }
      if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });
  const det = useRef(null);
  useEffect(() => { if (det.current) det.current.scrollTop = 0; }, [sel]);
  const ph = phaseFin(p, s);
  const ndti = p.meas.filter((m) => m[2] && m[1] < s.low && m[3] != null);
  const cbox = useRef(null);
  const cw = useWidth(cbox);
  const W = cw > 50 ? Math.round(cw) : 400, n = D.nDays, X = (k) => 30 + (k / (n - 1)) * (W - 38);
  const lect = p.lect_fin && p.lect_fin !== "—" ? p.lect_fin : null;
  return html`<section class="detail" ref=${det}>
    <div class="dhead">
      <div class="ftitle">
        <h1>${p.id}</h1>
        <span class="pill" style=${{ background: PH[ph], color: ph === "peu" ? "var(--ink)" : "#fff" }}>${PH_LABEL[ph]} en fin de période</span>
        ${lect && html`<span class="pill lect">${lect}</span>`}
        ${inGrey(p, s) && html`<span class="pill warnp">À vérifier (zone grise)</span>`}
        <span class="pill conf">Confiance ${p.conf.toLowerCase()}</span>
        <span class="cap">${fr(p.ha)} ha</span>
      </div>
      <div class="dnav">
        <span class="cap">${i < 0 ? "hors filtres" : `${i + 1} / ${ids.length}`}</span>
        <button class="tool" onClick=${() => step(-1)} title="Parcelle précédente (↑)">▲</button>
        <button class="tool" onClick=${() => step(1)} title="Parcelle suivante (↓)">▼</button>
        <button class=${"tool " + (showMap ? "act" : "")} onClick=${() => setShowMap(!showMap)}>${showMap ? "Masquer la carte" : "Afficher la carte"}</button>
      </div>
    </div>
    <div class="metrics">
      <div><span>NDVI lissé en fin de période</span><b>${fr(p.ndvi_fin)}</b></div>
      <div><span>Mesures retenues</span><b>${p.n_ret} / ${D.dates.length}</b></div>
      <div><span>Plus long trou sans mesure</span><b>${p.trou} j</b></div>
      <div><span>Baisse rapide</span><b class="small">${p.drops === "—" ? "aucune" : p.drops}</b></div>
    </div>
    ${showMap && html`<div class="mappanel">
      <${MapView} settings=${s} day=${day} sel=${sel} setSel=${setSel} hover=${hover} setHover=${setHover} visible=${visible} />
      <p class="cap">Phases au ${fmtDay(day)} (date du repère bleu de la courbe). Clic sur une parcelle : ouvre sa fiche. Parcelles hors filtres en transparence.</p>
    </div>`}
    <div class="fgrid">
      <div class="box" ref=${cbox}>
        <h2>Évolution du NDVI</h2>
        <p class="cap">${p.chrono}</p>
        <${ParcelChart} p=${p} settings=${s} day=${day} setDay=${setDay} W=${W} />
        <p class="cap">● mesure retenue, ○ mesure écartée, pointillé : courbe interpolée loin de toute mesure. Survol : valeurs du jour ; clic : place le repère bleu${showMap ? " (la carte suit)" : ""}.</p>
        <h3>Météo de la zone (ERA5-Land)</h3>
        ${D.weather ? html`<${WeatherStrip} day=${day} W=${W} />
          <p class="cap">Barres : pluie journalière. Trait brun : humidité du sol. ▼ gel (≤ −2 °C).</p>`
          : html`<p class="cap">Météo absente de ce fichier : la lecture du sol se fait sans pluie ni humidité.</p>`}
        ${ndti.length > 0 && html`<h3>NDTI quand la parcelle est peu verte</h3>
          <svg viewBox=${`0 0 ${W} 90`} class="chart">
            <line x1="30" x2=${W - 8} y1=${84 - 30} y2=${84 - 30} class="thr" /><line x1="30" x2=${W - 8} y1=${84 - 15} y2=${84 - 15} class="thr" />
            <text x="25" y=${87 - 30} class="ax" text-anchor="end">0,10</text><text x="25" y=${87 - 15} class="ax" text-anchor="end">0,05</text>
            ${ndti.map((m) => html`<circle key=${m[0]} cx=${X(m[0])} cy=${84 - Math.min(0.27, m[3]) * 300} r="3.4"
              fill=${D.etats[m[4]] === "Sec" ? "#4a3aa7" : "#fff"} stroke=${D.etats[m[4]] === "Sec" ? "#fff" : "#eb6834"} stroke-width="1.4">
              <title>${fmtDay(m[0])} : NDTI ${fr(m[3], 3)}, sol ${D.etats[m[4]].toLowerCase()}, ${D.lectures[m[5]]}</title></circle>`)}
            <line x1=${X(day)} x2=${X(day)} y1="0" y2="90" class="cursor" />
          </svg>
          <p class="cap">Plein : sol sec, NDTI lisible. Cerclé orange : sol humide, NDTI non lu.</p>`}
      </div>
      <div class="col">
        <${ImageBlock} key=${p.id} p=${p} />
        <${ReadingBox} p=${p} s=${s} />
      </div>
    </div>
    <div class="box">
      <h2>Mesures de la parcelle</h2>
      <${SortTable} sticky=${1} rows=${p.meas.map((m) => ({ id: m[0], m }))} sel=${day} onRow=${(k) => setDay(k)} cols=${[
        { k: "d", label: "Date", help: H("Date"), sort: (r) => r.m[0], render: (r) => fmtDay(r.m[0]) },
        { k: "v", label: "NDVI", num: 1, help: H("NDVI"), sort: (r) => r.m[1], render: (r) => fr(r.m[1], 3) },
        { k: "l", label: "NDVI lissé", num: 1, help: H("NDVI_lisse"), sort: (r) => r.m[13], render: (r) => fr(r.m[13], 3) },
        { k: "ph", label: "Phase", help: H("Phase"), sort: (r) => r.m[1], render: (r) => (r.m[2] && r.m[1] != null ? PH_LABEL[phaseOf(r.m[1], s)] : "—") },
        { k: "fi", label: "Fiabilité", help: H("Fiabilite"), sort: (r) => r.m[8], render: (r) => r.m[17] || "—" },
        { k: "po", label: "Poids", num: 1, help: H("Poids"), sort: (r) => r.m[8], render: (r) => fr(r.m[8]) },
        { k: "r", label: "Retenue", help: H("Retenue"), sort: (r) => r.m[2], render: (r) => (r.m[2] ? "Oui" : "Non") },
        { k: "mo", label: "Motif", help: H("Motif"), sort: (r) => MOTIF(r.m), render: (r) => MOTIF(r.m) },
        { k: "nt", label: "NDTI", num: 1, help: H("NDTI"), sort: (r) => r.m[3], render: (r) => fr(r.m[3], 3) },
        { k: "es", label: "État du sol", help: H("Etat_sol"), sort: (r) => r.m[4], render: (r) => D.etats[r.m[4]] },
        { k: "le", label: "Lecture", help: H("Lecture"), sort: (r) => r.m[5], render: (r) => D.lectures[r.m[5]] },
        { k: "p3", label: "Pluie 3 j (mm)", num: 1, help: H("Pluie_3j_mm"), sort: (r) => r.m[10], render: (r) => fr(r.m[10], 1) },
        { k: "pv", label: "Pluie veille (mm)", num: 1, help: H("Pluie_veille_mm"), sort: (r) => r.m[15], render: (r) => fr(r.m[15], 1) },
        { k: "hs", label: "Humidité sol", num: 1, help: H("Humidite_sol"), sort: (r) => r.m[11], render: (r) => fr(r.m[11]) },
        { k: "tm", label: "Tmin 7 j (°C)", num: 1, help: H("Tmin_7j_C"), sort: (r) => r.m[16], render: (r) => fr(r.m[16], 1) },
        { k: "ge", label: "Gel", help: H("Gel"), sort: (r) => r.m[12], render: (r) => r.m[12] || "—" },
        { k: "st", label: "Statut", help: H("Statut"), sort: (r) => r.m[14], render: (r) => r.m[14] || "—" },
        { k: "cl", label: "Clair (%)", num: 1, help: H("Clair_pct"), sort: (r) => r.m[9], render: (r) => fr(r.m[9], 0) },
      ]} />
      <p class="cap">Clic sur une ligne : place le repère à cette date sur les graphiques${showMap ? " et la carte" : ""}.</p>
    </div>
  </section>`;
}

function Review({ s, sel, setSel }) {
  const all = useMemo(() => Object.fromEntries(D.parcels.map((p) => [p.id, p])), []);
  const [f, setF] = useState({ q: "", phase: "all", conf: new Set(CONF_ORDER), grey: false, sort: "nom" });
  const [curves, setCurves] = useState(true);
  const [collapsed, setCollapsed] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const ids = useMemo(() => {
    const key = SORTS.find((x) => x[0] === f.sort)[2];
    return D.parcels
      .filter((p) => (f.phase === "all" || phaseFin(p, s) === f.phase) && f.conf.has(p.conf)
        && (!f.q || p.id.toLowerCase().includes(f.q.toLowerCase())) && (!f.grey || inGrey(p, s)))
      .sort((a, b) => {
        const va = key(a), vb = key(b);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return va > vb ? 1 : va < vb ? -1 : 0;
      })
      .map((p) => p.id);
  }, [f, s]);
  const visible = useMemo(() => new Set(ids), [ids]);
  return html`<div class=${"review " + (collapsed ? "folded" : "")}>
    <${ParcelList} s=${s} ids=${ids} all=${all} sel=${sel} setSel=${setSel} f=${f} setF=${setF}
      curves=${curves} setCurves=${setCurves} collapsed=${collapsed} setCollapsed=${setCollapsed} />
    <${Detail} s=${s} sel=${sel} setSel=${setSel} ids=${ids} showMap=${showMap} setShowMap=${setShowMap} visible=${visible} />
  </div>`;
}

/* ---------------------------------------------------------------- projet */
function OpenBox({ onFile, busy, compact }) {
  const [over, setOver] = useState(false);
  const inp = useRef(null);
  const pick = (f) => f && onFile(f);
  return html`<div class=${"drop " + (over ? "over " : "") + (compact ? "compact" : "")}
      onDragOver=${(e) => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]); }}>
    <input ref=${inp} type="file" accept=".zip" hidden onChange=${(e) => { pick(e.target.files[0]); e.target.value = ""; }} />
    <p><b>Ouvrir une analyse enregistrée</b> (fichier .zip créé par « Enregistrer »)</p>
    <button class="primary" disabled=${busy} onClick=${() => inp.current.click()}>${busy ? "Ouverture…" : "Choisir le fichier"}</button>
    <span class="cap">ou le glisser ici. Aucun appel à Earth Engine : les résultats enregistrés sont réinterprétés par le moteur Python.</span>
  </div>`;
}

function Projet({ info, go, onFile, busy }) {
  if (!info) return html`<div class="page">
    <h1>NDVI parcellaire</h1>
    <p class="cap">Analyse de la couverture des sols par le NDVI Sentinel-2, parcelle par parcelle.</p>
    <div class="steps two">
      <${OpenBox} onFile=${onFile} busy=${busy} />
      <div class="box muted-box"><h2>Nouvelle analyse</h2>
        <p>Charger des parcelles, choisir la période et les dates, lancer le calcul Earth Engine.</p>
        <p class="cap">Arrive à l'étape 2. En attendant : version Streamlit.</p>
        <button class="tool" disabled>Nouvelle analyse</button></div>
    </div>
  </div>`;
  const share = (k) => D.parcels.filter((p) => p.meas.some((m) => m[0] === k && m[2])).length;
  const saved = info.saved_at ? info.saved_at.replace("T", " à ").slice(0, 16).split("-").reverse().join("/") : "—";
  return html`<div class="page">
    <h1>Projet</h1>
    <div class="steps">
      <div class="box"><h2>Analyse ouverte</h2>
        <p><b>${info.file}</b></p>
        <p class="cap">Enregistrée le ${info.saved_at ? info.saved_at.slice(0, 10).split("-").reverse().join("/") + " à " + info.saved_at.slice(11, 16) : "—"} avec la version ${info.app_version || "—"}.
          Parcelles d'origine : ${info.source_file || "—"}.</p>
        <${OpenBox} onFile=${onFile} busy=${busy} compact=${true} /></div>
      <div class="box"><h2>Période et dates</h2>
        <p>Du <b>${fmtDay(0)}</b> au <b>${fmtDay(D.nDays - 1)}</b>, ${info.n_dates} images analysées, ${info.n_parcels} parcelles.</p>
        <p class="cap">Météo ERA5-Land : ${info.weather ? `du ${info.weather.first.split("-").reverse().join("/")} au ${info.weather.last.split("-").reverse().join("/")}` : "absente du fichier (récupérée à l'étape 2)"}.
          ${info.errors.length ? ` ${info.errors.length} date(s) en erreur lors du calcul.` : ""}</p>
        <button class="tool" disabled title="Étape 2">Agrandir la période</button></div>
      <div class="box"><h2>Résultats</h2><p>Analyse prête.</p>
        <button class="primary" onClick=${() => go("revue")}>Revue des parcelles</button>
        <button class="tool" onClick=${() => go("ensemble")}>Tableaux</button></div>
    </div>
    <div class="box"><h2>Dates analysées</h2>
      <p class="cap">Hauteur : part des parcelles avec une mesure retenue ce jour-là (survol : détail).</p>
      <svg viewBox="0 0 1000 90" class="chart">
        ${D.dates.map((k) => { const x = 10 + (k / Math.max(1, D.nDays - 1)) * 980, n = share(k), h = (n / D.parcels.length) * 70;
          return html`<g key=${k}><rect x=${x - 3} y=${78 - Math.max(1, h)} width="6" height=${Math.max(1, h)} fill=${n ? "#1a9850" : "#c9cfc8"} rx="1"><title>${fmtDay(k)} : ${n} parcelle(s) avec une mesure retenue</title></rect></g>`; })}
        <line x1="10" x2="990" y1="78" y2="78" stroke="#c9cfc8" />
      </svg>
    </div>
  </div>`;
}

/* ---------------------------------------------------------------- application */
function App() {
  const [file, setFile] = useState(null);         // fichier ouvert (renvoyé si le serveur l'a oublié)
  const [info, setInfo] = useState(null);
  const [geo, setGeo] = useState(null);
  const [data, setDataState] = useState(null);    // dernière réponse « results »
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState(null);       // réglages du fichier ou du dernier « Enregistrer »
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [err, setErr] = useState(null);
  const [page, setPage] = useState("projet");
  const [sel, setSel] = useState(null);
  const [drawer, setDrawer] = useState(false);
  const [dl, setDl] = useState(null);
  const reqId = useRef(0);
  const fileRef = useRef(null);

  const upload = async (f) => {
    const fd = new FormData();
    fd.append("file", f);
    fd.append("name", f.name);
    return (await call("/api/analyses", { method: "POST", body: fd })).json();
  };
  /* appel lié à l'analyse ; si le serveur a redémarré, renvoi du fichier puis nouvel essai */
  const withKey = async (fn) => {
    try { return await fn(info.key); }
    catch (e) {
      if (e.status !== 404 || !fileRef.current) throw e;
      const o = await upload(fileRef.current);
      return fn(o.info.key);
    }
  };

  const openFile = async (f) => {
    setOpening(true); setErr(null);
    try {
      const o = await upload(f);
      const st = o.info.settings;
      const s0 = { low: st.low, high: st.high, greyOn: !!st.grey, grey: st.grey || o.info.default_grey,
        min_clear: st.min_clear, min_pixels: st.min_pixels, min_weight: st.min_weight, smooth_days: st.smooth_days };
      const r = await (await postJSON(`/api/analyses/${o.info.key}/results`, serverSettings(s0))).json();
      setData(r, o.geometry);
      fileRef.current = f;
      setFile(f); setInfo(o.info); setGeo(o.geometry); setDataState(r); setS(s0);
      setSaved(JSON.stringify(serverSettings(s0)));
      setSel(r.parcels.length ? r.parcels[0].id : null);
      setPage("revue");
    } catch (e) {
      setErr("Impossible d'ouvrir ce fichier : " + e.message);
    } finally { setOpening(false); }
  };

  /* recalcul par le serveur après un changement de réglage (regroupé, dernière demande gagnante) */
  useEffect(() => {
    if (!info || !s || !data) return;
    const body = serverSettings(s);
    if (JSON.stringify(body) === JSON.stringify(data.settings)) return;
    const id = ++reqId.current;
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        const r = await withKey(async (k) => (await postJSON(`/api/analyses/${k}/results`, body)).json());
        if (id !== reqId.current) return;
        setData(r, geo);
        setDataState(r);
        setErr(null);
      } catch (e) {
        if (id === reqId.current) setErr("Recalcul impossible : " + e.message);
      } finally { if (id === reqId.current) setBusy(false); }
    }, 300);
    return () => clearTimeout(t);
  }, [s]);

  const download = async (what, path, body, fallback) => {
    setDl(what); setErr(null);
    try {
      const r = await withKey((k) => postJSON(`/api/analyses/${k}/${path}`, body));
      saveBlob(await r.blob(), fileName(r, fallback));
      return true;
    } catch (e) { setErr(`${what} impossible : ${e.message}`); return false; }
    finally { setDl(null); }
  };
  ACTIONS.mapFile = (fmt, day) => download(fmt === "kml" ? "Carte KML" : "Carte HTML", "map",
    { settings: serverSettings(s), day: isoDay(day), format: fmt }, `phases_ndvi.${fmt}`);
  const exportExcel = () => download("Export Excel", "excel", serverSettings(s), "ndvi_temporel.xlsx");
  const save = async () => {
    const body = serverSettings(s);
    if (await download("Enregistrement", "save", body, "analyse_ndvi.zip")) setSaved(JSON.stringify(body));
  };

  useEffect(() => {
    if (!drawer) return;
    const k = (e) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [drawer]);

  const go = (pg, id) => { if (id) setSel(id); if (pg === "fiche") pg = "revue"; setPage(pg); window.scrollTo(0, 0); };
  const ready = !!(info && data && s);
  const tabs = [["projet", "Projet"], ["revue", "Revue des parcelles"], ["ensemble", "Tableaux"], ["carte", "Carte"], ["date", "Analyse à une date"]];
  const nGrey = ready && s.greyOn ? D.parcels.filter((p) => inGrey(p, s)).length : 0;
  const changed = ready && JSON.stringify(serverSettings(s)) !== saved;
  const reset = () => {
    const st = info.settings;
    setS({ ...s, low: st.low, high: st.high, greyOn: !!st.grey, grey: st.grey || s.grey, min_clear: st.min_clear,
      min_pixels: st.min_pixels, min_weight: st.min_weight, smooth_days: st.smooth_days });
  };
  const pg = ready ? page : "projet";
  return html`<div class=${"app3 p-" + pg}>
    <header class="top">
      <nav class="tabs">
        <span class="brand"><svg width="20" height="20" viewBox="0 0 24 24"><path d="M4 20C4 10 10 4 20 4c0 10-6 16-16 16Z" fill="#1a9850"/><path d="M4 20 14 10" stroke="#fff" stroke-width="1.6"/></svg>NDVI parcellaire</span>
        ${tabs.map(([k, l]) => html`<button key=${k} class=${pg === k ? "on" : ""} disabled=${!ready && k !== "projet"} onClick=${() => go(k)}>${l}</button>`)}
        <span class="grow"></span>
        ${ready && html`<button class=${"tool set " + (drawer ? "act" : "")} onClick=${() => setDrawer(!drawer)}>
          ⚙ Réglages <span class="cond">${fr(s.low)} / ${fr(s.high)}${s.greyOn ? " · zone grise" : ""}</span></button>`}
      </nav>
      ${ready && html`<div class="banner">
        <span title=${info.file}><b class="file">${info.file}</b></span><span>${info.n_parcels} parcelles</span><span>${fmtDay(0)} → ${fmtDay(D.nDays - 1)}</span>
        <span>${info.n_dates} dates</span><span class="hide-s">buffer ${info.locked.buffer_m} m, Cloud Score+ ${fr(info.locked.cs_threshold)} (verrouillé)</span>
        ${!info.weather && html`<span class="badge warn" title="Fichier enregistré sans météo : lecture du sol sans pluie ni humidité.">météo absente</span>`}
        <span class="grow"></span>
        ${busy && html`<span class="badge busy">Recalcul…</span>`}
        ${s.greyOn && html`<span class="badge warn">${nGrey} à vérifier</span>`}
        <span class=${"badge " + (changed ? "warn" : "ok")} title=${changed ? "Un réglage d'interprétation a changé depuis l'ouverture ou l'enregistrement : « Enregistrer » pour le garder dans le fichier." : ""}>${changed ? "Réglages modifiés, non enregistrés" : "Enregistrée"}</span>
        <button class="tool" disabled=${!!dl} onClick=${exportExcel}>${dl === "Export Excel" ? "Préparation…" : "Exporter Excel"}</button>
        <button class="primary" disabled=${!!dl} onClick=${save}>${dl === "Enregistrement" ? "Préparation…" : "Enregistrer"}</button>
      </div>`}
      ${err && html`<div class="errbar"><span>${err}</span><button class="tool" onClick=${() => setErr(null)}>Fermer</button></div>`}
    </header>
    ${ready && drawer && html`<div class="backdrop" onClick=${() => setDrawer(false)}></div>`}
    ${ready && html`<div class=${"drawer " + (drawer ? "open" : "")}>
      <button class="tool close" onClick=${() => setDrawer(false)} title="Fermer (Échap)">✕</button>
      <${Sidebar} s=${s} setS=${setS} info=${info} reset=${reset} busy=${busy} />
    </div>`}
    ${pg === "projet" && html`<${Projet} key=${info ? info.key : "vide"} info=${ready ? info : null} go=${go} onFile=${openFile} busy=${opening} />`}
    ${pg === "revue" && html`<${Review} key=${info.key} s=${s} sel=${sel} setSel=${setSel} />`}
    ${pg === "ensemble" && html`<${Overview} key=${info.key} s=${s} go=${go} sel=${sel} />`}
    ${pg === "carte" && html`<${MapPage} key=${info.key} s=${s} sel=${sel} setSel=${setSel} go=${go} dl=${dl} />`}
    ${pg === "date" && html`<div class="page"><h1>Analyse à une date</h1><p class="cap">Choix d'une image Sentinel-2, tableau, carte et vignette : étape 2 (calcul Earth Engine). En attendant, la page Carte montre les phases à chaque date analysée.</p></div>`}
  </div>`;
}
ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
