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

export type CalibrationInputs = {
  kind: "calibration"
  dates: { cal: string; s11: string }
  files: Record<string, string | null> & { s11: Record<string, string> }
  temperatures: { ambient: Temperature; hot: Temperature }
  hk_coverage: number | null
  recommended_s11: string | null
  issues: string[]
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

export type S11GridDoc = {
  reference?: { file: string; count: number; range_mhz: [number, number] }
  warnings?: { file: string; from_count: number; to_count: number; from_range_mhz: [number, number] }[]
}

export type CalibrationResult = {
  kind: "calibration"
  dates: { cal: string; s11: string }
  params: Record<string, number>
  temperatures: { ambient_k: number; hot_k: number }
  t_load: number
  t_load_ns: number
  hot_load_gain: [number, number]
  n_cycles: Record<string, number>
  s11_grid: S11GridDoc
  issues: string[]
  data: { plots: string; waterfalls: string }
  seconds: number
}

export type ObservationResult = {
  kind: "observation"
  night: { date: string; start_unix: number; end_unix: number }
  dates: { night: string; ant_s11: string }
  calibration: { id: string; dates: { cal: string; s11: string }; params: Record<string, number> }
  params: Record<string, number>
  ant_s11_window_mhz: [number, number]
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
  params: Record<string, number>
  result: R | null
  log_tail: string | null
  base_url: string
}

export type Resolved<I> = {
  id: string
  params: Record<string, number>
  inputs: I
  status: RunStatus | null
}

export type CalibrationRequest = { cal: string; s11: string; params: Record<string, number> }
export type ObservationRequest = {
  night: string
  ant_s11: string
  params: Record<string, number>
  calibration: CalibrationRequest
}
