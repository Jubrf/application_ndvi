"""
Classeur Excel des résultats de l'analyse temporelle (bouton « Exporter »).

Code commun à l'interface Streamlit (ui/banner.py) et à l'API de la version React (api/).
"""
import pandas as pd

from ndvi_app.exports.excel import to_excel
from ndvi_app.exports.excel_charts import add_parcel_charts


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


def weather_table(days, p_start, p_end):
    """Météo journalière de la période (onglet Excel « Météo »)."""
    rows = [{"Date": pd.Timestamp(d).date(), "Pluie_mm": v[0], "Humidite_sol": v[1],
             "Tmin_C": v[2]} for d, v in sorted((days or {}).items())
            if str(p_start) <= d <= str(p_end)]
    return pd.DataFrame(rows, columns=["Date", "Pluie_mm", "Humidite_sol", "Tmin_C"])


def results_workbook(df_long, synthese, detail, courbes, p_start, p_end, low, high,
                     column_help, pick, cols, weather_days=None, weather_df=None):
    """
    Fichier .xlsx : Synthèse, Graphiques, NDVI par date, Détail, Courbes lissées, Technique,
    Météo (si disponible), Lexique.
    pick / cols : sélection des colonnes affichées (ui.columns.pick et ses listes
                  {"synth", "detail", "tech"}).
    weather_df  : onglet Météo déjà calculé (sinon calculé depuis weather_days).
    """
    synth_view = pick(synthese, cols["synth"])
    detail_view = pick(detail.assign(Date=detail["Date"].dt.date), cols["detail"])
    courbes_view = courbes.assign(Date=courbes["Date"].dt.date)[
        ["ID", "Date", "NDVI_lisse", "Phase", "Incertain"]]
    sheets = {"Synthèse": synth_view,
              "NDVI par date": pivot_for(pivot_all(detail), list(synthese["ID"])),
              "Détail": detail_view, "Courbes lissées": courbes_view,
              "Technique": pick(df_long, cols["tech"])}
    if weather_days:
        sheets["Météo"] = (weather_df if weather_df is not None
                           else weather_table(weather_days, p_start, p_end))
    return to_excel(
        sheets, column_help,
        extra=lambda book: add_parcel_charts(book, synthese, detail, courbes,
                                             p_start, p_end, low, high))
