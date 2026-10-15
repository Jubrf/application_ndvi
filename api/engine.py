"""
Pont entre l'API et le moteur Python de l'application (ndvi_app.core / exports), sans Streamlit.

Une analyse ouverte = un fichier .zip enregistré par l'application (core.session_format).
Son interprétation (statuts, courbes, synthèse) est recalculée à chaque changement de
réglage, exactement comme dans l'interface Streamlit (ui/analysis.py).
"""
import hashlib
import json
import threading
from collections import OrderedDict

import pandas as pd
from ndvi_app.config import APP_VERSION, COLOR_INVALID, DEFAULT_GREY_ZONE
from ndvi_app.core.geometry import centroids, region_geojson
from ndvi_app.core.quality import build_rows, colorize, unique_ids
from ndvi_app.core.session_format import build_session_zip, read_session_zip, saved_settings
from ndvi_app.core.timeseries import analyse_all
from ndvi_app.core.vector_io import load_vector_from_bytes
from ndvi_app.exports import workbook
from ndvi_app.exports.folium_map import build_map, legend_text
from ndvi_app.exports.kml import build_kml
from ndvi_app.ui.columns import DETAIL_COLS, SYNTH_COLS, TECH_COLS, column_help, pick

MAX_OPEN = 3            # analyses gardées en mémoire (les plus récentes ; 512 Mo sur Render gratuit)
MAX_INTERPRETATIONS = 3  # jeux de réglages gardés par analyse


class Analysis:
    """Analyse enregistrée ouverte : contenu du fichier + interprétations en cache."""

    def __init__(self, data, file_name):
        session, geojson = read_session_zip(data)
        self.data = data                       # fichier tel quel (renvoyé à l'interface)
        self.key = hashlib.sha1(data).hexdigest()[:16]
        self.file_name = file_name
        self.session = session
        # mêmes contours que l'interface Streamlit (ui/loading.py lit parcelles.geojson ainsi)
        self.features = load_vector_from_bytes(geojson, "parcelles.geojson")
        self.ids = unique_ids([f["properties"].get("ID") for f in self.features])
        if len(self.ids) != len(session["geoinfo"]):
            raise ValueError("fichier incohérent : nombre de parcelles différent entre "
                             "session.json et parcelles.geojson.")
        self.p_start, self.p_end = session["period"]
        weather = session.get("weather") or {}
        self.weather_days = weather.get("days") or None
        self.positions = centroids(self.ids, session["geoinfo"])
        self.geoinfo = session["geoinfo"]
        self.file_hash = session.get("file_hash") or self.key
        self.geoms_key = session["geoms_key"]
        self.params_t = tuple(sorted(session["params"].items()))
        self._region = None
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    @property
    def region(self):
        """Union simplifiée des parcelles (zone des requêtes de dates et de météo)."""
        if self._region is None:
            self._region = region_geojson(self.features)
        return self._region

    @property
    def calc(self):
        return {"geoms_key": self.geoms_key, "params_t": self.params_t}

    # ------------------------------------------------------------ réglages
    def default_settings(self):
        c = self.session["settings"]
        grey = c.get("grey_zone")
        return {"low": c["thresholds"][0], "high": c["thresholds"][1],
                "min_clear": c["min_clear"], "min_pixels": c["min_pixels"],
                "min_weight": c["min_weight"], "smooth_days": c["smooth_days"],
                "grey": list(grey) if grey else None}

    @staticmethod
    def _key(s):
        return (s["low"], s["high"], s["min_clear"], s["min_pixels"], s["min_weight"],
                s["smooth_days"], tuple(s["grey"]) if s.get("grey") else None)

    # ------------------------------------------------------------ interprétation
    def interpret(self, s):
        """(df_long, synthese, detail, courbes) pour les réglages s (même calcul que Streamlit)."""
        key = self._key(s)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        rows = []
        for date_str, raw in self.session["raws"]:
            rows += build_rows(self.ids, self.session["geoinfo"], raw, date_str, s["min_pixels"],
                               s["min_clear"], s["low"], s["high"], weather_days=self.weather_days)
        df_long = pd.DataFrame(rows)
        ts = {"min_weight": s["min_weight"], "smooth_days": s["smooth_days"]}
        if s.get("grey"):
            ts["grey_zone"] = tuple(s["grey"])
        synthese, detail, courbes = analyse_all(df_long, self.p_start, self.p_end, s["low"],
                                                s["high"], ts, weather_days=self.weather_days,
                                                positions=self.positions)
        out = (df_long, synthese, detail, courbes)
        with self._lock:
            self._cache[key] = out
            while len(self._cache) > 2 * MAX_INTERPRETATIONS:
                self._cache.popitem(last=False)
        return out

    # ------------------------------------------------------------ données envoyées à l'interface
    def info(self):
        """Description de l'analyse (page Projet, bandeau, réglages par défaut)."""
        ss = self.session
        c = ss["settings"]
        return {
            "key": self.key, "file": self.file_name,
            "start": str(self.p_start), "end": str(self.p_end),
            "n_parcels": len(self.ids), "n_dates": len(ss["raws"]),
            "saved_at": ss.get("saved_at"), "app_version": ss.get("app_version"),
            "source_file": ss.get("source_file"),
            "errors": ss.get("errors") or [],
            "weather": None if not self.weather_days else {
                "first": min(self.weather_days), "last": max(self.weather_days)},
            "weather_error": (ss.get("weather") or {}).get("error"),
            "settings": self.default_settings(),
            "locked": {"buffer_m": c["buffer_m"], "cs_threshold": c["cs_threshold"],
                       "cloud_buffer_m": c["cloud_buffer_m"], "iqr_k": c["iqr_k"]},
            "default_grey": list(DEFAULT_GREY_ZONE),
            "server_version": APP_VERSION,
        }

    def geometry(self):
        """Contours simplifiés (lon, lat) pour la carte de l'interface."""
        def rings(geom):
            geoms = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
            return [[[round(x, 5), round(y, 5)] for x, y in g.simplify(0.00002).exterior.coords]
                    for g in geoms if g.geom_type == "Polygon" and not g.is_empty]
        return {"parcels": [{"id": pid, "poly": rings(f["geometry"])}
                            for pid, f in zip(self.ids, self.features)]}

    def payload(self, s):
        """Résultats interprétés, au format compact de l'interface (un tableau par mesure)."""
        key = ("payload",) + self._key(s)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        out = self._payload(s)
        with self._lock:
            self._cache[key] = out
            while len(self._cache) > 2 * MAX_INTERPRETATIONS:
                self._cache.popitem(last=False)
        return out

    def _payload(self, s):
        df_long, syn, det, cou = self.interpret(s)
        p0 = pd.Timestamp(self.p_start)
        n_days = (pd.Timestamp(self.p_end) - p0).days + 1
        di = lambda d: int((pd.Timestamp(d) - p0).days)

        etats = ["—"] + sorted({str(v) for v in det.get("Etat_sol", pd.Series(dtype=str)).dropna()} - {"—"})
        lects = ["—"] + sorted({str(v) for v in det.get("Lecture", pd.Series(dtype=str)).dropna()} - {"—"})
        motifs = sorted({m for m in det["Motif"].dropna() if isinstance(m, str) and m})

        def idx(lst, v):
            return lst.index(v) if isinstance(v, str) and v in lst else 0

        cur_by = {pid: cc.sort_values("Date") for pid, cc in cou.groupby("ID")}
        det_by = {}
        for m in det.sort_values(["ID", "Date"]).to_dict("records"):
            det_by.setdefault(m["ID"], []).append(m)
        syn_by = syn.set_index("ID")
        parcels = []
        for pid in self.ids:
            r = syn_by.loc[pid]
            curve = None
            cc = cur_by.get(pid)
            if cc is not None and not cc.empty:
                start = di(cc["Date"].iloc[0])
                unc, run = [], None
                for k, u in enumerate(cc["Incertain"]):
                    if u and run is None:
                        run = [start + k, start + k]
                    elif u:
                        run[1] = start + k
                    elif run:
                        unc.append(run)
                        run = None
                if run:
                    unc.append(run)
                curve = {"s": start, "v": [_r(v) for v in cc["NDVI_lisse"]], "u": unc}
            meas = []
            for m in det_by.get(pid, []):
                ok = m["Retenue"] == "Oui"
                v = m["NDVI"] if ok else m.get("NDVI_brut")
                meas.append([di(m["Date"]), _r(v), 1 if ok else 0, _r(m.get("NDTI")),
                             idx(etats, m.get("Etat_sol")), idx(lects, m.get("Lecture")),
                             motifs.index(m["Motif"]) + 1 if isinstance(m["Motif"], str) and m["Motif"] else 0,
                             _r(m.get("SWIR1_median")), _r(m.get("Poids")), _r(m.get("Clair_pct")),
                             _r(m.get("Pluie_3j_mm"), 1), _r(m.get("Humidite_sol")),
                             _s(m.get("Gel")), _r(m.get("NDVI_lisse")), _s(m.get("Statut")),
                             _r(m.get("Pluie_veille_mm"), 1), _r(m.get("Tmin_7j_C"), 1),
                             _s(m.get("Fiabilite"))])
            parcels.append({
                "id": pid, "ha": _r(r.get("Surface_ha")),
                "ndvi_fin": _r(r.get("NDVI_fin")), "phase_fin": _s(r.get("Phase_fin")),
                "a_verif": _s(r.get("A_verifier")),
                "conf": _s(r.get("Confiance")), "lect_fin": _s(r.get("Lecture_fin")),
                "etat_fin": _s(r.get("Etat_sol_fin")), "drops": _s(r.get("Baisses_rapides")),
                "chrono": _s(r.get("Chronologie")), "trou": _i(r.get("Plus_long_trou_j")),
                "n_ret": _i(r.get("Mesures_retenues")), "n_ecart": _i(r.get("Mesures_ecartees")),
                "jsm": _i(r.get("Jours_sans_mesure_fin")), "last": _s(r.get("Derniere_mesure")),
                "curve": curve, "meas": meas,
            })
        days = [str((p0 + pd.Timedelta(days=k)).date()) for k in range(n_days)]
        wx = self.weather_days or {}
        weather = None if not wx else {
            "rain": [(wx.get(d) or [None])[0] for d in days],
            "sm": [(wx.get(d) or [None, None])[1] for d in days],
            "tmin": [(wx.get(d) or [None, None, None])[2] for d in days]}
        return {"key": self.key, "settings": s, "start": str(self.p_start), "end": str(self.p_end),
                "nDays": n_days, "dates": sorted({di(d) for d, _ in self.session["raws"]}),
                "etats": etats, "lectures": lects, "motifs": motifs, "weather": weather,
                "help": column_help(s["low"], s["high"], tuple(s["grey"]) if s.get("grey") else None),
                "parcels": parcels}

    # ------------------------------------------------------------ fichiers
    def excel(self, s):
        df_long, syn, det, cou = self.interpret(s)
        grey = tuple(s["grey"]) if s.get("grey") else None
        return workbook.results_workbook(
            df_long, syn, det, cou, self.p_start, self.p_end, s["low"], s["high"],
            column_help(s["low"], s["high"], grey), pick,
            {"synth": SYNTH_COLS, "detail": DETAIL_COLS, "tech": TECH_COLS},
            weather_days=self.weather_days)

    def session_zip(self, s):
        """Fichier d'enregistrement avec les réglages d'interprétation s (mêmes résultats bruts)."""
        ss = self.session
        params = dict(ss["params"])
        meta = {
            "app_version": APP_VERSION, "source_file": ss.get("source_file"),
            "file_hash": ss.get("file_hash"), "id_field": ss.get("id_field"),
            "geoms_key": ss["geoms_key"], "params": params,
            "settings": saved_settings(s, params, ss["geoms_key"]),
            "period": [self.p_start, self.p_end], "errors": ss.get("errors") or [],
            "weather": ss.get("weather"),
        }
        return build_session_zip(meta, self.features, self.ids, ss["geoinfo"], ss["raws"])

    def map_file(self, s, day, fmt):
        """Carte des phases au jour `day` (date ISO) : KML ou HTML, comme la page Carte."""
        _, syn, _, cou = self.interpret(s)
        d = pd.Timestamp(day)
        label = f"{d:%d/%m/%Y}"
        on_day = cou[cou["Date"] == d].set_index("ID")
        conf = syn.set_index("ID")["Confiance"]
        items = []
        for pid in self.ids:
            if pid in on_day.index:
                r = on_day.loc[pid]
                unc = bool(r["Incertain"])
                items.append({"id": pid, "color": colorize(r["Phase"]), "opacity": 0.35 if unc else 0.65,
                              "props": {"ID": pid, "Date": label, "Phase": r["Phase"],
                                        "NDVI lissé": f"{r['NDVI_lisse']:.3f}".replace(".", ","),
                                        "Interpolé": "Oui" if unc else "Non",
                                        "Confiance": conf.get(pid, "—")},
                              "tooltip": f"<b>{pid}</b><br>{r['Phase']}<br>NDVI lissé : {r['NDVI_lisse']:.3f}"
                                         + ("<br><i>Interpolé : aucune mesure à moins de 15 jours</i>" if unc else "")})
            else:
                items.append({"id": pid, "color": COLOR_INVALID, "opacity": 0.5,
                              "props": {"ID": pid, "Date": label, "Phase": "Pas de courbe à cette date",
                                        "NDVI lissé": "—", "Interpolé": "—", "Confiance": conf.get(pid, "—")},
                              "tooltip": f"<b>{pid}</b><br>Pas de courbe à cette date"})
        extra = [("Pas de courbe à cette date", COLOR_INVALID)]
        title = f"Phases NDVI au {label}"
        if fmt == "kml":
            return build_kml(title, [
                {"geometry": f["geometry"], "name": it["id"], "color": it["color"],
                 "opacity": it["opacity"], "props": it["props"]}
                for f, it in zip(self.features, items)], legend_text=legend_text(extra))
        geoms = [f["geometry"] for f in self.features]
        bounds = (min(g.bounds[0] for g in geoms), min(g.bounds[1] for g in geoms),
                  max(g.bounds[2] for g in geoms), max(g.bounds[3] for g in geoms))
        return build_map(self.features, items, bounds, extra, title=title,
                         for_export=True).get_root().render().encode("utf-8")


def _r(v, n=3):
    try:
        if v is None or pd.isna(v):
            return None
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        if v is None or pd.isna(v):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _s(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v.date()) if isinstance(v, pd.Timestamp) else str(v)


class Store:
    """Analyses ouvertes, en mémoire (perdues au redémarrage du serveur : l'interface
    renvoie alors le fichier automatiquement)."""

    def __init__(self):
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def open(self, data, file_name):
        key = hashlib.sha1(data).hexdigest()[:16]
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                a = self._items[key]
                a.file_name = file_name
                return a
        a = Analysis(data, file_name)
        with self._lock:
            self._items[key] = a
            while len(self._items) > MAX_OPEN:
                self._items.popitem(last=False)
        return a

    def get(self, key):
        with self._lock:
            a = self._items.get(key)
            if a is not None:
                self._items.move_to_end(key)
            return a
