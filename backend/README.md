# Backend (`backend/`)

FastAPI service + the EDGES-3 calibration/temperature pipeline.

## Files

| File | Role |
|---|---|
| `config.py` | **All paths and tunable defaults live here.** Env-driven; see the top-level `README.md` for the full table. |
| `backend_api.py` | FastAPI app — REST endpoints (`/manifest.json`, `/latest_run`, `/run_pipeline`, `/save_outputs`, `/download/...`) and a static-file mount at `/` serving `$EDGES_OUTPUT_ROOT`. |
| `run_single_day.py` | The actual EDGES pipeline: receiver cal, S11 modelling, Dicke + linear frontend calibration, antenna temp, per-load actual temp matching. Writes the manifest + per-run npz/jpg trees. |
| `scan_dates.py` | Walks the raw data tree and writes `available_dates.json` so the UI can list what's runnable. |
| `io_utils.py` | Shared dataclasses (`Plot`), deterministic run-hash, manifest writer. |
| `requirements.txt` | Every Python dep, installed via `uv pip install`. |

## Running locally

```bash
cd edges3-website
source .venv/bin/activate   # uv venv created during install

cd backend
python -m uvicorn backend_api:app --host 127.0.0.1 --port 8003
```

## Running the pipeline manually

```bash
python run_single_day.py \
    --cal-date 2026_227 \
    --s11-date 2026_242_23 \
    --spec-date 2026_244_22_24_54 \
    --source user
```

The script prints a probe survey, then derived calibration temperatures,
then the linear-frontend calibration. The manifest lands in
`$EDGES_OUTPUT_ROOT/manifest.json`.

## Path configuration cheat sheet

`config.py` resolves every path from environment variables, with
sensible defaults for the SSH cluster. Override any of these:

```bash
export EDGES_RAW_DATA_ROOT=/path/to/mro
export EDGES_OUTPUT_ROOT=/path/to/outputs
export EDGES_TEMP_LOG_DIR=/path/to/temperature_logger
python -m uvicorn backend_api:app --host 127.0.0.1 --port 8003
```

See the top-level `README.md` for the full env-var table.
