"""
Colonnes des tableaux affichés et exportés, et leurs définitions (infobulles, onglet Lexique).
"""
import streamlit as st

from ndvi_app.core import soil_reading as soil
from ndvi_app.ui.formatting import fr_decimal as _t

# Colonnes affichées, dans l'ordre
OS_COLS = ["ID", "NDVI", "Phase", "Couvert", "Fiabilite", "Poids", "Statut", "NDTI",
           "Etat_sol", "Lecture", "Pluie_3j_mm", "Humidite_sol", "Gel", "Clair_pct",
           "Pixels_utilises", "Surface_ha", "Date"]
TECH_COLS = ["ID", "Date", "Statut", "NDVI_brut", "NDVI_median", "NDVI_pondere", "NDVI_moyen",
             "NDVI_ecart_type", "EVI2_median", "NDTI_median", "SWIR1_median", "Poids", "Score_clarte", "Clair_pct",
             "Pixels_total", "Pixels_clairs", "Pixels_utilises", "Outliers_exclus",
             "Surface_ha", "Buffer_m", "Satellite"]
SYNTH_COLS = ["ID", "Phase_fin", "NDVI_fin", "Lecture_fin", "Etat_sol_fin", "Confiance",
              "Chronologie", "Baisses_rapides",
              "Mesures_retenues", "Mesures_ecartees", "Plus_long_trou_j", "Derniere_mesure",
              "Jours_sans_mesure_fin", "Surface_ha"]
DETAIL_COLS = ["ID", "Date", "NDVI", "NDVI_lisse", "Phase", "Fiabilite", "Poids", "Retenue",
               "Motif", "NDTI", "Etat_sol", "Lecture", "Pluie_3j_mm", "Pluie_veille_mm",
               "Humidite_sol", "Tmin_7j_C", "Gel", "Statut", "Clair_pct"]
INT_COLS = ["Pixels_utilises", "Outliers_exclus", "Buffer_m", "Pixels_total", "Pixels_clairs",
            "Mesures_retenues", "Mesures_ecartees", "Plus_long_trou_j", "Jours_sans_mesure_fin"]

WIDE_COLS = {"Chronologie", "Motif"}


def pick(df, cols):
    """Colonnes `cols` présentes dans df, dans cet ordre ; colonnes de comptage en entiers."""
    df = df[[c for c in cols if c in df.columns]].copy()
    for c in INT_COLS:
        if c in df.columns:
            df[c] = df[c].round().astype("Int64")
    return df


def column_help(low, high):
    """Définition de chaque colonne ; les seuils d'interprétation y sont rappelés."""
    return {
        "ID": "Identifiant de la parcelle (champ choisi au chargement).",
        "NDVI": "NDVI médian de la parcelle (pixels clairs, hors valeurs aberrantes). "
                "Vide si la mesure n'est pas exploitable.",
        "NDVI_brut": "NDVI médian calculé même si la mesure n'est pas exploitable (à titre indicatif).",
        "Phase": f"Sol nu (NDVI < {_t(low)}), couvert peu développé ({_t(low)}–{_t(high)}), "
                 f"couvert bien développé (≥ {_t(high)}). Dans le détail temporel : phase de la "
                 "courbe lissée à cette date.",
        "Couvert": f"Oui si NDVI ≥ {_t(low)}, Non en dessous, — si la mesure n'est pas exploitable.",
        "Poids": "Poids de fiabilité de la mesure, de 0 à 1 (équivalent du « raw NDVI weight » "
                 "de KERMAP, formule propre à l'appli) = clarté × score Cloud Score+ moyen × "
                 "part de pixels non aberrants × facteur taille (plein à partir de 30 pixels).",
        "Fiabilite": "Bonne (poids ≥ 0,8), Moyenne (0,5–0,8), Faible (< 0,5). "
                     "Non exploitable si le statut n'est pas OK.",
        "Statut": "OK : mesure exploitable. Nuageux : part de pixels clairs sous le seuil "
                  "(50 % par défaut). Trop peu de pixels : moins de pixels utilisés que le "
                  "minimum (10 par défaut). Hors image : parcelle hors de l'emprise. "
                  "Géométrie inexploitable : contour vide ou invalide.",
        "Clair_pct": "Part des pixels de la parcelle (après buffer) non masqués : ni nuage, "
                     "ni ombre, ni cirrus, ni neige, ni proche d'un nuage.",
        "Pixels_utilises": "Pixels de 10 m réellement utilisés : clairs et hors valeurs aberrantes.",
        "Surface_ha": "Surface de la parcelle d'origine (ha).",
        "Date": "Date d'acquisition de l'image.",
        "NDTI": "Indice de résidus de culture (B11 − B12) / (B11 + B12), médiane de la parcelle. "
                f"Interprétable seulement si la parcelle est peu verte (NDVI < {_t(low)}) : "
                "plus élevé sur résidus (cannes, pailles) que sur sol nu. EXPÉRIMENTAL : "
                f"repères indicatifs, à calibrer : ≥ {_t(soil.NDTI_RESIDUE)} résidus, "
                f"≤ {_t(soil.NDTI_BARE)} sol nu ; fortement réduit par l'humidité du sol.",
        "Etat_sol": "Parcelle peu verte seulement. Infrarouge moyen B11 (surface au passage du "
                    f"satellite, sombre si < {_t(soil.SWIR1_WET)}) confirmé par la météo "
                    f"ERA5-Land : humide si pluie des 3 jours ≥ {_t(soil.RAIN_WET, 0)} mm, pluie "
                    f"de la veille ≥ {_t(soil.RAIN_EVE, 0)} mm ou humidité du sol ≥ "
                    f"{_t(soil.SM_WET)} ; sec si pluie des 3 jours < {_t(soil.RAIN_DRY, 0)} mm et "
                    f"humidité < {_t(soil.SM_DRY)}. Humide : B11 sombre et météo humide ou "
                    "fraîche. Humide probable : météo humide, B11 clair (pluie récente), ou B11 "
                    "sombre sans météo. À vérifier : B11 sombre par temps sec (sol sombre ou "
                    "averse locale). Sec : B11 clair hors météo humide. Repères indicatifs, à "
                    "calibrer avec les relevés terrain.",
        "Lecture": "Parcelle peu verte, mesure retenue, dans cet ordre : Levée possible (NDVI "
                   f"lissé en hausse ≥ {_t(soil.RISE_MIN)} sur {soil.RISE_DAYS} jours) ; "
                   "Indéterminé (sol humide) ou (humidité à vérifier) : NDTI non lu ; Résidus "
                   f"probables (NDTI ≥ {_t(soil.NDTI_RESIDUE)}) ; Sol nu probable (NDTI ≤ "
                   f"{_t(soil.NDTI_BARE)}) ; Indéterminé entre les deux. Aide à la décision, à "
                   "confirmer sur le terrain.",
        "Pluie_3j_mm": "Pluie cumulée des 3 jours précédant l'image (ERA5-Land, mm). Vide si "
                       "pas encore publiée.",
        "Pluie_veille_mm": "Pluie de la veille de l'image (ERA5-Land, mm).",
        "Humidite_sol": "Humidité du sol entre 0 et 7 cm le jour de l'image (ERA5-Land, m³/m³ : "
                        "≈ 0,15 sec, ≈ 0,32 capacité au champ, ≈ 0,45 saturé). Maille d'environ "
                        "11 km : une valeur pour la zone.",
        "Tmin_7j_C": "Température minimale des 7 jours précédant l'image (ERA5-Land, °C).",
        "Gel": f"Oui si la température minimale est descendue à {_t(soil.FROST_C, 0)} °C ou "
               "moins dans les 7 jours précédant l'image.",
        "Lecture_fin": "Lecture à la dernière mesure retenue (voir Lecture). — si la parcelle "
                       "était verte.",
        "Etat_sol_fin": "État du sol à la dernière mesure retenue (voir Etat_sol).",
        "Pluie_mm": "Pluie du jour (ERA5-Land, mm).",
        "Tmin_C": "Température minimale du jour (ERA5-Land, °C).",
        "NDTI_median": "Médiane du NDTI des pixels conservés.",
        "SWIR1_median": "Médiane de la réflectance B11 (infrarouge moyen, 1 610 nm).",
        # Synthèse temporelle
        "Phase_fin": "Phase de la courbe lissée à la dernière mesure retenue (état « à l'instant T »).",
        "NDVI_fin": "NDVI lissé à la dernière mesure retenue.",
        "Confiance": "Bonne : ≥ 3 mesures retenues et aucun trou de plus de 20 jours. "
                     "Moyenne : ≥ 2 mesures et trou ≤ 35 jours. Faible sinon.",
        "Chronologie": "Succession des phases de la courbe lissée, épisodes de moins de 10 jours "
                       "fusionnés. Pas d'extrapolation avant la 1re ni après la dernière mesure retenue.",
        "Baisses_rapides": "Dates des chutes du NDVI lissé de plus de 0,20 en moins de 15 jours : "
                           "destruction, récolte ou gel probable. « (gel) » : gel relevé par "
                           "ERA5-Land dans les 10 jours avant ou 3 jours après (couvert gelé "
                           "plutôt que détruit).",
        "Mesures_retenues": "Mesures utilisées pour la courbe.",
        "Mesures_ecartees": "Mesures non utilisées (nuages, poids trop faible, chute isolée), "
                            "hors dates où la parcelle n'était pas dans l'image.",
        "Plus_long_trou_j": "Plus long intervalle sans mesure retenue sur la période, bords compris (jours).",
        "Derniere_mesure": "Date de la dernière mesure retenue.",
        "Jours_sans_mesure_fin": "Jours entre la dernière mesure retenue et la fin de la période.",
        # Détail temporel
        "NDVI_lisse": "Valeur de la courbe lissée à cette date.",
        "Retenue": "Oui si la mesure est utilisée pour la courbe.",
        "Motif": "Raison de l'exclusion : statut non OK, poids trop faible, ou chute isolée "
                 "(valeur nettement sous ses voisines avant et après : résidu de nuage probable).",
        "Incertain": "Vrai si la date est à plus de 15 jours de toute mesure retenue "
                     "(courbe interpolée, tracée en pointillé).",
        # Technique
        "NDVI_median": "Médiane du NDVI des pixels conservés.",
        "NDVI_pondere": "Moyenne du NDVI pondérée par le score Cloud Score+ de chaque pixel.",
        "NDVI_moyen": "Moyenne simple du NDVI des pixels conservés.",
        "NDVI_ecart_type": "Écart-type du NDVI : hétérogénéité de la parcelle.",
        "EVI2_median": "Médiane de l'EVI2, indice moins saturé que le NDVI sur couvert dense.",
        "Score_clarte": "Score Cloud Score+ moyen des pixels utilisés (1 = parfaitement dégagé).",
        "Pixels_total": "Pixels de la parcelle après buffer.",
        "Pixels_clairs": "Pixels non masqués (nuages, ombres…).",
        "Outliers_exclus": "Pixels clairs exclus car aberrants (hors Q1 − k·IQR / Q3 + k·IQR).",
        "Buffer_m": "Buffer intérieur réellement appliqué (m), réduit sur les petites parcelles.",
        "Satellite": "Satellite(s) Sentinel-2 de l'acquisition.",
    }


def column_config(df, help_by_column):
    """Infobulles des en-têtes de colonnes pour st.dataframe."""
    return {c: st.column_config.Column(help=h, width="large" if c in WIDE_COLS else None)
            for c, h in help_by_column.items() if c in df.columns}
