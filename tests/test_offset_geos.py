"""Regression: GEOS offset_curve must not crash on dense alpine-like polylines."""
from shapely.geometry import LineString

from dessin_gares import offset_line


def _switchback(n=400, span=80000.0, amp=800.0):
    coords = []
    step = span / n
    for i in range(n + 1):
        x = i * step
        y = amp if i % 2 else 0.0
        coords.append((x, y))
    return LineString(coords)


def test_large_offset_does_not_raise():
    geom = _switchback()
    out = offset_line(geom, 400.0)
    assert out is not None and not out.is_empty


def test_zero_offset_is_identity():
    geom = LineString([(0, 0), (1000, 0)])
    assert offset_line(geom, 0.0) is geom


def test_tolerance_commune_traits_jointifs():
    """Deux missions d'un faisceau décalées avec la même tolérance restent
    jointives : la distance entre leurs traits égale la somme des demi-largeurs."""
    geom = _switchback(n=60, span=30000.0, amp=300.0)
    a = offset_line(geom, 50.0, tol=120.0)
    b = offset_line(geom, -50.0, tol=120.0)
    mid = geom.interpolate(0.5, normalized=True)
    da, db = a.distance(mid), b.distance(mid)
    assert abs((da + db) - 100.0) < 60.0
    assert abs(a.hausdorff_distance(b) - 100.0) < 25.0
