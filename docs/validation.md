# Local validation

Verified on Windows on October 19, 2026 (America/Los_Angeles), using Python
3.12.14, Icarus Verilog 13.0, and Renode 1.17.0.

The final `run.ps1 -Verify` exited successfully. The saved transcript is
[`verification.log`](verification.log).

| Layer | Result |
| --- | --- |
| Python unit and integration suite | 38 tests passed, including seven actual Renode integration tests |
| Self-checking SystemVerilog | 15 capture scenarios plus configuration/reset checks passed |
| Seeded independent RTL scoreboard | 84 scenarios, 13,541 register/sample checks, seed 1592594996 |
| Additional randomized run | 280 scenarios, 30,562 checks, seed 20261019 |
| Renode → RTL → Python | All 512 samples exactly matched an independent UART/counter fixture |
| Trigger | Falling D0 edge at chronological sample index 32 |
| UART | Exactly `55 41 A3 0A`, no parity/framing/truncation errors |
| Logger | SQLite persistence and reload preserved the entire capture dictionary |
| Browser report | Waveforms and all four UART rows visually inspected; zoom and two-cursor delta exercised |
| Desktop Python/Tk | Window, waveform drawing, pan, zoom, trigger centering, and decoded table checked programmatically |
| Windows tool bootstrap | Pinned Icarus packages downloaded/verified/extracted successfully |

The browser cursor check selected samples 204 and 304, reporting a 100-sample /
100 µs delta, with sample values `0x98` and `0x60`. The screenshot is saved as
[`waveform-preview.png`](waveform-preview.png).

The tests exercise circular wrap and full capacity; early, last-slot, edge,
level, immediate and software triggers; zero prehistory; divider timing;
abort before/during capture; immutable active configuration; frozen readout;
invalid configuration; rearm and reset. Real Renode tests additionally verify
that MMIO accesses do not advance source time, missing triggers time out,
machine reset resets both RTL and source state, and a broken simulator image
reports failure and disposes the session.

UART coverage includes fractional sample periods, back-to-back frames,
selected packed channels, glitches, alternate framing, parity/stop-bit errors,
and truncated frames without invented bytes. Logger/CLI tests cover invalid
rates/widths/triggers, atomic JSON writes, checksum corruption detection,
database reopen, low-rate digital-only reports, invalid decoder channels, and
invalid database cleanup.

The normal test run uses 84 deterministic randomized scenarios. The additional
seed can be reproduced with:

```sh
python scripts/check_random_capture.py --seed 20261019 --cases 280
```

No target FPGA synthesis, timing closure, physical board capture, or hosted CI
run was performed. The Tk checks were programmatic; the saved pixel preview is
the Python-generated HTML viewer. Forced process-tree termination is a fallback
in the driver; graceful cleanup and startup-failure cleanup were tested.
