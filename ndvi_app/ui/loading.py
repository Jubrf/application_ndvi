"""
Projet : fichier de parcelles ou analyse enregistrée, conservé d'une page à l'autre.

Le fichier déposé est gardé en session (proj_new / proj_open) : la zone de dépôt de la page
Projet perd son contenu quand on change de page, pas le projet. La lecture du projet
(`load_project`) se fait à chaque passage, avant l'affichage de la page, sans widget : les
messages (erreurs, nombre de parcelles…) sont affichés par la page Projet.
"""
import datetime
import hashlib
from dataclasses import dataclass, field

import streamlit as st

from ndvi_app.core.geometry import looks_like_wgs84, prepare_all, region_geojson
from ndvi_app.core.quality import unique_ids
from ndvi_app.core.session_format import read_session_zip
from ndvi_app.core.vector_io import VectorFileError, load_vector_from_bytes
from ndvi_app.logs import log
from ndvi_app.ui import state
from ndvi_app.ui.context import Parcels, Settings

SRC_NEW, SRC_OPEN = state.SRC_NEW, state.SRC_OPEN
STORE = {SRC_NEW: "proj_new", SRC_OPEN: "proj_open"}

# Même fichier → pas de nouvelle lecture (cache sur le contenu)
load_vector = st.cache_data(show_spinner="Chargement du fichier vecteur…")(load_vector_from_bytes)


@st.cache_data(show_spinner="Préparation des géométries…")
def _prepare_geometries(_features, file_key, buf):
    return prepare_all(_features, buf), region_geojson(_features)


@dataclass
class Project:
    mode: str
    file_name: str | None = None
    parcels: Parcels | None = None
    fields: list = field(default_factory=list)
    id_field: str | None = None
    messages: list = field(default_factory=list)     # [(« success » | « warning » | « error » | « info », texte)]


def source_mode():
    return st.session_state.get("src_mode") or SRC_NEW


def default_id_field(fields, mode):
    return ("ID" if mode == SRC_OPEN and "ID" in fields
            else "NUM_ILOT" if "NUM_ILOT" in fields else fields[0])


def store_upload(mode, uploaded):
    """Garde en session le fichier déposé ; relance le script s'il est nouveau."""
    if uploaded is None:
        return
    data = uploaded.getvalue()
    digest = hashlib.md5(data).hexdigest()
    current = st.session_state.get(STORE[mode])
    if not current or current["hash"] != digest:
        st.session_state[STORE[mode]] = {"name": uploaded.name, "data": data, "hash": digest}
        st.rerun()


def _open_session(project, src):
    """Lit une analyse enregistrée ; au premier passage, programme la restauration de ses
    réglages et résultats puis relance le script. Retourne (session, bytes des parcelles)."""
    try:
        session, vec_bytes = read_session_zip(src["data"])
    except ValueError as e:
        project.messages.append(("error", f"Impossible d'ouvrir ce fichier : {e}"))
        return None, None
    sess_id = src["hash"]
    if st.session_state.get("opened_session") != sess_id:
        # Restauration appliquée en tête du prochain passage (state.init_settings)
        state.clear_results()
        st.session_state["loaded_file"] = session["file_hash"]
        st.session_state["opened_session"] = sess_id
        st.session_state["pending_restore"] = state.restore_payload(session, token=sess_id)
        st.rerun()
    saved = datetime.datetime.fromisoformat(session["saved_at"])
    project.messages.append((
        "info", f"Analyse enregistrée le {saved:%d/%m/%Y à %H:%M} "
                f"(version {session.get('app_version', '?')}) — période du "
                f"{session['period'][0]:%d/%m/%Y} au {session['period'][1]:%d/%m/%Y} — "
                f"fichier d'origine : {session['source_file']}."))
    return session, vec_bytes


def load_project(settings: Settings) -> Project:
    """Projet courant, reconstruit à chaque passage à partir du fichier gardé en session."""
    mode = source_mode()
    project = Project(mode=mode)
    if mode == SRC_NEW:
        state.release_session()
    src = st.session_state.get(STORE[mode])
    if not src:
        return project
    project.file_name = src["name"]

    if mode == SRC_NEW:
        vec_bytes, vec_name, source_name, file_hash = src["data"], src["name"], src["name"], src["hash"]
    else:
        session, vec_bytes = _open_session(project, src)
        if session is None:
            return project
        vec_name, source_name, file_hash = "parcelles.geojson", session["source_file"], session["file_hash"]

    if st.session_state.get("loaded_file") != file_hash:
        state.clear_results()
        st.session_state["loaded_file"] = file_hash

    try:
        features = load_vector(vec_bytes, vec_name)
    except VectorFileError as e:
        project.messages.append(("error", str(e)))
        return project
    except Exception as e:  # fichier inattendu : message lisible plutôt qu'un arrêt de l'appli
        log(f"Lecture du fichier de parcelles {vec_name} : {type(e).__name__}: {e}")
        project.messages.append(("error", f"Fichier « {vec_name} » illisible ({type(e).__name__}). "
                                          "Dépose un ZIP de shapefile (.shp, .shx, .dbf, .prj) "
                                          "ou un GeoJSON."))
        return project
    if not features:
        project.messages.append(("error", "Aucune parcelle trouvée dans le fichier."))
        return project
    if not looks_like_wgs84(features):
        project.messages.append((
            "error", "Coordonnées non reconnues : le fichier .prj est probablement absent "
                     "du ZIP. Ajoute-le ou exporte la couche en WGS84 / Lambert-93 avec son .prj."))
        return project

    fields = list(features[0]["properties"].keys())
    id_field = None
    if fields:
        id_field = default_id_field(fields, mode)
        chosen = st.session_state.get("proj_id_field")
        if mode == SRC_NEW and chosen in fields:
            id_field = chosen
        ids = unique_ids([f["properties"].get(id_field) for f in features])
    else:
        ids = [f"PARCELLE_{i + 1}" for i in range(len(features))]
    project.fields, project.id_field = fields, id_field

    geoinfo, region = _prepare_geometries(features, file_hash, settings.buffer_m)
    project.parcels = Parcels(features=features, ids=ids, fields=fields, id_field=id_field,
                              source_name=source_name, file_hash=file_hash, geoinfo=geoinfo,
                              region=region, geoms_key=f"{file_hash}|{settings.buffer_m}")

    n_reduced = sum(1 for g in geoinfo if g["geojson"] and g["buffer_m"] < settings.buffer_m)
    n_bad = sum(1 for g in geoinfo if g["geojson"] is None)
    msg = f"{len(features)} parcelles chargées"
    if n_reduced:
        msg += f" · buffer réduit sur {n_reduced} petite(s) parcelle(s)"
    project.messages.append(("success", msg))
    if n_bad:
        project.messages.append(
            ("warning", f"{n_bad} géométrie(s) inexploitable(s) (vides ou invalides) : ignorée(s)."))
    return project
