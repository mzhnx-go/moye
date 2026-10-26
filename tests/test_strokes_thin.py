from sketchpad.core.strokes import dist, thin

def test_dist_3_4_5():
    assert dist((0.0, 0.0), (3.0, 4.0)) == 5.0

def test_thin_drops_points_closer_than_threshold_and_keeps_first_and_last():
    # (0,0) (1.49,0) 被吞；(3.0,0) 保留；末点 (3.4,0) 距离 0.4 仍强制保留
    pts = [(0.0, 0.0), (1.49, 0.0), (3.0, 0.0), (3.4, 0.0)]
    assert thin(pts) == [(0.0, 0.0), (3.0, 0.0), (3.4, 0.0)]

def test_thin_boundary_exactly_threshold_is_kept():
    assert thin([(0.0, 0.0), (1.5, 0.0), (9.0, 0.0)]) == [(0.0, 0.0), (1.5, 0.0), (9.0, 0.0)]

def test_thin_boundary_just_under_threshold_is_dropped():
    assert thin([(0.0, 0.0), (1.499, 0.0), (9.0, 0.0)]) == [(0.0, 0.0), (9.0, 0.0)]

def test_thin_single_point_stroke_survives():
    assert thin([(2.0, 7.0)]) == [(2.0, 7.0)]

def test_thin_empty_returns_empty():
    assert thin([]) == []
