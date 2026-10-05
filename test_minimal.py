# Application de test minimale (diagnostic de déploiement Streamlit Cloud).
# Aucune dépendance autre que streamlit, aucun appel Earth Engine.
import sys

import streamlit as st

st.title("Test de déploiement")
st.write("Si ce texte s'affiche, Streamlit Cloud démarre correctement.")
st.write(f"Python {sys.version.split()[0]} · Streamlit {st.__version__}")
st.write("Secrets présents :", sorted(st.secrets.keys()) if len(st.secrets) else "aucun")
