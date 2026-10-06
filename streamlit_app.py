import datetime
import hashlib

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from utils.gee_ndvi import DEFAULT_PARAMS, compute_day_stats, init_gee, list_dates, log
from utils.geometry import looks_like_wgs84, prepare_all, region_geojson
from utils.charts import parcel_chart
from utils.ndvi_processing import (
    DEFAULT_THRESHOLDS,
    STATUS_OK,
    build_rows,
    colorize,
    unique_ids,
)
from utils.timeseries import DEFAULT_SETTINGS, analyse_all
from utils.vector_io import load_vector

# Version affichée dans la barre latérale : à changer à chaque modification,
# pour savoir quel code tourne réellement sur Streamlit Cloud.
APP_VERSION = "v2.0 — 06/10/2026"

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
# ============================================================
with st.sidebar:
    st.header("Paramètres d'analyse")
    low, high = st.slider(
        "Seuils d'interprétation (NDVI)", 0.0, 1.0, DEFAULT_THRESHOLDS, 0.01,
        help="Sous le 1er seuil : sol nu. Entre les deux : couvert peu développé. "
             "Au-dessus du 2nd : couvert bien développé. S'appliquent sans relancer l'analyse.",
    )
    if high - low < 0.05:
        st.warning("Les deux seuils sont très proches.")
    buffer_m = st.select_slider(
        "Buffer intérieur (m)", options=[0, 5, 10, 15, 20], value=10,
        help="Retire une bande en bordure de parcelle (haies, chemins, voisins). "
             "Réduit automatiquement pour les petites parcelles.",
    )
    with st.expander("Masque nuages et qualité"):
        cs_threshold = st.slider(
            "Seuil Cloud Score+", 0.40, 0.85, DEFAULT_PARAMS["cs_threshold"], 0.05,
            help="Pixels sous ce score rejetés (nuages, ombres, brume). "
                 "Plus haut = plus strict.",
        )
        cloud_buffer_m = st.select_slider(
            "Marge autour des nuages (m)", options=[0, 10, 20, 40, 60],
            value=DEFAULT_PARAMS["cloud_buffer_m"],
        )
        iqr_k = st.select_slider(
            "Exclusion des valeurs aberrantes (k × IQR)", options=[1.0, 1.5, 2.0, 3.0],
            value=DEFAULT_PARAMS["iqr_k"],
            help="Pixels hors [Q1 − k·IQR ; Q3 + k·IQR] exclus. Plus haut = moins d'exclusions.",
        )
        min_clear = st.slider("Part minimale de pixels clairs (%)", 0, 100, 50, 5)
        min_pixels = st.number_input("Nombre minimal de pixels utilisés", 1, 500, 10)
    with st.expander("Courbe temporelle"):
        min_weight = st.slider(
            "Poids de fiabilité minimal d'une mesure", 0.0, 1.0,
            DEFAULT_SETTINGS["min_weight"], 0.05,
            help="Les mesures sous ce poids sont écartées de la courbe (affichées en gris).")
        smooth_days = st.select_slider(
            "Lissage (jours)", options=[3, 4, 6, 8, 10, 15], value=DEFAULT_SETTINGS["smooth_days"],
            help="Plus la valeur est grande, plus la courbe est lisse, mais plus elle réagit "
                 "tard aux changements (levée, destruction).")

ts_settings = {"min_weight": min_weight, "smooth_days": smooth_days}
gee_params = {**DEFAULT_PARAMS, "cs_threshold": cs_threshold,
              "cloud_buffer_m": cloud_buffer_m, "iqr_k": iqr_k}
params_t = tuple(sorted(gee_params.items()))

# ============================================================
# CHARGEMENT DU FICHIER
# ============================================================
uploaded = st.file_uploader("📁 Charger un SHP (ZIP) ou un GeoJSON",
                            type=["zip", "geojson"])
if uploaded is None:
    st.stop()

file_hash = hashlib.md5(uploaded.getvalue()).hexdigest()
if st.session_state.get("loaded_file") != file_hash:
    for key in [k for k in st.session_state if k.startswith(("os_", "mt_"))]:
        del st.session_state[key]
    st.session_state["loaded_file"] = file_hash

features = load_vector(uploaded)
if not features:
    st.error("Aucune parcelle trouvée dans le fichier.")
    st.stop()
if not looks_like_wgs84(features):
    st.error("Coordonnées non reconnues : le fichier .prj est probablement absent "
             "du ZIP. Ajoute-le ou exporte la couche en WGS84 / Lambert-93 avec son .prj.")
    st.stop()

fields = list(features[0]["properties"].keys())
if fields:
    id_field = st.selectbox(
        "Champ identifiant des parcelles", fields,
        index=fields.index("NUM_ILOT") if "NUM_ILOT" in fields else 0,
    )
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


OS_COLS = ["ID", "NDVI", "Phase", "Couvert", "Fiabilite", "Poids", "Statut", "Clair_pct",
           "Pixels_utilises", "Surface_ha", "Date"]
TECH_COLS = ["ID", "Date", "Statut", "NDVI_brut", "NDVI_median", "NDVI_pondere", "NDVI_moyen",
             "NDVI_ecart_type", "EVI2_median", "Poids", "Score_clarte", "Clair_pct",
             "Pixels_total", "Pixels_clairs", "Pixels_utilises", "Outliers_exclus",
             "Surface_ha", "Buffer_m", "Satellite"]
SYNTH_COLS = ["ID", "Phase_fin", "NDVI_fin", "Confiance", "Chronologie", "Baisses_rapides",
              "Mesures_retenues", "Mesures_ecartees", "Plus_long_trou_j", "Derniere_mesure",
              "Jours_sans_mesure_fin", "Surface_ha"]
DETAIL_COLS = ["ID", "Date", "NDVI", "NDVI_lisse", "Phase", "Fiabilite", "Poids", "Retenue",
               "Motif", "Statut", "Clair_pct"]
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


def to_excel(sheets):
    """sheets : dict nom d'onglet -> DataFrame. Ajoute un onglet Lexique."""
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
    return buf.getvalue()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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

        m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=14,
                       tiles=None)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri World Imagery", name="Satellite").add_to(m)
        folium.TileLayer("OpenStreetMap", name="Plan").add_to(m)
        for feat, (_, row) in zip(features, df_os.iterrows()):
            color = colorize(row["Phase"]) if row["Statut"] == STATUS_OK else colorize(None)
            tooltip = (
                f"<b>{row['ID']}</b><br>"
                f"{row['Phase']}<br>"
                f"NDVI : {fmt(row['NDVI'])}<br>"
                f"Fiabilité : {row.get('Fiabilite', '—')} (poids {fmt(row.get('Poids'), 2)})<br>"
                f"Pixels utilisés : {row.get('Pixels_utilises', '—')} · "
                f"clairs : {fmt(row.get('Clair_pct'), 0, ' %')}"
            )
            folium.GeoJson(
                feat["geometry"].__geo_interface__,
                style_function=lambda x, c=color: {"fillColor": c, "color": "black",
                                                   "weight": 1, "fillOpacity": 0.6},
                tooltip=tooltip,
            ).add_to(m)
        folium.LayerControl().add_to(m)
        st_folium(m, height=520, use_container_width=True, key="os_map",
                  returned_objects=[])


# ╔══════════════════════════════════════════════════════════╗
# ║                ONGLET 2 — ANALYSE TEMPORELLE             ║
# ╚══════════════════════════════════════════════════════════╝
with tab2:
    st.header("Analyse NDVI — série temporelle")

    st.subheader("1. Période")
    today = datetime.date.today()

    def month_picker(label, key, default):
        c1, c2 = st.columns(2)
        with c1:
            y = st.selectbox(f"{label} — année", list(range(today.year, 2016, -1)),
                             index=today.year - default.year, key=f"{key}_y")
        with c2:
            m = st.selectbox(f"{label} — mois", range(1, 13), index=default.month - 1,
                             key=f"{key}_m", format_func=lambda v: months[v - 1])
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
        st.caption("Clique sur une ligne pour afficher le graphique de la parcelle. "
                   "Survole un en-tête de colonne pour sa définition.")
        synth_view = pick(synthese, SYNTH_COLS)
        event = st.dataframe(synth_view, hide_index=True, column_config=column_config(synth_view),
                             on_select="rerun", selection_mode="single-row", key="mt_table")

        # Parcelle affichée : clic dans le tableau, ou liste déroulante
        pid_list = list(synthese["ID"])
        rows_sel = list(event.selection.rows) if event is not None else []
        if rows_sel != st.session_state.get("mt_last_rows"):
            st.session_state.mt_last_rows = rows_sel
            if rows_sel:
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
        st.caption("● mesure retenue · ○ mesure écartée (survol : motif) · trait plein : courbe "
                   "lissée · pointillé : interpolation à plus de 15 jours de toute mesure · "
                   "lignes tiretées : seuils.")

        with st.expander(f"Mesures de la parcelle {pid}"):
            d_view = pick(d_pid.assign(Date=d_pid["Date"].dt.date), DETAIL_COLS)
            st.dataframe(d_view, hide_index=True, column_config=column_config(d_view))

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
        st.download_button(
            "⬇️ Exporter (Excel : synthèse, NDVI par date, détail, courbes lissées, technique)",
            data=to_excel({"Synthèse": synth_view, "NDVI par date": pivot, "Détail": detail_view,
                           "Courbes lissées": courbes_view, "Technique": tech}),
            file_name=f"ndvi_temporel_{p_start}_{p_end}.xlsx", mime=XLSX_MIME, key="mt_dl")
