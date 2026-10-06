# Application NDVI — analyse parcellaire Sentinel-2

Application Streamlit + Google Earth Engine pour suivre la couverture des sols
à la parcelle à partir du NDVI Sentinel-2.

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

## Analyse temporelle (utils/timeseries.py)

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

## Enregistrer / rouvrir une analyse (utils/session_io.py)

Bouton « Enregistrer l'analyse » de l'onglet temporel : fichier .zip contenant
`session.json` (réglages, période, résultats bruts de chaque date) et
`parcelles.geojson` (contours + identifiant, réutilisable comme fichier de parcelles).
« Ouvrir une analyse enregistrée » en haut de page restaure réglages et résultats
sans requête Earth Engine. Supprimer une analyse = supprimer le fichier.

## Déploiement

Secrets Streamlit requis : `GEE_SERVICE_ACCOUNT`, `GEE_PRIVATE_KEY`.
