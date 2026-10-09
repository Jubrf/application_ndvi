"""Classeur Excel des résultats : un onglet par tableau, plus un onglet « Lexique »."""
import io

import pandas as pd

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def to_excel(sheets, column_help, extra=None):
    """
    sheets      : dict nom d'onglet -> DataFrame (dans l'ordre des onglets)
    column_help : dict colonne -> définition ; l'onglet Lexique reprend les colonnes présentes
    extra       : fonction optionnelle appelée sur le classeur (ex. ajout de graphiques) ;
                  un onglet « Graphiques » créé par elle est placé en 2e position.
    Retourne le fichier .xlsx (bytes).
    """
    lexique = pd.DataFrame(
        [{"Colonne": c, "Définition": h} for c, h in column_help.items()
         if any(c in df.columns for df in sheets.values())])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in {**sheets, "Lexique": lexique}.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for i, col in enumerate(df.columns, start=1):
                width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)])
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width + 2, 90)
            ws.freeze_panes = "B2"
        if extra is not None:
            extra(xw.book)
            if "Graphiques" in xw.book.sheetnames:
                xw.book.move_sheet("Graphiques", offset=1 - xw.book.sheetnames.index("Graphiques"))
    return buf.getvalue()
