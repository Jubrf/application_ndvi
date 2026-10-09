"""
Onglet « Analyse temporelle » : période, dates, calcul, puis résultats
(synthèse, courbe d'une parcelle, carte des phases à une date, exports, enregistrement).
"""
import datetime
import hashlib

import pandas as pd
import streamlit as st

from ndvi_app.config import APP_VERSION, COLOR_INVALID, COLOR_MAP, FIRST_YEAR, MONTHS_FR
from ndvi_app.core.quality import build_rows, colorize
from ndvi_app.core.session_format import build_session_zip
from ndvi_app.core.timeseries import analyse_all
from ndvi_app.exports.excel import XLSX_MIME, to_excel
from ndvi_app.exports.excel_charts import MAX_CHARTS, add_parcel_charts
from ndvi_app.ui.charts import dates_timeline, ndti_chart, parcel_chart
from ndvi_app.ui.columns import DETAIL_COLS, SYNTH_COLS, TECH_COLS, column_config, pick
from ndvi_app.ui.context import Context
from ndvi_app.ui.formatting import date_label, fmt, fr_decimal, usable
from ndvi_app.ui.maps import phase_map
from ndvi_app.ui.state import stale_warning
from ndvi_app.ui.thumbnail import satellite_view

NO_PHASE = "Aucune courbe"
NO_IMAGE_STATUSES = ["Hors image", "Géométrie inexploitable"]


def render(ctx: Context):
    st.header("Analyse NDVI — série temporelle")
    period_start, period_end = _period()
    _select_and_run(ctx, period_start, period_end)
    if st.session_state.get("mt_raw"):
        _results(ctx)


# ------------------------------------------------------------------ 1. période
def _month_picker(label, key, default, today):
    c1, c2 = st.columns(2)
    st.session_state.setdefault(f"{key}_y", default.year)
    st.session_state.setdefault(f"{key}_m", default.month)
    with c1:
        y = st.selectbox(f"{label} — année", list(range(today.year, FIRST_YEAR - 1, -1)),
                         key=f"{key}_y")
    with c2:
        m = st.selectbox(f"{label} — mois", list(range(1, 13)), key=f"{key}_m",
                         format_func=lambda v: MONTHS_FR[v - 1])
    return y, m


def _period():
    """Mois de début et de fin → (premier jour, dernier jour, au plus aujourd'hui)."""
    st.subheader("1. Période")
    today = datetime.date.today()
    dflt_start = (today.replace(day=1) - datetime.timedelta(days=62)).replace(day=1)
    ys, ms = _month_picker("Début", "mt_start", dflt_start, today)
    ye, me = _month_picker("Fin", "mt_end", today, today)
    period_start = datetime.date(ys, ms, 1)
    period_end = min((datetime.date(ye + 1, 1, 1) if me == 12 else datetime.date(ye, me + 1, 1))
                     - datetime.timedelta(days=1), today)

    if period_start > period_end:
        st.error("Le mois de début doit précéder le mois de fin.")
        st.stop()
    st.caption(f"Période analysée : du {period_start:%d/%m/%Y} au {period_end:%d/%m/%Y} "
               f"({(period_end - period_start).days + 1} jours).")
    return period_start, period_end


# ------------------------------------------------------------------ 2. dates et calcul
def _select_and_run(ctx: Context, period_start, period_end):
    min_clear = ctx.settings.min_clear
    if st.button("🔍 Rechercher les dates disponibles", key="mt_btn_search"):
        st.session_state.mt_query = (period_start, period_end)

    if st.session_state.get("mt_query") != (period_start, period_end):
        return
    try:
        dates = ctx.list_dates(period_start, period_end)
    except Exception as e:
        st.error(f"Erreur Earth Engine (recherche des dates) : {type(e).__name__} — {e}")
        return
    if not dates:
        st.info("Aucune image Sentinel-2 sur cette période.")
        return

    st.subheader("2. Dates à analyser")
    presel = st.slider(
        "Présélection : ciel clair minimum sur l'ensemble des parcelles (%)",
        0, 100, 20, 10, key="mt_presel",
        help="Une date partiellement nuageuse peut rester exploitable pour une partie "
             "des parcelles : le contrôle se fait ensuite parcelle par parcelle, "
             "et les mesures douteuses sont écartées de la courbe.",
    )
    default = [d["date"] for d in dates if usable(d, presel)]
    by_date = {d["date"]: d for d in dates}
    sel = st.multiselect(
        f"{len(dates)} date(s) trouvée(s), {len(default)} présélectionnée(s)",
        options=[d["date"] for d in dates], default=default,
        format_func=lambda x: date_label(by_date[x], min_clear),
        key=f"mt_multisel_{presel}_{period_start}_{period_end}",
    )
    if not sel:
        st.info("Sélectionne au moins une date.")
        return

    st.caption(f"{len(sel)} date(s) × {len(ctx.parcels.features)} parcelles — une requête "
               f"Earth Engine par date, environ 2 à 5 s chacune (instantané si déjà calculée).")
    if st.button("▶️ Lancer l'analyse temporelle", key="mt_btn_run"):
        raws, errors = [], []
        bar = st.progress(0.0, text="Initialisation…")
        for i, d in enumerate(sorted(sel)):
            bar.progress(i / len(sel), text=f"{d:%d/%m/%Y} ({i + 1}/{len(sel)})…")
            try:
                raws.append((str(d), ctx.run_analysis(str(d))))
            except Exception as e:
                errors.append(f"{d:%d/%m/%Y} : {type(e).__name__} — {e}")
        bar.empty()
        st.session_state.mt_raw = raws
        st.session_state.mt_period = (period_start, period_end)
        st.session_state.mt_ctx = ctx.calc_context()
        st.session_state.mt_geoinfo = ctx.parcels.geoinfo
        st.session_state.mt_errors = errors


# ------------------------------------------------------------------ 3. résultats
def _results(ctx: Context):
    s, p = ctx.settings, ctx.parcels
    stale_warning(st.session_state.mt_ctx, ctx.calc_context())
    for err in st.session_state.get("mt_errors", []):
        st.warning(f"Date non traitée — {err}")
    p_start, p_end = st.session_state.mt_period

    rows = []
    for date_str, raw in st.session_state.mt_raw:
        rows += build_rows(p.ids, st.session_state.mt_geoinfo, raw, date_str,
                           s.min_pixels, s.min_clear, s.low, s.high)
    df_long = pd.DataFrame(rows)
    synthese, detail, courbes = analyse_all(df_long, p_start, p_end, s.low, s.high, s.ts_settings)

    n_kept = int(synthese["Mesures_retenues"].sum())
    st.success(f"Du {p_start:%d/%m/%Y} au {p_end:%d/%m/%Y} — {df_long['Date'].nunique()} date(s) "
               f"× {len(synthese)} parcelles — {n_kept} mesures retenues pour les courbes")

    synth_view, pid_list = _synthesis_table(ctx, synthese)
    _parcel_detail(ctx, synthese, detail, courbes, pid_list, p_start, p_end)
    _phase_map_section(ctx, synthese, detail, courbes, p_start, p_end)

    pivot = (detail[detail["Retenue"] == "Oui"]
             .assign(Date=lambda x: x["Date"].dt.strftime("%Y-%m-%d"))
             .pivot_table(index="ID", columns="Date", values="NDVI", aggfunc="first")
             .reindex(pid_list).reset_index())
    pivot.columns.name = None
    with st.expander("NDVI par parcelle et par date (mesures retenues)"):
        st.dataframe(pivot, hide_index=True)

    _excel_export(ctx, synth_view, pivot, synthese, detail, courbes, df_long, p_start, p_end)
    _save_button(ctx, p_start, p_end)


def _synthesis_table(ctx: Context, synthese):
    """Tableau de synthèse filtrable ; un clic sur une ligne choisit la parcelle affichée.
    Retourne (tableau complet affiché, liste des parcelles proposées)."""
    st.subheader("Synthèse par parcelle")
    synth_view = pick(synthese, SYNTH_COLS)
    phase_vals = synth_view["Phase_fin"].fillna(NO_PHASE)
    c1, c2 = st.columns(2)
    with c1:
        ph_opts = [ph for ph in list(COLOR_MAP) + [NO_PHASE] if ph in set(phase_vals)]
        f_phase = st.multiselect("Filtrer : phase en fin de période", ph_opts, default=ph_opts,
                                 key="mt_f_phase")
    with c2:
        cf_opts = [c for c in ["Bonne", "Moyenne", "Faible", "Aucune mesure"]
                   if c in set(synth_view["Confiance"])]
        f_conf = st.multiselect("Filtrer : confiance", cf_opts, default=cf_opts, key="mt_f_conf")
    shown = synth_view[phase_vals.isin(f_phase) & synth_view["Confiance"].isin(f_conf)]
    shown = shown.reset_index(drop=True)
    st.caption(f"{len(shown)} parcelle(s) affichée(s) sur {len(synth_view)}. Clique sur une "
               "ligne pour afficher le graphique de la parcelle. Survole un en-tête de "
               "colonne pour sa définition. L'export Excel contient toutes les parcelles.")
    # Clé liée aux filtres : la sélection est remise à zéro quand ils changent
    table_key = "mt_table_" + hashlib.md5(
        "|".join(sorted(f_phase) + ["#"] + sorted(f_conf)).encode()).hexdigest()[:8]
    event = st.dataframe(shown, hide_index=True, column_config=column_config(shown, ctx.column_help),
                         on_select="rerun", selection_mode="single-row", key=table_key)

    if shown.empty:
        st.info("Aucune parcelle ne correspond aux filtres : la liste ci-dessous les propose toutes.")

    # Parcelle affichée : clic dans le tableau, ou liste déroulante
    pid_list = list(shown["ID"]) or list(synthese["ID"])
    rows_sel = list(event.selection.rows) if event is not None else []
    if (rows_sel, table_key) != st.session_state.get("mt_last_rows"):
        st.session_state.mt_last_rows = (rows_sel, table_key)
        if rows_sel:
            if rows_sel[0] < len(pid_list):
                st.session_state.mt_pid = pid_list[rows_sel[0]]
    if st.session_state.get("mt_pid") not in pid_list:
        st.session_state.mt_pid = pid_list[0]
    return synth_view, pid_list


def _parcel_detail(ctx: Context, synthese, detail, courbes, pid_list, p_start, p_end):
    """Courbe NDVI (et panneau NDTI) d'une parcelle, vignette satellite et mesures."""
    s = ctx.settings
    st.subheader("Évolution du NDVI")
    c1, c2 = st.columns([3, 1])
    with c1:
        pid = st.selectbox("Parcelle", pid_list, key="mt_pid")
    with c2:
        show_excl = st.checkbox("Afficher les mesures écartées", value=True, key="mt_show_excl")

    s_row = synthese[synthese["ID"] == pid].iloc[0]
    st.markdown(
        f"**{pid}** — fin de période : "
        f"**{'—' if pd.isna(s_row['Phase_fin']) else s_row['Phase_fin']}** "
        f"(NDVI lissé {fmt(s_row['NDVI_fin']).replace('.', ',')}) · confiance {s_row['Confiance']} · "
        f"plus long trou sans mesure : {s_row['Plus_long_trou_j']} j")
    st.caption(f"Chronologie : {s_row['Chronologie']}"
               + (f" · Baisse(s) rapide(s) vers le {s_row['Baisses_rapides']}"
                  if s_row["Baisses_rapides"] != "—" else ""))

    d_pid = detail[detail["ID"] == pid]
    c_pid = courbes[courbes["ID"] == pid]
    st.altair_chart(parcel_chart(d_pid, c_pid, p_start, p_end, s.low, s.high, show_excl),
                    width="stretch")
    nd_chart = ndti_chart(d_pid, p_start, p_end, s.low)
    if nd_chart is not None:
        st.markdown("**NDTI — résidus de culture (expérimental)**")
        st.altair_chart(nd_chart, width="stretch")
    st.caption("● mesure retenue · ○ mesure écartée (survol : motif) · trait plein : courbe "
               "lissée · pointillé : interpolation à plus de 15 jours de toute mesure · "
               "lignes tiretées : seuils. Panneau NDTI (expérimental) : points pleins quand "
               f"la parcelle est peu verte (NDVI < {fr_decimal(s.low)}), seuls interprétables ; "
               "cercle orange : sol humide probable (NDTI peu fiable).")

    with st.expander(f"🛰️ Image satellite de la parcelle {pid}"):
        d_img = d_pid[~d_pid["Statut"].isin(NO_IMAGE_STATUSES)]
        if d_img.empty:
            st.info("Aucune image ne couvre cette parcelle sur la période.")
        else:
            opts = list(d_img.itertuples(index=False))

            def _lab(r):
                v = r.NDVI if r.Retenue == "Oui" else getattr(r, "NDVI_brut", None)
                txt = f"{r.Date:%d/%m/%Y} — NDVI {fmt(v).replace('.', ',')}"
                return txt + (" · retenue" if r.Retenue == "Oui" else f" · écartée ({r.Motif})")

            kept_idx = [i for i, r in enumerate(opts) if r.Retenue == "Oui"]
            r_sel = st.selectbox("Date", opts, index=kept_idx[-1] if kept_idx else len(opts) - 1,
                                 format_func=_lab, key=f"mt_thumb_date_{pid}")
            satellite_view(ctx, pid, r_sel.Date.strftime("%Y-%m-%d"), key="mt_thumb")

    with st.expander(f"Mesures de la parcelle {pid}"):
        d_view = pick(d_pid.assign(Date=d_pid["Date"].dt.date), DETAIL_COLS)
        st.dataframe(d_view, hide_index=True, column_config=column_config(d_view, ctx.column_help))


def _timeline_date(meas_dates, p_start, p_end, last_curve):
    """Date affichée sur la carte : clic sur la frise (prioritaire au passage du clic),
    boutons ◀ ▶ ou curseur. Met à jour st.session_state.mt_map_date."""
    if not (p_start <= st.session_state.get("mt_map_date", last_curve) <= p_end):
        st.session_state.mt_map_date = last_curve
    st.session_state.setdefault("mt_map_date", last_curve)
    tl_state = st.session_state.get("mt_timeline")
    picked = None
    try:
        pts_sel = (tl_state or {}).get("selection", {}).get("pick") or []
        if pts_sel and meas_dates:
            v = pts_sel[0].get("Date")
            t = pd.to_datetime(v, unit="ms") if isinstance(v, (int, float)) else pd.to_datetime(v)
            # date mesurée la plus proche (le clic peut être décalé par le fuseau horaire)
            picked = min(meas_dates, key=lambda d: abs((pd.Timestamp(d) - t).total_seconds()))
    except (TypeError, ValueError, AttributeError):
        picked = None
    if picked != st.session_state.get("mt_tl_last"):
        st.session_state.mt_tl_last = picked
        if picked is not None:
            st.session_state.mt_map_date = picked


def _phase_map_section(ctx: Context, synthese, detail, courbes, p_start, p_end):
    """Carte de la phase de la courbe lissée de chaque parcelle à une date choisie."""
    st.subheader("Carte des phases à une date")
    if courbes.empty:
        st.info("Aucune courbe disponible : pas de mesure retenue sur la période.")
        return
    last_curve = min(max(courbes["Date"].max().date(), p_start), p_end)
    meas = (detail.assign(ok=detail["Retenue"].eq("Oui"))
            .groupby("Date")["ok"].agg(["sum", "count"]).reset_index()
            .rename(columns={"sum": "Retenues", "count": "Total"}))
    meas_dates = sorted(d.date() for d in meas["Date"])
    _timeline_date(meas_dates, p_start, p_end, last_curve)

    def _jump(step):
        cur = st.session_state.mt_map_date
        cands = [d for d in meas_dates if (d > cur if step > 0 else d < cur)]
        if cands:
            st.session_state.mt_map_date = cands[0] if step > 0 else cands[-1]

    st.caption("Points : dates des images analysées (plus le point est foncé, plus il y a de "
               "parcelles avec une mesure retenue). Clique sur un point pour afficher la carte "
               "à cette date ; le trait rouge indique la date affichée.")
    st.altair_chart(dates_timeline(meas, p_start, p_end, st.session_state.mt_map_date),
                    width="stretch", on_select="rerun", selection_mode="pick",
                    key="mt_timeline")
    b1, b2, _sp = st.columns([1, 1, 4])
    with b1:
        st.button("◀ Précédente", key="mt_prev", on_click=_jump, args=(-1,),
                  help="Date d'image analysée précédente",
                  disabled=not any(d < st.session_state.mt_map_date for d in meas_dates))
    with b2:
        st.button("Suivante ▶", key="mt_next", on_click=_jump, args=(1,),
                  help="Date d'image analysée suivante",
                  disabled=not any(d > st.session_state.mt_map_date for d in meas_dates))
    map_date = st.slider("Date affichée (tout jour de la période)", min_value=p_start,
                         max_value=p_end, format="DD/MM/YYYY", key="mt_map_date")
    if map_date in meas_dates:
        st.caption(f"📍 {map_date:%d/%m/%Y} : date d'image analysée.")

    day = courbes[courbes["Date"] == pd.Timestamp(map_date)].set_index("ID")
    items, n_unc, n_none = [], 0, 0
    for _, srow in synthese.iterrows():
        pid_m = srow["ID"]
        if pid_m in day.index:
            r = day.loc[pid_m]
            unc = bool(r["Incertain"])
            n_unc += unc
            items.append({
                "id": pid_m, "color": colorize(r["Phase"]), "opacity": 0.35 if unc else 0.65,
                "props": {"ID": pid_m, "Date": f"{map_date:%d/%m/%Y}", "Phase": r["Phase"],
                          "NDVI lissé": fmt(r["NDVI_lisse"]).replace(".", ","),
                          "Interpolé": "Oui" if unc else "Non",
                          "Confiance": srow["Confiance"]},
                "tooltip": (f"<b>{pid_m}</b><br>{r['Phase']}<br>"
                            f"NDVI lissé : {fmt(r['NDVI_lisse'])}"
                            + ("<br><i>Interpolé : aucune mesure à moins de 15 jours</i>"
                               if unc else "")),
            })
        else:
            n_none += 1
            items.append({
                "id": pid_m, "color": COLOR_INVALID, "opacity": 0.5,
                "props": {"ID": pid_m, "Date": f"{map_date:%d/%m/%Y}",
                          "Phase": "Pas de courbe à cette date", "NDVI lissé": "—",
                          "Interpolé": "—", "Confiance": srow["Confiance"]},
                "tooltip": (f"<b>{pid_m}</b><br>Pas de courbe à cette date<br>"
                            "(avant la 1re ou après la dernière mesure retenue)"),
            })
    st.caption(
        f"Phase de la courbe lissée de chaque parcelle au {map_date:%d/%m/%Y}. "
        f"Teinte atténuée : valeur interpolée loin de toute mesure ({n_unc} parcelle(s)). "
        f"Gris : pas de courbe à cette date ({n_none} parcelle(s)).")
    phase_map(ctx.parcels, items, key="mt_map",
              legend_extra=[("Pas de courbe à cette date", COLOR_INVALID)],
              export={"title": f"Phases NDVI au {map_date:%d/%m/%Y}",
                      "file": f"phases_ndvi_{map_date}"})


# ------------------------------------------------------------------ 4. exports
def _excel_export(ctx: Context, synth_view, pivot, synthese, detail, courbes, df_long,
                  p_start, p_end):
    s = ctx.settings
    detail_view = pick(detail.assign(Date=detail["Date"].dt.date), DETAIL_COLS)
    courbes_view = courbes.assign(Date=courbes["Date"].dt.date)[
        ["ID", "Date", "NDVI_lisse", "Phase", "Incertain"]]
    tech = pick(df_long, TECH_COLS)
    xlsx = to_excel(
        {"Synthèse": synth_view, "NDVI par date": pivot, "Détail": detail_view,
         "Courbes lissées": courbes_view, "Technique": tech},
        ctx.column_help,
        extra=lambda book: add_parcel_charts(book, synthese, detail, courbes,
                                             p_start, p_end, s.low, s.high))
    st.download_button(
        "⬇️ Exporter (Excel : synthèse, graphiques par parcelle, NDVI par date, détail, "
        "courbes lissées, technique)",
        data=xlsx, file_name=f"ndvi_temporel_{p_start}_{p_end}.xlsx", mime=XLSX_MIME,
        key="mt_dl")
    if len(synthese) > MAX_CHARTS:
        st.caption(f"Graphiques Excel limités aux {MAX_CHARTS} premières parcelles.")


def _save_button(ctx: Context, p_start, p_end):
    """Enregistrement de l'analyse (réouverture sans recalcul Earth Engine)."""
    s, p = ctx.settings, ctx.parcels
    calc = st.session_state.mt_ctx
    calc_params = dict(calc["params_t"])
    session_zip = build_session_zip(
        meta={
            "app_version": APP_VERSION, "source_file": p.source_name, "file_hash": p.file_hash,
            "id_field": p.id_field if p.fields else None,
            "geoms_key": calc["geoms_key"], "params": calc_params,
            "settings": {
                "thresholds": [s.low, s.high],
                "buffer_m": int(calc["geoms_key"].rsplit("|", 1)[1]),
                "cs_threshold": calc_params["cs_threshold"],
                "cloud_buffer_m": calc_params["cloud_buffer_m"],
                "iqr_k": calc_params["iqr_k"],
                "min_clear": int(s.min_clear), "min_pixels": int(s.min_pixels),
                "min_weight": float(s.min_weight), "smooth_days": int(s.smooth_days),
            },
            "period": [p_start, p_end],
            "errors": st.session_state.get("mt_errors", []),
        },
        features=p.features, ids=p.ids, geoinfo=st.session_state.mt_geoinfo,
        raws=st.session_state.mt_raw)
    st.download_button(
        "💾 Enregistrer l'analyse (.zip, à rouvrir plus tard sans recalcul)",
        data=session_zip, file_name=f"analyse_ndvi_{p_start}_{p_end}.zip",
        mime="application/zip", key="mt_save",
        help="Contient les réglages, les résultats de chaque date et les contours des "
             "parcelles. Pour la rouvrir : « Ouvrir une analyse enregistrée » en haut de page. "
             "Pour la supprimer : supprime simplement le fichier.")
