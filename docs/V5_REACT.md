# Version React (v5) — branche `v5-maquette-react`

Même moteur Python que la version Streamlit (`ndvi_app/core`, `ndvi_app/exports`,
`ndvi_app/earth_engine`), nouvelle interface : liste des parcelles et fiche côte à côte,
changements de page instantanés. La version Streamlit reste en service pendant la transition.

## Organisation

```
api/            service web (FastAPI) : appelle le moteur, sert l'interface
  main.py         routes, mot de passe, fichiers statiques
  engine.py       analyse ouverte : interprétation, Excel, enregistrement, cartes
  projects.py     Earth Engine : parcelles, dates, calcul en tâche de fond, météo, vignettes,
                  analyse à une date (mêmes appels que les pages Streamlit)
  gee.py          connexion Earth Engine (variables d'environnement, clé nettoyée)
  requirements.txt
web/            interface React, sans étape de compilation
  index.html, styles.css
  js/             core, map, charts, tables, review, overview, onedate, project, app
  vendor/         React 18.3.1, ReactDOM, htm 3.1.1 (fichiers npm d'origine, licences incluses)
render.yaml     description du service Render
```

Code commun aux deux interfaces : classeur Excel (`exports/workbook.py`), bloc de réglages
du fichier (`core/session_format.saved_settings`), cache des appels Earth Engine
(`ndvi_app/cache.py` : `st.cache_data` sous Streamlit, cache mémoire dans l'API), période
météo (`earth_engine/weather.needed_period`). Les fichiers produits (analyse .zip, Excel,
cartes, analyse à une date) sont identiques à ceux de Streamlit (banc de tests).

## Fonctions

- Projet : état de la connexion Earth Engine ; ouvrir une analyse enregistrée ; nouvelle
  analyse (parcelles, identifiant, paramètres de calcul, période, dates avec présélection
  par ciel clair, calcul en tâche de fond avec progression et arrêt) ; compléter l'analyse
  ouverte (un ou plusieurs mois au choix, avant, pendant ou après la période : seules les
  dates pas encore analysées sont proposées ; paramètres verrouillés) ; dates analysées ;
  fermer l'analyse.
- Revue des parcelles, Tableaux, Carte : interprétation par le moteur, réglages dans le
  panneau « Réglages » ; fiche avec courbe, météo (pluie, gel, humidité du sol), NDTI,
  vignette Sentinel-2 (naturelles / infrarouge), aide à la lecture, mesures.
  Valeurs du jour survolé affichées sous chaque graphique (sinon celles du repère bleu).
- Analyse à une date : image d'une date sur l'analyse ouverte ou les parcelles chargées.
- Exports : Excel, Enregistrer (.zip), cartes KML et HTML.
- Météo absente d'un fichier : récupérée à l'ouverture (bouton « Actualiser la météo »).
- Conservation dans le navigateur (IndexedDB) : l'analyse ouverte et l'état de l'interface
  reviennent après une actualisation de la page ; « Fermer l'analyse » les efface.

Le serveur garde les analyses et fichiers de parcelles en mémoire ; s'il a redémarré
(mise en veille de Render), l'interface lui renvoie le fichier automatiquement. Un calcul
en cours est perdu si le serveur redémarre pendant le calcul.

## Lancer en local

```
pip install -r api/requirements.txt
APP_PASSWORD=motdepasse GEE_SERVICE_ACCOUNT=... GEE_PRIVATE_KEY="$(cat cle.json)" \
  uvicorn api.main:app --reload
```
puis http://localhost:8000 (identifiant quelconque, mot de passe ci-dessus).

## Render

Créé une fois : New → Blueprint → dépôt `Jubrf/application_ndvi`, branche
`v5-maquette-react`. Variables (onglet Environment) :
- `APP_PASSWORD` : mot de passe d'accès (obligatoire : sans lui le service refuse tout) ;
- `GEE_SERVICE_ACCOUNT`, `GEE_PRIVATE_KEY` : mêmes valeurs que les secrets Streamlit. La
  clé peut être le fichier JSON du compte de service ou la clé PEM ; guillemets autour et
  « \n » écrits en toutes lettres sont corrigés. La page Projet affiche l'état de la
  connexion et le message d'erreur d'Earth Engine.

Offre gratuite : mise en veille après 15 min sans visite, réveil en une minute environ ;
machine modeste, le recalcul après un changement de réglage y prend plus de temps qu'en local.
