"""
Page « Vue d'ensemble » : toutes les parcelles d'un coup d'œil.

Chiffres clés et filtres, puis trois présentations du même ensemble de parcelles :
  Évolution des phases : courbe NDVI et barre des phases de chaque parcelle, sur la même
                         échelle de temps (mois repérés) ;
  Valeurs NDVI par date : tableau des mesures retenues, teinté selon la phase ;
  Synthèse détaillée : tableau de synthèse complet (chronologie, trous, mesures…).
Un clic sur une ligne ouvre la fiche de la parcelle.
"""
import hashlib

import pandas as pd
import streamlit as st

from ndvi_app.config import COLOR_MAP, PHASE_BIEN, PHASE_NU, PHASE_PEU
from ndvi_app.exports.workbook import pivot_all, pivot_for
from ndvi_app.ui.charts import phase_evolution_uri
from ndvi_app.ui.columns import SYNTH_COLS, column_config, pick
from ndvi_app.ui.formatting import fr_decimal

NO_PHASE = "Aucune courbe"
ALL = "Toutes"
VIEWS = ["Évolution des phases", "Valeurs NDVI par date", "Synthèse détaillée"]
CONF_ORDER = ["Bonne", "Moyenne", "Faible", "Aucune mesure"]
CELL_STYLE = {PHASE_NU: "background-color: #F6D5D2; color: #7A1C14",
              PHASE_PEU: "background-color: #E4F2D0; color: #3D5A12",
              PHASE_BIEN: "background-color: #CDEBD8; color: #0E5A31"}


def render(app):
    st.title("Vue d'ensemble des parcelles")
    res = app.results
    if res is None:
        st.info("Pas encore d'analyse temporelle : choisis la période et lance l'analyse dans "
                "la page Projet, ou ouvre une analyse enregistrée.")
        st.page_link(app.pages["projet"], label="Aller à Projet", icon=":material/folder_open:")
        return
    s = app.ctx.settings
    syn = res.synthese
    phase = syn["Phase_fin"].fillna(NO_PHASE)
    st.caption(f"Du {res.p_start:%d/%m/%Y} au {res.p_end:%d/%m/%Y} · phase de la courbe lissée "
               "à la dernière mesure retenue de chaque parcelle.")

    # ── Chiffres clés ──────────────────────────────────────
    labels = [(ALL, len(syn))] + [(ph, int((phase == ph).sum())) for ph in list(COLOR_MAP) + [NO_PHASE]]
    grey_on = "A_verifier" in syn
    n_grey = int((syn["A_verifier"] == "Oui").sum()) if grey_on else 0
    cards = labels + ([("À vérifier", n_grey)] if grey_on else [])
    for col, (lab, n) in zip(st.columns(len(cards)), cards):
        col.metric("Toutes les parcelles" if lab == ALL else lab, n, border=True)

    # ── Filtres ────────────────────────────────────────────
    counts = dict(labels)
    f_phase = st.pills("Phase en fin de période", [lab for lab, n in labels if n or lab == ALL],
                       format_func=lambda v: f"{v} ({counts[v]})", default=ALL,
                       key="ov_phase", persist_state="session") or ALL
    c2, c3 = st.columns([3, 2], vertical_alignment="bottom")
    with c2:
        conf_opts = [c for c in CONF_ORDER if c in set(syn["Confiance"])]
        f_conf = st.pills("Confiance", conf_opts, selection_mode="multi", default=conf_opts,
                          key="ov_conf_" + "_".join(conf_opts), persist_state="session")
    with c3:
        query = st.text_input("Rechercher une parcelle", key="ov_q", placeholder="Identifiant",
                              persist_state="session")
    only_grey = grey_on and st.toggle(
        f"Seulement les parcelles à vérifier ({n_grey})", key="ov_grey_only",
        persist_state="session",
        help=f"NDVI en fin de période dans la zone grise ({fr_decimal(s.grey[0])}–"
             f"{fr_decimal(s.grey[1])}, réglable dans la barre latérale).")
    mask = syn["Confiance"].isin(f_conf)
    if only_grey:
        mask &= syn["A_verifier"] == "Oui"
    if f_phase != ALL:
        mask &= phase == f_phase
    if query:
        mask &= syn["ID"].astype(str).str.contains(query.strip(), case=False, regex=False)
    shown = syn[mask].reset_index(drop=True)
    st.session_state["ov_ids"] = list(shown["ID"])        # parcelles proposées dans la fiche

    view = st.segmented_control("Présentation", VIEWS, default=VIEWS[0], key="ov_view",
                                required=True, persist_state="session",
                                label_visibility="collapsed")
    st.caption(f"{len(shown)} parcelle(s) affichée(s) sur {len(syn)}. Clique sur une ligne pour "
               "ouvrir la fiche de la parcelle. Exports : bouton « Exporter » en haut de page.")
    if shown.empty:
        st.info("Aucune parcelle ne correspond aux filtres.")
        return

    table_key = "ov_table_" + hashlib.md5(
        "|".join([view, f_phase, query or "", str(only_grey)] + sorted(f_conf)).encode()).hexdigest()[:8]
    if view == VIEWS[0]:
        event = _evolution_table(shown, res, s, table_key)
    elif view == VIEWS[1]:
        event = _values_table(shown, res, s, table_key)
    else:
        view_df = pick(shown, SYNTH_COLS)
        event = st.dataframe(view_df, hide_index=True,
                             column_config=column_config(view_df, app.ctx.column_help),
                             on_select="rerun", selection_mode="single-row", key=table_key)
    _open_selected(app, shown, event, table_key)


def _evolution_table(shown, res, settings, key):
    curves = {pid: c for pid, c in res.courbes.groupby("ID")}
    empty = res.courbes.iloc[0:0]
    rows = []
    for _, r in shown.iterrows():
        c = curves.get(r["ID"], empty)
        rows.append({
            "ID": r["ID"], "Surface_ha": r["Surface_ha"],
            "Evolution": phase_evolution_uri(c, res.p_start, res.p_end, settings.low, settings.high),
            "NDVI_fin": r["NDVI_fin"], "Phase_fin": r["Phase_fin"] if pd.notna(r["Phase_fin"]) else NO_PHASE,
            **({"A_verifier": r["A_verifier"]} if "A_verifier" in r else {}),
            "Lecture_fin": r.get("Lecture_fin", "—"),
            "Confiance": r["Confiance"], "Baisses_rapides": r["Baisses_rapides"],
        })
    df = pd.DataFrame(rows)
    months = f"{res.p_start:%d/%m/%Y} → {res.p_end:%d/%m/%Y}"
    cfg = {
        "ID": st.column_config.TextColumn("Parcelle", pinned=True),
        "Surface_ha": st.column_config.NumberColumn("Surface (ha)", format="%.2f"),
        "Evolution": st.column_config.ImageColumn(
            f"NDVI et phases ({months})", width="large",
            help="En haut : courbe NDVI lissée (0 en bas, 1 en haut), pointillés horizontaux aux "
                 "seuils des phases. En bas : phase de la courbe. Rouge : sol nu · vert clair : "
                 "couvert peu développé · vert foncé : couvert bien développé · gris : pas de "
                 "courbe (avant la 1re ou après la dernière mesure retenue). Courbe en pointillé "
                 "et teinte atténuée : interpolée à plus de 15 jours d'une mesure. Traits : 1er "
                 "de chaque mois, même échelle de temps pour toutes les parcelles."),
        "NDVI_fin": st.column_config.NumberColumn("NDVI fin", format="%.2f"),
        "Phase_fin": st.column_config.TextColumn("Phase fin", width="medium"),
        "A_verifier": st.column_config.TextColumn(
            "À vérifier", help="NDVI en fin de période dans la zone grise (barre latérale) : "
                               "à vérifier sur l'image infrarouge ou sur le terrain."),
        "Lecture_fin": st.column_config.TextColumn(
            "Lecture fin", width="large",
            help="Parcelle peu verte à la dernière mesure : levée possible, résidus probables, "
                 "sol nu probable ou indéterminé, d'après le NDVI, le NDTI, l'état du sol et la "
                 "météo. Aide à la décision, à confirmer sur le terrain. — : parcelle verte."),
        "Confiance": st.column_config.TextColumn("Confiance"),
        "Baisses_rapides": st.column_config.TextColumn("Baisse rapide", width="medium"),
    }
    return st.dataframe(df, hide_index=True, column_config=cfg, row_height=72,
                        on_select="rerun", selection_mode="single-row", key=key)


def _values_table(shown, res, settings, key):
    pivot = pivot_for(pivot_all(res.detail), list(shown["ID"]))
    date_cols = [c for c in pivot.columns if c != "ID"]
    low, high = settings.low, settings.high

    def tint(v):
        if pd.isna(v):
            return "color: #8A8D96"
        return CELL_STYLE[PHASE_NU if v < low else PHASE_PEU if v < high else PHASE_BIEN]

    renamed = {c: f"{c[8:10]}/{c[5:7]}/{c[2:4]}" for c in date_cols}
    values = pivot.rename(columns=renamed)
    shown_txt = values.copy()
    for c in renamed.values():
        shown_txt[c] = values[c].map(lambda v: "—" if pd.isna(v) else f"{v:.2f}".replace(".", ","))
    css = values.copy()
    css["ID"] = ""
    for c in renamed.values():
        css[c] = values[c].map(tint)
    styler = shown_txt.style.apply(lambda _: css, axis=None)
    st.caption("Mesures retenues uniquement, teintées selon la phase (seuils de la barre latérale). "
               "« — » : pas de mesure retenue ce jour-là (nuages, poids trop faible, chute isolée sur image nuageuse).")
    return st.dataframe(styler, hide_index=True,
                        column_config={"ID": st.column_config.TextColumn("Parcelle", pinned=True)},
                        on_select="rerun", selection_mode="single-row", key=key)


def _open_selected(app, shown, event, key):
    """Ligne cliquée → fiche de la parcelle."""
    rows = list(event.selection.rows) if event is not None else []
    if (rows, key) == st.session_state.get("ov_last_sel"):
        return
    st.session_state.ov_last_sel = (rows, key)
    if rows and rows[0] < len(shown):
        st.session_state.mt_pid = shown["ID"].iloc[rows[0]]
        st.switch_page(app.pages["fiche"])
