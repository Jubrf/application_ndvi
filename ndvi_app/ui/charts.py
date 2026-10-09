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
        layers.append(alt.Chart(kept).mark_circle(
            size=64, color=CURVE_COLOR, stroke="white", strokeWidth=1.5, opacity=1).encode(
            x=x_enc, y=alt.Y("NDVI_aff:Q", scale=y_scale),
            tooltip=[alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
                     alt.Tooltip("NDVI_aff:Q", title="NDVI mesuré", format=".3f"),
                     alt.Tooltip("Fiabilite:N", title="Fiabilité"),
                     alt.Tooltip("Poids:Q", title="Poids", format=".2f")]))

    return (alt.layer(*layers).properties(height=height).resolve_scale(color="independent")
            .configure_axisY(minExtent=AXIS_Y_WIDTH))


NDTI_COLOR = "#4a3aa7"      # panneau NDTI : teinte distincte du NDVI
WET_COLOR = "#eb6834"       # sol humide probable
NDTI_REF = 0.10             # repère indicatif, à calibrer
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
    humid = d["Sol_humide"] if "Sol_humide" in d else pd.Series("—", index=d.index)
    d["Humide"] = humid.fillna("—")

    v = d["NDTI"]
    y_lo = min(-0.05, (v.min() // 0.05) * 0.05)
    y_hi = max(0.30, -((-v.max()) // 0.05) * 0.05)
    y_scale = alt.Scale(domain=[y_lo, y_hi], nice=False)
    x_enc = alt.X("Date:T", scale=alt.Scale(domain=[p0, p1]), axis=_x_axis(p0, p1))
    tooltip = [alt.Tooltip("Date:T", title="Date", format="%d/%m/%Y"),
               alt.Tooltip("NDTI:Q", title="NDTI", format=".3f"),
               alt.Tooltip("NDVI:Q", title="NDVI", format=".3f"),
               alt.Tooltip("Lisible:N", title="Interprétable"),
               alt.Tooltip("Humide:N", title="Sol humide")]

    ref = pd.DataFrame({"y": [NDTI_REF], "x0": [p0], "label": ["0,10 (repère indicatif)"]})
    layers = [
        alt.Chart(ref).mark_rule(strokeDash=[4, 4], strokeWidth=1, color=MUTED).encode(
            y=alt.Y("y:Q", scale=y_scale, title="NDTI")),
        alt.Chart(ref).mark_text(align="left", baseline="bottom", dx=4, dy=-3, fontSize=11,
                                 color=MUTED).encode(x="x0:T", y=alt.Y("y:Q", scale=y_scale),
                                                     text="label:N"),
    ]
    green = d[d["Lisible"] != "Oui"]
    bare = d[d["Lisible"] == "Oui"]
    wet = bare[bare["Humide"] == "Probable"]
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
