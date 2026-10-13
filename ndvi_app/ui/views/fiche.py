"""Page « Fiche parcelle » : une parcelle à la fois, ◀ ▶ pour passer à la suivante."""
import pandas as pd
import streamlit as st

from ndvi_app.config import PHASE_BIEN, PHASE_NU, PHASE_PEU
from ndvi_app.ui.charts import ndti_chart, parcel_chart
from ndvi_app.ui.columns import DETAIL_COLS, column_config, pick
from ndvi_app.ui.formatting import fmt, fr_decimal
from ndvi_app.ui.thumbnail import satellite_view

NO_IMAGE_STATUSES = ["Hors image", "Géométrie inexploitable"]
PHASE_BADGE = {PHASE_NU: "red", PHASE_PEU: "orange", PHASE_BIEN: "green"}


def render(app):
    res = app.results
    if res is None:
        st.title("Fiche parcelle")
        st.info("Pas encore d'analyse temporelle : lance-la ou ouvre une analyse enregistrée dans "
                "la page Projet.")
        st.page_link(app.pages["projet"], label="Aller à Projet", icon=":material/folder_open:")
        return
    _fiche(app.ctx, res)


def _parcel_list(res):
    """Parcelles proposées : celles affichées dans la vue d'ensemble (filtres), sinon toutes."""
    all_ids = list(res.synthese["ID"])
    shown = [i for i in st.session_state.get("ov_ids") or [] if i in set(all_ids)]
    return shown or all_ids


@st.fragment
def _fiche(ctx, res):
    s = ctx.settings
    pid_list = _parcel_list(res)
    if st.session_state.get("mt_pid") not in pid_list:
        st.session_state.mt_pid = pid_list[0]

    def _step(delta):
        i = pid_list.index(st.session_state.mt_pid)
        st.session_state.mt_pid = pid_list[(i + delta) % len(pid_list)]

    with st.container(horizontal=True, vertical_alignment="bottom"):
        st.button("", icon=":material/chevron_left:", key="fp_prev", on_click=_step, args=(-1,),
                  help="Parcelle précédente")
        pid = st.selectbox("Parcelle", pid_list, key="mt_pid", persist_state="session",
                           width=260)
        st.button("", icon=":material/chevron_right:", key="fp_next", on_click=_step, args=(1,),
                  help="Parcelle suivante")
        st.caption(f"Parcelle {pid_list.index(pid) + 1} sur {len(pid_list)}"
                   + (" (filtres de la vue d'ensemble)" if len(pid_list) < len(res.synthese) else ""))

    s_row = res.synthese[res.synthese["ID"] == pid].iloc[0]
    phase_fin = s_row["Phase_fin"]
    with st.container(horizontal=True, vertical_alignment="center"):
        st.title(str(pid))
        if pd.notna(phase_fin):
            st.badge(f"{phase_fin} en fin de période", color=PHASE_BADGE.get(phase_fin, "gray"))
        else:
            st.badge("Aucune courbe", color="gray")
        st.badge(f"Confiance {str(s_row['Confiance']).lower()}", color="blue")
        st.caption(f"{fmt(s_row['Surface_ha'], 2).replace('.', ',')} ha")

    n_dates = res.df_long["Date"].nunique()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("NDVI lissé en fin de période", fmt(s_row["NDVI_fin"], 2).replace(".", ","), border=True)
    m2.metric("Mesures retenues", f"{s_row['Mesures_retenues']} / {n_dates}", border=True)
    m3.metric("Plus long trou sans mesure", f"{s_row['Plus_long_trou_j']} j", border=True)
    m4.metric("Baisse rapide", "aucune" if s_row["Baisses_rapides"] == "—" else s_row["Baisses_rapides"],
              border=True)

    d_pid = res.detail[res.detail["ID"] == pid]
    c_pid = res.courbes[res.courbes["ID"] == pid]
    left, right = st.columns([2, 1], gap="large")
    with left:
        with st.container(border=True):
            st.subheader("Évolution du NDVI")
            st.caption(f"Chronologie : {s_row['Chronologie']}")
            show_excl = st.checkbox("Afficher les mesures écartées", value=True, key="mt_show_excl",
                                    persist_state="session")
            st.altair_chart(parcel_chart(d_pid, c_pid, res.p_start, res.p_end, s.low, s.high,
                                         show_excl), width="stretch")
            st.caption("● mesure retenue · ○ mesure écartée (survol : motif) · trait plein : courbe "
                       "lissée · pointillé : interpolation à plus de 15 jours de toute mesure · "
                       "lignes tiretées : seuils.")
            nd_chart = ndti_chart(d_pid, res.p_start, res.p_end, s.low)
            if nd_chart is not None:
                with st.expander("NDTI — résidus de culture (expérimental)"):
                    st.altair_chart(nd_chart, width="stretch")
                    st.caption("Points pleins quand la parcelle est peu verte "
                               f"(NDVI < {fr_decimal(s.low)}), seuls interprétables ; cercle "
                               "orange : sol humide probable (NDTI peu fiable).")
    with right:
        with st.container(border=True):
            st.subheader("Image satellite")
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
                satellite_view(ctx, pid, r_sel.Date.strftime("%Y-%m-%d"), key="mt_thumb", stacked=True)
        with st.expander(f"Mesures de la parcelle ({int(s_row['Mesures_retenues'])} retenues)"):
            d_view = pick(d_pid.assign(Date=d_pid["Date"].dt.date), DETAIL_COLS)
            st.dataframe(d_view, hide_index=True, column_config=column_config(d_view, ctx.column_help))
