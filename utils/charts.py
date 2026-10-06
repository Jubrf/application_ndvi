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

from utils.timeseries import PHASE_BIEN, PHASE_NU, PHASE_PEU, PHASES

CURVE_COLOR = "#2a78d6"     # série unique : bleu
MUTED = "#8a8984"           # seuils, libellés, mesures écartées
PHASE_TINTS = {PHASE_NU: "#d73027", PHASE_PEU: "#a6d96a", PHASE_BIEN: "#1a9850"}

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

    return alt.layer(*layers).properties(height=height).resolve_scale(color="independent")
