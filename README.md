# Application NDVI — analyse parcellaire Sentinel-2

Application Streamlit + Google Earth Engine pour suivre la couverture des sols
à la parcelle à partir du NDVI Sentinel-2.

## Interface (v4.0)

Menu en haut de page, barre latérale de réglages commune à toutes les pages :
- **Projet** : fichier de parcelles ou analyse enregistrée, période, recherche et choix des
  dates, lancement ou complément de l'analyse ;
- **Vue d'ensemble** : chiffres clés, filtres et tableau de toutes les parcelles, avec la barre
  d'évolution des phases et la mini-courbe NDVI, les valeurs NDVI par date ou la synthèse
  détaillée ; un clic sur une ligne ouvre la fiche ;
- **Fiche parcelle** : courbe NDVI, NDTI, vignette satellite et mesures d'une parcelle ;
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

| NDVI (médiane) | Phase |
|---|---|
| < 0,25 | Sol nu |
| 0,25 – 0,50 | Couvert peu développé |
| ≥ 0,50 | Couvert bien développé |

## Analyse temporelle (ndvi_app/core/timeseries.py)

1. Mesures retenues : statut OK, poids de fiabilité ≥ 0,5, pas de chute isolée
   (valeur inférieure de plus de 0,15 à ses voisines avant et après, à ±15 jours).
2. Courbe lissée journalière : moyenne pondérée à noyau gaussien (6 jours par défaut),
   poids réduit pour les points sous la courbe ; pas d'extrapolation.
3. Phases de la courbe, épisodes de moins de 10 jours fusionnés, baisses rapides
   (> 0,20 en 15 jours : destruction, récolte ou gel).
4. Confiance selon le nombre de mesures retenues et le plus long trou sans mesure.

## NDTI — résidus de culture (expérimental, v2.5)

NDTI = (B11 − B12) / (B11 + B12), médiane par parcelle, calculé dans la même requête
que le NDVI. Interprétable seulement si la parcelle est peu verte (NDVI < seuil bas).
Indicateur « Sol_humide » (réflectance B11 < 0,15 sur parcelle peu verte) : l'humidité
réduit fortement le contraste NDTI. Aucun seuil d'interprétation tant que le calibrage
terrain (sol nu / cannes de maïs / chaumes) n'est pas fait.

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
