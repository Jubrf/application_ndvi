"""
Mesure d'une parcelle à une date (Python pur, sans Earth Engine ni Streamlit) :
mise en forme des statistiques brutes, statut qualité, poids de fiabilité, phase NDVI.
L'analyse temporelle est dans ndvi_app/core/timeseries.py.
"""
from ndvi_app.config import (COLOR_INVALID, COLOR_MAP, DEFAULT_THRESHOLDS, PHASE_NU,
                             STATUS_CLOUD, STATUS_FEW, STATUS_NODATA, STATUS_NOGEOM, STATUS_OK)
from ndvi_app.core.timeseries import phase_of

# Réflectance B11 sous laquelle un sol peu couvert est signalé « humide probable »
# (valeur indicative, à confirmer sur le terrain) : l'humidité réduit le contraste NDTI.
SWIR1_WET = 0.15


def colorize(phase):
    return COLOR_MAP.get(phase, COLOR_INVALID)


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
        "NDTI_median": _r(raw.get("NDTI_median")),
        "SWIR1_median": _r(raw.get("SWIR1_median"), 4),
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


def build_rows(ids, geoinfo, day_result, date_str, min_pixels, min_clear_pct,
               low=DEFAULT_THRESHOLDS[0], high=DEFAULT_THRESHOLDS[1]):
    """
    ids        : identifiants des parcelles (ordre des features)
    geoinfo    : sortie de geometry.prepare_all
    day_result : sortie de earth_engine.sentinel2.compute_day_stats
    """
    sats = ", ".join(s.replace("Sentinel-", "S") for s in day_result.get("satellites", []))
    rows = []
    for i, (pid, gi) in enumerate(zip(ids, geoinfo)):
        parsed = parse_stats(day_result["stats"].get(i)) if gi["geojson"] else None
        status = quality_status(parsed, min_pixels, min_clear_pct)
        raw_value = parsed["NDVI_median"] if parsed else None
        value = raw_value if status == STATUS_OK else None
        phase = phase_of(value, low, high)
        ndti = parsed.get("NDTI_median") if (parsed and status == STATUS_OK) else None
        swir1 = parsed.get("SWIR1_median") if parsed else None

        weight = reliability_weight(parsed)
        row = {"ID": pid, "Date": date_str, "NDVI": value, "NDVI_brut": raw_value,
               "Poids": weight, "Fiabilite": reliability_level(weight, status),
               "Statut": status,
               "Phase": phase if status == STATUS_OK else status,
               "Couvert": "—" if phase is None else ("Non" if phase == PHASE_NU else "Oui"),
               "NDTI": ndti,
               # Indicatif, à confirmer : infrarouge moyen sombre sur parcelle peu verte
               "Sol_humide": ("—" if ndti is None or value is None or value >= low
                              else ("Probable" if swir1 is not None and swir1 < SWIR1_WET
                                    else "Non"))}
        if parsed:
            row.update(parsed)
        row.update({"Surface_ha": gi["area_ha"], "Buffer_m": gi["buffer_m"],
                    "Satellite": sats})
        rows.append(row)
    return rows


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
