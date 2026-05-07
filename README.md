# City Explorer

A small FastAPI backend plus two iOS Shortcuts that turn natural-language
requests like *"show me the best tourist attractions and stop somewhere
cheap for dinner"* into a multi-stop walking route opened in Google Maps.

The user never leaves their phone:

1. Tap the **Plan City Tour** Shortcut (or "Hey Siri, plan my tour").
2. Type or dictate what you want.
3. The backend uses an LLM to parse intent, OpenStreetMap + Foursquare to
   find candidate places, OSRM to estimate travel times, and a tiny
   nearest-neighbor + 2-opt solver to order the stops.
4. The Shortcut opens a `https://www.google.com/maps/dir/?api=1&...`
   universal link, which launches Google Maps with all the waypoints
   already filled in.
5. **Refine City Tour** lets you say *"swap the museum for a Thai lunch"*
   and re-run the planner with the previous itinerary as context.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # then fill in OPENAI_API_KEY and FOURSQUARE_API_KEY
uvicorn app.main:app --reload
```

Hit it once to confirm:

```bash
curl -X POST http://localhost:8000/plan \
  -H 'Content-Type: application/json' \
  -d '{"query":"top attractions plus a thai lunch","lat":48.8566,"lng":2.3522}'
```

## Endpoints

| Method | Path      | Body                                                      |
| ------ | --------- | --------------------------------------------------------- |
| POST   | `/plan`   | `{ query, city? (or lat+lng), radius_m?, mode? }`         |
| POST   | `/refine` | `{ session_id, instruction }`                             |
| GET    | `/health` | —                                                         |

Responses include `gmaps_url` (and `gmaps_urls` for >9 stops) plus a
human-readable `summary` to show in the Shortcut alert.

## iOS Shortcuts

Step-by-step recreate guides live in [`shortcuts/`](shortcuts/):

- [`shortcuts/Plan-City-Tour.md`](shortcuts/Plan-City-Tour.md)
- [`shortcuts/Refine-City-Tour.md`](shortcuts/Refine-City-Tour.md)

## Web Interface (shareable)

A shareable web MVP lives in [`web/`](web/). It calls this backend's
`/plan` and `/refine` endpoints and displays `itinerary_text`, stops, and
Google Maps launch links.

```bash
cd web
npm install
cp .env.example .env.local
npm run dev
```

## Tests

```bash
pytest
```

## Deploy

A `Dockerfile`, `render.yaml`, and `fly.toml` live at the repo root for
one-command deploys to either Render or Fly.io. See
[`deploy/README.md`](deploy/README.md).
