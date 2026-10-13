"""
Période et dates de l'analyse temporelle (page Projet) : nouvelle analyse, ou complétion
d'une analyse existante (agrandissement de la période, seules les nouvelles dates calculées).
"""
import datetime

import streamlit as st

from ndvi_app.config import FIRST_YEAR, MONTHS_FR
from ndvi_app.core.completion import dates_to_add, merge_results
from ndvi_app.ui import analysis
from ndvi_app.ui.charts import GAP_DAYS, coverage_timeline
from ndvi_app.ui.context import Context
from ndvi_app.ui.formatting import date_label, fr_decimal, usable
from ndvi_app.ui.state import session_lock

PRESEL_HELP = ("Une date partiellement nuageuse peut rester exploitable pour une partie des "
               "parcelles : le contrôle se fait ensuite parcelle par parcelle, et les mesures "
               "douteuses sont écartées de la courbe.")


def render(ctx: Context, results_page=None):
    """Bloc « Période et dates » ; results_page : page des résultats (lien après calcul)."""
    st.subheader("Période et dates")
    if session_lock() is not None:
        st.caption("Pour compléter l'analyse enregistrée, agrandis la période : seules les dates "
                   "pas encore analysées sont proposées et calculées.")
    elif analysis.has_results():
        st.caption("Agrandir la période complète l'analyse (seules les nouvelles dates sont "
                   "calculées) ; la même période permet de la refaire avec d'autres dates.")
    period = _period()
    if period is None:
        return
    _select_and_run(ctx, *period)
    added = st.session_state.pop("mt_added", None)
    if added is not None:
        st.success(f"Analyse complétée : {added} date(s) ajoutée(s). Pense à l'enregistrer "
                   "(bouton « Enregistrer » en haut de page).")
    if st.session_state.pop("mt_just_run", False):
        st.success("Analyse terminée.")
    if results_page is not None and analysis.has_results():
        st.page_link(results_page, label="Voir la vue d'ensemble des parcelles",
                     icon=":material/arrow_forward:")


# ------------------------------------------------------------------ période
def _month_picker(label, key, default, today):
    c1, c2 = st.columns(2)
    st.session_state.setdefault(f"{key}_y", default.year)
    st.session_state.setdefault(f"{key}_m", default.month)
    with c1:
        y = st.selectbox(f"{label} — année", list(range(today.year, FIRST_YEAR - 1, -1)),
                         key=f"{key}_y", persist_state="session")
    with c2:
        m = st.selectbox(f"{label} — mois", list(range(1, 13)), key=f"{key}_m",
                         format_func=lambda v: MONTHS_FR[v - 1], persist_state="session")
    return y, m


def _period():
    """Mois de début et de fin → (premier jour, dernier jour, au plus aujourd'hui)."""
    today = datetime.date.today()
    dflt_start = (today.replace(day=1) - datetime.timedelta(days=62)).replace(day=1)
    c1, c2 = st.columns(2)
    with c1:
        ys, ms = _month_picker("Début", "mt_start", dflt_start, today)
    with c2:
        ye, me = _month_picker("Fin", "mt_end", today, today)
    period_start = datetime.date(ys, ms, 1)
    period_end = min((datetime.date(ye + 1, 1, 1) if me == 12 else datetime.date(ye, me + 1, 1))
                     - datetime.timedelta(days=1), today)
    if period_start > period_end:
        st.error("Le mois de début doit précéder le mois de fin.")
        return None
    st.caption(f"Période : du {period_start:%d/%m/%Y} au {period_end:%d/%m/%Y} "
               f"({(period_end - period_start).days + 1} jours).")
    return period_start, period_end


# ------------------------------------------------------------------ dates et calcul
def _extend_mode(ctx: Context, period_start, period_end):
    """Compléter l'analyse existante plutôt que la remplacer ?
    Oui si des résultats existent, calculés avec les paramètres actuels, et si la période
    choisie contient celle déjà analysée (on ne fait qu'agrandir). Pour une analyse faite
    dans la séance (non rouverte), la même période permet toujours de la refaire autrement."""
    if not analysis.has_results():
        return False
    p0, p1 = st.session_state.mt_period
    if session_lock() is None and (period_start, period_end) == (p0, p1):
        return False
    return (period_start <= p0 and p1 <= period_end
            and st.session_state.mt_ctx == ctx.calc_context())


def _select_and_run(ctx: Context, period_start, period_end):
    extend = _extend_mode(ctx, period_start, period_end)
    if session_lock() is not None and not extend:
        # Analyse enregistrée ouverte : seul l'agrandissement de la période est possible
        p0, p1 = st.session_state.mt_period
        if period_start <= p0 and p1 <= period_end:
            st.warning("Les paramètres de calcul enregistrés dans ce fichier ne correspondent pas "
                       "à ceux de cette version de l'application : l'analyse ne peut pas être "
                       "complétée (les nouvelles dates ne seraient pas comparables).")
            return
        st.warning(f"Pour compléter l'analyse enregistrée, la période doit contenir celle déjà "
                   f"analysée (du {p0:%d/%m/%Y} au {p1:%d/%m/%Y}) : choisis un mois de début "
                   "identique ou antérieur et un mois de fin identique ou postérieur.")
        return
    if st.button("Rechercher les dates disponibles", key="mt_btn_search", icon=":material/search:"):
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
    if extend:
        _select_and_extend(ctx, dates, period_start, period_end)
    else:
        _select_and_run_new(ctx, dates, period_start, period_end)


def _presel_slider(label):
    return st.slider(label, 0, 100, 20, 10, key="mt_presel", help=PRESEL_HELP,
                     persist_state="session")


def _run_dates(ctx: Context, dates, calc=None, geoinfo=None):
    """Calcule les dates une par une avec barre de progression. Retourne (raws, erreurs)."""
    raws, errors = [], []
    bar = st.progress(0.0, text="Initialisation…")
    for i, d in enumerate(sorted(dates)):
        bar.progress(i / len(dates), text=f"{d:%d/%m/%Y} ({i + 1}/{len(dates)})…")
        try:
            raws.append((str(d), ctx.run_analysis(str(d), calc, geoinfo)))
        except Exception as e:
            errors.append(f"{d:%d/%m/%Y} : {type(e).__name__} — {e}")
    bar.empty()
    return raws, errors


def _select_and_run_new(ctx: Context, dates, period_start, period_end):
    """Nouvelle analyse : toutes les dates de la période sont proposées."""
    min_clear = ctx.settings.min_clear
    presel = _presel_slider("Présélection : ciel clair minimum sur l'ensemble des parcelles (%)")
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
    if st.button("Lancer l'analyse temporelle", key="mt_btn_run", type="primary",
                 icon=":material/play_arrow:"):
        raws, errors = _run_dates(ctx, sel)
        analysis.store(raws, (period_start, period_end), ctx.calc_context(),
                       ctx.parcels.geoinfo, errors)
        st.session_state.mt_just_run = True
        st.rerun()


def _select_and_extend(ctx: Context, dates, period_start, period_end):
    """Compléter l'analyse : seules les dates pas encore analysées sont proposées et calculées,
    avec les paramètres et les géométries de l'analyse existante."""
    min_clear = ctx.settings.min_clear
    st.markdown("**Dates à ajouter**")
    done, new, n_outside = dates_to_add(dates, st.session_state.mt_raw)
    old0, old1 = st.session_state.mt_period

    n_before = sum(1 for d in new if d["date"] < old0)
    n_after = sum(1 for d in new if d["date"] > old1)
    n_inside = len(new) - n_before - n_after
    st.markdown(f"**{len(done)}** date(s) déjà analysée(s) (du {old0:%d/%m/%Y} au {old1:%d/%m/%Y}) · "
                f"**{len(new)}** disponible(s) non analysée(s) : {n_before} avant, "
                f"{n_inside} pendant, {n_after} après"
                + (f" · {n_outside} autre(s) image(s) ne couvrant pas les parcelles, non proposée(s)"
                   if n_outside else ""))
    timeline_slot = st.empty()
    if not new:
        timeline_slot.altair_chart(coverage_timeline(done, [], [], period_start, period_end,
                                                     (old0, old1)), width="stretch")
        st.info("Toutes les dates disponibles sur cette période sont déjà analysées.")
        return

    presel = _presel_slider("Présélection des dates à ajouter : ciel clair minimum sur "
                            "l'ensemble des parcelles (%)")
    default = [d["date"] for d in new if usable(d, presel)]
    by_date = {d["date"]: d for d in new}
    sel = st.multiselect(
        f"{len(new)} date(s) à ajouter possible(s), {len(default)} présélectionnée(s)",
        options=[d["date"] for d in new], default=default,
        format_func=lambda x: date_label(by_date[x], min_clear),
        key=f"mt_extsel_{presel}_{period_start}_{period_end}",
    )
    with timeline_slot.container():
        st.altair_chart(coverage_timeline(done, new, sel, period_start, period_end, (old0, old1)),
                        width="stretch")
        st.caption("Ligne du haut : dates déjà analysées. Ligne du bas : dates disponibles, "
                   "orange si sélectionnées, cercle vide sinon (survol : % de ciel clair). "
                   "Tirets : début et fin de l'analyse existante. Bande rouge : plus de "
                   f"{GAP_DAYS} jours sans image analysée, sélection comprise.")
    if not sel:
        st.info("Sélectionne au moins une date à ajouter.")
        st.session_state.pop("mt_confirm", None)
        return

    st.caption(f"{len(sel)} date(s) × {len(ctx.parcels.features)} parcelles — seules ces dates "
               "sont envoyées à Earth Engine, avec les paramètres de l'analyse existante.")
    st.button(f"Compléter l'analyse ({len(sel)} date(s))", key="mt_btn_extend", type="primary",
              icon=":material/add:", on_click=_open_confirm)
    if st.session_state.get("mt_confirm"):
        _confirm_extend(ctx, sorted(sel), period_start, period_end, len(done))


def _open_confirm():
    st.session_state.mt_confirm = True


def _close_confirm():
    st.session_state.pop("mt_confirm", None)


@st.dialog("Compléter l'analyse ?", on_dismiss=_close_confirm)
def _confirm_extend(ctx: Context, sel, period_start, period_end, n_done):
    """Fenêtre de confirmation, puis calcul des seules dates choisies et fusion."""
    old0, old1 = st.session_state.mt_period
    calc = st.session_state.mt_ctx
    params = dict(calc["params_t"])
    buffer_m = calc["geoms_key"].rsplit("|", 1)[1]
    n_before = sum(1 for d in sel if d < old0)
    n_after = sum(1 for d in sel if d > old1)
    st.markdown(
        f"- **{len(sel)} date(s)** ajoutée(s) : {n_before} avant, {n_after} après, "
        f"{len(sel) - n_before - n_after} pendant la période existante\n"
        f"- Période après ajout : {period_start:%d/%m/%Y} → {period_end:%d/%m/%Y}\n"
        f"- Calcul avec les paramètres de l'analyse : buffer {buffer_m} m, "
        f"Cloud Score+ {fr_decimal(params['cs_threshold'])}, marge {params['cloud_buffer_m']} m\n"
        f"- Durée estimée : environ {max(5, 3 * len(sel))} s")
    st.caption(f"Les {n_done} date(s) déjà analysée(s) ne sont pas recalculées. "
               "Pense à enregistrer l'analyse ensuite.")
    c1, c2 = st.columns(2)
    if c1.button("Annuler", key="mt_btn_extend_cancel", width="stretch"):
        _close_confirm()
        st.rerun()
    if c2.button("Compléter", key="mt_btn_extend_ok", type="primary", width="stretch"):
        raws, errors = _run_dates(ctx, sel, calc=calc, geoinfo=st.session_state.mt_geoinfo)
        merged, all_errors = merge_results(st.session_state.mt_raw, raws,
                                           st.session_state.get("mt_errors", []), errors)
        analysis.store(merged, (period_start, period_end), calc, st.session_state.mt_geoinfo,
                       all_errors)
        st.session_state.mt_added = len(raws)
        _close_confirm()
        st.rerun()
