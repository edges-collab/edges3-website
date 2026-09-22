# Frontend (`frontend/`)

React + TypeScript + Vite single-page app for browsing the EDGES-3
calibration/temperature outputs. Talks to the backend over HTTP only —
it never reads files from disk directly.

## Files

| Path | Role |
|---|---|
| `src/utils/baseURL.ts` | The **only** configurable connection point. Reads `VITE_API_URL` at build time; defaults to empty (same-origin). Vite dev server proxies same-origin paths to the backend (see `vite.config.ts`). |
| `src/utils/plotComponent.tsx` | Lazy-loaded Plotly factory — wraps `plotly.js-basic-dist` (~1 MB) instead of the full `plotly.js` (~3 MB) since we only use scatter charts. |
| `src/state/RunContext.tsx` | Frontend cache of `/latest_run`. Polls after a new run is triggered. |
| `src/components/LatestRunBanner.tsx` | Top-of-page banner: source, dates, per-load probe readings, calibration parameters, save controls. |
| `src/components/MultiPlotter.tsx` | Two-line overlay plot used for the "Calibration vs Actual Temp" comparisons. |
| `src/components/SinglePlotter.tsx` | One-line plot used for spectra and TNW coefficients. |
| `src/components/S11Plotter.tsx` | S11 plot with Re/Im ↔ Mag/Phase toggle. |
| `src/components/HeatmapPlotter.tsx` | Waterfall / heatmap plot with down-sampling. |
| `src/components/ManifestPage.tsx` | Wraps all 4 plotters in `React.lazy()` + `<Suspense>` so the plot chunks (and `plotly.js-basic-dist`) only download when a page actually renders plots. Home/Select stay light. |
| `src/types/manifest.ts` | Types matching the manifest the backend emits. |

## Running locally

```bash
npm install
npm run dev          # http://localhost:5173
```

The dev server proxies API/static paths to the FastAPI backend on
`127.0.0.1:8003` (see `vite.config.ts`) — so the laptop only ever
tunnels to `localhost:5173`. On first navigation to a plot-bearing
page, Vite optimises `plotly.js-basic-dist` into `node_modules/.vite/deps/`
(2.3 MB dev chunk); this is a one-time cost per dev session.

## Building for production

When the backend lives on a different origin than the static host,
set `VITE_API_URL` at build time:

```bash
VITE_API_URL=https://edges.example.com npm run build
```

The resulting `dist/` is a fully static bundle. Upload it to any static
host (nginx, GitHub Pages, S3, …). Production chunks: a ~320 KB main
bundle, plus the 4 plotter chunks and a ~1.2 MB `plotly.js-basic-dist`
chunk that only downloads when a user visits a page with plots.

## Path configuration cheat sheet

| Build env var | Default | When to override |
|---|---|---|
| `VITE_API_URL` | *(empty — same-origin)* | Backend lives on a different origin than the static host. Set this before `npm run build`. |
| `VITE_PROXY_PORT` | `8003` | Override only if uvicorn is on a non-default port (see `vite.config.ts`). |
