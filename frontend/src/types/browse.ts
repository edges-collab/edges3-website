/**
 * The raw-data browse page (backend/browse_api.py). Times are POSIX seconds.
 */
type Num = number | null

/** A receiver; ``band_mhz``: its antenna's band (null: show everything). */
export type Deployment = { name: string; label: string; band_mhz: [number, number] | null }

export type Overview = {
  deployment: string
  label: string
  /** UTC days, every day from the first to the last with data */
  days: string[]
  files?: Num[]
  gb?: Num[]
  hours?: Num[]
  cycles?: Num[]
  drops?: Num[]
  adcmax?: Num[]
  s11_sessions?: Num[]
  /** calibration-load spectra (EDGES-3), counted apart from the antenna's */
  cal_files?: Num[]
  cal_hours?: Num[]
  summary: {
    n_files: number
    n_calibration_files: number
    has_housekeeping: boolean
    n_extracted: number
    n_other_files: number
    tb: number
    hours: number
    n_s11_sessions: number
    t_first_unix: number | null
    t_last_unix: number | null
    days_with_data: number
  } | null
}

export type BrowseFile = {
  file_id: number
  name: string
  load: string | null
  category: string
  stamp_unix: number | null
  size_mb: number
  n_cycles: Num
  t_start_unix: Num
  t_end_unix: Num
  duration_hr: Num
  data_drops: Num
  adcmax: Num
  adcmin: Num
}

/** A catalog S11 session (``session_id``), or files sharing a stamp (null id). */
export type S11Session = { session_id: number | null; stamp_unix: number; kind: string; labels: string[]; name: string }

export type Housekeeping = {
  available: boolean
  reason?: string
  source?: string
  series?: Record<string, { unit: string; time_unix: Num[]; value: Num[] }>
}

export type RangeData = {
  deployment: string
  start_unix: number
  end_unix: number
  files: BrowseFile[]
  s11_sessions: S11Session[]
}

export type Weather = {
  available: boolean
  reason?: string
  site?: string
  time_unix?: Num[]
  series?: Record<string, Num[]>
}

export type Cycles = {
  available: boolean
  name: string
  reason?: string
  time_unix?: Num[]
  adcmax0?: Num[]; adcmax1?: Num[]; adcmax2?: Num[]
  adcmin0?: Num[]; adcmin1?: Num[]; adcmin2?: Num[]
  drops0?: number[]; drops1?: number[]; drops2?: number[]
}

export type S11Traces = {
  deployment: string
  stamp_unix: number
  traces: Record<string, { freq_mhz: Num[]; re: Num[]; im: Num[] } | { error: string }>
}
