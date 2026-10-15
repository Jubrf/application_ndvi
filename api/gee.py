"""
Connexion à Google Earth Engine pour l'API (variables d'environnement du serveur).

GEE_SERVICE_ACCOUNT : adresse du compte de service (facultative si la clé est le fichier JSON)
GEE_PRIVATE_KEY     : clé privée, telle que dans les secrets Streamlit — fichier JSON du compte
                      de service ou clé PEM (« -----BEGIN PRIVATE KEY----- … »).
La clé est nettoyée des erreurs de copier-coller courantes (guillemets autour, « \\n » écrits
en toutes lettres au lieu de retours à la ligne) avant d'être passée à Earth Engine.
"""
import json
import os
import threading
import time

from ndvi_app.earth_engine import sentinel2 as gee
from ndvi_app.logs import log

_lock = threading.Lock()
_state = {"ok": False, "message": None, "checked": 0.0}
RETRY_S = 30            # nouvel essai de connexion après un échec


class GeeUnavailable(RuntimeError):
    pass


def _clean_key(raw):
    key = (raw or "").strip()
    for q in ('"""', "'''", '"', "'"):
        if len(key) >= 2 * len(q) and key.startswith(q) and key.endswith(q):
            key = key[len(q):-len(q)].strip()
            break
    if key.startswith("{"):
        try:                       # JSON tolérant (retours à la ligne réels dans la clé)
            return json.dumps(json.loads(key, strict=False)), "JSON"
        except ValueError:
            return key, "JSON illisible"
    if "\\n" in key and "\n" not in key:
        key = key.replace("\\n", "\n")
    return key, "PEM"


def configured():
    return bool(os.environ.get("GEE_PRIVATE_KEY", "").strip())


def connect(force=False):
    """Initialise Earth Engine une fois ; retourne (ok, message)."""
    with _lock:
        if _state["ok"] and not force:
            return True, _state["message"]
        if not force and _state["message"] and time.time() - _state["checked"] < RETRY_S:
            return False, _state["message"]
        _state["checked"] = time.time()
        if not configured():
            _state.update(ok=False, message="Clé Earth Engine absente : renseigner GEE_PRIVATE_KEY "
                                            "(et GEE_SERVICE_ACCOUNT) dans l'onglet Environment "
                                            "du service Render.")
            return False, _state["message"]
        key, kind = _clean_key(os.environ.get("GEE_PRIVATE_KEY"))
        account = os.environ.get("GEE_SERVICE_ACCOUNT", "").strip().strip('"').strip("'")
        if kind == "JSON":
            account = account or json.loads(key).get("client_email", "")
        try:
            gee.init_gee(account, key)
        except Exception as e:      # message lisible pour la page Projet
            log(f"Connexion Earth Engine impossible : {type(e).__name__}: {e}")
            hint = (" Vérifier la clé GEE_PRIVATE_KEY (format " + kind + ") et le compte "
                    "GEE_SERVICE_ACCOUNT dans l'onglet Environment de Render.")
            _state.update(ok=False, message=f"{type(e).__name__} : {str(e)[:300]}.{hint}")
            return False, _state["message"]
        _state.update(ok=True, message=f"Connecté ({account or 'compte de service'}, clé {kind}).")
        return True, _state["message"]


def ensure():
    ok, msg = connect()
    if not ok:
        raise GeeUnavailable(msg)
