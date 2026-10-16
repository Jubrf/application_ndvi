/* ---------------------------------------------------------------- page Projet
   Ouvrir une analyse enregistrée, nouvelle analyse (parcelles, paramètres, période, dates,
   calcul Earth Engine en tâche de fond), compléter l'analyse ouverte, état de la connexion. */
const CALC_OPTS = {
  buffer_m: [[0, "0 m"], [5, "5 m"], [10, "10 m"], [15, "15 m"], [20, "20 m"]],
  cs_threshold: [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85].map((v) => [v, fr(v)]),
  cloud_buffer_m: [[0, "0 m"], [10, "10 m"], [20, "20 m"], [40, "40 m"], [60, "60 m"]],
  iqr_k: [[1.0, "1,0"], [1.5, "1,5"], [2.0, "2,0"], [3.0, "3,0"]],
};
const DEFAULT_CALC = { buffer_m: 10, cs_threshold: 0.6, cloud_buffer_m: 20, iqr_k: 1.5 };
const PRESEL_HELP = "Une date partiellement nuageuse peut rester exploitable pour une partie des parcelles : le contrôle se fait ensuite parcelle par parcelle, et les mesures douteuses sont écartées de la courbe.";

function defaultMonths() {
  const t = new Date();
  const s = new Date(t.getFullYear(), t.getMonth() - 2, 1);
  return [[s.getFullYear(), s.getMonth() + 1], [t.getFullYear(), t.getMonth() + 1]];
}
const monthOf = (iso) => [+iso.slice(0, 4), +iso.slice(5, 7)];

function OpenBox({ onFile, busy, compact }) {
  const [over, setOver] = useState(false);
  const inp = useRef(null);
  const pick = (f) => f && onFile(f);
  return html`<div class=${"drop " + (over ? "over " : "") + (compact ? "compact" : "")}
      onDragOver=${(e) => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]); }}>
    <input ref=${inp} type="file" accept=".zip" hidden onChange=${(e) => { pick(e.target.files[0]); e.target.value = ""; }} />
    <p><b>Ouvrir une analyse enregistrée</b> (fichier .zip créé par « Enregistrer », Streamlit ou React)</p>
    <button class=${compact ? "tool" : "primary"} disabled=${busy} onClick=${() => inp.current.click()}>${busy ? "Ouverture…" : "Choisir le fichier"}</button>
    <span class="cap">ou le glisser ici.</span>
  </div>`;
}

function GeeStatus({ gee, retry }) {
  if (gee.ok) return html`<p class="cap okc">Earth Engine : ${gee.message}</p>`;
  return html`<div class="warnbox"><b>Earth Engine non connecté.</b> ${gee.message || "Connexion en cours…"}
    <button class="tool small" onClick=${retry}>Réessayer</button>
    <p class="cap">Sans connexion : rouvrir une analyse enregistrée, la relire et l'exporter reste possible.</p></div>`;
}

/* Suivi d'un calcul en tâche de fond : barre de progression, arrêt, fin */
function JobRunner({ job, onDone, onFail }) {
  const [j, setJ] = useState(job);
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      if (stop) return;
      try {
        const v = await getJSON(`/api/jobs/${job.id}`);
        setJ(v);
        if (v.state === "running") setTimeout(tick, 1000);
        else if (v.state === "done") onDone(v);
        else onFail(v);
      } catch (e) {
        onFail({ state: "error", message: e.status === 404 ? "Calcul perdu : le serveur a redémarré pendant le calcul. Relance-le." : e.message });
      }
    };
    const t = setTimeout(tick, 800);
    return () => { stop = true; clearTimeout(t); };
  }, [job.id]);
  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  return html`<div class="job">
    <div class="bar"><i style=${{ width: pct + "%" }}></i></div>
    <div class="lrow"><span>${j.current === "météo" ? "Météo ERA5-Land…" : j.current ? `${fmtIso(j.current)} (${Math.min(j.done + 1, j.total)}/${j.total})…` : j.state === "running" ? "Initialisation…" : ""}</span>
      <span class="cap">${j.elapsed ?? 0} s · environ 2 à 5 s par date</span><span class="grow"></span>
      ${j.state === "running" && html`<button class="tool small" onClick=${() => postJSON(`/api/jobs/${job.id}/cancel`, {})}>Arrêter</button>`}</div>
    ${j.errors && j.errors.length > 0 && html`<p class="cap errc">${j.errors.length} date(s) en erreur : ${j.errors.slice(-3).join(" · ")}</p>`}
  </div>`;
}

/* Liste des dates à cocher, avec présélection par % de ciel clair */
function DatePicker({ list, sel, setSel, presel, setPresel, minClear, presetLabel }) {
  const usable = (d) => d.covers && d.clear_pct >= presel;
  return html`<div class="datepick">
    <label class="presel" title=${PRESEL_HELP}>${presetLabel} : <b>${presel} %</b>
      <input type="range" min="0" max="100" step="10" value=${presel}
        onInput=${(e) => { const v = +e.target.value; setPresel(v); setSel(new Set(list.filter((d) => d.covers && d.clear_pct >= v).map((d) => d.date))); }} /></label>
    <div class="lrow small">
      <span>${sel.size} date(s) choisie(s) sur ${list.length} · ${list.filter(usable).length} présélectionnée(s)</span>
      <button class="tool small" onClick=${() => setSel(new Set(list.filter((d) => d.covers).map((d) => d.date)))}>Toutes</button>
      <button class="tool small" onClick=${() => setSel(new Set())}>Aucune</button>
      <button class="tool small" onClick=${() => setSel(new Set(list.filter(usable).map((d) => d.date)))}>Présélection</button>
    </div>
    <div class="dlist">
      ${list.map((d) => html`<label key=${d.date} class=${"dopt " + (d.covers ? "" : "off")}>
        <input type="checkbox" disabled=${!d.covers} checked=${sel.has(d.date)}
          onChange=${(e) => { const n = new Set(sel); e.target.checked ? n.add(d.date) : n.delete(d.date); setSel(n); }} />
        ${dateLabel(d, minClear)}</label>`)}
    </div>
  </div>`;
}

/* Nouvelle analyse : fichier de parcelles → paramètres → période → dates → calcul */
function NewAnalysis({ pset, setPset, psetCall, minClear, onResult, hasOpen }) {
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState(null);
  const [months, setMonths] = useState(defaultMonths());
  const [found, setFound] = useState(null);        // {query, list}
  const [sel, setSel] = useState(new Set());
  const [presel, setPresel] = useState(20);
  const [job, setJob] = useState(null);
  const [searching, setSearching] = useState(false);
  const inp = useRef(null);
  const calc = pset ? pset.calc : DEFAULT_CALC;
  const period = periodOf(months[0], months[1]);
  const query = pset ? [pset.info.key, period.join("|"), JSON.stringify(calc)].join("#") : null;
  const fresh = found && found.query === query;

  const load = async (file, idField, buffer) => {
    setLoading(true); setMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file); fd.append("name", file.name);
      fd.append("buffer_m", String(buffer)); if (idField) fd.append("id_field", idField);
      const o = await (await call("/api/parcels", { method: "POST", body: fd })).json();
      setPset({ file, info: o.info, geometry: o.geometry, calc: { ...(pset ? pset.calc : DEFAULT_CALC), buffer_m: buffer } });
    } catch (e) { setMsg(["error", e.message]); }
    finally { setLoading(false); }
  };
  const setCalc = (k, v) => {
    if (k === "buffer_m") load(pset.file, pset.info.id_field, v);
    else setPset({ ...pset, calc: { ...pset.calc, [k]: v } });
  };
  const search = async () => {
    setSearching(true); setMsg(null);
    try {
      const r = await psetCall((source) => postJSON("/api/dates", { source, start: period[0], end: period[1], min_clear: minClear }));
      const list = (await r.json()).dates;
      setFound({ query, list });
      setSel(new Set(list.filter((d) => d.covers && d.clear_pct >= presel).map((d) => d.date)));
      if (!list.length) setMsg(["info", "Aucune image Sentinel-2 sur cette période."]);
    } catch (e) { setMsg(["error", "Erreur Earth Engine (recherche des dates) : " + e.message]); }
    finally { setSearching(false); }
  };
  const run = async () => {
    if (hasOpen && !window.confirm("L'analyse ouverte sera remplacée par la nouvelle. Continuer ?")) return;
    setMsg(null);
    try {
      const r = await psetCall((source) => postJSON("/api/jobs", { source, start: period[0], end: period[1], dates: [...sel].sort(), settings: null }));
      setJob(await r.json());
    } catch (e) { setMsg(["error", "Calcul impossible : " + e.message]); }
  };
  return html`<div class="box wizard">
    <h2>Nouvelle analyse</h2>
    <div class="wstep"><span class="wn">1</span><div>
      <b>Parcelles</b>
      <input ref=${inp} type="file" accept=".zip,.geojson,.json" hidden onChange=${(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) load(f, null, calc.buffer_m); }} />
      <div class="lrow"><button class="tool" disabled=${loading || !!job} onClick=${() => inp.current.click()}>${loading ? "Chargement…" : pset ? "Changer de fichier" : "Choisir le fichier de parcelles"}</button>
        <span class="cap">ZIP de shapefile (.shp, .shx, .dbf, .prj) ou GeoJSON.</span></div>
      ${pset && html`<div>
        <p class="cap"><b>${pset.info.name}</b> — ${pset.info.messages.map((m) => m[1]).join(" · ")}</p>
        ${pset.info.fields.length > 0 && html`<label class="lrow small">Identifiant des parcelles
          <select class="sel" value=${pset.info.id_field} disabled=${loading || !!job} onChange=${(e) => load(pset.file, e.target.value, calc.buffer_m)}>
            ${pset.info.fields.map((f) => html`<option key=${f} value=${f}>${f}</option>`)}</select>
          <span class="cap">ex. ${pset.info.ids_preview.join(", ")}</span></label>`}
      </div>`}
    </div></div>
    ${pset && html`<div class="wstep"><span class="wn">2</span><div>
      <b>Paramètres de calcul</b> <span class="cap">(conditionnent les requêtes Earth Engine ; verrouillés ensuite dans le fichier)</span>
      <div class="calcgrid">
        ${[["buffer_m", "Buffer intérieur", "Retire une bande en bordure de parcelle (haies, chemins, voisins). Réduit automatiquement pour les petites parcelles."],
          ["cs_threshold", "Seuil Cloud Score+", "Pixels sous ce score rejetés (nuages, ombres, brume). Plus haut = plus strict."],
          ["cloud_buffer_m", "Marge autour des nuages", ""],
          ["iqr_k", "Valeurs aberrantes (k × IQR)", "Pixels hors [Q1 − k·IQR ; Q3 + k·IQR] exclus. Plus haut = moins d'exclusions."]].map(([k, l, h]) => html`
          <label key=${k} title=${h}>${l}<select class="sel" value=${calc[k]} disabled=${loading || !!job}
            onChange=${(e) => setCalc(k, +e.target.value)}>${CALC_OPTS[k].map(([v, t]) => html`<option key=${v} value=${v}>${t}</option>`)}</select></label>`)}
      </div>
    </div></div>`}
    ${pset && html`<div class="wstep"><span class="wn">3</span><div>
      <b>Période et dates</b>
      <div class="lrow">
        <${MonthPicker} label="Début" value=${months[0]} onChange=${(v) => setMonths([v, months[1]])} />
        <${MonthPicker} label="Fin" value=${months[1]} onChange=${(v) => setMonths([months[0], v])} />
      </div>
      ${period[0] > period[1] ? html`<p class="errc">Le mois de début doit précéder le mois de fin.</p>` : html`
        <p class="cap">Période : du ${fmtIso(period[0])} au ${fmtIso(period[1])}.</p>
        <button class="tool" disabled=${searching || !!job || !ACTIONS.gee.ok} onClick=${search}>${searching ? "Recherche…" : "Rechercher les dates disponibles"}</button>`}
      ${fresh && found.list.length > 0 && html`<${DatePicker} list=${found.list} sel=${sel} setSel=${setSel} presel=${presel} setPresel=${setPresel}
        minClear=${minClear} presetLabel="Présélection : ciel clair minimum sur l'ensemble des parcelles" />`}
    </div></div>`}
    ${pset && fresh && found.list.length > 0 && html`<div class="wstep"><span class="wn">4</span><div>
      <b>Calcul</b>
      <p class="cap">${sel.size} date(s) × ${pset.info.n} parcelles — une requête Earth Engine par date, environ 2 à 5 s chacune (instantané si déjà calculée). Le calcul continue sur le serveur ; garde cette page ouverte jusqu'à la fin.</p>
      ${job ? html`<${JobRunner} job=${job}
          onDone=${(v) => { setJob(null); onResult(v); }}
          onFail=${(v) => { setJob(null); setMsg(["error", v.message || "Calcul arrêté."]); }} />`
        : html`<button class="primary" disabled=${!sel.size || !ACTIONS.gee.ok} onClick=${run}>Lancer l'analyse temporelle</button>`}
    </div></div>`}
    ${msg && html`<p class=${msg[0] === "error" ? "errc" : "cap"}>${msg[1]}</p>`}
  </div>`;
}

/* Compléter l'analyse ouverte : recherche sur le ou les mois choisis, seules les dates pas encore
   analysées sont proposées ; la période de l'analyse devient l'enveloppe des deux. */
const addDays = (iso, n) => { const d = new Date(iso + "T00:00:00"); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
function ExtendBox({ info, s, onResult }) {
  const now = new Date();
  const [months, setMonths] = useState([monthOf(info.end), [now.getFullYear(), now.getMonth() + 1]]);
  const [found, setFound] = useState(null);
  const [sel, setSel] = useState(new Set());
  const [presel, setPresel] = useState(20);
  const [job, setJob] = useState(null);
  const [searching, setSearching] = useState(false);
  const [msg, setMsg] = useState(null);
  const win = periodOf(months[0], months[1]);
  const valid = win[0] <= win[1];
  const after = [win[0] < info.start ? win[0] : info.start, win[1] > info.end ? win[1] : info.end];
  const gap = valid && (win[0] > addDays(info.end, 1) ? [addDays(info.end, 1), addDays(win[0], -1)]
    : win[1] < addDays(info.start, -1) ? [addDays(win[1], 1), addDays(info.start, -1)] : null);
  const query = win.join("|");
  const fresh = found && found.query === query;
  const search = async () => {
    setSearching(true); setMsg(null);
    try {
      const r = await ACTIONS.withKey((k) => postJSON("/api/dates", { source: { type: "analysis", key: k }, start: win[0], end: win[1], min_clear: s.min_clear }));
      const ext = (await r.json()).extension;
      ext.doneIn = ext.done.filter((d) => d >= win[0] && d <= win[1]);
      setFound({ query, ext });
      setSel(new Set(ext.new.filter((d) => d.clear_pct >= presel).map((d) => d.date)));
    } catch (e) { setMsg(["error", "Erreur Earth Engine (recherche des dates) : " + e.message]); }
    finally { setSearching(false); }
  };
  const run = async () => {
    const chosen = [...sel].sort();
    const nb = chosen.filter((d) => d < info.start).length, na = chosen.filter((d) => d > info.end).length;
    const txt = `Compléter l'analyse ?\n\n- ${chosen.length} date(s) ajoutée(s) : ${nb} avant, ${na} après, ${chosen.length - nb - na} pendant la période existante\n`
      + `- Période après ajout : ${fmtIso(after[0])} → ${fmtIso(after[1])}${gap ? ` (du ${fmtIso(gap[0])} au ${fmtIso(gap[1])} : aucune date analysée, courbe interpolée)` : ""}\n`
      + `- Calcul avec les paramètres de l'analyse : buffer ${info.locked.buffer_m} m, Cloud Score+ ${fr(info.locked.cs_threshold)}, marge ${info.locked.cloud_buffer_m} m\n`
      + `- Durée estimée : environ ${Math.max(5, 3 * chosen.length)} s\n\nLes ${info.n_dates} date(s) déjà analysée(s) ne sont pas recalculées. Pense à enregistrer l'analyse ensuite.`;
    if (!window.confirm(txt)) return;
    try {
      const r = await ACTIONS.withKey((k) => postJSON("/api/jobs", { source: { type: "analysis", key: k }, start: win[0], end: win[1], dates: chosen, settings: serverSettings(s) }));
      setJob(await r.json());
    } catch (e) { setMsg(["error", "Calcul impossible : " + e.message]); }
  };
  const ext = fresh ? found.ext : null;
  const nb = ext ? ext.new.filter((d) => d.date < info.start).length : 0, na = ext ? ext.new.filter((d) => d.date > info.end).length : 0;
  return html`<div class="box wizard">
    <h2>Compléter l'analyse</h2>
    <p class="cap">Choisis le ou les mois où chercher des images : seules les dates pas encore dans l'analyse sont proposées et calculées, avec les paramètres et les contours de l'analyse ouverte. Analyse actuelle : du ${fmtIso(info.start)} au ${fmtIso(info.end)}, ${info.n_dates} dates.</p>
    <div class="lrow">
      <${MonthPicker} label="Du mois" value=${months[0]} onChange=${(v) => setMonths([v, months[1]])} />
      <${MonthPicker} label="au mois" value=${months[1]} onChange=${(v) => setMonths([months[0], v])} />
    </div>
    ${!valid ? html`<p class="errc">Le premier mois doit précéder le second.</p>` : html`
      <p class="cap">${`Recherche du ${fmtIso(win[0])} au ${fmtIso(win[1])}. Période de l'analyse après ajout : du ${fmtIso(after[0])} au ${fmtIso(after[1])}.`}</p>
      ${gap && html`<p class="warnc">${`Du ${fmtIso(gap[0])} au ${fmtIso(gap[1])}, aucune date ne sera analysée : la courbe y sera interpolée (pointillé).`}</p>`}
      <button class="tool" disabled=${searching || !!job || !ACTIONS.gee.ok} onClick=${search}>${searching ? "Recherche…" : "Rechercher les dates à ajouter"}</button>`}
    ${ext && html`<p class="cap">${`Sur ces mois : ${ext.doneIn.length} date(s) déjà analysée(s) · ${ext.new.length} disponible(s) non analysée(s) : ${nb} avant, ${ext.new.length - nb - na} pendant, ${na} après la période existante${ext.n_outside ? ` · ${ext.n_outside} autre(s) image(s) ne couvrant pas les parcelles, non proposée(s)` : ""}.`}</p>`}
    ${ext && (ext.new.length ? html`<${DatePicker} list=${ext.new} sel=${sel} setSel=${setSel} presel=${presel} setPresel=${setPresel} minClear=${s.min_clear}
        presetLabel="Présélection des dates à ajouter : ciel clair minimum" />
      ${job ? html`<${JobRunner} job=${job} onDone=${(v) => { setJob(null); setFound(null); onResult(v); }}
          onFail=${(v) => { setJob(null); setMsg(["error", v.message || "Calcul arrêté."]); }} />`
        : html`<button class="primary" disabled=${!sel.size || !ACTIONS.gee.ok} onClick=${run}>Compléter l'analyse (${sel.size} date(s))</button>`}`
      : html`<p class="cap">Toutes les dates disponibles sur ces mois sont déjà analysées.</p>`)}
    ${msg && html`<p class="errc">${msg[1]}</p>`}
  </div>`;
}

function Projet({ info, s, go, onFile, busy, gee, retryGee, pset, setPset, psetCall, onResult, close, dirty }) {
  return html`<div class="page">
    <h1>${info ? "Projet" : "NDVI parcellaire"}</h1>
    <${GeeStatus} gee=${gee} retry=${retryGee} />
    ${info && html`<div class="steps">
      <div class="box"><h2>Analyse ouverte</h2>
        <p><b>${info.file}</b>${dirty ? html` <span class="badge warn">non enregistrée</span>` : ""}</p>
        <p class="cap">${info.saved_at ? `Fichier du ${fmtIso(info.saved_at)} à ${info.saved_at.slice(11, 16)}, version ${info.app_version || "—"}.` : ""} Parcelles d'origine : ${info.source_file || "—"}.</p>
        <div class="lrow"><button class="tool" onClick=${close} title="Retire l'analyse de cette page et du navigateur">Fermer l'analyse</button></div>
        <${OpenBox} onFile=${onFile} busy=${busy} compact=${true} /></div>
      <div class="box"><h2>Période et dates</h2>
        <p>Du <b>${fmtDay(0)}</b> au <b>${fmtDay(D.nDays - 1)}</b>, ${info.n_dates} images analysées, ${info.n_parcels} parcelles.</p>
        <p class="cap">Météo ERA5-Land : ${info.weather ? `du ${fmtIso(info.weather.first)} au ${fmtIso(info.weather.last)}` : "absente du fichier"}.
          ${info.errors.length ? ` ${info.errors.length} date(s) en erreur lors du calcul.` : ""}</p>
        ${info.errors.length > 0 && html`<details><summary class="cap">Dates en erreur</summary><ul class="cap">${info.errors.map((e, i) => html`<li key=${i}>${e}</li>`)}</ul></details>`}</div>
      <div class="box"><h2>Résultats</h2><p>Analyse prête.</p>
        <div class="lrow"><button class="primary" onClick=${() => go("revue")}>Revue des parcelles</button>
        <button class="tool" onClick=${() => go("ensemble")}>Tableaux</button></div></div>
    </div>`}
    ${info && html`<div class="box"><h2>Dates analysées</h2><${DatesChart} /></div>`}
    ${info && html`<${ExtendBox} key=${info.key} info=${info} s=${s} onResult=${onResult} />`}
    ${!info && html`<${OpenBox} onFile=${onFile} busy=${busy} />`}
    <${NewAnalysis} pset=${pset} setPset=${setPset} psetCall=${psetCall} minClear=${s ? s.min_clear : 50} onResult=${onResult} hasOpen=${!!info} />
  </div>`;
}
