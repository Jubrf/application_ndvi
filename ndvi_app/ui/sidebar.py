"""
Barre latérale (toutes les pages) : réglages gardés en session sous les clés set_*.

Deux blocs :
  Interprétation : seuils, qualité minimale, courbe — s'appliquent sans nouveau calcul ;
  Calcul         : buffer et masque nuages — conditionnent les requêtes Earth Engine,
                   verrouillés quand une analyse enregistrée est ouverte.
"""
import streamlit as st

from ndvi_app.config import APP_VERSION
from ndvi_app.ui.context import Settings


def render_sidebar(locked=False) -> Settings:
    """Affiche les réglages et renvoie leurs valeurs. state.init_settings() doit avoir été appelé."""
    with st.sidebar:
        st.subheader("Interprétation")
        st.caption("S'applique sans nouveau calcul.")
        low, high = st.slider(
            "Seuils d'interprétation (NDVI)", 0.0, 1.0, step=0.01, key="set_thr",
            help="Sous le 1er seuil : sol nu. Entre les deux : couvert peu développé. "
                 "Au-dessus du 2nd : couvert bien développé.",
        )
        if high - low < 0.05:
            st.warning("Les deux seuils sont très proches.")
        grey_on = st.toggle(
            "Zone grise « à vérifier »", key="set_grey_on",
            help="Repère les parcelles dont le NDVI en fin de période tombe dans une plage de "
                 "doute (couvert qui lève, adventices, sol nu) : à vérifier sur l'image "
                 "infrarouge ou sur le terrain. Désactivée : rien n'est signalé.")
        # Toujours affiché (grisé si désactivé) : les bornes choisies sont conservées
        grey = st.slider("Zone grise (NDVI)", 0.0, 1.0, step=0.01, key="set_grey",
                         disabled=not grey_on,
                         help="NDVI lissé à la dernière mesure retenue compris dans cette plage "
                              "(bornes incluses) → « à vérifier ». Bornes à caler avec les "
                              "relevés terrain.")
        grey = grey if grey_on else None
        min_clear = st.slider("Part minimale de pixels clairs (%)", 0, 100, step=5,
                              key="set_minclear",
                              help="Mesure non exploitable si la part de pixels clairs de la "
                                   "parcelle est inférieure.")
        min_pixels = st.number_input("Nombre minimal de pixels utilisés", 1, 500, key="set_minpix")
        min_weight = st.slider(
            "Poids de fiabilité minimal d'une mesure", 0.0, 1.0, step=0.05, key="set_minw",
            help="Les mesures sous ce poids sont écartées de la courbe (affichées en gris).")
        smooth_days = st.select_slider(
            "Lissage de la courbe (jours)", options=[3, 4, 6, 8, 10, 15], key="set_smooth",
            help="Plus la valeur est grande, plus la courbe est lisse, mais plus elle réagit "
                 "tard aux changements (levée, destruction).")

        st.divider()
        st.subheader(":material/lock: Calcul" if locked else "Calcul")
        st.caption("Fixé par l'analyse ouverte : les dates ajoutées sont calculées exactement "
                   "comme les autres." if locked else
                   "Conditionne les requêtes Earth Engine : relancer l'analyse après un changement.")
        buffer_m = st.select_slider(
            "Buffer intérieur (m)", options=[0, 5, 10, 15, 20], key="set_buffer",
            help="Retire une bande en bordure de parcelle (haies, chemins, voisins). "
                 "Réduit automatiquement pour les petites parcelles.",
            disabled=locked,
        )
        cs_threshold = st.slider(
            "Seuil Cloud Score+", 0.40, 0.85, step=0.05, key="set_cs",
            help="Pixels sous ce score rejetés (nuages, ombres, brume). Plus haut = plus strict.",
            disabled=locked,
        )
        cloud_buffer_m = st.select_slider(
            "Marge autour des nuages (m)", options=[0, 10, 20, 40, 60], key="set_cloudbuf",
            disabled=locked,
        )
        iqr_k = st.select_slider(
            "Exclusion des valeurs aberrantes (k × IQR)", options=[1.0, 1.5, 2.0, 3.0],
            key="set_iqr",
            help="Pixels hors [Q1 − k·IQR ; Q3 + k·IQR] exclus. Plus haut = moins d'exclusions.",
            disabled=locked,
        )
        st.divider()
        st.caption(f"Version {APP_VERSION}")

    return Settings(low=low, high=high, buffer_m=buffer_m, cs_threshold=cs_threshold,
                    cloud_buffer_m=cloud_buffer_m, iqr_k=iqr_k, min_clear=min_clear,
                    min_pixels=min_pixels, min_weight=min_weight, smooth_days=smooth_days,
                    grey=tuple(grey) if grey else None)
