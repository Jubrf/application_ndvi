"""
Préparation des géométries d'analyse (côté Python, sans GEE).

- Buffer négatif en mètres (projection UTM locale) pour exclure les pixels
  de bordure (haies, chemins, parcelles voisines) qui biaisent le NDVI.
- Buffer adaptatif : si la parcelle devient trop petite, on réduit le buffer
  (buffer → buffer/2 → 0) pour conserver assez de pixels.
- Conservation des trous (îlots non cultivés à l'intérieur d'une parcelle).
"""
from pyproj import CRS, Transformer
from shapely.geometry import mapping
from shapely.ops import transform, unary_union

# Surface minimale après buffer pour accepter un niveau de buffer donné.
# 1 500 m² ≈ 15 pixels Sentinel-2 à 10 m.
MIN_AREA_M2 = 1500

# Tolérance de simplification (m) : réduit la taille des requêtes GEE
# sans effet mesurable à la résolution de 10 m.
SIMPLIFY_M = 1.0


def _strip_z(geom):
    if not geom.has_z:
        return geom
    return transform(lambda x, y, z=None: (x, y), geom)


def utm_epsg(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _transformers(lon, lat):
    utm = CRS.from_epsg(utm_epsg(lon, lat))
    wgs = CRS.from_epsg(4326)
    to_utm = Transformer.from_crs(wgs, utm, always_xy=True).transform
    to_wgs = Transformer.from_crs(utm, wgs, always_xy=True).transform
    return to_utm, to_wgs


def prepare_analysis_geometry(geom_wgs84, buffer_m=10, min_area_m2=MIN_AREA_M2):
    """
    Retourne un dict :
      geojson   : géométrie d'analyse (WGS84) ou None si inexploitable
      buffer_m  : buffer réellement appliqué (m, valeur positive)
      area_ha   : surface d'origine (ha)
      area_analysis_ha : surface après buffer (ha)
    """
    geom = _strip_z(geom_wgs84)
    c = geom.centroid
    to_utm, to_wgs = _transformers(c.x, c.y)

    g_utm = transform(to_utm, geom).buffer(0)  # répare les géométries invalides
    area_m2 = g_utm.area

    candidates = []
    if buffer_m and buffer_m > 0:
        candidates = [buffer_m, buffer_m / 2]
    candidates.append(0)

    chosen, chosen_buf = None, 0
    for b in candidates:
        g = g_utm.buffer(-b) if b > 0 else g_utm
        if g.is_empty:
            continue
        if b == 0 or g.area >= min_area_m2:
            chosen, chosen_buf = g, b
            break

    if chosen is None or chosen.is_empty:
        return {"geojson": None, "buffer_m": 0,
                "area_ha": round(area_m2 / 1e4, 2), "area_analysis_ha": 0.0}

    chosen = chosen.simplify(SIMPLIFY_M, preserve_topology=True)
    return {
        "geojson": mapping(transform(to_wgs, chosen)),
        "buffer_m": chosen_buf,
        "area_ha": round(area_m2 / 1e4, 2),
        "area_analysis_ha": round(chosen.area / 1e4, 2),
    }


def prepare_all(features, buffer_m=10):
    return [prepare_analysis_geometry(f["geometry"], buffer_m) for f in features]


def region_geojson(features, tolerance_deg=1e-4):
    """Union simplifiée des parcelles (≈10 m) : zone de calcul du % de ciel clair."""
    geoms = [_strip_z(f["geometry"]).buffer(0) for f in features]
    union = unary_union(geoms).simplify(tolerance_deg, preserve_topology=True)
    return mapping(union)


def looks_like_wgs84(features):
    for f in features:
        minx, miny, maxx, maxy = f["geometry"].bounds
        if not (-180 <= minx <= 180 and -180 <= maxx <= 180
                and -90 <= miny <= 90 and -90 <= maxy <= 90):
            return False
    return True
