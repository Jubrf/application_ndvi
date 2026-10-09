"""
Analyse multi-temporelle par parcelle (Python pur, sans Earth Engine).

Chaîne de traitement d'une parcelle :
  1. Sélection des mesures : écartées si statut non OK, poids de fiabilité
     sous le minimum, ou chute isolée (valeur nettement sous ses voisines
     avant ET après, signe d'un résidu de nuage).
  2. Lissage : moyenne pondérée à noyau gaussien sur une grille journalière
     (poids = fiabilité × proximité dans le temps). Deux passes supplémentaires
     réduisent le poids des points situés sous la courbe : les nuages ne font
     que baisser le NDVI, la courbe suit donc plutôt le haut de la série.
     Pas d'extrapolation : la courbe va de la première à la dernière mesure retenue.
  3. Phases (sol nu / couvert peu développé / bien développé) selon deux seuils,
     épisodes de moins de N jours fusionnés, baisses rapides repérées.
  4. Synthèse : chronologie en texte, phase en fin de période, plus long trou
     sans mesure, niveau de confiance.
"""
import numpy as np
import pandas as pd

from ndvi_app.config import PHASE_BIEN, PHASE_NU, PHASE_PEU, PHASES, STATUS_OK  # noqa: F401 (réexportés)

DEFAULT_SETTINGS = {
    "min_weight": 0.5,          # poids de fiabilité minimal d'une mesure retenue
    "dip_threshold": 0.15,      # chute isolée : écart sous les voisines (NDVI)
    "dip_window_days": 15,      # fenêtre de recherche des voisines (jours)
    "smooth_days": 6,           # écart-type du noyau de lissage (jours)
    "min_episode_days": 10,     # épisode plus court : fusionné avec ses voisins
    "drop_threshold": 0.20,     # baisse rapide : perte de NDVI lissé…
    "drop_window_days": 15,     # …en moins de N jours
    "uncertain_days": 15,       # courbe incertaine à plus de N jours d'une mesure
}

MOTIF_STATUS = "Statut : {}"
MOTIF_WEIGHT = "Poids < {:.2f}"
MOTIF_DIP = "Chute isolée (nuage probable)"


def phase_of(value, low, high):
    if value is None or pd.isna(value):
        return None
    if value < low:
        return PHASE_NU
    if value < high:
        return PHASE_PEU
    return PHASE_BIEN


# ------------------------------------------------------------
# 1. Sélection des mesures
# ------------------------------------------------------------
def select_measures(sub, settings, status_ok=STATUS_OK):
    """
    sub : lignes d'une parcelle (colonnes Date datetime64, NDVI, Poids, Statut), triées par date.
    Retourne (retenue: array bool, motif: list[str|None]).
    """
    n = len(sub)
    retained = np.zeros(n, dtype=bool)
    motif = [None] * n
    for i, (status, val, w) in enumerate(zip(sub["Statut"], sub["NDVI"], sub["Poids"])):
        if status != status_ok or pd.isna(val):
            motif[i] = MOTIF_STATUS.format(status)
        elif pd.isna(w) or w < settings["min_weight"]:
            motif[i] = MOTIF_WEIGHT.format(settings["min_weight"])
        else:
            retained[i] = True

    # Chute isolée : comparée aux mesures retenues de part et d'autre (une seule passe)
    days = (sub["Date"] - sub["Date"].min()).dt.days.to_numpy()
    vals = sub["NDVI"].to_numpy(dtype=float)
    win, thr = settings["dip_window_days"], settings["dip_threshold"]
    base = retained.copy()
    for i in np.flatnonzero(base):
        before = base & (days < days[i]) & (days >= days[i] - win)
        after = base & (days > days[i]) & (days <= days[i] + win)
        if before.any() and after.any():
            if vals[i] < vals[before].max() - thr and vals[i] < vals[after].max() - thr:
                retained[i] = False
                motif[i] = MOTIF_DIP
    return retained, motif


# ------------------------------------------------------------
# 2. Lissage pondéré
# ------------------------------------------------------------
def smooth_curve(dates, values, weights, settings, iterations=2):
    """
    dates : datetime64 des mesures retenues ; values, weights : arrays.
    Retourne un DataFrame journalier (Date, NDVI_lisse, Incertain) ou vide.
    """
    if len(dates) == 0:
        return pd.DataFrame(columns=["Date", "NDVI_lisse", "Incertain"])
    t0 = dates.min()
    ti = (dates - t0).days.to_numpy(dtype=float)
    vi = np.asarray(values, dtype=float)
    wi = np.clip(np.asarray(weights, dtype=float), 0.05, 1.0)
    grid_days = np.arange(0, int(ti.max()) + 1, dtype=float)

    sigma = float(settings["smooth_days"])
    kernel = np.exp(-0.5 * ((grid_days[:, None] - ti[None, :]) / sigma) ** 2)

    def _fit(w):
        num = kernel @ (w * vi)
        den = kernel @ w
        return np.where(den > 1e-9, num / np.maximum(den, 1e-9), np.nan)

    w = wi.copy()
    fit = _fit(w)
    for _ in range(iterations):
        at_pts = fit[ti.astype(int)]
        resid = vi - at_pts
        # Points sous la courbe : poids réduit, au plus de moitié (les chutes isolées
        # sont déjà écartées ; une vraie baisse doit rester suivie sans trop de retard).
        w = wi * np.where(resid < 0, np.maximum(0.5, 1.0 / (1.0 + (resid / 0.05) ** 2)), 1.0)
        fit = _fit(w)

    dist = np.abs(grid_days[:, None] - ti[None, :]).min(axis=1)
    return pd.DataFrame({
        "Date": t0 + pd.to_timedelta(grid_days, unit="D"),
        "NDVI_lisse": np.round(fit, 3),
        "Incertain": dist > settings["uncertain_days"],
    })


# ------------------------------------------------------------
# 3. Phases, épisodes, baisses rapides
# ------------------------------------------------------------
def _runs(labels):
    """[(label, i_debut, i_fin)] pour les suites consécutives identiques."""
    runs = []
    for i, lab in enumerate(labels):
        if runs and runs[-1][0] == lab:
            runs[-1][2] = i
        else:
            runs.append([lab, i, i])
    return runs


def episodes(curve, low, high, min_days):
    """Épisodes de phase sur la courbe lissée, les plus courts fusionnés."""
    if curve.empty:
        return []
    labels = [phase_of(v, low, high) for v in curve["NDVI_lisse"]]
    runs = _runs(labels)
    while len(runs) > 1:
        lengths = [r[2] - r[1] + 1 for r in runs]
        k = int(np.argmin(lengths))
        if lengths[k] >= min_days:
            break
        if k == 0:
            target = 1
        elif k == len(runs) - 1:
            target = k - 1
        else:
            target = k - 1 if lengths[k - 1] >= lengths[k + 1] else k + 1
        lab = runs[target][0]
        for i in range(runs[k][1], runs[k][2] + 1):
            labels[i] = lab
        runs = _runs(labels)
    return [(lab, curve["Date"].iloc[a], curve["Date"].iloc[b]) for lab, a, b in runs]


def rapid_drops(curve, settings):
    """Dates des baisses rapides du NDVI lissé (destruction, récolte ou gel)."""
    if len(curve) < 2:
        return []
    s = curve["NDVI_lisse"].to_numpy(dtype=float)
    win = int(settings["drop_window_days"])
    n = len(s)
    flag = np.zeros(n, dtype=bool)
    for t in range(n):
        u = min(n - 1, t + win)
        if u > t and s[t] - s[u] > settings["drop_threshold"]:
            flag[t] = True
    events = []
    for lab, a, b in _runs(list(flag)):
        if not lab:
            continue
        end = min(n - 1, b + win)
        daily = np.diff(s[a:end + 1])
        k = a + int(np.argmin(daily)) + 1 if len(daily) else a
        events.append(curve["Date"].iloc[k])
    return events


# ------------------------------------------------------------
# 4. Synthèse d'une parcelle
# ------------------------------------------------------------
def _fmt(d, with_year):
    return d.strftime("%d/%m/%y" if with_year else "%d/%m")


def confidence(n_retained, max_gap):
    if n_retained == 0:
        return "Aucune mesure"
    if n_retained >= 3 and max_gap <= 20:
        return "Bonne"
    if n_retained >= 2 and max_gap <= 35:
        return "Moyenne"
    return "Faible"


def analyse_parcel(sub, period_start, period_end, low, high, settings):
    """
    sub : lignes d'une parcelle (Date datetime64, NDVI, Poids, Statut).
    Retourne (detail: DataFrame, courbe: DataFrame, synthese: dict).
    """
    sub = sub.sort_values("Date").reset_index(drop=True)
    retained, motif = select_measures(sub, settings)
    sub = sub.assign(Retenue=np.where(retained, "Oui", "Non"), Motif=motif)

    kept = sub[retained]
    curve = smooth_curve(pd.DatetimeIndex(kept["Date"]), kept["NDVI"], kept["Poids"], settings)
    curve["Phase"] = [phase_of(v, low, high) for v in curve["NDVI_lisse"]]

    lisse = curve.set_index("Date")["NDVI_lisse"]
    sub["NDVI_lisse"] = sub["Date"].map(lisse)
    sub["Phase"] = [phase_of(v, low, high) for v in sub["NDVI_lisse"]]

    p0, p1 = pd.Timestamp(period_start), pd.Timestamp(period_end)
    with_year = p0.year != p1.year
    kept_dates = list(kept["Date"])
    if kept_dates:
        bounds = [p0] + kept_dates + [p1]
        max_gap = max((b - a).days for a, b in zip(bounds[:-1], bounds[1:]))
    else:
        max_gap = (p1 - p0).days

    eps = episodes(curve, low, high, settings["min_episode_days"])
    chrono = " → ".join(f"{lab} ({_fmt(a, with_year)}–{_fmt(b, with_year)})" for lab, a, b in eps)
    drops = rapid_drops(curve, settings)

    n_hors = int((sub["Statut"] == "Hors image").sum())
    synth = {
        "Mesures_retenues": int(retained.sum()),
        "Mesures_ecartees": int(len(sub) - retained.sum() - n_hors),
        "Plus_long_trou_j": int(max_gap),
        "Confiance": confidence(int(retained.sum()), max_gap),
        "Derniere_mesure": kept_dates[-1].date() if kept_dates else None,
        "Jours_sans_mesure_fin": int((p1 - kept_dates[-1]).days) if kept_dates else None,
        "NDVI_fin": float(curve["NDVI_lisse"].iloc[-1]) if not curve.empty else None,
        "Phase_fin": curve["Phase"].iloc[-1] if not curve.empty else None,
        "Chronologie": chrono or "—",
        "Baisses_rapides": ", ".join(_fmt(d, with_year) for d in drops) or "—",
    }
    return sub, curve, synth


def analyse_all(df_long, period_start, period_end, low, high, settings=None):
    """
    df_long : une ligne par parcelle × date (sortie de build_rows).
    Retourne (synthese, detail, courbes) — trois DataFrames.
    """
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    df = df_long.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    synth_rows, details, curves = [], [], []
    for pid, sub in df.groupby("ID", sort=False):
        det, curve, synth = analyse_parcel(sub, period_start, period_end, low, high, settings)
        details.append(det)
        curves.append(curve.assign(ID=pid))
        synth_rows.append({"ID": pid, "Surface_ha": sub["Surface_ha"].iloc[0]
                           if "Surface_ha" in sub else None, **synth})
    synthese = pd.DataFrame(synth_rows)
    detail = pd.concat(details, ignore_index=True) if details else df
    curves = [c for c in curves if not c.empty]
    courbes = (pd.concat(curves, ignore_index=True) if curves
               else pd.DataFrame(columns=["ID", "Date", "NDVI_lisse", "Incertain", "Phase"]))
    courbes["Date"] = pd.to_datetime(courbes["Date"])
    return synthese, detail, courbes
