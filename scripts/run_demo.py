"""Build RTL, run the virtual board, log the capture, then decode UART and view."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from toolchain import ROOT, build_design, check_rtl, find_tool

sys.path.insert(0, str(ROOT / "src"))
from logic_analyzer.capture import export_csv, export_vcd, save_capture
from logic_analyzer.store import CaptureStore
from renode_capture import capture


def expected_demo_samples() -> list[int]:
    """Independent wire-level fixture: idle, start, LSB-first data, stop, gap."""
    wire = [1] * 32
    for byte in bytes((0x55, 0x41, 0xA3, 0x0A)):
        for bit in [0, *[(byte >> bit) & 1 for bit in range(8)], 1]:
            wire.extend([bit] * 10)
        wire.extend([1] * 20)
    return [((index % 128) * 2) | bit for index, bit in enumerate(wire)]


def run(output: Path, *, renode=None, iverilog=None, vvp=None, desktop=False) -> dict:
    iverilog, vvp, renode = (find_tool("iverilog", iverilog), find_tool("vvp", vvp),
                             find_tool("renode", renode))
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    print("Checking RTL capture and buffer...", flush=True)
    rtl_result = check_rtl(iverilog, vvp)
    (output / "rtl-tests.log").write_text(rtl_result, encoding="utf-8")
    print(rtl_result.strip(), flush=True)
    design = build_design(iverilog)
    print("Running Renode virtual board with actual RTL...", flush=True)
    data = capture(renode, vvp, design, output / "renode")
    expected = expected_demo_samples()
    if data["samples"] != expected or data["trigger_index"] != 32:
        raise AssertionError("Renode/RTL capture differs from the independent 512-sample fixture")
    json_path = save_capture(data, output / "capture.json")
    export_csv(data, output / "capture.csv")
    export_vcd(data, output / "capture.vcd")
    print("PASS: every captured sample and trigger position matched.", flush=True)

    # UART is layered on top only after raw capture verification succeeds.
    from logic_analyzer.uart import decode_uart
    from logic_analyzer.viewer import export_html, launch
    frames = decode_uart(data["samples"], data["sample_rate_hz"], 100000, channel=0)
    values = [frame.value for frame in frames if not frame.errors]
    if values != [0x55, 0x41, 0xA3, 0x0A] or len(frames) != 4:
        raise AssertionError(f"UART decode mismatch: {values}")
    with CaptureStore(output / "captures.sqlite3") as store:
        capture_id = store.record(data, "Renode UART + counter demo")
        store.record_decode(capture_id, {"baud": 100000, "channel": 0, "format": "8N1"},
                            [frame.as_dict() for frame in frames])
        if store.load(capture_id) != data:
            raise AssertionError("SQLite roundtrip changed capture data")
    report = export_html(data, output / "waveforms.html", baud=100000, channel=0)
    validation = {"passed": True, "verified_utc": datetime.now(timezone.utc).isoformat(),
                  "sample_count": len(data["samples"]), "trigger_index": data["trigger_index"],
                  "uart_bytes_hex": [f"{value:02X}" for value in values], "capture_id": capture_id,
                  "checks": ["self-checking SystemVerilog testbench", "Renode virtual-time RTL co-simulation",
                             "all 512 samples equal independent fixture", "trigger index 32",
                             "UART 55 41 A3 0A", "SQLite lossless roundtrip"],
                  "tools": {"iverilog": str(iverilog), "vvp": str(vvp), "renode": str(renode)}}
    (output / "validation.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: UART 55 41 A3 0A; capture {capture_id} persisted.\nCapture: {json_path}\nWaveforms: {report}")
    if desktop:
        launch(data, baud=100000, channel=0)
    return validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "captures/demo")
    parser.add_argument("--renode", type=Path)
    parser.add_argument("--iverilog", type=Path)
    parser.add_argument("--vvp", type=Path)
    parser.add_argument("--view", action="store_true", help="open the Python/Tk desktop viewer")
    args = parser.parse_args()
    try:
        run(args.output, renode=args.renode, iverilog=args.iverilog, vvp=args.vvp, desktop=args.view)
    except (OSError, RuntimeError, ValueError, AssertionError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
