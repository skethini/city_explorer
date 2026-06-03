<img width="689" height="1031" alt="Screenshot 2026-06-03 at 10 39 20 AM" src="https://github.com/user-attachments/assets/aca5eb0b-c19e-4ce7-b3b0-5b8de47570b0" />


# City Explorer

Turn a natural-language city request into an ordered walking (or driving) tour
with a Google Maps link. Example: *"best parks in Madrid from 9am to 9pm"*.

The user flow:

1. Enter a city and describe what you want (web UI, iOS Shortcut, or API).
2. **OpenAI** returns an ordered list of real venue names for that city, time
   budget, and transport mode.
3. Each name is **geocoded** (Google Geocoding when configured, else Nominatim).
4. **OSRM** estimates total travel distance and duration along the route.
5. Optional **Wikipedia + OpenAI** blurbs and images are attached to each stop.
6. The response includes a human-readable itinerary, a suggested schedule, and
   a **Google Maps directions URL** (with chunking when there are many stops).
7. **Refine** sends a follow-up instruction; OpenAI revises the full stop list
   and the pipeline runs again (session id ties refine to the prior plan).

## Architecture

| Layer | Role |
|-------|------|
| `web/` | Next.js UI — calls the API, shows stops and Maps link |
| `app/main.py` | FastAPI routes: `/plan`, `/refine`, `/city-suggestions`, `/health` |
| `app/llm.py` | OpenAI direct tour planner + refiner |
| `app/itinerary.py` | Orchestration: geocode stops, enrich, assemble itinerary |
| `app/curation.py` | Geocoding, city autocomplete, Wikipedia/LLM blurbs |
| `app/places.py` | Stop enrichment wrapper |
| `app/route.py` | OSRM metrics + itinerary assembly |
| `app/gmaps.py` | Google Maps multi-waypoint URL builder |
| `app/sessions.py` | In-memory session store for refine |

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # set OPENAI_API_KEY (required)
uvicorn app.main:app --reload
```

```bash
curl -X POST http://localhost:8000/plan \
  -H 'Content-Type: application/json' \
  -d '{"query":"top parks and a cafe","city":"Madrid","mode":"walking"}'
```

## Web UI

```bash
cd web
npm install
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE=http://localhost:8000
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## API

| Method | Path | Body |
|--------|------|------|
| POST | `/plan` | `{ query, city?, lat?, lng?, mode?, radius_m? }` |
| POST | `/refine` | `{ session_id, instruction }` |
| GET | `/city-suggestions` | `?q=...` |
| GET | `/health` | — |

Responses include `gmaps_url`, `schedule`, `itinerary`, and `session_id`.

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | Yes | Tour planning and stop blurbs |
| `OPENAI_MODEL` | No | Default `gpt-4o-mini` |
| `GOOGLE_MAPS_API_KEY` | No | Faster, more reliable geocoding |
| `OSRM_URL` | No | Default public OSRM router |

## iOS Shortcuts

Recreate guides in [`shortcuts/`](shortcuts/):

- [`shortcuts/Plan-City-Tour.md`](shortcuts/Plan-City-Tour.md)
- [`shortcuts/Refine-City-Tour.md`](shortcuts/Refine-City-Tour.md)

## Tests

```bash
pytest
```

## Deploy

Docker + Render/Fly configs at the repo root. See [`deploy/README.md`](deploy/README.md).
Host the **API** on Render or Fly; deploy **`web/`** on Vercel (or a second Render
service) with `NEXT_PUBLIC_API_BASE` pointing at the API.
