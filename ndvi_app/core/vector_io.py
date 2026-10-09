"""
Lecture des fichiers de parcelles (Python pur, sans Streamlit).

Formats acceptés :
  - ZIP contenant un shapefile (.shp, .shx, .dbf et .prj) : reprojeté en WGS84
    d'après le .prj (Lambert-93, CC48…) ;
  - GeoJSON (WGS84).
Le cache Streamlit (même fichier → pas de nouvelle lecture) est ajouté côté interface,
dans ndvi_app/ui/loading.py.
"""
import json
import os
import shutil
import tempfile
import zipfile

import pyproj
import shapefile
from shapely.geometry import shape
from shapely.ops import transform


def load_vector_from_bytes(file_bytes, filename):
    """
    file_bytes : contenu du fichier déposé
    filename   : nom du fichier (l'extension .zip désigne un shapefile zippé,
                 tout autre nom un GeoJSON)
    Retourne une liste de {"geometry": shapely (WGS84), "properties": dict}.
    """
    suffix = ".zip" if filename.endswith(".zip") else ".geojson"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(file_bytes)
        tmp.close()
        if suffix == ".geojson":
            return _read_geojson(tmp.name)
        return _read_zipped_shapefile(tmp.name)
    finally:
        os.unlink(tmp.name)


def _read_geojson(path):
    with open(path, "r") as f:
        data = json.load(f)
    return [{"geometry": shape(feat["geometry"]), "properties": feat.get("properties", {})}
            for feat in data["features"]]


def _read_zipped_shapefile(path):
    extract = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(path, "r") as z:
            z.extractall(extract)

        shp = [f for f in os.listdir(extract) if f.endswith(".shp")][0]
        shp_path = os.path.join(extract, shp)

        with shapefile.Reader(shp_path) as sf:
            fields = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
            shapes = sf.shapes()
            records = sf.records()

        transformer = _to_wgs84(shp_path.replace(".shp", ".prj"))
        features = []
        for shp_rec, rec in zip(shapes, records):
            geom = shape(shp_rec.__geo_interface__)
            if transformer:
                geom = transform(transformer, geom)
            features.append({"geometry": geom, "properties": dict(zip(fields, rec))})
        return features
    finally:
        shutil.rmtree(extract, ignore_errors=True)


def _to_wgs84(prj_path):
    """Fonction de reprojection vers WGS84 d'après le .prj, None si absent ou déjà WGS84."""
    if not os.path.exists(prj_path):
        return None
    with open(prj_path, "r") as f:
        wkt = f.read()
    try:
        src = pyproj.CRS.from_wkt(wkt)
        if src.to_epsg() is None or src.to_epsg() != 4326:
            dst = pyproj.CRS.from_epsg(4326)
            return pyproj.Transformer.from_crs(src, dst, always_xy=True).transform
    except Exception:  # .prj illisible : coordonnées laissées telles quelles (contrôle ensuite)
        pass
    return None
