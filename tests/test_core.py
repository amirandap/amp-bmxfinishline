"""Tests for geometry helpers and OCR fusion logic."""

from __future__ import annotations

import pytest

from app.core.geometry import point_in_polygon, side_of_line, has_crossed, bbox_bottom_center
from app.core.ocr import fuse_readings, _filter_digits
from app.core.types import Detection


# ═══════════════════════════════════════════════════════════════════
# Geometry tests
# ═══════════════════════════════════════════════════════════════════

class TestPointInPolygon:
    """Ray-casting point-in-polygon."""

    SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10]]

    def test_inside(self):
        assert point_in_polygon(5, 5, self.SQUARE) is True

    def test_outside(self):
        assert point_in_polygon(15, 5, self.SQUARE) is False

    def test_on_edge(self):
        # Edge behaviour is implementation-defined; just ensure no crash
        result = point_in_polygon(0, 5, self.SQUARE)
        assert isinstance(result, bool)

    def test_outside_above(self):
        assert point_in_polygon(5, -1, self.SQUARE) is False

    def test_triangle(self):
        tri = [[0, 0], [10, 0], [5, 10]]
        assert point_in_polygon(5, 5, tri) is True
        assert point_in_polygon(0, 10, tri) is False

    def test_degenerate_polygon(self):
        # Fewer than 3 points → always False
        assert point_in_polygon(0, 0, [[0, 0], [1, 1]]) is False

    def test_complex_polygon(self):
        # L-shape
        poly = [[0, 0], [5, 0], [5, 5], [10, 5], [10, 10], [0, 10]]
        assert point_in_polygon(2, 2, poly) is True   # inside bottom-left
        assert point_in_polygon(8, 8, poly) is True    # inside top-right
        assert point_in_polygon(8, 2, poly) is False   # outside upper-right cutout


class TestSideOfLine:
    """Cross-product sign relative to a directed line."""

    LINE = [[0, 0], [10, 0]]  # horizontal line along x-axis

    def test_above_is_positive(self):
        # Point above → left of left-to-right line → positive
        assert side_of_line(5, 5, self.LINE) > 0

    def test_below_is_negative(self):
        assert side_of_line(5, -5, self.LINE) < 0

    def test_on_line_is_zero(self):
        assert side_of_line(5, 0, self.LINE) == 0.0

    def test_vertical_line(self):
        vline = [[5, 0], [5, 10]]
        assert side_of_line(0, 5, vline) > 0   # left of upward line
        assert side_of_line(10, 5, vline) < 0  # right


class TestHasCrossed:
    def test_sign_change(self):
        assert has_crossed(1.0, -1.0) is True
        assert has_crossed(-3.0, 5.0) is True

    def test_no_change(self):
        assert has_crossed(1.0, 2.0) is False
        assert has_crossed(-1.0, -2.0) is False

    def test_zero_not_a_crossing(self):
        assert has_crossed(0.0, 1.0) is False
        assert has_crossed(1.0, 0.0) is False
        assert has_crossed(0.0, 0.0) is False


class TestBboxBottomCenter:
    def test_simple(self):
        cx, cy = bbox_bottom_center(10, 20, 30, 40)
        assert cx == 20.0
        assert cy == 40.0


# ═══════════════════════════════════════════════════════════════════
# Crossing detection integration
# ═══════════════════════════════════════════════════════════════════

class TestCrossingDetector:
    def test_single_crossing(self):
        from app.core.crossing import CrossingDetector

        finish = [[0, 50], [100, 50]]  # horizontal line at y=50
        cd = CrossingDetector(finish, cooldown_seconds=0.0)

        # First frame: rider above line (y=30)
        det1 = Detection(track_id=1, x1=40, y1=10, x2=60, y2=30, confidence=0.9, class_id=0)
        events = cd.update([det1], timestamp_ms=0.0, frame_index=0)
        assert len(events) == 0

        # Second frame: rider below line (y=70)
        det2 = Detection(track_id=1, x1=40, y1=50, x2=60, y2=70, confidence=0.9, class_id=0)
        events = cd.update([det2], timestamp_ms=33.0, frame_index=1)
        assert len(events) == 1
        assert events[0].track_id == 1

    def test_no_double_finish(self):
        from app.core.crossing import CrossingDetector

        finish = [[0, 50], [100, 50]]
        cd = CrossingDetector(finish, cooldown_seconds=0.0)

        det_above = Detection(track_id=1, x1=40, y1=10, x2=60, y2=30, confidence=0.9, class_id=0)
        det_below = Detection(track_id=1, x1=40, y1=50, x2=60, y2=70, confidence=0.9, class_id=0)

        cd.update([det_above], 0.0, 0)
        cd.update([det_below], 33.0, 1)
        # Track 1 already finished – moving back shouldn't trigger again
        events = cd.update([det_above], 66.0, 2)
        assert len(events) == 0

    def test_cooldown_suppresses(self):
        from app.core.crossing import CrossingDetector

        finish = [[0, 50], [100, 50]]
        cd = CrossingDetector(finish, cooldown_seconds=5.0)

        # Track 1 crosses
        cd.update(
            [Detection(track_id=1, x1=40, y1=10, x2=60, y2=30, confidence=0.9, class_id=0)],
            0.0, 0,
        )
        cd.update(
            [Detection(track_id=1, x1=40, y1=50, x2=60, y2=70, confidence=0.9, class_id=0)],
            33.0, 1,
        )

        # Track 2 crosses within cooldown window → suppressed
        cd.update(
            [Detection(track_id=2, x1=40, y1=10, x2=60, y2=30, confidence=0.9, class_id=0)],
            100.0, 3,
        )
        events = cd.update(
            [Detection(track_id=2, x1=40, y1=50, x2=60, y2=70, confidence=0.9, class_id=0)],
            133.0, 4,
        )
        assert len(events) == 0  # within 5s cooldown


# ═══════════════════════════════════════════════════════════════════
# OCR fusion tests
# ═══════════════════════════════════════════════════════════════════

class TestFilterDigits:
    def test_clean(self):
        assert _filter_digits("123", 3) == "123"

    def test_extracts_digits(self):
        assert _filter_digits("bib #42!", 3) == "42"

    def test_truncates_long(self):
        assert _filter_digits("12345", 3) == "123"

    def test_empty_returns_none(self):
        assert _filter_digits("abc", 3) is None


class TestFuseReadings:
    def test_clear_winner(self):
        readings = [("42", 0.9), ("42", 0.85), ("42", 0.8), ("17", 0.3)]
        result = fuse_readings(readings)
        assert result.bib == "42"
        assert result.confidence > 0.8

    def test_empty_readings(self):
        result = fuse_readings([])
        assert result.bib is None
        assert result.confidence == 0.0

    def test_tie_broken_by_total_score(self):
        readings = [("1", 0.9), ("2", 0.8)]
        result = fuse_readings(readings)
        assert result.bib == "1"  # higher confidence wins

    def test_multiple_candidates_returned(self):
        readings = [("10", 0.9), ("20", 0.8), ("30", 0.7), ("40", 0.6)]
        result = fuse_readings(readings, top_n=3)
        assert len(result.candidates) == 3

    def test_repeated_low_vs_single_high(self):
        # Many low-confidence readings of "99" vs one high-confidence "7"
        readings = [("99", 0.3), ("99", 0.3), ("99", 0.3), ("99", 0.3), ("7", 0.95)]
        result = fuse_readings(readings)
        # Total score for 99 = 1.2, for 7 = 0.95  → 99 wins by total weight
        assert result.bib == "99"
