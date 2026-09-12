# -*- coding: utf-8 -*-
from shapely.geometry import LineString, Point

from dessin_gares import capsule_polygon, contourne_gare, contourne_obstacle


def test_capsule_allongee_selon_la_voie():
    poly = capsule_polygon(0, 0, 1, 0, half_len=20, half_wid=5)
    assert poly.is_valid and not poly.is_empty
    assert poly.bounds[2] - poly.bounds[0] > poly.bounds[3] - poly.bounds[1]


def test_contourne_rond_passe_du_cote_prefere():
    geom = LineString([(-30, 0), (30, 0)])
    out = contourne_gare(geom, 0, 0, 10, 0, 1)
    ys = [c[1] for c in out.coords]
    assert max(ys) >= 10 - 1e-6


def test_contourne_capsule_sort_de_la_forme():
    geom = LineString([(-40, 0), (40, 0)])
    cap = capsule_polygon(0, 0, 1, 0, half_len=18, half_wid=6)
    obs = cap.buffer(4)
    out = contourne_obstacle(geom, obs, 0, 0, 0, 1, extra=2)
    assert out.intersects(Point(0, 0)) is False or not cap.contains(Point(out.coords[len(out.coords)//2]))
    apex_y = max(c[1] for c in out.coords)
    assert apex_y > 6
    assert not obs.contains(Point(0, apex_y))
