"""
Contexte d'un passage du script : réglages de la barre latérale et parcelles chargées.

Remplace les variables globales de l'ancien fichier unique : chaque fonction de
l'interface reçoit explicitement ce dont elle a besoin.
"""
from dataclasses import dataclass
from functools import cached_property

from ndvi_app.earth_engine import sentinel2 as gee
from ndvi_app.ui.columns import column_help


@dataclass(frozen=True)
class Settings:
    """Réglages de la barre latérale."""
    low: float                 # seuil sol nu / couvert peu développé
    high: float                # seuil couvert peu / bien développé
    buffer_m: int              # buffer intérieur des parcelles (m)
    cs_threshold: float        # seuil Cloud Score+
    cloud_buffer_m: int        # marge autour des nuages (m)
    iqr_k: float               # exclusion des valeurs aberrantes (k × IQR)
    min_clear: int             # part minimale de pixels clairs (%)
    min_pixels: int            # nombre minimal de pixels utilisés
    min_weight: float          # poids minimal d'une mesure retenue dans la courbe
    smooth_days: int           # lissage de la courbe (jours)

    @property
    def gee_params(self) -> dict:
        """Paramètres du calcul Earth Engine (masque, valeurs aberrantes)."""
        return {**gee.DEFAULT_PARAMS, "cs_threshold": self.cs_threshold,
                "cloud_buffer_m": self.cloud_buffer_m, "iqr_k": self.iqr_k}

    @property
    def params_t(self) -> tuple:
        """Mêmes paramètres sous forme hashable (clé de cache Streamlit)."""
        return tuple(sorted(self.gee_params.items()))

    @property
    def ts_settings(self) -> dict:
        """Réglages de l'analyse temporelle modifiables dans l'interface."""
        return {"min_weight": self.min_weight, "smooth_days": self.smooth_days}


@dataclass
class Parcels:
    """Parcelles chargées et géométries d'analyse."""
    features: list             # [{"geometry": shapely WGS84, "properties": dict}]
    ids: list                  # identifiants uniques, même ordre que features
    fields: list               # attributs du fichier
    id_field: str | None       # attribut choisi comme identifiant (None si aucun attribut)
    source_name: str           # nom du fichier d'origine
    file_hash: str             # empreinte du fichier (clé de cache)
    geoinfo: list              # sortie de core.geometry.prepare_all (buffer appliqué)
    region: dict               # union simplifiée des parcelles (GeoJSON)
    geoms_key: str             # clé de cache des géométries : "<file_hash>|<buffer>"

    @property
    def analysis_geojsons(self) -> list:
        return [g["geojson"] for g in self.geoinfo]

    @cached_property
    def bounds(self) -> tuple:
        geoms = [f["geometry"] for f in self.features]
        return (min(g.bounds[0] for g in geoms), min(g.bounds[1] for g in geoms),
                max(g.bounds[2] for g in geoms), max(g.bounds[3] for g in geoms))


@dataclass
class Context:
    settings: Settings
    parcels: Parcels

    @cached_property
    def column_help(self) -> dict:
        """Définitions des colonnes (dépendent des seuils affichés)."""
        return column_help(self.settings.low, self.settings.high)

    def calc_context(self) -> dict:
        """Ce qui conditionne les calculs GEE : sert à détecter des résultats périmés."""
        return {"geoms_key": self.parcels.geoms_key, "params_t": self.settings.params_t}

    def run_analysis(self, date_str: str) -> dict:
        """Statistiques Earth Engine de toutes les parcelles à une date."""
        return gee.compute_day_stats(date_str, self.parcels.geoms_key, self.settings.params_t,
                                     self.parcels.analysis_geojsons, self.parcels.region)

    def list_dates(self, start, end) -> list:
        """Dates d'images Sentinel-2 sur la période, avec le % de ciel clair sur les parcelles."""
        return gee.list_dates(str(start), str(end), self.parcels.file_hash,
                              self.settings.params_t, self.parcels.region)
