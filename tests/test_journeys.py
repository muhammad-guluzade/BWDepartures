"""The navigator's journeys (efa/parse.py journey part, services/journeys.py,
GET /api/v1/journeys). EFA's journey planner is replaced by answers in its
real shape (rapidJSON, XML_TRIP_REQUEST2), so no network is needed."""
import unittest
from datetime import datetime, timedelta, timezone

from app import app
from efa import client, parse
from gtfs import store
from services import departures, journeys

app.config["RATE_LIMIT"] = False
# the navigator's tests expect it on, whatever BW_NAVIGATOR says (NavigatorSwitchTest tests "off")
app.config["NAVIGATOR"] = True

PRAG = "de:08111:115"
ULM = "de:08421:1008"
LATER = datetime.now(timezone.utc) + timedelta(hours=2)


def at(minutes):
    return (LATER + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def platform(stop_id, name, number, coord, **times):
    return {"isGlobalId": True, "id": f"{stop_id}:1:{number}", "name": name, "type": "platform", "coord": coord,
            "properties": {"platformName": f"Gleis {number}", "platform": number},
            "parent": {"isGlobalId": True, "id": stop_id, "name": name, "type": "stop",
                       "parent": {"name": "Stuttgart", "type": "locality"}}, **times}


U15 = {"id": "vvs:20015: :H:j26", "name": "Stadtbahn U15", "disassembledName": "U15", "number": "U15",
       "product": {"id": 2, "class": 3, "name": "Stadtbahn"}, "destination": {"name": "Heumaden"}, "properties": {"tripCode": 1004}}
RE5 = {"id": "ddb:90T05: :H:j26", "name": "R-Bahn RE5", "disassembledName": "RE5", "number": "RE5",
       "product": {"id": 8, "class": 13, "name": "R-Bahn"}, "destination": {"name": "Ulm Hauptbahnhof"}, "properties": {"tripCode": 19005}}
ICE = {"id": "ddb:11569: :H:j26", "name": "ICE 569 InterCityExpress", "number": "569",
       "product": {"id": 0, "class": 16, "name": "Zug"}, "destination": {"name": "München Hbf"},
       "properties": {"tripCode": 569, "trainType": "ICE", "trainNumber": "569"}}
NOTICE = {"priority": "high", "type": "lineInfo", "infoLinks": [{
    "urlText": "Stuttgart-Mitte/Degerloch: Streckenunterbrechung",
    "content": "<div><strong>Von Samstag</strong>&nbsp;bis Sonntag<br />gesperrt.</div><ul><li>Linie U5: Leinfelden</li></ul>"}]}


def ride(transportation, start, end, infos=()):
    return {"duration": 240, "distance": 3000, "origin": start, "destination": end, "transportation": transportation,
            "stopSequence": [start, {**platform("de:08111:6056", "Charlottenplatz", "1", [48.77, 9.18],
                                                 arrivalTimePlanned=at(2), departureTimePlanned=at(2))}, end],
            "infos": list(infos), "coords": [start["coord"], end["coord"]]}


def walk(start, end, minutes, cls=99):
    return {"duration": minutes * 60, "distance": minutes * 80, "origin": start, "destination": end,
            "transportation": {"product": {"class": cls, "name": "footpath"}}, "coords": [start["coord"], end["coord"]], "infos": []}


def journey(second=RE5, change_at=14, walk_minutes=10, infos=(NOTICE,)):
    """Pragfriedhof --U15--> Arnulf-Klett-Platz --walk--> Hauptbahnhof --second--> Ulm."""
    first = ride(U15, platform(PRAG, "Pragfriedhof", "1", [48.798, 9.182], departureTimePlanned=at(0), departureTimeEstimated=at(0)),
                 platform("de:08111:6112", "Hauptbahnhof (A.-Klett-Platz)", "4", [48.783, 9.181], arrivalTimePlanned=at(4), arrivalTimeEstimated=at(4)),
                 infos)
    legs = [first]
    if walk_minutes:
        legs.append(walk(first["destination"], platform("de:08111:6115", "Stuttgart Hauptbahnhof", "15", [48.784, 9.183]), walk_minutes))
    legs.append(ride(second, platform("de:08111:6115", "Stuttgart Hauptbahnhof", "15", [48.784, 9.183],
                                      departureTimePlanned=at(change_at), departureTimeEstimated=at(change_at)),
                     platform(ULM, "Ulm Hauptbahnhof", "2", [48.399, 9.983], arrivalTimePlanned=at(70), arrivalTimeEstimated=at(70))))
    return {"rating": 0, "isAdditional": False, "interchanges": 1, "legs": legs, "fare": {}}


class ParseJourneyTest(unittest.TestCase):
    def test_a_connection(self):
        j = parse.parse_journey(journey())
        self.assertEqual([leg["kind"] for leg in j["legs"]], ["ride", "walk", "ride"])
        self.assertEqual((j["changes"], j["minutes"], j["dticket"]), (1, 70, True))
        u15, foot, re5 = j["legs"]
        self.assertEqual((u15["line"], u15["destination"], u15["from"]["platform"]["code"], u15["to"]["platform"]["code"]), ("U15", "Heumaden", "1", "4"))
        self.assertEqual((foot["minutes"], foot["metres"]), (10, 800))
        self.assertEqual([s["name"] for s in u15["stops"]], ["Charlottenplatz"])        # the stops in between
        self.assertEqual(u15["path"], [[48.798, 9.182], [48.783, 9.181]])
        # the same id as the ride on the board of the stop it leaves from: the live checks look it up there
        self.assertEqual(u15["departure_id"], parse.departure_id(parse.leg_as_event(journey()["legs"][0])))
        self.assertEqual(u15["board_stop"], PRAG)

    def test_train_names_and_d_ticket_as_on_the_boards(self):
        j = parse.parse_journey(journey(second=ICE, change_at=14))
        ice = j["legs"][-1]
        self.assertEqual((ice["line"], ice["dticket"], ice["brand"]), ("ICE 569", False, "ice"))
        self.assertFalse(j["dticket"])

    def test_the_french_copy_of_a_tgv_is_called_tgv(self):
        sncf = {"id": "snc:89FRL:9:R:26o", "name": "Zug", "number": None, "product": {"id": 0, "class": 13, "name": "Zug"},
                "operator": {"id": "x0008", "name": "SNCF"}, "destination": {"name": "Paris Est"},
                "properties": {"tripCode": 3, "attributes": ["LONG_DISTANCE_TRAINS", "SUPPLEMENT"]}}
        legs = parse.parse_journey(journey(second=sncf))["legs"]
        self.assertEqual(legs[-1]["line"], "TGV")
        regional = {**sncf, "properties": {"tripCode": 3, "attributes": []}}     # (a numberless regional train stays as it is)
        self.assertEqual(parse.parse_journey(journey(second=regional))["legs"][-1]["line"], "Zug")

    def test_walks_of_both_kinds(self):
        for cls in (99, 100):
            leg = walk(platform(PRAG, "a", "1", [48.0, 9.0]), platform(ULM, "b", "2", [48.1, 9.1]), 5, cls)
            self.assertTrue(parse.is_walk(leg), cls)
        self.assertFalse(parse.is_walk(ride(U15, platform(PRAG, "a", "1", [48.0, 9.0]), platform(ULM, "b", "2", [48.1, 9.1]))))

    def test_staying_seated_is_no_ride(self):
        # "nicht umsteigen" (class 97) between two lines that are one bus: no ride called "?"
        j = journey(walk_minutes=0)
        stay = {"duration": 60, "origin": j["legs"][0]["destination"], "destination": j["legs"][1]["origin"],
                "transportation": {"product": {"class": 97, "name": "nicht umsteigen"}}, "infos": []}
        j["legs"].insert(1, stay)
        parsed = parse.parse_journey(j)
        self.assertEqual([leg.get("line") for leg in parsed["legs"]], ["U15", "RE5"])

    def test_changes(self):
        # U15 arrives at +4, a 10 min walk, the train leaves at +14: no minute to spare, possible
        t = parse.parse_journey(journey(change_at=14))["transfers"][0]
        self.assertEqual((t["walk_minutes"], t["needed_minutes"], t["spare_minutes"], t["possible"]), (10, 10, 0, True))
        # the U15 a minute late: one minute short
        late = journey(change_at=14)
        late["legs"][0]["destination"]["arrivalTimeEstimated"] = at(5)
        t = parse.parse_journey(late)["transfers"][0]
        self.assertEqual((t["spare_minutes"], t["possible"]), (-1, False))
        # no walk in between: at least 2 minutes
        t = parse.parse_journey(journey(change_at=6, walk_minutes=0))["transfers"][0]
        self.assertEqual((t["needed_minutes"], t["spare_minutes"], t["possible"]), (2, 0, True))

    def test_a_change_the_timetable_plans_shorter_is_kept_until_delays_shorten_it(self):
        """The 2-minute rule is for changes delays squeeze; a bus the timetable
        plans 1 minute after the tram (operators' own plan) stays possible."""
        t = parse.parse_journey(journey(change_at=5, walk_minutes=0))["transfers"][0]
        self.assertEqual((t["needed_minutes"], t["spare_minutes"], t["possible"]), (2, -1, True))
        # 3 planned minutes, the first ride 2 minutes late: 1 left, under 2
        squeezed = journey(change_at=7, walk_minutes=0)
        squeezed["legs"][0]["destination"]["arrivalTimeEstimated"] = at(6)
        self.assertFalse(parse.parse_journey(squeezed)["transfers"][0]["possible"])
        # 1 minute late: 2 left, enough
        squeezed["legs"][0]["destination"]["arrivalTimeEstimated"] = at(5)
        self.assertTrue(parse.parse_journey(squeezed)["transfers"][0]["possible"])
        # the next ride leaving before this one arrives: never
        squeezed["legs"][0]["destination"]["arrivalTimeEstimated"] = at(8)
        self.assertFalse(parse.parse_journey(squeezed)["transfers"][0]["possible"])

    def test_notices_as_plain_text(self):
        notice = parse.parse_journey(journey())["legs"][0]["notices"][0]
        self.assertEqual(notice["title"], "Stuttgart-Mitte/Degerloch: Streckenunterbrechung")
        self.assertEqual(notice["text"], "Von Samstag bis Sonntag\ngesperrt.\n• Linie U5: Leinfelden")
        self.assertEqual(notice["priority"], "high")
        self.assertNotIn("<", notice["text"])

    def test_a_point_on_the_map_is_a_place_without_a_board(self):
        raw = journey()
        address = {"id": "streetID:1", "name": "Mitte (Stuttgart), Kleiner Schloßplatz 13", "type": "address", "coord": [48.7789, 9.1767]}
        raw["legs"].insert(0, walk(address, raw["legs"][0]["origin"], 5, cls=100))
        start = parse.parse_journey(raw)["legs"][0]["from"]
        self.assertEqual((start["kind"], start["id"], start["has_board"], start["name"]),
                         ("place", "", False, "Mitte (Stuttgart), Kleiner Schloßplatz 13"))

    def test_same_place(self):
        self.assertEqual(parse.journey_error({"systemMessages": [{"type": "error", "code": -4007, "text": "origin too close to destination"}]}), "too_close")
        self.assertIsNone(parse.journey_error({"systemMessages": []}))


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.real = (client.get_journeys, departures.stop_importance, store.in_area, departures.outside_timetable)
        self.answer = {"journeys": [journey()]}
        self.asked = []

        self.rail_answer = {"journeys": []}
        self.rail_asked = []
        self.twin_answer = {"journeys": []}
        self.twin_asked = []
        self.timetable_answer = {"journeys": []}
        self.timetable_asked = []

        def fake(origin, destination, start=None, arrive=False, classes=(), count=5, realtime=True):
            # the timetable's own connections, as if nothing were late (see rescued())
            if not realtime:
                self.timetable_asked.append((origin, destination, start, arrive, tuple(classes), count))
                if isinstance(self.timetable_answer, Exception):
                    raise self.timetable_answer
                return self.timetable_answer
            # the twins' question: every kind of transport but one
            if classes and count == journeys.RAIL_JOURNEYS and not set(classes) <= set(journeys.WITHOUT_BUSES):
                self.twin_asked.append((origin, destination, start, arrive, tuple(classes), count))
                if isinstance(self.twin_answer, Exception):
                    raise self.twin_answer
                return self.twin_answer
            # the second question, without buses, is answered on its own (see AlternativesTest)
            if list(classes) and set(classes) <= set(journeys.WITHOUT_BUSES):
                self.rail_asked.append((origin, destination, start, arrive, tuple(classes), count))
                if isinstance(self.rail_answer, Exception):
                    raise self.rail_answer
                return self.rail_answer
            self.asked.append((origin, destination, start, arrive, tuple(classes), count))
            return self.answer
        client.get_journeys = fake
        departures.stop_importance = lambda: {}

        def no_db(*a, **kw):
            raise store.StopsNotImported("test")
        store.in_area = no_db

        self.real_live_ride = journeys.live_ride

    def tearDown(self):
        client.get_journeys, departures.stop_importance, store.in_area, departures.outside_timetable = self.real
        journeys.live_ride = self.real_live_ride

    def test_stops_and_points(self):
        result = journeys.plan(PRAG, ULM)
        self.assertEqual(self.asked[0][:2], (PRAG, ULM))
        self.assertEqual((result["from"]["kind"], result["from"]["name"], result["to"]["name"]), ("stop", "Pragfriedhof", "Ulm Hauptbahnhof"))
        journeys.plan("48.7789, 9.1767", ULM)
        self.assertEqual(self.asked[1][0], "9.17670:48.77890:WGS84[dd.ddddd]")

    def test_places_refused(self):
        for bad in ("", "Stuttgart", "de:09162:100", "48.5734,7.7521x", "91,9"):
            with self.assertRaises(journeys.InvalidPlace, msg=bad):
                journeys.plan(bad, ULM)
        with self.assertRaises(journeys.InvalidPlace):
            journeys.plan("52.52,13.40", ULM)                 # Berlin: outside the state's box
        # with the stop database: a point is in the state when a stop of it is near
        store.in_area = lambda *a, **kw: ([], 0)
        with self.assertRaises(journeys.InvalidPlace):
            journeys.plan("48.5734,7.7521", ULM)              # Strasbourg, inside the box
        store.in_area = lambda *a, **kw: ([{"id": "x"}], 1)
        journeys.plan("48.7789,9.1767", ULM)
        # a stop id the database does not know
        departures.stop_importance = lambda: {PRAG: 1.0}
        with self.assertRaises(journeys.InvalidPlace):
            journeys.plan(PRAG, "de:08111:99999")

    def test_a_point_across_the_border(self):
        """Neu-Ulm is next to Ulm's stops: the town the planner found there
        decides (its municipality key; 08 = Baden-Württemberg)."""
        store.in_area = lambda *a, **kw: ([{"id": "x"}], 1)
        neu_ulm = journey()
        neu_ulm["legs"][0]["origin"]["parent"] = {"id": "placeID:9775135:-1", "name": "Neu-Ulm", "type": "locality"}
        self.answer = {"journeys": [neu_ulm]}
        with self.assertRaisesRegex(journeys.InvalidPlace, "from is outside"):
            journeys.plan("48.3955,10.0115", ULM)
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)    # a stop id is never asked
        neu_ulm["legs"][-1]["destination"]["parent"] = {"id": "placeID:9775135:-1"}
        with self.assertRaisesRegex(journeys.InvalidPlace, "to is outside"):
            journeys.plan(PRAG, "48.3955,10.0115")
        ulm = journey()
        ulm["legs"][0]["origin"]["parent"] = {"id": "placeID:8421000:-1", "name": "Ulm", "type": "locality"}
        self.answer = {"journeys": [ulm]}
        self.assertEqual(len(journeys.plan("48.3984,9.9916", ULM)["journeys"]), 1)
        self.answer = {"journeys": ["nonsense", {"legs": "x"}]}             # nothing says: allowed
        self.assertEqual(journeys.plan("48.3984,9.9916", ULM)["journeys"], [])

    def test_trains_are_one_kind_as_on_the_boards(self):
        self.answer = {"journeys": [journey(second=ICE)]}
        legs = journeys.plan(PRAG, ULM)["journeys"][0]["legs"]
        self.assertEqual(legs[-1]["mode"], {"id": "trains", "name": "Trains"})
        self.assertEqual(legs[0]["mode"], {"id": "u_bahn_tram", "name": "U-Bahn / Tram"})

    def test_d_ticket_only(self):
        self.answer = {"journeys": [journey(second=ICE), journey(second=RE5)]}
        result = journeys.plan(PRAG, ULM, dticket=True)
        self.assertEqual([j["legs"][-1]["line"] for j in result["journeys"]], ["RE5"])
        self.assertNotIn(16, self.asked[0][4])                # long-distance trains not asked for
        self.assertIn(13, self.asked[0][4])
        self.assertTrue(result["dticket"])

    def test_connections_that_left_already_are_dropped(self):
        gone = journey()
        for leg in gone["legs"]:
            for end in (leg["origin"], leg["destination"]):
                for key in [k for k in end if k.endswith("Planned") or k.endswith("Estimated")]:
                    end[key] = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.answer = {"journeys": [gone, journey()]}
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)

    def shifted(self, minutes, walk_first=0):
        """journey() moved by `minutes`; with `walk_first`, a walk of that many minutes from a
        point on the map to Pragfriedhof comes first, ending when the U15 leaves."""
        moved = journey()
        for leg in moved["legs"]:
            for end in (leg["origin"], leg["destination"]):
                for key in [k for k in end if k.endswith("Planned") or k.endswith("Estimated")]:
                    end[key] = (datetime.strptime(end[key], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                                + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if walk_first:
            board = moved["legs"][0]["origin"]
            home = {"name": "Rosenackerweg 20", "type": "singlehouse", "coord": [48.9, 9.17],
                    "departureTimePlanned": (datetime.strptime(board["departureTimePlanned"], "%Y-%m-%dT%H:%M:%SZ")
                                             - timedelta(minutes=walk_first)).strftime("%Y-%m-%dT%H:%M:%SZ")}
            moved["legs"].insert(0, {**walk(home, board, walk_first, cls=100)})
        return moved

    def test_connections_starting_a_little_early_are_kept(self):
        # asked for LATER (ahead): as DB Navigator, up to 3 minutes earlier, not more
        self.answer = {"journeys": [self.shifted(-2), self.shifted(-5), self.shifted(1)]}
        result = journeys.plan(PRAG, ULM, start=LATER)
        self.assertEqual([j["departure"]["time"] for j in result["journeys"]], [at(-2), at(1)])

    def test_a_time_chosen_in_the_past_is_looked_at_as_if_it_were_now(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        to_past = round((past - LATER).total_seconds() / 60)
        self.answer = {"journeys": [self.shifted(to_past + 5), self.shifted(to_past - 2, walk_first=0), self.shifted(to_past + 5, walk_first=7)]}
        result = journeys.plan(PRAG, ULM, start=past.replace(second=0, microsecond=0))
        # leaving 5 minutes after the chosen time, and a walk that starts 2 minutes before it: shown; a U15 2 minutes before: gone
        self.assertEqual(len(result["journeys"]), 2)

    def test_now_a_walk_started_a_little_ago_is_kept_a_ride_that_left_is_not(self):
        now = datetime.now(timezone.utc)
        to_now = round((now - LATER).total_seconds() / 60)
        walk_ago = self.shifted(to_now + 5, walk_first=7)   # the walk should have started 2 minutes ago, the U15 leaves in 5
        bus_gone = self.shifted(to_now - 2)                 # its U15 left 2 minutes ago
        self.answer = {"journeys": [walk_ago, bus_gone]}
        result = journeys.plan(PRAG, ULM)
        self.assertEqual(len(result["journeys"]), 1)
        self.assertEqual(result["journeys"][0]["legs"][0]["kind"], "walk")

    def test_arrive_by(self):
        late = journey()
        late["legs"][-1]["destination"]["arrivalTimePlanned"] = at(200)
        self.answer = {"journeys": [journey(), late]}
        result = journeys.plan(PRAG, ULM, start=LATER + timedelta(minutes=80), arrive=True)
        self.assertEqual(len(result["journeys"]), 1)
        self.assertTrue(result["arrive"])
        self.assertTrue(self.asked[0][3])

    def test_flights_and_on_demand_are_not_offered(self):
        demand = journey(second={**RE5, "product": {"class": 10, "name": "AST"}})
        self.answer = {"journeys": [demand]}
        self.assertEqual(journeys.plan(PRAG, ULM)["journeys"], [])

    def test_taxis_are_not_offered(self):
        # leaving buses out, the planner fills in with taxis (product class 105), even for a whole trip
        taxi = {"product": {"class": 105, "name": "Taxi"}, "properties": {"tripCode": 0}}
        whole = {**journey(), "legs": [{**journey()["legs"][0], "transportation": taxi}]}
        first = journey()
        first["legs"][0] = {**first["legs"][0], "transportation": taxi}
        self.rail_answer = {"journeys": [whole, first]}
        self.answer = {"journeys": [journey(second=ICE, change_at=20)]}
        result = journeys.plan(PRAG, ULM)
        self.assertEqual([[leg.get("line") for leg in j["legs"] if leg["kind"] == "ride"] for j in result["journeys"]], [["U15", "ICE 569"]])
        for cls in (101, 105, 107):
            self.assertTrue(parse.is_excluded({"transportation": {"product": {"class": cls}}}))
        for cls in (0, 5, 13, 99, 100):
            self.assertFalse(parse.is_excluded({"transportation": {"product": {"class": cls}}}))

    def test_an_equally_fast_twin_is_added(self):
        # the fastest goes U15 -> RE5; asked without the RE5's kind (13), the planner offers U15 -> ICE, as fast
        self.answer = {"journeys": [journey()]}
        twin = journey(second={**ICE, "product": {"id": 0, "class": 16, "name": "Zug"}})
        slower = journey(second=ICE)
        slower["legs"][-1]["destination"]["arrivalTimePlanned"] = slower["legs"][-1]["destination"]["arrivalTimeEstimated"] = at(80)
        self.twin_answer = {"journeys": [twin, slower]}
        result = journeys.plan(PRAG, ULM, start=LATER)
        self.assertEqual(len(self.twin_asked), 1)
        self.assertNotIn(13, self.twin_asked[0][4])           # the kind after the change left out
        self.assertIn(3, self.twin_asked[0][4])               # ... the others asked for
        lines = [[leg.get("line") for leg in j["legs"] if leg["kind"] == "ride"] for j in result["journeys"]]
        self.assertEqual(sorted(map(tuple, lines)), [("U15", "ICE 569"), ("U15", "RE5")])   # the slower one is not a twin
        self.assertTrue(all("_classes" not in j for j in result["journeys"]))

    def test_without_a_change_the_ride_s_kind_is_left_out_and_never_when_arriving_by(self):
        # (since 6 November: bus 44 next to the U6 at Charlottenplatz, as DB Navigator)
        one_ride = journey(walk_minutes=0)
        one_ride["legs"] = one_ride["legs"][:1]
        self.answer = {"journeys": [one_ride]}
        journeys.plan(PRAG, ULM, start=LATER)
        self.assertEqual(len(self.twin_asked), 1)
        self.assertNotIn(3, self.twin_asked[0][4])            # the U15's kind (Stadtbahn) left out
        self.twin_asked.clear()
        self.answer = {"journeys": [journey()]}
        journeys.plan(PRAG, ULM, start=LATER, arrive=True)
        self.assertEqual(self.twin_asked, [])

    def test_the_same_departure_a_little_slower_is_added(self):
        # the fastest leaves at(0) and arrives at(70); another way leaving at(5) and arriving at(85) is shown,
        # one leaving at(30) and arriving at(85), or leaving at(5) and arriving at(100), is not
        self.answer = {"journeys": [journey()]}
        def other(leave, arrive):
            j = journey(second=ICE)
            first = j["legs"][0]["origin"]
            first["departureTimePlanned"] = first["departureTimeEstimated"] = at(leave)
            last = j["legs"][-1]["destination"]
            last["arrivalTimePlanned"] = last["arrivalTimeEstimated"] = at(arrive)
            j["legs"][0]["transportation"] = {**U15, "properties": {"tripCode": 2000 + leave + arrive}}
            return j
        self.twin_answer = {"journeys": [other(5, 85), other(30, 85), other(5, 100)]}
        result = journeys.plan(PRAG, ULM, start=LATER)
        self.assertEqual(sorted(j["departure"]["time"] for j in result["journeys"]), [at(0), at(5)])

    def test_twins_are_a_bonus(self):
        self.twin_answer = client.EfaError("down")
        self.assertEqual(len(journeys.plan(PRAG, ULM, start=LATER)["journeys"]), 1)

    def test_why_nothing(self):
        self.answer = {"journeys": [], "systemMessages": [{"type": "error", "code": -4007}]}
        self.assertEqual(journeys.plan(PRAG, ULM)["empty"], "too_close")
        self.answer = {"journeys": []}
        self.assertEqual(journeys.plan(PRAG, ULM)["empty"], "none")
        departures.outside_timetable = lambda start: True
        self.assertEqual(journeys.plan(PRAG, ULM, start=LATER)["empty"], "no_timetable")

    def test_alternatives_without_buses_are_added(self):
        """The planner keeps the fastest connections; a slower one by train or
        S-Bahn is asked for separately and added (DB shows such alternatives)."""
        rail = journey(second=ICE, change_at=20)
        self.rail_answer = {"journeys": [journey(), rail]}           # the first one the planner had already
        result = journeys.plan(PRAG, ULM)
        self.assertEqual(len(result["journeys"]), 2)
        self.assertEqual(len(self.rail_asked), 1)
        self.assertNotIn(5, self.rail_asked[0][4])                     # no buses
        self.assertEqual(sorted(j["departure"]["time"] for j in result["journeys"]), [j["departure"]["time"] for j in result["journeys"]])
        # with D-Ticket only, the second question has no long-distance trains either
        self.rail_asked.clear()
        journeys.plan(PRAG, ULM, dticket=True)
        self.assertNotIn(16, self.rail_asked[0][4])

    def test_the_same_rides_with_another_walk_are_shown_once(self):
        """Albstadt -> Sigmaringen: the train-only question walked 5 minutes to the
        RE6a the first answer reaches in 3; DB shows that train once."""
        same = journey(walk_minutes=6)
        self.assertNotEqual(parse.parse_journey(same, {})["id"], parse.parse_journey(journey(), {})["id"])
        self.rail_answer = {"journeys": [same]}
        result = journeys.plan(PRAG, ULM)
        self.assertEqual(len(result["journeys"]), 1)
        self.assertEqual([leg.get("minutes") for leg in result["journeys"][0]["legs"] if leg["kind"] == "walk"], [10])
        # the twins' question is held to the same rule
        self.rail_answer, self.twin_answer = {"journeys": []}, {"journeys": [same]}
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)

    def nordbahnhof(self, first_late, second_late, cancelled=False):
        """The timetable's connection: U15 arrives at +4, RE5 leaves the same
        place at +8 (4 minutes to change), arrives at +60; the live answer only
        has a later one (RE5 at +30, arriving +70). The rides' live times are
        made up: the U15 `first_late`, the RE5 `second_late` minutes late."""
        tight = journey(change_at=8, walk_minutes=0)
        for end in ("arrivalTimePlanned", "arrivalTimeEstimated"):
            tight["legs"][-1]["destination"][end] = at(60)
        self.answer = {"journeys": [journey(change_at=30, walk_minutes=0)]}
        self.timetable_answer = {"journeys": [tight]}
        asked = []

        def stop(stop_id, minutes, delay):
            return {"id": stop_id, "name": stop_id, "time": at(minutes + delay), "planned": at(minutes), "delay": delay,
                    "realtime": True, "cancelled": cancelled and stop_id == ULM}

        def fake_live(ride):
            asked.append(ride)
            stop_id, departure = ride
            if stop_id == PRAG:
                trip = {"here": stop(PRAG, 0, first_late), "onward": [stop("de:08111:6112", 4, first_late)]}
            else:
                trip = {"here": stop("de:08111:6115", 8, second_late), "onward": [stop(ULM, 60, second_late)]}
            return {"stop": stop_id, "id": departure, "ok": True, "trip": trip}
        journeys.live_ride = fake_live
        return journeys.plan(PRAG, ULM), asked

    def test_a_change_a_delay_made_too_short_for_the_planner_is_offered_when_it_still_works(self):
        """Nordbahnhof, 4 November 2026: S4 3 late, U12 2 late, 4 planned minutes
        became 3; the planner (its own minimum there 4) dropped it, DB kept it."""
        result, asked = self.nordbahnhof(3, 2)
        self.assertEqual(len(self.timetable_asked), 1)
        self.assertEqual(len(asked), 2)
        self.assertEqual(len(result["journeys"]), 2)
        tight = next(j for j in result["journeys"] if j["arrival"]["planned"] == at(60))
        self.assertEqual((tight["departure"]["time"], tight["arrival"]["time"], tight["arrival"]["delay"]), (at(3), at(62), 2))
        rides = [leg for leg in tight["legs"] if leg["kind"] == "ride"]
        self.assertEqual((rides[0]["to"]["time"], rides[1]["from"]["time"]), (at(7), at(10)))
        self.assertEqual((tight["transfers"][0]["spare_minutes"], tight["transfers"][0]["possible"]), (1, True))
        self.assertNotIn("_classes", tight)

    def test_a_change_squeezed_under_two_minutes_is_not_offered(self):
        result, _ = self.nordbahnhof(3, 0)       # 1 minute left
        self.assertEqual(len(result["journeys"]), 1)
        result, _ = self.nordbahnhof(5, 0)       # the next ride leaves before this one arrives
        self.assertEqual(len(result["journeys"]), 1)
        result, _ = self.nordbahnhof(0, 0, cancelled=True)
        self.assertEqual(len(result["journeys"]), 1)

    def test_the_timetable_is_asked_only_where_live_times_exist(self):
        self.nordbahnhof(3, 2)
        self.timetable_asked.clear()
        journeys.plan(PRAG, ULM, datetime.now(timezone.utc) + timedelta(hours=5))
        self.assertEqual(self.timetable_asked, [])
        # the same rides as the live answer's: nothing to look up
        self.answer = self.timetable_answer = {"journeys": [journey()]}
        asked = []
        journeys.live_ride = lambda ride: asked.append(ride)
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)
        self.assertEqual(asked, [])
        # no timetable answer: the list goes without
        self.timetable_answer = client.EfaError("down")
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)

    def test_a_ride_that_cannot_be_checked_is_not_offered(self):
        self.nordbahnhof(3, 2)
        journeys.live_ride = lambda ride: {"stop": ride[0], "id": ride[1], "ok": False,
                                           "error": {"code": "unknown_departure", "message": "gone"}}
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)

    def test_alternatives_are_a_bonus(self):
        # its question failing, the first answer is shown alone
        self.rail_answer = client.EfaError("down")
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)
        # a walk only, or much later than the rest: no alternative
        walk_only = journey()
        walk_only["legs"] = [walk(walk_only["legs"][0]["origin"], walk_only["legs"][-1]["destination"], 120)]
        late = journey(change_at=14)
        for leg in late["legs"]:
            for end in (leg["origin"], leg["destination"]):
                for key in [k for k in end if k.endswith("Planned") or k.endswith("Estimated")]:
                    end[key] = at(200)
        self.rail_answer = {"journeys": [walk_only, late]}
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)

    def test_one_unreadable_connection_is_skipped(self):
        broken = journey()
        broken["legs"][0]["origin"] = "nonsense"
        self.answer = {"journeys": [broken, journey()]}
        self.assertEqual(len(journeys.plan(PRAG, ULM)["journeys"]), 1)


class JourneysApiTest(unittest.TestCase):
    def setUp(self):
        self.real = (client.get_journeys, departures.stop_importance, store.in_area)
        client.get_journeys = lambda *a, **kw: {"journeys": [journey()]}
        departures.stop_importance = lambda: {}

        def no_db(*a, **kw):
            raise store.StopsNotImported("test")
        store.in_area = no_db
        self.http = app.test_client()

    def tearDown(self):
        client.get_journeys, departures.stop_importance, store.in_area = self.real

    def test_shape(self):
        res = self.http.get(f"/api/v1/journeys?from={PRAG}&to={ULM}")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(list(body), ["from", "to", "when", "arrive", "dticket", "journeys", "empty"])
        j = body["journeys"][0]
        self.assertEqual(list(j), ["id", "departure", "arrival", "minutes", "changes", "dticket", "legs", "transfers"])
        self.assertEqual(list(j["legs"][0]), ["kind", "line", "mode", "brand", "destination", "dticket", "replacement", "departure_id",
                                              "board_stop", "from", "to", "stops", "path", "notices"])
        self.assertEqual(list(j["legs"][1]), ["kind", "minutes", "metres", "from", "to", "path"])
        self.assertEqual(list(j["transfers"][0]), ["at", "to", "walk_minutes", "needed_minutes", "spare_minutes", "possible"])

    def test_options(self):
        asked = []
        client.get_journeys = lambda *a, **kw: asked.append((a, kw)) or {"journeys": []}
        self.http.get(f"/api/v1/journeys?from={PRAG}&to={ULM}&when=2026-11-02T08:00&arrive=1&dticket=1")
        (origin, destination, start, arrive, classes, count), _ = asked[0]
        self.assertEqual((start, arrive), (datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc), True))
        self.assertTrue(classes)

    def test_errors(self):
        for query in ("", f"from={PRAG}", f"from=Stuttgart&to={ULM}", f"from={PRAG}&to={ULM}&when=tomorrow"):
            res = self.http.get(f"/api/v1/journeys?{query}")
            self.assertEqual((res.status_code, res.get_json()["error"]["code"]), (400, "invalid_parameter"), query)

    def test_counts_as_a_timetable_request(self):
        from api import limits
        self.assertEqual(limits.KIND_OF_ENDPOINT["api_v1.plan_journeys"], "timetable")


if __name__ == "__main__":
    unittest.main()


class WorthAddingTest(unittest.TestCase):
    """A connection without buses next to the first answer's (Ravensburg -> Weingarten, 6 November 2026)."""

    @staticmethod
    def conn(dep, arr, walks=()):
        legs = [{"kind": "ride"}] + [{"kind": "walk", "minutes": m} for m in walks]
        return {"departure": {"time": f"2026-11-06T{dep}:00Z"}, "arrival": {"time": f"2026-11-06T{arr}:00Z"}, "legs": legs}

    def test_beaten_by_a_later_one_arriving_much_sooner(self):
        bus = self.conn("06:53", "07:05")
        self.assertFalse(journeys.worth_adding(self.conn("06:45", "07:48", walks=(32,)), [bus]))
        self.assertFalse(journeys.worth_adding(self.conn("06:45", "07:40"), [bus]))           # 35 minutes later, without a walk too
        self.assertTrue(journeys.worth_adding(self.conn("06:45", "07:20"), [bus]))            # 15 minutes later: an alternative
        self.assertTrue(journeys.worth_adding(self.conn("07:00", "07:40"), [bus]))            # the bus leaves before it

    def test_a_much_longer_walk_than_all_others(self):
        others = [self.conn("05:00", "06:30", walks=(7,)), self.conn("05:30", "07:00", walks=(5,))]
        self.assertFalse(journeys.worth_adding(self.conn("05:20", "06:20", walks=(25,)), others))
        self.assertTrue(journeys.worth_adding(self.conn("05:20", "06:20", walks=(14,)), others))
        self.assertTrue(journeys.worth_adding(self.conn("05:20", "06:20", walks=(25,)), others + [self.conn("05:10", "06:40", walks=(21,))]))


class NeedlessWalkTest(unittest.TestCase):
    @staticmethod
    def conn(dep, arr, ride=True):
        return {"departure": {"time": f"2026-11-06T{dep}:00Z"}, "arrival": {"time": f"2026-11-06T{arr}:00Z"},
                "legs": [{"kind": "ride" if ride else "walk", "minutes": 17}]}

    def test_a_walk_is_left_out_when_a_ride_about_as_early_arrives_sooner(self):
        walk, u7 = self.conn("11:30", "11:47", ride=False), self.conn("11:31", "11:33")
        self.assertTrue(journeys.needless_walk(walk, [walk, u7]))
        self.assertFalse(journeys.needless_walk(u7, [walk, u7]))
        self.assertFalse(journeys.needless_walk(walk, [walk, self.conn("11:31", "11:50")]))   # the ride arrives later
        self.assertFalse(journeys.needless_walk(walk, [walk, self.conn("12:00", "12:02")]))   # it leaves half an hour later
