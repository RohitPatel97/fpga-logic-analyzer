import copy
import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from logic_analyzer.capture import export_csv, export_vcd, load_capture, save_capture, validate_capture
from logic_analyzer.store import CaptureStore


def fixture():
    return {"version": 1, "sample_rate_hz": 1000000, "channels": 8, "trigger_index": 2,
            "samples": [1, 3, 254, 255, 0], "source": "test", "metadata": {"board": "fixture"}}


class CaptureTests(unittest.TestCase):
    def test_json_and_database_preserve_samples_and_metadata(self):
        data = fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save_capture(data, path / "capture.json")
            self.assertEqual(load_capture(path / "capture.json"), data)
            with CaptureStore(path / "captures.sqlite3") as store:
                first = store.record(data, "first")
                second = store.record(data, "second")
                self.assertNotEqual(first, second)
                self.assertEqual(store.load(first), data)
                self.assertEqual(store.list()[0]["id"], second)
                store.record_decode(first, {"baud": 100000}, [{"value": 85}])
            with CaptureStore(path / "captures.sqlite3") as reopened:
                self.assertEqual(reopened.load(second), data)

    def test_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as directory, CaptureStore(Path(directory) / "data.db") as store:
            capture_id = store.record(fixture())
            with store.connection:
                store.connection.execute("UPDATE captures SET samples=? WHERE id=?", (b"broken", capture_id))
            with self.assertRaisesRegex(ValueError, "corrupt"):
                store.load(capture_id)

    def test_bad_capture_does_not_overwrite_good_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "capture.json"
            original = fixture()
            save_capture(original, target)
            invalid = fixture()
            invalid["samples"][0] = 256
            with self.assertRaises(ValueError):
                save_capture(invalid, target)
            self.assertEqual(load_capture(target), original)

    def test_validation_rejects_invalid_rate_width_trigger_and_samples(self):
        for key, value in [("sample_rate_hz", 0), ("sample_rate_hz", math.nan),
                           ("sample_rate_hz", True), ("channels", 0), ("channels", 33),
                           ("trigger_index", 5), ("trigger_index", -1),
                           ("samples", [-1]), ("samples", [256]), ("samples", [True])]:
            with self.subTest(key=key, value=value):
                data = fixture()
                data[key] = value
                with self.assertRaises(ValueError):
                    validate_capture(data)

    def test_empty_abort_and_no_trigger_are_valid(self):
        data = fixture()
        data.update(samples=[], trigger_index=None)
        self.assertEqual(validate_capture(data), data)

    def test_csv_and_vcd_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            export_csv(fixture(), path / "out.csv")
            with (path / "out.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(float(rows[0]["relative_to_trigger_s"]), -2e-6)
            self.assertEqual(rows[2]["D0"], "0")
            export_vcd(fixture(), path / "out.vcd")
            vcd = (path / "out.vcd").read_text()
            self.assertIn("$timescale 1ps $end", vcd)
            self.assertIn("#2000000\n", vcd)
            self.assertTrue(vcd.endswith("#5000000\n"))


if __name__ == "__main__":
    unittest.main()
