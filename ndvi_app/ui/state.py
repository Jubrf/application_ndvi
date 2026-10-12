"""
État de session Streamlit : réglages, restauration d'une analyse enregistrée, résultats.

Clés utilisées :
  set_*            réglages de la barre latérale (restaurables depuis une session)
  pending_restore  réglages et résultats à appliquer au début du passage suivant
                   (un widget déjà affiché ne peut plus être modifié dans le passage en cours)
  loaded_file      empreinte du fichier de parcelles chargé (changement → résultats effacés)
  opened_session   empreinte de l'analyse enregistrée ouverte
  session_lock     paramètres de calcul de l'analyse enregistrée ouverte (verrouillés)
  os_*             onglet « Analyse à une date » (os_raw : résultat GEE, os_ctx, os_geoinfo)
  mt_*             onglet « Analyse temporelle » (mt_raw : [(date, résultat GEE)], mt_period,
                   mt_ctx, mt_geoinfo, mt_errors, choix d'affichage)
"""
import streamlit as st

from ndvi_app.config import DEFAULT_BUFFER_M, DEFAULT_MIN_CLEAR, DEFAULT_MIN_PIXELS, DEFAULT_THRESHOLDS
from ndvi_app.core.timeseries import DEFAULT_SETTINGS
from ndvi_app.earth_engine.sentinel2 import DEFAULT_PARAMS

SRC_NEW = "Nouvelle analyse : charger des parcelles"
SRC_OPEN = "Ouvrir une analyse enregistrée"

# Réglages qui conditionnent le calcul Earth Engine : verrouillés quand une analyse
# enregistrée est ouverte, pour que les dates ajoutées soient calculées comme les autres.
CALC_SETTING_KEYS = ("set_buffer", "set_cs", "set_cloudbuf", "set_iqr")

SETTING_DEFAULTS = {
    "set_thr": DEFAULT_THRESHOLDS, "set_buffer": DEFAULT_BUFFER_M,
    "set_cs": DEFAULT_PARAMS["cs_threshold"], "set_cloudbuf": DEFAULT_PARAMS["cloud_buffer_m"],
    "set_iqr": DEFAULT_PARAMS["iqr_k"], "set_minclear": DEFAULT_MIN_CLEAR,
    "set_minpix": DEFAULT_MIN_PIXELS,
    "set_minw": DEFAULT_SETTINGS["min_weight"], "set_smooth": DEFAULT_SETTINGS["smooth_days"],
}


def init_settings():
    """Applique une restauration en attente, puis les valeurs par défaut des réglages.
    À appeler avant d'afficher la barre latérale."""
    pending = st.session_state.pop("pending_restore", None)
    if pending:
        st.session_state.update(pending)
    for key, value in SETTING_DEFAULTS.items():
        st.session_state.setdefault(key, value)
    lock = session_lock()
    if lock:
        st.session_state.update(lock)


def session_lock():
    """Paramètres de calcul verrouillés ({clé set_*: valeur}) si une analyse enregistrée
    est ouverte, sinon None."""
    if st.session_state.get("src_mode") != SRC_OPEN or not st.session_state.get("opened_session"):
        return None
    return st.session_state.get("session_lock")


def release_session():
    """Nouvelle analyse : l'analyse enregistrée n'est plus ouverte (réglages déverrouillés,
    elle sera restaurée à nouveau si on la rouvre)."""
    st.session_state.pop("opened_session", None)
    st.session_state.pop("session_lock", None)


def clear_results():
    """Efface les résultats et choix d'affichage des deux onglets."""
    for key in [k for k in st.session_state if k.startswith(("os_", "mt_"))]:
        del st.session_state[key]


def restore_payload(session):
    """Réglages et résultats d'une analyse enregistrée (core.session_format.read_session_zip),
    sous forme de clés de session à appliquer au passage suivant."""
    cfg = session["settings"]
    p0, p1 = session["period"]
    payload = {
        "set_thr": tuple(cfg["thresholds"]), "set_buffer": cfg["buffer_m"],
        "set_cs": cfg["cs_threshold"], "set_cloudbuf": cfg["cloud_buffer_m"],
        "set_iqr": cfg["iqr_k"], "set_minclear": cfg["min_clear"],
        "set_minpix": cfg["min_pixels"], "set_minw": cfg["min_weight"],
        "set_smooth": cfg["smooth_days"],
        "mt_start_y": p0.year, "mt_start_m": p0.month, "mt_end_y": p1.year, "mt_end_m": p1.month,
        "mt_raw": session["raws"], "mt_period": (p0, p1), "mt_geoinfo": session["geoinfo"],
        "mt_ctx": {"geoms_key": session["geoms_key"],
                   "params_t": tuple(sorted(session["params"].items()))},
        "mt_errors": session.get("errors", []),
    }
    payload["session_lock"] = {k: payload[k] for k in CALC_SETTING_KEYS}
    return payload


def stale_warning(saved_ctx, current_ctx):
    """Avertit si les paramètres de calcul ont changé depuis le calcul affiché."""
    if saved_ctx != current_ctx:
        st.info("Les paramètres de calcul (buffer, masque, valeurs aberrantes) ont changé "
                "depuis ce calcul : relance l'analyse pour les appliquer. "
                "Les seuils d'interprétation, de qualité et de la courbe s'appliquent sans relancer.")
