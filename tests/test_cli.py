import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from logic_analyzer.capture import save_capture, validate_capture
from logic_analyzer.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.data = {"version": 1, "sample_rate_hz": 1000, "channels": 1,
                     "samples": [1, 1, 0, 0, 1], "trigger_index": 2, "source": "fixture"}
        self.capture = save_capture(self.data, self.path / "capture.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_digital_only_report_works_at_low_sample_rate(self):
        report = self.path / "wave.html"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["report", str(self.capture), "--no-uart", "--output", str(report)]), 0)
        self.assertIn("Digital waveform capture", report.read_text(encoding="utf-8"))

    def test_channel_must_exist_in_capture(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(main(["decode", str(self.capture), "--channel", "1"]), 1)
        self.assertIn("outside", stderr.getvalue())

    def test_sqlite_error_is_reported_cleanly(self):
        invalid = self.path / "bad.db"
        invalid.write_text("not a database")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["list", "--db", str(invalid)]), 1)

    def test_required_trigger_and_integer_version(self):
        data = dict(self.data)
        data.pop("trigger_index")
        with self.assertRaises(ValueError):
            validate_capture(data)
        data = dict(self.data, version=True)
        with self.assertRaises(ValueError):
            validate_capture(data)
