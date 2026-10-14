"""
Météo ERA5-Land de l'analyse temporelle (st.session_state.mt_weather) :
récupérée une fois pour la période (plus WX_MARGIN_DAYS jours avant, pour la pluie des
3 jours précédents, le gel des 7 à 10 jours précédents), enregistrée dans le fichier .zip.
Une seule requête Earth Engine par période ; un échec n'empêche pas l'analyse (lecture
sur l'infrarouge moyen seul, comme avant).
"""
import datetime

import streamlit as st

from ndvi_app.earth_engine import weather as era5
from ndvi_app.logs import log

WX_MARGIN_DAYS = 10


def needed_period(p_start, p_end):
    return (str(p_start - datetime.timedelta(days=WX_MARGIN_DAYS)), str(p_end))


def fetch(ctx, start, end):
    """Météo journalière de la zone des parcelles ; {"days": {}, "error": …} en cas d'échec."""
    try:
        return era5.daily_weather(start, end, ctx.parcels.file_hash, ctx.parcels.region)
    except Exception as e:      # météo facultative : l'analyse continue sans elle
        log(f"Météo ERA5-Land indisponible : {type(e).__name__}: {e}")
        return {"source": era5.SOURCE, "start": start, "end": end, "days": {},
                "error": f"{type(e).__name__}: {e}"[:300]}


def ensure(ctx):
    """Récupère la météo de l'analyse temporelle si elle manque ou ne couvre pas la période
    (nouvelle analyse, complétion, analyse enregistrée avant la v4.2)."""
    ss = st.session_state
    if ctx is None or not ss.get("mt_raw"):
        return
    start, end = needed_period(*ss.mt_period)
    w = ss.get("mt_weather")
    if w and (w.get("start"), w.get("end")) == (start, end):
        return
    ss.mt_weather = fetch(ctx, start, end)
    ss.mt_dirty = True          # le fichier enregistré n'a pas (encore) cette météo


def refresh(ctx):
    """Nouvelle requête (derniers jours publiés depuis la précédente)."""
    era5.daily_weather.clear()
    st.session_state.pop("mt_weather", None)
    ensure(ctx)


def days():
    """Météo journalière de l'analyse temporelle ({jour: [pluie, humidité, tmin]}) ou None."""
    w = st.session_state.get("mt_weather")
    return (w or {}).get("days") or None


def key():
    """Clé de cache des résultats interprétés : change quand la météo change."""
    w = st.session_state.get("mt_weather") or {}
    d = w.get("days") or {}
    return (w.get("start"), w.get("end"), len(d), max(d) if d else None)


def status(p_end):
    """(niveau, texte) sur la météo de l'analyse temporelle, ou None."""
    w = st.session_state.get("mt_weather")
    if not w:
        return None
    if w.get("error"):
        return ("warning", "Météo ERA5-Land indisponible : état du sol estimé sur l'infrarouge "
                           "moyen seul.")
    d = w.get("days") or {}
    if not d:
        return ("warning", "Météo ERA5-Land : aucun jour publié sur la période.")
    last = datetime.date.fromisoformat(max(d))
    if last < p_end:
        return ("info", f"Météo ERA5-Land disponible jusqu'au {last:%d/%m/%Y} (publication "
                        "avec quelques jours de retard) : les mesures suivantes sont lues sur "
                        "l'infrarouge moyen seul.")
    return ("ok", f"Météo ERA5-Land sur toute la période ({w.get('source', era5.SOURCE)}).")
