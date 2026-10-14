"""
Lecture d'une parcelle peu verte : état du sol (météo + infrarouge moyen) et lecture
NDVI / NDTI (levée, résidus, sol nu). Python pur, sans Earth Engine ni Streamlit.

Toutes les valeurs repères sont INDICATIVES, à calibrer avec les relevés terrain.

1. Météo au jour d'une mesure (ERA5-Land, une série pour la zone) :
     pluie des 3 jours précédents (J-3 à J-1 ; la pluie du jour J peut tomber après le
     passage du satellite vers 10 h 30), humidité du sol 0–7 cm du jour, température
     minimale des 7 derniers jours (J-7 à J).
   Classe météo du sol (la pluie récente décide : la surface, seule vue par le satellite,
   sèche en un à deux jours ; l'humidité 0–7 cm du modèle ne compte que près de la
   saturation, car elle reste élevée tout l'hiver) :
     humide : pluie 3 j ≥ RAIN_WET, ou pluie de la veille ≥ RAIN_EVE, ou humidité ≥ SM_WET
     sec    : pluie 3 j < RAIN_DRY et humidité < SM_DRY (≈ capacité au champ)
     frais  : entre les deux

2. État du sol (seulement si la mesure est retenue et la parcelle peu verte, NDVI < seuil
   bas). L'infrarouge moyen B11 voit la surface au moment du passage ; la météo confirme :
                    B11 < SWIR1_WET (sombre)      B11 ≥ SWIR1_WET (clair)
     météo humide   Humide                        Humide probable (pluie récente)
     météo frais    Humide                        Sec
     météo sec      À vérifier (sol sombre ?)     Sec
     sans météo     Humide probable               Sec

3. Lecture (mêmes conditions), dans cet ordre :
     a. NDVI lissé en hausse d'au moins RISE_MIN sur RISE_DAYS jours  → Levée possible
     b. état du sol Humide ou Humide probable                         → Indéterminé (sol humide)
     c. état du sol À vérifier                                        → Indéterminé (humidité à vérifier)
     d. NDTI ≥ NDTI_RESIDUE                                           → Résidus probables
     e. NDTI ≤ NDTI_BARE                                              → Sol nu probable
     f. sinon                                                         → Indéterminé
   Le NDTI n'est lu que sur sol sec : l'eau écrase le contraste B11 / B12 qui distingue
   les résidus (cellulose, lignine) du sol nu.

4. Gel : température minimale ≤ FROST_C dans les 7 jours. Une baisse rapide du NDVI
   précédée de gel (FROST_BEFORE_DAYS jours avant, FROST_AFTER_DAYS après) est signalée
   « (gel) » : couvert gelé plutôt que détruit.
"""
import datetime

import pandas as pd

SWIR1_WET = 0.15        # réflectance B11 sous laquelle la surface paraît humide
SM_WET = 0.40           # humidité du sol 0–7 cm (m³/m³) : proche de la saturation
SM_DRY = 0.32           # … : sous la capacité au champ (≈ sol ressuyé)
RAIN_WET = 10.0         # pluie des 3 jours précédents (mm) : sol humide
RAIN_EVE = 5.0          # pluie de la veille (mm) : sol humide
RAIN_DRY = 2.0          # pluie des 3 jours précédents (mm) : sol sec en dessous
NDTI_RESIDUE = 0.10     # NDTI au-dessus : résidus probables (sol sec)
NDTI_BARE = 0.05        # NDTI en dessous : sol nu probable (sol sec)
RISE_MIN = 0.05         # hausse du NDVI lissé…
RISE_DAYS = 14          # …sur N jours : levée possible
FROST_C = -2.0          # gel : température minimale (°C)
FROST_BEFORE_DAYS = 10
FROST_AFTER_DAYS = 3

ETAT_HUMIDE, ETAT_PROBABLE, ETAT_VERIF, ETAT_SEC = "Humide", "Humide probable", "À vérifier", "Sec"
LEVEE = "Levée possible"
INDET_HUMIDE = "Indéterminé (sol humide)"
INDET_VERIF = "Indéterminé (humidité à vérifier)"
RESIDUS = "Résidus probables"
SOL_NU = "Sol nu probable"
INDET = "Indéterminé"
NONE = "—"


def _day(d, offset=0):
    """Jour (chaîne, date ou Timestamp) décalé de offset jours, au format AAAA-MM-JJ."""
    return (pd.Timestamp(d).date() + datetime.timedelta(days=offset)).isoformat()


def weather_at(days, date):
    """Pluie 3 j (mm), humidité du sol, température minimale 7 j (°C) au jour d'une mesure ;
    None quand la donnée manque (pas encore publiée)."""
    if not days:
        return {"Pluie_3j_mm": None, "Pluie_veille_mm": None, "Humidite_sol": None,
                "Tmin_7j_C": None}
    rain = [days.get(_day(date, -k)) for k in (3, 2, 1)]
    p3 = round(sum(r[0] for r in rain), 1) if all(rain) else None
    eve = rain[2][0] if rain[2] else None
    today = days.get(_day(date)) or days.get(_day(date, -1))
    tmins = [days[_day(date, -k)][2] for k in range(0, 8) if _day(date, -k) in days]
    return {"Pluie_3j_mm": p3, "Pluie_veille_mm": eve,
            "Humidite_sol": today[1] if today else None,
            "Tmin_7j_C": min(tmins) if len(tmins) >= 4 else None}


def meteo_class(p3, sm, eve=None):
    if p3 is None and sm is None:
        return None
    if ((p3 is not None and p3 >= RAIN_WET) or (eve is not None and eve >= RAIN_EVE)
            or (sm is not None and sm >= SM_WET)):
        return "humide"
    if (p3 is None or p3 < RAIN_DRY) and (sm is None or sm < SM_DRY):
        return "sec"
    return "frais"


def soil_state(ndvi, low, b11, meteo):
    """État du sol d'une parcelle peu verte (voir le tableau en tête de module)."""
    if ndvi is None or pd.isna(ndvi) or ndvi >= low or b11 is None or pd.isna(b11):
        return NONE
    dark = b11 < SWIR1_WET
    if meteo is None:
        return ETAT_PROBABLE if dark else ETAT_SEC
    if meteo == "humide":
        return ETAT_HUMIDE if dark else ETAT_PROBABLE
    if meteo == "frais":
        return ETAT_HUMIDE if dark else ETAT_SEC
    return ETAT_VERIF if dark else ETAT_SEC


def frost(tmin7):
    if tmin7 is None or pd.isna(tmin7):
        return NONE
    return "Oui" if tmin7 <= FROST_C else "Non"


def reading(ndvi, low, ndti, state, rise):
    """Lecture d'une mesure retenue sur parcelle peu verte (voir l'ordre en tête de module)."""
    if ndvi is None or pd.isna(ndvi) or ndvi >= low:
        return NONE
    if rise is not None and not pd.isna(rise) and rise >= RISE_MIN:
        return LEVEE
    if state in (ETAT_HUMIDE, ETAT_PROBABLE):
        return INDET_HUMIDE
    if state == ETAT_VERIF:
        return INDET_VERIF
    if ndti is None or pd.isna(ndti):
        return INDET
    if ndti >= NDTI_RESIDUE:
        return RESIDUS
    if ndti <= NDTI_BARE:
        return SOL_NU
    return INDET


def rise_at(curve_by_date, date, curve_start):
    """Hausse du NDVI lissé sur RISE_DAYS jours avant la date (None si la courbe commence
    moins de 7 jours avant)."""
    d = pd.Timestamp(date)
    if curve_start is None or (d - curve_start).days < 7 or d not in curve_by_date.index:
        return None
    ref = max(curve_start, d - pd.Timedelta(days=RISE_DAYS))
    return float(curve_by_date[d] - curve_by_date[ref])


def frost_near(days, date):
    """Gel entre FROST_BEFORE_DAYS jours avant et FROST_AFTER_DAYS jours après la date."""
    if not days:
        return False
    vals = [days[k][2] for k in (_day(date, o) for o in range(-FROST_BEFORE_DAYS, FROST_AFTER_DAYS + 1))
            if k in days]
    return any(v <= FROST_C for v in vals)
