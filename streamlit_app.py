import datetime
import hashlib

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from utils.gee_ndvi import (DEFAULT_PARAMS, compute_day_stats, init_gee, list_dates, log,
                             parcel_thumbnail)
from utils.geometry import looks_like_wgs84, outline_geojson, prepare_all, region_geojson
from utils.charts import dates_timeline, ndti_chart, parcel_chart
from utils.excel_charts import MAX_CHARTS, add_parcel_charts
from utils.map_export import build_kml
from utils.ndvi_processing import (
    DEFAULT_THRESHOLDS,
    STATUS_OK,
    build_rows,
    COLOR_INVALID,
    COLOR_MAP,
    colorize,
    unique_ids,
)
from utils.timeseries import DEFAULT_SETTINGS, analyse_all
from utils.session_io import build_session_zip, read_session_zip
from utils.vector_io import _load_vector_from_bytes

# Version affichée dans la barre latérale : à changer à chaque modification,
# pour savoir quel code tourne réellement sur Streamlit Cloud.
APP_VERSION = "v2.7.1 — 08/10/2026"

st.set_page_config(page_title="NDVI parcellaire", page_icon="🌱", layout="wide")
st.title("🌱 NDVI – Analyse parcellaire Sentinel-2")
st.sidebar.caption(f"Version {APP_VERSION}")
log(f"Script lancé ({APP_VERSION})")

# ============================================================
# INIT GEE
# ============================================================
with st.spinner("Connexion à Earth Engine…"):
    try:
        init_gee(st.secrets["GEE_SERVICE_ACCOUNT"], st.secrets["GEE_PRIVATE_KEY"])
    except Exception as e:
        log(f"Échec de la connexion Earth Engine : {type(e).__name__} — {e}")
        st.error(f"Connexion à Earth Engine impossible : {type(e).__name__} — {e}")
        st.stop()

# ============================================================
# PARAMÈTRES (barre latérale)
# Valeurs gardées en session (clés set_*) pour pouvoir les restaurer
# à l'ouverture d'une analyse enregistrée.
# ============================================================
SETTING_DEFAULTS = {
    "set_thr": DEFAULT_THRESHOLDS, "set_buffer": 10,
    "set_cs": DEFAULT_PARAMS["cs_threshold"], "set_cloudbuf": DEFAULT_PARAMS["cloud_buffer_m"],
    "set_iqr": DEFAULT_PARAMS["iqr_k"], "set_minclear": 50, "set_minpix": 10,
    "set_minw": DEFAULT_SETTINGS["min_weight"], "set_smooth": DEFAULT_SETTINGS["smooth_days"],
}
_pending = st.session_state.pop("pending_restore", None)
if _pending:
    st.session_state.update(_pending)
for _k, _v in SETTING_DEFAULTS.items():
    st.session_state.setdefault(_k, _v)

with st.sidebar:
    st.header("Paramètres d'analyse")
    low, high = st.slider(
        "Seuils d'interprétation (NDVI)", 0.0, 1.0, step=0.01, key="set_thr",
        help="Sous le 1er seuil : sol nu. Entre les deux : couvert peu développé. "
             "Au-dessus du 2nd : couvert bien développé. S'appliquent sans relancer l'analyse.",
    )
    if high - low < 0.05:
        st.warning("Les deux seuils sont très proches.")
    buffer_m = st.select_slider(
        "Buffer intérieur (m)", options=[0, 5, 10, 15, 20], key="set_buffer",
        help="Retire une bande en bordure de parcelle (haies, chemins, voisins). "
             "Réduit automatiquement pour les petites parcelles.",
    )
    with st.expander("Masque nuages et qualité"):
        cs_threshold = st.slider(
            "Seuil Cloud Score+", 0.40, 0.85, step=0.05, key="set_cs",
            help="Pixels sous ce score rejetés (nuages, ombres, brume). "
                 "Plus haut = plus strict.",
        )
        cloud_buffer_m = st.select_slider(
            "Marge autour des nuages (m)", options=[0, 10, 20, 40, 60], key="set_cloudbuf",
        )
        iqr_k = st.select_slider(
            "Exclusion des valeurs aberrantes (k × IQR)", options=[1.0, 1.5, 2.0, 3.0],
            key="set_iqr",
            help="Pixels hors [Q1 − k·IQR ; Q3 + k·IQR] exclus. Plus haut = moins d'exclusions.",
        )
        min_clear = st.slider("Part minimale de pixels clairs (%)", 0, 100, step=5,
                              key="set_minclear")
        min_pixels = st.number_input("Nombre minimal de pixels utilisés", 1, 500,
                                     key="set_minpix")
    with st.expander("Courbe temporelle"):
        min_weight = st.slider(
            "Poids de fiabilité minimal d'une mesure", 0.0, 1.0, step=0.05, key="set_minw",
            help="Les mesures sous ce poids sont écartées de la courbe (affichées en gris).")
        smooth_days = st.select_slider(
            "Lissage (jours)", options=[3, 4, 6, 8, 10, 15], key="set_smooth",
            help="Plus la valeur est grande, plus la courbe est lisse, mais plus elle réagit "
                 "tard aux changements (levée, destruction).")

ts_settings = {"min_weight": min_weight, "smooth_days": smooth_days}
gee_params = {**DEFAULT_PARAMS, "cs_threshold": cs_threshold,
              "cloud_buffer_m": cloud_buffer_m, "iqr_k": iqr_k}
params_t = tuple(sorted(gee_params.items()))

# ============================================================
# CHARGEMENT DU FICHIER
# ============================================================
SRC_NEW = "Nouvelle analyse : charger des parcelles"
SRC_OPEN = "Ouvrir une analyse enregistrée"
src_mode = st.radio("Source", [SRC_NEW, SRC_OPEN], horizontal=True, key="src_mode",
                    label_visibility="collapsed")


def _clear_results():
    for key in [k for k in st.session_state if k.startswith(("os_", "mt_"))]:
        del st.session_state[key]


if src_mode == SRC_NEW:
    uploaded = st.file_uploader("📁 Charger un SHP (ZIP) ou un GeoJSON", type=["zip", "geojson"])
    if uploaded is None:
        st.stop()
    vec_bytes, vec_name, source_name = uploaded.getvalue(), uploaded.name, uploaded.name
    file_hash = hashlib.md5(vec_bytes).hexdigest()
else:
    sess_file = st.file_uploader("📂 Analyse enregistrée (.zip créé par le bouton « Enregistrer "
                                 "l'analyse » de l'onglet temporel)", type=["zip"], key="session_file")
    if sess_file is None:
        st.stop()
    try:
        session, vec_bytes = read_session_zip(sess_file.getvalue())
    except ValueError as e:
        st.error(f"Impossible d'ouvrir ce fichier : {e}")
        st.stop()
    vec_name, source_name, file_hash = "parcelles.geojson", session["source_file"], session["file_hash"]
    sess_id = hashlib.md5(sess_file.getvalue()).hexdigest()
    if st.session_state.get("opened_session") != sess_id:
        # Restauration des réglages et des résultats, appliquée en tête du prochain passage
        _clear_results()
        st.session_state["loaded_file"] = file_hash
        st.session_state["opened_session"] = sess_id
        cfg = session["settings"]
        p0, p1 = session["period"]
        st.session_state["pending_restore"] = {
            "set_thr": tuple(cfg["thresholds"]), "set_buffer": cfg["buffer_m"],
            "set_cs": cfg["cs_threshold"], "set_cloudbuf": cfg["cloud_buffer_m"],
            "set_iqr": cfg["iqr_k"], "set_minclear": cfg["min_clear"],
            "set_minpix": cfg["min_pixels"], "set_minw": cfg["min_weight"],
            "set_smooth": cfg["smooth_days"],
            "mt_start_y": p0.year, "mt_start_m": p0.month, "mt_end_y": p1.year, "mt_end_m": p1.month,
            "mt_raw": session["raws"], "mt_period": (p0, p1), "mt_geoinfo": session["geoinfo"],
            "mt_ctx": {"geoms_key": session["geoms_key"],
                       "params_t": tuple(sorted(session["params"].items()))},
            "mt_errors": session.get("errors", []),
        }
        st.rerun()
    _saved = datetime.datetime.fromisoformat(session["saved_at"])
    st.info(f"Analyse enregistrée le {_saved:%d/%m/%Y à %H:%M} "
            f"(version {session.get('app_version', '?')}) — période du "
            f"{session['period'][0]:%d/%m/%Y} au {session['period'][1]:%d/%m/%Y} — "
            f"fichier d'origine : {source_name}. Résultats dans l'onglet « Analyse temporelle ».")

if st.session_state.get("loaded_file") != file_hash:
    _clear_results()
    st.session_state["loaded_file"] = file_hash

features = _load_vector_from_bytes(vec_bytes, vec_name)
if not features:
    st.error("Aucune parcelle trouvée dans le fichier.")
    st.stop()
if not looks_like_wgs84(features):
    st.error("Coordonnées non reconnues : le fichier .prj est probablement absent "
             "du ZIP. Ajoute-le ou exporte la couche en WGS84 / Lambert-93 avec son .prj.")
    st.stop()

fields = list(features[0]["properties"].keys())
if fields:
    _default_id = ("ID" if src_mode == SRC_OPEN and "ID" in fields
                   else "NUM_ILOT" if "NUM_ILOT" in fields else fields[0])
    id_field = st.selectbox("Champ identifiant des parcelles", fields,
                            index=fields.index(_default_id), disabled=src_mode == SRC_OPEN)
    ids = unique_ids([f["properties"].get(id_field) for f in features])
else:
    ids = [f"PARCELLE_{i + 1}" for i in range(len(features))]


@st.cache_data(show_spinner="Préparation des géométries…")
def _prepare_geometries(_features, file_key, buf):
    return prepare_all(_features, buf), region_geojson(_features)


geoinfo, region = _prepare_geometries(features, file_hash, buffer_m)
geoms_key = f"{file_hash}|{buffer_m}"
analysis_geojsons = [g["geojson"] for g in geoinfo]

n_reduced = sum(1 for g in geoinfo if g["geojson"] and g["buffer_m"] < buffer_m)
n_bad = sum(1 for g in geoinfo if g["geojson"] is None)
msg = f"{len(features)} parcelles chargées"
if n_reduced:
    msg += f" · buffer réduit sur {n_reduced} petite(s) parcelle(s)"
st.success(msg)
if n_bad:
    st.warning(f"{n_bad} géométrie(s) inexploitable(s) (vides ou invalides) : ignorée(s).")

geoms = [f["geometry"] for f in features]
minx = min(g.bounds[0] for g in geoms)
miny = min(g.bounds[1] for g in geoms)
maxx = max(g.bounds[2] for g in geoms)
maxy = max(g.bounds[3] for g in geoms)


# ============================================================
# UTILITAIRES
# ============================================================
def fmt(v, digits=3, suffix=""):
    try:
        if v is None or pd.isna(v):
            return "—"
        return f"{float(v):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def covers(d):
    """L'image couvre-t-elle au moins une partie des parcelles ?"""
    return bool(d.get("cover_pct")) and d.get("clear_pct") is not None


def usable(d, threshold):
    return covers(d) and d["clear_pct"] >= threshold


def date_label(d):
    if not covers(d):
        return f"⛔ {d['date']:%d/%m/%Y} — ne couvre pas les parcelles"
    mark = "✅" if d["clear_pct"] >= min_clear else "⚠️"
    label = f"{mark} {d['date']:%d/%m/%Y} — {d['clear_pct']:.0f} % de ciel clair"
    if d.get("cover_pct") is not None and d["cover_pct"] < 99.5:
        label += f" · couvre {d['cover_pct']:.0f} % des parcelles"
    return label


def run_analysis(date_str):
    return compute_day_stats(date_str, geoms_key, params_t,
                             analysis_geojsons, region)


def calc_context():
    """Ce qui conditionne les calculs GEE : sert à détecter des résultats périmés."""
    return {"geoms_key": geoms_key, "params_t": params_t}


def stale_warning(ctx):
    if ctx != calc_context():
        st.info("Les paramètres de calcul (buffer, masque, valeurs aberrantes) ont changé "
                "depuis ce calcul : relance l'analyse pour les appliquer. "
                "Les seuils d'interprétation, de qualité et de la courbe s'appliquent sans relancer.")


OS_COLS = ["ID", "NDVI", "Phase", "Couvert", "Fiabilite", "Poids", "Statut", "NDTI",
           "Sol_humide", "Clair_pct", "Pixels_utilises", "Surface_ha", "Date"]
TECH_COLS = ["ID", "Date", "Statut", "NDVI_brut", "NDVI_median", "NDVI_pondere", "NDVI_moyen",
             "NDVI_ecart_type", "EVI2_median", "NDTI_median", "SWIR1_median", "Poids", "Score_clarte", "Clair_pct",
             "Pixels_total", "Pixels_clairs", "Pixels_utilises", "Outliers_exclus",
             "Surface_ha", "Buffer_m", "Satellite"]
SYNTH_COLS = ["ID", "Phase_fin", "NDVI_fin", "Confiance", "Chronologie", "Baisses_rapides",
              "Mesures_retenues", "Mesures_ecartees", "Plus_long_trou_j", "Derniere_mesure",
              "Jours_sans_mesure_fin", "Surface_ha"]
DETAIL_COLS = ["ID", "Date", "NDVI", "NDVI_lisse", "Phase", "Fiabilite", "Poids", "Retenue",
               "Motif", "NDTI", "Sol_humide", "Statut", "Clair_pct"]
INT_COLS = ["Pixels_utilises", "Outliers_exclus", "Buffer_m", "Pixels_total", "Pixels_clairs",
            "Mesures_retenues", "Mesures_ecartees", "Plus_long_trou_j", "Jours_sans_mesure_fin"]


def pick(df, cols):
    df = df[[c for c in cols if c in df.columns]].copy()
    for c in INT_COLS:
        if c in df.columns:
            df[c] = df[c].round().astype("Int64")
    return df


def _t(v):
    return f"{v:.2f}".replace(".", ",")


COLUMN_HELP = {
    "ID": "Identifiant de la parcelle (champ choisi au chargement).",
    "NDVI": "NDVI médian de la parcelle (pixels clairs, hors valeurs aberrantes). "
            "Vide si la mesure n'est pas exploitable.",
    "NDVI_brut": "NDVI médian calculé même si la mesure n'est pas exploitable (à titre indicatif).",
    "Phase": f"Sol nu (NDVI < {_t(low)}), couvert peu développé ({_t(low)}–{_t(high)}), "
             f"couvert bien développé (≥ {_t(high)}). Dans le détail temporel : phase de la "
             "courbe lissée à cette date.",
    "Couvert": f"Oui si NDVI ≥ {_t(low)}, Non en dessous, — si la mesure n'est pas exploitable.",
    "Poids": "Poids de fiabilité de la mesure, de 0 à 1 (équivalent du « raw NDVI weight » "
             "de KERMAP, formule propre à l'appli) = clarté × score Cloud Score+ moyen × "
             "part de pixels non aberrants × facteur taille (plein à partir de 30 pixels).",
    "Fiabilite": "Bonne (poids ≥ 0,8), Moyenne (0,5–0,8), Faible (< 0,5). "
                 "Non exploitable si le statut n'est pas OK.",
    "Statut": "OK : mesure exploitable. Nuageux : part de pixels clairs sous le seuil "
              "(50 % par défaut). Trop peu de pixels : moins de pixels utilisés que le "
              "minimum (10 par défaut). Hors image : parcelle hors de l'emprise. "
              "Géométrie inexploitable : contour vide ou invalide.",
    "Clair_pct": "Part des pixels de la parcelle (après buffer) non masqués : ni nuage, "
                 "ni ombre, ni cirrus, ni neige, ni proche d'un nuage.",
    "Pixels_utilises": "Pixels de 10 m réellement utilisés : clairs et hors valeurs aberrantes.",
    "Surface_ha": "Surface de la parcelle d'origine (ha).",
    "Date": "Date d'acquisition de l'image.",
    "NDTI": "Indice de résidus de culture (B11 − B12) / (B11 + B12), médiane de la parcelle. "
            f"Interprétable seulement si la parcelle est peu verte (NDVI < {_t(low)}) : "
            "plus élevé sur résidus (cannes, pailles) que sur sol nu. EXPÉRIMENTAL : "
            "repère indicatif ≈ 0,10, à calibrer ; fortement réduit par l'humidité du sol.",
    "Sol_humide": "Indicatif, à confirmer : « Probable » si la parcelle est peu verte et "
                  "l'infrarouge moyen (B11) sombre (réflectance < 0,15). Le NDTI est alors "
                  "peu fiable (résidus et sol nu se ressemblent).",
    "NDTI_median": "Médiane du NDTI des pixels conservés.",
    "SWIR1_median": "Médiane de la réflectance B11 (infrarouge moyen, 1 610 nm).",
    # Synthèse temporelle
    "Phase_fin": "Phase de la courbe lissée à la dernière mesure retenue (état « à l'instant T »).",
    "NDVI_fin": "NDVI lissé à la dernière mesure retenue.",
    "Confiance": "Bonne : ≥ 3 mesures retenues et aucun trou de plus de 20 jours. "
                 "Moyenne : ≥ 2 mesures et trou ≤ 35 jours. Faible sinon.",
    "Chronologie": "Succession des phases de la courbe lissée, épisodes de moins de 10 jours "
                   "fusionnés. Pas d'extrapolation avant la 1re ni après la dernière mesure retenue.",
    "Baisses_rapides": "Dates des chutes du NDVI lissé de plus de 0,20 en moins de 15 jours : "
                       "destruction, récolte ou gel probable.",
    "Mesures_retenues": "Mesures utilisées pour la courbe.",
    "Mesures_ecartees": "Mesures non utilisées (nuages, poids trop faible, chute isolée), "
                        "hors dates où la parcelle n'était pas dans l'image.",
    "Plus_long_trou_j": "Plus long intervalle sans mesure retenue sur la période, bords compris (jours).",
    "Derniere_mesure": "Date de la dernière mesure retenue.",
    "Jours_sans_mesure_fin": "Jours entre la dernière mesure retenue et la fin de la période.",
    # Détail temporel
    "NDVI_lisse": "Valeur de la courbe lissée à cette date.",
    "Retenue": "Oui si la mesure est utilisée pour la courbe.",
    "Motif": "Raison de l'exclusion : statut non OK, poids trop faible, ou chute isolée "
             "(valeur nettement sous ses voisines avant et après : résidu de nuage probable).",
    "Incertain": "Vrai si la date est à plus de 15 jours de toute mesure retenue "
                 "(courbe interpolée, tracée en pointillé).",
    # Technique
    "NDVI_median": "Médiane du NDVI des pixels conservés.",
    "NDVI_pondere": "Moyenne du NDVI pondérée par le score Cloud Score+ de chaque pixel.",
    "NDVI_moyen": "Moyenne simple du NDVI des pixels conservés.",
    "NDVI_ecart_type": "Écart-type du NDVI : hétérogénéité de la parcelle.",
    "EVI2_median": "Médiane de l'EVI2, indice moins saturé que le NDVI sur couvert dense.",
    "Score_clarte": "Score Cloud Score+ moyen des pixels utilisés (1 = parfaitement dégagé).",
    "Pixels_total": "Pixels de la parcelle après buffer.",
    "Pixels_clairs": "Pixels non masqués (nuages, ombres…).",
    "Outliers_exclus": "Pixels clairs exclus car aberrants (hors Q1 − k·IQR / Q3 + k·IQR).",
    "Buffer_m": "Buffer intérieur réellement appliqué (m), réduit sur les petites parcelles.",
    "Satellite": "Satellite(s) Sentinel-2 de l'acquisition.",
}


WIDE_COLS = {"Chronologie", "Motif"}


def column_config(df):
    return {c: st.column_config.Column(help=h, width="large" if c in WIDE_COLS else None)
            for c, h in COLUMN_HELP.items() if c in df.columns}


def to_excel(sheets, extra=None):
    """sheets : dict nom d'onglet -> DataFrame. Ajoute un onglet Lexique.
    extra : fonction optionnelle appelée sur le classeur (ex. ajout de graphiques)."""
    import io
    lexique = pd.DataFrame(
        [{"Colonne": c, "Définition": h} for c, h in COLUMN_HELP.items()
         if any(c in df.columns for df in sheets.values())])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in {**sheets, "Lexique": lexique}.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for i, col in enumerate(df.columns, start=1):
                width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)])
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width + 2, 90)
            ws.freeze_panes = "B2"
        if extra is not None:
            extra(xw.book)
            if "Graphiques" in xw.book.sheetnames:
                xw.book.move_sheet("Graphiques", offset=1 - xw.book.sheetnames.index("Graphiques"))
    return buf.getvalue()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _build_map(items, legend_extra=None, title=None):
    """Carte folium des parcelles colorées par phase (titre optionnel, pour l'export HTML)."""
    m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=14, tiles=None)
    # Fonds Esri : utilisables aussi depuis un fichier HTML ouvert en local (les serveurs
    # OpenStreetMap refusent ces requêtes : « Access blocked »). Satellite affiché par défaut.
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Street Map", name="Fond plan", show=False).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery", name="Fond satellite", show=True).add_to(m)
    labels = folium.FeatureGroup(name="Identifiants des parcelles", show=True)
    for feat, it in zip(features, items):
        folium.GeoJson(
            feat["geometry"].__geo_interface__,
            name=str(it["id"]),
            style_function=lambda x, c=it["color"], o=it.get("opacity", 0.6): {
                "fillColor": c, "color": "black", "weight": 1, "fillOpacity": o},
            tooltip=folium.Tooltip(it["tooltip"]),
        ).add_to(m)
        pt = feat["geometry"].representative_point()
        folium.Marker(
            [pt.y, pt.x],
            icon=folium.DivIcon(icon_size=(0, 0), html=(
                '<div style="transform:translate(-50%,-50%);white-space:nowrap;'
                'font:600 11px sans-serif;color:#fff;pointer-events:none;'
                'text-shadow:0 0 3px #000,0 0 2px #000">' + str(it["id"]) + "</div>")),
        ).add_to(labels)
    labels.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)

    entries = [(lab, col) for lab, col in COLOR_MAP.items()] + (legend_extra or [])
    rows_html = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0">'
        f'<span style="width:14px;height:14px;background:{col};opacity:.75;'
        f'border:1px solid #333;display:inline-block"></span>{lab}</div>'
        for lab, col in entries)
    box = ('position:absolute;z-index:1000;background:rgba(255,255,255,.92);color:#222;'
           'padding:8px 10px;border-radius:6px;font:12px/1.3 sans-serif;'
           'box-shadow:0 1px 4px rgba(0,0,0,.3)')
    m.get_root().html.add_child(folium.Element(
        f'<div style="{box};bottom:24px;left:12px">{rows_html}</div>'))
    if title:
        m.get_root().header.add_child(folium.Element(f"<title>{title}</title>"))
        m.get_root().html.add_child(folium.Element(
            f'<div style="{box};top:12px;left:50%;transform:translateX(-50%);'
            f'font-size:14px;font-weight:600">{title}</div>'))
    return m


def phase_map(items, key, legend_extra=None, height=520, export=None):
    """
    Carte des parcelles colorées par phase, avec téléchargements optionnels.
    items  : une entrée par parcelle (dans l'ordre de `features`) :
             {"id", "color", "tooltip" (HTML), "opacity", "props" (attributs export)}
    export : {"title", "file"} → boutons KML (Google My Maps / Earth) et HTML.
    Chaque parcelle est une couche nommée par son identifiant (liste des couches).
    """
    m = _build_map(items, legend_extra)
    st_folium(m, height=height, use_container_width=True, key=key, returned_objects=[])
    if not export:
        return
    legend_txt = " ; ".join(f"{lab} : {col}" for lab, col in
                            list(COLOR_MAP.items()) + (legend_extra or []))
    kml = build_kml(export["title"], [
        {"geometry": feat["geometry"], "name": it["id"], "color": it["color"],
         "opacity": it.get("opacity", 0.6), "props": it.get("props", {"ID": it["id"]})}
        for feat, it in zip(features, items)], legend_text=legend_txt)
    html = _build_map(items, legend_extra, title=export["title"]).get_root().render()
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        st.download_button("🗺️ Carte KML", data=kml, help="Pour Google My Maps ou Google Earth",
                           file_name=f"{export['file']}.kml",
                           mime="application/vnd.google-earth.kml+xml", key=f"{key}_kml")
    with c2:
        st.download_button("🌐 Carte HTML", data=html.encode("utf-8"), help="S'ouvre dans un navigateur",
                           file_name=f"{export['file']}.html", mime="text/html",
                           key=f"{key}_html")
    with c3:
        st.caption("KML : mymaps.google.com → Créer une carte → Importer. Si les couleurs ne "
                   "sont pas reprises : Style → « Styles par colonne de données » → Phase. "
                   "HTML : s'ouvre dans un navigateur (connexion internet requise pour le fond Esri).")


def satellite_view(pid, date_str, key):
    """Vignette couleurs naturelles d'une parcelle à une date (bouton puis affichage)."""
    c1, c2 = st.columns([1, 2])
    with c1:
        show_mask = st.checkbox("Pixels masqués en magenta", value=True, key=f"{key}_mask",
                                help="Nuages, ombres, cirrus et marge autour des nuages, "
                                     "selon les réglages du masque.")
        if st.button("Afficher l'image", key=f"{key}_btn"):
            st.session_state[key] = (pid, date_str, show_mask, params_t, geoms_key)
    if st.session_state.get(key) != (pid, date_str, show_mask, params_t, geoms_key):
        return
    idx = ids.index(pid)
    with c2:
        with st.spinner("Génération de l'image…"):
            try:
                url = parcel_thumbnail(date_str, f"{geoms_key}|{idx}", params_t,
                                       outline_geojson(features[idx]["geometry"]),
                                       analysis_geojsons[idx], show_mask)
            except Exception as e:
                st.error(f"Erreur Earth Engine (image) : {type(e).__name__} — {e}")
                return
        st.image(url, width=480)
        st.caption(f"Sentinel-2 du {pd.Timestamp(date_str):%d/%m/%Y}, couleurs naturelles · "
                   "contour blanc : parcelle · contour jaune : zone analysée (après buffer)"
                   + (" · magenta : pixels masqués" if show_mask else "")
                   + ". Lien temporaire (quelques heures).")


# ============================================================
# ONGLETS
# ============================================================
tab1, tab2 = st.tabs(["📅 Analyse à une date", "📈 Analyse temporelle"])

# ╔══════════════════════════════════════════════════════════╗
# ║                 ONGLET 1 — UNE DATE                      ║
# ╚══════════════════════════════════════════════════════════╝
with tab1:
    st.header("Analyse NDVI — une date")

    target = None  # dict de list_dates

    months = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
              "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
    today = datetime.date.today()
    c1, c2 = st.columns(2)
    with c1:
        year = st.selectbox("Année", list(range(today.year, 2016, -1)), key="os_year")
    with c2:
        month = st.selectbox("Mois", range(1, 13), index=today.month - 1, key="os_month",
                             format_func=lambda m: months[m - 1])
    start = datetime.date(year, month, 1)
    end = min((datetime.date(year + 1, 1, 1) if month == 12
               else datetime.date(year, month + 1, 1)) - datetime.timedelta(days=1), today)

    if start > today:
        st.info("Ce mois n'a pas encore commencé.")
    elif st.button("Rechercher les dates disponibles", key="os_btn_search"):
        st.session_state.os_query = (start, end)

    # La liste suit les réglages du masque : recalculée (une requête) s'ils changent.
    if st.session_state.get("os_query") == (start, end):
        try:
            dates = list_dates(str(start), str(end), file_hash, params_t, region)
        except Exception as e:
            dates = None
            st.error(f"Erreur Earth Engine (recherche des dates) : {type(e).__name__} — {e}")

        if dates is not None and not dates:
            st.error("Aucune image Sentinel-2 sur cette période.")
        elif dates:
            n_ok = sum(1 for d in dates if usable(d, min_clear))
            n_out = sum(1 for d in dates if not covers(d))
            st.caption(
                f"{len(dates)} date(s) en {months[month - 1].lower()} {year} : "
                f"{n_ok} ✅ à {min_clear} % de ciel clair ou plus, "
                f"{len(dates) - n_ok - n_out} ⚠️ en dessous, {n_out} ⛔ hors emprise des parcelles. "
                f"Le % de ciel clair porte sur la partie des parcelles couverte par l'image : "
                f"une date ⚠️ peut rester exploitable pour certaines (voir leur statut après analyse)."
            )
            default = next((i for i, d in enumerate(dates) if usable(d, min_clear)),
                           next((i for i, d in enumerate(dates) if covers(d)), 0))
            choice = st.selectbox("Date à analyser", dates, index=default,
                                  format_func=date_label, key=f"os_sel_{start}")
            if st.button("Analyser cette date", key="os_btn_load"):
                target = choice

    if target is not None:
        with st.spinner(f"Calcul des statistiques du {target['date']:%d/%m/%Y}…"):
            try:
                st.session_state.os_raw = (str(target["date"]), run_analysis(str(target["date"])))
                st.session_state.os_ctx = calc_context()
                st.session_state.os_geoinfo = geoinfo
            except Exception as e:
                st.error(f"Erreur Earth Engine (statistiques) : {type(e).__name__} — {e}")

    # ── Affichage ────────────────────────────────────────────
    if st.session_state.get("os_raw"):
        date_str, raw = st.session_state.os_raw
        stale_warning(st.session_state.os_ctx)
        rows = build_rows(ids, st.session_state.os_geoinfo, raw, date_str,
                          min_pixels, min_clear, low, high)
        df_all = pd.DataFrame(rows)
        df_os = pick(df_all, OS_COLS)

        n_ok = int((df_os["Statut"] == STATUS_OK).sum())
        st.success(f"Résultats du {date_str} — {n_ok}/{len(df_os)} parcelles exploitables")
        st.dataframe(df_os, hide_index=True, column_config=column_config(df_os))
        st.caption("Survole un en-tête de colonne pour sa définition.")
        st.download_button(
            "⬇️ Exporter (Excel)",
            data=to_excel({"Résultats": df_os, "Technique": pick(df_all, TECH_COLS)}),
            file_name=f"ndvi_{date_str}.xlsx", mime=XLSX_MIME, key="os_dl")

        items = []
        for _, row in df_os.iterrows():
            ok = row["Statut"] == STATUS_OK
            items.append({
                "id": row["ID"],
                "color": colorize(row["Phase"]) if ok else COLOR_INVALID,
                "props": {"ID": row["ID"], "Date": pd.Timestamp(date_str).strftime("%d/%m/%Y"),
                          "Phase": row["Phase"], "NDVI": fmt(row["NDVI"]).replace(".", ","),
                          "NDTI": fmt(row.get("NDTI")).replace(".", ","),
                          "Fiabilité": row.get("Fiabilite", "—"),
                          "Pixels clairs (%)": fmt(row.get("Clair_pct"), 0)},
                "tooltip": (f"<b>{row['ID']}</b><br>{row['Phase']}<br>"
                            f"NDVI : {fmt(row['NDVI'])}"
                            + (f" · NDTI : {fmt(row['NDTI'])}" if pd.notna(row.get("NDTI")) else "")
                            + "<br>"
                            f"Fiabilité : {row.get('Fiabilite', '—')} "
                            f"(poids {fmt(row.get('Poids'), 2)})<br>"
                            f"Pixels utilisés : {row.get('Pixels_utilises', '—')} · "
                            f"clairs : {fmt(row.get('Clair_pct'), 0, ' %')}"),
            })
        phase_map(items, key="os_map", legend_extra=[("Non exploitable", COLOR_INVALID)],
                  export={"title": f"NDVI au {pd.Timestamp(date_str):%d/%m/%Y}",
                          "file": f"ndvi_{date_str}"})

        with st.expander("🛰️ Image satellite d'une parcelle à cette date"):
            pid_os = st.selectbox("Parcelle", list(df_os["ID"]), key="os_thumb_pid")
            satellite_view(pid_os, date_str, key="os_thumb")


# ╔══════════════════════════════════════════════════════════╗
# ║                ONGLET 2 — ANALYSE TEMPORELLE             ║
# ╚══════════════════════════════════════════════════════════╝
with tab2:
    st.header("Analyse NDVI — série temporelle")

    st.subheader("1. Période")
    today = datetime.date.today()

    def month_picker(label, key, default):
        c1, c2 = st.columns(2)
        st.session_state.setdefault(f"{key}_y", default.year)
        st.session_state.setdefault(f"{key}_m", default.month)
        with c1:
            y = st.selectbox(f"{label} — année", list(range(today.year, 2016, -1)), key=f"{key}_y")
        with c2:
            m = st.selectbox(f"{label} — mois", list(range(1, 13)), key=f"{key}_m",
                             format_func=lambda v: months[v - 1])
        return y, m

    dflt_start = (today.replace(day=1) - datetime.timedelta(days=62)).replace(day=1)
    ys, ms = month_picker("Début", "mt_start", dflt_start)
    ye, me = month_picker("Fin", "mt_end", today)
    period_start = datetime.date(ys, ms, 1)
    period_end = min((datetime.date(ye + 1, 1, 1) if me == 12 else datetime.date(ye, me + 1, 1))
                     - datetime.timedelta(days=1), today)

    if period_start > period_end:
        st.error("Le mois de début doit précéder le mois de fin.")
        st.stop()
    st.caption(f"Période analysée : du {period_start:%d/%m/%Y} au {period_end:%d/%m/%Y} "
               f"({(period_end - period_start).days + 1} jours).")

    if st.button("🔍 Rechercher les dates disponibles", key="mt_btn_search"):
        st.session_state.mt_query = (period_start, period_end)

    dates = None
    if st.session_state.get("mt_query") == (period_start, period_end):
        try:
            dates = list_dates(str(period_start), str(period_end), file_hash, params_t, region)
        except Exception as e:
            st.error(f"Erreur Earth Engine (recherche des dates) : {type(e).__name__} — {e}")

    if dates is not None:
        if not dates:
            st.info("Aucune image Sentinel-2 sur cette période.")
        else:
            st.subheader("2. Dates à analyser")
            presel = st.slider(
                "Présélection : ciel clair minimum sur l'ensemble des parcelles (%)",
                0, 100, 20, 10, key="mt_presel",
                help="Une date partiellement nuageuse peut rester exploitable pour une partie "
                     "des parcelles : le contrôle se fait ensuite parcelle par parcelle, "
                     "et les mesures douteuses sont écartées de la courbe.",
            )
            default = [d["date"] for d in dates if usable(d, presel)]
            by_date = {d["date"]: d for d in dates}
            sel = st.multiselect(
                f"{len(dates)} date(s) trouvée(s), {len(default)} présélectionnée(s)",
                options=[d["date"] for d in dates], default=default,
                format_func=lambda x: date_label(by_date[x]),
                key=f"mt_multisel_{presel}_{period_start}_{period_end}",
            )

            if sel:
                st.caption(f"{len(sel)} date(s) × {len(features)} parcelles — une requête "
                           f"Earth Engine par date, environ 2 à 5 s chacune (instantané si déjà calculée).")
                if st.button("▶️ Lancer l'analyse temporelle", key="mt_btn_run"):
                    raws, errors = [], []
                    bar = st.progress(0.0, text="Initialisation…")
                    for i, d in enumerate(sorted(sel)):
                        bar.progress(i / len(sel), text=f"{d:%d/%m/%Y} ({i + 1}/{len(sel)})…")
                        try:
                            raws.append((str(d), run_analysis(str(d))))
                        except Exception as e:
                            errors.append(f"{d:%d/%m/%Y} : {type(e).__name__} — {e}")
                    bar.empty()
                    st.session_state.mt_raw = raws
                    st.session_state.mt_period = (period_start, period_end)
                    st.session_state.mt_ctx = calc_context()
                    st.session_state.mt_geoinfo = geoinfo
                    st.session_state.mt_errors = errors
            else:
                st.info("Sélectionne au moins une date.")

    # ── Affichage ────────────────────────────────────────────
    if st.session_state.get("mt_raw"):
        stale_warning(st.session_state.mt_ctx)
        for err in st.session_state.get("mt_errors", []):
            st.warning(f"Date non traitée — {err}")
        p_start, p_end = st.session_state.mt_period

        rows = []
        for date_str, raw in st.session_state.mt_raw:
            rows += build_rows(ids, st.session_state.mt_geoinfo, raw, date_str,
                               min_pixels, min_clear, low, high)
        df_long = pd.DataFrame(rows)
        synthese, detail, courbes = analyse_all(df_long, p_start, p_end, low, high, ts_settings)

        n_kept = int(synthese["Mesures_retenues"].sum())
        st.success(f"Du {p_start:%d/%m/%Y} au {p_end:%d/%m/%Y} — {df_long['Date'].nunique()} date(s) "
                   f"× {len(synthese)} parcelles — {n_kept} mesures retenues pour les courbes")

        st.subheader("Synthèse par parcelle")
        synth_view = pick(synthese, SYNTH_COLS)
        NO_PHASE = "Aucune courbe"
        phase_vals = synth_view["Phase_fin"].fillna(NO_PHASE)
        c1, c2 = st.columns(2)
        with c1:
            ph_opts = [p for p in list(COLOR_MAP) + [NO_PHASE] if p in set(phase_vals)]
            f_phase = st.multiselect("Filtrer : phase en fin de période", ph_opts, default=ph_opts,
                                     key="mt_f_phase")
        with c2:
            cf_opts = [c for c in ["Bonne", "Moyenne", "Faible", "Aucune mesure"]
                       if c in set(synth_view["Confiance"])]
            f_conf = st.multiselect("Filtrer : confiance", cf_opts, default=cf_opts, key="mt_f_conf")
        shown = synth_view[phase_vals.isin(f_phase) & synth_view["Confiance"].isin(f_conf)]
        shown = shown.reset_index(drop=True)
        st.caption(f"{len(shown)} parcelle(s) affichée(s) sur {len(synth_view)}. Clique sur une "
                   "ligne pour afficher le graphique de la parcelle. Survole un en-tête de "
                   "colonne pour sa définition. L'export Excel contient toutes les parcelles.")
        table_key = "mt_table_" + hashlib.md5(
            "|".join(sorted(f_phase) + ["#"] + sorted(f_conf)).encode()).hexdigest()[:8]
        event = st.dataframe(shown, hide_index=True, column_config=column_config(shown),
                             on_select="rerun", selection_mode="single-row", key=table_key)

        if shown.empty:
            st.info("Aucune parcelle ne correspond aux filtres : la liste ci-dessous les propose toutes.")

        # Parcelle affichée : clic dans le tableau, ou liste déroulante
        pid_list = list(shown["ID"]) or list(synthese["ID"])
        rows_sel = list(event.selection.rows) if event is not None else []
        if (rows_sel, table_key) != st.session_state.get("mt_last_rows"):
            st.session_state.mt_last_rows = (rows_sel, table_key)
            if rows_sel:
                if rows_sel[0] < len(pid_list):
                    st.session_state.mt_pid = pid_list[rows_sel[0]]
        if st.session_state.get("mt_pid") not in pid_list:
            st.session_state.mt_pid = pid_list[0]

        st.subheader("Évolution du NDVI")
        c1, c2 = st.columns([3, 1])
        with c1:
            pid = st.selectbox("Parcelle", pid_list, key="mt_pid")
        with c2:
            show_excl = st.checkbox("Afficher les mesures écartées", value=True, key="mt_show_excl")

        s_row = synthese[synthese["ID"] == pid].iloc[0]
        st.markdown(
            f"**{pid}** — fin de période : "
            f"**{'—' if pd.isna(s_row['Phase_fin']) else s_row['Phase_fin']}** "
            f"(NDVI lissé {fmt(s_row['NDVI_fin']).replace('.', ',')}) · confiance {s_row['Confiance']} · "
            f"plus long trou sans mesure : {s_row['Plus_long_trou_j']} j")
        st.caption(f"Chronologie : {s_row['Chronologie']}"
                   + (f" · Baisse(s) rapide(s) vers le {s_row['Baisses_rapides']}"
                      if s_row["Baisses_rapides"] != "—" else ""))

        d_pid = detail[detail["ID"] == pid]
        c_pid = courbes[courbes["ID"] == pid]
        st.altair_chart(parcel_chart(d_pid, c_pid, p_start, p_end, low, high, show_excl),
                        width="stretch")
        nd_chart = ndti_chart(d_pid, p_start, p_end, low)
        if nd_chart is not None:
            st.markdown("**NDTI — résidus de culture (expérimental)**")
            st.altair_chart(nd_chart, width="stretch")
        st.caption("● mesure retenue · ○ mesure écartée (survol : motif) · trait plein : courbe "
                   "lissée · pointillé : interpolation à plus de 15 jours de toute mesure · "
                   "lignes tiretées : seuils. Panneau NDTI (expérimental) : points pleins quand "
                   f"la parcelle est peu verte (NDVI < {_t(low)}), seuls interprétables ; "
                   "cercle orange : sol humide probable (NDTI peu fiable).")

        with st.expander(f"🛰️ Image satellite de la parcelle {pid}"):
            d_img = d_pid[~d_pid["Statut"].isin(["Hors image", "Géométrie inexploitable"])]
            if d_img.empty:
                st.info("Aucune image ne couvre cette parcelle sur la période.")
            else:
                opts = list(d_img.itertuples(index=False))

                def _lab(r):
                    v = r.NDVI if r.Retenue == "Oui" else getattr(r, "NDVI_brut", None)
                    txt = f"{r.Date:%d/%m/%Y} — NDVI {fmt(v).replace('.', ',')}"
                    return txt + (" · retenue" if r.Retenue == "Oui" else f" · écartée ({r.Motif})")

                kept_idx = [i for i, r in enumerate(opts) if r.Retenue == "Oui"]
                r_sel = st.selectbox("Date", opts, index=kept_idx[-1] if kept_idx else len(opts) - 1,
                                     format_func=_lab, key=f"mt_thumb_date_{pid}")
                satellite_view(pid, r_sel.Date.strftime("%Y-%m-%d"), key="mt_thumb")

        with st.expander(f"Mesures de la parcelle {pid}"):
            d_view = pick(d_pid.assign(Date=d_pid["Date"].dt.date), DETAIL_COLS)
            st.dataframe(d_view, hide_index=True, column_config=column_config(d_view))

        # ── Carte des phases à une date ──────────────────────
        st.subheader("Carte des phases à une date")
        if courbes.empty:
            st.info("Aucune courbe disponible : pas de mesure retenue sur la période.")
        else:
            last_curve = min(max(courbes["Date"].max().date(), p_start), p_end)
            meas = (detail.assign(ok=detail["Retenue"].eq("Oui"))
                    .groupby("Date")["ok"].agg(["sum", "count"]).reset_index()
                    .rename(columns={"sum": "Retenues", "count": "Total"}))
            meas_dates = sorted(d.date() for d in meas["Date"])

            # Date affichée : clic sur la frise, boutons, ou curseur
            if not (p_start <= st.session_state.get("mt_map_date", last_curve) <= p_end):
                st.session_state.mt_map_date = last_curve
            st.session_state.setdefault("mt_map_date", last_curve)
            tl_state = st.session_state.get("mt_timeline")
            picked = None
            try:
                pts_sel = (tl_state or {}).get("selection", {}).get("pick") or []
                if pts_sel and meas_dates:
                    v = pts_sel[0].get("Date")
                    t = pd.to_datetime(v, unit="ms") if isinstance(v, (int, float)) else pd.to_datetime(v)
                    # date mesurée la plus proche (le clic peut être décalé par le fuseau horaire)
                    picked = min(meas_dates, key=lambda d: abs((pd.Timestamp(d) - t).total_seconds()))
            except (TypeError, ValueError, AttributeError):
                picked = None
            if picked != st.session_state.get("mt_tl_last"):
                st.session_state.mt_tl_last = picked
                if picked is not None:
                    st.session_state.mt_map_date = picked

            def _jump(step):
                cur = st.session_state.mt_map_date
                cands = [d for d in meas_dates if (d > cur if step > 0 else d < cur)]
                if cands:
                    st.session_state.mt_map_date = cands[0] if step > 0 else cands[-1]

            st.caption("Points : dates des images analysées (plus le point est foncé, plus il y a de "
                       "parcelles avec une mesure retenue). Clique sur un point pour afficher la carte "
                       "à cette date ; le trait rouge indique la date affichée.")
            st.altair_chart(dates_timeline(meas, p_start, p_end, st.session_state.mt_map_date),
                            width="stretch", on_select="rerun", selection_mode="pick",
                            key="mt_timeline")
            b1, b2, _sp = st.columns([1, 1, 4])
            with b1:
                st.button("◀ Précédente", key="mt_prev", on_click=_jump, args=(-1,),
                          help="Date d'image analysée précédente",
                          disabled=not any(d < st.session_state.mt_map_date for d in meas_dates))
            with b2:
                st.button("Suivante ▶", key="mt_next", on_click=_jump, args=(1,),
                          help="Date d'image analysée suivante",
                          disabled=not any(d > st.session_state.mt_map_date for d in meas_dates))
            map_date = st.slider("Date affichée (tout jour de la période)", min_value=p_start,
                                 max_value=p_end, format="DD/MM/YYYY", key="mt_map_date")
            if map_date in meas_dates:
                st.caption(f"📍 {map_date:%d/%m/%Y} : date d'image analysée.")
            day = courbes[courbes["Date"] == pd.Timestamp(map_date)].set_index("ID")
            items, n_unc, n_none = [], 0, 0
            for _, srow in synthese.iterrows():
                pid_m = srow["ID"]
                if pid_m in day.index:
                    r = day.loc[pid_m]
                    unc = bool(r["Incertain"])
                    n_unc += unc
                    items.append({
                        "id": pid_m, "color": colorize(r["Phase"]), "opacity": 0.35 if unc else 0.65,
                        "props": {"ID": pid_m, "Date": f"{map_date:%d/%m/%Y}", "Phase": r["Phase"],
                                  "NDVI lissé": fmt(r["NDVI_lisse"]).replace(".", ","),
                                  "Interpolé": "Oui" if unc else "Non",
                                  "Confiance": srow["Confiance"]},
                        "tooltip": (f"<b>{pid_m}</b><br>{r['Phase']}<br>"
                                    f"NDVI lissé : {fmt(r['NDVI_lisse'])}"
                                    + ("<br><i>Interpolé : aucune mesure à moins de 15 jours</i>"
                                       if unc else "")),
                    })
                else:
                    n_none += 1
                    items.append({
                        "id": pid_m, "color": COLOR_INVALID, "opacity": 0.5,
                        "props": {"ID": pid_m, "Date": f"{map_date:%d/%m/%Y}",
                                  "Phase": "Pas de courbe à cette date", "NDVI lissé": "—",
                                  "Interpolé": "—", "Confiance": srow["Confiance"]},
                        "tooltip": (f"<b>{pid_m}</b><br>Pas de courbe à cette date<br>"
                                    "(avant la 1re ou après la dernière mesure retenue)"),
                    })
            st.caption(
                f"Phase de la courbe lissée de chaque parcelle au {map_date:%d/%m/%Y}. "
                f"Teinte atténuée : valeur interpolée loin de toute mesure ({n_unc} parcelle(s)). "
                f"Gris : pas de courbe à cette date ({n_none} parcelle(s)).")
            phase_map(items, key="mt_map", legend_extra=[("Pas de courbe à cette date", COLOR_INVALID)],
                      export={"title": f"Phases NDVI au {map_date:%d/%m/%Y}",
                              "file": f"phases_ndvi_{map_date}"})

        pivot = (detail[detail["Retenue"] == "Oui"]
                 .assign(Date=lambda x: x["Date"].dt.strftime("%Y-%m-%d"))
                 .pivot_table(index="ID", columns="Date", values="NDVI", aggfunc="first")
                 .reindex(pid_list).reset_index())
        pivot.columns.name = None
        with st.expander("NDVI par parcelle et par date (mesures retenues)"):
            st.dataframe(pivot, hide_index=True)

        detail_view = pick(detail.assign(Date=detail["Date"].dt.date), DETAIL_COLS)
        courbes_view = courbes.assign(Date=courbes["Date"].dt.date)[
            ["ID", "Date", "NDVI_lisse", "Phase", "Incertain"]]
        tech = pick(df_long, TECH_COLS)
        xlsx = to_excel(
            {"Synthèse": synth_view, "NDVI par date": pivot, "Détail": detail_view,
             "Courbes lissées": courbes_view, "Technique": tech},
            extra=lambda book: add_parcel_charts(book, synthese, detail, courbes,
                                                 p_start, p_end, low, high))
        st.download_button(
            "⬇️ Exporter (Excel : synthèse, graphiques par parcelle, NDVI par date, détail, "
            "courbes lissées, technique)",
            data=xlsx, file_name=f"ndvi_temporel_{p_start}_{p_end}.xlsx", mime=XLSX_MIME,
            key="mt_dl")
        if len(synthese) > MAX_CHARTS:
            st.caption(f"Graphiques Excel limités aux {MAX_CHARTS} premières parcelles.")

        # ── Enregistrement de l'analyse (réouverture sans recalcul) ──
        ctx = st.session_state.mt_ctx
        calc_params = dict(ctx["params_t"])
        session_zip = build_session_zip(
            meta={
                "app_version": APP_VERSION, "source_file": source_name, "file_hash": file_hash,
                "id_field": id_field if fields else None,
                "geoms_key": ctx["geoms_key"], "params": calc_params,
                "settings": {
                    "thresholds": [low, high],
                    "buffer_m": int(ctx["geoms_key"].rsplit("|", 1)[1]),
                    "cs_threshold": calc_params["cs_threshold"],
                    "cloud_buffer_m": calc_params["cloud_buffer_m"],
                    "iqr_k": calc_params["iqr_k"],
                    "min_clear": int(min_clear), "min_pixels": int(min_pixels),
                    "min_weight": float(min_weight), "smooth_days": int(smooth_days),
                },
                "period": [p_start, p_end],
                "errors": st.session_state.get("mt_errors", []),
            },
            features=features, ids=ids, geoinfo=st.session_state.mt_geoinfo,
            raws=st.session_state.mt_raw)
        st.download_button(
            "💾 Enregistrer l'analyse (.zip, à rouvrir plus tard sans recalcul)",
            data=session_zip, file_name=f"analyse_ndvi_{p_start}_{p_end}.zip",
            mime="application/zip", key="mt_save",
            help="Contient les réglages, les résultats de chaque date et les contours des "
                 "parcelles. Pour la rouvrir : « Ouvrir une analyse enregistrée » en haut de page. "
                 "Pour la supprimer : supprime simplement le fichier.")
