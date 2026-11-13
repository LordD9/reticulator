# -*- coding: utf-8 -*-
from shapely.geometry import LineString, Point

from dessin_gares import capsule_polygon, contourne_gare, contourne_obstacle, wrap_nom_gare


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


def test_wrap_nom_court_inchange():
    assert wrap_nom_gare("Arles") == "Arles"


def test_wrap_nom_long_deux_lignes():
    out = wrap_nom_gare("Aix-en-Provence TGV", max_chars=12)
    assert "\n" in out
    assert out.replace("\n", " ") == "Aix-en-Provence TGV" or "Aix-en-Provence" in out


def test_rond_proportionnel_a_la_frequence():
    from dessin_gares import emprise_gare
    _t, r1, _ = emprise_gare([(0.0, 10.0)], 2.0)
    _t, r4, _ = emprise_gare([(0.0, 40.0)], 2.0)
    assert (r1, r4) == (7.0, 22.0)


def test_capsule_couvre_les_traits_desservis():
    from dessin_gares import emprise_gare
    # orange (40) à gauche, bleu (10) à droite, jointifs : bords -40 et +10
    t_c, hl, hw = emprise_gare([(-20.0, 40.0), (5.0, 10.0)], 2.0)
    assert (t_c, hl, hw) == (-15.0, 27.0, 7.0)


def test_coude_prolonge_le_tronc():
    from dessin_gares import directions_alignees, trace_coude
    # Tronc vertical A(0,0)-T(0,10) ; branche A -> B(5,10) : monter le long du
    # tronc puis tourner, pas partir à l'horizontale depuis A.
    pos = {"A": (0, 0), "T": (0, 10), "B": (5, 10)}
    dirs = directions_alignees([("A", "T")], pos, 0.01)
    assert trace_coude(pos["A"], pos["B"], dirs.get("A", set()), set(), 0.01) == [
        (0, 0), (0, 10), (5, 10)]
    assert trace_coude((0, 0), (5, 10), set(), set(), 0.01) == [(0, 0), (5, 0), (5, 10)]
