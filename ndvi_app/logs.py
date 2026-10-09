"""Journal du serveur (visible dans « Manage app » sur Streamlit Cloud)."""
import datetime


def log(msg):
    """Ligne horodatée dans les logs du serveur."""
    print(f"[ndvi {datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)
