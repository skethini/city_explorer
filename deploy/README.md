# Deploying City Explorer

The backend is a single stateless container with two pieces of secret
state: `OPENAI_API_KEY` and `FOURSQUARE_API_KEY`. Either of the two recipes
below works; the entire repo deploys in a few minutes on the free tiers.

## Local Docker

```bash
docker build -t city-explorer .
docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  -e FOURSQUARE_API_KEY=fsq3... \
  city-explorer
curl http://localhost:8000/health
```

## Render (one-click via `render.yaml`)

1. Push this repo to GitHub.
2. In the Render dashboard pick **New > Blueprint**, point it at the repo.
   It will detect [`render.yaml`](../render.yaml) and create the service.
3. Set the two secret env vars (`OPENAI_API_KEY`, `FOURSQUARE_API_KEY`)
   when prompted. Render keeps them out of git because they have
   `sync: false`.
4. Wait ~3 minutes for the first build. Test it:

   ```bash
   curl https://<your-service>.onrender.com/health
   ```

5. Paste that base URL into both Shortcuts (replace every `BACKEND` from
   [`shortcuts/`](../shortcuts/)).

## Fly.io

```bash
fly launch --no-deploy --copy-config           # uses fly.toml in this repo
fly secrets set OPENAI_API_KEY=sk-... FOURSQUARE_API_KEY=fsq3...
fly deploy
fly status
```

The `[http_service]` config auto-stops the machine when idle so the free
allowance lasts indefinitely for personal use.

## Production checklist

- [ ] Replace the in-memory `SessionStore` with Redis (or SQLite on a
      persistent volume) before more than one machine is running.
- [ ] Self-host OSRM if your usage starts hitting the public demo's rate
      limit; the public endpoint at `router.project-osrm.org` is fine for
      personal use but not advertised for production.
- [ ] Stand up monitoring on `/health` and alert if the LLM or Foursquare
      keys expire.
- [ ] Lock the API down with a shared-secret header that the iOS Shortcut
      sends (`Authorization: Bearer ...`). Add the check in
      [`app/main.py`](../app/main.py).
