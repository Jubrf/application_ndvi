import datetime
import hashlib

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from utils.gee_ndvi import DEFAULT_PARAMS, compute_day_stats, init_gee, list_dates, log
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

# Version affichée dans la barre latérale : à changer à chaque modification,
# pour savoir quel code tourne réellement sur Streamlit Cloud.
APP_VERSION = "v1.8 — 05/10/2026 15h15"

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
                "L'indicateur et les seuils de qualité s'appliquent sans relancer.")


DISPLAY_COLS = ["ID", "NDVI", "Poids", "Fiabilite", "Interpretation", "Couvert", "Statut",
                "NDVI_median", "NDVI_pondere", "NDVI_moyen", "NDVI_ecart_type",
                "EVI2_median", "Pixels_utilises", "Outliers_exclus", "Clair_pct", "Score_clarte",
                "Surface_ha", "Buffer_m", "Satellite", "Date"]


INT_COLS = ["Pixels_utilises", "Outliers_exclus", "Buffer_m"]


def ordered(df):
    df = df[[c for c in DISPLAY_COLS if c in df.columns]].copy()
    for c in INT_COLS:
        if c in df.columns:
            df[c] = df[c].round().astype("Int64")
    return df


COLUMN_HELP = {
    "ID": "Identifiant de la parcelle (champ choisi au chargement).",
    "NDVI": "Valeur retenue pour l'interprétation : l'indicateur choisi dans la barre "
            "latérale (médiane par défaut). Vide si la mesure n'est pas exploitable.",
    "Poids": "Poids de fiabilité de la mesure, de 0 à 1 (équivalent du « raw NDVI weight » "
             "de KERMAP, formule propre à l'appli) = clarté × score Cloud Score+ moyen × "
             "part de pixels non aberrants × facteur taille (plein à partir de 30 pixels).",
    "Fiabilite": "Bonne (poids ≥ 0,8), Moyenne (0,5–0,8), Faible (< 0,5). "
                 "Non exploitable si le statut n'est pas OK.",
    "Score_clarte": "Score Cloud Score+ moyen des pixels utilisés (1 = parfaitement dégagé). "
                    "Un score bas signale un voile ou une brume résiduelle.",
    "Interpretation": "Classe NDVI : < 0,20 sol nu ou non levé ; 0,20–0,25 levant ; "
                      "0,25–0,50 en développement ; ≥ 0,50 établi.",
    "Couvert": "Oui / Non / — (indéterminé ou mesure non exploitable).",
    "Statut": "OK : mesure exploitable. Nuageux : part de pixels clairs sous le seuil "
              "(50 % par défaut). Trop peu de pixels : moins de pixels utilisés que le "
              "minimum (10 par défaut). Hors image : parcelle hors de l'emprise. "
              "Géométrie inexploitable : contour vide ou invalide.",
    "NDVI_median": "Médiane du NDVI des pixels conservés (clairs, hors valeurs aberrantes).",
    "NDVI_pondere": "Moyenne du NDVI où chaque pixel compte selon son score Cloud Score+ "
                    "(probabilité d'être dégagé, de 0 à 1). Après masquage (score < 0,60 "
                    "exclu), les poids vont de 0,60 à 1 : un pixel légèrement voilé compte moins.",
    "NDVI_moyen": "Moyenne simple du NDVI des pixels conservés.",
    "NDVI_ecart_type": "Écart-type du NDVI : hétérogénéité de la parcelle.",
    "EVI2_median": "Médiane de l'EVI2, indice moins saturé que le NDVI sur couvert dense.",
    "Pixels_utilises": "Pixels de 10 m réellement utilisés : clairs et hors valeurs aberrantes.",
    "Outliers_exclus": "Pixels clairs exclus car aberrants (hors Q1 − 1,5·IQR / Q3 + 1,5·IQR).",
    "Clair_pct": "Part des pixels de la parcelle (après buffer) non masqués : ni nuage, "
                 "ni ombre, ni cirrus, ni neige, ni à moins de 20 m d'un nuage.",
    "Surface_ha": "Surface de la parcelle d'origine (ha).",
    "Buffer_m": "Buffer intérieur réellement appliqué (m), réduit sur les petites parcelles.",
    "Satellite": "Satellite(s) Sentinel-2 de l'acquisition.",
    "Date": "Date d'acquisition de l'image.",
    "Delta_NDVI": "Écart avec la mesure exploitable précédente de la même parcelle.",
    "Mesures_valides": "Nombre de dates exploitables pour la parcelle.",
    "Tendance": "Hausse (> +0,10), Baisse (< −0,05) ou Stable, entre la première et la "
                "dernière mesure exploitable.",
    "Delta_total": "Écart entre la première et la dernière mesure exploitable.",
}


def column_config(df):
    return {c: st.column_config.Column(help=h) for c, h in COLUMN_HELP.items() if c in df.columns}


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
                          indicator_col, min_pixels, min_clear)
        df_os = ordered(pd.DataFrame(rows))

        n_ok = int((df_os["Statut"] == STATUS_OK).sum())
        st.success(f"Résultats du {date_str} — {n_ok}/{len(df_os)} parcelles exploitables")
        st.dataframe(df_os, hide_index=True, column_config=column_config(df_os))
        st.caption("Survole un en-tête de colonne pour sa définition.")
        st.download_button("⬇️ Exporter (Excel)", data=to_excel({"Résultats": df_os}),
                           file_name=f"ndvi_{date_str}.xlsx", mime=XLSX_MIME,
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
        try:
            st.session_state.mt_dates = list_dates(str(date_start), str(date_end),
                                                   file_hash, params_t, region)
        except Exception as e:
            st.session_state.mt_dates = None
            st.error(f"Erreur Earth Engine (recherche des dates) : {type(e).__name__} — {e}")

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
            default = [d["date"] for d in dates if usable(d, presel)]
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
        st.dataframe(pivot, hide_index=True, column_config=column_config(pivot))

        detail = ordered(df_long).assign(Delta_NDVI=df_long["Delta_NDVI"])
        with st.expander("Détail complet (toutes les dates × parcelles)"):
            st.dataframe(detail, hide_index=True, column_config=column_config(detail))

        st.download_button(
            "⬇️ Exporter synthèse + détail (Excel)",
            data=to_excel({"Synthèse": pivot, "Détail": detail}),
            file_name=f"ndvi_temporel_{date_start}_{date_end}.xlsx",
            mime=XLSX_MIME, key="mt_dl")
