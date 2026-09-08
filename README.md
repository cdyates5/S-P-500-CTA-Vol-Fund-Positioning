# S&P 500 Systematic Positioning Dashboards — auto-refreshing

Two self-contained, single-file HTML dashboards that reconstruct systematic-fund equity positioning:

- **`sp500_cta_positioning.html`** — CTA / trend-following net exposure (multi-horizon momentum, `tanh` sizing, conditional 1-month flow scenarios).
- **`sp500_volcontrol_positioning.html`** — volatility-control / target-vol implied exposure (realised-vol windows, leverage cap, vol-threshold and flow scenarios).

Each file embeds its data at build time and recomputes interactively in the browser (sliders, toggles, overlays) — no live calls, fully portable. A GitHub Actions workflow **rebuilds both from fresh market data every week** and republishes them via GitHub Pages.

## How the refresh works

```
.github/workflows/refresh.yml   weekly cron (Fri 23:00 UTC, after the close) + manual trigger
        └─ python build.py
               ├─ build_cta.py   → fetch SPX (Yahoo → FRED fallback) → compute → data.json
               ├─ build_vol.py   → fetch SPX (Yahoo → FRED fallback) → compute → data_vol.json
               ├─ inject each JSON into its template_*.html  → public/*.html
               └─ write public/index.html (landing page + current readings)
        └─ upload public/ as a Pages artifact → deploy to GitHub Pages
```

Data is fetched **server-side in the runner**, so there is no browser CORS issue. Each run is a full rebuild (15y of daily SPX, recomputed from scratch), so the output is always internally consistent. If a run fails (e.g. both data sources unreachable), the previously deployed version simply stays live until the next successful run.

## One-time setup

1. **Create a repo** and push these files (keep the layout; the build script and templates expect to sit in the repo root).
2. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. (Optional) **Settings → Actions → General → Workflow permissions:** ensure "Read and write" is allowed if your org defaults are restrictive — the workflow already requests the `pages: write` / `id-token: write` permissions it needs.
4. Trigger the first build: **Actions → Refresh dashboards → Run workflow** (don't wait for Saturday).
5. The dashboards will be live at:
   - `https://<user>.github.io/<repo>/` (landing page)
   - `https://<user>.github.io/<repo>/sp500_cta_positioning.html`
   - `https://<user>.github.io/<repo>/sp500_volcontrol_positioning.html`

## Run locally

```bash
pip install -r requirements.txt
python build.py
# outputs into ./public/  (open public/index.html)
```

## Notes

- **Schedule timing:** `cron: '0 23 * * 5'` is **Friday 23:00 UTC**, ~2-3h after the US cash close. GitHub cron is UTC and ignores daylight saving, so the gap to the 16:00 ET close shifts between 3h (summer) and 2h (winter) — always post-close. For maximum certainty Friday's bar is posted you can push it to early Saturday UTC (e.g. `'0 2 * * 6'`). GitHub may also delay scheduled runs under heavy load.
- **Inactivity:** GitHub disables scheduled workflows after ~60 days with no repo activity. A manual run (or any push) re-arms them.
- **Changing assumptions:** model parameters (capacity/AUM, target vol, leverage cap, smoothing weights, capacity) live at the top of `build_cta.py` / `build_vol.py`; the in-browser controls let viewers override most of them per session without rebuilding.
- These are independent reconstructions for research/education only — not investment advice, and not affiliated with any third-party research provider.
```
