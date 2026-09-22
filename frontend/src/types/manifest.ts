/**
 * Manifest type definitions — must stay in sync with `io_utils.py` on the
 * backend.
 */

export type PlotType = "s11" | "single" | "multi" | "image" | "heatmap"
export type PageName = "calibration" | "calibrated" | "raw"
export type PlotSource = "user"

export interface RunDates {
  cal?: string
  s11?: string
  raw?: string
}

export interface BasePlot {
  id: string
  type: PlotType
  title: string
  page: PageName
  /** Per-plot dates if the backend attached them (optional). */
  dates?: RunDates
  /** Which pipeline produced this plot. */
  source?: PlotSource
  /** X-axis label with units (e.g. "Frequency [MHz]"). */
  axisx?: string
  /** Y-axis label with units (e.g. "Temperature [K]"). */
  axisy?: string
}

export interface S11Plot extends BasePlot {
  type: "s11"
  filePath: string
}

export interface SinglePlot extends BasePlot {
  type: "single"
  filePath: string
  xKey?: string
  yKey?: string
}

export interface MultiPlot extends BasePlot {
  type: "multi"
  filePath1: string
  filePath2: string
  name1?: string
  name2?: string
}

export interface ImagePlot extends BasePlot {
  type: "image"
  filePath: string
}

export interface HeatmapPlot extends BasePlot {
  type: "heatmap"
  filePath: string
  /** Z-axis label with units for the colour bar. */
  axisz?: string
}

export type Plot = S11Plot | SinglePlot | MultiPlot | ImagePlot | HeatmapPlot

export interface Manifest {
  latest_run: string
  /** 'user' — which pipeline produced this manifest. */
  source?: PlotSource
  dates?: RunDates
  generated_at?: string
  plots: Plot[]
}

export interface ActualTemperature {
  /** ISO-8601 timestamp of when the temperature was sampled. */
  time: string | null
  /** Sampled temperature in Kelvin. */
  temperature_k: number | null
}

export interface S11GridReference {
  type: "s11_grid_reference"
  file: string
  count: number
  range_mhz: [number, number]
}

export interface S11GridMismatch {
  type: "s11_grid_mismatch"
  file: string
  from_count: number
  to_count: number
  from_range_mhz: [number, number]
  to_range_mhz: [number, number]
}

export type S11GridWarning = S11GridReference | S11GridMismatch

export interface LatestRunInfo {
  source: PlotSource | null
  run_id: string | null
  dates: RunDates
  generated_at: string | null
  /** Per-load actual temperatures (ambient/hot/lna) keyed by load name.
   *  These come from the temperature log at the matching calibration time. */
  actual_temperatures?: Record<string, ActualTemperature>
  /** True if any ``_2d.npz`` heatmap files exist in the current source tree. */
  has_2d?: boolean
  /** Non-fatal pipeline warnings (e.g. S11 grid resampling) — empty
   *  array or undefined means the run was clean. */
  warnings?: S11GridWarning[]
}
