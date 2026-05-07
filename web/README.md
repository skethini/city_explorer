# City Explorer Web UI

Quick MVP interface for testing and sharing your planner with others.

## Run locally

```bash
cd web
npm install
cp .env.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Deploy to Vercel

1. Push repo to GitHub.
2. In Vercel, import the repo.
3. Set **Root Directory** to `web`.
4. Add env var:
   - `NEXT_PUBLIC_API_BASE=https://city-explorer-t9oj.onrender.com`
5. Deploy and share the Vercel URL.
