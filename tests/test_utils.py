"""Tests for the time, formatting and atomic-IO helpers."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from support import EPOCH, TrackerTestCase

from tracker.utils import (
    CorruptJSONError,
    atomic_write_json,
    format_duration,
    format_timestamp,
    now,
    parse_timestamp,
    read_json,
)


class TestTimestamps(unittest.TestCase):
    def test_now_is_timezone_aware_and_whole_seconds(self) -> None:
        moment = now()
        self.assertIsNotNone(moment.tzinfo)
        self.assertEqual(0, moment.microsecond)

    def test_format_matches_the_documented_iso_8601_shape(self) -> None:
        self.assertEqual("2026-07-14T19:42:18+03:00", format_timestamp(EPOCH))

    def test_format_rejects_a_naive_datetime(self) -> None:
        with self.assertRaises(ValueError):
            format_timestamp(datetime(2026, 7, 14, 19, 42, 18))

    def test_round_trip_preserves_the_instant(self) -> None:
        self.assertEqual(EPOCH, parse_timestamp(format_timestamp(EPOCH)))

    def test_parse_rejects_a_timestamp_without_an_offset(self) -> None:
        with self.assertRaises(CorruptJSONError):
            parse_timestamp("2026-07-14T19:42:18")

    def test_parse_rejects_garbage(self) -> None:
        for bad in ("not-a-date", "", 42, None):
            with self.subTest(value=bad):
                with self.assertRaises(CorruptJSONError):
                    parse_timestamp(bad)  # type: ignore[arg-type]

    def test_utc_timestamps_survive_the_round_trip(self) -> None:
        utc = datetime(2026, 7, 14, 16, 42, 18, tzinfo=timezone.utc)
        self.assertEqual(utc, parse_timestamp(format_timestamp(utc)))


class TestFormatDuration(unittest.TestCase):
    def test_formats_hours_minutes_and_seconds(self) -> None:
        cases = {0: "0:00:00", 59: "0:00:59", 60: "0:01:00", 3661: "1:01:01", 36000: "10:00:00"}
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(expected, format_duration(seconds))

    def test_clamps_a_negative_duration_to_zero(self) -> None:
        self.assertEqual("0:00:00", format_duration(-120))


class TestAtomicWrite(TrackerTestCase):
    def test_writes_a_readable_document(self) -> None:
        path = self.root / "out.json"
        atomic_write_json(path, {"hello": "world"})
        self.assertEqual({"hello": "world"}, read_json(path))

    def test_creates_missing_parent_directories(self) -> None:
        path = self.root / "deep" / "nested" / "out.json"
        atomic_write_json(path, [1, 2, 3])
        self.assertEqual([1, 2, 3], read_json(path))

    def test_leaves_no_temporary_files_behind(self) -> None:
        atomic_write_json(self.root / "out.json", {"a": 1})
        self.assertEqual(["out.json"], [p.name for p in self.root.iterdir()])

    def test_overwrites_the_previous_document_completely(self) -> None:
        path = self.root / "out.json"
        atomic_write_json(path, {"long": "x" * 500})
        atomic_write_json(path, {"short": 1})
        # A non-atomic overwrite could leave trailing bytes from the longer doc.
        self.assertEqual({"short": 1}, json.loads(path.read_text()))

    def test_does_not_leave_a_temp_file_when_serialisation_fails(self) -> None:
        with self.assertRaises(TypeError):
            atomic_write_json(self.root / "out.json", {"bad": object()})
        self.assertEqual([], list(self.root.iterdir()))


class TestReadJSON(TrackerTestCase):
    def test_missing_file_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_json(self.root / "nope.json")

    def test_malformed_file_raises_corrupt_json(self) -> None:
        path: Path = self.root / "bad.json"
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(CorruptJSONError):
            read_json(path)


if __name__ == "__main__":
    unittest.main()
