# Backend (`backend/`)

FastAPI service + the EDGES-3 calibration/temperature pipeline.

## Files

| File | Role |
|---|---|
| `config.py` | **All paths and tunable defaults live here.** Env-driven; see the top-level `README.md` for the full table. |
| `backend_api.py` | FastAPI app — REST endpoints (`/manifest.json`, `/latest_run`, `/run_pipeline`, `/save_outputs`, `/download/...`) and a static-file mount at `/` serving `$EDGES_OUTPUT_ROOT`. |
| `run_single_day.py` | The actual EDGES pipeline: receiver cal, S11 modelling, Dicke + linear frontend calibration, antenna temp, per-load actual temp matching. Writes the manifest + per-run npz/jpg trees. |
| `products_api.py` | Read-only `/api/*` endpoints (`/api/night`, `/api/nights/latest`, `/api/quicklook`, `/api/l1`, `/api/housekeeping`, `/api/status`) over the catalog and pipeline products. Needs the optional `edges-catalog`/`edges-pipeline` packages. |
| `tests/` | pytest tests of `products_api.py` on a synthetic catalog. |
| `catalog_inputs.py` | Dates, input files, S11 sessions and probe temperatures for the calibration flow, from the EDGES catalog (nothing scans the raw data tree). |
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
    --cal_date 2026_267 \
    --spec_date 2026_268_12_15_02 \
    --output_root /path/to/outputs --run_dir /path/to/outputs/runs/manual
```

The inputs are resolved in the catalog (`--s11_date` defaults to the
catalog's pick) and written to `<run_dir>/inputs.json`; `--inputs FILE`
reuses such a file. The script prints the input issues and calibration
temperatures, then the linear-frontend calibration. The manifest lands in
`$EDGES_OUTPUT_ROOT/manifest.json`.

## Path configuration cheat sheet

`config.py` resolves every path from environment variables, with
sensible defaults for the SSH cluster. Override any of these:

```bash
export EDGES_PIPELINE_ROOT=/path/to/edges-db   # catalog + products
export EDGES_OUTPUT_ROOT=/path/to/outputs
python -m uvicorn backend_api:app --host 127.0.0.1 --port 8003
```

See the top-level `README.md` for the full env-var table.
