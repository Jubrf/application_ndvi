/* ---------------------------------------------------------------- réglages d'interprétation (panneau) */
const SMOOTH_OPTS = [3, 4, 6, 8, 10, 15];
const UNC_OPTS = [7, 10, 15, 20, 25, 30, 45];
function Sidebar({ s, setS, info, reset, busy }) {
  const set = (patch) => setS({ ...s, ...patch });
  const setLow = (v) => set({ low: Math.min(v, s.high - 0.01) });
  const setHigh = (v) => set({ high: Math.max(v, s.low + 0.01) });
  const si = Math.max(0, SMOOTH_OPTS.indexOf(s.smooth_days));
  const unc = s.uncertain_days ?? 15, ui = Math.max(0, UNC_OPTS.indexOf(unc));
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
    <label title="Entre deux mesures retenues plus espacées que ce délai, la courbe est tracée en pointillé et les jours comptés « incertains » (durée de couverture).">Courbe incertaine si plus de <b>${unc} jours</b> entre deux mesures
      <input type="range" min="0" max=${UNC_OPTS.length - 1} step="1" value=${ui} onInput=${(e) => set({ uncertain_days: UNC_OPTS[+e.target.value] })} /></label>
    <button class="tool" onClick=${reset}>Revenir aux réglages du fichier</button>
    <h3 class="lock">Calcul, verrouillé</h3>
    <p class="note">Fixé par l'analyse ouverte : buffer ${L.buffer_m} m, Cloud Score+ ${fr(L.cs_threshold)}, marge nuages ${L.cloud_buffer_m} m, valeurs aberrantes ${fr(L.iqr_k, 1)} × IQR.</p>
    <p class="note version">Interface React v5.6 · moteur ${info.server_version}</p>
  </aside>`;
}

/* ---------------------------------------------------------------- application */
function App() {
  const [info, setInfo] = useState(null);
  const [geo, setGeo] = useState(null);
  const [data, setDataState] = useState(null);    // dernière réponse « results »
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState(null);       // réglages du fichier ou du dernier « Enregistrer »
  const [dirty, setDirty] = useState(false);      // analyse calculée ou complétée, pas encore enregistrée
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [err, setErr] = useState(null);
  const [note, setNote] = useState(null);
  const [page, setPage] = useState("projet");
  const [sel, setSel] = useState(null);
  const [focus, setFocus] = useState(null);       // parcelle à cadrer sur la carte
  const [drawer, setDrawer] = useState(false);
  const [dl, setDl] = useState(null);
  const [gee, setGee] = useState({ ok: false, message: null });
  const [pset, setPset] = useState(null);         // fichier de parcelles d'une nouvelle analyse
  const [restoring, setRestoring] = useState(true);
  const reqId = useRef(0);
  const fileRef = useRef(null);
  const keyRef = useRef(null);
  const psetRef = useRef(null);
  const weatherTried = useRef(new Set());
  psetRef.current = pset;
  ACTIONS.gee = gee;
  ACTIONS.info = info;

  const upload = async (f) => {
    const fd = new FormData();
    fd.append("file", f);
    fd.append("name", f.name);
    return (await call("/api/analyses", { method: "POST", body: fd })).json();
  };
  /* appel lié à l'analyse ; si le serveur l'a oubliée (redémarrage), renvoi du fichier puis nouvel essai */
  const withKey = async (fn) => {
    try { return await fn(keyRef.current); }
    catch (e) {
      if (e.status !== 404 || !fileRef.current) throw e;
      const o = await upload(fileRef.current);
      keyRef.current = o.info.key;
      return fn(o.info.key);
    }
  };
  ACTIONS.withKey = withKey;
  /* même principe pour le fichier de parcelles d'une nouvelle analyse */
  const psetCall = async (fn) => {
    const ps = psetRef.current;
    const src = (p) => ({ type: "parcels", key: p.info.key, calc: p.calc });
    try { return await fn(src(ps)); }
    catch (e) {
      if (e.status !== 404 || !ps) throw e;
      const fd = new FormData();
      fd.append("file", ps.file); fd.append("name", ps.file.name);
      fd.append("buffer_m", String(ps.calc.buffer_m)); if (ps.info.id_field) fd.append("id_field", ps.info.id_field);
      const o = await (await call("/api/parcels", { method: "POST", body: fd })).json();
      const nps = { ...ps, info: o.info, geometry: o.geometry };
      psetRef.current = nps;
      setPset(nps);
      return fn(src(nps));
    }
  };

  /* Adopte une analyse connue du serveur (o = {info, geometry}) et son fichier f */
  const adopt = async (o, f, opt = {}) => {
    const s0 = withUnc(opt.s) || uiSettings(o.info.settings, o.info.default_grey);
    const r = await (await postJSON(`/api/analyses/${o.info.key}/results`, serverSettings(s0))).json();
    setData(r, o.geometry);
    fileRef.current = f;
    keyRef.current = o.info.key;
    setInfo(o.info); setGeo(o.geometry); setDataState(r); setS(s0);
    setSaved(savedKey(opt.saved) || JSON.stringify(serverSettings(uiSettings(o.info.settings, o.info.default_grey))));
    setDirty(!!opt.dirty);
    const ids = r.parcels.map((p) => p.id);
    setSel(opt.sel && ids.includes(opt.sel) ? opt.sel : ids[0] || null);
    setPage(opt.page || "revue");
    IDB.set("file", { blob: f, name: f.name });
    return o;
  };
  const unsavedWork = () => info && (dirty || JSON.stringify(serverSettings(s)) !== saved);
  const openFile = async (f, opt = {}) => {
    if (!opt.restore && unsavedWork() && !window.confirm("L'analyse ouverte a des modifications non enregistrées. L'ouvrir quand même à la place ?")) return;
    setOpening(true); setErr(null);
    try {
      const o = await upload(f);
      await adopt(o, f, opt.restore || {});
    } catch (e) {
      setErr("Impossible d'ouvrir ce fichier : " + e.message);
    } finally { setOpening(false); }
  };
  /* Analyse produite par le serveur (calcul, complément, météo) : récupération de son fichier */
  const adoptFromServer = async (o, opt) => {
    const r = await call(`/api/analyses/${o.info.key}/file`);
    const f = new File([await r.blob()], fileName(r, o.info.file || "analyse_ndvi.zip"), { type: "application/zip" });
    return adopt(o, f, opt);
  };
  const onResult = async (v) => {
    try {
      await adoptFromServer(v.analysis, { dirty: true, page: "revue" });
      setNote(`Analyse ${v.kind === "compléter" ? "complétée" : "terminée"} : ${v.message}. Pense à l'enregistrer (bouton « Enregistrer »).`);
    } catch (e) { setErr("Résultat du calcul illisible : " + e.message); }
  };
  ACTIONS.weather = async (refresh) => {
    try {
      const r = await withKey((k) => postJSON(`/api/analyses/${k}/weather`, { settings: serverSettings(s), refresh }));
      await adoptFromServer(await r.json(), { s, page, sel, saved, dirty: true });
      setNote("Météo ERA5-Land récupérée : pense à enregistrer l'analyse pour la garder dans le fichier.");
    } catch (e) { setErr("Météo indisponible : " + e.message); }
  };

  /* Démarrage : connexion Earth Engine, analyse gardée dans le navigateur */
  const checkGee = async (retry) => {
    try { setGee(await getJSON("/api/gee" + (retry ? "?retry=true" : ""))); }
    catch (e) { setGee({ ok: false, message: e.message }); }
  };
  useEffect(() => {
    checkGee(false);
    (async () => {
      const [fileRec, ui] = await Promise.all([IDB.get("file"), IDB.get("ui")]);
      if (fileRec && fileRec.blob) {
        const f = new File([fileRec.blob], fileRec.name || "analyse.zip", { type: "application/zip" });
        await openFile(f, { restore: ui ? { s: ui.s, page: ui.page, sel: ui.sel, saved: ui.saved, dirty: ui.dirty } : {} });
      }
      setRestoring(false);
    })();
  }, []);
  /* Conservation de l'état de l'interface */
  useEffect(() => {
    if (!info || !s) return;
    const t = setTimeout(() => IDB.set("ui", { key: info.key, s, page, sel, saved, dirty }), 400);
    return () => clearTimeout(t);
  }, [info, s, page, sel, saved, dirty]);
  /* Météo manquante (fichier enregistré sans météo) : récupérée une fois à l'ouverture */
  useEffect(() => {
    if (!info || info.weather || !gee.ok || weatherTried.current.has(info.key)) return;
    weatherTried.current.add(info.key);
    ACTIONS.weather(false);
  }, [info, gee.ok]);

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
    if (await download("Enregistrement", "save", body, "analyse_ndvi.zip")) { setSaved(JSON.stringify(body)); setDirty(false); }
  };
  const close = () => {
    if (unsavedWork() && !window.confirm("L'analyse a des modifications non enregistrées. La fermer quand même ?")) return;
    IDB.del("file"); IDB.del("ui");
    fileRef.current = null; keyRef.current = null;
    D = null;
    setInfo(null); setGeo(null); setDataState(null); setS(null); setSaved(null); setDirty(false); setNote(null); setPage("projet");
  };

  useEffect(() => {
    if (!drawer) return;
    const k = (e) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [drawer]);

  const go = (pg, id) => { if (id) setSel(id); if (pg === "fiche") pg = "revue"; setFocus(null); setPage(pg); window.scrollTo(0, 0); };
  /* « Voir sur la carte » (fiche) : onglet Carte centré et zoomé sur la parcelle */
  const onMap = (id) => { setSel(id); setFocus(id); setPage("carte"); window.scrollTo(0, 0); };
  const ready = !!(info && data && s);
  const oneSrc = ready ? { kind: "analysis", label: `${info.file} (${info.n_parcels} parcelles)`, geometry: geo,
      call: (fn) => withKey((k) => fn({ type: "analysis", key: k })) }
    : pset ? { kind: "parcels", label: `${pset.info.name} (${pset.info.n} parcelles)`, geometry: pset.geometry, call: psetCall } : null;
  const tabs = [["projet", "Projet"], ["revue", "Revue des parcelles"], ["ensemble", "Tableaux"], ["carte", "Carte"], ["date", "Analyse à une date"]];
  const enabled = (k) => k === "projet" || (k === "date" ? !!oneSrc : ready);
  const nGrey = ready && s.greyOn ? D.parcels.filter((p) => inGrey(p, s)).length : 0;
  const changed = ready && JSON.stringify(serverSettings(s)) !== saved;
  const reset = () => setS({ ...uiSettings(info.settings, s.grey) });
  const pg = enabled(page) ? page : "projet";
  return html`<div class=${"app3 p-" + pg}>
    <header class="top">
      <nav class="tabs">
        <span class="brand"><svg width="20" height="20" viewBox="0 0 24 24"><path d="M4 20C4 10 10 4 20 4c0 10-6 16-16 16Z" fill="#1a9850"/><path d="M4 20 14 10" stroke="#fff" stroke-width="1.6"/></svg>NDVI parcellaire</span>
        ${tabs.map(([k, l]) => html`<button key=${k} class=${pg === k ? "on" : ""} disabled=${!enabled(k)} onClick=${() => go(k)}>${l}</button>`)}
        <span class="grow"></span>
        ${ready && html`<button class=${"tool set " + (drawer ? "act" : "")} onClick=${() => setDrawer(!drawer)}>
          ⚙ Réglages <span class="cond">${fr(s.low)} / ${fr(s.high)}${s.greyOn ? " · zone grise" : ""}</span></button>`}
      </nav>
      ${ready && html`<div class="banner">
        <span title=${info.file}><b class="file">${info.file}</b></span><span>${info.n_parcels} parcelles</span><span>${fmtDay(0)} → ${fmtDay(D.nDays - 1)}</span>
        <span>${info.n_dates} dates</span><span class="hide-s">buffer ${info.locked.buffer_m} m, Cloud Score+ ${fr(info.locked.cs_threshold)} (verrouillé)</span>
        ${!info.weather && html`<span class="badge warn" title="Fichier sans météo : lecture du sol sans pluie ni humidité.">météo absente</span>`}
        <span class="grow"></span>
        ${busy && html`<span class="badge busy">Recalcul…</span>`}
        ${s.greyOn && html`<span class="badge warn">${nGrey} à vérifier</span>`}
        ${dirty ? html`<span class="badge warn" title="Analyse calculée, complétée ou météo ajoutée depuis l'ouverture : « Enregistrer » pour avoir le fichier.">Modifications non enregistrées</span>`
          : html`<span class=${"badge " + (changed ? "warn" : "ok")} title=${changed ? "Un réglage d'interprétation a changé depuis l'ouverture ou l'enregistrement : « Enregistrer » pour le garder dans le fichier." : ""}>${changed ? "Réglages modifiés, non enregistrés" : "Enregistrée"}</span>`}
        <button class="tool" disabled=${!!dl} onClick=${exportExcel}>${dl === "Export Excel" ? "Préparation…" : "Exporter Excel"}</button>
        <button class="primary" disabled=${!!dl} onClick=${save}>${dl === "Enregistrement" ? "Préparation…" : "Enregistrer"}</button>
      </div>`}
      ${err && html`<div class="errbar"><span>${err}</span><button class="tool" onClick=${() => setErr(null)}>Fermer</button></div>`}
      ${note && html`<div class="notebar"><span>${note}</span><button class="tool" onClick=${() => setNote(null)}>Fermer</button></div>`}
    </header>
    ${ready && drawer && html`<div class="backdrop" onClick=${() => setDrawer(false)}></div>`}
    ${ready && html`<div class=${"drawer " + (drawer ? "open" : "")}>
      <button class="tool close" onClick=${() => setDrawer(false)} title="Fermer (Échap)">✕</button>
      <${Sidebar} s=${s} setS=${setS} info=${info} reset=${reset} busy=${busy} />
    </div>`}
    ${restoring && !ready && html`<div class="page"><p class="cap">Ouverture de l'analyse gardée dans le navigateur…</p></div>`}
    ${pg === "projet" && !(restoring && !ready) && html`<${Projet} info=${ready ? info : null} s=${s} go=${go} onFile=${openFile} busy=${opening}
      gee=${gee} retryGee=${() => checkGee(true)} pset=${pset} setPset=${setPset} psetCall=${psetCall} onResult=${onResult} close=${close} dirty=${dirty} />`}
    ${pg === "revue" && html`<${Review} key=${info.key} s=${s} sel=${sel} setSel=${setSel} onMap=${onMap} />`}
    ${pg === "ensemble" && html`<${Overview} key=${info.key} s=${s} go=${go} sel=${sel} />`}
    ${pg === "carte" && html`<${MapPage} key=${info.key} s=${s} sel=${sel} setSel=${setSel} go=${go} dl=${dl} focus=${focus} />`}
    ${pg === "date" && html`<${OneDate} key=${oneSrc.kind + (ready ? info.key : pset.info.key)} src=${oneSrc} s=${ready ? s : null} />`}
  </div>`;
}
ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
