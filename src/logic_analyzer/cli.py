"""Command-line entry point for logging, export, decoding, and the desktop viewer."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .capture import export_csv, export_vcd, load_capture, save_capture
from .store import CaptureStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("view", "decode", "report", "export", "log"):
        sub = commands.add_parser(name)
        sub.add_argument("capture", type=Path, help="capture JSON file")
        if name in ("view", "decode", "report"):
            sub.add_argument("--baud", type=float, default=100000)
            sub.add_argument("--channel", type=int, default=0)
        if name in ("view", "report"):
            sub.add_argument("--no-uart", action="store_true", help="show digital signals without UART decoding")
        if name in ("report", "export"):
            sub.add_argument("--output", required=True, type=Path)
        if name == "export":
            sub.add_argument("--format", choices=("csv", "vcd"), required=True)
        if name == "log":
            sub.add_argument("--db", type=Path, default=Path("captures/captures.sqlite3"))
            sub.add_argument("--label", default="")
    listing = commands.add_parser("list")
    listing.add_argument("--db", type=Path, default=Path("captures/captures.sqlite3"))
    restore = commands.add_parser("restore")
    restore.add_argument("id", type=int)
    restore.add_argument("--db", type=Path, default=Path("captures/captures.sqlite3"))
    restore.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command in ("list", "restore"):
            with CaptureStore(args.db) as store:
                if args.command == "list":
                    print(json.dumps(store.list(), indent=2))
                else:
                    print(save_capture(store.load(args.id), args.output))
            return 0
        capture = load_capture(args.capture)
        if args.command in ("view", "decode", "report") and not 0 <= args.channel < capture["channels"]:
            raise ValueError("UART channel is outside the captured channels")
        if args.command == "log":
            with CaptureStore(args.db) as store:
                print(f"Recorded capture {store.record(capture, args.label)} in {args.db}")
        elif args.command == "export":
            print((export_csv if args.format == "csv" else export_vcd)(capture, args.output))
        elif args.command == "decode":
            from .uart import decode_uart
            frames = decode_uart(capture["samples"], capture["sample_rate_hz"],
                                 args.baud, channel=args.channel)
            print(json.dumps([frame.as_dict() for frame in frames], indent=2))
        elif args.command == "report":
            from .viewer import export_html
            print(export_html(capture, args.output, baud=None if args.no_uart else args.baud, channel=args.channel))
        else:
            from .viewer import launch
            launch(capture, baud=None if args.no_uart else args.baud, channel=args.channel)
        return 0
    except (ValueError, OSError, KeyError, RuntimeError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
