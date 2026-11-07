# Fajr mosque timetable bridge

This is a small, read-only service for the Fajr app. It refreshes a fixed list of public Mawaqit mosque pages every six hours and serves date-specific prayer schedules from their calendar. No user downloads or accounts are required. The initial list covers five Dortmund mosques. Add verified public page URLs to `sources.json` to expand coverage.

Run locally from the project root:

```sh
python -m mosque_bridge.service
```

Routes:

- `GET /health`
- `GET /v1/mosques/nearby?lat=51.57&lng=7.39&radius=10000`
- `GET /v1/mosques?search=Dortmund`
- `GET /v1/mosques/18765`

The same routes accept a `/fajr-api` prefix for path-based proxy hosting. The app build can select this backend with `--dart-define=MOSQUE_API_BASE=https://din.hk/fajr-api/v1` once that URL is live. The default app build continues to use its existing source until this service is deployed and the rollout is approved.

The server fetches only explicitly configured HTTPS pages on `mawaqit.net`, refuses cross-host redirects, and limits body size and request time. Missing, invalid, or year-mismatched calendars are not used as alarm times. If refresh fails, cached records can only serve a day explicitly present in the current source year. A read-only page URL is not a public API guarantee. Mawaqit describes its API as private: https://help.mawaqit.net/en/articles/11991838-can-i-use-your-api

Deployment: build with repository root as Docker context and `mosque_bridge/Dockerfile` as Dockerfile; expose port 8766 and use `/health` for health checks. Mount `/data` persistently so validated cache survives redeployments. Do not publish the app build pointing here until live HTTP and timezone/date behavior are verified.
