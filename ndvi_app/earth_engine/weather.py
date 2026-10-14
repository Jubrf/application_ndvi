"""
Météo journalière ERA5-Land (réanalyse ECMWF, maille ≈ 11 km) via Google Earth Engine.

Une seule requête par période : pour chaque jour, pluie (mm), humidité du sol entre 0 et
7 cm (m³/m³) et température minimale (°C), moyennées sur les mailles dont le centre est à
moins de 8 km du centre de la zone des parcelles (au moins une maille, au plus quatre en
général). Une seule série pour toutes les parcelles : la maille est plus grande que la zone.

Publication avec quelques jours de retard : les derniers jours d'une période récente
peuvent manquer (signalés comme tels dans l'appli).
"""
import datetime
import time

import ee
import streamlit as st

from ndvi_app.logs import log

ERA5_COLLECTION = "ECMWF/ERA5_LAND/DAILY_AGGR"
BANDS = ["total_precipitation_sum", "volumetric_soil_water_layer_1", "temperature_2m_min"]
ERA5_SCALE_M = 11132
NEIGHBOURHOOD_M = 8000      # ≥ demi-diagonale d'une maille : toujours au moins une maille
SOURCE = "ERA5-Land (ECMWF), Google Earth Engine"


@st.cache_data(ttl=6 * 3600, show_spinner="Récupération de la météo (ERA5-Land)…")
def daily_weather(start, end, region_key, _region_geojson):
    """
    start, end : "YYYY-MM-DD" (end inclus) ; region_key : clé de cache de la zone.
    Retourne {"source", "start", "end", "days": {"YYYY-MM-DD": [pluie_mm, humidite_sol,
    tmin_c]}} ; jours absents = pas encore publiés.
    """
    t0 = time.time()
    end_excl = (datetime.date.fromisoformat(end) + datetime.timedelta(days=1)).isoformat()
    area = ee.Geometry(_region_geojson).centroid(100).buffer(NEIGHBOURHOOD_M)
    col = ee.ImageCollection(ERA5_COLLECTION).filterDate(start, end_excl).select(BANDS)
    table = col.getRegion(area, ERA5_SCALE_M).getInfo()
    header, rows = table[0], table[1:]
    i_time = header.index("time")
    i_b = [header.index(b) for b in BANDS]
    acc = {}
    for r in rows:
        day = datetime.datetime.fromtimestamp(r[i_time] / 1000, datetime.timezone.utc).date().isoformat()
        vals = [r[i] for i in i_b]
        if any(v is None for v in vals):
            continue
        acc.setdefault(day, []).append(vals)
    days = {}
    for day, vals in sorted(acc.items()):
        n = len(vals)
        tp, sm, tmin = (sum(v[k] for v in vals) / n for k in range(3))
        days[day] = [round(tp * 1000, 1), round(sm, 3), round(tmin - 273.15, 1)]
    log(f"Météo ERA5-Land {start} → {end} : {len(days)} jours en {time.time() - t0:.1f} s")
    return {"source": SOURCE, "start": start, "end": end, "days": days}
