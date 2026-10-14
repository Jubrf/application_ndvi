"""
Application Streamlit multipage : connexion Earth Engine, réglages (barre latérale), projet,
menu de navigation en haut, bandeau du projet, puis la page choisie.

Exécutée entièrement à chaque interaction (fonctionnement de Streamlit) ; l'état durable
(réglages, projet, résultats) est dans st.session_state (voir ndvi_app/ui/state.py).
"""
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import streamlit as st

from ndvi_app.config import APP_VERSION
from ndvi_app.earth_engine import sentinel2 as gee
from ndvi_app.logs import log
from ndvi_app.ui import analysis, banner, state, weather
from ndvi_app.ui.context import Context
from ndvi_app.ui.loading import Project, load_project
from ndvi_app.ui.sidebar import render_sidebar
from ndvi_app.ui.views import carte, ensemble, fiche, projet, une_date

LOGO = Path(__file__).parent / "assets" / "logo.svg"
ICON = Path(__file__).parent / "assets" / "icone.svg"


@dataclass
class AppView:
    """Ce que reçoit chaque page : projet, contexte (réglages + parcelles), pages du menu."""
    project: Project
    ctx: Context | None
    pages: dict = field(default_factory=dict)

    @cached_property
    def results(self):
        """Résultats de l'analyse temporelle interprétés avec les réglages actuels (ou None)."""
        return analysis.results(self.ctx)


def _connect_earth_engine():
    with st.spinner("Connexion à Earth Engine…"):
        try:
            gee.init_gee(st.secrets["GEE_SERVICE_ACCOUNT"], st.secrets["GEE_PRIVATE_KEY"])
        except Exception as e:
            log(f"Échec de la connexion Earth Engine : {type(e).__name__} — {e}")
            st.error(f"Connexion à Earth Engine impossible : {type(e).__name__} — {e}")
            st.stop()


def _pages(app):
    def page_projet():
        projet.render(app)

    def page_ensemble():
        ensemble.render(app)

    def page_fiche():
        fiche.render(app)

    def page_carte():
        carte.render(app)

    def page_une_date():
        une_date.render(app)

    return {
        "projet": st.Page(page_projet, title="Projet", icon=":material/folder_open:",
                          url_path="projet", default=True),
        "ensemble": st.Page(page_ensemble, title="Vue d'ensemble", icon=":material/table_view:",
                            url_path="vue-ensemble"),
        "fiche": st.Page(page_fiche, title="Fiche parcelle", icon=":material/show_chart:",
                         url_path="fiche-parcelle"),
        "carte": st.Page(page_carte, title="Carte", icon=":material/map:", url_path="carte"),
        "une_date": st.Page(page_une_date, title="Analyse à une date",
                            icon=":material/calendar_today:", url_path="une-date"),
    }


def main():
    st.set_page_config(page_title="NDVI parcellaire", page_icon=str(ICON), layout="wide")
    st.logo(str(LOGO), size="large")
    log(f"Script lancé ({APP_VERSION})")

    _connect_earth_engine()
    state.init_settings()
    settings = render_sidebar(locked=state.session_lock() is not None)
    project = load_project(settings)
    ctx = Context(settings=settings, parcels=project.parcels) if project.parcels else None
    weather.ensure(ctx)             # météo ERA5-Land de l'analyse temporelle (une requête)
    app = AppView(project=project, ctx=ctx)
    app.pages = _pages(app)

    page = st.navigation(list(app.pages.values()), position="top")
    banner.render(app)
    page.run()
