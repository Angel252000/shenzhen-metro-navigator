#!/usr/bin/env python3
"""Entry point for the Shenzhen Metro Smart Navigator.

Three ways to run it:

    python3 main.py                                  interactive CLI
    python3 main.py --gui                            tkinter front end
    python3 main.py --from Luohu --to "Airport East" one-shot lookup

The one-shot mode exists so the engine can be scripted or piped (``--json``
prints the structured itinerary) without going through any menu.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from navigator import (
    DEFAULT_DATA_FILE,
    InstructionGenerator,
    LANGUAGES,
    MetroError,
    MetroNetwork,
    Router,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shenzhen-metro",
        description="Offline route planner for the Shenzhen Metro.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_FILE,
        help="path to the map data (default: metro_data.json next to this file)",
    )
    parser.add_argument(
        "--lang",
        choices=LANGUAGES,
        default="both",
        help="output language: en, zh, or both (default: both)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="launch the tkinter interface instead of the CLI",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help=(
            "launch the natural-language voice interface (needs a mic; "
            "run with .venv-voice/bin/python)"
        ),
    )
    parser.add_argument(
        "--from",
        dest="origin",
        metavar="STATION",
        help="origin station (name in English or Chinese, or its id)",
    )
    parser.add_argument(
        "--to",
        dest="destination",
        metavar="STATION",
        help="destination station",
    )
    parser.add_argument(
        "--optimize",
        choices=("time", "stops"),
        default="time",
        help="time = Dijkstra on real minutes; stops = BFS on stop count",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --from/--to, print the structured route instead of prose",
    )
    return parser


def one_shot(args: argparse.Namespace, network: MetroNetwork) -> int:
    """Non-interactive lookup: resolve both names, print, exit."""
    route = Router(network).find_route(
        args.origin, args.destination, optimize=args.optimize
    )
    if args.json:
        print(json.dumps(route.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(InstructionGenerator(network, args.lang).render(route))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        network = MetroNetwork.load(args.data)
    except MetroError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if bool(args.origin) != bool(args.destination):
        print("error: --from and --to must be used together", file=sys.stderr)
        return 2

    try:
        if args.origin:
            return one_shot(args, network)
        if args.gui:
            from gui import run_gui  # imported lazily: tkinter may be absent

            return run_gui(network, lang=args.lang)
        if args.voice:
            from voice import run_voice  # imported lazily: mic deps may be absent

            return run_voice(network, lang=args.lang)
        from ui import CLI

        CLI(network, lang=args.lang).run()
        return 0
    except MetroError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        if args.voice:
            print(
                f"error: --voice needs its own venv, run with "
                f".venv-voice/bin/python main.py --voice ({exc})",
                file=sys.stderr,
            )
        else:
            print(f"error: the GUI needs tkinter, which is not available ({exc})",
                  file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
