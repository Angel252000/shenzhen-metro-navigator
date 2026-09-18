"""Command-line interface for the Shenzhen Metro Smart Navigator.

This module owns every ``print`` and every ``input`` in the project, and it
does no pathfinding of its own -- it only asks :mod:`navigator` questions.
Swapping the CLI for the tkinter front-end in :mod:`gui` therefore touches
nothing in the engine.

Stations are picked from numbered menus (Line -> Station) instead of being
typed, because "Futian" and "Futian Checkpoint" are two different places and
a tourist should not have to spell either of them.
"""

from __future__ import annotations

import unicodedata
from typing import Callable, List, Optional, Sequence, TypeVar

from navigator import (
    InstructionGenerator,
    MetroNetwork,
    NoRouteFound,
    Route,
    Router,
    StationNotFound,
)

T = TypeVar("T")

RULE = "=" * 62
THIN_RULE = "-" * 62

LANGUAGE_LABELS = {
    "both": "Bilingual (English + 中文)",
    "en": "English only",
    "zh": "中文",
}

OPTIMIZE_LABELS = {
    "time": "Fastest trip (Dijkstra: 2 min/stop, 5 min/transfer)",
    "stops": "Fewest stops (BFS, ignores transfer time)",
}


# --------------------------------------------------------------------------
# Terminal helpers
# --------------------------------------------------------------------------


def display_width(text: str) -> int:
    """Width of ``text`` in terminal cells.

    Chinese characters occupy two cells, so ``len()`` misaligns any column
    that mixes scripts -- which every bilingual station name does.
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def in_columns(entries: Sequence[str], columns: int = 3, gutter: int = 3) -> List[str]:
    """Lay entries out down-then-across so a 30-station line fits on a screen."""
    if not entries:
        return []
    columns = max(1, columns)
    rows = -(-len(entries) // columns)  # ceiling division
    width = max(display_width(e) for e in entries) + gutter
    lines = []
    for row in range(rows):
        cells = []
        for column in range(columns):
            index = column * rows + row
            if index < len(entries):
                cells.append(pad(entries[index], width))
        lines.append("".join(cells).rstrip())
    return lines


class Quit(Exception):
    """The user asked to leave the current menu or the program."""


def ask(prompt: str) -> str:
    """Read one line, turning Ctrl-C / Ctrl-D into a clean :class:`Quit`."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Quit from None


# --------------------------------------------------------------------------
# The interface
# --------------------------------------------------------------------------


class CLI:
    """Numbered-menu front end. Holds the session's display preferences."""

    def __init__(self, network: MetroNetwork, lang: str = "both") -> None:
        self.network = network
        self.router = Router(network)
        self.lang = lang
        self.optimize = "time"

    # -- preferences -------------------------------------------------------

    @property
    def voice(self) -> InstructionGenerator:
        """A generator bound to the language currently selected."""
        return InstructionGenerator(self.network, self.lang)

    def station_label(self, station_id: str) -> str:
        station = self.network.station(station_id)
        label = station.name(self.lang)
        return f"{label} ⇄" if station.is_transfer else label

    # -- generic menu ------------------------------------------------------

    def choose(
        self,
        title: str,
        options: Sequence[T],
        label: Callable[[T], str],
        columns: int = 1,
        back: str = "back",
    ) -> Optional[T]:
        """Show a numbered menu and return the chosen item.

        Returns ``None`` when the user picks 0 (go back); raises :class:`Quit`
        on "q" so a nested menu can unwind the whole way out.
        """
        entries = [f"{i:>3}. {label(o)}" for i, o in enumerate(options, start=1)]
        while True:
            print(f"\n{title}")
            print(THIN_RULE)
            for line in in_columns(entries, columns=columns):
                print(line)
            print(f"  0. « {back}    q. quit")

            raw = ask("Choice: ")
            if raw.lower() in {"q", "quit", "exit"}:
                raise Quit
            if raw == "0":
                return None
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]
            print(f"  ! Enter a number between 0 and {len(options)}.")

    # -- station selection -------------------------------------------------

    def select_station(self, role: str) -> Optional[str]:
        """Line menu, then station menu. Returns a station id."""
        while True:
            line = self.choose(
                f"{role}: choose a line",
                self.network.line_names(),
                label=lambda name: (
                    f"{self.network.lines[name].label(self.lang)}"
                    f"  ·  {self.network.lines[name].nickname}"
                    f"  ({len(self.network.lines[name].stations)} stations)"
                ),
            )
            if line is None:
                return None

            stations = self.network.lines[line].stations
            station = self.choose(
                f"{role}: choose a station on {self.network.lines[line].label(self.lang)}",
                stations,
                label=self.station_label,
                columns=3,
                back="pick another line",
            )
            if station is not None:
                return station

    # -- actions -----------------------------------------------------------

    def plan_route(self) -> None:
        start = self.select_station("FROM")
        if start is None:
            return
        print(f"\n  ✓ From: {self.network.station(start).name(self.lang)}")

        end = self.select_station("TO")
        if end is None:
            return
        print(f"  ✓ To:   {self.network.station(end).name(self.lang)}")

        try:
            route = self.router.find_route(start, end, optimize=self.optimize)
        except (NoRouteFound, StationNotFound) as exc:
            print(f"\n  ! {exc}")
            return
        self.show_route(route)

    def show_route(self, route: Route) -> None:
        voice = self.voice
        print()
        print(RULE)
        if route.is_trivial:
            print(voice.render(route))
            print(RULE)
            return
        header = (
            f"{self.network.station(route.start).name(self.lang)}"
            f"  →  {self.network.station(route.end).name(self.lang)}"
        )
        print(header)
        print(THIN_RULE)
        print(voice.render(route))
        print(THIN_RULE)
        print("Lines used: " + " → ".join(route.lines_used))
        print(RULE)

    def browse_line(self) -> None:
        line = self.choose(
            "Browse a line",
            self.network.line_names(),
            label=lambda name: self.network.lines[name].label(self.lang),
        )
        if line is None:
            return
        spec = self.network.lines[line]
        print(f"\n{spec.label(self.lang)} · {spec.nickname} · {spec.color}")
        print(f"{len(spec.stations)} stations, "
              f"{spec.stations[0]} ⇄ {spec.stations[-1]}")
        print(THIN_RULE)
        entries = [
            f"{i:>3}. {self.station_label(sid)}"
            for i, sid in enumerate(spec.stations, start=1)
        ]
        for row in in_columns(entries, columns=3):
            print(row)

    def list_transfers(self) -> None:
        transfers = sorted(self.network.transfer_stations(), key=lambda s: s.en)
        print(f"\n{len(transfers)} transfer stations")
        print(THIN_RULE)
        width = max(display_width(s.name(self.lang)) for s in transfers)
        for station in transfers:
            print(f"  {pad(station.name(self.lang), width)}   "
                  f"{', '.join(station.lines)}")

    def settings(self) -> None:
        while True:
            print(f"\nSettings")
            print(THIN_RULE)
            print(f"  1. Language   [{LANGUAGE_LABELS[self.lang]}]")
            print(f"  2. Route type [{OPTIMIZE_LABELS[self.optimize]}]")
            print("  0. « back")
            raw = ask("Choice: ")
            if raw == "0":
                return
            if raw.lower() in {"q", "quit", "exit"}:
                raise Quit
            if raw == "1":
                choice = self.choose(
                    "Language",
                    list(LANGUAGE_LABELS),
                    label=lambda code: LANGUAGE_LABELS[code],
                )
                if choice:
                    self.lang = choice
            elif raw == "2":
                choice = self.choose(
                    "Route type",
                    list(OPTIMIZE_LABELS),
                    label=lambda code: OPTIMIZE_LABELS[code],
                )
                if choice:
                    self.optimize = choice

    # -- main loop ---------------------------------------------------------

    def banner(self) -> None:
        print(RULE)
        print("  🚇  SHENZHEN METRO SMART NAVIGATOR  ·  深圳地铁导航")
        print(RULE)
        print(f"  {len(self.network.lines)} lines · "
              f"{len(self.network.stations)} stations · "
              f"{len(self.network.transfer_stations())} transfer points")
        print(f"  {self.network.minutes_per_stop} min per stop · "
              f"{self.network.minutes_per_transfer} min per transfer · "
              f"offline, no network needed")

    def run(self) -> None:
        self.banner()
        actions = [
            ("Plan a route", self.plan_route),
            ("Browse a line", self.browse_line),
            ("List transfer stations", self.list_transfers),
            ("Settings", self.settings),
        ]
        try:
            while True:
                print("\nMain menu")
                print(THIN_RULE)
                for index, (title, _) in enumerate(actions, start=1):
                    print(f"  {index}. {title}")
                print("  0. Quit")

                raw = ask("Choice: ")
                if raw in {"0"} or raw.lower() in {"q", "quit", "exit"}:
                    break
                if raw.isdigit() and 1 <= int(raw) <= len(actions):
                    try:
                        actions[int(raw) - 1][1]()
                    except Quit:
                        continue  # "q" inside a submenu returns to the main menu
                else:
                    print("  ! Pick one of the numbers listed.")
        except Quit:
            pass
        print("\n一路顺风 — have a good trip!\n")
