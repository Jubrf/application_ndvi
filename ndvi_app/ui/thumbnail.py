"""Vignette satellite (couleurs naturelles ou fausses couleurs infrarouges) d'une parcelle."""
import pandas as pd
import streamlit as st

from ndvi_app.core.geometry import outline_geojson
from ndvi_app.earth_engine import sentinel2 as gee
from ndvi_app.ui.context import Context

MODES = {gee.THUMB_NATURAL: "Naturelles", gee.THUMB_INFRARED: "Infrarouge"}


def satellite_view(ctx: Context, pid, date_str, key, stacked=False):
    """Case « pixels masqués » et bouton ; l'image reste affichée tant que la parcelle,
    la date et les paramètres de calcul ne changent pas.
    stacked : réglages au-dessus de l'image (colonne étroite) au lieu de côte à côte."""
    p, params_t = ctx.parcels, ctx.settings.params_t
    c1, c2 = (st.container(), st.container()) if stacked else st.columns([1, 2])
    with c1:
        mode = st.segmented_control(
            "Couleurs", list(MODES), default=gee.THUMB_NATURAL, required=True,
            format_func=MODES.get, key=f"{key}_mode", persist_state="session",
            help="Infrarouge : la végétation verte apparaît en rouge vif, même jeune ou "
                 "clairsemée (levée, adventices) ; sol nu gris-brun à bleuté.")
        show_mask = st.checkbox("Pixels masqués en couleur", value=True, key=f"{key}_mask",
                                help="Nuages, ombres, cirrus et marge autour des nuages, "
                                     "selon les réglages du masque : magenta en couleurs "
                                     "naturelles, cyan en infrarouge.")
        if st.button("Afficher l'image", key=f"{key}_btn"):
            st.session_state[key] = (pid, date_str, show_mask, params_t, p.geoms_key)
    if st.session_state.get(key) != (pid, date_str, show_mask, params_t, p.geoms_key):
        return
    idx = p.ids.index(pid)
    with c2:
        with st.spinner("Génération de l'image…"):
            try:
                extra = {} if mode == gee.THUMB_NATURAL else {"mode": mode}
                url = gee.parcel_thumbnail(date_str, f"{p.geoms_key}|{idx}", params_t,
                                           outline_geojson(p.features[idx]["geometry"]),
                                           p.analysis_geojsons[idx], show_mask, **extra)
            except Exception as e:
                st.error(f"Erreur Earth Engine (image) : {type(e).__name__} — {e}")
                return
        st.image(url, width="stretch" if stacked else 480)
        infra = mode == gee.THUMB_INFRARED
        st.caption(f"Sentinel-2 du {pd.Timestamp(date_str):%d/%m/%Y}, "
                   + ("fausses couleurs infrarouges (végétation verte en rouge) · " if infra
                      else "couleurs naturelles · ")
                   + "contour blanc : parcelle · contour jaune : zone analysée (après buffer)"
                   + ((" · cyan" if infra else " · magenta") + " : pixels masqués" if show_mask else "")
                   + ". Lien temporaire (quelques heures).")
