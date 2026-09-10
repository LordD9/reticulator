"""Regression: GEOS offset_curve must not crash on dense alpine-like polylines."""
import math
from shapely.geometry import LineString


def offset_line(geom, offset):
    # Mirror of app.offset_line (keep in sync).
    if geom is None or getattr(geom, "is_empty", True):
        return geom
    off = float(offset)
    if not math.isfinite(off) or abs(off) < 1e-9:
        return geom

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
        tol = max(25.0, min(abs(off) * 0.5, 250.0))
        simp = out.simplify(tol, preserve_topology=False)
        if (simp is not None and not simp.is_empty
                and simp.geom_type == "LineString" and simp.length > 0):
            out = simp
        return out

    def _one(ls):
        ls = _clean(ls)
        if ls is None:
            return None
        try:
            try:
                res = ls.offset_curve(off, join_style="mitre", mitre_limit=2.5)
            except TypeError:
                res = ls.offset_curve(off)
        except Exception:
            return ls
        if res is None or res.is_empty:
            return ls
        return res

    q = _one(geom)
    return geom if q is None or q.is_empty else q


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


if __name__ == "__main__":
    test_large_offset_does_not_raise()
    test_zero_offset_is_identity()
    print("ok")
