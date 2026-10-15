"""Page « Fiche parcelle » : une parcelle à la fois, ◀ ▶ pour passer à la suivante."""
import pandas as pd
import streamlit as st

from ndvi_app.config import PHASE_BIEN, PHASE_NU, PHASE_PEU
from ndvi_app.core import soil_reading as soil
from ndvi_app.ui import weather
from ndvi_app.ui.charts import ndti_chart, parcel_chart, weather_charts
from ndvi_app.ui.columns import DETAIL_COLS, column_config, pick
from ndvi_app.ui.formatting import fmt, fr_decimal
from ndvi_app.ui.thumbnail import satellite_view

NO_IMAGE_STATUSES = ["Hors image", "Géométrie inexploitable"]
PHASE_BADGE = {PHASE_NU: "red", PHASE_PEU: "orange", PHASE_BIEN: "green"}
READING_BADGE = {soil.LEVEE: "green", soil.RESIDUS: "violet", soil.SOL_NU: "red"}


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
        lecture = s_row.get("Lecture_fin", soil.NONE)
        if isinstance(lecture, str) and lecture != soil.NONE:
            st.badge(lecture, color=READING_BADGE.get(lecture, "gray"),
                     icon=":material/psychology_alt:",
                     help="Lecture à la dernière mesure retenue (NDVI, NDTI, état du sol et "
                          "météo) : aide à la décision, à confirmer sur le terrain.")
        if s_row.get("A_verifier") == "Oui":
            st.badge("À vérifier (zone grise)", color="orange", icon=":material/help:",
                     help=f"NDVI en fin de période entre {fr_decimal(s.grey[0])} et "
                          f"{fr_decimal(s.grey[1])} : vérifier sur l'image infrarouge ou sur "
                          "le terrain.")
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
            _weather_panel(ctx, res)
            nd_chart = ndti_chart(d_pid, res.p_start, res.p_end, s.low)
            if nd_chart is not None:
                with st.expander("NDTI — résidus de culture (expérimental)"):
                    st.altair_chart(nd_chart, width="stretch")
                    st.caption("Points pleins quand la parcelle est peu verte "
                               f"(NDVI < {fr_decimal(s.low)}), seuls interprétables ; cercle "
                               "orange : sol humide ou à vérifier (NDTI non lu). Survol : état "
                               "du sol et lecture. Lignes : repères indicatifs, à calibrer.")
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
                    if r.Retenue == "Oui":
                        note = r.Motif if isinstance(r.Motif, str) and r.Motif else None
                        return txt + " · retenue" + (f" ({note.lower()})" if note else "")
                    return txt + f" · écartée ({r.Motif})"

                kept_idx = [i for i, r in enumerate(opts) if r.Retenue == "Oui"]
                r_sel = st.selectbox("Date", opts, index=kept_idx[-1] if kept_idx else len(opts) - 1,
                                     format_func=_lab, key=f"mt_thumb_date_{pid}")
                satellite_view(ctx, pid, r_sel.Date.strftime("%Y-%m-%d"), key="mt_thumb", stacked=True)
        _reading_help(s, s_row, d_pid)
        with st.expander(f"Mesures de la parcelle ({int(s_row['Mesures_retenues'])} retenues)"):
            d_view = pick(d_pid.assign(Date=d_pid["Date"].dt.date), DETAIL_COLS)
            st.dataframe(d_view, hide_index=True, column_config=column_config(d_view, ctx.column_help))


def _weather_panel(ctx, res):
    """Météo ERA5-Land de la période sous la courbe NDVI (même axe des dates)."""
    st_ = weather.status(res.p_end)
    charts = weather_charts(weather.days(), res.p_start, res.p_end)
    # Directement sous la courbe NDVI (pas dans un volet) : même largeur, dates alignées
    st.markdown("**Météo** · pluie, gel, humidité du sol (ERA5-Land)")
    if True:
        for chart in charts:
            st.altair_chart(chart, width="stretch")
        if charts:
            st.caption("Barres : pluie du jour · ▼ : gel (température minimale ≤ "
                       f"{fr_decimal(soil.FROST_C, 0)} °C) · courbe : humidité du sol 0–7 cm, "
                       f"repères {fr_decimal(soil.SM_DRY)} (ressuyé) et {fr_decimal(soil.SM_WET)} "
                       "(proche saturation). Une valeur pour toute la zone (maille ≈ 11 km).")
        if st_ is not None and st_[0] != "ok":
            st.caption(st_[1])
            if st.button("Actualiser la météo", icon=":material/refresh:", key="fp_wx_refresh"):
                weather.refresh(ctx)
                st.rerun(scope="app")


def _current_case(s_row, d_pid):
    """Ligne de l'aide à la lecture correspondant à la dernière mesure retenue."""
    kept = d_pid[d_pid["Retenue"] == "Oui"]
    if kept.empty:
        return None, None
    last = kept.sort_values("Date").iloc[-1]
    lecture = last.get("Lecture", soil.NONE)
    case = {soil.LEVEE: "levee", soil.INDET_HUMIDE: "humide", soil.INDET_VERIF: "humide",
            soil.RESIDUS: "residus", soil.SOL_NU: "solnu", soil.INDET: "indet"}.get(lecture)
    if case is None and lecture == soil.NONE:
        case = "couvert"
    return case, last


def _reading_help(s, s_row, d_pid):
    """Aide à la lecture : règle simplifiée, cas de la parcelle (dernière mesure) surligné."""
    low = fr_decimal(s.low)
    case, last = _current_case(s_row, d_pid)

    def line(key, text, indent=False):
        txt = f":green-background[{text}] :material/arrow_back:" if key == case else text
        return ("  - " if indent else "- ") + txt

    lines = []
    if s.grey:
        g0, g1 = fr_decimal(s.grey[0]), fr_decimal(s.grey[1])
        in_grey = s_row.get("A_verifier") == "Oui"
        txt = (f"{g0} ≤ NDVI fin ≤ {g1} → **à vérifier** (zone grise : image infrarouge, "
               "terrain), en plus de la lecture ci-dessous")
        lines.append("- " + (f":orange-background[{txt}] :material/arrow_back:" if in_grey else txt))
    lines += [
        line("couvert", f"NDVI ≥ {low} → **couvert ou culture présent** (maïs jaunissant compris)"),
        f"- NDVI < {low} :",
        line("levee", f"en hausse (+ {fr_decimal(soil.RISE_MIN)} en {soil.RISE_DAYS} j) → "
                      "**levée possible**", True),
        line("humide", "sol humide ¹ → **indéterminé**, NDTI non lisible", True),
        line("residus", f"sol sec, NDTI ≥ {fr_decimal(soil.NDTI_RESIDUE)} → **résidus probables** "
                        "(chaumes, cannes)", True),
        line("solnu", f"sol sec, NDTI ≤ {fr_decimal(soil.NDTI_BARE)} → **sol nu probable**", True),
        line("indet", "sol sec, NDTI entre les deux → **indéterminé** (mélange, résidus en "
                      "partie enfouis)", True),
    ]
    with st.container(border=True):
        st.subheader("Aide à la lecture")
        st.markdown("\n".join(lines))
        if last is not None:
            parts = [f"NDVI {fr_decimal(last['NDVI'], 3)}"]
            for col, lab, n, unit in (("NDTI", "NDTI", 3, ""), ("SWIR1_median", "B11", 3, ""),
                                      ("Pluie_3j_mm", "pluie 3 j", 1, " mm")):
                v = last.get(col)
                if v is not None and pd.notna(v):
                    parts.append(f"{lab} {fr_decimal(v, n)}{unit}")
            state = last.get("Etat_sol", soil.NONE)
            if isinstance(state, str) and state != soil.NONE:
                parts.append(f"sol {state.lower()}")
            st.caption(f"Cette parcelle à sa dernière mesure retenue ({last['Date']:%d/%m/%Y}) : "
                       + " · ".join(parts) + ".")
        st.caption(
            f"¹ Sol humide : B11 < {fr_decimal(soil.SWIR1_WET)}, ou pluie des 3 jours ≥ "
            f"{fr_decimal(soil.RAIN_WET, 0)} mm, de la veille ≥ {fr_decimal(soil.RAIN_EVE, 0)} mm, "
            f"humidité du sol ≥ {fr_decimal(soil.SM_WET)}. Sol sec : B11 ≥ "
            f"{fr_decimal(soil.SWIR1_WET)} sans pluie récente. Baisse rapide « (gel) » : couvert "
            "gelé plutôt que détruit. Repères indicatifs, à calibrer sur le terrain.")
