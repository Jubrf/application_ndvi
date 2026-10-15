/* ---------------------------------------------------------------- analyse à une date
   (comme la page Streamlit) : choix du mois, des dates disponibles, calcul d'une image,
   tableau, carte, exports et vignette. Source : analyse ouverte ou fichier de parcelles chargé. */
const DEFAULT_S = { low: 0.25, high: 0.5, greyOn: false, grey: [0.2, 0.25], min_clear: 50, min_pixels: 10, min_weight: 0.5, smooth_days: 6 };
const OD_DIGITS = { Poids: 2, Humidite_sol: 2, Pluie_3j_mm: 1, Pluie_veille_mm: 1, Tmin_7j_C: 1, Clair_pct: 1, Surface_ha: 2 };

function OneDate({ src, s }) {
  const st = s || DEFAULT_S;
  const now = new Date();
  const [month, setMonth] = useState([now.getFullYear(), now.getMonth() + 1]);
  const [dates, setDates] = useState(null);         // {query, list}
  const [choice, setChoice] = useState(null);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const [pid, setPid] = useState(null);
  const [period] = [periodOf(month, month)];
  const future = period[0] > todayIso();
  const shapes = useMemo(() => makeShapes(src.geometry.parcels), [src.geometry]);

  const search = async () => {
    setBusy("dates"); setErr(null); setRes(null);
    try {
      const r = await src.call((source) => postJSON("/api/dates", { source, start: period[0], end: period[1], min_clear: st.min_clear }));
      const list = (await r.json()).dates;
      setDates({ query: period.join("|"), list });
      const d = list.find((x) => x.ok) || list.find((x) => x.covers) || list[0];
      setChoice(d ? d.date : null);
    } catch (e) { setErr("Erreur Earth Engine (recherche des dates) : " + e.message); }
    finally { setBusy(null); }
  };
  const analyse = async () => {
    setBusy("calc"); setErr(null);
    try {
      const r = await src.call((source) => postJSON("/api/onedate", { source, date: choice, settings: serverSettings(st) }));
      const j = await r.json();
      setRes(j);
      setPid(j.rows.length ? j.rows[0].ID : null);
    } catch (e) { setErr(`Erreur Earth Engine (statistiques du ${fmtIso(choice)}) : ${e.message}`); }
    finally { setBusy(null); }
  };
  const exportFile = async (fmt) => {
    setBusy(fmt); setErr(null);
    try {
      const r = await src.call((source) => postJSON("/api/onedate/file", { source, date: res.date, settings: serverSettings(st), format: fmt }));
      saveBlob(await r.blob(), fileName(r, `ndvi_${res.date}.${fmt}`));
    } catch (e) { setErr("Export impossible : " + e.message); }
    finally { setBusy(null); }
  };
  const fresh = dates && dates.query === period.join("|");
  const list = fresh ? dates.list : [];
  const nOk = list.filter((d) => d.ok).length, nOut = list.filter((d) => !d.covers).length;
  const byId = res ? Object.fromEntries(res.rows.map((r) => [r.ID, r])) : {};
  const fmtCell = (c, v) => (v == null ? "—" : typeof v === "number" ? (Number.isInteger(v) && !(c in OD_DIGITS) ? String(v) : fr(v, OD_DIGITS[c] ?? 3)) : String(v));
  return html`<div class="page">
    <h1>Analyse à une date</h1>
    <p class="cap">Parcelles : ${src.label}. Paramètres de calcul ${src.kind === "analysis" ? "de l'analyse ouverte (verrouillés)" : "de la nouvelle analyse (page Projet)"} ;
      interprétation ${s ? "selon les réglages en cours" : "avec les réglages par défaut"}.</p>
    ${!ACTIONS.gee.ok && html`<p class="cap warnc">Earth Engine non connecté : ${ACTIONS.gee.message || "connexion en cours…"}</p>`}
    <div class="box">
      <div class="lrow">
        <${MonthPicker} label="Mois" value=${month} onChange=${setMonth} />
        <button class="tool" disabled=${future || busy || !ACTIONS.gee.ok} onClick=${search}>${busy === "dates" ? "Recherche…" : "Rechercher les dates disponibles"}</button>
        ${future && html`<span class="cap">Ce mois n'a pas encore commencé.</span>`}
      </div>
      ${fresh && (list.length ? html`
        <p class="cap">${`${list.length} date(s) en ${MONTHS_FR[month[1] - 1].toLowerCase()} ${month[0]} : ${nOk} ✅ à ${st.min_clear} % de ciel clair ou plus, ${list.length - nOk - nOut} ⚠️ en dessous, ${nOut} ⛔ hors emprise des parcelles. Le % de ciel clair porte sur la partie des parcelles couverte par l'image : une date ⚠️ peut rester exploitable pour certaines (voir leur statut après analyse).`}</p>
        <div class="lrow">
          <select class="sel wide" value=${choice || ""} onChange=${(e) => setChoice(e.target.value)}>
            ${list.map((d) => html`<option key=${d.date} value=${d.date}>${dateLabel(d, st.min_clear)}</option>`)}
          </select>
          <button class="primary" disabled=${!choice || busy} onClick=${analyse}>${busy === "calc" ? "Calcul…" : "Analyser cette date"}</button>
        </div>` : html`<p class="cap errc">Aucune image Sentinel-2 sur cette période.</p>`)}
    </div>
    ${err && html`<p class="errc">${err}</p>`}
    ${res && html`
      <div class="box">
        <div class="h3row"><h2>Résultats du ${fmtIso(res.date)} — ${res.rows.filter((r) => r.Statut === "OK").length}/${res.rows.length} parcelles exploitables</h2>
          <span class="grow"></span>
          ${["xlsx", "kml", "html"].map((f) => html`<button key=${f} class="tool" disabled=${!!busy} onClick=${() => exportFile(f)}>${busy === f ? "Préparation…" : { xlsx: "Excel", kml: "Carte KML", html: "Carte HTML" }[f]}</button>`)}</div>
        <${SortTable} sticky=${1} rows=${res.rows.map((r) => ({ id: r.ID, r }))} sel=${pid} onRow=${setPid}
          cols=${res.columns.map((c) => ({ k: c, label: c, help: res.help[c] || "", num: res.rows.some((r) => typeof r[c] === "number") ? 1 : 0,
            sort: (x) => x.r[c], render: (x) => fmtCell(c, x.r[c]) }))} />
        <p class="cap">${"Survol d'un en-tête : définition de la colonne. Météo : " + (res.weather ? "ERA5-Land (pluie, humidité du sol, gel) de la zone." : "indisponible, état du sol sur l'infrarouge moyen seul.") + " Pas de « Levée possible » à une date : il faut la tendance de la courbe."}</p>
      </div>
      <div class="mapgrid">
        <div class="mappanel tall">
          <${MapView} shapes=${shapes} sel=${pid} setSel=${setPid}
            fillOf=${(id) => { const r = byId[id]; return [r && r.Statut === "OK" ? (PHASE_COLOR[r.Phase] || INVALID) : INVALID, 0.7]; }}
            tipOf=${(id) => { const r = byId[id] || {}; return html`<b>${id}</b><br/>${r.Phase || "—"}<br/>NDVI : ${fr(r.NDVI, 3)}${r.NDTI != null ? ` · NDTI : ${fr(r.NDTI, 3)}` : ""}<br/>Fiabilité : ${r.Fiabilite || "—"} (poids ${fr(r.Poids)})<br/>Pixels utilisés : ${r.Pixels_utilises ?? "—"} · clairs : ${fr(r.Clair_pct, 0)} %`; }}
            label=${html`<strong>${fmtIso(res.date)}</strong><span>NDVI de l'image (gris : non exploitable)</span>`} />
          <div class="legend">${["nu", "peu", "bien"].map((k) => html`<span key=${k}><i style=${{ background: PH[k] }}></i>${PH_LABEL[k]}</span>`)}<span><i style=${{ background: INVALID }}></i>Non exploitable</span></div>
        </div>
        <div class="box mini">
          <h2>Image satellite d'une parcelle</h2>
          <select class="sel" value=${pid || ""} onChange=${(e) => setPid(e.target.value)}>
            ${res.rows.map((r) => html`<option key=${r.ID} value=${r.ID}>${r.ID}</option>`)}</select>
          ${pid && html`<${ThumbBox} key=${pid + res.date} pid=${pid} call=${src.call} defaultDate=${res.date}
            options=${[{ date: res.date, label: `${fmtIso(res.date)} — NDVI ${fr((byId[pid] || {}).NDVI, 3)} · ${(byId[pid] || {}).Statut || ""}` }]} />`}
        </div>
      </div>`}
  </div>`;
}
