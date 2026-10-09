"""Carte des parcelles colorées par phase, avec téléchargements KML et HTML."""
import streamlit as st
import streamlit_folium

from ndvi_app.exports.folium_map import build_map, legend_text
from ndvi_app.exports.kml import build_kml
from ndvi_app.ui.context import Parcels


def phase_map(parcels: Parcels, items, key, legend_extra=None, height=520, export=None):
    """
    items  : une entrée par parcelle (dans l'ordre de parcels.features) :
             {"id", "color", "tooltip" (HTML), "opacity", "props" (attributs export)}
    export : {"title", "file"} → boutons KML (Google My Maps / Earth) et HTML.
    """
    m = build_map(parcels.features, items, parcels.bounds, legend_extra)
    streamlit_folium.st_folium(m, height=height, use_container_width=True, key=key,
                               returned_objects=[])
    if not export:
        return
    kml = build_kml(export["title"], [
        {"geometry": feat["geometry"], "name": it["id"], "color": it["color"],
         "opacity": it.get("opacity", 0.6), "props": it.get("props", {"ID": it["id"]})}
        for feat, it in zip(parcels.features, items)], legend_text=legend_text(legend_extra))
    html = build_map(parcels.features, items, parcels.bounds, legend_extra,
                     title=export["title"], for_export=True).get_root().render()
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        st.download_button("🗺️ Carte KML", data=kml, help="Pour Google My Maps ou Google Earth",
                           file_name=f"{export['file']}.kml",
                           mime="application/vnd.google-earth.kml+xml", key=f"{key}_kml")
    with c2:
        st.download_button("🌐 Carte HTML", data=html.encode("utf-8"), help="S'ouvre dans un navigateur",
                           file_name=f"{export['file']}.html", mime="text/html",
                           key=f"{key}_html")
    with c3:
        st.caption("KML : mymaps.google.com → Créer une carte → Importer. Si les couleurs ne "
                   "sont pas reprises : Style → « Styles par colonne de données » → Phase. "
                   "HTML : s'ouvre dans un navigateur (connexion internet requise pour le fond Esri).")
