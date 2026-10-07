# Backend (`backend/`)

FastAPI service for the EDGES-3 site: catalog and pipeline products, receiver
calibrations from edges-pipeline, and nights calibrated with them.

## Files

| File | Role |
|---|---|
| `config.py` | **All paths and tunable defaults live here.** Env-driven; see the top-level `README.md` for the full table. |
| `backend_api.py` | FastAPI app: the routers below, `/health`, `/pipeline/status`, the `/data` static mount of `$EDGES_OUTPUT_ROOT` and the SPA fallback. |
| `products_api.py` | Read-only `/api/night`, `/api/nights/latest`, `/api/quicklook`, `/api/l1`, `/api/housekeeping`, `/api/status` over the catalog and pipeline products (Nightly Overview). Needs the optional `edges-catalog`/`edges-pipeline` packages. |
| `runs_api.py` | `/api/calibrations/...` (stored calibrations, and computations with other settings as background jobs), `/api/observations/...` (nights, background jobs), `/api/runs/...`. |
| `calibrations.py` | Receiver calibrations from edges-pipeline: the settings form and its validation, the day list with the pipeline's reasons for gaps (cached), the JSON a page plots (stored or computed alike) and the loads' cycles from L1. |
| `browse_api.py` | `/api/browse/...` for the Raw Data page (EDGES-3, EDGES-2 low2): a receiver's record per day, files, cycles, raw S11 sessions, housekeeping and weather, from the catalog only. |
| `catalog_inputs.py` | Nights, antenna files and S11 sessions from the EDGES catalog (nothing scans the raw data tree). |
| `run_single_day.py` | Two stages: `--stage calibration` (a receiver calibration with non-default settings, by `rcal.calibrate_day`) and `--stage observation` (a night calibrated with a stored or computed calibration). |
| `tests/` | pytest tests on a synthetic catalog and products database (with fake stored calibrations), and of the job runner (`rcal.calibrate_day` mocked in `tests/fake_pipeline.py`). |
| `requirements.txt` | Every Python dep, installed via `uv pip install`. |

## Running locally

```bash
cd edges3-website
source .venv/bin/activate   # uv venv created during install

cd backend
python -m uvicorn backend_api:app --host 127.0.0.1 --port 8003
```

## Running a stage by hand

```bash
python run_single_day.py --stage calibration --day 2026_267 --params params.json --run_dir /path/to/cal
python run_single_day.py --stage observation --inputs inputs.json \
    --calibration calibration.json --run_dir /path/to/obs
```

`params.json` holds the pipeline settings by section (`{"fit": {"cterms":
7}}`); copy an observation's `inputs.json`/`calibration.json` from a run the
site made. Results: `result.json` (and `rcal.h5`, or `plots.npz` and
`waterfalls.npz`).

## Path configuration cheat sheet

`config.py` resolves every path from environment variables, with
sensible defaults for the SSH cluster. Override any of these:

```bash
export EDGES_PIPELINE_ROOT=/path/to/edges-db   # catalog + products
export EDGES_OUTPUT_ROOT=/path/to/outputs
python -m uvicorn backend_api:app --host 127.0.0.1 --port 8003
```

See the top-level `README.md` for the full env-var table.
