"""
Analyse multi-temporelle par parcelle (Python pur, sans Earth Engine).

Chaîne de traitement d'une parcelle :
  1. Sélection des mesures : écartées si statut non OK ou poids de fiabilité
     sous le minimum. Une valeur nettement sous ses voisines avant ET après n'est
     écartée que si un nuage est probable (v4.3) : parcelle en partie masquée, score de
     clarté faible, ou nuages sur le secteur ce jour-là. Plusieurs mesures basses
     consécutives (fauche puis repousse) sont gardées si au moins une est sur une image
     propre ; une chute seule sur une image propre est gardée et signalée.
  2. Lissage : moyenne pondérée à noyau gaussien sur une grille journalière
     (poids = fiabilité × proximité dans le temps). Deux passes supplémentaires
     réduisent le poids des points situés sous la courbe : les nuages ne font
     que baisser le NDVI, la courbe suit donc plutôt le haut de la série.
     Pas d'extrapolation : la courbe va de la première à la dernière mesure retenue.
  3. Phases (sol nu / couvert peu développé / bien développé) selon deux seuils,
     épisodes de moins de N jours fusionnés, baisses rapides repérées.
  4. Synthèse : chronologie en texte, phase en fin de période, plus long trou
     sans mesure, niveau de confiance, durée de couverture du sol (v5.3).
  5. Lecture des mesures sur parcelle peu verte (levée, résidus, sol nu) selon le NDVI,
     le NDTI et l'état du sol (météo + infrarouge moyen) : voir core/soil_reading.py.
"""
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from ndvi_app.config import PHASE_BIEN, PHASE_NU, PHASE_PEU, PHASES, STATUS_OK  # noqa: F401 (réexportés)
from ndvi_app.config import STATUS_CLOUD
from ndvi_app.core import soil_reading as soil

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
MOTIF_DIP_SERIES = "Baisse sur plusieurs dates nuageuses"
NOTE_DIP_KEPT = "Chute ponctuelle sans nuage détecté (gardée)"   # mesure retenue, signalée

# Signes de nuage pour une mesure en baisse (v4.3)
CLOUD_MIN_CLEAR_PCT = 99.5    # parcelle en partie masquée : moins de 100 % de pixels clairs
CLOUD_MIN_SCORE = 0.75        # score de clarté Cloud Score+ moyen des pixels gardés
SECTOR_RADIUS_KM = 2.0        # voisines : parcelles à moins de 2 km…
SECTOR_MIN_NEIGHBOURS = 5     # …au moins 5 avec une mesure ce jour-là, sinon repli sur la journée
SECTOR_SHARE = 0.5            # nuage sur le secteur : la moitié des voisines masquées ou en baisse
DAY_SHARE = 0.30              # repli : 30 % des parcelles du jour en partie masquées


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
def _status_selection(sub, settings, status_ok=STATUS_OK):
    """Mesures exploitables (statut OK, NDVI présent, poids suffisant) et motifs d'exclusion."""
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
    return retained, motif


def low_points(sub, base, settings):
    """Mesures nettement sous les mesures exploitables de part et d'autre (fenêtre de N jours) :
    candidates à l'exclusion (nuage) ou vraie baisse (fauche, récolte). Tableau booléen."""
    days = (sub["Date"] - sub["Date"].min()).dt.days.to_numpy()
    vals = sub["NDVI"].to_numpy(dtype=float)
    win, thr = settings["dip_window_days"], settings["dip_threshold"]
    low = np.zeros(len(sub), dtype=bool)
    for i in np.flatnonzero(base):
        before = base & (days < days[i]) & (days >= days[i] - win)
        after = base & (days > days[i]) & (days <= days[i] + win)
        if before.any() and after.any():
            low[i] = vals[i] < vals[before].max() - thr and vals[i] < vals[after].max() - thr
    return low


def _num(sub, col):
    return (pd.to_numeric(sub[col], errors="coerce").to_numpy(dtype=float) if col in sub
            else np.full(len(sub), np.nan))


def select_measures(sub, settings, status_ok=STATUS_OK, sector_cloud=None):
    """
    sub : lignes d'une parcelle (colonnes Date datetime64, NDVI, Poids, Statut, et si présents
          Clair_pct, Score_clarte), triées par date.
    sector_cloud : {Date: bool} nuages probables sur le secteur ce jour-là (voir sector_cloudiness).
    Retourne (retenue: array bool, motif: list[str|None]).

    Mesure basse (sous ses voisines avant ET après) :
      - plusieurs mesures basses consécutives : gardées (fauche, récolte puis repousse), sauf si
        toutes portent un signe de nuage ;
      - mesure basse seule : écartée si signe de nuage, sinon gardée et signalée.
    Signe de nuage : parcelle en partie masquée, score de clarté < CLOUD_MIN_SCORE, ou nuages
    sur le secteur ce jour-là.
    """
    retained, motif = _status_selection(sub, settings, status_ok)
    base = retained.copy()
    low = low_points(sub, base, settings)
    if not low.any():
        return retained, motif
    clear, score = _num(sub, "Clair_pct"), _num(sub, "Score_clarte")
    sector_cloud = sector_cloud or {}
    cloud = np.array([bool(low[i] and ((clear[i] < CLOUD_MIN_CLEAR_PCT) or (score[i] < CLOUD_MIN_SCORE)
                                       or sector_cloud.get(d, False)))
                      for i, d in enumerate(sub["Date"])])
    # séries de mesures basses consécutives (parmi les mesures exploitables)
    runs, cur = [], []
    for i in np.flatnonzero(base):
        if low[i]:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    for run in runs:
        if len(run) >= 2:
            if cloud[run].all():
                for i in run:
                    retained[i] = False
                    motif[i] = MOTIF_DIP_SERIES
        elif cloud[run[0]]:
            retained[run[0]] = False
            motif[run[0]] = MOTIF_DIP
        else:
            motif[run[0]] = NOTE_DIP_KEPT
    return retained, motif


def _km(p, q):
    """Distance approchée (km) entre deux points (lon, lat) proches."""
    k = 111.32 * math.cos(math.radians((p[1] + q[1]) / 2))
    return math.hypot((p[0] - q[0]) * k, (p[1] - q[1]) * 110.57)


def sector_cloudiness(df, settings, positions=None, status_ok=STATUS_OK):
    """
    Nuages probables sur le secteur, pour chaque mesure basse : {(ID, Date): bool}.
    Avec au moins SECTOR_MIN_NEIGHBOURS voisines (positions {ID: (lon, lat)}, rayon
    SECTOR_RADIUS_KM) ayant une mesure ce jour-là : la moitié ou plus sont masquées ou en baisse.
    Sinon (pas de positions ou trop peu de voisines) : DAY_SHARE des parcelles du jour en partie
    masquées. Une fauche ne touche que les parcelles fauchées ; un nuage, tout le secteur.
    """
    affected = defaultdict(dict)     # Date → {ID: masquée ou en baisse}
    masked_day = defaultdict(list)   # Date → [masquée]
    lows = []
    for pid, sub in df.groupby("ID", sort=False):
        sub = sub.sort_values("Date").reset_index(drop=True)
        base, _ = _status_selection(sub, settings, status_ok)
        low = low_points(sub, base, settings)
        clear = _num(sub, "Clair_pct")
        for i, (d, st) in enumerate(zip(sub["Date"], sub["Statut"])):
            if st not in (status_ok, STATUS_CLOUD):
                continue                      # hors image, géométrie, trop peu de pixels
            masked = st == STATUS_CLOUD or clear[i] < CLOUD_MIN_CLEAR_PCT
            affected[d][pid] = bool(masked or low[i])
            masked_day[d].append(bool(masked))
            if low[i]:
                lows.append((pid, d))
    out = {}
    positions = positions or {}
    for pid, d in lows:
        here = positions.get(pid)
        neigh = ([q for q in affected[d] if q != pid and q in positions
                  and _km(here, positions[q]) <= SECTOR_RADIUS_KM] if here else [])
        if len(neigh) >= SECTOR_MIN_NEIGHBOURS:
            out[(pid, d)] = sum(affected[d][q] for q in neigh) / len(neigh) >= SECTOR_SHARE
        else:
            m = masked_day[d]
            out[(pid, d)] = bool(m) and sum(m) / len(m) >= DAY_SHARE
    return out


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


def _grey_zone(curve, zone):
    """Zone grise : NDVI lissé à la dernière mesure retenue dans [min, max] → à vérifier.
    Rien (pas de colonne) quand la zone grise est désactivée."""
    if not zone:
        return {}
    if curve.empty:
        return {"A_verifier": "—"}
    v = float(curve["NDVI_lisse"].iloc[-1])
    return {"A_verifier": "Oui" if zone[0] <= v <= zone[1] else "Non"}


def coverage(curve, low, span, uncertain_days):
    """
    Durée de couverture du sol (NDVI lissé ≥ seuil bas) de la première à la dernière date
    analysée (span = (début, fin), commun à toutes les parcelles).
    La courbe va de la 1re à la dernière mesure retenue de la parcelle : avant et après, elle
    est prolongée à plat ; ces jours sont incertains au-delà de uncertain_days, comme dans la
    courbe. Jours sans valeur lissée au milieu (trou très long) : interpolation linéaire, comme
    le tracé. Les jours incertains sont comptés et signalés à part.
    """
    empty = {"Jours_couverts": None, "Part_couverte_pct": None, "Jours_couverts_incertains": None,
             "Couverture_max_j": None, "Couverture_max_debut": None, "Couverture_max_fin": None}
    if curve.empty or span is None:
        return empty
    d0, d1 = pd.Timestamp(span[0]).normalize(), pd.Timestamp(span[1]).normalize()
    c0, c1 = curve["Date"].iloc[0], curve["Date"].iloc[-1]
    vals = pd.Series(curve["NDVI_lisse"].to_numpy(dtype=float)).interpolate(limit_area="inside")
    if vals.isna().all():
        return empty
    vals = vals.bfill().ffill().to_numpy()
    unc = curve["Incertain"].to_numpy(dtype=bool)
    before, after = max(0, (c0 - d0).days), max(0, (d1 - c1).days)
    v = np.concatenate([np.full(before, vals[0]), vals, np.full(after, vals[-1])])
    u = np.concatenate([np.arange(before, 0, -1) > uncertain_days, unc,
                        np.arange(1, after + 1) > uncertain_days])
    start = c0 - pd.Timedelta(days=before)
    covered = v >= low
    best, best_a, a = 0, None, None
    for k, cv in enumerate(np.append(covered, False)):
        if cv and a is None:
            a = k
        elif not cv and a is not None:
            if k - a > best:
                best, best_a = k - a, a
            a = None
    total = (d1 - d0).days + 1
    n = int(covered.sum())
    return {
        "Jours_couverts": n,
        "Part_couverte_pct": round(100 * n / total) if total > 0 else None,
        "Jours_couverts_incertains": int((covered & u).sum()),
        "Couverture_max_j": int(best),
        "Couverture_max_debut": (start + pd.Timedelta(days=best_a)).date() if best else None,
        "Couverture_max_fin": (start + pd.Timedelta(days=best_a + best - 1)).date() if best else None,
    }


def analyse_parcel(sub, period_start, period_end, low, high, settings, weather_days=None,
                   sector_cloud=None, span=None):
    """
    sub : lignes d'une parcelle (Date datetime64, NDVI, Poids, Statut).
    weather_days : météo journalière (gel avant une baisse rapide) ou None.
    sector_cloud : {Date: bool} nuages sur le secteur pour les mesures basses (sector_cloudiness).
    span : (première, dernière date analysée), pour la durée de couverture.
    Retourne (detail: DataFrame, courbe: DataFrame, synthese: dict).
    """
    sub = sub.sort_values("Date").reset_index(drop=True)
    retained, motif = select_measures(sub, settings, sector_cloud=sector_cloud)
    sub = sub.assign(Retenue=np.where(retained, "Oui", "Non"), Motif=motif)

    kept = sub[retained]
    curve = smooth_curve(pd.DatetimeIndex(kept["Date"]), kept["NDVI"], kept["Poids"], settings)
    curve["Phase"] = [phase_of(v, low, high) for v in curve["NDVI_lisse"]]

    lisse = curve.set_index("Date")["NDVI_lisse"]
    sub["NDVI_lisse"] = sub["Date"].map(lisse)
    sub["Phase"] = [phase_of(v, low, high) for v in sub["NDVI_lisse"]]

    # Lecture des mesures retenues sur parcelle peu verte (levée, résidus, sol nu)
    c_start = curve["Date"].iloc[0] if not curve.empty else None
    has_state = "Etat_sol" in sub
    sub["Lecture"] = [
        soil.reading(v, low, r.get("NDTI"), r.get("Etat_sol") if has_state else None,
                     soil.rise_at(lisse, d, c_start)) if keep else soil.NONE
        for v, d, keep, (_, r) in zip(sub["NDVI"], sub["Date"], retained, sub.iterrows())]

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
    drop_txt = [_fmt(d, with_year) + (" (gel)" if soil.frost_near(weather_days, d) else "")
                for d in drops]
    last_i = sub.index[retained][-1] if retained.any() else None

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
        "Baisses_rapides": ", ".join(drop_txt) or "—",
        "Lecture_fin": sub.at[last_i, "Lecture"] if last_i is not None else soil.NONE,
        **_grey_zone(curve, settings.get("grey_zone")),
        "Etat_sol_fin": (sub.at[last_i, "Etat_sol"] if last_i is not None and has_state
                         else soil.NONE),
        **coverage(curve, low, span, settings["uncertain_days"]),
    }
    return sub, curve, synth


def analyse_all(df_long, period_start, period_end, low, high, settings=None,
                weather_days=None, positions=None):
    """
    df_long : une ligne par parcelle × date (sortie de build_rows).
    weather_days : météo journalière ou None.
    positions : {ID: (lon, lat)} des parcelles (geometry.centroids), pour repérer les nuages
                sur le secteur autour d'une mesure basse ; None → repli sur la journée entière.
    Retourne (synthese, detail, courbes) — trois DataFrames.
    """
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    df = df_long.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    sector = sector_cloudiness(df, settings, positions)
    span = (df["Date"].min(), df["Date"].max()) if len(df) else None
    by_parcel = defaultdict(dict)
    for (pid, d), v in sector.items():
        by_parcel[pid][d] = v
    synth_rows, details, curves = [], [], []
    for pid, sub in df.groupby("ID", sort=False):
        det, curve, synth = analyse_parcel(sub, period_start, period_end, low, high, settings,
                                           weather_days, sector_cloud=by_parcel.get(pid), span=span)
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
