/**
 * Response of `GET /api/night` (see backend/products_api.py).
 *
 * Times are POSIX seconds (UTC). `null` in a series marks a gap or a
 * missing value; plots must not interpolate across it.
 */

export type EncodedArray = {
  dtype: "float32"
  shape: number[]
  data: string // base64, little-endian
}

export type NightInfo = {
  date: string // local date of the night's evening (YYYY-MM-DD)
  start_unix: number
  end_unix: number
  deployment: string
  utc_offset_hours: number
  timezone: string
  latest_date: string | null
  is_latest: boolean
}

export type Coverage = {
  stage: string
  config_hash: string
  n_files: number
  t_first_unix: number | null
  t_last_unix: number | null
}

export type QuickLook = {
  available: boolean
  reason: string | null
  n_cycles: number
  n_rows: number // after decimation
  time_unix?: (number | null)[]
  lst_hour?: (number | null)[]
  freq_mhz?: (number | null)[]
  freq_edges_mhz?: (number | null)[]
  waterfall_q?: EncodedArray | null
  waterfall_p0?: EncodedArray | null
  decimation?: number
  files?: string[]
  missing_files?: string[]
  coverage?: Coverage
}

export type BandSeries = {
  time_unix: (number | null)[]
  q_median: (number | null)[]
  p0: (number | null)[]
  p1: (number | null)[]
  p2: (number | null)[]
  q_band_mhz: number[]
  power_band_mhz: number[]
}

export type HousekeepingSeries = {
  name: string
  label: string
  code: number | null
  unit: string | null
  t_unix: (number | null)[]
  value: (number | null)[]
}

export type Badge = { level: "warn" | "info" | "ok"; text: string }

export type FileQA = {
  file_id: number
  name: string
  t_start_unix: number | null
  t_end_unix: number | null
  n_cycles: number | null
  has_l1: boolean
  has_ql: boolean
  total_data_drops: number | null
  n_adc_clip_cycles: number
  rfi_occupancy: number | null
  n_outlier_cycles: number | null
  n_persistent_lines: number | null
  n_nonfinite: number | null
  q_median: number | null
  badges: Badge[]
}

export type NightPayload = {
  night: NightInfo
  quicklook: QuickLook
  band: BandSeries | null
  housekeeping: { series: HousekeepingSeries[]; n_readings: number; gap_s: number } | null
  files: FileQA[]
  events: { adc_clip_unix: number[]; data_drop_unix: number[] }
  thresholds: Record<string, number>
  warnings: string[]
  generated_at: string
  elapsed_s: number
}
