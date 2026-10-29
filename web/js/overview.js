/* ---------------------------------------------------------------- page Carte (phases à une date) */
function MapPage({ s, sel, setSel, go, dl, focus }) {
  const [day, setDay] = useState(LAST_DATE);
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
      <${PhaseMap} s=${s} day=${day} sel=${sel} setSel=${setSel} focus=${focus} />
      ${P && html`<div class="box mini">
        <h2>${P.id}</h2>
        <p class="cap">${fr(P.ha)} ha, NDVI lissé au ${fmtShort(day)} : <b>${fr(curveAt(P, day))}</b></p>
        <${EvoCell} p=${P} s=${s} w=${300} />
        <button class="primary" onClick=${() => go("fiche", P.id)}>Ouvrir la fiche</button>
      </div>`}
    </div>
  </div>`;
}
