"""
Bandeau du projet, en haut de chaque page : fichier, parcelles, période, dates analysées,
paramètres de calcul verrouillés, état d'enregistrement, boutons Exporter et Enregistrer.
"""
import streamlit as st

from ndvi_app.config import APP_VERSION
from ndvi_app.core.session_format import build_session_zip
from ndvi_app.exports.excel import XLSX_MIME, to_excel
from ndvi_app.exports.excel_charts import MAX_CHARTS, add_parcel_charts
from ndvi_app.ui import analysis
from ndvi_app.ui.columns import DETAIL_COLS, SYNTH_COLS, TECH_COLS, pick
from ndvi_app.ui.formatting import fr_decimal
from ndvi_app.ui.state import session_lock, stale_warning


def pivot_all(detail):
    """NDVI des mesures retenues : une ligne par parcelle, une colonne par date."""
    return (detail[detail["Retenue"] == "Oui"]
            .assign(Date=lambda x: x["Date"].dt.strftime("%Y-%m-%d"))
            .pivot_table(index="ID", columns="Date", values="NDVI", aggfunc="first"))


def pivot_for(pivot, ids):
    """Tableau NDVI par parcelle (lignes, dans l'ordre de ids) et par date (colonnes)."""
    out = pivot.reindex(ids).reset_index()
    out.columns.name = None
    return out


@st.cache_data(max_entries=4, show_spinner="Préparation du fichier Excel…")
def _excel_bytes(token, ids, skey, _res, _column_help):
    res = _res
    low, high = skey[2], skey[3]
    synth_view = pick(res.synthese, SYNTH_COLS)
    detail_view = pick(res.detail.assign(Date=res.detail["Date"].dt.date), DETAIL_COLS)
    courbes_view = res.courbes.assign(Date=res.courbes["Date"].dt.date)[
        ["ID", "Date", "NDVI_lisse", "Phase", "Incertain"]]
    return to_excel(
        {"Synthèse": synth_view,
         "NDVI par date": pivot_for(pivot_all(res.detail), list(res.synthese["ID"])),
         "Détail": detail_view, "Courbes lissées": courbes_view,
         "Technique": pick(res.df_long, TECH_COLS)},
        _column_help,
        extra=lambda book: add_parcel_charts(book, res.synthese, res.detail, res.courbes,
                                             res.p_start, res.p_end, low, high))


@st.cache_data(max_entries=4, show_spinner="Préparation du fichier d'enregistrement…")
def _session_zip(token, ids, settings_t, id_field, source_name, file_hash,
                 _features, _geoinfo, _raws, _calc, _period, _errors):
    s = dict(settings_t)
    calc_params = dict(_calc["params_t"])
    return build_session_zip(
        meta={
            "app_version": APP_VERSION, "source_file": source_name, "file_hash": file_hash,
            "id_field": id_field,
            "geoms_key": _calc["geoms_key"], "params": calc_params,
            "settings": {
                "thresholds": [s["low"], s["high"]],
                "buffer_m": int(_calc["geoms_key"].rsplit("|", 1)[1]),
                "cs_threshold": calc_params["cs_threshold"],
                "cloud_buffer_m": calc_params["cloud_buffer_m"],
                "iqr_k": calc_params["iqr_k"],
                "min_clear": int(s["min_clear"]), "min_pixels": int(s["min_pixels"]),
                "min_weight": float(s["min_weight"]), "smooth_days": int(s["smooth_days"]),
            },
            "period": list(_period),
            "errors": _errors,
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
        st.markdown(" · ".join(parts), width="content")
        if res is None:
            st.badge("Pas encore d'analyse temporelle", color="gray")
            return
        if ss.get("mt_dirty"):
            st.badge("Modifications non enregistrées", icon=":material/warning:", color="orange")
        else:
            st.badge("Enregistrée", icon=":material/check:", color="green")
        st.space("stretch")
        ids = tuple(p.ids)
        skey = analysis.settings_key(s)
        with st.popover("Exporter", icon=":material/download:"):
            st.download_button(
                "Excel : synthèse, graphiques par parcelle, NDVI par date, détail, courbes, technique",
                data=_excel_bytes(ss.mt_token, ids, skey, res, ctx.column_help),
                file_name=f"ndvi_temporel_{res.p_start}_{res.p_end}.xlsx", mime=XLSX_MIME,
                key="mt_dl", icon=":material/table_view:")
            if len(res.synthese) > MAX_CHARTS:
                st.caption(f"Graphiques Excel limités aux {MAX_CHARTS} premières parcelles.")
            st.caption("Cartes KML et HTML : page Carte.")
        settings_t = tuple(sorted({"low": s.low, "high": s.high, "min_clear": s.min_clear,
                                   "min_pixels": s.min_pixels, "min_weight": s.min_weight,
                                   "smooth_days": s.smooth_days}.items()))
        st.download_button(
            "Enregistrer", type="primary", icon=":material/save:",
            data=_session_zip(ss.mt_token, ids, settings_t, p.id_field if p.fields else None,
                              p.source_name, p.file_hash, p.features, ss.mt_geoinfo, ss.mt_raw,
                              ss.mt_ctx, ss.mt_period, ss.get("mt_errors", [])),
            file_name=f"analyse_ndvi_{res.p_start}_{res.p_end}.zip", mime="application/zip",
            key="mt_save", on_click=analysis.mark_saved,
            help="Fichier .zip avec les réglages, les résultats de chaque date et les contours "
                 "des parcelles, à rouvrir plus tard sans recalcul (page Projet).")
    stale_warning(ss.mt_ctx, ctx.calc_context())
