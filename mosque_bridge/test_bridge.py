import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

from mosque_bridge.bridge import parse_public_page, MosqueIndex
from mosque_bridge.service import BridgeService, make_handler


def page(*, updated=1792927069, broken=False):
    day = ["05:34", "07:22", "12:19", "14:36", "17:06", "18:48"]
    if broken:
        day[4] = "31:99"
    calendar = [{} for _ in range(12)]
    calendar[10] = {"3": day}
    data = {
        "name": "مسجد أبو بكر Moschee Abu Bakr - DORTMUND",
        "latitude": 51.5293374,
        "longitude": 7.4634373,
        "timezone": "Europe/Berlin",
        "countryCode": "DE",
        "times": ["05:20", "12:20", "14:49", "17:23", "19:03"],
        "shuruq": "07:07",
        "calendar": calendar,
        "jumua": "13:00",
        "jumua2": "14:00",
        "jumua3": None,
    }
    return (
        f'<script>var lastUpdated = {updated}; var confData = '
        + json.dumps(data, ensure_ascii=False)
        + ';</script><div data-remote="/id/18765/has-been-updated"></div>'
    )


class PublicPageTests(unittest.TestCase):
    def test_bochum_public_mosques_are_in_the_configured_index(self):
        sources = json.loads(
            (Path(__file__).with_name("sources.json")).read_text(encoding="utf-8")
        )
        expected = {
            "https://mawaqit.net/en/khaled-bochum",
            "https://mawaqit.net/de/msjd-tawba",
            "https://mawaqit.net/ml/masjid-riyad-alsalihin-bochum-44801-germany-1",
            "https://mawaqit.net/tr/igmg-bochum-fatih-camii-bochum-44787-germany",
        }
        self.assertTrue(expected.issubset(set(sources)))

    def test_uses_dated_calendar_not_snapshot(self):
        mosque = parse_public_page(page(), "https://mawaqit.net/en/test", dt.date(2026, 11, 3))
        self.assertEqual(mosque["effectiveTimings"]["fajr"], "05:34")
        self.assertEqual(mosque["effectiveTimings"]["sunrise"], "07:22")
        self.assertEqual(mosque["effectiveTimings"]["maghrib"], "17:06")
        self.assertEqual(mosque["effectiveTimings"]["jummah"], ["13:00", "14:00"])
        self.assertEqual(mosque["scheduleDate"], "2026-11-03")
        self.assertEqual(mosque["source"], "Mawaqit")

    def test_rejects_invalid_clock_and_wrong_year(self):
        with self.assertRaises(ValueError):
            parse_public_page(page(broken=True), "https://mawaqit.net/en/test", dt.date(2026, 11, 3))
        with self.assertRaises(ValueError):
            parse_public_page(page(), "https://mawaqit.net/en/test", dt.date(2027, 11, 3))

    def test_public_page_accepts_let_declarations(self):
        current = page().replace("var lastUpdated", "let lastUpdated").replace("var confData", "let confData")
        self.assertEqual(parse_public_page(current, "https://mawaqit.net/en/test", dt.date(2026, 11, 3))["id"], "18765")

    def test_search_only_returns_indexed_mosques_and_cached_day(self):
        index = MosqueIndex()
        parsed = parse_public_page(page(), "https://mawaqit.net/en/test", dt.date(2026, 11, 3))
        index.put(parsed)
        self.assertEqual(index.search("أبو بكر", dt.date(2026, 11, 3))[0]["id"], "18765")
        self.assertEqual(index.nearby(51.53, 7.46, 10000, dt.date(2026, 11, 3))[0]["id"], "18765")
        self.assertEqual(index.detail("18765", dt.date(2026, 11, 3))["effectiveTimings"]["asr"], "14:36")
        self.assertIsNone(index.detail("18765", dt.date(2026, 11, 4)))

    def test_refresh_failure_keeps_only_valid_dated_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "cache.json"
            url = "https://mawaqit.net/en/test"
            def good(_):
                return page(), url
            service = BridgeService([url], cache, fetcher=good)
            self.assertEqual(service.refresh(dt.date(2026, 11, 3)), 1)
            service.fetcher = lambda _: (_ for _ in ()).throw(OSError("offline"))
            self.assertEqual(service.refresh(dt.date(2026, 11, 3)), 0)
            restored = BridgeService([url], cache, fetcher=service.fetcher)
            self.assertEqual(restored.index.detail("18765", dt.date(2026, 11, 3))["effectiveTimings"]["isha"], "18:48")
            self.assertIsNone(restored.index.detail("18765", dt.date(2026, 11, 4)))

    def test_http_nearby_search_and_detail_use_same_calendar(self):
        with tempfile.TemporaryDirectory() as folder:
            url = "https://mawaqit.net/en/test"
            service = BridgeService([url], Path(folder) / "cache.json", fetcher=lambda _: (page(), url))
            service.refresh(dt.date(2026, 11, 3))
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                root = f"http://127.0.0.1:{server.server_port}/v1"
                if dt.date.today() != dt.date(2026, 11, 3):
                    with self.assertRaises(urllib.error.HTTPError) as failed_health:
                        urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health")
                    self.assertEqual(failed_health.exception.code, 503)
                with urllib.request.urlopen(root + "/mosques/nearby?lat=51.53&lng=7.46&date=2026-11-03") as response:
                    nearby = json.load(response)["data"]
                with urllib.request.urlopen(root + "/mosques?search=%D8%A3%D8%A8%D9%88&date=2026-11-03") as response:
                    search = json.load(response)["data"]
                with urllib.request.urlopen(root + "/mosques/18765?date=2026-11-03") as response:
                    detail = json.load(response)["data"]
                self.assertEqual([row["id"] for row in nearby], ["18765"])
                self.assertEqual([row["id"] for row in search], ["18765"])
                self.assertEqual(detail["effectiveTimings"]["fajr"], "05:34")
            finally:
                server.shutdown()
                server.server_close()

    def test_http_name_search_with_city_location_hides_far_mosques(self):
        with tempfile.TemporaryDirectory() as folder:
            service = BridgeService([], Path(folder) / "cache.json")
            berlin = parse_public_page(page(), "https://mawaqit.net/de/test", dt.date(2026, 11, 3))
            berlin.update(id="25290", name="Al Noor Berlin", latitude=52.4875, longitude=13.3115)
            dortmund = dict(berlin, id="18765", name="Al Noor Dortmund", latitude=51.5293, longitude=7.4634)
            service.index.put(berlin)
            service.index.put(dortmund)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = (f"http://127.0.0.1:{server.server_port}/v1/mosques"
                       "?search=Al&lat=52.4875&lng=13.3115&radius=50000&date=2026-11-03")
                with urllib.request.urlopen(url) as response:
                    found = json.load(response)["data"]
                self.assertEqual([row["id"] for row in found], ["25290"])
                self.assertLess(found[0]["distanceMeters"], 100)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
