"""
Export de la carte des phases en KML (Google My Maps, Google Earth).

Contraintes Google My Maps prises en compte :
  - pas de géométries multiples : une parcelle en plusieurs morceaux devient
    plusieurs formes portant le même nom ;
  - pas de dossiers ni de bulles HTML : description en texte simple ;
  - attributs en ExtendedData (colonnes « ID », « Phase »…) pour pouvoir
    « Styliser par colonne de données → Phase » si les couleurs ne sont pas reprises.
Google Earth reprend directement les couleurs des styles.
"""
from xml.sax.saxutils import escape

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

from ndvi_app.core.geometry import strip_z


def _polygons(geom):
    geom = strip_z(geom)
    if isinstance(geom, Polygon):
        return [geom] if not geom.is_empty else []
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        out = []
        for g in geom.geoms:
            out += _polygons(g)
        return out
    return []


def _kml_color(hex_color, opacity):
    """'#rrggbb' + opacité 0–1 → 'aabbggrr' (ordre KML)."""
    h = hex_color.lstrip("#")
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    aa = f"{int(round(max(0.0, min(1.0, opacity)) * 255)):02x}"
    return f"{aa}{bb}{gg}{rr}".lower()


def _coords(ring):
    return " ".join(f"{x:.7f},{y:.7f},0" for x, y in ring.coords)


def build_kml(title, entries, legend_text=""):
    """
    title   : nom du document (ex. « Phases au 15/01/2026 »)
    entries : liste de dicts {"geometry" (shapely WGS84), "name", "color" '#rrggbb',
              "opacity" 0–1, "props" (dict ordonné : colonnes et description)}
    Retourne le KML (bytes UTF-8).
    """
    styles, placemarks = {}, []
    for e in entries:
        sid = "s_" + _kml_color(e["color"], e.get("opacity", 0.6))
        styles[sid] = (_kml_color(e["color"], e.get("opacity", 0.6)))
        props = e.get("props", {})
        desc = "\n".join(f"{k} : {v}" for k, v in props.items() if k != "ID")
        data = "".join(f'<Data name="{escape(str(k))}"><value>{escape(str(v))}</value></Data>'
                       for k, v in props.items())
        for poly in _polygons(e["geometry"]):
            inner = "".join(
                f"<innerBoundaryIs><LinearRing><coordinates>{_coords(r)}</coordinates>"
                f"</LinearRing></innerBoundaryIs>" for r in poly.interiors)
            placemarks.append(
                f"<Placemark><name>{escape(str(e['name']))}</name>"
                f"<description>{escape(desc)}</description>"
                f"<styleUrl>#{sid}</styleUrl>"
                f"<ExtendedData>{data}</ExtendedData>"
                f"<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing><coordinates>"
                f"{_coords(poly.exterior)}</coordinates></LinearRing></outerBoundaryIs>{inner}"
                f"</Polygon></Placemark>")

    style_xml = "".join(
        f'<Style id="{sid}"><LineStyle><color>ff000000</color><width>1</width></LineStyle>'
        f"<PolyStyle><color>{col}</color><fill>1</fill><outline>1</outline></PolyStyle></Style>"
        for sid, col in styles.items())
    kml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>{escape(title)}</name><description>{escape(legend_text)}</description>"
           f"{style_xml}{''.join(placemarks)}</Document></kml>")
    return kml.encode("utf-8")
