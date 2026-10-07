/**
 * Calibration / observation runs (see backend/runs_api.py and
 * backend/run_single_day.py).
 */
export type RunKind = "calibration" | "observation"
export type RunState = "queued" | "running" | "done" | "failed"

export type RunStatus = { state: RunState; error?: string; updated_utc?: string }

export type Temperature = {
  probe: number
  time: string | null
  reading_time: string | null
  source: "snapshot" | "templog" | "default"
  temperature_k: number
  temperature_c: number
}

export type ObservationInputs = {
  kind: "observation"
  night: { date: string; start_unix: number; end_unix: number }
  dates: { night: string; ant_s11: string }
  files: {
    ant: { name: string; t_start_unix: number | null; t_end_unix: number | null; n_cycles: number | null;
           temperatures: { obs_ambient: Temperature } }[]
    ant_s11: Record<string, string>
  }
  recommended_ant_s11: string | null
  issues: string[]
}

/** Receiver-calibration settings, by section (edges-pipeline rcal). */
export type CalParams = Record<string, Record<string, number>>

type Arr = (number | null)[]

/** One receiver calibration (backend calibrations.calibration_json). */
export type CalibrationData = {
  source: "stored" | "computed"
  cal_day: string
  s11_session: string | null
  config_hash: string
  product: string | null
  is_default?: boolean
  t_start_unix: number | null
  t_end_unix: number | null
  t_ambient_k: number | null
  t_hot_k: number | null
  rms_k: Record<string, number | null>
  t_load: number
  t_load_ns: number
  method: string | null
  n_readings: Record<string, number> | null
  spectra: Record<string, string[]> | null
  issues: string[]
  config: Record<string, Record<string, unknown>>
  freq_mhz: Arr
  loads: Record<string, { calibrated: Arr | null; known: Arr | null; s11_re: Arr | null; s11_im: Arr | null }>
  hot_load_loss: Arr | null
  nw: { freq_mhz: Arr; Tsca: Arr; Toff: Arr; Tunc: Arr; Tcos: Arr; Tsin: Arr }
  receiver_s11: { re: Arr; im: Arr }
  params?: CalParams
  seconds?: number
}

export type StoredRow = {
  cal_day: string
  s11_session: string
  t_ambient_k: number
  t_hot_k: number
  rms_ambient_k: number | null
  rms_hot_k: number | null
  rms_open_k: number | null
  rms_short_k: number | null
  config_hash: string
}

export type CalField = {
  section: string
  key: string
  label: string
  min: number
  max: number
  integer: boolean
  main: boolean
}

export type CalibrationList = {
  stored: StoredRow[]
  missing: { cal_day: string; reason: string }[]
  note: string
  defaults: Record<string, Record<string, unknown>>
  fields: CalField[]
  default_hash: string | null
  /** the installed edges-pipeline cannot reproduce the stored calibrations */
  version_skew: boolean
  other_configs: { config_hash: string; name: string; created_utc: string; code_version: string; n_done: number }[]
  pipeline: string
}

/** Which calibration a request means (POST /api/calibrations/resolve). */
export type CalibrationResolved = {
  day: string
  params: CalParams
  config_hash: string
  default_hash: string | null
  is_default: boolean
  version_skew: boolean
  pipeline: string
  source: "stored" | "computed"
  id: string | null
  status: RunStatus | null
  stored: StoredRow | null
  unavailable: string | null
}

export type ObservationResult = {
  kind: "observation"
  night: { date: string; start_unix: number; end_unix: number }
  dates: { night: string; ant_s11: string }
  calibration: {
    source: "stored" | "computed"
    cal_day: string
    s11_session: string | null
    config_hash: string
    id: string | null
    params: CalParams
  }
  params: Record<string, number>
  ant_s11_window_mhz: [number, number]
  calibration_band_mhz: [number, number]
  t_load: number
  t_load_ns: number
  files: { name: string; n_cycles_night: number; obs_ambient: Temperature }[]
  n_cycles: number
  issues: string[]
  data: { plots: string; waterfalls: string }
  seconds: number
}

export type RunDetail<I, R> = {
  id: string
  kind: RunKind
  status: RunStatus
  inputs: I
  params: Record<string, unknown>
  result: R | null
  log_tail: string | null
  base_url: string
}

/** What every resolve answers: the run's id (null: nothing to run) and status. */
export type Resolvable = { id: string | null; status: RunStatus | null }

export type Resolved<I> = {
  id: string | null
  params: Record<string, number>
  inputs: I
  status: RunStatus | null
}

export type CalibrationRequest = { day: string; params: CalParams }
export type ObservationRequest = {
  night: string
  ant_s11: string
  params: Record<string, number>
  calibration: CalibrationRequest
}
