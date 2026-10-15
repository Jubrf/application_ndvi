"""
Calculs Earth Engine de l'API : fichier de parcelles, dates disponibles, calcul d'une analyse
(nouvelle ou complétée) en tâche de fond, météo, vignettes, analyse à une date.

Mêmes fonctions et mêmes arguments que l'interface Streamlit (ui/loading.py,
ui/temporal_setup.py, ui/weather.py, ui/thumbnail.py, ui/views/une_date.py) : une date
calculée ici donne les mêmes résultats bruts que dans Streamlit, et le fichier produit
(analyse enregistrée .zip) est le même format.
"""
import datetime
import hashlib
import threading
import time
import uuid
from collections import OrderedDict

import pandas as pd

from api import gee as gee_conn
from ndvi_app.config import (APP_VERSION, COLOR_INVALID, DEFAULT_BUFFER_M, DEFAULT_MIN_CLEAR,
                             DEFAULT_MIN_PIXELS, DEFAULT_THRESHOLDS, STATUS_OK)
from ndvi_app.core import soil_reading as soil
from ndvi_app.core.completion import dates_to_add, image_covers, merge_results
from ndvi_app.core.geometry import looks_like_wgs84, outline_geojson, prepare_all, region_geojson
from ndvi_app.core.quality import build_rows, colorize, unique_ids
from ndvi_app.core.session_format import build_session_zip, saved_settings
from ndvi_app.core.timeseries import DEFAULT_SETTINGS
from ndvi_app.core.vector_io import VectorFileError, load_vector_from_bytes
from ndvi_app.earth_engine import sentinel2 as gee
from ndvi_app.earth_engine import weather as era5
from ndvi_app.exports.excel import to_excel
from ndvi_app.exports.folium_map import build_map, legend_text
from ndvi_app.exports.kml import build_kml
from ndvi_app.logs import log
from ndvi_app.ui.columns import OS_COLS, TECH_COLS, column_help, pick

MAX_PARCEL_SETS = 3


def calc_params(calc):
    """Paramètres Earth Engine (comme ui.context.Settings.params_t) à partir des réglages de
    calcul de l'interface {buffer_m, cs_threshold, cloud_buffer_m, iqr_k}."""
    p = {**gee.DEFAULT_PARAMS, "cs_threshold": float(calc["cs_threshold"]),
         "cloud_buffer_m": int(calc["cloud_buffer_m"]), "iqr_k": float(calc["iqr_k"])}
    return tuple(sorted(p.items()))


def default_calc():
    return {"buffer_m": DEFAULT_BUFFER_M, "cs_threshold": gee.DEFAULT_PARAMS["cs_threshold"],
            "cloud_buffer_m": gee.DEFAULT_PARAMS["cloud_buffer_m"],
            "iqr_k": gee.DEFAULT_PARAMS["iqr_k"]}


def default_interpretation():
    return {"low": DEFAULT_THRESHOLDS[0], "high": DEFAULT_THRESHOLDS[1],
            "min_clear": DEFAULT_MIN_CLEAR, "min_pixels": DEFAULT_MIN_PIXELS,
            "min_weight": DEFAULT_SETTINGS["min_weight"],
            "smooth_days": DEFAULT_SETTINGS["smooth_days"], "grey": None}


# ---------------------------------------------------------------- fichier de parcelles
class ParcelSet:
    """Fichier de parcelles d'une nouvelle analyse (comme ui/loading.load_project)."""

    def __init__(self, data, name, buffer_m=DEFAULT_BUFFER_M, id_field=None):
        self.data, self.name = data, name
        self.file_hash = hashlib.md5(data).hexdigest()      # même empreinte que Streamlit
        try:
            features = load_vector_from_bytes(data, name)
        except VectorFileError as e:
            raise ValueError(str(e))
        except Exception as e:
            log(f"Lecture du fichier de parcelles {name} : {type(e).__name__}: {e}")
            raise ValueError(f"Fichier « {name} » illisible ({type(e).__name__}). Dépose un ZIP de "
                             "shapefile (.shp, .shx, .dbf, .prj) ou un GeoJSON.")
        if not features:
            raise ValueError("Aucune parcelle trouvée dans le fichier.")
        if not looks_like_wgs84(features):
            raise ValueError("Coordonnées non reconnues : le fichier .prj est probablement absent "
                             "du ZIP. Ajoute-le ou exporte la couche en WGS84 / Lambert-93 avec son .prj.")
        self.features = features
        self.fields = list(features[0]["properties"].keys())
        if self.fields:
            # même choix par défaut que ui/loading.default_id_field (nouvelle analyse)
            default = "NUM_ILOT" if "NUM_ILOT" in self.fields else self.fields[0]
            self.id_field = id_field if id_field in self.fields else default
            self.ids = unique_ids([f["properties"].get(self.id_field) for f in features])
        else:
            self.id_field = None
            self.ids = [f"PARCELLE_{i + 1}" for i in range(len(features))]
        self.buffer_m = int(buffer_m)
        self.geoinfo = prepare_all(features, self.buffer_m)
        self.region = region_geojson(features)
        self.geoms_key = f"{self.file_hash}|{self.buffer_m}"
        self.key = f"{self.file_hash}|{self.buffer_m}|{self.id_field}"

    def info(self):
        n_reduced = sum(1 for g in self.geoinfo if g["geojson"] and g["buffer_m"] < self.buffer_m)
        n_bad = sum(1 for g in self.geoinfo if g["geojson"] is None)
        dup = len(self.ids) - len({f["properties"].get(self.id_field) for f in self.features}) if self.id_field else 0
        msgs = [("success", f"{len(self.features)} parcelles chargées"
                 + (f" · buffer réduit sur {n_reduced} petite(s) parcelle(s)" if n_reduced else ""))]
        if n_bad:
            msgs.append(("warning", f"{n_bad} géométrie(s) inexploitable(s) (vides ou invalides) : ignorée(s)."))
        if dup > 0:
            msgs.append(("warning", f"Identifiants en double dans « {self.id_field} » : suffixes _2, _3… ajoutés."))
        return {"key": self.key, "name": self.name, "n": len(self.features), "fields": self.fields,
                "id_field": self.id_field, "buffer_m": self.buffer_m, "messages": msgs,
                "ids_preview": self.ids[:5]}

    def geometry(self):
        return {"parcels": [{"id": pid, "poly": _rings(f["geometry"])}
                            for pid, f in zip(self.ids, self.features)]}


def _rings(geom):
    geoms = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    return [[[round(x, 5), round(y, 5)] for x, y in g.simplify(0.00002).exterior.coords]
            for g in geoms if g.geom_type == "Polygon" and not g.is_empty]


class ParcelStore:
    def __init__(self):
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def load(self, data, name, buffer_m, id_field):
        key = f"{hashlib.md5(data).hexdigest()}|{int(buffer_m)}|{id_field}"
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        ps = ParcelSet(data, name, buffer_m, id_field)
        with self._lock:
            self._items[ps.key] = ps
            self._items[key] = ps
            while len(self._items) > 2 * MAX_PARCEL_SETS:
                self._items.popitem(last=False)
        return ps

    def get(self, key):
        with self._lock:
            return self._items.get(key)


# ---------------------------------------------------------------- dates disponibles
def list_dates(src, start, end, params_t):
    """Dates Sentinel-2 de la période avec le % de ciel clair sur les parcelles (src : ParcelSet
    ou Analysis). Entrées list_dates (date en datetime.date)."""
    gee_conn.ensure()
    return gee.list_dates(str(start), str(end), src.file_hash, params_t, src.region)


def date_entry(d, min_clear):
    covers = image_covers(d)
    return {"date": d["date"].isoformat(), "clear_pct": d.get("clear_pct"),
            "cover_pct": d.get("cover_pct"), "n_images": d.get("n_images"), "covers": covers,
            "ok": bool(covers and d["clear_pct"] >= min_clear)}


# ---------------------------------------------------------------- météo
def fetch_weather(src, start, end, refresh=False):
    """Météo ERA5-Land de la zone ; {"days": {}, "error": …} en cas d'échec (comme ui/weather.fetch)."""
    try:
        gee_conn.ensure()
        if refresh:
            era5.daily_weather.clear()
        return era5.daily_weather(start, end, src.file_hash, src.region)
    except Exception as e:
        log(f"Météo ERA5-Land indisponible : {type(e).__name__}: {e}")
        return {"source": era5.SOURCE, "start": start, "end": end, "days": {},
                "error": f"{type(e).__name__}: {e}"[:300]}


def _meta(src_name, file_hash, id_field, geoms_key, params_t, s, period, errors, weather):
    params = dict(params_t)
    return {"app_version": APP_VERSION, "source_file": src_name, "file_hash": file_hash,
            "id_field": id_field, "geoms_key": geoms_key, "params": params,
            "settings": saved_settings(s, params, geoms_key), "period": list(period),
            "errors": errors,
            # météo enregistrée seulement si la requête a abouti (comme ui/banner._session_zip)
            "weather": weather if weather and not weather.get("error") else None}


def with_weather(a, s, refresh=False):
    """Fichier d'analyse avec la météo de sa période (récupérée maintenant)."""
    start, end = era5.needed_period(a.p_start, a.p_end)
    w = fetch_weather(a, start, end, refresh=refresh)
    ss = a.session
    meta = _meta(ss.get("source_file"), ss.get("file_hash"), ss.get("id_field"), a.geoms_key,
                 a.params_t, s, (a.p_start, a.p_end), ss.get("errors") or [], w)
    return build_session_zip(meta, a.features, a.ids, a.geoinfo, ss["raws"]), w


# ---------------------------------------------------------------- calcul en tâche de fond
class Job:
    def __init__(self, kind, total):
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.total = kind, total
        self.done, self.current = 0, None
        self.state, self.message = "running", None
        self.errors, self.result = [], None      # result : (zip bytes, nom de fichier)
        self.cancel = False
        self.started = time.time()

    def view(self):
        return {"id": self.id, "kind": self.kind, "total": self.total, "done": self.done,
                "current": self.current, "state": self.state, "message": self.message,
                "errors": self.errors[-20:], "elapsed": round(time.time() - self.started)}


class Jobs:
    def __init__(self):
        self._jobs = OrderedDict()
        self._lock = threading.Lock()

    def start(self, kind, total, fn):
        job = Job(kind, total)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > 10:
                self._jobs.popitem(last=False)

        def run():
            try:
                fn(job)
                if job.state == "running":
                    job.state = "done"
            except Exception as e:
                log(f"Tâche {job.kind} en erreur : {type(e).__name__}: {e}")
                job.state, job.message = "error", f"{type(e).__name__} : {str(e)[:400]}"
        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, jid):
        with self._lock:
            return self._jobs.get(jid)


def _compute_dates(job, dates, geoms_key, params_t, geojsons, region):
    """Calcule les dates une par une (comme ui/temporal_setup._run_dates)."""
    raws, errors = [], []
    for d in sorted(dates):
        if job.cancel:
            break
        job.current = d
        try:
            raws.append((d, gee.compute_day_stats(d, geoms_key, params_t, geojsons, region)))
        except Exception as e:
            dd = datetime.date.fromisoformat(d)
            errors.append(f"{dd:%d/%m/%Y} : {type(e).__name__} — {e}")
            job.errors.append(errors[-1])
        job.done += 1
    job.current = None
    return raws, errors


def run_new(job, ps, period, dates, params_t, s):
    """Nouvelle analyse : dates choisies, météo, fichier d'analyse."""
    gee_conn.ensure()
    raws, errors = _compute_dates(job, dates, ps.geoms_key, params_t,
                                  [g["geojson"] for g in ps.geoinfo], ps.region)
    if job.cancel:
        job.state, job.message = "cancelled", "Calcul arrêté."
        return
    if not raws:
        job.state, job.message = "error", "Aucune date n'a pu être calculée."
        return
    job.current = "météo"
    w = fetch_weather(ps, *era5.needed_period(*period))
    meta = _meta(ps.name, ps.file_hash, ps.id_field, ps.geoms_key, params_t, s, period, errors, w)
    data = build_session_zip(meta, ps.features, ps.ids, ps.geoinfo, raws)
    job.result = (data, f"analyse_ndvi_{period[0]}_{period[1]}.zip")
    job.message = f"{len(raws)} date(s) calculée(s)" + (f", {len(errors)} en erreur" if errors else "")


def run_extend(job, a, period, dates, s):
    """Compléter une analyse : seules les nouvelles dates, avec ses paramètres et ses contours."""
    gee_conn.ensure()
    raws, errors = _compute_dates(job, dates, a.geoms_key, a.params_t,
                                  [g["geojson"] for g in a.geoinfo], a.region)
    if job.cancel:
        job.state, job.message = "cancelled", "Calcul arrêté."
        return
    ss = a.session
    merged, all_errors = merge_results(ss["raws"], raws, ss.get("errors") or [], errors)
    job.current = "météo"
    w = fetch_weather(a, *era5.needed_period(*period))
    meta = _meta(ss.get("source_file"), ss.get("file_hash"), ss.get("id_field"), a.geoms_key,
                 a.params_t, s, period, all_errors, w)
    data = build_session_zip(meta, a.features, a.ids, a.geoinfo, merged)
    job.result = (data, f"analyse_ndvi_{period[0]}_{period[1]}.zip")
    job.message = f"{len(raws)} date(s) ajoutée(s)" + (f", {len(errors)} en erreur" if errors else "")


def extension_dates(a, available, min_clear):
    done, new, n_outside = dates_to_add(available, a.session["raws"])
    return {"done": [d.isoformat() for d in done], "new": [date_entry(d, min_clear) for d in new],
            "n_outside": n_outside}


# ---------------------------------------------------------------- vignette
def thumbnail(src, params_t, pid, date_str, mode, show_mask):
    gee_conn.ensure()
    idx = src.ids.index(pid)
    extra = {} if mode == gee.THUMB_NATURAL else {"mode": mode}
    return gee.parcel_thumbnail(date_str, f"{src.geoms_key}|{idx}", params_t,
                                outline_geojson(src.features[idx]["geometry"]),
                                src.geoinfo[idx]["geojson"], show_mask, **extra)


# ---------------------------------------------------------------- analyse à une date
def one_date(src, params_t, date_str, s):
    """Tableau d'une date (comme ui/views/une_date._results) : (df_os, df_all, météo)."""
    gee_conn.ensure()
    raw = gee.compute_day_stats(date_str, src.geoms_key, params_t,
                                [g["geojson"] for g in src.geoinfo], src.region)
    day0 = datetime.date.fromisoformat(date_str)
    wx = fetch_weather(src, str(day0 - datetime.timedelta(days=era5.WX_MARGIN_DAYS)), date_str)
    rows = build_rows(src.ids, src.geoinfo, raw, date_str, s["min_pixels"], s["min_clear"],
                      s["low"], s["high"], weather_days=wx.get("days"))
    df_all = pd.DataFrame(rows)
    # Lecture à une date : sans tendance de la courbe (pas de « Levée possible »)
    df_all["Lecture"] = [soil.reading(v, s["low"], n, e, None) if st_ == STATUS_OK else soil.NONE
                         for v, n, e, st_ in zip(df_all["NDVI"], df_all["NDTI"],
                                                 df_all["Etat_sol"], df_all["Statut"])]
    return pick(df_all, OS_COLS), df_all, wx


def one_date_excel(src, params_t, date_str, s):
    df_os, df_all, _ = one_date(src, params_t, date_str, s)
    grey = tuple(s["grey"]) if s.get("grey") else None
    return to_excel({"Résultats": df_os, "Technique": pick(df_all, TECH_COLS)},
                    column_help(s["low"], s["high"], grey))


def _fmt(v, digits=3, suffix=""):
    """Comme ui.formatting.fmt."""
    try:
        return "—" if v is None or pd.isna(v) else f"{float(v):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def one_date_map(src, params_t, date_str, s, fmt):
    """Carte KML / HTML de la date (comme ui/views/une_date._map_item)."""
    df_os, _, _ = one_date(src, params_t, date_str, s)
    day = pd.Timestamp(date_str)
    items = []
    for _, row in df_os.iterrows():
        ok = row["Statut"] == STATUS_OK
        items.append({
            "id": row["ID"], "color": colorize(row["Phase"]) if ok else COLOR_INVALID,
            "props": {"ID": row["ID"], "Date": day.strftime("%d/%m/%Y"), "Phase": row["Phase"],
                      "NDVI": _fmt(row["NDVI"]).replace(".", ","),
                      "NDTI": _fmt(row.get("NDTI")).replace(".", ","),
                      "Fiabilité": row.get("Fiabilite", "—"),
                      "Pixels clairs (%)": _fmt(row.get("Clair_pct"), 0)},
            "tooltip": (f"<b>{row['ID']}</b><br>{row['Phase']}<br>NDVI : {_fmt(row['NDVI'])}"
                        + (f" · NDTI : {_fmt(row['NDTI'])}" if pd.notna(row.get("NDTI")) else "")
                        + f"<br>Fiabilité : {row.get('Fiabilite', '—')} (poids {_fmt(row.get('Poids'), 2)})"
                        + f"<br>Pixels utilisés : {row.get('Pixels_utilises', '—')} · "
                          f"clairs : {_fmt(row.get('Clair_pct'), 0, ' %')}"),
        })
    extra = [("Non exploitable", COLOR_INVALID)]
    title = f"NDVI au {day:%d/%m/%Y}"
    if fmt == "kml":
        return build_kml(title, [{"geometry": f["geometry"], "name": it["id"], "color": it["color"],
                                  "opacity": 0.6, "props": it["props"]}
                                 for f, it in zip(src.features, items)], legend_text=legend_text(extra))
    geoms = [f["geometry"] for f in src.features]
    bounds = (min(g.bounds[0] for g in geoms), min(g.bounds[1] for g in geoms),
              max(g.bounds[2] for g in geoms), max(g.bounds[3] for g in geoms))
    return build_map(src.features, items, bounds, extra, title=title,
                     for_export=True).get_root().render().encode("utf-8")
