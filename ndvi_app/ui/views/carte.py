"""Page « Carte » : phase de la courbe lissée de chaque parcelle à une date choisie."""
import pandas as pd
import streamlit as st

from ndvi_app.config import COLOR_INVALID
from ndvi_app.core.quality import colorize
from ndvi_app.ui.charts import dates_timeline
from ndvi_app.ui.formatting import fmt
from ndvi_app.ui.maps import phase_map


def render(app):
    res = app.results
    if res is None:
        st.title("Carte des phases")
        st.info("Pas encore d'analyse temporelle : lance-la ou ouvre une analyse enregistrée dans "
                "la page Projet.")
        st.page_link(app.pages["projet"], label="Aller à Projet", icon=":material/folder_open:")
        return
    _map_page(app.ctx, res)


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


@st.fragment
def _map_page(ctx, res):
    synthese, detail, courbes = res.synthese, res.detail, res.courbes
    p_start, p_end = res.p_start, res.p_end
    title = st.empty()
    if courbes.empty:
        title.title("Carte des phases")
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

    with st.container(border=True):
        st.altair_chart(dates_timeline(meas, p_start, p_end, st.session_state.mt_map_date),
                        width="stretch", on_select="rerun", selection_mode="pick",
                        key="mt_timeline")
        with st.container(horizontal=True, vertical_alignment="center"):
            st.button("Précédente", icon=":material/chevron_left:", key="mt_prev", on_click=_jump,
                      args=(-1,), help="Date d'image analysée précédente",
                      disabled=not any(d < st.session_state.mt_map_date for d in meas_dates))
            st.button("Suivante", icon=":material/chevron_right:", key="mt_next", on_click=_jump,
                      args=(1,), help="Date d'image analysée suivante",
                      disabled=not any(d > st.session_state.mt_map_date for d in meas_dates))
            st.caption("Points : dates des images analysées (plus le point est foncé, plus il y a "
                       "de parcelles avec une mesure retenue). Clique sur un point pour afficher "
                       "la carte à cette date ; le trait rouge indique la date affichée.")
        map_date = st.slider("Date affichée (tout jour de la période)", min_value=p_start,
                             max_value=p_end, format="DD/MM/YYYY", key="mt_map_date",
                             persist_state="session")
    title.title(f"Carte des phases au {map_date:%d/%m/%Y}")

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
        ("Date d'image analysée · " if map_date in meas_dates else "")
        + f"Phase de la courbe lissée de chaque parcelle au {map_date:%d/%m/%Y}. "
        f"Teinte atténuée : valeur interpolée loin de toute mesure ({n_unc} parcelle(s)). "
        f"Gris : pas de courbe à cette date ({n_none} parcelle(s)).")
    phase_map(ctx.parcels, items, key="mt_map", height=600,
              legend_extra=[("Pas de courbe à cette date", COLOR_INVALID)],
              export={"title": f"Phases NDVI au {map_date:%d/%m/%Y}",
                      "file": f"phases_ndvi_{map_date}"})
