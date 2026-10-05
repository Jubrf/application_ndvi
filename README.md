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

## Interprétation (indicateur NDVI)

| NDVI | Interprétation |
|---|---|
| < 0,20 | Sol nu ou couvert non levé |
| 0,20 – 0,25 | Sol nu ou couvert levant |
| 0,25 – 0,50 | Couvert en développement |
| ≥ 0,50 | Couvert établi |

## Déploiement

Secrets Streamlit requis : `GEE_SERVICE_ACCOUNT`, `GEE_PRIVATE_KEY`.
