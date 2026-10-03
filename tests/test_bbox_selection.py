import pandas as pd
import pytest

from utils.bbox_selection import (
    bounds_from_drawing,
    filter_by_bounds,
    format_bounds,
    latest_drawing_bounds,
    parse_coordinate_bounds,
)


def _rectangle(west, south, east, north):
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [west, north],
                    [east, north],
                    [east, south],
                    [west, south],
                ]
            ],
        },
    }


def test_bounds_from_drawing_reads_rectangle():
    assert bounds_from_drawing(_rectangle(100.0, -5.0, 106.5, 1.25)) == (
        100.0,
        -5.0,
        106.5,
        1.25,
    )


def test_bounds_from_drawing_rejects_points_and_garbage():
    point = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}}
    assert bounds_from_drawing(point) is None
    assert bounds_from_drawing(None) is None
    assert bounds_from_drawing(_rectangle(100, 1, 100, 2)) is None
    assert bounds_from_drawing(_rectangle(100, -5, 181, 2)) is None
    assert bounds_from_drawing(_rectangle(100, -5, 105, 91)) is None
    assert bounds_from_drawing(_rectangle(100, -5, float("nan"), 2)) is None


def test_latest_drawing_bounds_uses_last_rectangle():
    drawings = [_rectangle(90, -10, 95, -5), _rectangle(120, 0, 125, 5)]
    assert latest_drawing_bounds(drawings) == (120, 0, 125, 5)
    assert latest_drawing_bounds([]) is None
    assert latest_drawing_bounds(None) is None


def test_filter_by_bounds_keeps_points_inside_inclusive():
    df = pd.DataFrame(
        {
            "bujur": [100.0, 105.0, 110.0, 103.0],
            "lintang": [-2.0, 0.0, 0.0, 5.0],
        }
    )
    result = filter_by_bounds(df, (100.0, -2.0, 105.0, 2.0))
    assert result["bujur"].tolist() == [100.0, 105.0]
    assert filter_by_bounds(df, None) is df


def test_format_bounds_uses_hemisphere_labels():
    assert format_bounds((95.0, -2.0, 100.5, 4.0)) == (
        "95.00° BT – 100.50° BT, 2.00° LS – 4.00° LU"
    )
    assert format_bounds((108.61, -12.037, 132.165, 3.515)) == (
        "108.61° BT – 132.165° BT, 12.037° LS – 3.515° LU"
    )


def test_parse_coordinate_bounds_accepts_decimal_text_and_preserves_precision():
    assert parse_coordinate_bounds("3.515", "108.61", "132.165", "-12.037") == (
        108.61, -12.037, 132.165, 3.515,
    )
    assert parse_coordinate_bounds("3,515", "108,61", "132,165", "-12,037") == (
        108.61, -12.037, 132.165, 3.515,
    )


@pytest.mark.parametrize("coordinates", [
    ("", "108", "132", "-12"),
    ("north", "108", "132", "-12"),
    ("nan", "108", "132", "-12"),
    ("91", "108", "132", "-12"),
    ("3", "-181", "132", "-12"),
    ("3", "108", "181", "-12"),
    ("3", "108", "108", "-12"),
    ("-12", "108", "132", "-12"),
])
def test_parse_coordinate_bounds_rejects_invalid_boxes(coordinates):
    with pytest.raises(ValueError):
        parse_coordinate_bounds(*coordinates)


def test_publication_frame_centres_on_selection_with_padding():
    from utils.publication_map import _selection_frame_bounds

    west, south, east, north = _selection_frame_bounds((100.0, -4.0, 104.0, 0.0), 10)
    assert (west + east) / 2 == 102.0
    assert (south + north) / 2 == -2.0
    assert round(east - west, 6) == 4.8
    assert round(north - south, 6) == 4.8
