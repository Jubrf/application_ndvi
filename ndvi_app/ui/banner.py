"""
Bandeau du projet, en haut de chaque page : fichier, parcelles, période, dates analysées,
paramètres de calcul verrouillés, état d'enregistrement, boutons Exporter et Enregistrer.
"""
import pandas as pd
import streamlit as st

from ndvi_app.config import APP_VERSION
from ndvi_app.core.session_format import build_session_zip, saved_settings
from ndvi_app.exports import workbook
from ndvi_app.exports.excel import XLSX_MIME
from ndvi_app.exports.excel_charts import MAX_CHARTS
from ndvi_app.ui import analysis, weather
from ndvi_app.ui.columns import DETAIL_COLS, SYNTH_COLS, TECH_COLS, pick
from ndvi_app.ui.formatting import fr_decimal
from ndvi_app.ui.state import session_lock, stale_warning


@st.cache_data(max_entries=4, show_spinner="Préparation du fichier Excel…")
def weather_table(days, p_start, p_end):
    """Météo journalière de la période (onglet Excel « Météo »)."""
    return workbook.weather_table(days, p_start, p_end)


def _excel_bytes(token, ids, skey, wkey, _res, _column_help, _wdays):
    res = _res
    low, high = skey[2], skey[3]
    return workbook.results_workbook(
        res.df_long, res.synthese, res.detail, res.courbes, res.p_start, res.p_end, low, high,
        _column_help, pick, {"synth": SYNTH_COLS, "detail": DETAIL_COLS, "tech": TECH_COLS},
        weather_days=_wdays,
        weather_df=weather_table(_wdays, res.p_start, res.p_end) if _wdays else None)


@st.cache_data(max_entries=4, show_spinner="Préparation du fichier d'enregistrement…")
def _session_zip(token, ids, settings_t, id_field, source_name, file_hash, wkey,
                 _features, _geoinfo, _raws, _calc, _period, _errors, _weather):
    s = dict(settings_t)
    calc_params = dict(_calc["params_t"])
    return build_session_zip(
        meta={
            "app_version": APP_VERSION, "source_file": source_name, "file_hash": file_hash,
            "id_field": id_field,
            "geoms_key": _calc["geoms_key"], "params": calc_params,
            "settings": saved_settings(s, calc_params, _calc["geoms_key"]),
            "period": list(_period),
            "errors": _errors,
            # météo enregistrée seulement si la requête a abouti (sinon nouvel essai à l'ouverture)
            "weather": _weather if _weather and not _weather.get("error") else None,
        },
        features=_features, ids=list(ids), geoinfo=_geoinfo, raws=_raws)


def render(app):
    """Bandeau ; rien tant qu'aucune parcelle n'est chargée."""
    ctx = app.ctx
    if ctx is None:
        return
    p, s, ss = ctx.parcels, ctx.settings, st.session_state
    res = app.results
    lock = session_lock()
    with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
        parts = [f":material/folder: **{app.project.file_name}**", f"{len(p.features)} parcelles"]
        if res is not None:
            parts += [f"{res.p_start:%d/%m/%Y} → {res.p_end:%d/%m/%Y}", f"{len(ss.mt_raw)} dates analysées"]
        if lock:
            params = dict(ss.mt_ctx["params_t"])
            parts.append(f":material/lock: buffer {ss.mt_ctx['geoms_key'].rsplit('|', 1)[1]} m · "
                         f"Cloud Score+ {fr_decimal(params['cs_threshold'])} · "
                         f"marge {params['cloud_buffer_m']} m")
        wx = weather.status(res.p_end) if res is not None else None
        if wx is not None and wx[0] != "ok":
            last = max(weather.days()) if weather.days() else None
            parts.append(":material/rainy: météo " + (f"jusqu'au {last[8:10]}/{last[5:7]}" if last
                                                      else "indisponible"))
        st.markdown(" · ".join(parts), width="content")
        if res is None:
            st.badge("Pas encore d'analyse temporelle", color="gray")
            return
        settings_t = tuple(sorted({"low": s.low, "high": s.high, "min_clear": s.min_clear,
                                   "min_pixels": s.min_pixels, "min_weight": s.min_weight,
                                   "smooth_days": s.smooth_days,
                                   **({"grey": s.grey} if s.grey else {})}.items()))
        # Réglages d'interprétation de la version enregistrée (ou ouverte) : un seuil changé
        # depuis rend le fichier différent de ce qui est affiché
        if not ss.get("mt_dirty") and ss.get("mt_saved_settings") is None:
            ss.mt_saved_settings = settings_t
        if ss.get("mt_dirty"):
            st.badge("Modifications non enregistrées", icon=":material/warning:", color="orange")
        elif ss.get("mt_saved_settings") != settings_t:
            st.badge("Réglages modifiés, non enregistrés", icon=":material/tune:", color="orange",
                     help="Un réglage d'interprétation (seuils, zone grise, qualité, lissage) a "
                          "changé depuis l'enregistrement : les tableaux affichés ne sont plus "
                          "ceux du fichier. « Enregistrer » pour garder ces réglages.")
        else:
            st.badge("Enregistrée", icon=":material/check:", color="green")
        st.space("stretch")
        ids = tuple(p.ids)
        skey = analysis.settings_key(s)
        with st.popover("Exporter", icon=":material/download:"):
            st.download_button(
                "Excel : synthèse, graphiques par parcelle, NDVI par date, détail, courbes, technique",
                data=_excel_bytes(ss.mt_token, ids, skey, weather.key(), res, ctx.column_help,
                                  weather.days()),
                file_name=f"ndvi_temporel_{res.p_start}_{res.p_end}.xlsx", mime=XLSX_MIME,
                key="mt_dl", icon=":material/table_view:")
            if len(res.synthese) > MAX_CHARTS:
                st.caption(f"Graphiques Excel limités aux {MAX_CHARTS} premières parcelles.")
            st.caption("Cartes KML et HTML : page Carte.")
        st.download_button(
            "Enregistrer", type="primary", icon=":material/save:",
            data=_session_zip(ss.mt_token, ids, settings_t, p.id_field if p.fields else None,
                              p.source_name, p.file_hash, weather.key(), p.features,
                              ss.mt_geoinfo, ss.mt_raw, ss.mt_ctx, ss.mt_period,
                              ss.get("mt_errors", []), ss.get("mt_weather")),
            file_name=f"analyse_ndvi_{res.p_start}_{res.p_end}.zip", mime="application/zip",
            key="mt_save", on_click=analysis.mark_saved, args=(settings_t,),
            help="Fichier .zip avec les réglages, les résultats de chaque date et les contours "
                 "des parcelles, à rouvrir plus tard sans recalcul (page Projet).")
    stale_warning(ss.mt_ctx, ctx.calc_context())
