# SmartServe AI – Vercel deploy

smartserve/
├── api/index.py      FastAPI backend (Vercel Python serverless function)
├── public/index.html Frontend (static, calls /api/*)
├── requirements.txt
└── vercel.json

## Deploy
1. Push this folder to a GitHub repo.
2. vercel.com → Add New → Project → import the repo → Deploy (no build settings needed).
3. Optional: Project Settings → Environment Variables → ANTHROPIC_API_KEY for real expiry OCR.

Or with the CLI: `npm i -g vercel && vercel --prod`

## Local test
pip install -r requirements.txt uvicorn
uvicorn api.index:app --reload   # API on :8000
(open public/index.html through a local server that proxies /api, or just use `vercel dev`)

## Notes
- Data is in-memory and seeded (demo mode). Vercel functions are stateless/read-only, so SQLite will NOT persist.
  For persistence use Neon / Supabase Postgres via Vercel Marketplace.
- Camera needs HTTPS: Vercel provides it.
