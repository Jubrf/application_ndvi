"""
Enregistrement / réouverture d'une analyse temporelle (sans recalcul Earth Engine).

Fichier .zip contenant :
  session.json      : réglages, période, géométries d'analyse, résultats bruts par date
  parcelles.geojson : contours des parcelles (WGS84) avec leur identifiant (propriété ID),
                      réutilisable aussi comme fichier de parcelles pour une nouvelle analyse
"""
import datetime
import io
import json
import zipfile

from shapely.geometry import mapping

from ndvi_app.core.geometry import strip_z

FORMAT = "ndvi-session"
FORMAT_VERSION = 1


def build_session_zip(meta, features, ids, geoinfo, raws):
    """
    meta     : dict (app_version, source_file, file_hash, id_field, settings, params,
               geoms_key, period [date, date], errors)
    features : parcelles chargées (géométries shapely WGS84)
    ids      : identifiants (même ordre)
    geoinfo  : sortie de geometry.prepare_all au moment du calcul
    raws     : [(date_str, résultat earth_engine.sentinel2.compute_day_stats)]
    """
    p0, p1 = meta["period"]
    session = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        **{k: v for k, v in meta.items() if k != "period"},
        "period": [str(p0), str(p1)],
        "geoinfo": geoinfo,
        "results": [
            {"date": d, "satellites": r.get("satellites", []),
             "stats": {str(k): v for k, v in r["stats"].items()}}
            for d, r in raws
        ],
    }
    parcels = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"ID": pid},
             "geometry": mapping(strip_z(f["geometry"]))}
            for f, pid in zip(features, ids)
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("session.json", json.dumps(session, ensure_ascii=False))
        z.writestr("parcelles.geojson", json.dumps(parcels, ensure_ascii=False))
    return buf.getvalue()


def read_session_zip(data):
    """Retourne (session: dict, parcelles_geojson: bytes). Lève ValueError si invalide."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if not {"session.json", "parcelles.geojson"} <= names:
                raise ValueError("ce fichier n'est pas une analyse enregistrée par l'application "
                                 "(session.json ou parcelles.geojson absent).")
            session = json.loads(z.read("session.json").decode("utf-8"))
            parcels = z.read("parcelles.geojson")
    except zipfile.BadZipFile as e:
        raise ValueError("fichier ZIP illisible.") from e
    if session.get("format") != FORMAT:
        raise ValueError("format de session non reconnu.")
    if session.get("format_version", 0) > FORMAT_VERSION:
        raise ValueError("analyse enregistrée par une version plus récente de l'application.")

    session["period"] = [datetime.date.fromisoformat(d) for d in session["period"]]
    session["raws"] = [
        (r["date"], {"satellites": r.get("satellites", []),
                     "stats": {int(k): v for k, v in r["stats"].items()}})
        for r in session.pop("results")
    ]
    return session, parcels
