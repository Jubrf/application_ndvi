"""Mise en forme des valeurs et des dates affichées."""
import pandas as pd


def fmt(v, digits=3, suffix=""):
    """Nombre arrondi en texte, « — » si absent ou non numérique."""
    try:
        if v is None or pd.isna(v):
            return "—"
        return f"{float(v):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def fr_decimal(v):
    """Seuil à deux décimales avec virgule (ex. 0,25)."""
    return f"{v:.2f}".replace(".", ",")


# ------------------------------------------------------------------ dates d'images
def covers(d):
    """L'image (entrée de list_dates) couvre-t-elle au moins une partie des parcelles ?"""
    return bool(d.get("cover_pct")) and d.get("clear_pct") is not None


def usable(d, threshold):
    """Image couvrant les parcelles avec au moins `threshold` % de ciel clair."""
    return covers(d) and d["clear_pct"] >= threshold


def date_label(d, min_clear):
    """Libellé d'une date d'image dans les listes de choix."""
    if not covers(d):
        return f"⛔ {d['date']:%d/%m/%Y} — ne couvre pas les parcelles"
    mark = "✅" if d["clear_pct"] >= min_clear else "⚠️"
    label = f"{mark} {d['date']:%d/%m/%Y} — {d['clear_pct']:.0f} % de ciel clair"
    if d.get("cover_pct") is not None and d["cover_pct"] < 99.5:
        label += f" · couvre {d['cover_pct']:.0f} % des parcelles"
    return label
