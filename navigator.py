"""Pathfinding engine and instruction generator for the Shenzhen Metro.

The network is modelled as a *line-aware* graph: a node is not a station but
the pair ``(station_id, line_name)`` -- "being at Futian, on Line 3".

That distinction is what makes transfer costs expressible at all. With plain
station nodes the graph has no idea which train you are sitting on, so it
cannot tell a straight ride from a route that hops between four lines.

Edges:
    ride      (A, L) -> (B, L)   where A and B are adjacent on L
    transfer  (S, L1) -> (S, L2) where S is served by both lines

Boarding is free: the search simply starts from every ``(start, line)`` node
at once, so the first train you get on is never charged a transfer penalty.
"""

from __future__ import annotations

import heapq
import json
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

DEFAULT_DATA_FILE = Path(__file__).with_name("metro_data.json")

#: A search node: which station you are at, and which line you are on.
Node = Tuple[str, str]

LANGUAGES = ("en", "zh", "both")


class MetroError(Exception):
    """Base class for every error this module raises."""


class MetroDataError(MetroError):
    """The map data is malformed."""


class StationNotFound(MetroError):
    """A station name or id could not be resolved."""


class NoRouteFound(MetroError):
    """The two stations are not connected in this network."""


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Station:
    """One physical station, possibly served by several lines."""

    id: str
    en: str
    zh: str
    lines: Tuple[str, ...]

    @property
    def is_transfer(self) -> bool:
        """True when more than one line stops here."""
        return len(self.lines) > 1

    def name(self, lang: str = "both") -> str:
        if lang == "en":
            return self.en
        if lang == "zh":
            return self.zh
        return f"{self.en} {self.zh}"


@dataclass(frozen=True)
class Line:
    """One metro line: an ordered sequence of station ids."""

    name: str
    zh: str
    nickname: str
    color: str
    stations: Tuple[str, ...]

    @property
    def terminals(self) -> Tuple[str, str]:
        return self.stations[0], self.stations[-1]

    def label(self, lang: str = "both") -> str:
        if lang == "en":
            return self.name
        if lang == "zh":
            return self.zh
        return f"{self.name} ({self.zh})"


class MetroNetwork:
    """The map: lines, stations, adjacency, and the timing model.

    Transfer stations are *derived*, never declared. A station is a transfer
    point exactly when it appears in more than one line's station list, so
    extending the map means adding stations to ``metro_data.json`` and
    nothing else.
    """

    def __init__(self, data: dict) -> None:
        self._validate(data)
        timing = data.get("timing", {})
        self.minutes_per_stop: int = int(timing.get("minutes_per_stop", 2))
        self.minutes_per_transfer: int = int(timing.get("minutes_per_transfer", 5))
        self.name: str = data.get("network", "Metro")

        self.lines: Dict[str, Line] = {
            name: Line(
                name=name,
                zh=spec.get("zh", name),
                nickname=spec.get("nickname", ""),
                color=spec.get("color", "#888888"),
                stations=tuple(spec["stations"]),
            )
            for name, spec in data["lines"].items()
        }

        # station id -> the lines serving it, in map order
        serving: Dict[str, List[str]] = {}
        for line in self.lines.values():
            for sid in line.stations:
                serving.setdefault(sid, []).append(line.name)

        self.stations: Dict[str, Station] = {
            sid: Station(
                id=sid,
                en=spec["en"],
                zh=spec["zh"],
                lines=tuple(serving.get(sid, ())),
            )
            for sid, spec in data["stations"].items()
            if sid in serving
        }

        # (line, station) -> position along the line, for O(1) neighbour lookup
        self._position: Dict[Tuple[str, str], int] = {
            (line.name, sid): index
            for line in self.lines.values()
            for index, sid in enumerate(line.stations)
        }

    # -- construction ------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str = DEFAULT_DATA_FILE) -> "MetroNetwork":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise MetroDataError(f"map data not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise MetroDataError(f"map data is not valid JSON: {exc}") from exc
        return cls(data)

    @staticmethod
    def _validate(data: dict) -> None:
        for key in ("lines", "stations"):
            if key not in data:
                raise MetroDataError(f"map data is missing the {key!r} section")
        known = set(data["stations"])
        for name, spec in data["lines"].items():
            stations = spec.get("stations") or []
            if len(stations) < 2:
                raise MetroDataError(f"{name}: needs at least two stations")
            unknown = [s for s in stations if s not in known]
            if unknown:
                raise MetroDataError(f"{name}: unknown station ids {unknown}")

    # -- lookups -----------------------------------------------------------

    def line_names(self) -> List[str]:
        """Lines in map order (Line 1, Line 2, ... Line 11 -- not alphabetical)."""
        return list(self.lines)

    def stations_on(self, line_name: str) -> List[Station]:
        if line_name not in self.lines:
            raise StationNotFound(f"unknown line: {line_name}")
        return [self.stations[sid] for sid in self.lines[line_name].stations]

    def station(self, station_id: str) -> Station:
        try:
            return self.stations[station_id]
        except KeyError as exc:
            raise StationNotFound(f"unknown station id: {station_id}") from exc

    def transfer_stations(self) -> List[Station]:
        return [s for s in self.stations.values() if s.is_transfer]

    def resolve(self, query: str) -> str:
        """Turn an id, an English name or a Chinese name into a station id.

        Tourists read the English sign, locals read the Chinese one, and the
        code works with ids -- all three have to land on the same station.
        """
        needle = query.strip()
        if needle in self.stations:
            return needle
        folded = needle.casefold()
        for station in self.stations.values():
            if folded in (station.en.casefold(), station.zh.casefold()):
                return station.id
        matches = [
            s.id for s in self.stations.values() if folded in s.en.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            names = ", ".join(self.stations[m].en for m in sorted(matches))
            raise StationNotFound(f"{query!r} is ambiguous: {names}")
        raise StationNotFound(f"no station matches {query!r}")

    # -- graph -------------------------------------------------------------

    def position(self, line_name: str, station_id: str) -> int:
        return self._position[(line_name, station_id)]

    def neighbors(self, node: Node) -> Iterator[Tuple[Node, int, str]]:
        """Yield ``(neighbour, minutes, kind)`` for one node.

        ``kind`` is ``"ride"`` for one stop along the current line and
        ``"transfer"`` for stepping onto another line at the same station.
        """
        station_id, line_name = node
        line = self.lines[line_name]
        index = self._position[(line_name, station_id)]

        if index > 0:
            yield (line.stations[index - 1], line_name), self.minutes_per_stop, "ride"
        if index + 1 < len(line.stations):
            yield (line.stations[index + 1], line_name), self.minutes_per_stop, "ride"

        for other in self.stations[station_id].lines:
            if other != line_name:
                yield (station_id, other), self.minutes_per_transfer, "transfer"

    def direction(self, line_name: str, from_id: str, to_id: str) -> str:
        """Which terminus the train is heading towards -- what the sign says."""
        line = self.lines[line_name]
        start = self._position[(line_name, from_id)]
        end = self._position[(line_name, to_id)]
        return line.stations[-1] if end > start else line.stations[0]


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@dataclass
class Route:
    """A computed journey, as structured data -- never as a string.

    ``steps`` is the machine-readable itinerary; the CLI, a GUI or a future
    web API all render the same object their own way.
    """

    start: str
    end: str
    steps: List[dict] = field(default_factory=list)
    path: List[Node] = field(default_factory=list)
    total_stops: int = 0
    transfers: int = 0
    total_minutes: int = 0

    @property
    def is_trivial(self) -> bool:
        """Start and end are the same station: nothing to ride."""
        return self.start == self.end

    @property
    def lines_used(self) -> List[str]:
        seen: List[str] = []
        for _, line_name in self.path:
            if not seen or seen[-1] != line_name:
                seen.append(line_name)
        return seen

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "steps": self.steps,
            "total_stops": self.total_stops,
            "transfers": self.transfers,
            "total_minutes": self.total_minutes,
        }


class Router:
    """Shortest-path search over the line-aware graph."""

    def __init__(self, network: MetroNetwork) -> None:
        self.network = network

    def find_route(
        self, start: str, end: str, optimize: str = "time"
    ) -> Route:
        """Find the best route between two stations.

        ``optimize="time"`` runs Dijkstra over the real cost model (2 min per
        stop, 5 min per transfer). ``optimize="stops"`` runs a 0-1 BFS that
        counts stops only and ignores the pain of transferring.
        """
        start_id = self.network.resolve(start)
        end_id = self.network.resolve(end)

        if start_id == end_id:
            return Route(start=start_id, end=end_id)

        if optimize == "time":
            path = self._dijkstra(start_id, end_id)
        elif optimize == "stops":
            path = self._bfs(start_id, end_id)
        else:
            raise ValueError(f"unknown optimize mode: {optimize!r}")

        return self._build_route(path)

    # -- algorithms --------------------------------------------------------

    def _start_nodes(self, station_id: str) -> List[Node]:
        return [(station_id, line) for line in self.network.station(station_id).lines]

    def _dijkstra(self, start_id: str, end_id: str) -> List[Node]:
        """Classic Dijkstra with a lexicographic cost ``(minutes, transfers)``.

        BFS would be wrong here: ride edges cost 2 and transfer edges cost 5,
        and BFS only finds shortest paths when every edge weighs the same.
        The second cost component is a tie-break -- between two itineraries
        that take equally long, a tourist wants the one with fewer changes.
        """
        tiebreak = count()
        best: Dict[Node, Tuple[int, int]] = {}
        previous: Dict[Node, Optional[Node]] = {}
        queue: List[Tuple[Tuple[int, int], int, Node]] = []

        for node in self._start_nodes(start_id):
            best[node] = (0, 0)
            previous[node] = None
            heapq.heappush(queue, ((0, 0), next(tiebreak), node))

        while queue:
            cost, _, node = heapq.heappop(queue)
            if cost > best.get(node, cost):
                continue  # stale heap entry, already improved on
            if node[0] == end_id:
                return self._unwind(previous, node)

            minutes, changes = cost
            for neighbor, weight, kind in self.network.neighbors(node):
                candidate = (
                    minutes + weight,
                    changes + (1 if kind == "transfer" else 0),
                )
                if candidate < best.get(neighbor, (1 << 30, 1 << 30)):
                    best[neighbor] = candidate
                    previous[neighbor] = node
                    heapq.heappush(queue, (candidate, next(tiebreak), neighbor))

        raise NoRouteFound(
            f"no route from {start_id} to {end_id} in this network"
        )

    def _bfs(self, start_id: str, end_id: str) -> List[Node]:
        """0-1 BFS minimising stops only: ride costs 1, transfer costs 0.

        Zero-weight edges go to the front of the deque and unit-weight edges
        to the back, which keeps the queue sorted without a heap.
        """
        best: Dict[Node, int] = {}
        previous: Dict[Node, Optional[Node]] = {}
        queue: deque[Tuple[int, Node]] = deque()

        for node in self._start_nodes(start_id):
            best[node] = 0
            previous[node] = None
            queue.append((0, node))

        while queue:
            stops, node = queue.popleft()
            if stops > best.get(node, stops):
                continue
            if node[0] == end_id:
                return self._unwind(previous, node)

            for neighbor, _, kind in self.network.neighbors(node):
                weight = 1 if kind == "ride" else 0
                candidate = stops + weight
                if candidate < best.get(neighbor, 1 << 30):
                    best[neighbor] = candidate
                    previous[neighbor] = node
                    if weight:
                        queue.append((candidate, neighbor))
                    else:
                        queue.appendleft((candidate, neighbor))

        raise NoRouteFound(
            f"no route from {start_id} to {end_id} in this network"
        )

    @staticmethod
    def _unwind(
        previous: Dict[Node, Optional[Node]], node: Node
    ) -> List[Node]:
        path: List[Node] = []
        cursor: Optional[Node] = node
        while cursor is not None:
            path.append(cursor)
            cursor = previous[cursor]
        path.reverse()
        return path

    # -- itinerary ---------------------------------------------------------

    def _segments(self, path: Sequence[Node]) -> List[Tuple[str, List[str]]]:
        """Split the raw node path into one leg per line."""
        segments: List[Tuple[str, List[str]]] = []
        for station_id, line_name in path:
            if segments and segments[-1][0] == line_name:
                segments[-1][1].append(station_id)
            else:
                segments.append((line_name, [station_id]))
        return segments

    def _build_route(self, path: Sequence[Node]) -> Route:
        segments = self._segments(path)
        network = self.network
        steps: List[dict] = []
        total_stops = 0

        first_line, first_leg = segments[0]
        steps.append(
            {
                "action": "board",
                "line": first_line,
                "station": first_leg[0],
                "direction": network.direction(first_line, first_leg[0], first_leg[-1]),
            }
        )

        for index, (line_name, leg) in enumerate(segments):
            stops = len(leg) - 1
            if stops:
                total_stops += stops
                steps.append(
                    {
                        "action": "travel",
                        "line": line_name,
                        "stops": stops,
                        "from": leg[0],
                        "to": leg[-1],
                        "through": leg[1:-1],
                    }
                )
            if index + 1 < len(segments):
                next_line, next_leg = segments[index + 1]
                steps.append(
                    {
                        "action": "transfer",
                        "from_line": line_name,
                        "to_line": next_line,
                        "at_station": leg[-1],
                        "direction": network.direction(
                            next_line, next_leg[0], next_leg[-1]
                        ),
                    }
                )

        last_line, last_leg = segments[-1]
        steps.append({"action": "exit", "station": last_leg[-1], "line": last_line})

        transfers = len(segments) - 1
        return Route(
            start=path[0][0],
            end=path[-1][0],
            steps=steps,
            path=list(path),
            total_stops=total_stops,
            transfers=transfers,
            total_minutes=(
                total_stops * network.minutes_per_stop
                + transfers * network.minutes_per_transfer
            ),
        )


# --------------------------------------------------------------------------
# The voice: structured steps -> sentences a tourist can follow
# --------------------------------------------------------------------------


class InstructionGenerator:
    """Renders a :class:`Route` as human-readable directions.

    Language modes: ``en`` (English only), ``zh`` (Chinese only) and ``both``,
    which keeps the sentence in English but prints every station and line in
    both scripts -- so the rider can match the characters on the platform sign.
    """

    def __init__(self, network: MetroNetwork, lang: str = "both") -> None:
        if lang not in LANGUAGES:
            raise ValueError(f"lang must be one of {LANGUAGES}, got {lang!r}")
        self.network = network
        self.lang = lang

    # -- naming helpers ----------------------------------------------------

    @property
    def _chinese(self) -> bool:
        return self.lang == "zh"

    def _station(self, station_id: str) -> str:
        return self.network.station(station_id).name(self.lang)

    def _line(self, line_name: str) -> str:
        return self.network.lines[line_name].label(self.lang)

    # -- rendering ---------------------------------------------------------

    def steps(self, route: Route) -> List[str]:
        """One sentence per step, ready to print or feed to a GUI list."""
        if route.is_trivial:
            here = self._station(route.start)
            if self._chinese:
                return [f"您已经在 {here} 了！"]
            return [f"You are already at {here}!"]

        lines: List[str] = []
        for step in route.steps:
            renderer = getattr(self, f"_say_{step['action']}")
            lines.append(renderer(step))
        return lines

    def summary(self, route: Route) -> str:
        if route.is_trivial:
            return "总时间：0 分钟。" if self._chinese else "Total time: 0 minutes."
        if self._chinese:
            return (
                f"共 {route.total_stops} 站，{route.transfers} 次换乘，"
                f"约 {route.total_minutes} 分钟。"
            )
        change = "change" if route.transfers == 1 else "changes"
        stop = "stop" if route.total_stops == 1 else "stops"
        return (
            f"Total: {route.total_stops} {stop}, {route.transfers} {change}, "
            f"about {route.total_minutes} minutes."
        )

    def render(self, route: Route) -> str:
        """The whole itinerary as one numbered block of text."""
        body = self.steps(route)
        if route.is_trivial:
            return body[0]
        numbered = [f"{i}. {line}" for i, line in enumerate(body, start=1)]
        return "\n".join(numbered + ["", self.summary(route)])

    # -- one method per action type ---------------------------------------

    def _say_board(self, step: dict) -> str:
        line = self._line(step["line"])
        station = self._station(step["station"])
        toward = self._station(step["direction"])
        if self._chinese:
            return f"在 {station} 乘坐 {line}，往 {toward} 方向。"
        return f"Board {line} at {station}, heading towards {toward}."

    def _say_travel(self, step: dict) -> str:
        stops = step["stops"]
        destination = self._station(step["to"])
        if self._chinese:
            return f"乘坐 {stops} 站到 {destination}。"
        word = "stop" if stops == 1 else "stops"
        return f"Ride {stops} {word} to {destination}."

    def _say_transfer(self, step: dict) -> str:
        at = self._station(step["at_station"])
        to_line = self._line(step["to_line"])
        toward = self._station(step["direction"])
        if self._chinese:
            return f"在 {at} 换乘 {to_line}，往 {toward} 方向。"
        return f"At {at}, transfer to {to_line} heading towards {toward}."

    def _say_exit(self, step: dict) -> str:
        station = self._station(step["station"])
        if self._chinese:
            return f"在 {station} 下车，到达目的地。"
        return f"Get off at {station}. You have arrived."
