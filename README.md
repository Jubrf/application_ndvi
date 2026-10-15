# Application NDVI — analyse parcellaire Sentinel-2

Application Streamlit + Google Earth Engine pour suivre la couverture des sols
à la parcelle à partir du NDVI Sentinel-2.

> Branche `v5-maquette-react` : nouvelle interface React servie par une API Python (même
> moteur de calcul), voir [docs/V5_REACT.md](docs/V5_REACT.md).

## Interface (v4)

Menu en haut de page, barre latérale de réglages commune à toutes les pages :
- **Projet** : fichier de parcelles ou analyse enregistrée, période, recherche et choix des
  dates, lancement ou complément de l'analyse ;
- **Vue d'ensemble** : chiffres clés, filtres et tableau de toutes les parcelles, avec pour
  chacune la courbe NDVI au-dessus de la barre des phases (même échelle de temps, mois repérés,
  v4.1), les valeurs NDVI par date ou la synthèse détaillée ; un clic sur une ligne ouvre la
  fiche ;
- **Fiche parcelle** : courbe NDVI, météo (pluie, gel, humidité du sol), NDTI, lecture du
  sol, vignette satellite (couleurs naturelles ou fausses couleurs infrarouges : végétation
  verte en rouge vif, utile pour un couvert jeune ou des adventices), aide à la lecture (règle simplifiée, cas de la parcelle surligné)
  et mesures d'une parcelle ;
- **Carte** : carte des phases à une date, exports KML et HTML ;
- **Une date** : analyse ponctuelle à une date.

Le bandeau en haut de chaque page rappelle le fichier, la période et les dates analysées,
indique si l'analyse est enregistrée, et porte les boutons « Exporter » (Excel) et
« Enregistrer » (.zip).

## Méthode de calcul (par parcelle et par date)

1. **Images** : Sentinel-2 L2A, réflectance de surface (`COPERNICUS/S2_SR_HARMONIZED`).
   Pas de repli sur le niveau TOA, dont le NDVI n'est pas comparable.
2. **Masque pixel** : pixel rejeté si Cloud Score+ (`cs_cdf`) < seuil (0,60 par défaut)
   ou si sa classe SCL est exclue (pas de donnée, saturé, ombre, nuages, cirrus, neige),
   avec une marge de 20 m autour des pixels rejetés.
3. **Composition du jour** : `qualityMosaic` sur le score de clarté (pixel le plus clair,
   pas le plus vert) quand plusieurs tuiles couvrent la zone.
4. **Géométrie** : buffer intérieur de 10 m (réduit à 5 puis 0 m si la parcelle
   devient trop petite), calcul sur la grille native Sentinel-2 (UTM, 10 m),
   pixels retenus si leur centre est dans la parcelle.
5. **Valeurs aberrantes** : exclusion des pixels hors [Q1 − 1,5·IQR ; Q3 + 1,5·IQR].
6. **Indicateurs** : médiane (par défaut), moyenne, moyenne pondérée par le score
   de clarté, écart-type, EVI2 médian, nombre de pixels.
7. **Statut qualité** : mesure exploitable si ≥ 50 % de pixels clairs et ≥ 10 pixels
   utilisés (paramétrable).

## Interprétation (seuils réglables dans la barre latérale)

**Zone grise « à vérifier »** (v4.2.3, désactivée par défaut) : interrupteur et bornes dans la
barre latérale (0,20–0,25 par défaut, à caler sur le terrain). Activée, une parcelle dont le
NDVI lissé à la dernière mesure retenue est dans la plage est signalée « à vérifier » (colonne
A_verifier, carte de chiffres et filtre de la vue d'ensemble, badge et aide à la lecture de la
fiche). Désactivée, rien n'est calculé ni affiché. Le réglage est enregistré avec l'analyse.

Le bandeau indique « Réglages modifiés, non enregistrés » quand un réglage d'interprétation
(seuils, zone grise, qualité, lissage) a changé depuis l'enregistrement ou l'ouverture : le
fichier .zip garde les réglages du moment où il a été enregistré.

| NDVI (médiane) | Phase |
|---|---|
| < 0,25 | Sol nu |
| 0,25 – 0,50 | Couvert peu développé |
| ≥ 0,50 | Couvert bien développé |

## Analyse temporelle (ndvi_app/core/timeseries.py)

1. Mesures retenues : statut OK, poids de fiabilité ≥ 0,5. Une mesure basse (inférieure de
   plus de 0,15 à ses voisines avant et après, à ±15 jours) n'est écartée que si un nuage est
   probable (v4.3) :
   - signe de nuage : parcelle en partie masquée (< 100 % de pixels clairs), score de clarté
     Cloud Score+ < 0,75, ou nuages sur le secteur ce jour-là (la moitié des parcelles à moins
     de 2 km masquées ou en baisse, s'il y en a au moins 5 ; sinon 30 % des parcelles du jour
     en partie masquées) ;
   - plusieurs mesures basses consécutives (fauche, récolte puis repousse) : gardées, sauf si
     toutes portent un signe de nuage ;
   - mesure basse seule : écartée avec un signe de nuage, sinon gardée et signalée
     (« chute ponctuelle sans nuage détecté »).
   Les analyses enregistrées sont réinterprétées avec cette règle à l'ouverture (pas de
   nouvel appel Earth Engine).
2. Courbe lissée journalière : moyenne pondérée à noyau gaussien (6 jours par défaut),
   poids réduit pour les points sous la courbe ; pas d'extrapolation.
3. Phases de la courbe, épisodes de moins de 10 jours fusionnés, baisses rapides
   (> 0,20 en 15 jours : destruction, récolte ou gel).
4. Confiance selon le nombre de mesures retenues et le plus long trou sans mesure.

## NDTI — résidus de culture (expérimental, v2.5)

NDTI = (B11 − B12) / (B11 + B12), médiane par parcelle, calculé dans la même requête
que le NDVI. Interprétable seulement si la parcelle est peu verte (NDVI < seuil bas).
L'humidité réduit fortement le contraste NDTI : il n'est lu que sur sol sec (voir
ci-dessous). Repères indicatifs (≥ 0,10 résidus, ≤ 0,05 sol nu) en attendant le calibrage
terrain (sol nu / cannes de maïs / chaumes).

## Météo et lecture du sol (v4.2, ndvi_app/core/soil_reading.py)

Météo journalière **ERA5-Land** (ECMWF, maille ≈ 11 km) récupérée dans Earth Engine, **une
requête par analyse** (`ECMWF/ERA5_LAND/DAILY_AGGR`) : pluie, humidité du sol 0–7 cm,
température minimale. Enregistrée dans le fichier .zip ; récupérée à la réouverture d'une
analyse plus ancienne. Publication avec quelques jours de retard : les dernières mesures
peuvent être lues sans météo (signalé, bouton « Actualiser la météo » sur la fiche).

Pour chaque mesure retenue d'une parcelle **peu verte** (NDVI < seuil bas) :

1. **Météo** : pluie des 3 jours précédents, pluie de la veille, humidité du sol du jour.
   Humide si pluie 3 j ≥ 10 mm, veille ≥ 5 mm ou humidité ≥ 0,40 ; sec si pluie 3 j < 2 mm
   et humidité < 0,32 ; frais sinon.
2. **État du sol** : l'infrarouge moyen B11 voit la surface au passage, la météo confirme.

   | | B11 < 0,15 (sombre) | B11 ≥ 0,15 (clair) |
   |---|---|---|
   | météo humide | Humide | Humide probable |
   | météo fraîche | Humide | Sec |
   | météo sèche | À vérifier | Sec |
   | sans météo | Humide probable | Sec |

3. **Lecture**, dans cet ordre : *Levée possible* (NDVI lissé + 0,05 en 14 jours) ;
   *Indéterminé (sol humide)* ou *(humidité à vérifier)* : NDTI non lu ; *Résidus probables*
   (NDTI ≥ 0,10) ; *Sol nu probable* (NDTI ≤ 0,05) ; *Indéterminé* entre les deux.
4. **Gel** : température minimale ≤ −2 °C dans les 7 jours ; une baisse rapide précédée de
   gel est marquée « (gel) » (couvert gelé plutôt que détruit).

Colonnes Etat_sol, Lecture, Pluie_3j_mm, Humidite_sol, Tmin_7j_C, Gel (détail),
Lecture_fin et Etat_sol_fin (synthèse, vue d'ensemble), onglet Excel « Météo », graphique
pluie / gel / humidité sous la courbe de la fiche parcelle. **Tous les seuils sont
indicatifs, à calibrer avec les relevés terrain** (constantes en tête de
`core/soil_reading.py`). Une météo indisponible n'empêche pas l'analyse : l'état du sol
repose alors sur B11 seul.

## Enregistrer / rouvrir une analyse (ndvi_app/core/session_format.py)

Bouton « Enregistrer » du bandeau : fichier .zip contenant
`session.json` (réglages, période, résultats bruts de chaque date) et
`parcelles.geojson` (contours + identifiant, réutilisable comme fichier de parcelles).
« Ouvrir une analyse enregistrée » (page Projet) restaure réglages et résultats
sans requête Earth Engine. Supprimer une analyse = supprimer le fichier.

**Compléter une analyse enregistrée (v3.1)** : après l'avoir rouverte, choisir un mois de
début plus tôt et/ou un mois de fin plus tard, puis « Rechercher les dates disponibles ».
Seules les dates pas encore analysées sont proposées, avec le filtre de ciel clair, et une frise
montre ce qui est déjà analysé et ce qui manque. « Compléter l'analyse » ne calcule que ces
dates, après confirmation, puis « Enregistrer » enregistre la version complétée. Le buffer et le masque
nuages sont verrouillés sur ceux de l'analyse ouverte, pour que toutes les dates soient
calculées de la même façon.

## Organisation du code

Voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) : calculs (`ndvi_app/core`), accès
Earth Engine (`ndvi_app/earth_engine`), exports (`ndvi_app/exports`) et interface (`ndvi_app/ui`).

## Déploiement

Secrets Streamlit requis : `GEE_SERVICE_ACCOUNT`, `GEE_PRIVATE_KEY`.
Après une mise à jour du code : « Reboot app » dans Streamlit Cloud.
Streamlit 1.65 ou plus récent ; thème dans `.streamlit/config.toml`.
