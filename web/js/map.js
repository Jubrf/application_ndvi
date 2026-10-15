/* ---------------------------------------------------------------- carte (fond IGN, contours en SVG) */
const TILE = {
  ortho: (z, x, y) => `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX=${z}&TILEROW=${y}&TILECOL=${x}`,
  plan: (z, x, y) => `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/png&TILEMATRIX=${z}&TILEROW=${y}&TILECOL=${x}`,
};

/* Carte générique.
   shapes : {ids, proj, bbox} (makeShapes) ; fillOf(id) → [couleur, opacité] ;
   tipOf(id) → contenu de l'infobulle ; label : encart en haut à gauche ;
   sel / setSel : parcelle choisie (clic). */
function MapView({ shapes, fillOf, tipOf, label, sel, setSel }) {
  const box = useRef(null);
  const [size, setSize] = useState(null);
  const [view, setView] = useState(null); // {cx, cy, z}
  const [base, setBase] = useState("ortho");
  const [hover, setHover] = useState(null);
  const drag = useRef(null);
  const BB = shapes.bbox;

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
  useEffect(() => { if (!view && size && size[0] > 10) fit(size); }, [size]);

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
    const isSel = id === sel, isHov = id === hover;
    return html`<path key=${id} d=${d} fill=${fill} fill-opacity=${op}
      stroke=${isSel ? "#10201a" : isHov ? "#2f6f8f" : "#ffffff"} stroke-width=${isSel ? 3 : isHov ? 2.5 : 1}
      class="parcel" onMouseEnter=${() => setHover(id)} onMouseLeave=${() => setHover(null)}
      onClick=${() => { if (!drag.current?.moved && setSel) setSel(id); }} />`;
  });
  return html`<div class="map" ref=${box} onWheel=${onWheel} onPointerDown=${onDown} onPointerMove=${onMove}
      onPointerUp=${onUp} onPointerLeave=${onUp}>
    <div class="tiles">${tiles}</div>
    <svg class="overlay" width=${w} height=${h}>${polys}</svg>
    ${label && html`<div class="map-date">${label}</div>`}
    ${hover != null && tipOf && html`<div class="map-tip">${tipOf(hover)}</div>`}
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

/* Carte des phases de l'analyse ouverte au jour `day` (courbe lissée) */
function PhaseMap({ s, day, sel, setSel, visible }) {
  const byId = useMemo(() => Object.fromEntries(D.parcels.map((p) => [p.id, p])), [D]);
  const shapes = { ids: D.parcels.map((p) => p.id), proj: PROJ, bbox: BBOX };
  const fillOf = (id) => [PH[phaseOf(curveAt(byId[id], day), s)], visible && !visible.has(id) ? 0.18 : 0.78];
  const tipOf = (id) => {
    const p = byId[id], v = curveAt(p, day);
    return html`<b>${id}</b> · ${fr(p.ha)} ha<br/>NDVI lissé ${fr(v)} — ${PH_LABEL[phaseOf(v, s)]}`;
  };
  return html`<${MapView} shapes=${shapes} fillOf=${fillOf} tipOf=${tipOf} sel=${sel} setSel=${setSel}
    label=${html`<strong>${fmtDay(day)}</strong><span>phase de la courbe lissée ce jour-là</span>`} />`;
}
