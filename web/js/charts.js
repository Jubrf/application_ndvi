/* ---------------------------------------------------------------- graphiques de la fiche parcelle
   Même axe des dates (marges L et R) pour la courbe NDVI, la météo et le NDTI : dates alignées. */
const AX_L = 34, AX_R = 8;
const monthStarts = (n) => { const out = []; for (let k = 1; k < n; k++) if (dayDate(k).getDate() === 1) out.push(k); return out; };
const xOf = (W) => (k) => AX_L + (k / Math.max(1, D.nDays - 1)) * (W - AX_L - AX_R);
const kOf = (e, W) => {
  const r = e.currentTarget.getBoundingClientRect();
  const k = Math.round(((e.clientX - r.left) * (W / r.width) - AX_L) / (W - AX_L - AX_R) * (D.nDays - 1));
  return Math.max(0, Math.min(D.nDays - 1, k));
};
function Tip({ x, W, children }) {
  const left = Math.max(12, Math.min(88, (x / W) * 100));
  return html`<div class="ctip" style=${{ left: left + "%" }}>${children}</div>`;
}
const motifTxt = (m) => (m[6] ? D.motifs[m[6] - 1] : m[2] ? "" : (m[14] ? "Statut : " + m[14] : ""));

/* Courbe NDVI : zones des phases, seuils, mesures retenues (●) et écartées (○), repère du jour */
function ParcelChart({ p, settings, day, setDay, W = 400 }) {
  const Hh = 230, T = 8, B = 32;
  const n = D.nDays, X = xOf(W), Y = (v) => T + (1 - v) * (Hh - T - B);
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
  const months = monthStarts(n);
  const pts = p.meas.filter((m) => m[1] != null);
  const hm = hov != null ? pts.reduce((best, m) => (Math.abs(m[0] - hov) <= 3 && (!best || Math.abs(m[0] - hov) < Math.abs(best[0] - hov)) ? m : best), null) : null;
  const lv = hov != null ? curveAt(p, hov) : null;
  return html`<div class="chartwrap">
    <svg viewBox=${`0 0 ${W} ${Hh}`} class="chart" onMouseMove=${(e) => setHov(kOf(e, W))} onMouseLeave=${() => setHov(null)}
        onClick=${() => hov != null && setDay(hov)}>
      <rect x=${AX_L} y=${Y(settings.low)} width=${W - AX_L - AX_R} height=${Y(-0.05) - Y(settings.low)} fill="#d73027" opacity="0.07" />
      <rect x=${AX_L} y=${Y(settings.high)} width=${W - AX_L - AX_R} height=${Y(settings.low) - Y(settings.high)} fill="#a6d96a" opacity="0.13" />
      <rect x=${AX_L} y=${Y(1)} width=${W - AX_L - AX_R} height=${Y(settings.high) - Y(1)} fill="#1a9850" opacity="0.08" />
      ${[0, 0.25, 0.5, 0.75, 1].map((v) => html`<text key=${v} x=${AX_L - 5} y=${Y(v) + 3} class="ax" text-anchor="end">${fr(v, v % 0.5 ? 2 : 1)}</text>`)}
      ${[settings.low, settings.high].map((t) => html`<line key=${t} x1=${AX_L} x2=${W - AX_R} y1=${Y(t)} y2=${Y(t)} class="thr" />`)}
      ${months.map((k) => html`<g key=${k}><line x1=${X(k)} x2=${X(k)} y1=${T} y2=${Hh - B} class="grid" />
        <text x=${X(k) + 3} y=${Hh - 6} class="ax">${MONTHS[dayDate(k).getMonth()]}</text></g>`)}
      ${segs.map((sg, i) => html`<polyline key=${i} points=${sg.pts.join(" ")} fill="none" stroke="#2a78d6"
          stroke-width="2.2" stroke-dasharray=${sg.unc ? "4 3" : ""} />`)}
      ${pts.map((m) => m[2]
        ? html`<circle key=${m[0]} cx=${X(m[0])} cy=${Y(Math.max(-0.05, m[1]))} r=${hm === m ? 5 : 3.6} fill="#2a78d6" stroke="#fff" stroke-width="1" />`
        : html`<circle key=${m[0]} cx=${X(m[0])} cy=${Y(Math.max(-0.05, m[1]))} r=${hm === m ? 4.6 : 3.2} fill="#fff" stroke="#8a8f88" stroke-width="1.2" />`)}
      <line x1=${X(day)} x2=${X(day)} y1=${T} y2=${Hh - B} class="cursor" />
      ${hov != null && html`<line x1=${X(hov)} x2=${X(hov)} y1=${T} y2=${Hh - B} class="hovline" />`}
    </svg>
    ${hov != null && html`<${Tip} x=${X(hov)} W=${W}>
      <b>${fmtDay(hov)}</b><br/>NDVI lissé ${fr(lv, 3)}${lv != null ? " · " + PH_LABEL[phaseOf(lv, settings)] : ""}
      ${hm && html`<div class="tsep"></div><b>Mesure du ${fmtDay(hm[0])}</b>${hm[2] ? " (retenue)" : " (écartée)"}<br/>
        ${hm[2] ? "NDVI mesuré" : "NDVI brut"} ${fr(hm[1], 3)}
        ${hm[17] ? html`<br/>Fiabilité ${hm[17].toLowerCase()}${hm[8] != null ? ` (poids ${fr(hm[8])})` : ""}` : ""}
        ${motifTxt(hm) ? html`<br/>${hm[2] ? "Remarque" : "Écartée"} : ${motifTxt(hm)}` : ""}`}
      <br/><span class="muted">Clic : place le repère ici</span><//>`}
  </div>`;
}

/* Météo ERA5-Land (comme Streamlit) : pluie journalière + gel, puis humidité du sol 0–7 cm */
function WeatherCharts({ day, W = 400 }) {
  const wx = D.weather, n = D.nDays, X = xOf(W);
  const [hov, setHov] = useState(null);
  const maxR = Math.max(10, ...wx.rain.filter((v) => v != null));
  const R_H = 90, S_H = 100, SM = [0.1, 0.5], Ys = (v) => 6 + (1 - (v - SM[0]) / (SM[1] - SM[0])) * (S_H - 24);
  const sm = [];
  let seg = [];
  wx.sm.forEach((v, k) => { if (v == null) { if (seg.length) sm.push(seg); seg = []; } else seg.push(X(k).toFixed(1) + "," + Ys(Math.max(SM[0], Math.min(SM[1], v))).toFixed(1)); });
  if (seg.length) sm.push(seg);
  const months = monthStarts(n);
  const events = { onMouseMove: (e) => setHov(kOf(e, W)), onMouseLeave: () => setHov(null) };
  return html`<div class="chartwrap">
    <svg viewBox=${`0 0 ${W} ${R_H}`} class="chart" ...${events}>
      <text x=${AX_L - 5} y="12" class="ax" text-anchor="end">${Math.round(maxR)}</text>
      <text x=${AX_L - 5} y=${R_H - 4} class="ax" text-anchor="end">0</text>
      <text x="2" y=${R_H / 2} class="ax">mm</text>
      ${months.map((k) => html`<line key=${k} x1=${X(k)} x2=${X(k)} y1="4" y2=${R_H - 4} class="grid" />`)}
      ${wx.rain.map((v, k) => v ? html`<rect key=${k} x=${X(k) - 1} y=${R_H - 4 - (v / maxR) * (R_H - 12)} width="2" height=${(v / maxR) * (R_H - 12)} fill="#3b7dd8" opacity="0.85" />` : null)}
      ${wx.tmin.map((v, k) => v != null && v <= -2 ? html`<path key=${"g" + k} d=${`M${X(k) - 3.5},4 L${X(k) + 3.5},4 L${X(k)},10 Z`} fill="#5bc0eb" />` : null)}
      <line x1=${X(day)} x2=${X(day)} y1="0" y2=${R_H} class="cursor" />
      ${hov != null && html`<line x1=${X(hov)} x2=${X(hov)} y1="0" y2=${R_H} class="hovline" />`}
    </svg>
    <svg viewBox=${`0 0 ${W} ${S_H}`} class="chart" ...${events}>
      ${[0.2, 0.3, 0.4].map((v) => html`<text key=${v} x=${AX_L - 5} y=${Ys(v) + 3} class="ax" text-anchor="end">${fr(v, 1)}</text>`)}
      ${[0.32, 0.40].map((v) => html`<line key=${v} x1=${AX_L} x2=${W - AX_R} y1=${Ys(v)} y2=${Ys(v)} class="thr" />`)}
      ${months.map((k) => html`<g key=${k}><line x1=${X(k)} x2=${X(k)} y1="4" y2=${S_H - 18} class="grid" />
        <text x=${X(k) + 3} y=${S_H - 4} class="ax">${MONTHS[dayDate(k).getMonth()]}</text></g>`)}
      ${sm.map((sg, i) => html`<polyline key=${i} points=${sg.join(" ")} fill="none" stroke="#8a5a2b" stroke-width="2" />`)}
      <line x1=${X(day)} x2=${X(day)} y1="0" y2=${S_H - 18} class="cursor" />
      ${hov != null && html`<line x1=${X(hov)} x2=${X(hov)} y1="0" y2=${S_H - 18} class="hovline" />`}
    </svg>
    ${hov != null && html`<${Tip} x=${X(hov)} W=${W}>
      <b>${fmtDay(hov)}</b><br/>Pluie : ${fr(wx.rain[hov], 1)} mm<br/>Humidité du sol : ${fr(wx.sm[hov])} m³/m³<br/>Température min. : ${fr(wx.tmin[hov], 1)} °C<//>`}
  </div>`;
}

/* NDTI (expérimental, comme Streamlit) : toutes les mesures ; points pleins si la parcelle est
   peu verte (NDVI < seuil bas, seuls interprétables), cercle orange si le sol est humide. */
function NdtiChart({ p, s, day, W = 400 }) {
  const pts = p.meas.filter((m) => m[3] != null);
  const [hov, setHov] = useState(null);
  if (!pts.length) return html`<p class="cap">Pas de valeur NDTI pour cette parcelle (analyse enregistrée avant la v2.5).</p>`;
  const Hh = 150, T = 8, B = 20, X = xOf(W);
  const vals = pts.map((m) => m[3]);
  const lo = Math.min(-0.05, Math.floor(Math.min(...vals) / 0.05) * 0.05), hi = Math.max(0.30, Math.ceil(Math.max(...vals) / 0.05) * 0.05);
  const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (Hh - T - B);
  const readable = (m) => m[1] != null && m[1] < s.low;
  const wet = (m) => WET_STATES.includes(D.etats[m[4]]);
  const ticks = [];
  for (let v = Math.ceil(lo / 0.1) * 0.1; v <= hi + 1e-9; v += 0.1) ticks.push(Math.round(v * 10) / 10);
  const months = monthStarts(D.nDays);
  return html`<div class="chartwrap">
    <svg viewBox=${`0 0 ${W} ${Hh}`} class="chart">
      ${ticks.map((v) => html`<text key=${v} x=${AX_L - 5} y=${Y(v) + 3} class="ax" text-anchor="end">${fr(v, 1)}</text>`)}
      ${months.map((k) => html`<g key=${k}><line x1=${X(k)} x2=${X(k)} y1=${T} y2=${Hh - B} class="grid" />
        <text x=${X(k) + 3} y=${Hh - 5} class="ax">${MONTHS[dayDate(k).getMonth()]}</text></g>`)}
      ${[[0.10, "0,10 résidus"], [0.05, "0,05 sol nu"]].map(([v, l]) => html`<g key=${v}>
        <line x1=${AX_L} x2=${W - AX_R} y1=${Y(v)} y2=${Y(v)} class="thr" />
        <text x=${W - AX_R - 4} y=${Y(v) - 3} class="ax" text-anchor="end">${l}</text></g>`)}
      ${pts.filter((m) => !readable(m)).map((m) => html`<circle key=${"g" + m[0]} cx=${X(m[0])} cy=${Y(m[3])} r="3.6" fill="#fff" stroke="#8a8f88" stroke-width="1.2" />`)}
      ${pts.filter((m) => readable(m) && wet(m)).map((m) => html`<circle key=${"w" + m[0]} cx=${X(m[0])} cy=${Y(m[3])} r="7" fill="none" stroke="#eb6834" stroke-width="2" />`)}
      ${pts.filter(readable).map((m) => html`<circle key=${"b" + m[0]} cx=${X(m[0])} cy=${Y(m[3])} r="4.2" fill="#4a3aa7" stroke="#fff" stroke-width="1.4" />`)}
      ${pts.map((m) => html`<circle key=${"h" + m[0]} cx=${X(m[0])} cy=${Y(m[3])} r="9" fill="transparent"
          onMouseEnter=${() => setHov(m)} onMouseLeave=${() => setHov(null)} />`)}
      <line x1=${X(day)} x2=${X(day)} y1=${T} y2=${Hh - B} class="cursor" />
    </svg>
    ${hov && html`<${Tip} x=${X(hov[0])} W=${W}>
      <b>${fmtDay(hov[0])}</b><br/>NDTI : ${fr(hov[3], 3)}<br/>NDVI : ${fr(hov[1], 3)}<br/>
      Interprétable : ${readable(hov) ? "Oui" : "Non (parcelle verte)"}<br/>État du sol : ${D.etats[hov[4]]}<br/>Lecture : ${D.lectures[hov[5]]}<//>`}
  </div>`;
}

/* Image « NDVI et phases » (comme la v4.1) : courbe au-dessus de la barre, mois repérés */
function EvoCell({ p, s, w = 300, h = 58 }) {
  const n = D.nDays, X = (k) => (k / (n - 1)) * w, c = p.curve;
  const months = monthStarts(n);
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

/* Dates analysées (page Projet) : une barre par image, hauteur = part des parcelles avec une
   mesure retenue ; axe des mois, graduations et légende. */
function DatesChart() {
  const W = 1000, Hh = 150, L = 46, R = 10, T = 10, B = 26;
  const n = D.nDays, X = (k) => L + (k / Math.max(1, n - 1)) * (W - L - R), Y = (f) => T + (1 - f) * (Hh - T - B);
  const share = (k) => D.parcels.filter((p) => p.meas.some((m) => m[0] === k && m[2])).length;
  const months = monthStarts(n);
  const [hov, setHov] = useState(null);
  return html`<div class="chartwrap">
    <svg viewBox=${`0 0 ${W} ${Hh}`} class="chart">
      ${[0, 0.5, 1].map((f) => html`<g key=${f}><line x1=${L} x2=${W - R} y1=${Y(f)} y2=${Y(f)} class="grid" />
        <text x=${L - 6} y=${Y(f) + 4} class="ax" text-anchor="end">${Math.round(f * 100)} %</text></g>`)}
      ${months.map((k) => html`<g key=${k}><line x1=${X(k)} x2=${X(k)} y1=${T} y2=${Hh - B} class="grid" />
        <text x=${X(k) + 3} y=${Hh - 8} class="ax">${MONTHS[dayDate(k).getMonth()]} ${dayDate(k).getFullYear()}</text></g>`)}
      ${D.dates.map((k) => {
        const c = share(k), f = c / D.parcels.length, x = X(k);
        return html`<rect key=${k} x=${x - 3} y=${Y(Math.max(f, 0.02))} width="6" height=${Y(0) - Y(Math.max(f, 0.02))}
          fill=${c ? "#1a9850" : "#c9cfc8"} rx="1" onMouseEnter=${() => setHov({ k, c, x })} onMouseLeave=${() => setHov(null)} />`;
      })}
      <line x1=${L} x2=${W - R} y1=${Y(0)} y2=${Y(0)} stroke="#9aa39c" />
    </svg>
    ${hov && html`<${Tip} x=${hov.x} W=${W}><b>${fmtDay(hov.k)}</b><br/>${hov.c} parcelle(s) sur ${D.parcels.length} avec une mesure retenue (${Math.round((hov.c / D.parcels.length) * 100)} %)<//>`}
    <div class="legend chartleg">
      <span><i style=${{ background: "#1a9850" }}></i>Image analysée : hauteur = part des parcelles avec une mesure retenue ce jour-là (0 à 100 %)</span>
      <span><i style=${{ background: "#c9cfc8" }}></i>Image analysée sans aucune mesure retenue (nuages sur toutes les parcelles)</span>
      <span>Axe horizontal : période analysée, mois repérés</span>
    </div>
  </div>`;
}
