/**
 * The raw-data browse page (backend/browse_api.py). Times are POSIX seconds.
 */
type Num = number | null

export type Deployment = { name: string; label: string }

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
  summary: {
    n_files: number
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

export type S11Session = { stamp_unix: number; labels: string[]; name: string }

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
