# EDGES-3 Web Interface

A web UI for inspecting the EDGES-3 instrument's daily calibration and
antenna-temperature pipeline output. The React frontend and the FastAPI
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
from disk directly — it asks the backend for JSON (`/manifest.json`,
`/latest_run`) and for binary files (`.npz`, `.jpg`) which the backend
serves from its `OUTPUT_ROOT` static mount.

There is **no daemon**, no scheduler, no systemd unit. Runs happen
when you click "Run with these dates" on the Select page.

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
* `/data/...`     — `OUTPUT_ROOT` (manifest, `runs/<id>/...`, saved zips)
* `/run_pipeline`, `/save_outputs`, etc. — JSON API endpoints

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

1. Go to **Select**.
2. Pick dates (defaults to "Latest" for all three) and parameters (40–190
   MHz, 6 cterms, 5 wterms, no 2D by default).
3. Click **Run with these dates**. This calls `POST /run_pipeline`,
   which wipes any prior run and writes a fresh `outputs/runs/<id>/`
   directory and `manifest.json`. Dedup: clicking again with the same
   dates and parameters reuses the previous run instead of recomputing.
4. Browse the results in **Calibration**, **Raw Data**, and
   **Calibrated Data**. The plot pages show "Run has not been completed"
   until you trigger a run.

When you want a copy of the current outputs, click **Save outputs** at
the top of any data page; the resulting zip lives at
`outputs/saved/<label>.zip` and is downloadable from the UI.

---

## Layout

| Path | Purpose |
|---|---|
| `backend/` | Python service |
| `backend/config.py` | All env-driven paths and tunable defaults (single source of truth) |
| `backend/backend_api.py` | FastAPI app — REST endpoints + static-file mount |
| `backend/run_single_day.py` | The actual EDGES calibration + temperature pipeline |
| `backend/scan_dates.py` | Scans the raw-data tree and writes `available_dates.json` |
| `backend/io_utils.py` | Shared dataclasses (`Plot`), hashing, manifest writing |
| `backend/requirements.txt` | Every Python dep, installed via `uv pip install` |
| `frontend/` | React + Vite SPA |
| `frontend/src/utils/baseURL.ts` | Where the frontend reads `VITE_API_URL` from |
| `frontend/src/state/RunContext.tsx` | Frontend cache of `/latest_run` and refresh counter |
| `outputs/` | **NOT** checked into git — runtime artefacts (manifest, runs, zip downloads) |

---

## Paths you may want to change

Every path the backend uses is environment-driven. The defaults are in
`backend/config.py`; override any of them with an env var before
launching the backend.

### Backend (runtime env vars)

| Env var | Default | What it controls |
|---|---|---|
| `EDGES_RAW_DATA_ROOT` | `/data5/edges/data/EDGES3_data/MRO` | Root of the raw `.acq` + `.log` tree the pipeline reads |
| `EDGES_OUTPUT_ROOT` | `<repo>/outputs` | Where the manifest, runs, saved zips, and run history are written |
| `EDGES_TEMP_LOG_FILE` | `$EDGES_RAW_DATA_ROOT/temperature_logger/temperature.log` | Single-file temperature log (legacy) |
| `EDGES_TEMP_LOG_DIR` | `$EDGES_TEMP_LOG_FILE`'s parent | Directory of log files; every `*.log`, `*.backup`, and `*.txt` in here is read and merged into one timeline |
| `EDGES_BEAM_FACTOR_FILE` | `/data4/vydula/edges/packages/edges3-data-analysis/data/e3_beam_factor.hickle` (canonical; falls back to `<RAW_DATA_ROOT>/../../../e3_beam_factor.hickle` then `/mnt/data5/...`, `/scratch/...`, `$HOME/edges/...`) | Path to the EDGES-3 antenna beam factor file. Required for the absolute temperature calibration; the canonical path ships with the `edges-3-data-analysis` package. Set this explicitly only if the file lives somewhere else. |
| `EDGES_PYTHON` | current interpreter (`sys.executable`) | Python the backend shells out to when running the pipeline |
| `EDGES_PROBE_AMBIENT` | `100` | Temperature-log probe for ambient cal |
| `EDGES_PROBE_HOT` | `102` | Temperature-log probe for hot cal |
| `EDGES_PROBE_LNA` | `100` | Temperature-log probe for LNA cal |
| `EDGES_PROBE_COLD_LOAD` | `152` | Temperature-log probe for the cold load (informational) |
| `EDGES_ALLOWED_ORIGINS` | `http://localhost:5173, http://127.0.0.1:5173, http://localhost:8003, http://127.0.0.1:8003` | Comma-separated CORS allowlist for the API. Loopback origins are always allowed; when the SPA is hosted on a different host than the backend, set this to the SPA's origin (e.g. `https://edges.example.com`). Never set it to `*`. |

Calibration temperatures are auto-derived from the temperature log at
the matching calibration time and passed straight to the EDGES receiver
calibration — there are no user-tunable setpoints. If no probe reading
is available at the calibration time the pipeline falls back to
internal constants (`306.5`, `393.22`, `306.5` K) and logs a warning.

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
If you blow it away, the next user run will rebuild it from scratch.

```
outputs/
├── manifest.json             # Latest manifest (mirrors runs/<run_id>/manifest.json)
├── latest_run.json           # {source, run_id, dates, generated_at, parameters, has_2d, …}
├── available_dates.json      # Scanned by scan_dates.py on backend startup
├── runs/<run_id>/            # One folder per user-triggered run
│   ├── calibration/
│   ├── calibration_s11/
│   ├── calibration_spectra/
│   ├── calibration_coefficients/   # scale/offset/unc/cos/sin TNW + scale_temperature/offset_temperature
│   ├── calibration_temperatures/   # noise-wave fit values per load (ambient/hot/open/short)
│   ├── calibrated_temperature/     # Tcal = a*Q + b from specal.txt
│   ├── average_temperature/        # time-averaged Tuncal (Dicke only)
│   ├── antenna_s11/
│   ├── raw_spectra/  raw_waterfalls/
│   └── actual_temperature/         # probe readings at each cal time
├── user_cache/<hash>/        # Snapshot of the previous user run (single entry, evicted on next run)
└── saved/                    # ZIP archives produced by the Save button in the UI
```

---

## Pipeline steps (what `run_single_day.py` does, top to bottom)

For a given `(cal_date, s11_date, spec_date)` triple the pipeline runs:

1. Load the four `.acq` calibration files (amb / hot / open / short) and
   the antenna `.acq`.
2. Merge every `*.log` / `*.backup` / `*.txt` in
   `EDGES_TEMP_LOG_DIR` into one timeline of probe readings.
3. Look up the calibration temperatures:
   * Primary: the `.tmp` snapshot file written at the moment of the
     cal/obs (e.g. `2026_227_05_amb.tmp`).
   * Fallback: nearest-in-time reading in the merged temperature log.
   * Final fallback: the internal default constants.
4. Run the EDGES receiver calibration
   (`alancal_edges3`) — writes `calibration/specal.txt` and
   `calibration/s11_modelled.txt`.
5. Save the noise-wave coefficients
   (`scale`, `offset`, `unc`, `cos`, `sin`) and the linear-frontend
   coefficients (`scale_temperature`, `offset_temperature`) as
   `calibration_coefficients/<date>_<coeff>.npz`.
6. Save the per-frequency temperatures the noise-wave model fit against
   each load (`calibration_temperatures/<date>_{ambient,hot,open,short}.npz`).
7. Save the raw antenna spectra (`raw_spectra/<spec_date>_{P0,P1,P2,Q}.npz`)
   and time-averaged + 2D waterfall plots.
8. Run the Dicke switching step, then the linear frontend
   calibration `Tcal = a*Q + b` (where `a` and `b` come from
   `specal.txt` via `calobs.calibrate_approximate_temperature`):
   * `calibrated_temperature/<spec_date>_cal_temp.npz` — time-averaged
     `Tcal` over 40–190 MHz.
   * `average_temperature/<spec_date>_avg_temp.npz` — time-averaged
     uncalibrated temperature (Tuncal, the Dicke-only result).
   * `calibrated_waterfalls/<spec_date>_calibrated.jpg` — 2D waterfall
     plot of `Tcal` over LST.
9. Save the antenna S11 measurement (`antenna_s11/<s11_date>_antenna_S11.npz`).
10. Save per-load actual probe readings
    (`actual_temperature/<spec_date>_{ambient,hot}_actual_temp.npz`).
11. Write `manifest.json` describing every plot above.

---

## Development workflow

### Running the pipeline manually

```bash
cd backend
EDGES_RAW_DATA_ROOT=/path/to/mro \
EDGES_OUTPUT_ROOT=/path/to/outputs \
python run_single_day.py \
    --cal-date 2026_227 --s11-date 2026_242_23 --spec-date 2026_244_22_24_54 \
    --source user
```

Run with `--help` to see every tunable (`cterms`, `wterms`,
`fstart`/`fstop`, `wfstart`/`wfstop`, `save_2d_npz`, `--run-hash`, …).

### Triggering the pipeline from the UI

* Click **Run with these dates** on the Select page (`POST /run_pipeline`).

### Dedup behaviour

User runs are deduped by a hash of `(cal-date, s11-date, spec-date,
cterms, wterms, fstart, fstop, wfstart, wfstop, save_2d_npz)`.
Re-running with the same parameters reuses the previous run's outputs
from `user_cache/<hash>/` and just bumps `latest_run.json` — no
recomputation. The cache holds only the immediately previous run (one
entry); it is evicted on the next run that produces a different hash.

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
All generated artefacts (`manifest.json`, plot `.npz` / `.jpg` files,
saved zips, run history) go to `OUTPUT_ROOT` which is separate and
gitignored.

---

## Troubleshooting

* **"Pipeline failed (exit 1)" in the UI** — open the terminal running
  the backend. The full traceback prints to stderr.
* **`manifest.json` not found** — you haven't run the pipeline yet.
  Click **Run with these dates** on the Select page.
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
