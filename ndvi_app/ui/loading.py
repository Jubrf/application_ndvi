"""
Chargement des parcelles : nouveau fichier (SHP zippé ou GeoJSON) ou analyse enregistrée.

S'arrête (st.stop) tant qu'aucun fichier exploitable n'est chargé.
"""
import datetime
import hashlib

import streamlit as st

from ndvi_app.core.geometry import looks_like_wgs84, prepare_all, region_geojson
from ndvi_app.core.quality import unique_ids
from ndvi_app.core.session_format import read_session_zip
from ndvi_app.core.vector_io import load_vector_from_bytes
from ndvi_app.ui import state
from ndvi_app.ui.context import Parcels, Settings

SRC_NEW = "Nouvelle analyse : charger des parcelles"
SRC_OPEN = "Ouvrir une analyse enregistrée"

# Même fichier → pas de nouvelle lecture (cache sur le contenu)
load_vector = st.cache_data(show_spinner="Chargement du fichier vecteur…")(load_vector_from_bytes)


@st.cache_data(show_spinner="Préparation des géométries…")
def _prepare_geometries(_features, file_key, buf):
    return prepare_all(_features, buf), region_geojson(_features)


def _open_session(sess_file):
    """Lit une analyse enregistrée ; au premier passage, programme la restauration de ses
    réglages et résultats puis relance le script. Retourne (session, bytes des parcelles)."""
    try:
        session, vec_bytes = read_session_zip(sess_file.getvalue())
    except ValueError as e:
        st.error(f"Impossible d'ouvrir ce fichier : {e}")
        st.stop()
    sess_id = hashlib.md5(sess_file.getvalue()).hexdigest()
    if st.session_state.get("opened_session") != sess_id:
        # Restauration appliquée en tête du prochain passage (state.init_settings)
        state.clear_results()
        st.session_state["loaded_file"] = session["file_hash"]
        st.session_state["opened_session"] = sess_id
        st.session_state["pending_restore"] = state.restore_payload(session)
        st.rerun()
    saved = datetime.datetime.fromisoformat(session["saved_at"])
    st.info(f"Analyse enregistrée le {saved:%d/%m/%Y à %H:%M} "
            f"(version {session.get('app_version', '?')}) — période du "
            f"{session['period'][0]:%d/%m/%Y} au {session['period'][1]:%d/%m/%Y} — "
            f"fichier d'origine : {session['source_file']}. Résultats dans l'onglet « Analyse temporelle ».")
    return session, vec_bytes


def load_parcels(settings: Settings) -> Parcels:
    src_mode = st.radio("Source", [SRC_NEW, SRC_OPEN], horizontal=True, key="src_mode",
                        label_visibility="collapsed")

    if src_mode == SRC_NEW:
        uploaded = st.file_uploader("📁 Charger un SHP (ZIP) ou un GeoJSON", type=["zip", "geojson"])
        if uploaded is None:
            st.stop()
        vec_bytes, vec_name, source_name = uploaded.getvalue(), uploaded.name, uploaded.name
        file_hash = hashlib.md5(vec_bytes).hexdigest()
    else:
        sess_file = st.file_uploader("📂 Analyse enregistrée (.zip créé par le bouton « Enregistrer "
                                     "l'analyse » de l'onglet temporel)", type=["zip"],
                                     key="session_file")
        if sess_file is None:
            st.stop()
        session, vec_bytes = _open_session(sess_file)
        vec_name, source_name, file_hash = "parcelles.geojson", session["source_file"], session["file_hash"]

    if st.session_state.get("loaded_file") != file_hash:
        state.clear_results()
        st.session_state["loaded_file"] = file_hash

    features = load_vector(vec_bytes, vec_name)
    if not features:
        st.error("Aucune parcelle trouvée dans le fichier.")
        st.stop()
    if not looks_like_wgs84(features):
        st.error("Coordonnées non reconnues : le fichier .prj est probablement absent "
                 "du ZIP. Ajoute-le ou exporte la couche en WGS84 / Lambert-93 avec son .prj.")
        st.stop()

    fields = list(features[0]["properties"].keys())
    id_field = None
    if fields:
        default_id = ("ID" if src_mode == SRC_OPEN and "ID" in fields
                      else "NUM_ILOT" if "NUM_ILOT" in fields else fields[0])
        id_field = st.selectbox("Champ identifiant des parcelles", fields,
                                index=fields.index(default_id), disabled=src_mode == SRC_OPEN)
        ids = unique_ids([f["properties"].get(id_field) for f in features])
    else:
        ids = [f"PARCELLE_{i + 1}" for i in range(len(features))]

    geoinfo, region = _prepare_geometries(features, file_hash, settings.buffer_m)
    parcels = Parcels(features=features, ids=ids, fields=fields, id_field=id_field,
                      source_name=source_name, file_hash=file_hash, geoinfo=geoinfo,
                      region=region, geoms_key=f"{file_hash}|{settings.buffer_m}")

    n_reduced = sum(1 for g in geoinfo if g["geojson"] and g["buffer_m"] < settings.buffer_m)
    n_bad = sum(1 for g in geoinfo if g["geojson"] is None)
    msg = f"{len(features)} parcelles chargées"
    if n_reduced:
        msg += f" · buffer réduit sur {n_reduced} petite(s) parcelle(s)"
    st.success(msg)
    if n_bad:
        st.warning(f"{n_bad} géométrie(s) inexploitable(s) (vides ou invalides) : ignorée(s).")
    return parcels
