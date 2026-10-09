"""Barre latérale : réglages de l'analyse (valeurs gardées en session sous les clés set_*)."""
import streamlit as st

from ndvi_app.ui.context import Settings


def render_sidebar() -> Settings:
    """Affiche les réglages et renvoie leurs valeurs. state.init_settings() doit avoir été appelé."""
    with st.sidebar:
        st.header("Paramètres d'analyse")
        low, high = st.slider(
            "Seuils d'interprétation (NDVI)", 0.0, 1.0, step=0.01, key="set_thr",
            help="Sous le 1er seuil : sol nu. Entre les deux : couvert peu développé. "
                 "Au-dessus du 2nd : couvert bien développé. S'appliquent sans relancer l'analyse.",
        )
        if high - low < 0.05:
            st.warning("Les deux seuils sont très proches.")
        buffer_m = st.select_slider(
            "Buffer intérieur (m)", options=[0, 5, 10, 15, 20], key="set_buffer",
            help="Retire une bande en bordure de parcelle (haies, chemins, voisins). "
                 "Réduit automatiquement pour les petites parcelles.",
        )
        with st.expander("Masque nuages et qualité"):
            cs_threshold = st.slider(
                "Seuil Cloud Score+", 0.40, 0.85, step=0.05, key="set_cs",
                help="Pixels sous ce score rejetés (nuages, ombres, brume). "
                     "Plus haut = plus strict.",
            )
            cloud_buffer_m = st.select_slider(
                "Marge autour des nuages (m)", options=[0, 10, 20, 40, 60], key="set_cloudbuf",
            )
            iqr_k = st.select_slider(
                "Exclusion des valeurs aberrantes (k × IQR)", options=[1.0, 1.5, 2.0, 3.0],
                key="set_iqr",
                help="Pixels hors [Q1 − k·IQR ; Q3 + k·IQR] exclus. Plus haut = moins d'exclusions.",
            )
            min_clear = st.slider("Part minimale de pixels clairs (%)", 0, 100, step=5,
                                  key="set_minclear")
            min_pixels = st.number_input("Nombre minimal de pixels utilisés", 1, 500,
                                         key="set_minpix")
        with st.expander("Courbe temporelle"):
            min_weight = st.slider(
                "Poids de fiabilité minimal d'une mesure", 0.0, 1.0, step=0.05, key="set_minw",
                help="Les mesures sous ce poids sont écartées de la courbe (affichées en gris).")
            smooth_days = st.select_slider(
                "Lissage (jours)", options=[3, 4, 6, 8, 10, 15], key="set_smooth",
                help="Plus la valeur est grande, plus la courbe est lisse, mais plus elle réagit "
                     "tard aux changements (levée, destruction).")

    return Settings(low=low, high=high, buffer_m=buffer_m, cs_threshold=cs_threshold,
                    cloud_buffer_m=cloud_buffer_m, iqr_k=iqr_k, min_clear=min_clear,
                    min_pixels=min_pixels, min_weight=min_weight, smooth_days=smooth_days)
