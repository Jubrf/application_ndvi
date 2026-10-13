"""
Compléter une analyse existante (Python pur) : dates à ajouter et fusion des résultats.

Les résultats d'une analyse sont une liste [(date ISO, résultat Earth Engine de la date)]
et une liste d'erreurs « jj/mm/aaaa : message » (dates qui n'ont pas pu être calculées).
"""
import datetime


def image_covers(d):
    """L'image (entrée de list_dates) couvre-t-elle au moins une partie des parcelles ?"""
    return bool(d.get("cover_pct")) and d.get("clear_pct") is not None


def dates_to_add(available, raws):
    """
    available : entrées de list_dates sur la nouvelle période
    raws      : résultats déjà présents
    Retourne (dates déjà analysées triées, nouvelles entrées couvrant les parcelles,
              nombre d'images non analysées qui ne couvrent pas les parcelles).
    """
    done = sorted(datetime.date.fromisoformat(d) for d, _ in raws)
    done_set = set(done)
    not_done = [d for d in available if d["date"] not in done_set]
    new = [d for d in not_done if image_covers(d)]
    return done, new, len(not_done) - len(new)


def error_date(err):
    """Date ISO d'un message d'erreur « jj/mm/aaaa : … » (None si illisible)."""
    try:
        return datetime.datetime.strptime(err[:10], "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def merge_results(old_raws, new_raws, old_errors, new_errors):
    """Fusionne les dates ajoutées : résultats triés par date ; une date en erreur qui a été
    recalculée avec succès sort de la liste des erreurs. Retourne (raws, erreurs)."""
    added = {d for d, _ in new_raws}
    errors = [e for e in old_errors if error_date(e) not in added] + list(new_errors)
    return sorted(list(old_raws) + list(new_raws), key=lambda r: r[0]), errors
