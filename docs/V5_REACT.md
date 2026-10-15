# Version React (v5) — branche `v5-maquette-react`

Même moteur Python que la version Streamlit (`ndvi_app/core`, `ndvi_app/exports`), nouvelle
interface : liste des parcelles et fiche côte à côte, changements de page instantanés.
La version Streamlit (branche `main`) reste en service pendant la transition.

## Organisation

```
api/            service web (FastAPI) : appelle le moteur, sert l'interface
  main.py         routes, mot de passe, fichiers statiques
  engine.py       pont avec ndvi_app (interprétation, Excel, enregistrement, cartes)
  requirements.txt
web/            interface React, sans étape de compilation
  index.html, styles.css, app.js
  vendor/         React 18.3.1, ReactDOM, htm 3.1.1 (fichiers npm d'origine, licences incluses)
render.yaml     description du service Render
```

`ndvi_app` sert aux deux interfaces : le classeur Excel (`exports/workbook.py`) et le bloc
de réglages du fichier d'enregistrement (`core/session_format.saved_settings`) sont communs,
ce qui garantit des fichiers identiques entre Streamlit et la v5.

## Étape 1 (actuelle) : rouvrir une analyse enregistrée

- Page Projet : ouvrir un .zip créé par « Enregistrer » (Streamlit ou v5).
- Revue des parcelles, Tableaux, Carte : comme la maquette 3, avec les vrais calculs.
- Réglages d'interprétation (seuils, zone grise, pixels clairs, pixels minimum, poids,
  lissage) : couleurs mises à jour aussitôt dans le navigateur, puis recalcul par le moteur.
- Exports : Excel, Enregistrer (.zip), cartes KML et HTML à la date choisie.
- Pas d'appel à Earth Engine : vignettes satellite, nouvelle analyse, ajout de dates,
  météo manquante et « Analyse à une date » arrivent à l'étape 2.

Le serveur garde les analyses ouvertes en mémoire ; s'il a redémarré (mise en veille de
Render), l'interface lui renvoie le fichier automatiquement.

## Lancer en local

```
pip install -r api/requirements.txt
APP_PASSWORD=motdepasse uvicorn api.main:app --reload
```
puis http://localhost:8000 (identifiant quelconque, mot de passe ci-dessus).

## Render

Créé une fois : New → Blueprint → dépôt `Jubrf/application_ndvi`, branche
`v5-maquette-react`. Variables demandées à la création :
- `APP_PASSWORD` : mot de passe d'accès (obligatoire : sans lui le service refuse tout) ;
- `GEE_SERVICE_ACCOUNT`, `GEE_PRIVATE_KEY` : mêmes valeurs que les secrets Streamlit
  (utilisées à l'étape 2).

Offre gratuite : mise en veille après 15 min sans visite, réveil en une minute environ ;
machine modeste, le recalcul après un changement de réglage y prend plus de temps qu'en local.
