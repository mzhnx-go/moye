from sketchpad.core.strokes import Stroke, stroke_distance

def test_distance_to_segment_not_just_vertices():
    s = Stroke([(0.0, 0.0), (10.0, 0.0)], "#000000", 3.0)
    assert stroke_distance(s, (5.0, 2.0)) == 2.0

def test_distance_to_single_point_stroke_is_radial():
    s = Stroke([(3.0, 4.0)], "#000000", 3.0)
    assert stroke_distance(s, (0.0, 0.0)) == 5.0

def test_distance_outside_segment_ends_clamps_to_endpoint():
    s = Stroke([(0.0, 0.0), (10.0, 0.0)], "#000000", 3.0)
    assert stroke_distance(s, (20.0, 0.0)) == 10.0

def test_distance_to_empty_stroke_is_infinite():
    import math
    assert stroke_distance(Stroke([], "#000000", 3.0), (1.0, 1.0)) == math.inf

def test_distance_takes_the_nearest_segment():
    s = Stroke([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], "#000000", 3.0)
    assert stroke_distance(s, (9.0, 5.0)) == 1.0
