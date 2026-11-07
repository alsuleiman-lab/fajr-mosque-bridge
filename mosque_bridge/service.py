"""Small HTTP service and refresh worker for known public mosque pages."""

import datetime as dt
import json
import logging
import math
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bridge import MosqueIndex, fetch_public_page, parse_public_page


LOG = logging.getLogger(__name__)


class BridgeService:
    def __init__(self, seed_urls, cache_path, fetcher=fetch_public_page):
        self.seed_urls = tuple(seed_urls)
        self.cache_path = Path(cache_path)
        self.fetcher = fetcher
        self.index = MosqueIndex()
        if self.cache_path.exists():
            try:
                saved = json.loads(self.cache_path.read_text(encoding="utf-8"))
                for record in saved:
                    if record.get("sourceUrl") in self.seed_urls and record.get("source") == "Mawaqit":
                        self.index.put(record)
            except (OSError, ValueError, TypeError):
                LOG.warning("Ignored invalid mosque cache")

    def refresh(self, day=None):
        day = day or dt.date.today()
        successes = 0
        for url in self.seed_urls:
            try:
                html, resolved_url = self.fetcher(url)
                record = parse_public_page(html, resolved_url, day)
                record["sourceUrl"] = url
                self.index.put(record)
                successes += 1
            except (OSError, ValueError, KeyError, TypeError) as exc:
                LOG.warning("Mosque refresh failed for %s: %s", url, exc)
        if successes:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.index.dump(), ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.cache_path)
        return successes


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=300" if status == 200 else "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.removeprefix("/fajr-api")
            query = urllib.parse.parse_qs(parsed.query)
            try:
                date_text = query.get("date", [None])[0]
                day = dt.date.fromisoformat(date_text) if date_text else None
                if path == "/health":
                    indexed = service.index.usable_count()
                    return self._reply(200 if indexed else 503, {"status": "ok" if indexed else "warming", "indexed": indexed})
                if path == "/v1/mosques/nearby":
                    lat = float(query["lat"][0])
                    lng = float(query["lng"][0])
                    radius = int(query.get("radius", ["10000"])[0])
                    limit = int(query.get("limit", ["20"])[0])
                    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180
                            and 1 <= radius <= 50000 and 1 <= limit <= 20):
                        raise ValueError("Invalid nearby query")
                    return self._reply(200, {"data": service.index.nearby(lat, lng, radius, day, limit)})
                if path == "/v1/mosques":
                    term = query.get("search", [""])[0][:100]
                    lat = lng = None
                    radius = 50000
                    if "lat" in query or "lng" in query:
                        lat = float(query["lat"][0])
                        lng = float(query["lng"][0])
                        radius = int(query.get("radius", ["50000"])[0])
                        if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90
                                and -180 <= lng <= 180 and 1 <= radius <= 50000):
                            raise ValueError("Invalid search location")
                    limit = int(query.get("limit", ["20"])[0])
                    if not 1 <= limit <= 20:
                        raise ValueError("Invalid search limit")
                    return self._reply(200, {"data": service.index.search(term, day, limit, lat, lng, radius)})
                if path.startswith("/v1/mosques/"):
                    source_id = path.rsplit("/", 1)[-1]
                    if not source_id.isdecimal():
                        raise ValueError("Invalid mosque ID")
                    record = service.index.detail(source_id, day)
                    return self._reply(200 if record else 404, {"data": record} if record else {"error": "No valid schedule for this date"})
                return self._reply(404, {"error": "Not found"})
            except (ValueError, KeyError, IndexError, OverflowError):
                return self._reply(400, {"error": "Invalid request"})

        def log_message(self, fmt, *args):
            LOG.info("HTTP %s", self.path.split("?", 1)[0])

    return Handler


def main():
    logging.basicConfig(level=logging.INFO)
    folder = Path(__file__).resolve().parent
    sources = json.loads((folder / "sources.json").read_text(encoding="utf-8"))
    cache = Path(os.environ.get("MOSQUE_CACHE_FILE", str(folder / "cache.json")))
    service = BridgeService(sources, cache)

    def update_loop():
        while True:
            LOG.info("Refreshed %s mosque pages", service.refresh())
            time.sleep(6 * 60 * 60)

    threading.Thread(target=update_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8766"))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(service))
    server.serve_forever()


if __name__ == "__main__":
    main()
