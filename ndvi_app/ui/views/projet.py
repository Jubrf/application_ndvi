"""Page « Projet » : source (nouvelle analyse / analyse enregistrée), étapes, période et dates."""
import streamlit as st

from ndvi_app.config import COLOR_MAP
from ndvi_app.ui import temporal_setup
from ndvi_app.ui.loading import SRC_NEW, SRC_OPEN, store_upload

NO_PHASE = "Aucune courbe"
MESSAGE = {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}


def render(app):
    st.title("Projet")
    mode = st.segmented_control("Source", [SRC_NEW, SRC_OPEN], default=SRC_NEW, key="src_mode",
                                required=True, persist_state="session",
                                label_visibility="collapsed")
    if mode == SRC_NEW:
        uploaded = st.file_uploader("📁 Charger un SHP (ZIP) ou un GeoJSON", type=["zip", "geojson"])
    else:
        uploaded = st.file_uploader("📂 Analyse enregistrée (.zip créé par le bouton « Enregistrer »)",
                                    type=["zip"], key="session_file")
    store_upload(mode, uploaded)

    project = app.project
    if project.file_name and uploaded is None:
        st.caption(f"Fichier en cours : **{project.file_name}** — dépose un autre fichier pour le remplacer.")
    for kind, text in project.messages:
        MESSAGE[kind](text)
    if project.fields:
        if mode == SRC_NEW:
            st.selectbox("Champ identifiant des parcelles", project.fields,
                         index=project.fields.index(project.id_field), key="proj_id_field",
                         persist_state="session")
        else:
            st.selectbox("Champ identifiant des parcelles", project.fields,
                         index=project.fields.index(project.id_field), key="proj_id_field_open",
                         disabled=True, help="Fixé par l'analyse enregistrée.")
    if app.ctx is None:
        return

    _steps(app)
    with st.container(border=True):
        temporal_setup.render(app.ctx, results_page=app.pages["ensemble"])


def _step(num, title, text, done, link=None):
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        st.markdown(f"**{num}. {title}**  \n{text}")
        st.space("stretch")
        if link is not None:
            st.page_link(link[0], label=link[1], icon=":material/arrow_forward:")
        elif done:
            st.badge("Fait", icon=":material/check:", color="green")


def _steps(app):
    p = app.ctx.parcels
    ss = st.session_state
    _step(1, "Parcelles", f"{len(p.features)} parcelles · identifiant : {p.id_field or '—'} · "
          f"fichier d'origine : {p.source_name}", True)
    res = app.results
    if res is None:
        _step(2, "Période et dates", "Choisis la période ci-dessous puis lance l'analyse.", False)
        return
    errors = ss.get("mt_errors", [])
    _step(2, "Période et dates", f"{res.p_start:%d/%m/%Y} → {res.p_end:%d/%m/%Y} · "
          f"{len(ss.mt_raw)} dates analysées · "
          + (f"{len(errors)} date(s) en erreur" if errors else "aucune erreur"), True)
    for err in errors:
        st.warning(f"Date non traitée — {err}")
    phase = res.synthese["Phase_fin"].fillna(NO_PHASE)
    counts = " · ".join(f"{(phase == ph).sum()} {ph.lower()}" for ph in list(COLOR_MAP) + [NO_PHASE]
                        if (phase == ph).any())
    _step(3, "Résultats", f"Phase en fin de période : {counts}", True,
          link=(app.pages["ensemble"], "Vue d'ensemble"))
