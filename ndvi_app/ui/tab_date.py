"""Onglet « Analyse à une date » : choix d'une image, tableau, carte, vignette."""
import datetime

import pandas as pd
import streamlit as st

from ndvi_app.config import COLOR_INVALID, FIRST_YEAR, MONTHS_FR, STATUS_OK
from ndvi_app.core.quality import build_rows, colorize
from ndvi_app.exports.excel import XLSX_MIME, to_excel
from ndvi_app.ui.columns import OS_COLS, TECH_COLS, column_config, pick
from ndvi_app.ui.context import Context
from ndvi_app.ui.formatting import covers, date_label, fmt, usable
from ndvi_app.ui.maps import phase_map
from ndvi_app.ui.state import stale_warning
from ndvi_app.ui.thumbnail import satellite_view


def render(ctx: Context):
    st.header("Analyse NDVI — une date")
    target = _pick_date(ctx)
    if target is not None:
        with st.spinner(f"Calcul des statistiques du {target['date']:%d/%m/%Y}…"):
            try:
                st.session_state.os_raw = (str(target["date"]), ctx.run_analysis(str(target["date"])))
                st.session_state.os_ctx = ctx.calc_context()
                st.session_state.os_geoinfo = ctx.parcels.geoinfo
            except Exception as e:
                st.error(f"Erreur Earth Engine (statistiques) : {type(e).__name__} — {e}")
    if st.session_state.get("os_raw"):
        _results(ctx)


def _pick_date(ctx: Context):
    """Mois → dates disponibles → date choisie. Retourne l'entrée de list_dates à analyser
    (bouton « Analyser cette date » cliqué) ou None."""
    min_clear = ctx.settings.min_clear
    today = datetime.date.today()
    c1, c2 = st.columns(2)
    with c1:
        year = st.selectbox("Année", list(range(today.year, FIRST_YEAR - 1, -1)), key="os_year")
    with c2:
        month = st.selectbox("Mois", range(1, 13), index=today.month - 1, key="os_month",
                             format_func=lambda m: MONTHS_FR[m - 1])
    start = datetime.date(year, month, 1)
    end = min((datetime.date(year + 1, 1, 1) if month == 12
               else datetime.date(year, month + 1, 1)) - datetime.timedelta(days=1), today)

    if start > today:
        st.info("Ce mois n'a pas encore commencé.")
    elif st.button("Rechercher les dates disponibles", key="os_btn_search"):
        st.session_state.os_query = (start, end)

    # La liste suit les réglages du masque : recalculée (une requête) s'ils changent.
    if st.session_state.get("os_query") != (start, end):
        return None
    try:
        dates = ctx.list_dates(start, end)
    except Exception as e:
        st.error(f"Erreur Earth Engine (recherche des dates) : {type(e).__name__} — {e}")
        return None

    if not dates:
        st.error("Aucune image Sentinel-2 sur cette période.")
        return None
    n_ok = sum(1 for d in dates if usable(d, min_clear))
    n_out = sum(1 for d in dates if not covers(d))
    st.caption(
        f"{len(dates)} date(s) en {MONTHS_FR[month - 1].lower()} {year} : "
        f"{n_ok} ✅ à {min_clear} % de ciel clair ou plus, "
        f"{len(dates) - n_ok - n_out} ⚠️ en dessous, {n_out} ⛔ hors emprise des parcelles. "
        f"Le % de ciel clair porte sur la partie des parcelles couverte par l'image : "
        f"une date ⚠️ peut rester exploitable pour certaines (voir leur statut après analyse)."
    )
    default = next((i for i, d in enumerate(dates) if usable(d, min_clear)),
                   next((i for i, d in enumerate(dates) if covers(d)), 0))
    choice = st.selectbox("Date à analyser", dates, index=default,
                          format_func=lambda d: date_label(d, min_clear), key=f"os_sel_{start}")
    if st.button("Analyser cette date", key="os_btn_load"):
        return choice
    return None


def _results(ctx: Context):
    s, p = ctx.settings, ctx.parcels
    date_str, raw = st.session_state.os_raw
    stale_warning(st.session_state.os_ctx, ctx.calc_context())
    rows = build_rows(p.ids, st.session_state.os_geoinfo, raw, date_str,
                      s.min_pixels, s.min_clear, s.low, s.high)
    df_all = pd.DataFrame(rows)
    df_os = pick(df_all, OS_COLS)

    n_ok = int((df_os["Statut"] == STATUS_OK).sum())
    st.success(f"Résultats du {date_str} — {n_ok}/{len(df_os)} parcelles exploitables")
    st.dataframe(df_os, hide_index=True, column_config=column_config(df_os, ctx.column_help))
    st.caption("Survole un en-tête de colonne pour sa définition.")
    st.download_button(
        "⬇️ Exporter (Excel)",
        data=to_excel({"Résultats": df_os, "Technique": pick(df_all, TECH_COLS)}, ctx.column_help),
        file_name=f"ndvi_{date_str}.xlsx", mime=XLSX_MIME, key="os_dl")

    day = pd.Timestamp(date_str)
    phase_map(p, [_map_item(row, day) for _, row in df_os.iterrows()], key="os_map",
              legend_extra=[("Non exploitable", COLOR_INVALID)],
              export={"title": f"NDVI au {day:%d/%m/%Y}", "file": f"ndvi_{date_str}"})

    with st.expander("🛰️ Image satellite d'une parcelle à cette date"):
        pid_os = st.selectbox("Parcelle", list(df_os["ID"]), key="os_thumb_pid")
        satellite_view(ctx, pid_os, date_str, key="os_thumb")


def _map_item(row, day):
    """Couleur, infobulle et attributs d'export d'une parcelle sur la carte du jour."""
    ok = row["Statut"] == STATUS_OK
    return {
        "id": row["ID"],
        "color": colorize(row["Phase"]) if ok else COLOR_INVALID,
        "props": {"ID": row["ID"], "Date": day.strftime("%d/%m/%Y"),
                  "Phase": row["Phase"], "NDVI": fmt(row["NDVI"]).replace(".", ","),
                  "NDTI": fmt(row.get("NDTI")).replace(".", ","),
                  "Fiabilité": row.get("Fiabilite", "—"),
                  "Pixels clairs (%)": fmt(row.get("Clair_pct"), 0)},
        "tooltip": (f"<b>{row['ID']}</b><br>{row['Phase']}<br>"
                    f"NDVI : {fmt(row['NDVI'])}"
                    + (f" · NDTI : {fmt(row['NDTI'])}" if pd.notna(row.get("NDTI")) else "")
                    + "<br>"
                    f"Fiabilité : {row.get('Fiabilite', '—')} "
                    f"(poids {fmt(row.get('Poids'), 2)})<br>"
                    f"Pixels utilisés : {row.get('Pixels_utilises', '—')} · "
                    f"clairs : {fmt(row.get('Clair_pct'), 0, ' %')}"),
    }
