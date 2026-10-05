"""
Accès Google Earth Engine : sélection Sentinel-2, masquage nuages/ombres,
composition journalière et statistiques zonales.

Chaîne de traitement d'une date :
  1. Images Sentinel-2 L2A (réflectance de surface, collection harmonisée).
     Pas de repli sur le niveau TOA : NDVI TOA et NDVI SR ne sont pas comparables.
  2. Masque pixel : Cloud Score+ (cs_cdf < seuil) OU classe SCL exclue,
     puis dilatation de quelques dizaines de mètres autour des pixels rejetés
     (bords de nuages et d'ombres, mal détectés).
  3. Composition des images du jour (tuiles qui se recouvrent) par
     qualityMosaic sur le score de clarté : on garde, pour chaque pixel,
     l'observation la plus « propre » (et non la plus verte).
  4. Par parcelle (géométrie déjà réduite par buffer négatif) :
       a. quartiles du NDVI → bornes de Tukey (Q1 - k·IQR, Q3 + k·IQR)
       b. exclusion des pixels hors bornes (valeurs aberrantes)
       c. médiane, moyenne, moyenne pondérée par le score de clarté,
          écart-type, nombre de pixels.
     Le tout est calculé côté serveur en une seule requête getInfo.
"""
import datetime

import ee
import streamlit as st

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
CSPLUS_COLLECTION = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
CS_BAND = "cs_cdf"

# Classes SCL exclues :
#  0 pas de donnée · 1 saturé/défectueux · 3 ombre de nuage
#  8 nuage proba. moyenne · 9 nuage proba. haute · 10 cirrus · 11 neige
# Conservées : 2 zones sombres (sols humides labourés), 4 végétation,
#  5 sol nu, 6 eau, 7 non classé.
SCL_EXCLUDE = [0, 1, 3, 8, 9, 10, 11]

DEFAULT_PARAMS = {
    "cs_threshold": 0.60,   # Cloud Score+ : 0 = nuageux, 1 = parfaitement clair
    "cloud_buffer_m": 20,   # dilatation autour des pixels rejetés
    "iqr_k": 1.5,           # coefficient de Tukey pour les valeurs aberrantes
    "iqr_floor": 0.02,      # IQR minimal (évite d'exclure le bruit naturel
                            # sur une parcelle très homogène)
}

_MAX_RAW = 20000  # médiane/percentiles exacts jusqu'à 20 000 pixels (200 ha)


# ----------------------------------------------------------
# INITIALISATION GEE
# ----------------------------------------------------------
@st.cache_resource
def init_gee(service_account, private_key):
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)


# ----------------------------------------------------------
# PRÉPARATION D'UNE IMAGE : masque + indices
# Important : aucune opération sur collection à l'intérieur des fonctions
# appliquées par map() (sinon « Too many concurrent aggregations »).
# Le score Cloud Score+ est rattaché par linkCollection (jointure).
# ----------------------------------------------------------
def _make_prepare(cs_threshold, cloud_buffer_m):
    """
    Retourne une fonction img -> image avec les bandes :
      NDVI, EVI2, W (score de clarté) : masquées hors pixels clairs
      CLEAR : 1 = clair, 0 = rejeté (masquée hors emprise de l'image)
    """
    n_excl = len(SCL_EXCLUDE)

    def _prepare(img):
        img = ee.Image(img)
        # Image sans Cloud Score+ associé : bande masquée → score 1,
        # seul le masque SCL s'applique alors.
        cs = img.select(CS_BAND).unmask(1)

        scl_bad = img.select("SCL").remap(SCL_EXCLUDE, [1] * n_excl, 0)
        bad = cs.lt(cs_threshold).Or(scl_bad).unmask(0)
        if cloud_buffer_m and cloud_buffer_m > 0:
            bad = bad.focalMax(radius=cloud_buffer_m, kernelType="circle", units="meters")

        footprint = img.select("B4").mask().gt(0)
        clear = bad.Not().And(footprint)

        ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
        nir = img.select("B8").divide(10000)
        red = img.select("B4").divide(10000)
        evi2 = (nir.subtract(red).multiply(2.5)
                .divide(nir.add(red.multiply(2.4)).add(1))
                .rename("EVI2"))

        out = (ndvi.addBands(evi2).addBands(cs.rename("W"))
               .updateMask(clear)
               .addBands(clear.rename("CLEAR").updateMask(footprint)))
        return ee.Image(out.copyProperties(img, ["system:time_start", "system:index"]))

    return _prepare


def _collections(region, start, end, params):
    s2 = (ee.ImageCollection(S2_COLLECTION)
          .filterBounds(region).filterDate(start, end))
    cs = (ee.ImageCollection(CSPLUS_COLLECTION)
          .filterBounds(region).filterDate(start, end))
    prepare = _make_prepare(params["cs_threshold"], params["cloud_buffer_m"])
    return s2, s2.linkCollection(cs, [CS_BAND]).map(prepare)


# ----------------------------------------------------------
# LISTE DES DATES + % DE CIEL CLAIR SUR LES PARCELLES
# Une réduction par image (pas de boucle imbriquée dates × images),
# agrégation par date côté Python.
# ----------------------------------------------------------
@st.cache_data(show_spinner="Recherche des dates disponibles…", ttl=6 * 3600)
def list_dates(start, end, region_key, params_t, _region_geojson):
    """
    start, end : "YYYY-MM-DD" (end inclus)
    region_key / params_t : clés de cache (hashables)
    Retourne une liste triée (récent → ancien) de dicts :
      {"date": datetime.date, "clear_pct": float|None, "n_images": int}
    """
    params = dict(params_t)
    region = ee.Geometry(_region_geojson)
    end_excl = (datetime.date.fromisoformat(end) + datetime.timedelta(days=1)).isoformat()
    _, prepared = _collections(region, start, end_excl, params)

    def _per_image(img):
        clear = img.select("CLEAR")
        common = {"geometry": region, "scale": 20, "maxPixels": 1e9, "tileScale": 4}
        n_clear = clear.reduceRegion(reducer=ee.Reducer.sum(), **common).get("CLEAR")
        n_cov = clear.reduceRegion(reducer=ee.Reducer.count(), **common).get("CLEAR")
        return ee.Feature(None, {
            "date": ee.Date(img.get("system:time_start")).format("YYYY-MM-dd"),
            "n_clear": n_clear, "n_cov": n_cov,
        })

    info = ee.FeatureCollection(prepared.map(_per_image)).getInfo()

    by_date = {}
    for f in info.get("features", []):
        p = f["properties"]
        agg = by_date.setdefault(p["date"], {"clear": 0.0, "cov": 0.0, "n": 0})
        agg["clear"] += p.get("n_clear") or 0
        agg["cov"] += p.get("n_cov") or 0
        agg["n"] += 1

    out = []
    for d, agg in by_date.items():
        out.append({
            "date": datetime.date.fromisoformat(d),
            "clear_pct": round(agg["clear"] / agg["cov"] * 100, 1) if agg["cov"] else None,
            "n_images": agg["n"],
        })
    return sorted(out, key=lambda x: x["date"], reverse=True)


# ----------------------------------------------------------
# STATISTIQUES ZONALES D'UNE DATE
# reduceRegions traite toutes les parcelles en une passe.
#   1. comptages (pixels clairs / total) et quartiles NDVI
#   2. bornes de Tukey par parcelle, rastérisées (reduceToImage)
#   3. stats finales sur les pixels conservés
# ----------------------------------------------------------
def _num_or(f, key, default):
    v = f.get(key)
    return ee.Number(ee.Algorithms.If(ee.Algorithms.IsEqual(v, None), default, v))


def _zonal_stats(day_img, proj, fc, params):
    k = params["iqr_k"]
    floor = params["iqr_floor"]
    rr = {"crs": proj, "tileScale": 2}

    ndvi = day_img.select("NDVI")
    all_px = ee.Image.constant(1).rename("NTOTAL")

    # 1. Comptages + quartiles
    fc1 = (ndvi.rename("NCLEAR").addBands(all_px)
           .reduceRegions(collection=fc, reducer=ee.Reducer.count().unweighted(), **rr))
    fc1 = (day_img.select(["NDVI", "EVI2"])
           .reduceRegions(collection=fc1,
                          reducer=ee.Reducer.percentile([25, 75], maxRaw=_MAX_RAW).unweighted(),
                          **rr))

    # 2. Bornes de Tukey (sans pixel valide : bornes neutres)
    def _bounds(f):
        p25 = _num_or(f, "NDVI_p25", -10)
        p75 = _num_or(f, "NDVI_p75", 10)
        iqr = p75.subtract(p25).max(floor)
        return f.set({"lo": p25.subtract(iqr.multiply(k)),
                      "hi": p75.add(iqr.multiply(k))})

    fc1 = fc1.map(_bounds)
    lo = fc1.reduceToImage(["lo"], ee.Reducer.first()).rename("lo").unmask(-10)
    hi = fc1.reduceToImage(["hi"], ee.Reducer.first()).rename("hi").unmask(10)
    keep = ndvi.gte(lo).And(ndvi.lte(hi))
    filt = day_img.select(["NDVI", "EVI2", "W"]).updateMask(keep)

    # 3. Statistiques finales
    final_red = (ee.Reducer.median(maxRaw=_MAX_RAW)
                 .combine(ee.Reducer.mean(), sharedInputs=True)
                 .combine(ee.Reducer.stdDev(), sharedInputs=True)
                 .combine(ee.Reducer.count(), sharedInputs=True)
                 .unweighted())
    fc2 = filt.select(["NDVI", "EVI2"]).reduceRegions(collection=fc1, reducer=final_red, **rr)
    fc3 = (filt.select("NDVI").multiply(filt.select("W")).rename("WNDVI")
           .addBands(filt.select("W"))
           .reduceRegions(collection=fc2, reducer=ee.Reducer.sum().unweighted(), **rr))

    # Sans géométrie dans la réponse (plus léger)
    return fc3.map(lambda f: ee.Feature(None, f.toDictionary()))


@st.cache_data(show_spinner=False, ttl=6 * 3600, max_entries=200)
def compute_day_stats(date_str, geoms_key, params_t, _geojsons, _region_geojson):
    """
    date_str  : "YYYY-MM-DD" (date d'acquisition présente dans list_dates)
    _geojsons : liste de géométries d'analyse (après buffer), None si inexploitable
    Retourne {"stats": {idx: propriétés brutes}, "satellites": [..]}
    """
    params = dict(params_t)
    region = ee.Geometry(_region_geojson)
    d0 = ee.Date(date_str)
    s2, prepared = _collections(region, d0, d0.advance(1, "day"), params)

    proj = s2.first().select("B4").projection()  # grille native S2 (UTM, 10 m)
    day_img = prepared.select(["NDVI", "EVI2", "W"]).qualityMosaic("W")

    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry(gj), {"idx": i})
        for i, gj in enumerate(_geojsons) if gj is not None
    ])

    stats_info = _zonal_stats(day_img, proj, fc, params).getInfo()
    satellites = s2.aggregate_array("SPACECRAFT_NAME").distinct().getInfo()

    stats = {}
    for f in stats_info["features"]:
        props = f["properties"]
        props["n_clear"] = props.get("NCLEAR")
        props["n_total"] = props.get("NTOTAL")
        stats[int(props["idx"])] = props
    return {"stats": stats, "satellites": satellites}
