"""Read only explicitly listed public Mawaqit pages and expose dated schedules."""

import datetime as dt
import json
import math
import re
import threading
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


_CONF = re.compile(r"\b(?:var|let|const)\s+confData\s*=\s*")
_UPDATED = re.compile(r"\b(?:var|let|const)\s+lastUpdated\s*=\s*(\d+)")
_ID = re.compile(r'/id/(\d+)/has-been-updated')
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_KEYS = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")


def _clock(value):
    if not isinstance(value, str) or not _CLOCK.fullmatch(value):
        raise ValueError("Invalid mosque clock")
    return value


def _schedule(record, day):
    if day.year != record["_calendarYear"]:
        return None
    try:
        row = record["_calendar"][day.month - 1][str(day.day)]
        if not isinstance(row, list) or len(row) != 6:
            return None
        timings = dict(zip(_KEYS, map(_clock, row)))
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    values = [int(t[:2]) * 60 + int(t[3:]) for t in timings.values()]
    if values != sorted(values) or len(set(values)) != len(values):
        return None
    friday = record["_friday"]
    if record["_jumuaAsDuhr"] and not friday:
        friday = [timings["dhuhr"]]
    if friday:
        timings["jummah"] = friday
    return {**{k: v for k, v in record.items() if not k.startswith("_")},
            "scheduleDate": day.isoformat(), "effectiveTimings": timings}


def parse_public_page(html, url, day):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "mawaqit.net":
        raise ValueError("Only public mawaqit.net HTTPS pages are allowed")
    if not isinstance(html, str) or len(html) > 1_000_000:
        raise ValueError("Page size is invalid")
    match = _CONF.search(html)
    updated = _UPDATED.search(html)
    source_id = _ID.search(html)
    if not (match and updated and source_id):
        raise ValueError("Missing public mosque data")
    conf, _ = json.JSONDecoder().raw_decode(html[match.end():])
    if not isinstance(conf, dict):
        raise ValueError("Invalid mosque configuration")
    tz = ZoneInfo(conf["timezone"])
    year = dt.datetime.fromtimestamp(int(updated[1]), tz).year
    lat, lng = float(conf["latitude"]), float(conf["longitude"])
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Invalid mosque coordinates")
    name = str(conf["name"]).strip()
    if not name or len(name) > 240:
        raise ValueError("Invalid mosque name")
    calendar = conf["calendar"]
    if not isinstance(calendar, list) or len(calendar) != 12:
        raise ValueError("Invalid mosque calendar")
    friday = []
    for key in ("jumua", "jumua2", "jumua3"):
        if conf.get(key):
            value = _clock(conf[key])
            if value not in friday:
                friday.append(value)
    record = {
        "id": source_id[1], "name": name, "originalName": name,
        "latitude": lat, "longitude": lng,
        "city": name.rsplit(" - ", 1)[-1] if " - " in name else "",
        "country": str(conf.get("countryCode") or ""),
        "timezone": conf["timezone"], "source": "Mawaqit",
        "sourceUrl": url,
        "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "_calendarYear": year, "_calendar": calendar,
        "_friday": friday, "_jumuaAsDuhr": conf.get("jumuaAsDuhr") is True,
    }
    scheduled = _schedule(record, day)
    if scheduled is None:
        raise ValueError("No valid timetable for requested date")
    return {**record, "scheduleDate": scheduled["scheduleDate"],
            "effectiveTimings": scheduled["effectiveTimings"]}


def distance_meters(lat, lng, other_lat, other_lng):
    a = math.sin(math.radians(other_lat - lat) / 2) ** 2
    a += math.cos(math.radians(lat)) * math.cos(math.radians(other_lat)) * math.sin(math.radians(other_lng - lng) / 2) ** 2
    return round(6371000 * 2 * math.asin(min(1, math.sqrt(a))))


class MosqueIndex:
    def __init__(self):
        self._items = {}
        self._lock = threading.RLock()

    def put(self, record):
        with self._lock:
            self._items[record["id"]] = record

    def dump(self):
        with self._lock:
            return list(self._items.values())

    def usable_count(self):
        with self._lock:
            records = list(self._items.values())
        usable = 0
        for record in records:
            try:
                today = dt.datetime.now(ZoneInfo(record["timezone"])).date()
                usable += _schedule(record, today) is not None
            except (KeyError, TypeError, ValueError):
                continue
        return usable

    def detail(self, source_id, day):
        with self._lock:
            record = self._items.get(source_id)
            if record is None:
                return None
            date = day or dt.datetime.now(ZoneInfo(record["timezone"])).date()
            return _schedule(record, date)

    def search(self, query, day=None, limit=20, lat=None, lng=None, radius=50000):
        needle = query.strip().casefold()
        if len(needle) < 2:
            return []
        with self._lock:
            records = list(self._items.values())
        results = []
        for record in records:
            if needle in record["name"].casefold() or needle in record["city"].casefold():
                date = day or dt.datetime.now(ZoneInfo(record["timezone"])).date()
                current = _schedule(record, date)
                if current:
                    if lat is not None and lng is not None:
                        distance = distance_meters(lat, lng, record["latitude"], record["longitude"])
                        if distance > radius:
                            continue
                        current = {**current, "distanceMeters": distance}
                    results.append(current)
        return results[:limit]

    def nearby(self, lat, lng, radius, day=None, limit=20):
        with self._lock:
            records = list(self._items.values())
        found = []
        for record in records:
            distance = distance_meters(lat, lng, record["latitude"], record["longitude"])
            if distance <= radius:
                date = day or dt.datetime.now(ZoneInfo(record["timezone"])).date()
                current = _schedule(record, date)
                if current:
                    found.append({**current, "distanceMeters": distance})
        return sorted(found, key=lambda item: item["distanceMeters"])[:limit]


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.hostname != "mawaqit.net":
            raise ValueError("Cross-host redirect refused")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_public_page(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "mawaqit.net":
        raise ValueError("Only public mawaqit.net HTTPS pages are allowed")
    request = urllib.request.Request(url, headers={"User-Agent": "FajrPrayerApp/1.0 (contact@mshmsdin.com)"})
    with urllib.request.build_opener(_SameHostRedirect()).open(request, timeout=12) as response:
        payload = response.read(1_000_001)
        if len(payload) > 1_000_000:
            raise ValueError("Public page exceeds size limit")
        return payload.decode("utf-8"), response.url
