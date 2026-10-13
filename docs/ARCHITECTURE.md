# Architecture

Application Streamlit d'analyse NDVI parcellaire à partir de Sentinel-2 via Google Earth Engine.
Ce document décrit l'organisation du code depuis la v3.0 (restructuration de la v2.7.2,
sans changement de résultat) et l'interface en pages de la v4.0 (mêmes résultats que la v3.1).

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
    completion.py           compléter une analyse : dates à ajouter, fusion des résultats
  earth_engine/
    sentinel2.py            collections, masque nuages, dates disponibles,
                            statistiques zonales, vignettes (avec cache Streamlit)
  exports/
    excel.py                classeur Excel + onglet Lexique
    excel_charts.py         graphiques Excel natifs par parcelle
    kml.py                  carte KML (Google My Maps, Google Earth)
    folium_map.py           carte folium (affichage dans l'appli et export HTML)
  ui/
    app.py                  connexion GEE, réglages, chargement, navigation entre pages
    state.py                clés de st.session_state, valeurs par défaut, restauration
    context.py              Settings (réglages), Parcels (parcelles), Context (les deux)
    sidebar.py              barre latérale (Interprétation, Calcul) → Settings
    loading.py              fichier de parcelles ou analyse enregistrée → Project, sans widget
    analysis.py             analyse temporelle en session, interprétation mise en cache
    banner.py               bandeau du projet : état, Exporter (Excel), Enregistrer (.zip)
    temporal_setup.py       période, recherche et sélection des dates, lancer / compléter
    views/
      projet.py             page Projet : source, fichier, étapes, période et dates
      ensemble.py           page Vue d'ensemble : chiffres clés, filtres, trois tableaux
      fiche.py              page Fiche parcelle : courbe, vignette, mesures
      carte.py              page Carte : carte des phases à une date, exports KML / HTML
      une_date.py           page Une date : analyse ponctuelle (ancien onglet)
    assets/                 logo et icône de l'appli (SVG)
    maps.py                 carte des phases + boutons KML / HTML
    thumbnail.py            vignette satellite d'une parcelle
    charts.py               graphiques Altair (courbe NDVI, panneau NDTI, frise des dates),
                            barre des phases et mini-courbe de la vue d'ensemble
    columns.py              colonnes des tableaux et leurs définitions
    formatting.py           mise en forme des nombres et des libellés de dates
```

Hors du paquet : `.streamlit/config.toml` (thème : couleur principale verte).

## Déroulement d'un passage

Streamlit réexécute tout le script à chaque interaction.

1. `app.main()` : configuration de la page, logo, connexion Earth Engine (mise en cache :
   une seule fois par serveur).
2. `state.init_settings()` : applique une restauration en attente (analyse enregistrée
   ouverte au passage précédent), puis les valeurs par défaut des réglages.
3. `sidebar.render_sidebar()` → `Settings`.
4. `loading.load_project(settings)` → `Project` (parcelles et messages). Le fichier choisi
   est gardé en session (`proj_new`, `proj_open`) : il reste chargé quand on change de page.
5. `st.navigation` (menu en haut) choisit la page ; `banner.render` affiche le bandeau ;
   la page reçoit un `AppView` (projet, `Context`, pages, résultats) : aucune variable globale.
   Sans parcelles chargées, les autres pages renvoient vers la page Projet.

## Pages (v4.0)

| Page | Adresse | Contenu |
|---|---|---|
| Projet | `/projet` | source, fichier, identifiant, étapes, période et dates à analyser |
| Vue d'ensemble | `/vue-ensemble` | 5 chiffres clés, filtres (phase, confiance, recherche), présentations « Évolution des phases » (barre des phases + mini-courbe), « Valeurs NDVI par date », « Synthèse détaillée » ; un clic sur une ligne ouvre la fiche |
| Fiche parcelle | `/fiche-parcelle` | parcelle précédente / suivante parmi celles filtrées, courbe NDVI, NDTI, vignette, mesures |
| Carte | `/carte` | carte des phases à une date choisie sur la frise, exports KML / HTML |
| Une date | `/une-date` | analyse ponctuelle, inchangée |

Les réglages des pages (filtres, présentation, parcelle affichée, date de la carte) sont
gardés quand on change de page (`persist_state="session"`).

Résultats temporels (`ui/analysis.py`) : `analysis.store` enregistre les résultats bruts et
leur donne un jeton (`mt_token`, ou l'empreinte du fichier pour une analyse rouverte).
`analysis.results(ctx)` recalcule l'interprétation en cache (`st.cache_data`), d'après le
jeton et les réglages d'interprétation : changer de page ne relance aucun calcul. Les fichiers
Excel et .zip du bandeau sont mis en cache de la même façon. `mt_dirty` signale une analyse
modifiée et pas encore enregistrée (badge du bandeau).

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

## Compléter une analyse (v3.1)

`ui/temporal_setup.py` (`_extend_mode`, `_select_and_extend`) et `core/completion.py`. Une fois une analyse affichée
(rouverte depuis un .zip ou faite dans la séance), on peut choisir une période qui contient
celle déjà analysée. La recherche des dates ne propose alors que les dates pas encore analysées,
avec le même filtre de ciel clair. Une frise (`ui/charts.coverage_timeline`) montre :
- les dates déjà analysées ;
- les nouvelles dates, sélectionnées ou non ;
- la période déjà analysée ;
- les trous de plus de 20 jours.

Seules les dates sélectionnées sont envoyées à Earth Engine. Elles sont calculées avec le
contexte de calcul (`mt_ctx`) et les géométries (`mt_geoinfo`) de l'analyse existante, puis
fusionnées avec les résultats déjà présents (`completion.merge_results`). En v4, une
fenêtre de confirmation rappelle le nombre de dates à calculer avant l'envoi. Une date en erreur qui est recalculée disparaît
de la liste des erreurs.

Quand une analyse enregistrée est ouverte, le buffer et le masque nuages sont verrouillés sur
ses valeurs (`state.session_lock`), et la période ne peut qu'être agrandie. Les seuils, la
qualité minimale et la courbe restent réglables. Le format du fichier .zip est inchangé.

## Où modifier quoi

| Besoin | Fichier |
|---|---|
| Nouvelle version affichée | `config.py` (`APP_VERSION`) |
| Seuils, couleurs, libellés de phases | `config.py` |
| Masque nuages, collections, indices calculés | `earth_engine/sentinel2.py` |
| Statut d'une mesure, poids de fiabilité | `core/quality.py` |
| Lissage, épisodes, confiance | `core/timeseries.py` |
| Colonnes et définitions des tableaux | `ui/columns.py` |
| Contenu d'une page | `ui/views/*.py` |
| Période, recherche et sélection des dates | `ui/temporal_setup.py` |
| Bandeau, fichiers Excel et .zip | `ui/banner.py` |
| Couleur principale de l'interface | `.streamlit/config.toml` |

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
La v4 demande Streamlit 1.65 ou plus récent (`requirements.txt`).
