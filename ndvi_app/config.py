"""
Constantes partagées par toute l'application (une seule définition de chaque valeur).

Les paramètres propres au calcul Earth Engine (collections, classes SCL, réglages
par défaut du masque) restent dans ndvi_app/earth_engine/sentinel2.py ; les réglages
de l'analyse temporelle dans ndvi_app/core/timeseries.py.
"""

# Version affichée dans la barre latérale : à changer à chaque modification,
# pour savoir quel code tourne réellement sur Streamlit Cloud.
APP_VERSION = "v3.0 — 09/10/2026"

# ------------------------------------------------------------------ phases NDVI
PHASE_NU = "Sol nu"
PHASE_PEU = "Couvert peu développé"
PHASE_BIEN = "Couvert bien développé"
PHASES = [PHASE_NU, PHASE_PEU, PHASE_BIEN]

# Seuils d'interprétation par défaut (réglables dans la barre latérale)
DEFAULT_THRESHOLDS = (0.25, 0.50)

# Couleurs des phases (cartes, graphiques, KML)
COLOR_MAP = {PHASE_NU: "#d73027", PHASE_PEU: "#a6d96a", PHASE_BIEN: "#1a9850"}
COLOR_INVALID = "#9e9e9e"     # mesure non exploitable / pas de courbe

# ------------------------------------------------------------------ statut d'une mesure
STATUS_OK = "OK"
STATUS_CLOUD = "Nuageux"
STATUS_FEW = "Trop peu de pixels"
STATUS_NOGEOM = "Géométrie inexploitable"
STATUS_NODATA = "Hors image"

# ------------------------------------------------------------------ réglages par défaut
DEFAULT_BUFFER_M = 10         # buffer intérieur des parcelles (m)
DEFAULT_MIN_CLEAR = 50        # part minimale de pixels clairs (%)
DEFAULT_MIN_PIXELS = 10       # nombre minimal de pixels utilisés

# ------------------------------------------------------------------ calendrier
FIRST_YEAR = 2017             # première année proposée (Sentinel-2 L2A harmonisé)
MONTHS_FR = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
             "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
