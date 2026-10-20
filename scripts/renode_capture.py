"""Drive a real Renode board connected to the Icarus RTL subprocess.

The capture() API returns the project's version-1 capture dictionary. Its output
directory contains the generated setup script and full monitor transcript.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import re
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x40000000


def _monitor_string(value: Path | str) -> str:
    value = str(value).replace("\\", "/")
    if any(c in value for c in '\r\n";'):
        raise ValueError("unsupported character in Renode argument")
    return '"' + value + '"'


def _monitor_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if any(c in value for c in '\r\n";'):
        raise ValueError("unsupported character in Renode path")
    return "@" + value.replace(" ", "\\ ")


class RenodeMonitor:
    """Own a private-stdin Renode process, with bounded responses and cleanup."""

    def __init__(self, executable: Path, script: Path, log_path: Path):
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._log = log_path.open("w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                [str(executable), "--disable-gui", "--console", "-p", str(script)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        except BaseException:
            self._log.close()
            raise
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout
        try:
            for line in self.process.stdout:
                self._log.write(line)
                self._log.flush()
                self._lines.put(line.strip())
        finally:
            self._lines.put(None)

    def command(self, command: str, *, timeout: float = 30) -> list[str]:
        token = "LA_ACK_" + secrets.token_hex(12)
        assert self.process.stdin
        self.process.stdin.write(command + '\necho "' + token + '"\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        lines = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Renode did not finish {command[:100]!r}; see renode.log")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("Renode monitor timed out; see renode.log") from exc
            if line is None:
                raise RuntimeError("Renode monitor exited; see renode.log")
            if line == token:
                break
            lines.append(line)
        errors = [line for line in lines if re.search(
            r"There was an error|Error E\d|Could not execute|Fatal error|error CS\d", line)]
        if errors:
            raise RuntimeError("Renode command failed: " + "\n".join(errors))
        return lines

    def number(self, command: str) -> int:
        lines = self.command(command)
        for line in reversed(lines):
            if re.fullmatch(r"(?:0x[0-9a-fA-F]+|\d+)", line):
                return int(line, 16 if line.startswith("0x") else 10)
        raise RuntimeError(f"Renode did not return a number for {command}: {lines[-4:]}")

    def close(self) -> None:
        if self.process.poll() is None:
            # Explicitly dispose before quit, so normal and failed captures close
            # the owned simulator even if Renode does not dispose the machine.
            with contextlib.suppress(Exception):
                self.command("sysbus.analyzer StopSource\nsysbus.analyzer Dispose", timeout=8)
            with contextlib.suppress(OSError):
                assert self.process.stdin
                self.process.stdin.write("quit\n")
                self.process.stdin.flush()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # A stuck Renode may still own vvp. Kill only our process tree.
                if os.name == "nt":
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                                       capture_output=True, timeout=5,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGKILL)
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=5)
        self._reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()
        self._log.close()

    def __enter__(self) -> "RenodeMonitor":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def capture(renode_exe: Path | str, vvp_exe: Path | str, bridge_vvp: Path | str,
            output_dir: Path | str, *, length: int = 512, pretrigger: int = 32,
            divider: int = 1, trigger_mode: int = 3, mask: int = 1,
            value: int = 0, max_ticks: int | None = None) -> dict:
    """Capture RTL samples using a 1 MHz Renode virtual clock and UART stimulus.

    The default stimulus is 100 kbaud UART on CH0 and a binary counter on CH1-7.
    max_ticks bounds virtual runtime; a trigger that never arrives raises instead
    of returning a fabricated or incomplete capture. Large dividers are slow in
    this intentionally simple one-transaction-per-sample co-simulation bridge.
    """
    renode_exe, vvp_exe, bridge_vvp = (
        Path(p).resolve() for p in (renode_exe, vvp_exe, bridge_vvp))
    for path in (renode_exe, vvp_exe, bridge_vvp):
        if not path.is_file():
            raise FileNotFoundError(path)
    if any(type(item) is not int for item in (length, pretrigger, divider, trigger_mode, mask, value)):
        raise ValueError("capture settings must be integers (booleans are not accepted)")
    if max_ticks is not None and type(max_ticks) is not int:
        raise ValueError("max_ticks must be an integer")
    if not 1 <= length <= 1024:
        raise ValueError("length must be between 1 and 1024")
    if not 0 <= pretrigger < length:
        raise ValueError("pretrigger must be smaller than length")
    if not 1 <= divider <= 0xFFFFFFFF:
        raise ValueError("divider must be a nonzero 32-bit integer")
    if trigger_mode not in (0, 1, 2, 3):
        raise ValueError("trigger_mode must be immediate=0, masked=1, rising=2, falling=3")
    if not 0 <= mask <= 255 or not 0 <= value <= 255:
        raise ValueError("mask and value must fit eight channels")
    ticks = max_ticks if max_ticks is not None else (length + pretrigger) * divider + 1024
    if not 1 <= ticks <= 1_000_000:
        raise ValueError("capture budget must be 1..1000000 ticks; pass max_ticks explicitly")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    startup = [
        f"include {_monitor_path(ROOT / 'renode/LogicAnalyzer.cs')}",
        'mach create "fpga-logic-analyzer"',
        f"machine LoadPlatformDescription {_monitor_path(ROOT / 'renode/logic_analyzer.repl')}",
        "sysbus.analyzer Configure " + _monitor_string(vvp_exe) + " " + _monitor_string(bridge_vvp),
    ]
    script = output_dir / "start.resc"
    script.write_text("\n".join(startup) + "\n", encoding="utf-8")
    with RenodeMonitor(renode_exe, script, output_dir / "renode.log") as monitor:
        if monitor.number(f"sysbus ReadDoubleWord {BASE + 48:#x}") != 1024:
            raise RuntimeError("unexpected RTL buffer capacity")
        channels = monitor.number(f"sysbus ReadDoubleWord {BASE + 52:#x}")
        config = [(8, divider), (12, length), (16, pretrigger),
                  (20, mask), (24, value), (28, trigger_mode), (0, 1)]
        monitor.command("\n".join(
            f"sysbus WriteDoubleWord {BASE + offset:#x} {data}" for offset, data in config))
        monitor.command("sysbus.analyzer StartSource")
        # RunFor blocks until virtual time has advanced; no wall-clock stimulus.
        # Small chunks bound command timeouts and stop promptly after completion.
        elapsed = 0
        status = 0
        while elapsed < ticks:
            chunk = min(1024, ticks - elapsed)
            monitor.command(f'emulation RunFor "{chunk / 1_000_000:.6f}"', timeout=60)
            elapsed += chunk
            status = monitor.number(f"sysbus ReadDoubleWord {BASE + 4:#x}")
            if status & 8:
                raise RuntimeError("RTL rejected the capture configuration")
            if status & 4:
                break
        monitor.command("sysbus.analyzer StopSource")
        if not status & 4:
            raise TimeoutError(f"capture did not complete after {elapsed} virtual ticks (status={status:#x})")
        count = monitor.number(f"sysbus ReadDoubleWord {BASE + 32:#x}")
        trigger = monitor.number(f"sysbus ReadDoubleWord {BASE + 36:#x}")
        lines = monitor.command("sysbus.analyzer DumpSamples", timeout=60)
        sample_line = next((line for line in lines if line.startswith("SAMPLES:")), None)
        if sample_line is None:
            raise RuntimeError("Renode did not return captured samples")
        samples = [int(value) for value in sample_line.removeprefix("SAMPLES:").split(",") if value]
        generated = monitor.number("sysbus.analyzer GeneratedSamples")
        if count != length or len(samples) != count:
            raise RuntimeError(f"capture size mismatch: requested={length}, RTL={count}, received={len(samples)}")
        if channels != 8 or any(sample < 0 or sample >= (1 << channels) for sample in samples):
            raise RuntimeError("invalid channel count or sample returned by RTL")
        if trigger >= count:
            raise RuntimeError("trigger index outside capture")
        return {
            "version": 1,
            "sample_rate_hz": 1_000_000 / divider,
            "channels": channels,
            "trigger_index": trigger,
            "samples": samples,
            "source": "renode-rtl",
            "metadata": {
                "virtual_board": "Renode monitor-controlled FPGA board",
                "rtl_simulator": "Icarus Verilog",
                "sample_clock_hz": 1_000_000,
                "divider": divider,
                "trigger_mode": trigger_mode,
                "trigger_mask": mask,
                "trigger_value": value,
                "pretrigger_requested": pretrigger,
                "generated_ticks": generated,
                "uart_channel": 0,
                "uart_baud": 100_000,
                "uart_format": "8N1",
                "uart_payload": [0x55, 0x41, 0xA3, 0x0A],
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renode", type=Path, required=True)
    parser.add_argument("--vvp", type=Path, required=True)
    parser.add_argument("--design", type=Path, default=ROOT / "build/bridge.vvp")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/renode-capture")
    args = parser.parse_args()
    result = capture(args.renode, args.vvp, args.design, args.output_dir)
    target = args.output_dir / "capture.json"
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(result['samples'])} samples to {target}")


if __name__ == "__main__":
    main()
