"""
Carte folium des parcelles colorées par phase (affichée dans l'appli et exportée en HTML).

Appli : fond plan OpenStreetMap par défaut, satellite Esri au choix.
Export HTML (for_export=True) : fonds Esri uniquement, satellite par défaut — les serveurs
OpenStreetMap bloquent les fichiers HTML ouverts en local (« Access blocked »).
"""
import folium

from ndvi_app.config import COLOR_MAP

ESRI_SATELLITE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ESRI_STREETS = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"

_BOX_STYLE = ("position:absolute;z-index:1000;background:rgba(255,255,255,.92);color:#222;"
              "padding:8px 10px;border-radius:6px;font:12px/1.3 sans-serif;"
              "box-shadow:0 1px 4px rgba(0,0,0,.3)")


def build_map(features, items, bounds, legend_extra=None, title=None, for_export=False):
    """
    features : parcelles (géométries shapely WGS84), même ordre que items
    items    : une entrée par parcelle {"id", "color", "tooltip" (HTML), "opacity"}
    bounds   : (minx, miny, maxx, maxy) de l'ensemble des parcelles
    legend_extra : [(libellé, couleur)] ajoutés à la légende des phases
    title    : titre affiché sur la carte (et titre de la page HTML)
    Chaque parcelle est une couche nommée par son identifiant (liste des couches).
    """
    minx, miny, maxx, maxy = bounds
    m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=14, tiles=None)
    if for_export:
        folium.TileLayer(tiles=ESRI_STREETS, attr="Esri World Street Map", name="Fond plan",
                         show=False).add_to(m)
        folium.TileLayer(tiles=ESRI_SATELLITE, attr="Esri World Imagery", name="Fond satellite",
                         show=True).add_to(m)
    else:
        folium.TileLayer(tiles=ESRI_SATELLITE, attr="Esri World Imagery", name="Fond satellite",
                         show=False).add_to(m)
        folium.TileLayer("OpenStreetMap", name="Fond plan (OpenStreetMap)", show=True).add_to(m)

    labels = folium.FeatureGroup(name="Identifiants des parcelles", show=True)
    for feat, it in zip(features, items):
        folium.GeoJson(
            feat["geometry"].__geo_interface__,
            name=str(it["id"]),
            style_function=lambda x, c=it["color"], o=it.get("opacity", 0.6): {
                "fillColor": c, "color": "black", "weight": 1, "fillOpacity": o},
            tooltip=folium.Tooltip(it["tooltip"]),
        ).add_to(m)
        pt = feat["geometry"].representative_point()
        folium.Marker(
            [pt.y, pt.x],
            icon=folium.DivIcon(icon_size=(0, 0), html=(
                '<div style="transform:translate(-50%,-50%);white-space:nowrap;'
                'font:600 11px sans-serif;color:#fff;pointer-events:none;'
                'text-shadow:0 0 3px #000,0 0 2px #000">' + str(it["id"]) + "</div>")),
        ).add_to(labels)
    labels.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)

    entries = list(COLOR_MAP.items()) + (legend_extra or [])
    rows_html = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0">'
        f'<span style="width:14px;height:14px;background:{col};opacity:.75;'
        f'border:1px solid #333;display:inline-block"></span>{lab}</div>'
        for lab, col in entries)
    m.get_root().html.add_child(folium.Element(
        f'<div style="{_BOX_STYLE};bottom:24px;left:12px">{rows_html}</div>'))
    if title:
        m.get_root().header.add_child(folium.Element(f"<title>{title}</title>"))
        m.get_root().html.add_child(folium.Element(
            f'<div style="{_BOX_STYLE};top:12px;left:50%;transform:translateX(-50%);'
            f'font-size:14px;font-weight:600">{title}</div>'))
    return m


def legend_text(legend_extra=None):
    """Légende en texte simple (description du document KML)."""
    return " ; ".join(f"{lab} : {col}" for lab, col in
                      list(COLOR_MAP.items()) + (legend_extra or []))
