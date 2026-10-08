/**
 * The views of a receiver (sub-tabs under the receiver picker), and which
 * ones it has. Addresses are ``/<receiver>/<view>``.
 */
import type { Deployment } from "../types/browse"

export type ViewKey = "record" | "night" | "calibration" | "calibrated"

export const VIEWS: { key: ViewKey; label: string; help: string; needs?: "calibration" }[] = [
  { key: "record", label: "Record", help: "The whole record, per day: when data was taken, how much, and its quality" },
  { key: "night", label: "Night", help: "One night (or a few): the waterfall, files, S11 sessions, housekeeping and weather" },
  { key: "calibration", label: "Calibration", needs: "calibration",
    help: "A day's receiver calibration, stored by the pipeline (or computed with other settings)" },
  { key: "calibrated", label: "Calibrated night", needs: "calibration",
    help: "One night calibrated with the calibration chosen on the Calibration tab" },
]

export const DEFAULT_RECEIVER = "edges3-mro"

export const hasView = (r: Deployment | undefined, v: string): boolean => {
  const view = VIEWS.find((x) => x.key === v)
  return !!view && (!view.needs || !!r?.[view.needs])
}

/** Where a receiver opens: its latest night if the pipeline makes night products for it, else its record. */
export const defaultView = (r: Deployment | undefined): ViewKey => (r?.night_products ? "night" : "record")

export const missingReason = (r: Deployment | undefined, v: ViewKey): string =>
  v === "calibration" || v === "calibrated"
    ? `No receiver calibrations for ${r?.label ?? "this receiver"} yet (edges-pipeline calibrates EDGES-3 only so far)`
    : ""
