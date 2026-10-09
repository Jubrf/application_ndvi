"""Vignette satellite (couleurs naturelles) d'une parcelle à une date."""
import pandas as pd
import streamlit as st

from ndvi_app.core.geometry import outline_geojson
from ndvi_app.earth_engine import sentinel2 as gee
from ndvi_app.ui.context import Context


def satellite_view(ctx: Context, pid, date_str, key):
    """Case « pixels masqués » et bouton ; l'image reste affichée tant que la parcelle,
    la date et les paramètres de calcul ne changent pas."""
    p, params_t = ctx.parcels, ctx.settings.params_t
    c1, c2 = st.columns([1, 2])
    with c1:
        show_mask = st.checkbox("Pixels masqués en magenta", value=True, key=f"{key}_mask",
                                help="Nuages, ombres, cirrus et marge autour des nuages, "
                                     "selon les réglages du masque.")
        if st.button("Afficher l'image", key=f"{key}_btn"):
            st.session_state[key] = (pid, date_str, show_mask, params_t, p.geoms_key)
    if st.session_state.get(key) != (pid, date_str, show_mask, params_t, p.geoms_key):
        return
    idx = p.ids.index(pid)
    with c2:
        with st.spinner("Génération de l'image…"):
            try:
                url = gee.parcel_thumbnail(date_str, f"{p.geoms_key}|{idx}", params_t,
                                           outline_geojson(p.features[idx]["geometry"]),
                                           p.analysis_geojsons[idx], show_mask)
            except Exception as e:
                st.error(f"Erreur Earth Engine (image) : {type(e).__name__} — {e}")
                return
        st.image(url, width=480)
        st.caption(f"Sentinel-2 du {pd.Timestamp(date_str):%d/%m/%Y}, couleurs naturelles · "
                   "contour blanc : parcelle · contour jaune : zone analysée (après buffer)"
                   + (" · magenta : pixels masqués" if show_mask else "")
                   + ". Lien temporaire (quelques heures).")
