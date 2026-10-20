"""Discover explicitly configured tools, PATH tools, or project-local tools."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_tool(name: str, explicit: str | Path | None = None) -> Path:
    configured = explicit or os.environ.get(name.upper())
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        resolved = shutil.which(str(configured))
        if resolved:
            return Path(resolved).resolve()
        raise FileNotFoundError(f"configured {name} executable does not exist: {configured}")
    executable = name + (".exe" if os.name == "nt" else "")
    candidates = [ROOT / "build/tools/mingw64/bin" / executable]
    if name == "renode":
        candidates += list((ROOT / "build/tools").glob("**/renode.exe"))
        candidates += list((ROOT / "build/tools").glob("**/renode"))
        # Convenience for this multi-project workspace; never copy or modify it.
        candidates += [ROOT.parent / "stm32-networked-sensor-system/build/tools/renode-1.17.0/renode_1.17.0-portable/renode.exe"]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    path = shutil.which(name)
    if path:
        return Path(path).resolve()
    raise FileNotFoundError(f"{name} not found: install it or set {name.upper()} to its executable")


def tool_environment(iverilog: Path) -> dict:
    environment = os.environ.copy()
    environment["PATH"] = str(iverilog.parent) + os.pathsep + environment.get("PATH", "")
    return environment


def build_design(iverilog: Path, *, testbench: bool = False) -> Path:
    top = "tb_logic_analyzer" if testbench else "bridge_tb"
    output = ROOT / "build" / (f"{top}.vvp" if testbench else "bridge.vvp")
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / "rtl/capture_buffer.sv", ROOT / "rtl/capture_controller.sv",
               ROOT / "rtl/logic_analyzer.sv", ROOT / "sim" / f"{top}.sv"]
    subprocess.run([str(iverilog), "-g2012", "-Wall", "-s", top, "-o", str(output),
                    *map(str, sources)], check=True, env=tool_environment(iverilog), timeout=60)
    return output


def check_rtl(iverilog: Path, vvp: Path) -> str:
    design = build_design(iverilog, testbench=True)
    result = subprocess.run([str(vvp), str(design)], check=True, text=True,
                            capture_output=True, timeout=60, cwd=ROOT / "build",
                            env=tool_environment(iverilog))
    if "PASS" not in result.stdout:
        raise RuntimeError("RTL testbench did not report PASS")
    return result.stdout
