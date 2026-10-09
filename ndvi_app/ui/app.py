"""
Page Streamlit : en-tête, connexion Earth Engine, réglages, chargement des parcelles, onglets.

Exécutée entièrement à chaque interaction (fonctionnement de Streamlit) ; l'état durable
(réglages, résultats) est dans st.session_state (voir ndvi_app/ui/state.py).
"""
import streamlit as st

from ndvi_app.config import APP_VERSION
from ndvi_app.earth_engine import sentinel2 as gee
from ndvi_app.logs import log
from ndvi_app.ui import state, tab_date, tab_temporal
from ndvi_app.ui.context import Context
from ndvi_app.ui.loading import load_parcels
from ndvi_app.ui.sidebar import render_sidebar


def _connect_earth_engine():
    with st.spinner("Connexion à Earth Engine…"):
        try:
            gee.init_gee(st.secrets["GEE_SERVICE_ACCOUNT"], st.secrets["GEE_PRIVATE_KEY"])
        except Exception as e:
            log(f"Échec de la connexion Earth Engine : {type(e).__name__} — {e}")
            st.error(f"Connexion à Earth Engine impossible : {type(e).__name__} — {e}")
            st.stop()


def main():
    st.set_page_config(page_title="NDVI parcellaire", page_icon="🌱", layout="wide")
    st.title("🌱 NDVI – Analyse parcellaire Sentinel-2")
    st.sidebar.caption(f"Version {APP_VERSION}")
    log(f"Script lancé ({APP_VERSION})")

    _connect_earth_engine()
    state.init_settings()
    settings = render_sidebar()
    ctx = Context(settings=settings, parcels=load_parcels(settings))

    tab1, tab2 = st.tabs(["📅 Analyse à une date", "📈 Analyse temporelle"])
    with tab1:
        tab_date.render(ctx)
    with tab2:
        tab_temporal.render(ctx)
