/* ---------------------------------------------------------------- revue des parcelles : liste + fiche */
const SORTS = [
  ["nom", "Nom", (p) => p.id],
  ["nfa", "NDVI fin croissant", (p) => p.ndvi_fin],
  ["nfd", "NDVI fin décroissant", (p) => (p.ndvi_fin == null ? null : -p.ndvi_fin)],
  ["ha", "Surface décroissante", (p) => -p.ha],
  ["conf", "Confiance", (p) => CONF_ORDER.indexOf(p.conf)],
  ["lect", "Lecture fin", (p) => p.lect_fin],
];

function ParcelList({ s, ids, all, sel, setSel, f, setF, curves, setCurves, collapsed, setCollapsed }) {
  const box = useRef(null);
  const w = useWidth(box);
  const counts = useMemo(() => {
    const c = { nu: 0, peu: 0, bien: 0, none: 0 };
    D.parcels.forEach((p) => c[phaseFin(p, s)]++);
    return c;
  }, [s, D]);
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
          <div class="l2">${p.lect_fin && p.lect_fin !== "—" ? p.lect_fin : PH_LABEL[k]} · ${(p.conf || "—").toLowerCase()}${p.drops && p.drops !== "—" ? " · baisse rapide" : ""}</div>
        </button>`;
      })}
      ${ids.length === 0 && html`<p class="cap pad">Aucune parcelle ne correspond aux filtres.</p>`}
    </div>
  </aside>`;
}

/* Vignette Sentinel-2 d'une parcelle (couleurs naturelles ou fausses couleurs infrarouges).
   source : {type, key, calc} (api.main.Source) ; options : [{date ISO, label}]. */
function ThumbBox({ source, pid, options, defaultDate, call }) {
  const [date, setDate] = useState(defaultDate);
  const [mode, setMode] = useState("naturel");
  const [mask, setMask] = useState(true);
  const [active, setActive] = useState(false);
  const [img, setImg] = useState(null);        // {url, date, mode, mask}
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);
  const req = useRef(0);
  useEffect(() => { setDate(defaultDate); setActive(false); setImg(null); setErr(null); }, [pid]);
  useEffect(() => {
    if (!active || !date) return;
    const id = ++req.current;
    setLoading(true); setErr(null);
    call((src) => postJSON("/api/thumbnail", { source: src, pid, date, mode, mask }))
      .then((r) => r.json())
      .then((j) => { if (id === req.current) setImg({ url: j.url, date, mode, mask }); })
      .catch((e) => { if (id === req.current) setErr(e.message); })
      .finally(() => { if (id === req.current) setLoading(false); });
  }, [active, date, mode, mask, pid]);
  if (!options.length) return html`<p class="cap">Aucune image ne couvre cette parcelle sur la période.</p>`;
  const infra = mode === "infrarouge";
  return html`<div class="thumb">
    <select class="sel" value=${date} onChange=${(e) => setDate(e.target.value)}>
      ${options.map((o) => html`<option key=${o.date} value=${o.date}>${o.label}</option>`)}
    </select>
    <div class="trow">
      <div class="seg"><button class=${mode === "naturel" ? "on" : ""} onClick=${() => setMode("naturel")}>Naturelles</button>
        <button class=${infra ? "on" : ""} title="La végétation verte apparaît en rouge vif, même jeune ou clairsemée (levée, adventices) ; sol nu gris-brun à bleuté."
          onClick=${() => setMode("infrarouge")}>Infrarouge</button></div>
      <label class="check" title="Nuages, ombres, cirrus et marge autour des nuages, selon les réglages du masque."><input type="checkbox" checked=${mask} onChange=${(e) => setMask(e.target.checked)} /> Pixels masqués en couleur</label>
      ${!active && html`<button class="primary" disabled=${!ACTIONS.gee.ok} onClick=${() => setActive(true)}>Afficher l'image</button>`}
    </div>
    ${!ACTIONS.gee.ok && html`<p class="cap warnc">Earth Engine non connecté : ${ACTIONS.gee.message || "connexion en cours…"}</p>`}
    ${active && html`<div class=${"imgbox " + (loading ? "loading" : "")}>
      ${img ? html`<img src=${img.url} alt=${"Sentinel-2 du " + fmtIso(img.date)} />` : html`<div class="imgph small"><span>${loading ? "Génération de l'image…" : ""}</span></div>`}
      ${loading && img && html`<span class="imgload">Génération…</span>`}
    </div>`}
    ${err && html`<p class="cap errc">Erreur Earth Engine (image) : ${err}</p>`}
    ${img && html`<p class="cap">Sentinel-2 du ${fmtIso(img.date)}, ${img.mode === "infrarouge" ? "fausses couleurs infrarouges (végétation verte en rouge)" : "couleurs naturelles"}${" "}· contour blanc : parcelle · contour jaune : zone analysée (après buffer)${img.mask ? (img.mode === "infrarouge" ? " · cyan" : " · magenta") + " : pixels masqués" : ""}. Lien temporaire (quelques heures).</p>`}
  </div>`;
}

/* Liste des dates de la vignette (comme Streamlit) : NDVI, retenue ou écartée (motif) */
function thumbOptions(p) {
  return p.meas.filter((m) => !NO_IMAGE_STATUSES.includes(m[14])).map((m) => {
    const note = MOTIF(m) || (m[2] ? "" : "Statut : " + m[14]);
    return { date: isoDay(m[0]), retained: !!m[2],
      label: `${fmtDay(m[0])} — NDVI ${fr(m[1], 3)} · ${m[2] ? "retenue" + (note ? ` (${note.toLowerCase()})` : "") : `écartée (${note})`}` };
  });
}

function ImageBlock({ p }) {
  const opts = useMemo(() => thumbOptions(p), [p]);
  const kept = opts.filter((o) => o.retained);
  const dflt = (kept.length ? kept[kept.length - 1] : opts[opts.length - 1] || {}).date;
  return html`<div class="box">
    <h2>Image satellite</h2>
    <${ThumbBox} source=${null} pid=${p.id} options=${opts} defaultDate=${dflt}
      call=${(fn) => ACTIONS.withKey((k) => fn({ type: "analysis", key: k }))} />
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
    <p class="note">¹ Sol humide : B11 ${"<"} 0,15, ou pluie des 3 jours ≥ 10 mm, de la veille ≥ 5 mm, humidité du sol ≥ 0,40 m³/m³. Repères indicatifs, à calibrer sur le terrain.</p>
  </div>`;
}

function WeatherBlock({ day, W }) {
  const info = ACTIONS.info || {};
  const [busy, setBusy] = useState(false);
  const refresh = async () => { setBusy(true); try { await ACTIONS.weather(true); } finally { setBusy(false); } };
  const last = D.weather ? (() => { let k = D.nDays - 1; while (k >= 0 && D.weather.rain[k] == null) k--; return k; })() : -1;
  return html`<div>
    <div class="h3row"><h3>Météo de la zone (ERA5-Land)</h3>
      ${ACTIONS.gee.ok && html`<button class="tool small" disabled=${busy} onClick=${refresh} title="Nouvelle requête : derniers jours publiés depuis la précédente">${busy ? "Actualisation…" : "Actualiser la météo"}</button>`}</div>
    ${D.weather ? html`<${WeatherCharts} day=${day} W=${W} />
      <p class="cap">Barres : pluie journalière (mm). ▼ gel (≤ −2 °C). Trait brun : humidité du sol entre 0 et 7 cm (m³/m³) ;
        tirets : 0,32 (ressuyé) et 0,40 (proche saturation). ${last >= 0 && last < D.nDays - 1 ? `Météo publiée jusqu'au ${fmtDay(last)} (quelques jours de retard).` : ""} Valeurs du jour survolé sous les graphiques (sinon celles du repère bleu).</p>`
      : html`<p class="cap">${info.weather_error ? "Météo ERA5-Land indisponible lors du calcul : " : "Météo absente de ce fichier : "}la lecture du sol se fait sans pluie ni humidité.${ACTIONS.gee.ok ? " « Actualiser la météo » pour la récupérer." : ""}</p>`}
  </div>`;
}

function Detail({ s, sel, setSel, ids, showMap, setShowMap, visible }) {
  const p = D.parcels.find((x) => x.id === sel) || D.parcels[0];
  const i = ids.indexOf(p.id);
  const step = (d) => ids.length && setSel(ids[((i < 0 ? 0 : i) + d + ids.length) % ids.length]);
  const [day, setDay] = useState(LAST_DATE);
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
  const cbox = useRef(null);
  const cw = useWidth(cbox);
  const W = cw > 50 ? Math.round(cw) : 400;
  const lect = p.lect_fin && p.lect_fin !== "—" ? p.lect_fin : null;
  return html`<section class="detail" ref=${det}>
    <div class="dhead">
      <div class="ftitle">
        <h1>${p.id}</h1>
        <span class="pill" style=${{ background: PH[ph], color: ph === "peu" || ph === "none" ? "var(--ink)" : "#fff" }}>${PH_LABEL[ph]} en fin de période</span>
        ${lect && html`<span class="pill lect">${lect}</span>`}
        ${inGrey(p, s) && html`<span class="pill warnp">À vérifier (zone grise)</span>`}
        <span class="pill conf">Confiance ${(p.conf || "—").toLowerCase()}</span>
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
      <div><span>Plus long trou sans mesure</span><b>${p.trou ?? "—"} j</b></div>
      <div><span>Baisse rapide</span><b class="small">${!p.drops || p.drops === "—" ? "aucune" : p.drops}</b></div>
      <div title=${H("Jours_couverts")}><span>${`Sol couvert (NDVI lissé ≥ ${fr(s.low)})`}</span>
        <b>${p.cov[0] == null ? "—" : `${p.cov[0]} / ${spanDays()} j · ${p.cov[1]} %`}</b>
        ${p.cov[0] != null && html`<em>${`dont ${p.cov[2]} j incertains${p.cov[3] ? ` · plus longue : ${p.cov[3]} j (${isoFrShort(p.cov[4])} → ${isoFrShort(p.cov[5])})` : ""}`}</em>`}</div>
    </div>
    ${showMap && html`<div class="mappanel">
      <${PhaseMap} s=${s} day=${day} sel=${p.id} setSel=${setSel} visible=${visible} />
      <p class="cap">Phases au ${fmtDay(day)} (date du repère bleu de la courbe). Clic sur une parcelle : ouvre sa fiche. Parcelles hors filtres en transparence.</p>
    </div>`}
    <div class="fgrid">
      <div class="box" ref=${cbox}>
        <h2>Évolution du NDVI</h2>
        <p class="cap">${p.chrono}</p>
        <${ParcelChart} p=${p} settings=${s} day=${day} setDay=${setDay} W=${W} />
        <p class="cap">● mesure retenue, ○ mesure écartée, pointillé : courbe interpolée à plus de ${s.uncertain_days ?? 15} jours de toute mesure, tirets : seuils. Valeurs du jour survolé et de la mesure la plus proche sous la courbe (sinon celles du repère bleu) ; clic : place le repère bleu${showMap ? " (la carte suit)" : ""}.</p>
        <${WeatherBlock} day=${day} W=${W} />
        <h3>NDTI — résidus de culture (expérimental)</h3>
        <${NdtiChart} p=${p} s=${s} day=${day} W=${W} />
        <p class="cap">Points pleins quand la parcelle est peu verte (NDVI ${"<"} ${fr(s.low)}), seuls interprétables ; cercle orange : sol humide ou à vérifier (NDTI non lu) ; cercle gris : parcelle verte. Survol d’un point : NDTI, état du sol et lecture sous le graphique. Lignes : repères indicatifs, à calibrer.</p>
      </div>
      <div class="col">
        <${ImageBlock} p=${p} />
        <${ReadingBox} p=${p} s=${s} />
      </div>
    </div>
    <div class="box">
      <h2>Mesures de la parcelle</h2>
      <${SortTable} sticky=${1} rows=${p.meas.map((m) => ({ id: m[0], m }))} sel=${day} onRow=${(k) => setDay(k)} cols=${[
        { k: "d", label: "Date", help: H("Date"), sort: (r) => r.m[0], render: (r) => fmtDay(r.m[0]) },
        { k: "v", label: "NDVI", num: 1, help: H("NDVI"), sort: (r) => r.m[1], render: (r) => fr(r.m[1], 3) },
        { k: "l", label: "NDVI lissé", num: 1, help: H("NDVI_lisse"), sort: (r) => r.m[13], render: (r) => fr(r.m[13], 3) },
        { k: "ph", label: "Phase", help: H("Phase"), sort: (r) => r.m[13], render: (r) => (r.m[13] != null ? PH_LABEL[phaseOf(r.m[13], s)] : "—") },
        { k: "fi", label: "Fiabilité", help: H("Fiabilite"), sort: (r) => r.m[8], render: (r) => r.m[17] || "—" },
        { k: "po", label: "Poids", num: 1, help: H("Poids"), sort: (r) => r.m[8], render: (r) => fr(r.m[8]) },
        { k: "r", label: "Retenue", help: H("Retenue"), sort: (r) => r.m[2], render: (r) => (r.m[2] ? "Oui" : "Non") },
        { k: "ci", label: "Courbe incertaine", help: H("Incertain"), sort: (r) => uncAt(p, r.m[0]), render: (r) => (!p.curve ? "—" : uncAt(p, r.m[0]) ? "Oui" : "Non") },
        { k: "mo", label: "Motif", help: H("Motif"), sort: (r) => MOTIF(r.m), render: (r) => MOTIF(r.m) },
        { k: "nt", label: "NDTI", num: 1, help: H("NDTI"), sort: (r) => r.m[3], render: (r) => fr(r.m[3], 3) },
        { k: "es", label: "État du sol", help: H("Etat_sol"), sort: (r) => r.m[4], render: (r) => D.etats[r.m[4]] },
        { k: "le", label: "Lecture", help: H("Lecture"), sort: (r) => r.m[5], render: (r) => D.lectures[r.m[5]] },
        { k: "p3", label: "Pluie 3 j (mm)", num: 1, help: H("Pluie_3j_mm"), sort: (r) => r.m[10], render: (r) => fr(r.m[10], 1) },
        { k: "pv", label: "Pluie veille (mm)", num: 1, help: H("Pluie_veille_mm"), sort: (r) => r.m[15], render: (r) => fr(r.m[15], 1) },
        { k: "hs", label: "Humidité sol (m³/m³)", num: 1, help: H("Humidite_sol"), sort: (r) => r.m[11], render: (r) => fr(r.m[11]) },
        { k: "tm", label: "Tmin 7 j (°C)", num: 1, help: H("Tmin_7j_C"), sort: (r) => r.m[16], render: (r) => fr(r.m[16], 1) },
        { k: "ge", label: "Gel", help: H("Gel"), sort: (r) => r.m[12], render: (r) => r.m[12] || "—" },
        { k: "st", label: "Statut", help: H("Statut"), sort: (r) => r.m[14], render: (r) => r.m[14] || "—" },
        { k: "cl", label: "Clair (%)", num: 1, help: H("Clair_pct"), sort: (r) => r.m[9], render: (r) => fr(r.m[9], 0) },
      ]} />
      <p class="cap">Clic sur une ligne : place le repère à cette date sur les graphiques${showMap ? " et la carte" : ""}. Survol d'un en-tête : définition de la colonne.</p>
    </div>
  </section>`;
}

function Review({ s, sel, setSel }) {
  const all = useMemo(() => Object.fromEntries(D.parcels.map((p) => [p.id, p])), [D]);
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
  }, [f, s, D]);
  const visible = useMemo(() => new Set(ids), [ids]);
  return html`<div class=${"review " + (collapsed ? "folded" : "")}>
    <${ParcelList} s=${s} ids=${ids} all=${all} sel=${sel} setSel=${setSel} f=${f} setF=${setF}
      curves=${curves} setCurves=${setCurves} collapsed=${collapsed} setCollapsed=${setCollapsed} />
    <${Detail} s=${s} sel=${sel} setSel=${setSel} ids=${ids} showMap=${showMap} setShowMap=${setShowMap} visible=${visible} />
  </div>`;
}
