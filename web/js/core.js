/* NDVI parcellaire — interface React (v5). Les calculs sont faits par le moteur Python (api/).
   Fichiers chargés dans l'ordre (index.html) : core, map, charts, tables, review, overview,
   onedate, project, app. Variables et fonctions partagées au niveau du module (scripts classiques). */
const { useState, useMemo, useRef, useEffect, useCallback } = React;
const html = htm.bind(React.createElement);

/* Données de l'analyse ouverte, renseignées par setData() à chaque réponse du serveur.
   D = résultats interprétés, PROJ / BBOX = contours projetés (carte).
   Mesure (p.meas[i]) : [0 jour, 1 NDVI, 2 retenue, 3 NDTI, 4 état du sol (indice D.etats),
   5 lecture (D.lectures), 6 motif (1 + indice D.motifs, 0 = aucun), 7 B11, 8 poids, 9 % clair,
   10 pluie 3 j, 11 humidité du sol, 12 gel, 13 NDVI lissé, 14 statut, 15 pluie veille,
   16 Tmin 7 j, 17 fiabilité]. */
let D = null, day0 = new Date(), LAST_DATE = 0, PROJ = [], BBOX = [0, 0, 1, 1], GEO_KEY = null;

const PH = { nu: "#d73027", peu: "#a6d96a", bien: "#1a9850", none: "#c3c9c2" };
const PH_LABEL = { nu: "Sol nu", peu: "Couvert peu développé", bien: "Couvert bien développé", none: "Pas de courbe" };
const PHASE_COLOR = { "Sol nu": PH.nu, "Couvert peu développé": PH.peu, "Couvert bien développé": PH.bien };
const INVALID = "#9e9e9e";
const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
const MONTHS_FR = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"];
const NO_IMAGE_STATUSES = ["Hors image", "Géométrie inexploitable"];
const WET_STATES = ["Humide", "Humide probable", "À vérifier"];
const dayDate = (k) => new Date(day0.getTime() + k * 86400000);
const pad2 = (n) => String(n).padStart(2, "0");
const fmtDay = (k) => { const d = dayDate(k); return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}/${d.getFullYear()}`; };
const isoDay = (k) => { const d = dayDate(k); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
const fmtShort = (k) => { const d = dayDate(k); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };
const fmtIso = (s) => (s ? s.slice(0, 10).split("-").reverse().join("/") : "—");
const fr = (v, n = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(n).replace(".", ","));
const H = (col) => (D && D.help && D.help[col]) || "";
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };

const merc = ([lon, lat]) => {
  const s = Math.sin((lat * Math.PI) / 180);
  return [(lon + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
};
/* Contours projetés pour la carte : {ids, proj, bbox} */
function makeShapes(parcels) {
  const proj = parcels.map((p) => p.poly.map((ring) => ring.map(merc)));
  let a = [1, 1, 0, 0];
  proj.forEach((rs) => rs.forEach((r) => r.forEach(([x, y]) => { a = [Math.min(a[0], x), Math.min(a[1], y), Math.max(a[2], x), Math.max(a[3], y)]; })));
  return { ids: parcels.map((p) => p.id), proj, bbox: a };
}

function setData(payload, geometry) {
  const polys = Object.fromEntries(geometry.parcels.map((p) => [p.id, p.poly]));
  payload.parcels.forEach((p) => { p.poly = polys[p.id] || []; });
  D = payload;
  day0 = new Date(D.start + "T00:00:00");
  LAST_DATE = D.dates.length ? D.dates[D.dates.length - 1] : D.nDays - 1;
  if (GEO_KEY !== D.key) {
    GEO_KEY = D.key;
    const sh = makeShapes(D.parcels);
    PROJ = sh.proj;
    BBOX = sh.bbox;
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
const getJSON = async (path) => (await call(path)).json();
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
  min_weight: s.min_weight, smooth_days: s.smooth_days, grey: s.greyOn ? [...s.grey] : null, uncertain_days: s.uncertain_days ?? 15 });
/* Réglages de l'interface à partir de ceux d'un fichier */
const uiSettings = (st, defaultGrey) => ({ low: st.low, high: st.high, greyOn: !!st.grey, grey: st.grey || defaultGrey || [0.2, 0.25],
  min_clear: st.min_clear, min_pixels: st.min_pixels, min_weight: st.min_weight, smooth_days: st.smooth_days,
  uncertain_days: st.uncertain_days ?? 15 });
/* Réglages conservés par une version précédente (sans délai d'incertitude) : complétés à 15 jours */
const withUnc = (s) => (s && s.uncertain_days == null ? { ...s, uncertain_days: 15 } : s);
const savedKey = (str) => (str ? JSON.stringify(withUnc(JSON.parse(str))) : str);
/* Durée de couverture : de la première à la dernière date analysée */
const spanDays = () => (D.dates.length ? D.dates[D.dates.length - 1] - D.dates[0] + 1 : 0);
const isoFr = (iso) => (iso ? iso.split("-").reverse().join("/") : "—");
const isoFrShort = (iso) => (iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) : "—");
/* Courbe incertaine au jour k (entre deux mesures retenues espacées de plus de N jours) */
const uncAt = (p, k) => !!(p.curve && p.curve.u.some(([a, b]) => k >= a && k <= b));
/* Actions de l'application (renseignées par App), appelées depuis les pages :
   withKey(fn) appel lié à l'analyse ouverte (renvoi du fichier si le serveur l'a oublié),
   mapFile, setErr, gee (état de la connexion Earth Engine). */
const ACTIONS = { gee: { ok: false } };

/* ---------------------------------------------------------------- conservation dans le navigateur
   L'analyse ouverte (fichier .zip) et l'état de l'interface sont gardés dans IndexedDB : une
   actualisation de la page les retrouve. Échec silencieux (navigation privée, stockage bloqué). */
const IDB = (() => {
  let dbp = null;
  const open = () => {
    if (!dbp) {
      dbp = new Promise((res, rej) => {
        try {
          const rq = indexedDB.open("ndvi-parcellaire", 1);
          rq.onupgradeneeded = () => rq.result.createObjectStore("kv");
          rq.onsuccess = () => res(rq.result);
          rq.onerror = () => rej(rq.error);
        } catch (e) { rej(e); }
      });
    }
    return dbp;
  };
  const tx = async (mode, fn) => {
    const db = await open();
    return new Promise((res, rej) => {
      const t = db.transaction("kv", mode);
      const rq = fn(t.objectStore("kv"));
      t.oncomplete = () => res(rq && rq.result);
      t.onerror = () => rej(t.error);
    });
  };
  return {
    get: (k) => tx("readonly", (st) => st.get(k)).catch(() => null),
    set: (k, v) => tx("readwrite", (st) => st.put(v, k)).catch(() => null),
    del: (k) => tx("readwrite", (st) => st.delete(k)).catch(() => null),
  };
})();

/* ---------------------------------------------------------------- outils d'interpretation */
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
const lastCurve = (p) => (p.curve ? p.curve.v[p.curve.v.length - 1] : null);
const phaseFin = (p, s) => phaseOf(lastCurve(p), s);
function inGrey(p, s) {
  const v = lastCurve(p);   // comme le moteur : courbe à la dernière mesure retenue
  if (!s.greyOn || v == null) return false;
  return v >= s.grey[0] && v <= s.grey[1];
}
const MOTIF = (m) => (m[6] ? D.motifs[m[6] - 1] : "");
const CONF_ORDER = ["Bonne", "Moyenne", "Faible", "Aucune mesure"];

function useWidth(ref) {
  const [w, setW] = useState(0);
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return w;
}

/* Date d'image dans les listes de choix (comme ui/formatting.date_label) */
function dateLabel(d, minClear) {
  const dd = fmtIso(d.date);
  if (!d.covers) return `⛔ ${dd} — ne couvre pas les parcelles`;
  let label = `${d.clear_pct >= minClear ? "✅" : "⚠️"} ${dd} — ${Math.round(d.clear_pct)} % de ciel clair`;
  if (d.cover_pct != null && d.cover_pct < 99.5) label += ` · couvre ${Math.round(d.cover_pct)} % des parcelles`;
  return label;
}
/* Sélecteur mois + année */
function MonthPicker({ label, value, onChange }) {
  const thisYear = new Date().getFullYear();
  const years = [];
  for (let y = thisYear; y >= 2017; y--) years.push(y);
  return html`<div class="mpick"><span>${label}</span>
    <select class="sel" value=${value[1]} onChange=${(e) => onChange([value[0], +e.target.value])}>
      ${MONTHS_FR.map((m, i) => html`<option key=${i} value=${i + 1}>${m}</option>`)}</select>
    <select class="sel" value=${value[0]} onChange=${(e) => onChange([+e.target.value, value[1]])}>
      ${years.map((y) => html`<option key=${y} value=${y}>${y}</option>`)}</select></div>`;
}
/* Période (mois de début, mois de fin) → [ISO début, ISO fin au plus aujourd'hui] */
function periodOf(start, end) {
  const s = `${start[0]}-${pad2(start[1])}-01`;
  const last = new Date(end[0], end[1], 0);
  const e = `${end[0]}-${pad2(end[1])}-${pad2(last.getDate())}`;
  const t = todayIso();
  return [s, e > t ? t : e];
}
