"""
Graphique d'évolution du NDVI d'une parcelle (Altair, inclus dans Streamlit).

Couches, de l'arrière vers l'avant :
  bandes de phase (teinte légère) · lignes de seuil · courbe lissée
  (pointillée là où elle est interpolée loin de toute mesure, pleine ailleurs)
  · mesures écartées (cercles gris vides) · mesures retenues (points pleins)
  · repère vertical au survol avec la valeur lissée du jour.
"""
import altair as alt
import pandas as pd

from ndvi_app.config import COLOR_MAP, PHASES

CURVE_COLOR = "#2a78d6"     # série unique : bleu
MUTED = "#8a8984"           # seuils, libellés, mesures écartées
PHASE_TINTS = COLOR_MAP     # mêmes couleurs que les cartes

_MONTHS = "['janv.','févr.','mars','avr.','mai','juin','juil.','août','sept.','oct.','nov.','déc.']"


def _x_axis(period_start, period_end):
    days = (pd.Timestamp(period_end) - pd.Timestamp(period_start)).days
    if days <= 62:
        return alt.Axis(title=None, tickCount={"interval": "week", "step": 1},
                        labelExpr="timeFormat(datum.value, '%d/%m')")
    return alt.Axis(title=None, tickCount={"interval": "month", "step": 1},
                    labelExpr=f"{_MONTHS}[month(datum.value)] + ' ' + "
                              "substring(toString(year(datum.value)), 2, 4)")


def parcel_chart(detail, curve, period_start, period_end, low, high,
                 show_excluded=True, height=380):
    """
    detail : lignes de la parcelle (Date, NDVI, Poids, Fiabilite, Retenue, Motif)
    curve  : courbe journalière (Date, NDVI_lisse, Incertain, Phase)
    """
    p0, p1 = pd.Timestamp(period_start), pd.Timestamp(period_end)
    values = pd.concat([detail["NDVI"], detail.get("NDVI_brut", detail["NDVI"]),
                        curve["NDVI_lisse"]]).dropna()
    y_min = min(0.0, (values.min() // 0.1) * 0.1) if len(values) else 0.0
    y_scale = alt.Scale(domain=[y_min, 1.0], nice=False)
    x_enc = alt.X("Date:T", scale=alt.Scale(domain=[p0, p1]), axis=_x_axis(p0, p1))

    # Bandes de phase + libellés
    bands = pd.DataFrame({
        "x0": [p0] * 3, "x1": [p1] * 3,
        "y0": [y_min, low, high], "y1": [low, high, 1.0], "Phase": PHASES,
    })
    phase_color = alt.Color("Phase:N", legend=None,
                            scale=alt.Scale(domain=PHASES, range=[PHASE_TINTS[p] for p in PHASES]))
    layer_bands = alt.Chart(bands).mark_rect(opacity=0.10).encode(
        x="x0:T", x2="x1:T", y=alt.Y("y0:Q", scale=y_scale, title="NDVI"), y2="y1:Q",
        color=phase_color, tooltip=alt.value(None))
    layer_band_labels = alt.Chart(bands).mark_text(
        align="right", baseline="top", dx=-6, dy=4, fontSize=11, color=MUTED
    ).encode(x="x1:T", y=alt.Y("y1:Q", scale=y_scale), text="Phase:N")

    # Lignes de seuil
    thresholds = pd.DataFrame({"y": [low, high], "x0": [p0, p0],
                               "label": [f"{low:.2f}".replace(".", ","),
                                         f"{high:.2f}".replace(".", ",")]})
    layer_rules = alt.Chart(thresholds).mark_rule(
        strokeDash=[4, 4], strokeWidth=1, color=MUTED).encode(y=alt.Y("y:Q", scale=y_scale))
    layer_rule_labels = alt.Chart(thresholds).mark_text(
        align="left", baseline="bottom", dx=4, dy=-3, fontSize=11, color=MUTED
    ).encode(x="x0:T", y=alt.Y("y:Q", scale=y_scale), text="label:N")

    layers = [layer_bands, layer_band_labels, layer_rules, layer_rule_labels]

    # Courbe lissée : pointillée partout, pleine là où elle est proche d'une mesure
    if not curve.empty:
        c = curve.copy()
        c["NDVI_sur"] = c["NDVI_lisse"].where(~c["Incertain"].astype(bool))
        base = alt.Chart(c).encode(x=x_enc)
        layers.append(base.mark_line(strokeWidth=1.5, strokeDash=[3, 3], color=CURVE_COLOR,
                                     opacity=0.6).encode(y=alt.Y("NDVI_lisse:Q", scale=y_scale)))
        layers.append(base.mark_line(strokeWidth=2, color=CURVE_COLOR).encode(
            y=alt.Y("NDVI_sur:Q", scale=y_scale)))

        hover = alt.selection_point(nearest=True, on="pointerover", fields=["Date"],
                                    empty=False, clear="pointerout")
        layers.append(base.mark_point(opacity=0, size=200).encode(
            y=alt.Y("NDVI_lisse:Q", scale=y_scale)).add_params(hover))
        layers.append(base.mark_rule(color=MUTED, strokeWidth=1).encode(
            tooltip=[alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
                     alt.Tooltip("NDVI_lisse:Q", title="NDVI lissé", format=".3f"),
                     alt.Tooltip("Phase:N", title="Phase")],
        ).transform_filter(hover))

    # Mesures
    d = detail.copy()
    brut = d["NDVI_brut"] if "NDVI_brut" in d else d["NDVI"]
    d["NDVI_aff"] = d["NDVI"].where(d["Retenue"] == "Oui", brut)
    d = d.dropna(subset=["NDVI_aff"])
    kept = d[d["Retenue"] == "Oui"]
    excl = d[d["Retenue"] != "Oui"]
    if show_excluded and not excl.empty:
        layers.append(alt.Chart(excl).mark_point(
            shape="circle", filled=False, size=60, strokeWidth=1.5, color=MUTED).encode(
            x=x_enc, y=alt.Y("NDVI_aff:Q", scale=y_scale),
            tooltip=[alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
                     alt.Tooltip("NDVI_aff:Q", title="NDVI brut", format=".3f"),
                     alt.Tooltip("Motif:N", title="Écartée")]))
    if not kept.empty:
        # mesures retenues ; celles qui portent une remarque (chute ponctuelle gardée, v4.3)
        # sont dessinées pareil, avec la remarque dans l'infobulle
        noted = kept["Motif"].notna() if "Motif" in kept else pd.Series(False, index=kept.index)
        for part, extra in ((kept[~noted], []),
                            (kept[noted], [alt.Tooltip("Motif:N", title="Remarque")])):
            if part.empty:
                continue
            layers.append(alt.Chart(part).mark_circle(
                size=64, color=CURVE_COLOR, stroke="white", strokeWidth=1.5, opacity=1).encode(
                x=x_enc, y=alt.Y("NDVI_aff:Q", scale=y_scale),
                tooltip=[alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
                         alt.Tooltip("NDVI_aff:Q", title="NDVI mesuré", format=".3f"),
                         alt.Tooltip("Fiabilite:N", title="Fiabilité"),
                         alt.Tooltip("Poids:Q", title="Poids", format=".2f")] + extra))

    return (alt.layer(*layers).properties(height=height).resolve_scale(color="independent")
            .configure_axisY(minExtent=AXIS_Y_WIDTH))


NDTI_COLOR = "#4a3aa7"      # panneau NDTI : teinte distincte du NDVI
WET_COLOR = "#eb6834"       # sol humide probable
from ndvi_app.core import soil_reading as soil  # noqa: E402

NDTI_REF = soil.NDTI_RESIDUE  # repères indicatifs, à calibrer
WET_STATES = (soil.ETAT_HUMIDE, soil.ETAT_PROBABLE, soil.ETAT_VERIF)
AXIS_Y_WIDTH = 44           # largeur fixe de l'axe Y : les deux panneaux restent alignés


def ndti_chart(detail, period_start, period_end, low, height=170):
    """
    Panneau NDTI (expérimental), même axe des dates que le graphique NDVI.
    Points pleins : parcelle peu verte (NDVI < seuil bas), seuls interprétables.
    Retourne None si aucune valeur NDTI (ex. analyse enregistrée avant la v2.5).
    """
    if "NDTI" not in detail or detail["NDTI"].dropna().empty:
        return None
    p0, p1 = pd.Timestamp(period_start), pd.Timestamp(period_end)
    d = detail.dropna(subset=["NDTI"]).copy()
    d["Lisible"] = (d["NDVI"] < low).map({True: "Oui", False: "Non (parcelle verte)"})
    for col in ("Etat_sol", "Lecture"):
        d[col] = d[col].fillna("—") if col in d else "—"

    v = d["NDTI"]
    y_lo = min(-0.05, (v.min() // 0.05) * 0.05)
    y_hi = max(0.30, -((-v.max()) // 0.05) * 0.05)
    y_scale = alt.Scale(domain=[y_lo, y_hi], nice=False)
    x_enc = alt.X("Date:T", scale=alt.Scale(domain=[p0, p1]), axis=_x_axis(p0, p1))
    tooltip = [alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
               alt.Tooltip("NDTI:Q", title="NDTI", format=".3f"),
               alt.Tooltip("NDVI:Q", title="NDVI", format=".3f"),
               alt.Tooltip("Lisible:N", title="Interprétable"),
               alt.Tooltip("Etat_sol:N", title="État du sol"),
               alt.Tooltip("Lecture:N", title="Lecture")]

    ref = pd.DataFrame({"y": [soil.NDTI_RESIDUE, soil.NDTI_BARE], "x0": [p0, p0],
                        "label": [f"{soil.NDTI_RESIDUE:.2f} résidus".replace(".", ","),
                                  f"{soil.NDTI_BARE:.2f} sol nu".replace(".", ",")]})
    layers = [
        alt.Chart(ref).mark_rule(strokeDash=[4, 4], strokeWidth=1, color=MUTED).encode(
            y=alt.Y("y:Q", scale=y_scale, title="NDTI")),
        alt.Chart(ref).mark_text(align="left", baseline="bottom", dx=4, dy=-3, fontSize=11,
                                 color=MUTED).encode(x="x0:T", y=alt.Y("y:Q", scale=y_scale),
                                                     text="label:N"),
    ]
    green = d[d["Lisible"] != "Oui"]
    bare = d[d["Lisible"] == "Oui"]
    wet = bare[bare["Etat_sol"].isin(WET_STATES)]
    if not green.empty:
        layers.append(alt.Chart(green).mark_point(
            shape="circle", filled=False, size=50, strokeWidth=1.2, color=MUTED).encode(
            x=x_enc, y=alt.Y("NDTI:Q", scale=y_scale), tooltip=tooltip))
    if not wet.empty:
        layers.append(alt.Chart(wet).mark_point(
            shape="circle", filled=False, size=170, strokeWidth=2, color=WET_COLOR).encode(
            x=x_enc, y=alt.Y("NDTI:Q", scale=y_scale), tooltip=tooltip))
    if not bare.empty:
        layers.append(alt.Chart(bare).mark_circle(
            size=64, color=NDTI_COLOR, stroke="white", strokeWidth=1.5, opacity=1).encode(
            x=x_enc, y=alt.Y("NDTI:Q", scale=y_scale), tooltip=tooltip))
    return alt.layer(*layers).properties(height=height).configure_axisY(minExtent=AXIS_Y_WIDTH)


RAIN_COLOR = "#3b7dd8"      # pluie
SOIL_COLOR = "#8a5a2b"      # humidité du sol
FROST_COLOR = "#5bc0eb"     # gel


def weather_charts(days, period_start, period_end):
    """
    Météo ERA5-Land sous le graphique NDVI, même axe des dates et même largeur d'axe Y
    (deux graphiques pleine largeur) : pluie journalière (barres) avec repères de gel, puis
    humidité du sol 0–7 cm et ses repères (ressuyé / proche saturation). [] sans données.
    """
    if not days:
        return []
    p0, p1 = pd.Timestamp(period_start), pd.Timestamp(period_end)
    df = pd.DataFrame([{"Date": pd.Timestamp(d), "Pluie": v[0], "Humidite": v[1], "Tmin": v[2]}
                       for d, v in days.items()])
    df = df[(df["Date"] >= p0) & (df["Date"] <= p1)]
    if df.empty:
        return []
    x_enc = alt.X("Date:T", scale=alt.Scale(domain=[p0, p1]), axis=_x_axis(p0, p1))
    x_top = alt.X("Date:T", scale=alt.Scale(domain=[p0, p1]),
                  axis=alt.Axis(title=None, labels=False, ticks=False, domain=False, grid=False))
    tooltip = [alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
               alt.Tooltip("Pluie:Q", title="Pluie (mm)", format=".1f"),
               alt.Tooltip("Humidite:Q", title="Humidité du sol (m³/m³)", format=".2f"),
               alt.Tooltip("Tmin:Q", title="Température min. (°C)", format=".1f")]
    rain_max = max(10.0, float(df["Pluie"].max()))
    rain = alt.Chart(df).mark_bar(color=RAIN_COLOR, opacity=0.85, width=2).encode(
        x=x_top, y=alt.Y("Pluie:Q", title="Pluie (mm)", scale=alt.Scale(domain=[0, rain_max])),
        tooltip=tooltip)
    frost_df = df[df["Tmin"] <= soil.FROST_C]
    rain_layers = [rain]
    if not frost_df.empty:
        rain_layers.append(alt.Chart(frost_df).mark_point(
            shape="triangle-down", filled=True, size=45, color=FROST_COLOR).encode(
            x=x_top, y=alt.value(4), tooltip=tooltip))
    top = alt.layer(*rain_layers).properties(height=90)
    refs = pd.DataFrame({"y": [soil.SM_DRY, soil.SM_WET]})
    sm = alt.layer(
        alt.Chart(refs).mark_rule(strokeDash=[3, 3], color=MUTED, strokeWidth=1).encode(
            y=alt.Y("y:Q", scale=alt.Scale(domain=[0.1, 0.5]))),
        alt.Chart(df).mark_line(color=SOIL_COLOR, strokeWidth=2).encode(
            x=x_enc, y=alt.Y("Humidite:Q", title="Humidité",
                             scale=alt.Scale(domain=[0.1, 0.5]), axis=alt.Axis(values=[0.2, 0.3, 0.4])),
            tooltip=tooltip),
    ).properties(height=100)
    # Même largeur d'axe Y que le graphique NDVI : dates alignées d'un graphique à l'autre
    return [c.configure_axisY(minExtent=AXIS_Y_WIDTH).configure_view(stroke=None) for c in (top, sm)]


CURRENT_COLOR = "#e34948"   # date affichée sur la carte


def dates_timeline(meas, period_start, period_end, current, height=80):
    """
    Frise des dates analysées (un point par date d'image), cliquable.
    meas    : DataFrame (Date, Retenues, Total) — mesures retenues par date
    current : date affichée sur la carte (trait vertical)
    Sélection Altair nommée « pick » (st.altair_chart(..., on_select="rerun")).
    """
    p0, p1 = pd.Timestamp(period_start), pd.Timestamp(period_end)
    m = meas.copy()
    m["Part"] = (m["Retenues"] / m["Total"].where(m["Total"] > 0)).fillna(0)
    m["Libelle"] = m["Retenues"].astype(int).astype(str) + " / " + m["Total"].astype(int).astype(str)
    m["y"] = 0
    x_enc = alt.X("Date:T", scale=alt.Scale(domain=[p0, p1]), axis=_x_axis(p0, p1))
    y_enc = alt.Y("y:Q", axis=None, scale=alt.Scale(domain=[-1, 1]))
    pick = alt.selection_point(name="pick", fields=["Date"], on="click", empty=False)

    base_line = alt.Chart(pd.DataFrame({"x0": [p0], "x1": [p1], "y": [0]})).mark_rule(
        color=MUTED, strokeWidth=1).encode(x="x0:T", x2="x1:T", y=y_enc)
    cur = alt.Chart(pd.DataFrame({"Date": [pd.Timestamp(current)]})).mark_rule(
        color=CURRENT_COLOR, strokeWidth=2).encode(x=x_enc)
    pts = alt.Chart(m).mark_circle(size=110, color=CURVE_COLOR, stroke="white",
                                   strokeWidth=1.5, cursor="pointer").encode(
        x=x_enc, y=y_enc,
        opacity=alt.Opacity("Part:Q", scale=alt.Scale(domain=[0, 1], range=[0.25, 1]),
                            legend=None),
        tooltip=[alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
                 alt.Tooltip("Libelle:N", title="Parcelles retenues")],
    ).add_params(pick)
    return (alt.layer(base_line, cur, pts).properties(height=height)
            .configure_axisY(minExtent=AXIS_Y_WIDTH).configure_view(stroke=None))


# ------------------------------------------------------------------ couverture des dates
COVERAGE_CATS = ["Déjà analysée", "Nouvelle, sélectionnée", "Nouvelle, non sélectionnée"]
COVERAGE_COLORS = [CURVE_COLOR, "#e8871e", MUTED]
LANE_DONE, LANE_NEW = "Déjà analysées", "Disponibles"
GAP_COLOR = "#e34948"
GAP_DAYS = 20


def coverage_timeline(done_dates, new_items, selected, period_start, period_end, old_period,
                      gap_days=GAP_DAYS, height=190):
    """
    Frise pour compléter une analyse, sur deux lignes :
      « Déjà analysées » : dates présentes dans l'analyse ;
      « Disponibles »    : dates pas encore analysées, pleines si sélectionnées, vides sinon.
    Tirets gris : bornes de la période déjà analysée. Bandes rouges : plus de `gap_days` jours
    sans image analysée une fois la sélection ajoutée (bords de période compris).
    done_dates : dates (datetime.date) déjà dans l'analyse
    new_items  : entrées de list_dates non encore analysées
    selected   : dates (datetime.date) choisies parmi new_items
    """
    p0, p1 = pd.Timestamp(period_start), pd.Timestamp(period_end)
    sel = set(selected)
    rows = [{"Date": pd.Timestamp(d), "Categorie": COVERAGE_CATS[0], "y": 1, "Ciel": None}
            for d in done_dates]
    rows += [{"Date": pd.Timestamp(it["date"]),
              "Categorie": COVERAGE_CATS[1] if it["date"] in sel else COVERAGE_CATS[2],
              "y": 0, "Ciel": it.get("clear_pct")} for it in new_items]
    pts_df = pd.DataFrame(rows, columns=["Date", "Categorie", "y", "Ciel"])

    future = sorted({pd.Timestamp(d) for d in done_dates} | {pd.Timestamp(d) for d in sel})
    bounds = [p0 - pd.Timedelta(days=1)] + future + [p1 + pd.Timedelta(days=1)]
    gaps = [{"x0": a, "x1": b, "Jours": (b - a).days - 1}
            for a, b in zip(bounds[:-1], bounds[1:]) if (b - a).days - 1 > gap_days]

    x_scale = alt.Scale(domain=[p0, p1])
    x_enc = alt.X("Date:T", scale=x_scale, axis=_x_axis(p0, p1))
    y_enc = alt.Y("y:Q", scale=alt.Scale(domain=[-0.7, 1.7]),
                  axis=alt.Axis(title=None, values=[0, 1], grid=False, domain=False, ticks=False,
                                labelColor=MUTED, labelFontSize=11, labelPadding=6,
                                labelExpr=f"datum.value == 1 ? '{LANE_DONE}' : '{LANE_NEW}'"))
    layers = []
    if gaps:
        layers.append(alt.Chart(pd.DataFrame(gaps)).mark_rect(color=GAP_COLOR, opacity=0.15).encode(
            x=alt.X("x0:T", scale=x_scale, title=None), x2="x1:T",
            tooltip=[alt.Tooltip("Jours:Q", title=f"Jours sans image analysée (> {gap_days})")]))
    old = pd.DataFrame({"Date": [pd.Timestamp(old_period[0]), pd.Timestamp(old_period[1])],
                        "Borne": ["Début de l'analyse existante", "Fin de l'analyse existante"]})
    layers.append(alt.Chart(old).mark_rule(color=MUTED, strokeDash=[4, 3], strokeWidth=1.5).encode(
        x=x_enc, tooltip=[alt.Tooltip("Borne:N", title="Repère"),
                          alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y")]))
    color = alt.Color("Categorie:N", scale=alt.Scale(domain=COVERAGE_CATS, range=COVERAGE_COLORS),
                      legend=alt.Legend(orient="bottom", title=None))
    tooltip = [alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
               alt.Tooltip("Categorie:N", title="État"),
               alt.Tooltip("Ciel:Q", title="Ciel clair (%)", format=".0f")]
    hollow = pts_df["Categorie"] == COVERAGE_CATS[2]
    layers.append(alt.Chart(pts_df[hollow]).mark_point(size=70, filled=False, strokeWidth=1.5)
                  .encode(x=x_enc, y=y_enc, color=color, tooltip=tooltip))
    layers.append(alt.Chart(pts_df[~hollow]).mark_circle(size=70, opacity=1)
                  .encode(x=x_enc, y=y_enc, color=color, tooltip=tooltip))
    return (alt.layer(*layers).properties(height=height)
            .configure_axisY(minExtent=90).configure_view(stroke=None))


# ------------------------------------------------------------------ vue d'ensemble
# Image SVG d'une cellule du tableau « Évolution des phases » : courbe NDVI lissée au-dessus
# de la barre des phases, sur la même échelle de temps (toute la période), traits au 1er de
# chaque mois et noms des mois. Mêmes proportions pour toutes les lignes : les dates sont
# alignées d'une parcelle à l'autre.
EVO_W, EVO_H = 320, 66
EVO_CURVE = (2, 34)          # haut et hauteur de la zone de courbe (NDVI 1 en haut, 0 en bas)
EVO_BAR = (40, 12)           # haut et hauteur de la barre des phases
EVO_LABEL_Y = 64             # ligne de base des noms de mois
EVO_LINE = "#1A7F4B"         # courbe (couleur principale de l'appli)
EVO_GRID = "#C9CCD3"         # seuils et traits de mois dans la zone de courbe
EVO_TEXT = "#6B6F7A"         # noms de mois
STRIP_EMPTY = "#E6E7EB"      # pas de courbe (avant la 1re / après la dernière mesure retenue)
_MONTH_ABBR = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]
_MONTH_INITIAL = "JFMAMJJASOND"


def _evo_bar(c, x):
    y, h = EVO_BAR
    out = [f'<rect x="0" y="{y}" width="{EVO_W}" height="{h}" fill="{STRIP_EMPTY}"/>']
    if c.empty:
        return out
    dates, phases, unc = list(c["Date"]), list(c["Phase"]), list(c["Incertain"])
    start = 0
    for i in range(1, len(c) + 1):
        if i == len(c) or phases[i] != phases[start] or bool(unc[i]) != bool(unc[start]):
            x0, x1 = x(dates[start]), x(dates[i - 1] + pd.Timedelta(days=1))
            op = 0.45 if unc[start] else 1
            out.append(f'<rect x="{x0:.1f}" y="{y}" width="{max(x1 - x0, 0.6):.1f}" height="{h}" '
                       f'fill="{PHASE_TINTS.get(phases[start], STRIP_EMPTY)}" fill-opacity="{op}"/>')
            start = i
    return out


def _evo_curve(c, x, low, high):
    top, h = EVO_CURVE
    yv = lambda v: top + (1 - min(max(float(v), 0.0), 1.0)) * h
    out = [f'<line x1="0" x2="{EVO_W}" y1="{yv(t):.1f}" y2="{yv(t):.1f}" stroke="{EVO_GRID}" '
           f'stroke-dasharray="2 3" stroke-width="0.8"/>' for t in (low, high)]
    if c.empty:
        return out
    pts = c.iloc[::2]
    if pts.index[-1] != c.index[-1]:                 # toujours le dernier jour de la courbe
        pts = pd.concat([pts, c.iloc[[-1]]])
    seg, seg_unc = [], None

    def flush():
        if len(seg) > 1:
            dash = ' stroke-dasharray="3 2" stroke-opacity="0.55"' if seg_unc else ""
            out.append(f'<polyline fill="none" stroke="{EVO_LINE}" stroke-width="1.6"{dash} '
                       f'points="{" ".join(seg)}"/>')

    for d, v, u in zip(pts["Date"], pts["NDVI_lisse"], pts["Incertain"]):
        pt, u = f"{x(d):.1f},{yv(v):.1f}", bool(u)
        if seg_unc is not None and u != seg_unc:
            seg.append(pt)                           # segments jointifs
            flush()
            seg = [pt]
        else:
            seg.append(pt)
        seg_unc = u
    flush()
    return out


def _evo_months(p0, p1, x):
    """Traits au 1er de chaque mois et noms des mois (initiale si la place manque) ;
    au 1er janvier et années pour les périodes trop longues pour lire les mois."""
    firsts = list(pd.date_range(p0 + pd.Timedelta(days=1), p1, freq="MS"))
    by_year = EVO_W / max(len(firsts) + 1, 1) < 8
    if by_year:
        firsts = [d for d in firsts if d.month == 1]
    top, h = EVO_CURVE
    by, bh = EVO_BAR
    out = []
    for d in firsts:
        xd = f"{x(d):.1f}"
        year = d.month == 1
        out.append(f'<line x1="{xd}" x2="{xd}" y1="{top}" y2="{top + h}" stroke="{EVO_GRID}" '
                   f'stroke-opacity="{1 if year else 0.7}" stroke-width="1"/>')
        out.append(f'<line x1="{xd}" x2="{xd}" y1="{by}" y2="{by + bh}" stroke="#FFFFFF" '
                   f'stroke-opacity="0.9" stroke-width="1"/>')
        out.append(f'<line x1="{xd}" x2="{xd}" y1="{by + bh}" y2="{by + bh + 3}" '
                   f'stroke="{EVO_TEXT if year else EVO_GRID}" stroke-width="1"/>')
    bounds = [p0] + firsts + [p1 + pd.Timedelta(days=1)]
    for a, b in zip(bounds[:-1], bounds[1:]):
        width = x(b) - x(a)
        if width < 8:
            continue
        if by_year:
            label = str(a.year) if width >= 24 else f"{a.year % 100:02d}"
        else:
            label = _MONTH_ABBR[a.month - 1] if width >= 24 else _MONTH_INITIAL[a.month - 1]
        out.append(f'<text x="{(x(a) + x(b)) / 2:.1f}" y="{EVO_LABEL_Y}" font-size="9.5" '
                   f'fill="{EVO_TEXT}" text-anchor="middle" '
                   f'font-family="Source Sans Pro, Arial, sans-serif">{label}</text>')
    return out


def phase_evolution_uri(curve, period_start, period_end, low, high):
    """
    Courbe NDVI lissée et barre des phases d'une parcelle sur toute la période, en image SVG
    (data URI) pour une colonne d'image de st.dataframe. curve : lignes de la courbe lissée
    (Date, NDVI_lisse, Phase, Incertain). Pointillés horizontaux : seuils des phases. Courbe
    en pointillé et teinte atténuée là où la courbe est interpolée loin de toute mesure.
    """
    import base64
    p0, p1 = pd.Timestamp(period_start), pd.Timestamp(period_end)
    span = max((p1 - p0).days + 1, 1)
    x = lambda d: (pd.Timestamp(d) - p0).days / span * EVO_W
    c = curve.sort_values("Date") if not curve.empty else curve
    parts = _evo_curve(c, x, low, high) + _evo_bar(c, x) + _evo_months(p0, p1, x)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{EVO_W}" height="{EVO_H}" '
           f'viewBox="0 0 {EVO_W} {EVO_H}">{"".join(parts)}</svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
