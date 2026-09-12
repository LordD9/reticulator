# -*- coding: utf-8 -*-
"""Formes de gares (rond / capsule type RATP) et contournement des passages."""
import math

from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import substring


def capsule_polygon(x, y, dx, dy, half_len, half_wid):
    """Stade / « suppositoire » orienté selon (dx, dy), centré en (x, y).

    half_len : demi-longueur le long de la voie.
    half_wid : demi-largeur (rayon des arrondis).
    """
    n = math.hypot(dx, dy)
    if n < 1e-9:
        dx, dy, n = 1.0, 0.0, 1.0
    dx, dy = dx / n, dy / n
    half_wid = max(float(half_wid), 1e-6)
    half_len = max(float(half_len), half_wid)
    nx, ny = -dy, dx
    body = half_len - half_wid
    if body <= 1e-9:
        return Point(x, y).buffer(half_wid)
    e1 = (x + dx * body, y + dy * body)
    e2 = (x - dx * body, y - dy * body)
    rect = Polygon([
        (x + dx * body + nx * half_wid, y + dy * body + ny * half_wid),
        (x + dx * body - nx * half_wid, y + dy * body - ny * half_wid),
        (x - dx * body - nx * half_wid, y - dy * body - ny * half_wid),
        (x - dx * body + nx * half_wid, y - dy * body + ny * half_wid),
    ])
    return rect.union(Point(e1).buffer(half_wid)).union(Point(e2).buffer(half_wid))


def _apex_hors_obstacle(obstacle, px, py, pdx, pdy, extra=0.0):
    d_max = 0.0
    ext = getattr(obstacle, "exterior", None)
    coords = list(ext.coords) if ext is not None else []
    if not coords and hasattr(obstacle, "geoms"):
        for g in obstacle.geoms:
            if g.geom_type == "Polygon":
                coords.extend(list(g.exterior.coords))
    for bx, by in coords:
        d_max = max(d_max, (bx - px) * pdx + (by - py) * pdy)
    dist = d_max + extra
    if dist < 1e-6:
        dist = extra if extra > 0 else 1.0
    return (px + pdx * dist, py + pdy * dist)


def contourne_obstacle(geom, obstacle, px, py, prefer_dx, prefer_dy, extra=0.0):
    """Remplace le morceau dans `obstacle` par un V vers un apex hors forme."""
    if geom is None or geom.is_empty or obstacle is None or obstacle.is_empty:
        return geom
    n = math.hypot(prefer_dx, prefer_dy)
    if n < 1e-9:
        prefer_dx, prefer_dy, n = 1.0, 0.0, 1.0
    prefer_dx, prefer_dy = prefer_dx / n, prefer_dy / n
    apex = _apex_hors_obstacle(obstacle, px, py, prefer_dx, prefer_dy, extra)

    def _one(ls):
        if ls is None or ls.is_empty or ls.length <= 0:
            return ls
        if not ls.intersects(obstacle):
            return ls
        hits = ls.intersection(obstacle.boundary)
        pts = []
        if hits.geom_type == "Point":
            pts = [hits]
        elif hits.geom_type == "MultiPoint":
            pts = list(hits.geoms)
        elif hits.geom_type == "GeometryCollection":
            pts = [g for g in hits.geoms if g.geom_type == "Point"]
        elif hits.geom_type == "LineString":
            pts = [Point(hits.coords[0]), Point(hits.coords[-1])]
        pts = sorted(pts, key=lambda p: ls.project(p))
        start_in = obstacle.intersects(Point(ls.coords[0]))
        end_in = obstacle.intersects(Point(ls.coords[-1]))
        if start_in and end_in:
            return LineString([ls.coords[0], apex, ls.coords[-1]])
        if not pts:
            return ls
        if start_in:
            after = substring(ls, ls.project(pts[0]), ls.length)
            coords = [apex]
            if after is not None and not after.is_empty:
                coords.extend(list(after.coords))
            return LineString(coords) if len(coords) >= 2 else ls
        if end_in:
            before = substring(ls, 0.0, ls.project(pts[-1]))
            coords = []
            if before is not None and not before.is_empty:
                coords.extend(list(before.coords))
            coords.append(apex)
            return LineString(coords) if len(coords) >= 2 else ls
        if len(pts) >= 2:
            d0, d1 = ls.project(pts[0]), ls.project(pts[-1])
            if d1 <= d0:
                return ls
            before = substring(ls, 0.0, d0)
            after = substring(ls, d1, ls.length)
            coords = []
            if before is not None and not before.is_empty:
                coords.extend(list(before.coords))
            coords.append(apex)
            if after is not None and not after.is_empty:
                ac = list(after.coords)
                if coords and ac and coords[-1] == ac[0]:
                    ac = ac[1:]
                coords.extend(ac)
            return LineString(coords) if len(coords) >= 2 else ls
        return ls

    if geom.geom_type == "LineString":
        return _one(geom)
    parts = []
    for g in getattr(geom, "geoms", []):
        if g.geom_type == "LineString":
            ng = _one(g)
            if ng is not None and not ng.is_empty:
                parts.append(ng)
    if not parts:
        return geom
    if len(parts) == 1:
        return parts[0]
    return MultiLineString(parts)


def contourne_gare(geom, px, py, radius, prefer_dx, prefer_dy):
    """Contourne le disque (px, py, radius) du cote `prefer`, sans inverser."""
    if geom is None or geom.is_empty or radius <= 0:
        return geom
    return contourne_obstacle(
        geom, Point(px, py).buffer(radius), px, py, prefer_dx, prefer_dy,
    )
