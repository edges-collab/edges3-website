# EDGES-3 Web Interface

A web UI for the EDGES-3 instrument: a nightly overview of the data,
receiver calibrations, and nights calibrated in detail. The React frontend and the FastAPI
backend are intended to be **installed and run on the SSH cluster**;
users reach the UI from a laptop by SSH-tunnelling the dev server.

```
edges3-website/
├── backend/          # FastAPI server + EDGES pipeline (Python)
├── frontend/         # React + TypeScript + Vite SPA
├── README.md         # ← you are here
├── LICENSE
└── .gitignore
```

The two halves communicate over HTTP. The frontend never reads files
from disk directly — it asks the backend for JSON (`/api/...`) and for
the `.npz` arrays of finished runs, which the backend serves from its
`OUTPUT_ROOT` static mount (`/data/...`).

There is **no daemon**, no scheduler, no systemd unit. Calibration and
observation runs happen when you click **Run** on the Calibrations or
Detailed Data View tab (they run as background jobs, one at a time).

---

## Install (one-time, on the SSH cluster)

### 1. Clone the repo

```bash
git clone https://github.com/edges-collab/edges3-website.git
cd edges3-website
```

### 2. Install the Python backend (uv)

`uv` reads `backend/requirements.txt` and creates an isolated venv with
every scientific dependency (`edges-analysis`, `edges-io`, `pygsdata`,
`read-acq`, `astropy`, `fastapi`, …). No conda environment, no system
packages.

```bash
# Install uv once (skip if it's already on PATH):
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create the venv and install deps:
uv venv .venv --python 3.13   # edges-analysis 8.4 (the pipeline's) needs >= 3.12
source .venv/bin/activate
uv pip install -r backend/requirements.txt
```

Verify the install:

```bash
python backend/config.py    # prints every resolved path + probe number
```

### 2b. Catalog and pipeline products

The site finds data through the EDGES **catalog** and the pipeline's
precomputed products, never by scanning the raw data tree: the **Last
night** page (the home page, `/`) reads quick-look (QL) and L1 QA
products, the **Calibrations** tab shows the pipeline's receiver
calibrations (stored, or computed with its code for other settings), and
the **Detailed Data View** takes its nights and input files from the
catalog. They use two packages that are
**not published yet**:
`edges-catalog` and `edges-pipeline`. They are local git repositories
on the EDGES server (ask the data maintainers for their location);
install them pinned to their committed code (not editable):

```bash
uv pip install \
  "edges-catalog @ git+file:///path/to/edges-catalog" \
  "edges-pipeline[cal] @ git+file:///path/to/edges-pipeline"
```

The `cal` extra pins the `edges-analysis` version the pipeline's receiver
calibration is written for (computing a calibration with other settings
fails without it).

Without them the Last night page, the date lists and the calibration
flow return 503 with an install hint. The products and the catalog live under
`EDGES_PIPELINE_ROOT` (default `/data6/edges/edges-db`); run the backend
as a member of the `loco` group, since the SQLite (WAL) databases need
their group-writable `-wal`/`-shm` files even for read-only access.
These endpoints never read the raw data tree.

### 3. Install the frontend

```bash
cd frontend
npm install                 # one-off
```

---

## Run the server (on the SSH cluster)

You'll need **one terminal** connected to the SSH cluster. The FastAPI
backend serves both the API and the built React SPA, so no separate
frontend dev server is needed.

### Step 1 — build the frontend (one-off per frontend change)

```bash
cd edges3-website/frontend
npm run build      # writes frontend/dist/
```

### Step 2 — start the backend

```bash
cd edges3-website
source .venv/bin/activate
cd backend
EDGES_PYTHON=$(which python) \
  python -m uvicorn backend_api:app --host 127.0.0.1 --port 8003
```

`EDGES_PYTHON=$(which python)` ensures the pipeline subprocess uses the
venv's interpreter (it is already `sys.executable`, but this keeps the
behaviour explicit and independent of PATH when launched from a wrapper
script).

The server serves:

* `/`             — the React SPA (`frontend/dist/`)
* `/data/...`     — `OUTPUT_ROOT` (`calibration/<id>/...`, `observation/<id>/...`)
* `/api/...`      — JSON API endpoints (see `backend/backend_api.py`)

Defaults in `backend/config.py` point at the cluster paths
(`/data5/edges/data/EDGES3_data/MRO` for raw data, `<repo>/outputs`
for outputs). Override any of them with the env vars documented below.

### Keeping the server running (tmux)

The steps above tie the server to your terminal: closing the SSH
connection kills it. To keep the backend running after you log out —
and to avoid needing a terminal open on your laptop at all — run it
inside a detached `tmux` session:

```bash
# On the cluster:
tmux new -s edges
cd edges3-website
source .venv/bin/activate
cd backend
EDGES_PYTHON=$(which python) \
  python -m uvicorn backend_api:app --host 127.0.0.1 --port 8003
```

Detach with `Ctrl-b` then `d`. The server keeps running in the
background; you can close the SSH connection and your terminal. The
server must be (re)started this way after the cluster reboots.

Reattach to inspect logs, or stop the server:

```bash
tmux attach -t edges       # view live output; Ctrl-b d to detach again
tmux kill-session -t edges # stop the server
```

### View the UI from your laptop

The backend listens on `localhost` only. SSH-tunnel it to your laptop:

```bash
# From your laptop (NOT the cluster):
ssh -L 8880:localhost:8003 your_user@edges-cluster.example.com
```

Open `http://localhost:8880/` in your browser. The SPA loads as
pre-compiled static chunks (no Vite at runtime), and every API/static
request stays within this single tunnel.

> If 8880 is taken on your laptop: `ssh -L 9090:localhost:8003 …` and
> open `http://localhost:9090/`.

### Optional — Vite dev server for hot-reloading frontend code

If you're actively editing frontend code, run Vite's dev server in a
second terminal and tunnel that instead:

```bash
# Cluster, terminal 2:
cd edges3-website/frontend
npm run dev          # serves on http://localhost:5173, proxies /api + /data to :8003
```

```bash
# Laptop:
ssh -L 8880:localhost:5173 your_user@edges-cluster.example.com
```

`vite.config.ts` proxies API calls and `/data/*` to the backend on
`8003`. The dev server is much slower than the built bundle over a
slow SSH tunnel — only use it when you're iterating on UI code.

---

## What you do in the UI

Four tabs:

* **Nightly Overview** (home, `/`): the most recent MRO night
  (18:00–06:00 AWST) from the precomputed quick-look and L1 products: a Q
  (or log p0) waterfall against site time with LST on the top axis, antenna
  dropouts and ADC/data-drop events, band-median Q and band power,
  housekeeping, and a per-file QA table with badges. The Q waterfall's
  0.5 MHz bins are the median of their ~80 channels by default, which hides
  narrowband RFI; **Q mean** and **Q max** keep it (QL version 3 products),
  and **minus each channel's median** shows changes and RFI against the
  night's typical spectrum. **Previous/Next** and
  the date picker choose other nights (`/?date=YYYY-MM-DD`, named by the
  local date of the evening). The badge thresholds are provisional (see
  `QA_THRESHOLDS` in `backend/products_api.py`).
* **Calibrations** (`/calibrations`): the receiver calibration of a day,
  from edges-pipeline. On load it shows the day's **stored pipeline
  calibration** (the pipeline's default settings, computed by its cron for
  every calibration day; nothing is computed here): the residuals of the
  four loads (calibrated − known, or the values), the noise-wave
  parameters, the modelled S11s of the loads and the receiver, the hot-load
  loss, each load's mean Q spectrum with the receiver temperature during it
  (flagged when it differs between the loads by more than 2 °C), the loads'
  mean R = P_L/(P_LNS − P_L) and their differences (gain drift), each
  load's band-median Q per cycle over time (from L1, flagged cycles in red),
  the issues, and its settings and configuration hash. Days without a
  calibration are listed with the pipeline's reason
  (`Products.calibration_days`, e.g. `no_temperature`,
  `s11_grids_differ`). Changing a setting (cterms, wterms, the fit band, S11
  model terms, probes, …) or choosing another full S11 session asks the
  server whether it is still the stored default; if not, **Run** computes
  the calibration in the background with the pipeline's own code
  (`rcal.calibrate_day`, ~45 s, ~2 GB), labelled as computed. The fit band
  is also the calibration band. Another stored configuration (e.g. the old
  Alan-mode products) can be overlaid for comparison.
* **Detailed Data View** (`/data`): choose a night, the antenna S11
  session and its fit window on the left; the receiver calibration is the
  one selected on the Calibrations tab (a computed one runs first if
  needed). Click **Run**
  (~30 s per night); the plots load on the right: the night-mean P_ant,
  P_load, P_LNS, Q, R = P_load/(P_LNS − P_load) and uncalibrated
  temperature (default axis limits: the central 90% of the values in the
  calibration band), the antenna calibration (a, b and calibrated
  temperature, within the antenna S11 fit window), the antenna S11 (model
  and measurement) and, on demand, Q and T_cal waterfalls.
* **Raw Data** (`/raw`; EDGES-3, EDGES-2 low2 and low2-128k so far): a
  receiver's whole record from the catalog alone, per UTC day (hours of
  antenna and of calibration-load data, or files before spectrum
  extraction; GB; S11 sessions; data drops; largest ADC value). Zoom in to
  two months or less, or click a day, for the range: each spectrum file's
  span (by load), the S11 sessions, the receiver's own housekeeping
  (EDGES-3: the temperature log; EDGES-2: its sensor files) and the site's
  weather (both panels zoom together); the pipeline's quick-look Q
  waterfall of the times shown (up to 8 days; median, mean or max binning,
  optionally minus each channel's median); a file table (click a file for
  its per-cycle ADC extremes and data drops) and the S11 sessions (click one
  for its raw, uncalibrated |S11| and phase). No spectrum file is read here.
* **Status** (`/status`): whether the data packages work, and what the
  job queue is doing.

Every finished run has a **Download (zip)** link. Runs are kept by their
key (inputs, parameters, file versions, pipeline code), so an identical
request is never recomputed; the newest 20 of each kind are kept.

---

## Layout

| Path | Purpose |
|---|---|
| `backend/` | Python service |
| `backend/config.py` | All env-driven paths and tunable defaults (single source of truth) |
| `backend/backend_api.py` | FastAPI app — routers, status, static-file mount, SPA fallback |
| `backend/products_api.py` | Read-only `/api/night` etc. over the catalog and pipeline products (Nightly Overview) |
| `backend/runs_api.py` | Calibration / observation runs as background jobs (`/api/calibrations`, `/api/observations`) |
| `backend/browse_api.py` | The Raw Data page's `/api/browse/...`: a receiver's record, files, cycles, raw S11 sessions, housekeeping and weather, from the catalog |
| `backend/catalog_inputs.py` | Dates, nights, input files, S11 sessions and probe temperatures, from the catalog (nothing scans the raw tree) |
| `backend/run_single_day.py` | The EDGES pipeline: `--stage calibration` and `--stage observation` |
| `backend/tests/` | pytest tests on a small synthetic catalog |
| `backend/requirements.txt` | Every Python dep, installed via `uv pip install` |
| `frontend/` | React + Vite SPA |
| `frontend/src/pages/` | `LastNight` (Nightly Overview), `Calibrations`, `DataView`, `Browse` (Raw Data), `Home` (Status) |
| `frontend/src/components/StackedPlot.tsx` | Multi-panel figures sharing an x axis |
| `frontend/src/state/CalibrationContext.tsx` | The calibration selected on the Calibrations tab (shared with the Data View) |
| `frontend/src/utils/baseURL.ts` | Where the frontend reads `VITE_API_URL` from |
| `outputs/` | **NOT** checked into git — runtime artefacts (runs) |

---

## Paths you may want to change

Every path the backend uses is environment-driven. The defaults are in
`backend/config.py`; override any of them with an env var before
launching the backend.

### Backend (runtime env vars)

| Env var | Default | What it controls |
|---|---|---|
| `EDGES_RAW_DATA_ROOT` | `/data5/edges/data/EDGES3_data/MRO` | Root of the raw `.acq` + `.log` tree the pipeline reads |
| `EDGES_OUTPUT_ROOT` | `<repo>/outputs` | Where calibration and observation runs are written |
| `EDGES_BEAM_FACTOR_FILE` | `/data4/vydula/edges/packages/edges3-data-analysis/data/e3_beam_factor.hickle` (canonical; falls back to `<RAW_DATA_ROOT>/../../../e3_beam_factor.hickle` then `/mnt/data5/...`, `/scratch/...`, `$HOME/edges/...`) | Path to the EDGES-3 antenna beam factor file. Required for the absolute temperature calibration; the canonical path ships with the `edges-3-data-analysis` package. Set this explicitly only if the file lives somewhere else. |
| `EDGES_PIPELINE_ROOT` | `/data6/edges/edges-db` | Where the catalog (`catalog.sqlite`), products database (`products.sqlite`) and QL/L1 products live (read by `/api/*`; read only) |
| `EDGES_PYTHON` | current interpreter (`sys.executable`) | Python the backend shells out to when running the pipeline |
| `EDGES_PROBE_AMBIENT` | `101` | Temperature-log code of the ambient load, shown per antenna file on the Detailed Data View (information only) |
| `EDGES_PROBE_COLD_LOAD` | `152` | Code 152 is `pr59_current`, **not a temperature**; informational only, not used by the calibration |
| `EDGES_ALLOWED_ORIGINS` | `http://localhost:5173, http://127.0.0.1:5173, http://localhost:8003, http://127.0.0.1:8003` | Comma-separated CORS allowlist for the API. Loopback origins are always allowed; when the SPA is hosted on a different host than the backend, set this to the SPA's origin (e.g. `https://edges.example.com`). Never set it to `*`. |

The receiver calibration's inputs are the pipeline's: the full S11 session
the catalog recommends for the day, and the mean temperature-log readings
of probes 101 (ambient load) and 102 (hot load) during those spectra (the
probes are settings on the Calibrations tab). The calibration of the
antenna needs no probe temperature: Q is converted to an approximate
temperature with the calibration's own Dicke convention
(`t_load = 300 K`, `t_load_ns = 1000 K`), which the calibrator then
calibrates.

The values above are documented programmatically in
`backend/config.py::describe()`. Run `python backend/config.py` to print
the resolved configuration.

### Frontend (build-time env var)

| Env var | Default | What it controls |
|---|---|---|
| `VITE_API_URL` | *(empty — same-origin)* | Base URL the frontend uses for API calls. Vite inlines it at build time. Leave it unset — FastAPI serves the API on the same origin as the SPA (`/`), so same-origin works. Set only when serving the built `dist/` from a different host than the API. |
| `VITE_PROXY_PORT` | `8003` | Port Vite's dev server proxies to (only matters for `npm run dev`). Override if you started uvicorn on a different port. |

---

## What gets written under `outputs/`

This is **runtime state** and is excluded from git (see `.gitignore`).
Deleting it only means runs are recomputed when next requested.

```
outputs/
├── calibration/<id>/          # a receiver calibration with non-default settings
│   ├── request.json  params.json  status.json  log.txt
│   ├── rcal.h5                 # the solution, in the layout of a stored rcal product
│   └── result.json             # what the page plots (as /api/calibrations/stored/<day>)
└── observation/<id>/          # one night with one calibration
    ├── request.json  inputs.json  params.json  status.json  log.txt
    ├── calibration.json        # which calibration (a stored product, or a run above)
    ├── result.json
    ├── plots.npz               # night-mean spectra, a/b, T_cal, antenna S11
    └── waterfalls.npz          # Q and T_cal per cycle (0.25 MHz bins)
```

---

## Pipeline stages (`run_single_day.py`)

**Calibration** (`--stage calibration`), only for non-default settings
(the default is stored by the pipeline for every day):
`edges_pipeline.stages.rcal.calibrate_day(day, params)` (the pipeline's own
code; it takes the inputs from the catalog), then save the solution
(`rcal.h5`) and the JSON the page plots (`result.json`). If the pipeline
cannot calibrate the day it says why (e.g. `no_temperature`) and the run
fails with that reason. The site never calls `edges.cal` itself.

**Observation** (`--stage observation`): the backend resolves the night's
inputs in the catalog (`catalog_inputs.resolve_observation`: the exact
files, the antenna S11 session and any issues) into `inputs.json`, and
names the calibration in `calibration.json`. Then, for each antenna file of
the night (one at a time):

1. Keep the cycles inside the night; compute Q, R and the uncalibrated
   temperature `t_load_ns Q + t_load`.
2. Calibrate with the receiver calibration (rebuilt by
   `edges_pipeline.products.rcal_calibrator`, stored or computed alike) and
   the antenna S11 model (fitted in its window, NaN outside):
   `T_cal = a Q + b`.
3. Accumulate night means and waterfalls; save a, b and the antenna S11.

---

## Development workflow

### Lint (pre-commit)

`.pre-commit-config.yaml` runs ruff (`ruff.toml`) on the backend and a few
file-hygiene checks; pre-commit.ci runs the same on every push. Run them
before committing (or install them as a git hook with `pre-commit install`):

```bash
uvx pre-commit run --all-files
```

### Tests

They build a tiny synthetic field mirror in a temporary directory, ingest
it with `edges-catalog` and run the `edges-pipeline` QL and L1 stages on
it (they are skipped if those packages are not installed). Stored
calibrations are faked in its products database (as the pipeline's tests
do); computations run the real calibration stage with
`rcal.calibrate_day` mocked, and nights a fake stage script:

```bash
uv pip install pytest httpx
cd backend
python -m pytest tests
```

### Running a stage by hand

```bash
cd backend
echo '{"fit": {"cterms": 7}}' > params.json
python run_single_day.py --stage calibration --day 2026_267 \
    --params params.json --run_dir /path/to/cal
python run_single_day.py --stage observation --inputs inputs.json \
    --calibration calibration.json --run_dir /path/to/obs
```

An observation's `inputs.json` and `calibration.json` are easiest to copy
from a run the site made (`outputs/observation/<id>/`). Run with `--help`
for the options.

---

## Raw data lives outside this repo

**This repo contains code only.** The raw MRO data (`.acq` files,
temperature logs) is large, environment-specific, and never committed.

On the cluster the raw tree lives at
`/data5/edges/data/EDGES3_data/MRO`. The backend reaches it via the
`EDGES_RAW_DATA_ROOT` env var; the default already points there, so
just set it if your mount is in a non-standard place:

```bash
export EDGES_RAW_DATA_ROOT=/the/place/where/the/raw/data/lives
```

`.gitignore` defensively excludes `/data5/`, `/data/`, `/raw/`, `/mro/`,
`/acq/`, and `*.acq` so the raw tree can't be accidentally committed.

The pipeline never writes into `RAW_DATA_ROOT`; it is read-only input.
All generated artefacts (runs, their `.npz` files and zips) go to
`OUTPUT_ROOT`, which is separate and gitignored.

---

## Open questions

To confirm with the team:

* **Ambient-load probe.** The pipeline's calibration uses code 101
  (`amb_load_temperature`); the site's own Alan-mode runs used 100
  (`front_end_temperature`). To confirm with the team; 100 can be chosen on
  the Calibrations tab (a computed calibration).
* **Antenna S11 fit window.** The default is 58–105 MHz, where the
  EDGES-3 antenna is matched (|S11| ≈ 0.16–0.27); outside it |S11| rises to
  ~0.9 and one model fits ~300× worse, so a, b and T_cal are only produced
  in the window. It can be changed on the Detailed Data View tab.
* **Code 152** is `pr59_current`, not a cold-load temperature; the site
  keeps `EDGES_PROBE_COLD_LOAD` only for information.
* **Code 0** is the thermal setpoint (35 °C for most of the record, 25 °C
  since ~2026-08; inferred by the data side).

## Troubleshooting

* **A run fails** — the tab shows the error and the end of the log; the
  full log is `outputs/<kind>/<id>/log.txt`. Clicking Run again retries.
* **"interrupted (server restarted)"** — the backend stopped while the run
  was queued or running; click Run again.
* **`e3_beam_factor.hickle` not found** — set
  `EDGES_BEAM_FACTOR_FILE` to the correct path on your cluster.
* **Vite can't reach the backend** — make sure you started the
  backend on `127.0.0.1:8003` *on the same machine as the frontend
  dev server* (i.e. both running on the cluster, behind the same
  `ssh -L` tunnel).
* **Browser shows stale data after a run** — every page refetches on
  focus, but you can also click the navbar's brand to bounce the app
  and force a refresh.

---

## License

See `LICENSE`.
