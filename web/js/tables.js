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
  }, [s, D]);
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

