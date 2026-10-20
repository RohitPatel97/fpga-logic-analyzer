"""Headless verification of waveform segmentation and standalone reports."""

from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from logic_analyzer.viewer import _pixel_envelopes, export_html, waveform_runs


class _ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.payload = ""
        self.inside_payload = False

    def handle_starttag(self, tag, attributes):
        self.tags.append(tag)
        if tag == "script" and dict(attributes).get("id") == "capture-data":
            self.inside_payload = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside_payload = False

    def handle_data(self, data):
        if self.inside_payload:
            self.payload += data


def capture(samples=None):
    return {"version": 1, "sample_rate_hz": 1_000_000, "channels": 2, "trigger_index": None, "samples": [0, 1, 1, 3, 2, 0] if samples is None else samples, "source": "test-rtl", "metadata": {"channel_names": ["RX", "Clock"]}}


class ViewerTests(unittest.TestCase):
    def test_runs_preserve_transitions(self):
        self.assertEqual(list(waveform_runs([0, 1, 1, 3, 2, 0], 0)), [(0, 1, 0), (1, 4, 1), (4, 6, 0)])
        self.assertEqual(list(waveform_runs([0, 1, 1, 3, 2, 0], 1)), [(0, 3, 0), (3, 5, 1), (5, 6, 0)])

    def test_windowed_runs_keep_original_sample_indices(self):
        self.assertEqual(list(waveform_runs([0, 1, 1, 3, 2, 0], 0, 2, 5)), [(2, 4, 1), (4, 5, 0)])
        self.assertEqual(list(waveform_runs([], 0)), [])

    def test_zoomed_out_envelope_preserves_single_sample_pulse(self):
        samples = [0] * 1000
        samples[501] = 0b101
        envelope = _pixel_envelopes(samples, 0, 1000, 10)
        self.assertEqual(len(envelope), 10)
        self.assertEqual(envelope[5], (500, 600, 0, 0b101))
        self.assertTrue(all(low == high == 0 for index, (_, _, low, high) in enumerate(envelope) if index != 5))

    def test_html_is_standalone_and_preserves_capture(self):
        original = capture()
        original["trigger_index"] = 3
        with tempfile.TemporaryDirectory() as folder:
            path = export_html(original, Path(folder) / "reports" / "capture.html", baud=None)
            document = path.read_text(encoding="utf-8")
        parser = _ReportParser()
        parser.feed(document)
        payload = json.loads(parser.payload)
        self.assertEqual(payload["capture"], original)
        self.assertEqual(payload["names"], ["RX", "Clock"])
        self.assertIn("canvas", parser.tags)
        self.assertNotIn('<script src=', document)
        self.assertIn("Go to trigger", document)

    def test_report_decodes_uart_and_escapes_untrusted_text(self):
        # 'A', transmitted LSB first at exactly 10 samples per bit.
        bits = [1, 1, 0] + [(ord("A") >> bit) & 1 for bit in range(8)] + [1, 1]
        original = capture([bit for bit in bits for _ in range(10)])
        original["source"] = '<img src=x onerror="bad()">'
        original["metadata"]["channel_names"][0] = '</script><script>bad()</script>'
        with tempfile.TemporaryDirectory() as folder:
            document = export_html(original, Path(folder) / "capture.html").read_text(encoding="utf-8")
        parser = _ReportParser()
        parser.feed(document)
        payload = json.loads(parser.payload)
        self.assertEqual(payload["frames"][0]["value"], ord("A"))
        self.assertIn("0x41", document)
        self.assertNotIn("<img", document)
        self.assertEqual(parser.tags.count("script"), 2)
        self.assertEqual(payload["capture"]["metadata"], original["metadata"])

    def test_empty_capture_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            path = export_html(capture([]), Path(folder) / "empty.html", baud=None)
            self.assertIn("Capture contains no samples", path.read_text(encoding="utf-8"))

    def test_html_preserves_full_64_bit_words_for_javascript(self):
        original = capture([(1 << 63) + 1, (1 << 64) - 1])
        original["channels"] = 64
        with tempfile.TemporaryDirectory() as folder:
            document = export_html(original, Path(folder) / "wide.html", baud=None).read_text(encoding="utf-8")
        parser = _ReportParser()
        parser.feed(document)
        payload = json.loads(parser.payload)
        self.assertEqual(payload["sample_words"], [str(value) for value in original["samples"]])
        self.assertIn("sample_words.map(BigInt)", document)

    def test_invalid_capture_or_decoder_parameters_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "report.html"
            for replacement in ({"version": 2}, {"sample_rate_hz": 0}, {"channels": 0}, {"samples": [4]}, {"trigger_index": 6}):
                with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                    export_html({**capture(), **replacement}, target, baud=None)
            with self.assertRaises(ValueError):
                export_html(capture(), target, channel=2)
            with self.assertRaises(ValueError):
                export_html(capture(), target, baud=0)


if __name__ == "__main__":
    unittest.main()
