"""Real Renode + Icarus integration checks (opt in with RUN_RENODE_TESTS=1)."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from renode_capture import BASE, RenodeMonitor, _monitor_path, _monitor_string, capture


def reference_samples() -> list[int]:
    """Construct UART frames from bits independently of the C# source algorithm."""
    uart = [1] * 32
    for byte in (0x55, 0x41, 0xA3, 0x0A):
        frame = [0] + [(byte >> bit) & 1 for bit in range(8)] + [1]
        for bit in frame:
            uart.extend([bit] * 10)
        uart.extend([1] * 20)
    assert len(uart) == 512
    return [((tick % 128) * 2) | level for tick, level in enumerate(uart)]


class MonitorArguments(unittest.TestCase):
    def test_monitor_rejects_multiline_arguments(self):
        for path in ('bad\nquit', 'bad"path', 'bad;quit', 'bad\rpath'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                _monitor_string(path)

    def test_monitor_quotes_spaces(self):
        self.assertEqual(_monitor_string("C:\\my tools\\vvp.exe"), '"C:/my tools/vvp.exe"')


@unittest.skipUnless(os.getenv("RUN_RENODE_TESTS") == "1", "set RUN_RENODE_TESTS=1 for co-simulation")
class RenodeIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renode = Path(os.getenv("RENODE_EXE") or shutil.which("renode") or "missing-renode")
        cls.vvp = Path(os.getenv("VVP_EXE") or shutil.which("vvp") or ROOT / "build/tools/mingw64/bin/vvp.exe")
        cls.design = Path(os.getenv("BRIDGE_VVP") or ROOT / "build/bridge.vvp")
        for path in (cls.renode, cls.vvp, cls.design):
            if not path.is_file():
                raise RuntimeError(f"Renode integration dependency missing: {path}")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="logic-analyzer-renode-")
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_capture(self, **kwargs):
        result = capture(self.renode, self.vvp, self.design, self.directory, **kwargs)
        log = (self.directory / "renode.log").read_text(encoding="utf-8")
        self.assertNotIn("[ERROR]", log)
        self.assertNotIn("Unhandled exception", log)
        self.assertIn("Disposed.", log)
        return result

    def test_all_512_samples_default_falling_trigger(self):
        result = self.run_capture()
        self.assertEqual(result["samples"], reference_samples())
        self.assertEqual(result["trigger_index"], 32)
        self.assertEqual(result["sample_rate_hz"], 1_000_000)
        self.assertEqual(result["metadata"]["generated_ticks"], 1024)

    def test_immediate_capture_needs_no_decoder(self):
        result = self.run_capture(trigger_mode=0)
        self.assertEqual(result["samples"], reference_samples())
        self.assertEqual(result["trigger_index"], 0)

    def test_divider_preserves_exact_samples_and_effective_rate(self):
        result = self.run_capture(divider=2, length=256, pretrigger=16)
        self.assertEqual(result["samples"], reference_samples()[1::2])
        self.assertEqual(result["sample_rate_hz"], 500_000)
        self.assertEqual(result["trigger_index"], 16)

    def test_missing_trigger_is_timeout_and_disposes(self):
        with self.assertRaisesRegex(TimeoutError, "capture did not complete"):
            capture(self.renode, self.vvp, self.design, self.directory,
                    length=64, pretrigger=0, mask=0, max_ticks=128)
        log = (self.directory / "renode.log").read_text(encoding="utf-8")
        self.assertIn("Disposed.", log)
        self.assertNotIn("Unhandled exception", log)

    def test_virtual_clock_and_full_machine_reset(self):
        script = self.directory / "start.resc"
        script.write_text("\n".join([
            f"include {_monitor_path(ROOT / 'renode/LogicAnalyzer.cs')}",
            'mach create "reset-test"',
            f"machine LoadPlatformDescription {_monitor_path(ROOT / 'renode/logic_analyzer.repl')}",
            f"sysbus.analyzer Configure {_monitor_string(self.vvp.resolve())} {_monitor_string(self.design.resolve())}",
        ]) + "\n", encoding="utf-8")
        with RenodeMonitor(self.renode.resolve(), script, self.directory / "renode.log") as monitor:
            monitor.command(f"sysbus WriteDoubleWord {BASE + 12:#x} 23")
            monitor.command("sysbus.analyzer StartSource")
            # Monitor calls and process startup do not advance virtual time.
            self.assertEqual(monitor.number("sysbus.analyzer GeneratedSamples"), 0)
            monitor.command('emulation RunFor "0.000010"')
            self.assertEqual(monitor.number("sysbus.analyzer GeneratedSamples"), 10)
            self.assertEqual(monitor.number("sysbus.analyzer GeneratedSamples"), 10)
            monitor.command("machine Reset")
            self.assertEqual(monitor.number("sysbus.analyzer GeneratedSamples"), 0)
            self.assertEqual(monitor.number(f"sysbus ReadDoubleWord {BASE + 12:#x}"), 512)
            self.assertEqual(monitor.number(f"sysbus ReadDoubleWord {BASE + 4:#x}"), 0)

    def test_noninteger_settings_rejected_before_launch(self):
        for setting in (True, 1.5, "2"):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                capture(self.renode, self.vvp, self.design, self.directory, divider=setting)

    def test_broken_simulator_image_fails_and_disposes(self):
        bad_design = self.directory / "broken.vvp"
        bad_design.write_text("This is not a compiled RTL image.\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Renode command failed"):
            capture(self.renode, self.vvp, bad_design, self.directory)
        log = (self.directory / "renode.log").read_text(encoding="utf-8")
        self.assertIn("Disposed.", log)
        self.assertNotIn("Unhandled exception", log)


if __name__ == "__main__":
    unittest.main()
