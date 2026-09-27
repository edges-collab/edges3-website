# Backend (`backend/`)

FastAPI service + the EDGES-3 calibration/temperature pipeline.

## Files

| File | Role |
|---|---|
| `config.py` | **All paths and tunable defaults live here.** Env-driven; see the top-level `README.md` for the full table. |
| `backend_api.py` | FastAPI app: the routers below, `/health`, `/pipeline/status`, the `/data` static mount of `$EDGES_OUTPUT_ROOT` and the SPA fallback. |
| `products_api.py` | Read-only `/api/night`, `/api/nights/latest`, `/api/quicklook`, `/api/l1`, `/api/housekeeping`, `/api/status` over the catalog and pipeline products (Nightly Overview). Needs the optional `edges-catalog`/`edges-pipeline` packages. |
| `runs_api.py` | Calibration and observation runs of `run_single_day.py` as background jobs: `/api/calibrations/...`, `/api/observations/...`, `/api/runs/...`. |
| `catalog_inputs.py` | Dates, nights, input files, S11 sessions and probe temperatures from the EDGES catalog (nothing scans the raw data tree). |
| `run_single_day.py` | The EDGES pipeline in two stages: `--stage calibration` (receiver calibration) and `--stage observation` (a night calibrated with it). |
| `tests/` | pytest tests on a synthetic catalog, and of the job runner with a fake stage script. |
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
python run_single_day.py --stage calibration --cal_date 2026_267 --run_dir /path/to/cal
python run_single_day.py --stage observation --night 2026-09-25 \
    --calibration_dir /path/to/cal --run_dir /path/to/obs
```

The inputs are resolved in the catalog (sessions default to the
recommended ones) and written to `<run_dir>/inputs.json`; `--inputs FILE`
(and `--params FILE`) reuse such files. Results: `result.json`,
`plots.npz`, `waterfalls.npz`.

## Path configuration cheat sheet

`config.py` resolves every path from environment variables, with
sensible defaults for the SSH cluster. Override any of these:

```bash
export EDGES_PIPELINE_ROOT=/path/to/edges-db   # catalog + products
export EDGES_OUTPUT_ROOT=/path/to/outputs
python -m uvicorn backend_api:app --host 127.0.0.1 --port 8003
```

See the top-level `README.md` for the full env-var table.
