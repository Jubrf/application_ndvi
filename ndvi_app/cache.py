"""
Cache des appels Earth Engine, commun aux deux interfaces.

- Streamlit installé : décorateurs st.cache_data / st.cache_resource d'origine (comportement
  de l'interface Streamlit inchangé, y compris .clear()).
- Sans Streamlit (API de la version React) : cache en mémoire équivalent. Comme Streamlit,
  les paramètres dont le nom commence par « _ » ne font pas partie de la clé ; ttl (s) et
  max_entries sont respectés ; show_spinner est ignoré.
"""
import functools
import inspect
import threading
import time
from collections import OrderedDict

try:
    import streamlit as _st
except ImportError:          # API v5 : Streamlit absent
    _st = None


def _memory_cache(func, ttl=None, max_entries=None):
    sig = inspect.signature(func)
    store = OrderedDict()
    lock = threading.Lock()

    def key_of(args, kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return tuple((k, v) for k, v in bound.arguments.items() if not k.startswith("_"))

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        key = key_of(args, kwargs)
        now = time.time()
        with lock:
            hit = store.get(key)
            if hit is not None and (ttl is None or now - hit[0] < ttl):
                store.move_to_end(key)
                return hit[1]
        value = func(*args, **kwargs)          # calcul hors verrou (appels en parallèle)
        with lock:
            store[key] = (time.time(), value)
            store.move_to_end(key)
            while max_entries and len(store) > max_entries:
                store.popitem(last=False)
        return value

    def clear():
        with lock:
            store.clear()

    wrapper.clear = clear
    return wrapper


def cache_data(**kw):
    """Équivalent de st.cache_data(**kw)."""
    if _st is not None:
        return _st.cache_data(**kw)
    return lambda func: _memory_cache(func, ttl=kw.get("ttl"), max_entries=kw.get("max_entries"))


def cache_resource(func):
    """Équivalent de @st.cache_resource (une seule exécution par jeu d'arguments)."""
    if _st is not None:
        return _st.cache_resource(func)
    return _memory_cache(func)
