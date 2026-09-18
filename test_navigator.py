"""Unit tests for the routing engine.

Run with:  python3 -m unittest -v
"""

from __future__ import annotations

import unittest

from navigator import (
    InstructionGenerator,
    MetroDataError,
    MetroNetwork,
    NoRouteFound,
    Router,
    StationNotFound,
)

# A tiny hand-made network: two lines crossing at "cross", plus an island line
# that touches nothing, so "unreachable" can actually be tested.
TOY_DATA = {
    "timing": {"minutes_per_stop": 2, "minutes_per_transfer": 5},
    "lines": {
        "Line A": {"zh": "A线", "nickname": "", "color": "#111111",
                   "stations": ["a1", "a2", "cross", "a3"]},
        "Line B": {"zh": "B线", "nickname": "", "color": "#222222",
                   "stations": ["b1", "cross", "b2"]},
        "Line Z": {"zh": "Z线", "nickname": "", "color": "#333333",
                   "stations": ["z1", "z2"]},
    },
    "stations": {
        "a1": {"en": "A One", "zh": "甲一"},
        "a2": {"en": "A Two", "zh": "甲二"},
        "a3": {"en": "A Three", "zh": "甲三"},
        "b1": {"en": "B One", "zh": "乙一"},
        "b2": {"en": "B Two", "zh": "乙二"},
        "cross": {"en": "Cross", "zh": "交叉"},
        "z1": {"en": "Z One", "zh": "丙一"},
        "z2": {"en": "Z Two", "zh": "丙二"},
    },
}


class ToyNetworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.network = MetroNetwork(TOY_DATA)
        self.router = Router(self.network)

    def test_transfers_are_derived_not_declared(self):
        self.assertTrue(self.network.station("cross").is_transfer)
        self.assertFalse(self.network.station("a1").is_transfer)
        self.assertEqual(
            {s.id for s in self.network.transfer_stations()}, {"cross"}
        )

    def test_same_station_is_trivial(self):
        route = self.router.find_route("a1", "a1")
        self.assertTrue(route.is_trivial)
        self.assertEqual(route.steps, [])
        self.assertEqual(route.total_minutes, 0)

    def test_same_line_needs_no_transfer(self):
        route = self.router.find_route("a1", "a3")
        self.assertEqual(route.transfers, 0)
        self.assertEqual(route.total_stops, 3)
        self.assertEqual(route.total_minutes, 6)
        self.assertEqual(route.lines_used, ["Line A"])

    def test_cross_line_route_charges_one_transfer(self):
        route = self.router.find_route("a1", "b2")
        self.assertEqual(route.transfers, 1)
        self.assertEqual(route.total_stops, 3)  # a1->a2->cross, cross->b2
        self.assertEqual(route.total_minutes, 3 * 2 + 1 * 5)
        self.assertEqual(route.lines_used, ["Line A", "Line B"])

    def test_disconnected_station_raises(self):
        with self.assertRaises(NoRouteFound):
            self.router.find_route("a1", "z2")

    def test_step_sequence_is_well_formed(self):
        steps = self.router.find_route("a1", "b2").steps
        self.assertEqual(steps[0]["action"], "board")
        self.assertEqual(steps[-1]["action"], "exit")
        self.assertEqual([s["action"] for s in steps],
                         ["board", "travel", "transfer", "travel", "exit"])

    def test_direction_points_at_the_right_terminus(self):
        forward = self.router.find_route("a1", "a3").steps[0]
        self.assertEqual(forward["direction"], "a3")
        backward = self.router.find_route("a3", "a1").steps[0]
        self.assertEqual(backward["direction"], "a1")

    def test_rejects_malformed_data(self):
        broken = {"lines": {"L": {"stations": ["nope", "a1"]}},
                  "stations": {"a1": {"en": "A", "zh": "甲"}}}
        with self.assertRaises(MetroDataError):
            MetroNetwork(broken)


class RealNetworkTests(unittest.TestCase):
    """Sanity checks against the shipped Shenzhen map."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.network = MetroNetwork.load()
        cls.router = Router(cls.network)

    def test_every_line_station_is_defined(self):
        for name, line in self.network.lines.items():
            for station_id in line.stations:
                self.assertIn(station_id, self.network.stations,
                              f"{name} references unknown {station_id}")

    def test_network_is_fully_connected(self):
        """Every station must be reachable from Luohu, or the map has a hole."""
        for station in self.network.stations.values():
            route = self.router.find_route("luohu", station.id)
            self.assertTrue(route.is_trivial or route.steps, station.id)

    def test_timing_matches_the_stated_model(self):
        route = self.router.find_route("luohu", "shenzhen_north")
        expected = route.total_stops * 2 + route.transfers * 5
        self.assertEqual(route.total_minutes, expected)

    def test_routes_are_symmetric(self):
        there = self.router.find_route("chiwan", "niuhu")
        back = self.router.find_route("niuhu", "chiwan")
        self.assertEqual(there.total_minutes, back.total_minutes)
        self.assertEqual(there.total_stops, back.total_stops)

    def test_bfs_never_rides_more_stops_than_dijkstra(self):
        pairs = [("luohu", "airport_east"), ("chiwan", "niuhu"),
                 ("shuanglong", "bitou"), ("fubao", "xinxiu")]
        for start, end in pairs:
            fastest = self.router.find_route(start, end, optimize="time")
            fewest = self.router.find_route(start, end, optimize="stops")
            self.assertLessEqual(fewest.total_stops, fastest.total_stops,
                                 f"{start}->{end}")

    def test_dijkstra_prefers_the_direct_ride(self):
        """Luohu -> Laojie is two stops on Line 1; no transfer may appear."""
        route = self.router.find_route("luohu", "laojie")
        self.assertEqual(route.transfers, 0)
        self.assertEqual(route.lines_used, ["Line 1"])

    def test_resolution_by_english_chinese_and_id(self):
        for query in ("Luohu", "luohu", "罗湖", "LUOHU"):
            self.assertEqual(self.network.resolve(query), "luohu")

    def test_ambiguous_and_unknown_names_are_rejected(self):
        # "Qiaocheng" is a prefix of Qiaocheng East and Qiaocheng North and an
        # exact match for neither, so the user has to be more specific.
        with self.assertRaises(StationNotFound) as caught:
            self.network.resolve("Qiaocheng")
        self.assertIn("ambiguous", str(caught.exception))
        with self.assertRaises(StationNotFound):
            self.network.resolve("Hogwarts")

    def test_exact_match_beats_a_longer_neighbour(self):
        """"Futian" is a substring of "Futian Checkpoint" but names a station."""
        self.assertEqual(self.network.resolve("Futian"), "futian")
        self.assertEqual(
            self.network.resolve("Futian Checkpoint"), "futian_checkpoint"
        )

    def test_exact_name_wins_over_substring(self):
        """'Bao'an' is also a substring of 'Bao'an Center' -- exact must win."""
        self.assertEqual(self.network.resolve("Bao'an"), "baoan")


class InstructionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.network = MetroNetwork.load()
        cls.router = Router(cls.network)

    def test_trivial_route_says_already_there(self):
        route = self.router.find_route("luohu", "luohu")
        text = InstructionGenerator(self.network, "en").render(route)
        self.assertIn("already at", text)

    def test_one_sentence_per_step(self):
        route = self.router.find_route("luohu", "shenzhen_north")
        sentences = InstructionGenerator(self.network, "en").steps(route)
        self.assertEqual(len(sentences), len(route.steps))

    def test_languages_render_their_own_script(self):
        route = self.router.find_route("luohu", "laojie")
        english = InstructionGenerator(self.network, "en").render(route)
        chinese = InstructionGenerator(self.network, "zh").render(route)
        both = InstructionGenerator(self.network, "both").render(route)
        self.assertIn("Luohu", english)
        self.assertNotIn("罗湖", english)
        self.assertIn("罗湖", chinese)
        self.assertNotIn("Luohu", chinese)
        self.assertIn("Luohu", both)
        self.assertIn("罗湖", both)

    def test_singular_plural_agreement(self):
        one = self.router.find_route("luohu", "guomao")
        text = InstructionGenerator(self.network, "en").render(one)
        self.assertIn("Ride 1 stop to", text)
        self.assertNotIn("1 stops", text)

    def test_rejects_unknown_language(self):
        with self.assertRaises(ValueError):
            InstructionGenerator(self.network, "klingon")


if __name__ == "__main__":
    unittest.main()
