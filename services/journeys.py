"""The navigator: connections from one place to another, from EFA's journey
planner, with the departure boards' rules for line names, D-Ticket, brands
and notices (efa/parse.py), the changes checked, and start and destination
in Baden-Württemberg.

A place is a stop id ("de:08111:115") or a point "lat,lon" (a tap on the
map, or the user's location)."""

import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from efa import client, parse
from gtfs import store
from services import departures

log = logging.getLogger(__name__)

# the D-Ticket switch: only the classes whose rides the boards mark valid
DTICKET_CLASSES = sorted(parse.DTICKET_CLASSES)
# more connections are asked for with the switch on: some still fall out
JOURNEYS, JOURNEYS_DTICKET = 6, 8
# The planner keeps only the fastest connections; a slower one by S-Bahn is
# left out even when people would take it (DB shows such alternatives). So it
# is also asked for connections without buses, and up to this many of them that
# its first answer did not have are added (the user's choice, 2026-10-27).
WITHOUT_BUSES = [0, 1, 2, 3, 4, 13, 14, 15, 16]   # trains, S-Bahn, U-Bahn / Stadtbahn, tram
RAIL_JOURNEYS = 4
RAIL_ALTERNATIVES = 3
RAIL_LATER_MINUTES = 60   # an alternative arrives at most this much after the latest connection found
# ... and is left out when a connection of the first answer that leaves no earlier
# arrives this much sooner, or when it walks much more than all of them (Ravensburg ->
# Weingarten, 6 November 2026: a train and a 32-minute walk, 08:48, next to the
# 07:53 bus arriving 08:05; DB Navigator shows no such thing)
RAIL_BEATEN_MINUTES = 30
RAIL_WALK_MINUTES = 20    # a walk this long ...
RAIL_WALK_MORE = 10       # ... and this much longer than the longest walk of the first answer's connections
# Connections that start a little before the time asked for are kept, as DB
# Navigator does (asked 16:30, it also offers 16:27-16:29): a walk to the station
# planned from 05:28 is still made at 05:30 by walking a little faster. Their
# first bus or train must not have left, though.
EARLIER_MINUTES = 3
GONE_GRACE_MINUTES = 1    # the clocks of the phone and of the timetable can differ a little
# The planner offers ONE connection per start and arrival time: of two equally fast
# ones it keeps one (4 November 2026: walk, S4 05:33, U12 from Nordbahnhof, 06:05 -
# and not the same S4 with bus 53 from Zuffenhausen, also 06:05). DB Navigator
# shows such twins. So when the fastest connection has a change, the planner is
# asked once more without the kind of transport taken after that change, and up
# to TWINS_MAX connections arriving at most TWIN_LATER_MINUTES after it are added
# (the user's choice, option 2). Asked after the first answer (it needs it), and
# only waited for TWIN_TIMEOUT seconds: twins are a bonus.
# The same question also brings what DB Navigator shows next to a connection: the same
# departure by another way, a little slower (6 November 2026: bus 44 next to the U6 at
# Charlottenplatz; the direct tram 5 at 18:00 next to the faster tram + train from
# Heidelberg Bismarckplatz). Without a change, the kind of transport of the only ride
# is left out. Taken: arriving at most TWIN_LATER_MINUTES after the fastest, or leaving
# within TWIN_DEPARTURE_MINUTES of it and arriving at most TWIN_SLOWER_MINUTES after it.
ALL_CLASSES = [c for c in range(20) if c not in parse.EXCLUDED_CLASSES]
TWINS_MAX = 2
TWIN_LATER_MINUTES = 2
TWIN_DEPARTURE_MINUTES = 10
TWIN_SLOWER_MINUTES = 20
TWIN_SLOWER_SHARE = 0.5   # ... and at most half the fastest connection's own duration (a 20-minute trip: 10)
TWIN_TIMEOUT = 8
# the two questions go to the planner at the same time
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="journeys")
# a point counts as in Baden-Württemberg when a stop of the state is this near
NEAR_A_STOP_DEGREES = 0.03          # about 3 km
POINT = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
# without a stop database: the state's rough outline
BW_BOX = (47.50, 7.45, 49.85, 10.55)

EMPTY_TOO_CLOSE = "too_close"        # start and destination are the same place
EMPTY_NONE = "none"                  # the planner found nothing (night, no service)
EMPTY_NO_TIMETABLE = "no_timetable"  # outside the timetable period


class InvalidPlace(ValueError):
    """A start or destination that can't be used; the message says why."""


def place(text: str, which: str) -> tuple:
    """(what EFA is asked with, {kind, id, lat, lon}) for "from" / "to"."""
    text = (text or "").strip()
    if not text:
        raise InvalidPlace(f"{which} is missing: a stop id, or a point as lat,lon")
    found = POINT.match(text)
    if found:
        lat, lon = float(found.group(1)), float(found.group(2))
        if not in_baden_wuerttemberg(lat, lon):
            raise InvalidPlace(f"{which} is outside Baden-Württemberg")
        return client.place_param(lat, lon), {"kind": "place", "id": None, "lat": lat, "lon": lon}
    if not parse.is_station_id(text):
        raise InvalidPlace(f"{which} must be a stop id of Baden-Württemberg (from the search) or a point lat,lon")
    known = departures.stop_importance()
    if known and text not in known:
        raise InvalidPlace(f"{which}: no such stop")
    return text, {"kind": "stop", "id": text, "lat": None, "lon": None}


def in_baden_wuerttemberg(lat: float, lon: float) -> bool:
    """A point is in the state when one of its stops is near (the outline of
    the state is not a box: Strasbourg and Basel are in its box). Near the
    border that is not enough (Neu-Ulm): plan() then asks the town's key."""
    try:
        d = NEAR_A_STOP_DEGREES
        stops, total = store.in_area(lon - d, lat - d, lon + d, lat + d, 1)
        return bool(stops or total)
    except (store.StopsNotImported, sqlite3.Error, ValueError):
        south, west, north, east = BW_BOX
        return south <= lat <= north and west <= lon <= east


# ---------- following a connection: the live state of its rides ----------
LIVE_RIDES_MAX = 8


def live_rides(rides: list) -> list:
    """[(board_stop, departure_id), ...] -> one answer per ride, in order:
    {stop, id, ok: True, trip} with the ride's live times (its stops ahead
    with their live arrivals, cancelled or not), or {stop, id, ok: False,
    error: {code, message}}. Asked side by side; one ride's trouble does not
    spoil the others'."""
    def one(ride):
        stop, departure = ride
        try:
            return {"stop": stop, "id": departure, "ok": True, "trip": departures.trip(stop, departure, fresh=True)}
        except departures.UnknownDeparture:
            return {"stop": stop, "id": departure, "ok": False,
                    "error": {"code": "unknown_departure", "message": "This departure is no longer known."}}
        except client.EfaError:
            return {"stop": stop, "id": departure, "ok": False,
                    "error": {"code": "timetable_unavailable", "message": "The timetable service is not responding."}}
    return list(_pool.map(one, rides))


def town_in_bw(data: dict, end: str) -> bool | None:
    """Is the town the planner found at a journey's "origin" / "destination"
    in Baden-Württemberg (by its municipality key)? None: no answer says."""
    for raw in data.get("journeys") or []:
        legs = raw.get("legs") if isinstance(raw, dict) else None
        leg = (legs[0] if end == "origin" else legs[-1]) if isinstance(legs, list) and legs else None
        location = leg.get(end) if isinstance(leg, dict) else None
        parent = location.get("parent") if isinstance(location, dict) else None
        if isinstance(parent, dict):
            return parse.municipality_in_bw(parent.get("id"))
    return None


def ride_events(raw: dict) -> list:
    return [parse.leg_as_event(leg) for leg in raw.get("legs") or [] if isinstance(leg, dict) and not parse.is_walk(leg)]


def read_journeys(data: dict, importance: dict, dticket: bool) -> list:
    """The connections of one planner answer, read with the boards' rules
    (no flights or on-demand services; with `dticket` only valid ones)."""
    journeys, seen = [], set()
    for raw in data.get("journeys") or []:
        if not isinstance(raw, dict):
            continue
        try:
            if any(parse.is_excluded(event) for event in ride_events(raw)):
                continue   # flights and on-demand services are not offered
            journey = parse.parse_journey(raw, importance)
        except Exception:  # noqa: BLE001 -- one unreadable connection must not take the others down
            log.warning("Skipping a journey that could not be read", exc_info=True)
            continue
        if journey is None or journey["id"] in seen or not journey["legs"]:
            continue
        if dticket and not journey["dticket"]:
            continue
        seen.add(journey["id"])
        # (the product classes of its rides, in order: what the twins' question leaves out; not in the answer)
        journey["_classes"] = [((event.get("transportation") or {}).get("product") or {}).get("class")
                               for event in ride_events(raw)
                               if ((event.get("transportation") or {}).get("product") or {}).get("class") != parse.STAY_SEATED_CLASS]
        for leg in journey["legs"]:
            if leg["kind"] == "ride" and leg["mode"] in departures.TRAIN_MODES:
                leg["mode"] = departures.TRAINS     # as on the boards: one kind "Trains"
        journeys.append(journey)
    return journeys


def first_ride_leaves(journey: dict) -> str:
    """When the first bus or train of a connection leaves (live when known);
    "9999" (never gone) when it has none: a walk alone can always be started."""
    ride = next((leg for leg in journey["legs"] if leg["kind"] == "ride"), None)
    if not ride:
        return "9999"
    return ride["from"].get("time") or ride["from"].get("planned") or ""


def longest_walk(journey: dict) -> int:
    return max((leg.get("minutes") or 0 for leg in journey["legs"] if leg["kind"] == "walk"), default=0)


def worth_adding(alternative: dict, journeys: list) -> bool:
    """Is a connection without buses worth showing next to the first answer's?
    Not when one of those leaves no earlier and arrives much sooner, nor when it
    walks much more than any of them (see RAIL_BEATEN_MINUTES)."""
    dep, arr = alternative["departure"]["time"] or "", alternative["arrival"]["time"] or ""
    if not dep or not arr:
        return False
    beaten = (datetime.strptime(arr, "%Y-%m-%dT%H:%M:%SZ") - timedelta(minutes=RAIL_BEATEN_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if any((j["departure"]["time"] or "") >= dep and (j["arrival"]["time"] or "9999") <= beaten for j in journeys):
        return False
    walk = longest_walk(alternative)
    most = max((longest_walk(j) for j in journeys), default=0)
    return not (walk >= RAIL_WALK_MINUTES and walk >= most + RAIL_WALK_MORE)


WALK_ONLY_NEAR_MINUTES = 15


def needless_walk(journey: dict, journeys: list) -> bool:
    """A walk alone, when a ride leaving about as early arrives sooner (Charlottenplatz
    -> Hauptbahnhof: 17 minutes on foot next to a U-Bahn of two minutes; DB Navigator
    does not offer the walk then)."""
    if any(leg["kind"] == "ride" for leg in journey["legs"]) or not journey["departure"]["time"]:
        return False
    at = lambda text: datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    leaves, arrives = at(journey["departure"]["time"]), journey["arrival"]["time"] or "9999"
    return any(j is not journey and any(leg["kind"] == "ride" for leg in j["legs"]) and j["departure"]["time"]
               and abs((at(j["departure"]["time"]) - leaves).total_seconds()) <= WALK_ONLY_NEAR_MINUTES * 60
               and (j["arrival"]["time"] or "9999") < arrives for j in journeys)


def in_time(journeys: list, start: datetime | None, arrive: bool) -> list:
    """The connections that fit the time asked for."""
    if arrive and start:
        # the planner also offers some that arrive later than asked
        latest = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        in_time_ = [j for j in journeys if (j["arrival"]["planned"] or "") <= latest]
        return in_time_ or journeys
    if arrive:
        return journeys
    # ... and some that start well before the time asked for, or whose first
    # ride has already left
    now = datetime.now(timezone.utc)
    asked = start or now
    earliest = (asked - timedelta(minutes=EARLIER_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # (a time chosen in the past is looked at as if it were now)
    gone = (min(asked, now) - timedelta(minutes=GONE_GRACE_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [j for j in journeys if (j["departure"]["time"] or "") >= earliest and first_ride_leaves(j) >= gone]


def twins(asked_from: str, asked_to: str, start: datetime | None, dticket: bool, importance: dict,
          journeys: list, known: set) -> list:
    """Connections as fast as the fastest one, by another way after its first
    change (see TWINS_MAX); [] when there is nothing to ask or no answer in time."""
    fastest = min(journeys, key=lambda j: j["arrival"]["time"] or "9999")
    classes = fastest.get("_classes") or []
    if not classes or not fastest["arrival"]["time"] or not fastest["departure"]["time"]:
        return []
    if len(classes) == 1:
        left_out = classes[0]          # no change: another kind of transport for the same trip
    elif classes[1] is not None and classes[1] != classes[0]:
        left_out = classes[1]          # the kind taken after the first change
    else:
        return []
    allowed = [c for c in (DTICKET_CLASSES if dticket else ALL_CLASSES) if c != left_out]
    if not allowed or left_out is None:
        return []
    try:
        data = _pool.submit(client.get_journeys, asked_from, asked_to, start, False, allowed, RAIL_JOURNEYS).result(timeout=TWIN_TIMEOUT)
    except Exception:  # noqa: BLE001 -- no answer, or too late: the list goes without
        log.info("No twins for %s -> %s", asked_from, asked_to)
        return []
    at = lambda text: datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    arrives, leaves = at(fastest["arrival"]["time"]), at(fastest["departure"]["time"])

    def taken(j):
        if not j["arrival"]["time"] or not j["departure"]["time"]:
            return False
        later = (at(j["arrival"]["time"]) - arrives).total_seconds() / 60
        apart = abs((at(j["departure"]["time"]) - leaves).total_seconds()) / 60
        slower = min(TWIN_SLOWER_MINUTES, TWIN_SLOWER_SHARE * (arrives - leaves).total_seconds() / 60)
        return later <= TWIN_LATER_MINUTES or (apart <= TWIN_DEPARTURE_MINUTES and later < slower)

    found = [j for j in in_time(read_journeys(data, importance, dticket), start, False)
             if j["id"] not in known and any(leg["kind"] == "ride" for leg in j["legs"]) and taken(j)]
    return sorted(found, key=lambda j: j["arrival"]["time"] or "")[:TWINS_MAX]


def plan(origin: str, destination: str, start: datetime | None = None, arrive: bool = False, dticket: bool = False) -> dict:
    """{from, to, when, arrive, dticket, journeys: [...], empty}. `start` is
    a UTC moment (None: now): the departure, or with `arrive` the latest
    arrival. `dticket`: only connections valid with the Deutschlandticket."""
    asked_from, start_place = place(origin, "from")
    asked_to, end_place = place(destination, "to")
    rail_classes = [c for c in WITHOUT_BUSES if not dticket or c in DTICKET_CLASSES]
    rail_asked = _pool.submit(client.get_journeys, asked_from, asked_to, start, arrive, rail_classes, RAIL_JOURNEYS)
    data = client.get_journeys(asked_from, asked_to, start, arrive, DTICKET_CLASSES if dticket else (),
                               JOURNEYS_DTICKET if dticket else JOURNEYS)
    try:
        rail_data = rail_asked.result()
    except client.EfaError:
        rail_data = {}   # (the alternatives are a bonus: the first answer is shown alone)
    # a point near the state's border can still be across it (Neu-Ulm): the
    # town the planner found there says which state
    for which, asked, end in (("from", start_place, "origin"), ("to", end_place, "destination")):
        if asked["kind"] == "place" and town_in_bw(data, end) is False:
            raise InvalidPlace(f"{which} is outside Baden-Württemberg")
    importance = departures.stop_importance()
    journeys = read_journeys(data, importance, dticket)
    known = {j["id"] for j in journeys}
    latest = max((j["arrival"]["time"] or "" for j in journeys), default="")
    limit = (datetime.strptime(latest, "%Y-%m-%dT%H:%M:%SZ") + timedelta(minutes=RAIL_LATER_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ") if latest else ""
    # (a ride at least: a long walk is no alternative; not much later than the others)
    extra = [j for j in read_journeys(rail_data, importance, dticket) if j["id"] not in known
             and any(leg["kind"] == "ride" for leg in j["legs"]) and (not limit or (j["arrival"]["time"] or "") <= limit)
             and worth_adding(j, journeys)]
    # the ones arriving soonest; the list is put in order below
    journeys += sorted(extra, key=lambda j: j["arrival"]["time"] or "")[:RAIL_ALTERNATIVES]
    journeys = in_time(journeys, start, arrive)
    journeys = [j for j in journeys if not needless_walk(j, journeys)]
    if journeys and not arrive:
        known = {j["id"] for j in journeys}
        journeys += twins(asked_from, asked_to, start, dticket, importance, journeys, known)
    for journey in journeys:
        journey.pop("_classes", None)
    journeys.sort(key=lambda j: (j["departure"]["time"] or "", j["arrival"]["time"] or ""))
    for journey in journeys:
        for leg in journey["legs"]:
            if leg["kind"] == "ride":
                leg["mode"] = {"id": parse.slug(leg["mode"]), "name": leg["mode"]}

    empty = None
    if not journeys:
        empty = parse.journey_error(data) or (EMPTY_NO_TIMETABLE if departures.outside_timetable(start) else EMPTY_NONE)
    if journeys:
        first, last = journeys[0]["legs"][0]["from"], journeys[0]["legs"][-1]["to"]
        start_place.update({"name": first["name"], "lat": start_place["lat"] or first["lat"], "lon": start_place["lon"] or first["lon"]})
        end_place.update({"name": last["name"], "lat": end_place["lat"] or last["lat"], "lon": end_place["lon"] or last["lon"]})
    else:
        start_place["name"] = end_place["name"] = None
    return {
        "from": start_place,
        "to": end_place,
        "when": departures.when_text(start),
        "arrive": bool(arrive and start),
        "dticket": bool(dticket),
        "journeys": journeys,
        "empty": empty,
    }
