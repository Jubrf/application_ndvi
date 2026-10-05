import datetime
import hashlib

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from utils.gee_ndvi import DEFAULT_PARAMS, compute_day_stats, init_gee, list_dates
from utils.geometry import looks_like_wgs84, prepare_all, region_geojson
from utils.ndvi_processing import (
    INDICATORS,
    STATUS_OK,
    build_rows,
    colorize,
    temporal_summary,
    unique_ids,
)
from utils.vector_io import load_vector

st.set_page_config(page_title="NDVI parcellaire", page_icon="🌱", layout="wide")
st.title("🌱 NDVI – Analyse parcellaire Sentinel-2")
print("[ndvi] script démarré", flush=True)

# ============================================================
# INIT GEE
# ============================================================
try:
    init_gee(st.secrets["GEE_SERVICE_ACCOUNT"], st.secrets["GEE_PRIVATE_KEY"])
except Exception as e:
    st.error(f"Connexion à Earth Engine impossible : {type(e).__name__} — {e}")
    st.stop()
print("[ndvi] Earth Engine initialisé", flush=True)

# ============================================================
# PARAMÈTRES (barre latérale)
# ============================================================
with st.sidebar:
    st.header("Paramètres d'analyse")
    indicator_label = st.radio(
        "Indicateur utilisé pour l'interprétation",
        list(INDICATORS),
        index=0,
        help="Médiane : robuste aux pixels atypiques restants. "
             "Moyenne pondérée : donne plus de poids aux pixels au meilleur score de clarté.",
    )
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

indicator_col = INDICATORS[indicator_label]
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


def date_label(d):
    clear = f"{d['clear_pct']:.0f} % clair" if d["clear_pct"] is not None else "clair ?"
    return f"{d['date']:%d/%m/%Y} — {clear}"


def show_gee_error(e, context):
    st.error(f"Erreur Earth Engine ({context}) : {type(e).__name__} — {e}")


def safe_list_dates(start, end):
    """Liste des dates ; affiche le message GEE complet en cas d'erreur et renvoie None."""
    try:
        return list_dates(str(start), str(end), file_hash, params_t, region)
    except Exception as e:
        show_gee_error(e, "recherche des dates")
        return None


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
                "L'indicateur et les seuils de qualité s'appliquent sans relancer.")


DISPLAY_COLS = ["ID", "NDVI", "Interpretation", "Couvert", "Statut",
                "NDVI_median", "NDVI_pondere", "NDVI_moyen", "NDVI_ecart_type",
                "EVI2_median", "Pixels_utilises", "Outliers_exclus", "Clair_pct",
                "Surface_ha", "Buffer_m", "Satellite", "Date"]


INT_COLS = ["Pixels_utilises", "Outliers_exclus", "Buffer_m"]


def ordered(df):
    df = df[[c for c in DISPLAY_COLS if c in df.columns]].copy()
    for c in INT_COLS:
        if c in df.columns:
            df[c] = df[c].round().astype("Int64")
    return df


def to_csv(df):
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


# ============================================================
# ONGLETS
# ============================================================
tab1, tab2 = st.tabs(["📅 Analyse à une date", "📈 Analyse temporelle"])

# ╔══════════════════════════════════════════════════════════╗
# ║                 ONGLET 1 — UNE DATE                      ║
# ╚══════════════════════════════════════════════════════════╝
with tab1:
    st.header("Analyse NDVI — une date")

    mode = st.radio("Sélection de la date",
                    ["Dernière date exploitable", "Choisir dans un mois"],
                    key="os_mode", horizontal=True)

    target = None  # dict de list_dates

    if mode == "Dernière date exploitable":
        st.caption(f"Date la plus récente des 60 derniers jours avec au moins "
                   f"{min_clear} % de ciel clair sur les parcelles.")
        if st.button("Rechercher et analyser", key="os_btn_latest"):
            today = datetime.date.today()
            dates = safe_list_dates(today - datetime.timedelta(days=60), today)
            usable = [d for d in dates or [] if (d["clear_pct"] or 0) >= min_clear]
            if dates is None:
                pass
            elif usable:
                target = usable[0]
            elif dates:
                target = max(dates, key=lambda d: d["clear_pct"] or 0)
                st.warning(f"Aucune date à {min_clear} % de ciel clair ou plus : "
                           f"date la moins nuageuse retenue ({date_label(target)}).")
            else:
                st.error("Aucune image Sentinel-2 sur les 60 derniers jours.")
    else:
        months = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
                  "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
        c1, c2 = st.columns(2)
        with c1:
            year = st.selectbox("Année",
                                list(range(datetime.date.today().year, 2016, -1)),
                                key="os_year")
        with c2:
            month = st.selectbox("Mois", range(1, 13), key="os_month",
                                 format_func=lambda m: months[m - 1])
        start = datetime.date(year, month, 1)
        end = (datetime.date(year + 1, 1, 1) if month == 12
               else datetime.date(year, month + 1, 1)) - datetime.timedelta(days=1)

        if st.button("Rechercher les dates disponibles", key="os_btn_search"):
            st.session_state.os_dates = safe_list_dates(start, end)

        dates = st.session_state.get("os_dates")
        if dates is not None:
            if not dates:
                st.error("Aucune image Sentinel-2 sur cette période.")
            else:
                choice = st.selectbox(f"{len(dates)} date(s) disponible(s)", dates,
                                      format_func=date_label, key="os_sel_date")
                if st.button("Analyser cette date", key="os_btn_load"):
                    target = choice

    if target is not None:
        with st.spinner(f"Calcul des statistiques du {target['date']:%d/%m/%Y}…"):
            try:
                st.session_state.os_raw = (str(target["date"]), run_analysis(str(target["date"])))
                st.session_state.os_ctx = calc_context()
                st.session_state.os_geoinfo = geoinfo
            except Exception as e:
                show_gee_error(e, "statistiques zonales")

    # ── Affichage ────────────────────────────────────────────
    if st.session_state.get("os_raw"):
        date_str, raw = st.session_state.os_raw
        stale_warning(st.session_state.os_ctx)
        rows = build_rows(ids, st.session_state.os_geoinfo, raw, date_str,
                          indicator_col, min_pixels, min_clear)
        df_os = ordered(pd.DataFrame(rows))

        n_ok = int((df_os["Statut"] == STATUS_OK).sum())
        st.success(f"Résultats du {date_str} — {n_ok}/{len(df_os)} parcelles exploitables")
        st.dataframe(df_os, hide_index=True)
        st.download_button("⬇️ Exporter CSV", data=to_csv(df_os),
                           file_name=f"ndvi_{date_str}.csv", mime="text/csv",
                           key="os_dl")

        m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=14,
                       tiles=None)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri World Imagery", name="Satellite").add_to(m)
        folium.TileLayer("OpenStreetMap", name="Plan").add_to(m)
        for feat, (_, row) in zip(features, df_os.iterrows()):
            color = colorize(row["Interpretation"]) if row["Statut"] == STATUS_OK else colorize(None)
            tooltip = (
                f"<b>{row['ID']}</b><br>"
                f"{row['Interpretation']}<br>"
                f"NDVI ({indicator_label.lower()}) : {fmt(row['NDVI'])}<br>"
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
    c1, c2 = st.columns(2)
    with c1:
        date_start = st.date_input("Date de début", value=today - datetime.timedelta(days=60),
                                   max_value=today, key="mt_date_start", format="DD/MM/YYYY")
    with c2:
        date_end = st.date_input("Date de fin", value=today, max_value=today,
                                 key="mt_date_end", format="DD/MM/YYYY")

    if date_start >= date_end:
        st.error("La date de début doit être antérieure à la date de fin.")
        st.stop()

    if st.button("🔍 Rechercher les dates disponibles", key="mt_btn_search"):
        st.session_state.mt_dates = safe_list_dates(date_start, date_end)

    dates = st.session_state.get("mt_dates")
    if dates is not None:
        if not dates:
            st.info("Aucune image Sentinel-2 sur cette période.")
        else:
            st.subheader("2. Dates à analyser")
            presel = st.slider(
                "Présélection : ciel clair minimum sur l'ensemble des parcelles (%)",
                0, 100, 30, 10, key="mt_presel",
                help="Une date partiellement nuageuse peut rester exploitable pour une partie "
                     "des parcelles : le contrôle final se fait parcelle par parcelle.",
            )
            default = [d["date"] for d in dates if (d["clear_pct"] or 0) >= presel]
            by_date = {d["date"]: d for d in dates}
            sel = st.multiselect(
                f"{len(dates)} date(s) trouvée(s), {len(default)} présélectionnée(s)",
                options=[d["date"] for d in dates], default=default,
                format_func=lambda x: date_label(by_date[x]), key=f"mt_multisel_{presel}",
            )

            if sel:
                st.caption(f"{len(sel)} date(s) × {len(features)} parcelles — "
                           f"une requête Earth Engine par date.")
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

        rows = []
        for date_str, raw in st.session_state.mt_raw:
            rows += build_rows(ids, st.session_state.mt_geoinfo, raw, date_str,
                               indicator_col, min_pixels, min_clear)
        df_long, pivot = temporal_summary(pd.DataFrame(rows))

        n_dates = df_long["Date"].nunique()
        n_ok = int((df_long["Statut"] == STATUS_OK).sum())
        st.success(f"{n_dates} date(s) × {df_long['ID'].nunique()} parcelles — "
                   f"{n_ok} mesures exploitables sur {len(df_long)}")

        st.subheader(f"Synthèse — NDVI ({indicator_label.lower()}) par parcelle et par date")
        st.caption("Cases vides : mesure non exploitable (nuages, trop peu de pixels).")
        st.dataframe(pivot, hide_index=True)

        with st.expander("Détail complet (toutes les dates × parcelles)"):
            st.dataframe(ordered(df_long).assign(Delta_NDVI=df_long["Delta_NDVI"]),
                         hide_index=True)

        c1, c2 = st.columns(2)
        with c1:
            st.download_button("⬇️ Exporter la synthèse (CSV)", data=to_csv(pivot),
                               file_name=f"ndvi_synthese_{date_start}_{date_end}.csv",
                               mime="text/csv", key="mt_dl_pivot")
        with c2:
            st.download_button("⬇️ Exporter le détail (CSV)", data=to_csv(df_long),
                               file_name=f"ndvi_detail_{date_start}_{date_end}.csv",
                               mime="text/csv", key="mt_dl_long")
