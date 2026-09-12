# -*- coding: utf-8 -*-
"""Carte geographique interactive (Leaflet) : zoom d'echelle, pas un simple CSS."""
import math

from pyproj import Transformer

from schema_reticulaire import CRS_METRIC

_TO_WGS = Transformer.from_crs(CRS_METRIC, "EPSG:4326", always_xy=True)


def _xy_to_latlon(xs, ys):
    lons, lats = _TO_WGS.transform(list(xs), list(ys))
    try:
        return [(float(la), float(lo)) for la, lo in zip(lats, lons)
                if math.isfinite(la) and math.isfinite(lo)]
    except TypeError:
        return [(float(lats), float(lons))] if math.isfinite(lats) else []


def folium_map_html(tracks, stations, height=760):
    """tracks: {xs, ys, color, weight}; stations: {kind, xs, ys, color, nom}."""
    import folium

    pts = []
    for t in tracks:
        pts.extend(_xy_to_latlon(t["xs"], t["ys"]))
    for s in stations:
        pts.extend(_xy_to_latlon(s.get("xs", [s.get("x", 0)]), s.get("ys", [s.get("y", 0)])))
    if not pts:
        loc = [46.6, 2.5]
        m = folium.Map(location=loc, zoom_start=6, tiles="CartoDB positron", control_scale=True)
    else:
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        m = folium.Map(location=[lat, lon], zoom_start=9, tiles="CartoDB positron",
                       control_scale=True, scrollWheelZoom=True)
    for t in tracks:
        latlons = _xy_to_latlon(t["xs"], t["ys"])
        if len(latlons) < 2:
            continue
        w = max(2.0, float(t.get("weight", 3)))
        folium.PolyLine(latlons, color="white", weight=w + 3, opacity=0.9).add_to(m)
        folium.PolyLine(latlons, color=t["color"], weight=w, opacity=0.95).add_to(m)
    for s in stations:
        kind = s.get("kind", "circle")
        nom = s.get("nom") or ""
        if kind == "capsule":
            latlons = _xy_to_latlon(s["xs"], s["ys"])
            if len(latlons) >= 3:
                folium.Polygon(
                    latlons,
                    color="#111111",
                    weight=2,
                    fill=True,
                    fill_color="#FFFFFF",
                    fill_opacity=1.0,
                    tooltip=nom,
                ).add_to(m)
            continue
        latlons = _xy_to_latlon([s["x"]], [s["y"]])
        if not latlons:
            continue
        folium.CircleMarker(
            latlons[0],
            radius=max(6, float(s.get("radius_px", 8))),
            color=s.get("color", "#4E79A7"),
            fill=True,
            fill_color=s.get("color", "#4E79A7"),
            fill_opacity=1.0,
            weight=0,
            tooltip=nom,
        ).add_to(m)
    if len(pts) >= 2:
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(30, 30))
    return m.get_root().render()
