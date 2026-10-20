"""Portable capture schema and lossless interchange formats."""
from __future__ import annotations

import csv
import json
import math
import os
import tempfile
from pathlib import Path

MAX_SAMPLES = 1_000_000


def validate_capture(capture: dict) -> dict:
    """Validate at the persistence boundary; return the original capture."""
    if not isinstance(capture, dict) or type(capture.get("version")) is not int or capture.get("version") != 1:
        raise ValueError("capture must use schema version 1")
    channels = capture.get("channels")
    if type(channels) is not int or not 1 <= channels <= 32:
        raise ValueError("channels must be an integer from 1 through 32")
    rate = capture.get("sample_rate_hz")
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("sample_rate_hz must be finite and positive")
    samples = capture.get("samples")
    if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
        raise ValueError(f"samples must be a list of at most {MAX_SAMPLES} integers")
    if any(type(sample) is not int or not 0 <= sample < 1 << channels for sample in samples):
        raise ValueError("sample outside channel bit width")
    if "trigger_index" not in capture:
        raise ValueError("trigger_index is required (use null for captures without a trigger)")
    trigger = capture["trigger_index"]
    if trigger is not None and (type(trigger) is not int or not 0 <= trigger < len(samples)):
        raise ValueError("trigger_index must be null or identify a captured sample")
    if not isinstance(capture.get("source"), str) or not capture["source"]:
        raise ValueError("source must be a nonempty string")
    if not isinstance(capture.get("metadata", {}), dict):
        raise ValueError("metadata must be an object")
    try:
        json.dumps(capture, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("capture must contain finite JSON data") from error
    return capture


def load_capture(path: str | Path) -> dict:
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("capture file exceeds 64 MiB")
    return validate_capture(json.loads(path.read_text(encoding="utf-8")))


def save_capture(capture: dict, path: str | Path) -> Path:
    """Atomic replacement prevents an interrupted write leaving half a capture."""
    validate_capture(capture)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(capture, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def export_csv(capture: dict, path: str | Path) -> Path:
    validate_capture(capture)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    trigger = capture["trigger_index"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sample", "time_s", "relative_to_trigger_s", "value", *[
            f"D{i}" for i in range(capture["channels"])]] )
        for index, value in enumerate(capture["samples"]):
            writer.writerow([index, index / capture["sample_rate_hz"],
                             "" if trigger is None else (index - trigger) / capture["sample_rate_hz"],
                             value, *[(value >> i) & 1 for i in range(capture["channels"])]])
    return path


def export_vcd(capture: dict, path: str | Path) -> Path:
    """Export transitions with picosecond timestamps (rounded to nearest ps)."""
    validate_capture(capture)
    if capture["sample_rate_hz"] > 1e12:
        raise ValueError("VCD export supports at most 1 THz")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    channels = capture["channels"]
    ids = [chr(33 + i) for i in range(channels)]
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write("$version Python FPGA logic analyzer $end\n$timescale 1ps $end\n")
        stream.write(f"$comment trigger_index={capture['trigger_index']} $end\n$scope module capture $end\n")
        for i, identifier in enumerate(ids):
            stream.write(f"$var wire 1 {identifier} D{i} $end\n")
        stream.write("$upscope $end\n$enddefinitions $end\n")
        previous = None
        for index, value in enumerate(capture["samples"]):
            if value != previous:
                stream.write(f"#{round(index * 1e12 / capture['sample_rate_hz'])}\n")
                for i, identifier in enumerate(ids):
                    if previous is None or (previous ^ value) & (1 << i):
                        stream.write(f"{(value >> i) & 1}{identifier}\n")
                previous = value
        stream.write(f"#{round(len(capture['samples']) * 1e12 / capture['sample_rate_hz'])}\n")
    return path
