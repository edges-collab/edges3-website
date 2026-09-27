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
uv venv .venv --python 3.11
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
products, and the **Calibrations** and **Detailed Data View** tabs take their dates, input files
and probe temperatures from the catalog. Both use two packages that are
**not published yet**:
`edges-catalog` and `edges-pipeline`. They are local git repositories
on the EDGES server (ask the data maintainers for their location);
install them pinned to their committed code (not editable):

```bash
uv pip install \
  "edges-catalog @ git+file:///path/to/edges-catalog" \
  "edges-pipeline @ git+file:///path/to/edges-pipeline"
```

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
  housekeeping, and a per-file QA table with badges. **Previous/Next** and
  the date picker choose other nights (`/?date=YYYY-MM-DD`, named by the
  local date of the evening). The badge thresholds are provisional (see
  `QA_THRESHOLDS` in `backend/products_api.py`).
* **Calibrations** (`/calibrations`): choose a calibration day, the S11
  session (default: the one the catalog recommends for the day) and the fit
  parameters on the left; the inputs, temperatures and any issues show
  there. Click **Run** (~1 min); the plots load on the right: the S11s of
  the four loads and the receiver (|S11|/phase or Re/Im, sharing one zoomable
  frequency axis), the noise-wave parameters, the calibrated load
  temperatures against the known ones (the hot load with its cable loss
  removed; values or residuals), and the calibration spectra (with
  waterfalls of the change over time on demand). A calibration run with the
  same options earlier is shown at once.
* **Detailed Data View** (`/data`): choose a night, the antenna S11
  session and its fit window on the left; the calibration is the one
  selected on the Calibrations tab (run first if needed). Click **Run**
  (~30 s per night); the plots load on the right: the night-mean P_ant,
  P_load, P_LNS, Q, R = P_load/(P_LNS − P_load) and uncalibrated
  temperature (default axis limits: the central 90% of the values in the
  calibration band), the antenna calibration (a, b and calibrated
  temperature, within the antenna S11 fit window), the antenna S11 (model
  and measurement) and, on demand, Q and T_cal waterfalls.
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
| `backend/catalog_inputs.py` | Dates, nights, input files, S11 sessions and probe temperatures, from the catalog (nothing scans the raw tree) |
| `backend/run_single_day.py` | The EDGES pipeline: `--stage calibration` and `--stage observation` |
| `backend/tests/` | pytest tests on a small synthetic catalog |
| `backend/requirements.txt` | Every Python dep, installed via `uv pip install` |
| `frontend/` | React + Vite SPA |
| `frontend/src/pages/` | `LastNight` (Nightly Overview), `Calibrations`, `DataView`, `Home` (Status) |
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
| `EDGES_PROBE_AMBIENT` | `100` | Temperature-log code for the ambient-load cal (100 is the *front end*; see [Open questions](#open-questions)) |
| `EDGES_PROBE_HOT` | `102` | Temperature-log code for the hot-load cal |
| `EDGES_PROBE_COLD_LOAD` | `152` | Code 152 is `pr59_current`, **not a temperature**; informational only, not used by the calibration |
| `EDGES_ALLOWED_ORIGINS` | `http://localhost:5173, http://127.0.0.1:5173, http://localhost:8003, http://127.0.0.1:8003` | Comma-separated CORS allowlist for the API. Loopback origins are always allowed; when the SPA is hosted on a different host than the backend, set this to the SPA's origin (e.g. `https://edges.example.com`). Never set it to `*`. |

Calibration temperatures are the probe readings at the time of each
calibration spectrum, from the catalog's housekeeping (de-duplicated, and
without the logs of other receivers such as the Adak system), and are
passed straight to the EDGES receiver calibration — there are no
user-tunable setpoints. For each spectrum: the `.tmp` snapshot of that
load at the hour of its time stamp; else the nearest temperature-log
reading of *that* probe within 15 minutes; else the internal constants
(`306.5`, `393.22` K), which the Calibrations tab reports as an issue.
The calibration of the antenna itself needs no probe temperature: Q is
converted to an approximate temperature with the same nominal values
(`T_LOAD = 300 K`, `T_NS = 1000 K`) that `alancal_edges3` writes
`specal.txt` with, so the calibration is self-consistent.

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
├── calibration/<id>/          # one receiver calibration (id = hash of its key)
│   ├── request.json  inputs.json  params.json  status.json  log.txt
│   ├── calibration/            # alancal_edges3 outputs (specal.txt, calibrated_temps.txt, ...)
│   ├── s11_cache/              # S11 files resampled onto one grid (if the VNA grid changed)
│   ├── result.json             # summary: dates, parameters, temperatures, warnings
│   ├── plots.npz               # S11s, noise-wave parameters, load temperatures, spectra
│   └── waterfalls.npz          # calibration spectra over time
└── observation/<id>/          # one night with one calibration
    ├── request.json  inputs.json  params.json  status.json  log.txt
    ├── result.json
    ├── plots.npz               # night-mean spectra, a/b, T_cal, antenna S11
    └── waterfalls.npz          # Q and T_cal per cycle (0.25 MHz bins)
```

---

## Pipeline stages (`run_single_day.py`)

The backend resolves each run's inputs in the catalog
(`catalog_inputs.resolve_calibration` / `resolve_observation`: the exact
files, the S11 session, the probe temperatures and any issues) and writes
them to `inputs.json`. Then:

**Calibration** (`--stage calibration`):

1. Run the EDGES receiver calibration (`alancal_edges3`) with the ambient
   and hot-load probe readings as the known load temperatures (S11 files
   resampled onto a common grid first if the VNA was reconfigured).
2. Save the modelled S11s (loads and receiver), the noise-wave parameters
   (`T_sca`, `T_off`, `T_unc`, `T_cos`, `T_sin`), the calibrated load
   temperatures and the known ones (the hot load also with its cable loss
   removed: `T_hot = (T_in − (1 − G) T_amb) / G`), and the calibration
   spectra (mean Q and its change over time).

**Observation** (`--stage observation`), for each antenna file of the night
(one at a time):

1. Keep the cycles inside the night; compute Q, R and the uncalibrated
   temperature `T_NS Q + T_LOAD`.
2. Calibrate with the calibration's `specal.txt` and the antenna S11 model
   (fitted in its window, NaN outside): `T_cal = a Q + b`.
3. Accumulate night means and waterfalls; save a, b and the antenna S11.

---

## Development workflow

### Tests

They build a tiny synthetic field mirror in a temporary directory, ingest
it with `edges-catalog` and run the `edges-pipeline` QL and L1 stages on
it (they are skipped if those packages are not installed); the job runner
is tested with a fake stage script:

```bash
uv pip install pytest httpx
cd backend
python -m pytest tests
```

### Running a stage by hand

```bash
cd backend
python run_single_day.py --stage calibration --cal_date 2026_267 \
    --run_dir /path/to/cal
python run_single_day.py --stage observation --night 2026-09-25 \
    --calibration_dir /path/to/cal --run_dir /path/to/obs
```

Dates are resolved in the catalog as the backend does (`Latest` by
default; `--s11_date` / `--ant_s11` default to the recommended sessions)
and the inputs are written to `<run_dir>/inputs.json`; or pass
`--inputs inputs.json` (and `--params params.json`). Run with `--help`
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

To confirm with the team (the defaults are unchanged for now):

* **Ambient-load probe.** `EDGES_PROBE_AMBIENT` defaults to code 100,
  which the catalog and `edges-analysis` call `front_end_temperature`;
  code 101 is `amb_load_temperature`. Should the ambient-load calibration
  temperature (`tcold`) use 101?
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
