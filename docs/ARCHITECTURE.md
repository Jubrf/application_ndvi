# Architecture

Application Streamlit d'analyse NDVI parcellaire à partir de Sentinel-2 via Google Earth Engine.
Ce document décrit l'organisation du code depuis la v3.0 (restructuration de la v2.7.2,
sans changement de résultat).

## Principe

Trois couches, de la plus stable à la plus changeante :

1. **Calculs** (`ndvi_app/core`) : Python pur, sans Streamlit ni Earth Engine.
   Testables isolément, réutilisables avec un autre fournisseur d'images.
2. **Accès Earth Engine** (`ndvi_app/earth_engine`) : le seul paquet qui importe `ee`.
   Il renvoie des types Python simples (dict, list, str).
3. **Interface** (`ndvi_app/ui`) et **fichiers produits** (`ndvi_app/exports`).

Les dépendances vont dans un seul sens : `ui` → `exports`, `earth_engine`, `core` → `config`.
`core` n'importe jamais `ui`, `exports` ou `earth_engine`.

```
streamlit_app.py            point d'entrée (fichier principal sur Streamlit Cloud)
ndvi_app/
  config.py                 constantes partagées : version, phases, couleurs, statuts,
                            réglages par défaut, mois
  logs.py                   journal du serveur (« Manage app » sur Streamlit Cloud)
  core/
    vector_io.py            lecture SHP zippé (reprojection d'après le .prj) ou GeoJSON
    geometry.py             buffer intérieur adaptatif, zone de calcul, contours
    quality.py              statistiques brutes → mesure : statut, poids de fiabilité, phase
    timeseries.py           analyse temporelle : sélection, lissage, phases, synthèse
    session_format.py       format du fichier .zip « analyse enregistrée »
  earth_engine/
    sentinel2.py            collections, masque nuages, dates disponibles,
                            statistiques zonales, vignettes (avec cache Streamlit)
  exports/
    excel.py                classeur Excel + onglet Lexique
    excel_charts.py         graphiques Excel natifs par parcelle
    kml.py                  carte KML (Google My Maps, Google Earth)
    folium_map.py           carte folium (affichage dans l'appli et export HTML)
  ui/
    app.py                  page : en-tête, connexion GEE, réglages, chargement, onglets
    state.py                clés de st.session_state, valeurs par défaut, restauration
    context.py              Settings (réglages), Parcels (parcelles), Context (les deux)
    sidebar.py              barre latérale → Settings
    loading.py              choix de la source, fichier ou analyse enregistrée → Parcels
    tab_date.py             onglet « Analyse à une date »
    tab_temporal.py         onglet « Analyse temporelle »
    maps.py                 carte des phases + boutons KML / HTML
    thumbnail.py            vignette satellite d'une parcelle
    charts.py               graphiques Altair (courbe NDVI, panneau NDTI, frise des dates)
    columns.py              colonnes des tableaux et leurs définitions
    formatting.py           mise en forme des nombres et des libellés de dates
```

## Déroulement d'un passage

Streamlit réexécute tout le script à chaque interaction.

1. `app.main()` : en-tête, puis connexion Earth Engine (mise en cache : une seule fois par serveur).
2. `state.init_settings()` : applique une restauration en attente (analyse enregistrée
   ouverte au passage précédent), puis les valeurs par défaut des réglages.
3. `sidebar.render_sidebar()` → `Settings`.
4. `loading.load_parcels(settings)` → `Parcels`. Le script s'arrête tant qu'aucun fichier
   exploitable n'est chargé.
5. Les deux onglets reçoivent un `Context` (réglages + parcelles) : aucune variable globale.

Les résultats Earth Engine bruts sont gardés en session (`os_raw`, `mt_raw`) avec le contexte
de calcul qui les a produits (`os_ctx`, `mt_ctx` : géométries + paramètres du masque).
L'interprétation (statuts, phases, courbes) est recalculée à chaque passage à partir de ces
résultats bruts. Ainsi, les seuils et réglages de courbe s'appliquent sans nouvelle requête,
et un changement de paramètre de calcul est signalé (`state.stale_warning`).

## Chaîne de calcul d'une date

```
fichier → core.vector_io → core.geometry.prepare_all (buffer)
        → earth_engine.sentinel2.compute_day_stats  (1 requête GEE pour toutes les parcelles)
        → core.quality.build_rows                   (statut, poids, phase par parcelle)
        → core.timeseries.analyse_all               (onglet temporel : courbes et synthèse)
        → ui (tableaux, graphiques, cartes) et exports (Excel, KML, HTML, .zip)
```

## Analyse enregistrée (.zip)

`core/session_format.py`. Le fichier contient :
- `session.json` : réglages, période et résultats GEE bruts de chaque date ;
- `parcelles.geojson` : contours et identifiants.

À l'ouverture, `ui/loading.py` programme la restauration (`state.restore_payload`), puis
relance le script. Aucune requête Earth Engine n'est faite. Le format est versionné
(`FORMAT_VERSION`) : une version plus récente de l'appli doit continuer à lire les anciens
fichiers.

## Où modifier quoi

| Besoin | Fichier |
|---|---|
| Nouvelle version affichée | `config.py` (`APP_VERSION`) |
| Seuils, couleurs, libellés de phases | `config.py` |
| Masque nuages, collections, indices calculés | `earth_engine/sentinel2.py` |
| Statut d'une mesure, poids de fiabilité | `core/quality.py` |
| Lissage, épisodes, confiance | `core/timeseries.py` |
| Colonnes et définitions des tableaux | `ui/columns.py` |
| Contenu des onglets | `ui/tab_date.py`, `ui/tab_temporal.py` |

## Changer de fournisseur d'images (piste)

Seul `earth_engine/sentinel2.py` dépend de Google Earth Engine. Un autre fournisseur
(openEO, Sentinel Hub, lecture directe des COG) devrait fournir les mêmes fonctions :
- `list_dates(start, end, region_key, params_t, region_geojson)` → `[{"date", "clear_pct", "cover_pct", "n_images"}]`
- `compute_day_stats(date_str, geoms_key, params_t, geojsons, region_geojson)` → `{"stats": {idx: {...}}, "satellites": [...]}`
- `parcel_thumbnail(...)` → URL d'image

Il n'y aurait alors rien à changer dans `core`, `exports` ou `ui`.

## Déploiement

Streamlit Cloud, fichier principal `streamlit_app.py`. Secrets : `GEE_SERVICE_ACCOUNT`,
`GEE_PRIVATE_KEY`. Après une modification des modules du paquet `ndvi_app`, utiliser
« Reboot app » : Streamlit Cloud garde sinon en mémoire les anciens modules importés.
