# -*- coding: utf-8 -*-
"""Carte géographique interactive (Leaflet) : zoom d'échelle, pas un simple CSS.

Les traits et les gares sont dimensionnés en PIXELS : décalage des missions
(leaflet-polylineoffset), épaisseur et symboles de gare restent cohérents à
tous les niveaux de zoom (avant : capsules en mètres, traits en pixels, les
capsules grossissaient en zoomant et décrochaient des traits).
"""
import html
import math

from pyproj import Transformer

from schema_reticulaire import CRS_METRIC

_TO_WGS = Transformer.from_crs(CRS_METRIC, "EPSG:4326", always_xy=True)

IGN_PLAN_URL = (
    "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png"
    "&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
)
IGN_ATTR = '&copy; <a href="https://www.ign.fr/">IGN</a> — Géoplateforme'


def fournisseurs_tuiles(fond="ign"):
    """Fournisseurs contextily (export PNG) dans l'ordre d'essai.

    Plan IGN (Géoplateforme, sans clé) d'abord : les tuiles OSM renvoient
    une image « 403 Access blocked » aux clients non navigateurs, sans
    erreur HTTP exploitable, et cette image finissait dans l'export.
    """
    import contextily as cx

    ign = cx.providers.GeoportailFrance.plan
    osm = cx.providers.OpenStreetMap.Mapnik
    esri = cx.providers.Esri.WorldGrayCanvas
    return [osm, ign, esri] if fond == "osm" else [ign, esri]


def _xy_to_latlon(xs, ys):
    lons, lats = _TO_WGS.transform(list(xs), list(ys))
    return [(float(la), float(lo)) for la, lo in zip(lats, lons)
            if math.isfinite(la) and math.isfinite(lo)]


def _icone_gare(s):
    """DivIcon HTML d'une gare, en pixels, posée sur les traits décalés.

    s : {kind, color, t, hl, hw, perp} — t = décalage du centre le long de
    `perp` (unitaire, repère métrique y vers le nord), hl / hw = demi-longueur
    / demi-largeur en pixels.
    """
    import folium

    px, py = s.get("perp", (1.0, 0.0))
    # Repère écran : y vers le bas.
    sx, sy = px, -py
    t = float(s.get("t", 0.0))
    hl = max(2.0, float(s.get("hl", 4.0)))
    hw = max(2.0, float(s.get("hw", hl)))
    ang = math.degrees(math.atan2(sy, sx))
    if s.get("kind") == "capsule":
        style = "background:#fff;border:2px solid #111;"
    else:
        style = f"background:{html.escape(s.get('color', '#4E79A7'))};border:1.5px solid #fff;"
    div = (
        f'<div style="position:absolute;left:{-hl:.1f}px;top:{-hw:.1f}px;'
        f'width:{2 * hl:.1f}px;height:{2 * hw:.1f}px;border-radius:{hw:.1f}px;'
        f'box-sizing:border-box;{style}'
        f'transform:translate({t * sx:.1f}px,{t * sy:.1f}px) rotate({ang:.1f}deg);"></div>'
    )
    return folium.DivIcon(html=div, icon_size=(0, 0), icon_anchor=(0, 0),
                          class_name="reticufer-gare")


def folium_map_html(tracks, stations, height=760, fond="ign"):
    """tracks : {xs, ys, color, weight, offset} (géométrie de voie NON décalée,
    weight / offset en pixels) ; stations : {kind, x, y, color, nom, t, hl, hw, perp}.

    fond : 'ign' (défaut, Plan IGN sans clé) ou 'osm'. Les deux fonds restent
    commutables dans le contrôle de couches Leaflet.
    """
    import folium
    from folium.plugins import PolyLineOffset

    pts = []
    for t in tracks:
        pts.extend(_xy_to_latlon(t["xs"], t["ys"]))
    for s in stations:
        pts.extend(_xy_to_latlon([s["x"]], [s["y"]]))
    if pts:
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        m = folium.Map(location=[lat, lon], zoom_start=9, tiles=None,
                       control_scale=True, scrollWheelZoom=True)
    else:
        m = folium.Map(location=[46.6, 2.5], zoom_start=6, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles=IGN_PLAN_URL, attr=IGN_ATTR, name="Plan IGN",
        overlay=False, control=True, show=(fond != "osm"), max_zoom=19,
    ).add_to(m)
    folium.TileLayer(
        tiles="OpenStreetMap", name="OpenStreetMap",
        overlay=False, control=True, show=(fond == "osm"),
    ).add_to(m)

    # Casing blanc d'abord (sous tout le faisceau), puis les couleurs.
    casing = folium.FeatureGroup(name="casing", control=False).add_to(m)
    lignes = folium.FeatureGroup(name="Missions", control=False).add_to(m)
    for t in tracks:
        latlons = _xy_to_latlon(t["xs"], t["ys"])
        if len(latlons) < 2:
            continue
        w = max(2.0, float(t.get("weight", 3)))
        off = float(t.get("offset", 0.0))
        PolyLineOffset(latlons, offset=off, color="white", weight=w + 2,
                       opacity=1.0, line_cap="butt").add_to(casing)
        PolyLineOffset(latlons, offset=off, color=t["color"], weight=w,
                       opacity=1.0).add_to(lignes)

    for s in stations:
        latlons = _xy_to_latlon([s["x"]], [s["y"]])
        if not latlons:
            continue
        folium.Marker(latlons[0], icon=_icone_gare(s),
                      tooltip=s.get("nom") or None).add_to(m)

    if len(pts) >= 2:
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(30, 30))
    folium.LayerControl(collapsed=True).add_to(m)
    return m.get_root().render()
