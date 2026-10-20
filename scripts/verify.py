"""Run Python and RTL tests; --renode additionally verifies the complete demo."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from toolchain import ROOT, build_design, check_rtl, find_tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renode", action="store_true")
    args = parser.parse_args()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT / "scripts")
    if args.renode:
        environment["RUN_RENODE_TESTS"] = "1"
        environment["RENODE_EXE"] = str(find_tool("renode"))
        environment["VVP_EXE"] = str(find_tool("vvp"))
        environment["BRIDGE_VVP"] = str(build_design(find_tool("iverilog")))
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                   cwd=ROOT, env=environment, check=True, timeout=120)
    subprocess.run([sys.executable, str(ROOT / "scripts/check_random_capture.py")],
                   cwd=ROOT, env=environment, check=True, timeout=120)
    if args.renode:
        from run_demo import run
        run(ROOT / "captures/verification")
    else:
        print(check_rtl(find_tool("iverilog"), find_tool("vvp")))
        print("Renode integration not requested; run verify.py --renode for full validation.")


if __name__ == "__main__":
    main()
