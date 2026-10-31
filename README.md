# CGC Terminal — Canola & Wheat

Weekly tracker built on the Canadian Grain Commission **Grain Statistics Weekly** (GSW) CSVs.
Static GitHub Pages site + GitHub Actions cron (canada.ca blocks browser CORS, so data is fetched server-side and committed).

## Tracks (Canola · Wheat ex-durum · Durum · All Wheat)
- Producer deliveries (weekly, CYTD, by province)
- Domestic use (process elevator milled/mfg grain — canola crush)
- Exports (terminal by port + direct from primaries)
- Commercial stocks (primary / process / terminal)
- Current vs last year vs prior 5-yr avg + min/max band, aligned by grain week

## Setup
1. Create a new GitHub repo, push these files.
2. Settings → Pages → Deploy from branch → `main` / root.
3. Actions tab → **Update CGC data** → *Run workflow* (first run builds `data/cgc.json`).
4. After that it runs Thursday 22:30 UTC and Friday 14:30 UTC.

## Files
- `scripts/fetch_cgc.py` — downloads current + 5 prior crop-year CSVs, builds `data/cgc.json` (stdlib only)
- `.github/workflows/update.yml` — cron + commit
- `index.html` — the terminal (Chart.js)

Local test: `python scripts/fetch_cgc.py && python -m http.server` → http://localhost:8000
