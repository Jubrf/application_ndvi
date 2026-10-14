"""
Résultats de l'analyse temporelle partagés par toutes les pages.

Les résultats bruts Earth Engine sont en session (mt_raw…) avec un jeton (mt_token) qui change
à chaque calcul, complétion ou réouverture. L'interprétation (statuts, courbes, synthèse) et les
exports sont mis en cache sur ce jeton et sur les réglages : changer de page ne recalcule rien.
"""
import uuid
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from ndvi_app.core.quality import build_rows
from ndvi_app.core.timeseries import analyse_all
from ndvi_app.ui import weather


@dataclass
class TemporalResults:
    df_long: pd.DataFrame       # une ligne par parcelle et par date
    synthese: pd.DataFrame      # une ligne par parcelle
    detail: pd.DataFrame        # mesures avec statut retenue / écartée
    courbes: pd.DataFrame       # courbe lissée journalière
    p_start: object             # période analysée (datetime.date)
    p_end: object


def store(raws, period, calc, geoinfo, errors, dirty=True, token=None):
    """Enregistre en session de nouveaux résultats bruts."""
    st.session_state.mt_raw = raws
    st.session_state.mt_period = period
    st.session_state.mt_ctx = calc
    st.session_state.mt_geoinfo = geoinfo
    st.session_state.mt_errors = errors
    st.session_state.mt_token = token or uuid.uuid4().hex
    st.session_state.mt_dirty = dirty


def has_results():
    return bool(st.session_state.get("mt_raw"))


def settings_key(settings):
    """Réglages qui changent l'interprétation (clé de cache)."""
    return (settings.min_pixels, settings.min_clear, settings.low, settings.high,
            tuple(sorted(settings.ts_settings.items())))


@st.cache_data(max_entries=8, show_spinner="Interprétation des mesures…")
def _interpret(token, ids, skey, p_start, p_end, wkey, _geoinfo, _raws, _wdays):
    min_pixels, min_clear, low, high, ts_items = skey
    rows = []
    for date_str, raw in _raws:
        rows += build_rows(list(ids), _geoinfo, raw, date_str, min_pixels, min_clear, low, high,
                           weather_days=_wdays)
    df_long = pd.DataFrame(rows)
    synthese, detail, courbes = analyse_all(df_long, p_start, p_end, low, high, dict(ts_items),
                                            weather_days=_wdays)
    return df_long, synthese, detail, courbes


def results(ctx):
    """Résultats interprétés avec les réglages actuels (None si aucune analyse)."""
    if ctx is None or not has_results():
        return None
    ss = st.session_state
    if not ss.get("mt_token"):
        ss.mt_token = uuid.uuid4().hex
    p_start, p_end = ss.mt_period
    df_long, synthese, detail, courbes = _interpret(
        ss.mt_token, tuple(ctx.parcels.ids), settings_key(ctx.settings), p_start, p_end,
        weather.key(), ss.mt_geoinfo, ss.mt_raw, weather.days())
    return TemporalResults(df_long, synthese, detail, courbes, p_start, p_end)


def mark_saved():
    st.session_state.mt_dirty = False
