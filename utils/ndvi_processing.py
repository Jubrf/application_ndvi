"""
Post-traitement Python (sans GEE) : mise en forme des statistiques,
statut qualité, interprétation agronomique, synthèse temporelle.
"""
import pandas as pd

# ------------------------------------------------------------
# Statut qualité d'une mesure parcelle × date
# ------------------------------------------------------------
STATUS_OK = "OK"
STATUS_CLOUD = "Nuageux"
STATUS_FEW = "Trop peu de pixels"
STATUS_NOGEOM = "Géométrie inexploitable"
STATUS_NODATA = "Hors image"

# Indicateur utilisé pour l'interprétation
INDICATORS = {
    "Médiane": "NDVI_median",
    "Moyenne pondérée qualité": "NDVI_pondere",
    "Moyenne": "NDVI_moyen",
}

# ------------------------------------------------------------
# Classification NDVI
#   < 0.20      → Sol nu ou couvert non levé
#   0.20–0.25   → Sol nu ou couvert levant  (zone limite)
#   0.25–0.50   → Couvert en développement
#   ≥ 0.50      → Couvert établi
# ------------------------------------------------------------
COLOR_MAP = {
    "Sol nu ou couvert non levé": "#d73027",
    "Sol nu ou couvert levant": "#fdae61",
    "Couvert en développement": "#66bd63",
    "Couvert établi": "#1a9850",
}
COLOR_INVALID = "#9e9e9e"


def classify_state(nd):
    """Retourne (interprétation, couvert: bool|None)."""
    if nd is None or pd.isna(nd):
        return None, None
    if nd < 0.20:
        return "Sol nu ou couvert non levé", False
    if nd < 0.25:
        return "Sol nu ou couvert levant", None
    if nd < 0.50:
        return "Couvert en développement", True
    return "Couvert établi", True


def colorize(interpretation):
    return COLOR_MAP.get(interpretation, COLOR_INVALID)


def _r(v, n=3):
    return round(float(v), n) if v is not None else None


def parse_stats(raw):
    """Propriétés brutes GEE d'une parcelle → valeurs lisibles."""
    if raw is None:
        return None
    n_total = raw.get("n_total") or 0
    n_clear = raw.get("n_clear") or 0
    n_used = raw.get("NDVI_count") or 0
    w = raw.get("W")
    wndvi = raw.get("WNDVI")
    wmean = wndvi / w if (w and wndvi is not None and n_used > 0) else None
    return {
        "NDVI_median": _r(raw.get("NDVI_median")),
        "NDVI_pondere": _r(wmean),
        "NDVI_moyen": _r(raw.get("NDVI_mean")),
        "NDVI_ecart_type": _r(raw.get("NDVI_stdDev")),
        "EVI2_median": _r(raw.get("EVI2_median")),
        "Pixels_total": int(n_total),
        "Pixels_clairs": int(n_clear),
        "Pixels_utilises": int(n_used),
        "Outliers_exclus": int(max(n_clear - n_used, 0)),
        "Clair_pct": round(n_clear / n_total * 100, 1) if n_total else None,
        # Score Cloud Score+ moyen des pixels utilisés (probabilité d'être dégagé, 0–1)
        "Score_clarte": round(w / n_used, 3) if (w and n_used) else None,
    }


# ------------------------------------------------------------
# Poids de fiabilité d'une mesure (0 à 1), dans l'esprit du « raw NDVI weight »
# de KERMAP (dont la formule n'est pas publique). Produit de quatre facteurs :
#   clarté      : part des pixels de la parcelle non masqués (Clair_pct / 100)
#   score       : score Cloud Score+ moyen des pixels utilisés (voile résiduel)
#   cohérence   : part des pixels clairs non exclus comme aberrants
#   taille      : nombre de pixels utilisés, plein poids à partir de N_PIXELS_REF
# ------------------------------------------------------------
N_PIXELS_REF = 30          # 30 pixels de 10 m = 0,3 ha analysés
RELIABILITY_LEVELS = [(0.8, "Bonne"), (0.5, "Moyenne"), (0.0, "Faible")]


def reliability_weight(parsed):
    if not parsed or not parsed["Pixels_utilises"] or parsed["Clair_pct"] is None:
        return None
    clarte = parsed["Clair_pct"] / 100
    score = parsed["Score_clarte"] if parsed["Score_clarte"] is not None else 1.0
    coherence = 1 - parsed["Outliers_exclus"] / parsed["Pixels_clairs"] if parsed["Pixels_clairs"] else 0
    taille = min(1.0, parsed["Pixels_utilises"] / N_PIXELS_REF)
    return round(max(0.0, min(1.0, clarte * score * coherence * taille)), 2)


def reliability_level(weight, status):
    if status != STATUS_OK:
        return "Non exploitable"
    if weight is None:
        return "—"
    return next(label for seuil, label in RELIABILITY_LEVELS if weight >= seuil)


def quality_status(parsed, min_pixels, min_clear_pct):
    if parsed is None:
        return STATUS_NOGEOM
    if parsed["Pixels_clairs"] == 0:
        return STATUS_CLOUD if parsed["Pixels_total"] > 0 else STATUS_NODATA
    if parsed["Clair_pct"] is not None and parsed["Clair_pct"] < min_clear_pct:
        return STATUS_CLOUD
    if parsed["Pixels_utilises"] < min_pixels:
        return STATUS_FEW
    return STATUS_OK


def build_rows(ids, geoinfo, day_result, date_str, indicator_col,
               min_pixels, min_clear_pct):
    """
    ids        : identifiants des parcelles (ordre des features)
    geoinfo    : sortie de geometry.prepare_all
    day_result : sortie de gee_ndvi.compute_day_stats
    """
    sats = ", ".join(s.replace("Sentinel-", "S") for s in day_result.get("satellites", []))
    rows = []
    for i, (pid, gi) in enumerate(zip(ids, geoinfo)):
        parsed = parse_stats(day_result["stats"].get(i)) if gi["geojson"] else None
        status = quality_status(parsed, min_pixels, min_clear_pct)
        value = parsed[indicator_col] if (parsed and status == STATUS_OK) else None
        interp, couvert = classify_state(value)

        weight = reliability_weight(parsed)
        row = {"ID": pid, "Date": date_str, "NDVI": value,
               "Poids": weight, "Fiabilite": reliability_level(weight, status),
               "Statut": status,
               "Interpretation": interp if status == STATUS_OK else status,
               "Couvert": "Oui" if couvert is True else ("Non" if couvert is False else "—")}
        if parsed:
            row.update(parsed)
        row.update({"Surface_ha": gi["area_ha"], "Buffer_m": gi["buffer_m"],
                    "Satellite": sats})
        rows.append(row)
    return rows


# ------------------------------------------------------------
# Synthèse temporelle (provisoire — refonte prévue à l'étape 2)
# Δ entre la première et la dernière mesure valide de chaque parcelle.
# ------------------------------------------------------------
def compute_tendency(values):
    """values : NDVI valides triés par date. Retourne (libellé, delta)."""
    if len(values) < 2:
        return "Indéterminé", None
    delta = round(values[-1] - values[0], 3)
    if delta > 0.10:
        return "Hausse", delta
    if delta < -0.05:
        return "Baisse", delta
    return "Stable", delta


def temporal_summary(df_long):
    """
    df_long : une ligne par parcelle × date (sortie de build_rows).
    Ajoute Delta_NDVI (vs mesure valide précédente) et retourne
    (df_long, pivot NDVI parcelle × date avec tendance).
    """
    df = df_long.copy()
    df["_d"] = pd.to_datetime(df["Date"])
    df = df.sort_values(["ID", "_d"]).reset_index(drop=True)

    valid = df[df["Statut"] == STATUS_OK]
    df["Delta_NDVI"] = valid.groupby("ID")["NDVI"].diff().round(3)

    pivot = (df.pivot_table(index="ID", columns="Date", values="NDVI",
                            aggfunc="first", dropna=False)
             .reindex(df["ID"].drop_duplicates()))
    tend = {}
    for pid, sub in valid.groupby("ID"):
        tend[pid] = compute_tendency(sub["NDVI"].tolist())
    pivot["Mesures_valides"] = [int(valid["ID"].eq(p).sum()) for p in pivot.index]
    pivot["Tendance"] = [tend.get(p, ("Indéterminé", None))[0] for p in pivot.index]
    pivot["Delta_total"] = [tend.get(p, ("Indéterminé", None))[1] for p in pivot.index]
    pivot = pivot.reset_index()
    pivot.columns.name = None

    return df.drop(columns="_d"), pivot


def unique_ids(raw_ids):
    """Rend les identifiants uniques (suffixe _2, _3… sur les doublons)."""
    seen, out = {}, []
    for v in raw_ids:
        v = str(v) if v is not None and str(v).strip() else "SANS_ID"
        if v in seen:
            seen[v] += 1
            out.append(f"{v}_{seen[v]}")
        else:
            seen[v] = 1
            out.append(v)
    return out
