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
    return rect.union(Point(e1).buffer(half_wid, quad_segs=24)).union(
        Point(e2).buffer(half_wid, quad_segs=24))


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


def emprise_gare(traits, marge):
    """Emprise d'un symbole de gare posé en travers des traits qui la desservent.

    traits : [(t, w)] position latérale (le long de l'axe perpendiculaire à la
    voie, relative au point gare) et largeur de chaque trait desservant.
    marge : débord du symbole au-delà des traits.
    Renvoie (t_centre, demi_long, demi_larg). Une seule mission : rond
    (demi_long == demi_larg) de diamètre trait + 2 × marge : proportionnel à
    la fréquence, il déborde juste du trait (le liseré blanc le détache).
    Plusieurs : capsule qui couvre tout le faisceau desservi, aussi large
    que le trait le plus fin plus la marge.
    """
    lo = min(t - w / 2.0 for t, w in traits)
    hi = max(t + w / 2.0 for t, w in traits)
    t_c = (lo + hi) / 2.0
    if len(traits) == 1:
        r = 0.5 * traits[0][1] + marge
        return t_c, r, r
    demi_long = (hi - lo) / 2.0 + marge
    demi_larg = min(w for _t, w in traits) / 2.0 + marge
    return t_c, max(demi_long, demi_larg), demi_larg


def directions_alignees(segments, pos, eps):
    """sid -> directions cardinales {(±1, 0), (0, ±1)} vers ses voisines
    schématiques alignées (même X ou même Y)."""
    dirs = {}
    for a, b in segments:
        if a not in pos or b not in pos:
            continue
        (xa, ya), (xb, yb) = pos[a], pos[b]
        if abs(xa - xb) < eps and abs(ya - yb) >= eps:
            d = (0, 1 if yb > ya else -1)
        elif abs(ya - yb) < eps and abs(xa - xb) >= eps:
            d = (1 if xb > xa else -1, 0)
        else:
            continue
        dirs.setdefault(a, set()).add(d)
        dirs.setdefault(b, set()).add((-d[0], -d[1]))
    return dirs


def _sens(v):
    return 0 if v == 0 else (1 if v > 0 else -1)


def trace_coude(pa, pb, dirs_a, dirs_b, eps):
    """Tronçon schématique a -> b : droit si aligné, sinon un coude.

    Le coude est placé pour que chaque branche prolonge un rail existant
    (direction déjà prise par une voisine alignée) : une bifurcation suit le
    tronc puis tourne, au lieu de quitter le faisceau dès la gare de départ
    et de faire un décroché parallèle au tronc. À égalité : horizontal puis
    vertical depuis a (comportement historique, déterministe).
    """
    (xa, ya), (xb, yb) = pa, pb
    if abs(xa - xb) < eps or abs(ya - yb) < eps:
        return [(xa, ya), (xb, yb)]
    sx, sy = _sens(xb - xa), _sens(yb - ya)
    h_dabord = int((sx, 0) in dirs_a) + int((0, -sy) in dirs_b)
    v_dabord = int((0, sy) in dirs_a) + int((-sx, 0) in dirs_b)
    if v_dabord > h_dabord:
        return [(xa, ya), (xa, yb), (xb, yb)]
    return [(xa, ya), (xb, ya), (xb, yb)]


def offset_line(geom, offset, tol=None):
    """Décale une LineString sans faire planter GEOS sur les tracés alpins.

    ``offset_curve`` sur un tracé très sinueux (ex. Aix–Briançon) avec un
    décalage de centaines de mètres peut lever ``GEOSException: cannot create
    std::vector larger than max_size()``. On simplifie d'abord.

    tol : tolérance de simplification. Elle doit être LA MÊME pour toutes les
    missions d'un faisceau : sinon chaque mission décale un squelette
    différent et des jours blancs apparaissent entre couleurs dans les
    courbes. Par défaut dérivée du décalage (ancien comportement).
    """
    if geom is None or getattr(geom, "is_empty", True):
        return geom
    try:
        off = float(offset)
    except (TypeError, ValueError):
        return geom
    if not math.isfinite(off) or abs(off) < 1e-9:
        return geom
    if tol is None:
        tol = max(25.0, min(abs(off) * 0.5, 250.0))

    def _clean(ls):
        coords = []
        for x, y, *_rest in ls.coords:
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            if coords and abs(x - coords[-1][0]) < 1e-6 and abs(y - coords[-1][1]) < 1e-6:
                continue
            coords.append((x, y))
        if len(coords) < 2:
            return None
        out = LineString(coords)
        if out.length <= 0:
            return None
        try:
            simp = out.simplify(tol, preserve_topology=False)
            if (simp is not None and not simp.is_empty
                    and simp.geom_type == "LineString" and simp.length > 0):
                out = simp
        except Exception:
            pass
        return out

    def _one(ls):
        ls = _clean(ls)
        if ls is None:
            return None
        try:
            res = ls.offset_curve(off, join_style="round", quad_segs=4)
        except Exception:
            return ls
        if res is None or res.is_empty:
            return ls
        return res

    if geom.geom_type == "LineString":
        src = [geom]
    else:
        src = [g for g in getattr(geom, "geoms", []) if g.geom_type == "LineString"]
    parts = []
    for p in src:
        q = _one(p)
        if q is None or q.is_empty:
            continue
        if q.geom_type == "LineString":
            parts.append(q)
        else:
            parts.extend(g for g in getattr(q, "geoms", [])
                         if g.geom_type == "LineString" and not g.is_empty)
    if not parts:
        return geom
    if len(parts) == 1:
        return parts[0]
    return MultiLineString(parts)


def wrap_nom_gare(nom, max_chars=16):
    """Coupe un nom trop long en deux lignes (espace près du milieu)."""
    nom = (nom or "").strip()
    if len(nom) <= max_chars:
        return nom
    mid = len(nom) // 2
    left = nom.rfind(" ", 0, max(mid + 4, min(len(nom), max_chars + 2)))
    if left < 3:
        left = nom.find(" ", mid)
    if left < 3:
        return nom[:mid].rstrip("-") + "\n" + nom[mid:].lstrip("-")
    return nom[:left] + "\n" + nom[left + 1 :]


def contourne_gare(geom, px, py, radius, prefer_dx, prefer_dy):
    """Contourne le disque (px, py, radius) du cote `prefer`, sans inverser."""
    if geom is None or geom.is_empty or radius <= 0:
        return geom
    return contourne_obstacle(
        geom, Point(px, py).buffer(radius), px, py, prefer_dx, prefer_dy,
    )
