/* ---------------------------------------------------------------- carte (fond IGN, contours en SVG) */
const TILE = {
  ortho: (z, x, y) => `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX=${z}&TILEROW=${y}&TILECOL=${x}`,
  plan: (z, x, y) => `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/png&TILEMATRIX=${z}&TILEROW=${y}&TILECOL=${x}`,
};

/* Carte générique.
   shapes : {ids, proj, bbox} (makeShapes) ; fillOf(id) → [couleur, opacité] ;
   tipOf(id) → contenu de l'infobulle ; label : encart en haut à gauche ;
   sel / setSel : parcelle choisie (clic, contour jaune) ; focus : parcelle sur laquelle centrer et
   zoomer à l'ouverture (bouton « Voir sur la carte » de la fiche). Noms des parcelles au centre des
   contours, sans chevauchement (bouton « Noms »). */
/* Point d'étiquette (centre de gravité du plus grand anneau) et taille d'une parcelle, en coordonnées projetées */
function labelSpot(rings) {
  let best = null;
  for (const r of rings) {
    let a = 0, cx = 0, cy = 0;
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const f = r[j][0] * r[i][1] - r[i][0] * r[j][1];
      a += f; cx += (r[j][0] + r[i][0]) * f; cy += (r[j][1] + r[i][1]) * f;
    }
    if (!best || Math.abs(a) > Math.abs(best.a)) {
      const xs = r.map((p) => p[0]), ys = r.map((p) => p[1]);
      const c = Math.abs(a) > 1e-18 ? [cx / (3 * a), cy / (3 * a)] : [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
      best = { a, c, w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
    }
  }
  return best;
}
function MapView({ shapes, fillOf, tipOf, label, sel, setSel, focus }) {
  const box = useRef(null);
  const [size, setSize] = useState(null);
  const [view, setView] = useState(null); // {cx, cy, z}
  const [base, setBase] = useState("ortho");
  const [hover, setHover] = useState(null);
  const [names, setNames] = useState(true);
  const drag = useRef(null);
  const BB = shapes.bbox;
  const spots = useMemo(() => shapes.proj.map((rings) => (rings.length ? labelSpot(rings) : null)), [shapes.proj]);

  const fit = useCallback((sz = size) => {
    const [w, h] = sz;
    const bw = Math.max(1e-7, BB[2] - BB[0]), bh = Math.max(1e-7, BB[3] - BB[1]);
    const z = Math.log2(Math.min((w * 0.86) / (bw * 256), (h * 0.86) / (bh * 256)));
    setView({ cx: (BB[0] + BB[2]) / 2, cy: (BB[1] + BB[3]) / 2, z: Math.min(z, 17) });
  }, [size, shapes]);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize([e.contentRect.width, e.contentRect.height]));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  const fitParcel = (id, sz = size) => {
    const i = shapes.ids.indexOf(id), pts = i < 0 ? [] : shapes.proj[i].flat();
    if (!pts.length) return false;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const bw = Math.max(1e-7, Math.max(...xs) - Math.min(...xs)), bh = Math.max(1e-7, Math.max(...ys) - Math.min(...ys));
    const z = Math.log2(Math.min((sz[0] * 0.45) / (bw * 256), (sz[1] * 0.45) / (bh * 256)));
    setView({ cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: (Math.min(...ys) + Math.max(...ys)) / 2, z: Math.min(z, 17.5) });
    return true;
  };
  useEffect(() => { if (!view && size && size[0] > 10 && !(focus && fitParcel(focus, size))) fit(size); }, [size]);

  // centrer sur la parcelle sélectionnée si elle est hors champ
  useEffect(() => {
    if (!view || !size || sel == null) return;
    const i = shapes.ids.indexOf(sel);
    if (i < 0) return;
    const pts = shapes.proj[i].flat();
    const ws = 256 * 2 ** view.z;
    const out = pts.some(([x, y]) => {
      const sx = (x - view.cx) * ws + size[0] / 2, sy = (y - view.cy) * ws + size[1] / 2;
      return sx < 30 || sy < 30 || sx > size[0] - 30 || sy > size[1] - 30;
    });
    if (out && pts.length) {
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
    const nz = Math.max(8, Math.min(18.5, view.z - e.deltaY * 0.0022));
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
  const polys = shapes.ids.map((id, i) => {
    const [fill, op] = fillOf(id);
    const d = shapes.proj[i].map((r) => "M" + r.map(toScreen).map(([x, y]) => x.toFixed(1) + "," + y.toFixed(1)).join("L") + "Z").join("");
    const isHov = id === hover;
    return html`<path key=${id} d=${d} fill=${fill} fill-opacity=${op}
      stroke=${isHov ? "#2f6f8f" : "#ffffff"} stroke-width=${isHov ? 2.5 : 1}
      class="parcel" onMouseEnter=${() => setHover(id)} onMouseLeave=${() => setHover(null)}
      onClick=${() => { if (!drag.current?.moved && setSel) setSel(id); }} />`;
  });
  // parcelle choisie : contour jaune sur liseré sombre, dessiné par-dessus les autres
  const si = sel == null ? -1 : shapes.ids.indexOf(sel);
  const selD = si < 0 ? null : shapes.proj[si].map((r) => "M" + r.map(toScreen).map(([x, y]) => x.toFixed(1) + "," + y.toFixed(1)).join("L") + "Z").join("");
  // noms : des plus grandes parcelles aux plus petites, sans chevauchement entre étiquettes
  const labels = [];
  if (names) {
    const placed = [];
    const order = shapes.ids.map((id, i) => i).filter((i) => spots[i]).sort((a, b) => spots[b].w * spots[b].h - spots[a].w * spots[a].h);
    for (const i of order) {
      const id = shapes.ids[i], [x, y] = toScreen(spots[i].c);
      const tw = String(id).length * 6.4 + 6, th = 15;
      if (x < -tw || y < -th || x > w + tw || y > h + th) continue;
      const bx = [x - tw / 2, y - th / 2, x + tw / 2, y + th / 2];
      if (placed.some((q) => bx[0] < q[2] && bx[2] > q[0] && bx[1] < q[3] && bx[3] > q[1])) continue;
      placed.push(bx);
      labels.push(html`<text key=${"t" + id} x=${x.toFixed(1)} y=${(y + 4).toFixed(1)} class="plabel">${id}</text>`);
    }
  }
  return html`<div class="map" ref=${box} onWheel=${onWheel} onPointerDown=${onDown} onPointerMove=${onMove}
      onPointerUp=${onUp} onPointerLeave=${onUp}>
    <div class="tiles">${tiles}</div>
    <svg class="overlay" width=${w} height=${h}>${polys}
      ${selD && html`<path d=${selD} fill="none" stroke="#10201a" stroke-width="5" stroke-linejoin="round" pointer-events="none" />
        <path d=${selD} fill="none" stroke="#ffd400" stroke-width="3" stroke-linejoin="round" pointer-events="none" />`}
      ${labels}</svg>
    ${label && html`<div class="map-date">${label}</div>`}
    ${hover != null && tipOf && html`<div class="map-tip">${tipOf(hover)}</div>`}
    <div class="map-tools">
      <div class="seg">
        ${[["ortho", "Photo"], ["plan", "Plan"], ["aucun", "Sans fond"]].map(([k, l]) =>
          html`<button key=${k} class=${base === k ? "on" : ""} onClick=${() => setBase(k)}>${l}</button>`)}
      </div>
      <button class=${"tool " + (names ? "act" : "")} title="Noms des parcelles (ceux qui se chevauchent sont masqués ; zoomer pour les voir tous)" onClick=${() => setNames(!names)}>Noms</button>
      <button class="tool" title="Voir toutes les parcelles" onClick=${() => fit()}>Recentrer</button>
    </div>
    <div class="attrib">${base !== "aucun" ? "Fond : © IGN Géoplateforme" : ""}</div>
  </div>`;
}

/* Carte des phases de l'analyse ouverte au jour `day` (courbe lissée) */
function PhaseMap({ s, day, sel, setSel, visible, focus }) {
  const byId = useMemo(() => Object.fromEntries(D.parcels.map((p) => [p.id, p])), [D]);
  const shapes = { ids: D.parcels.map((p) => p.id), proj: PROJ, bbox: BBOX };
  const fillOf = (id) => [PH[phaseOf(curveAt(byId[id], day), s)], visible && !visible.has(id) ? 0.18 : 0.78];
  const tipOf = (id) => {
    const p = byId[id], v = curveAt(p, day);
    return html`<b>${id}</b> · ${fr(p.ha)} ha<br/>NDVI lissé au ${fmtShort(day)} : ${fr(v)} — ${PH_LABEL[phaseOf(v, s)]}<br/>
      ${`NDVI fin : lissé ${fr(p.ndvi_fin)} · brut ${fr(p.ndvi_brut)}${p.last ? ` (${isoFrShort(p.last)})` : ""}`}`;
  };
  return html`<${MapView} shapes=${shapes} fillOf=${fillOf} tipOf=${tipOf} sel=${sel} setSel=${setSel} focus=${focus}
    label=${html`<strong>${fmtDay(day)}</strong><span>phase de la courbe lissée ce jour-là</span>`} />`;
}
