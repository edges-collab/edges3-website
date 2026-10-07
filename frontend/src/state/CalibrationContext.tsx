/**
 * The calibration selected on the Calibrations tab: a day and the pipeline
 * settings that differ from the default (none: the stored calibration). The
 * Detailed Data View uses it, so both tabs agree. Kept in localStorage per
 * browser (a selection saved in an older format is dropped).
 */
import { createContext, useContext, useState, type ReactNode } from "react"
import type { CalibrationRequest } from "../types/runs"

const KEY = "edges.calibration.v2"
const DEFAULT: CalibrationRequest = { day: "Latest", params: {} }

const isSettings = (p: unknown): boolean =>
  !!p && typeof p === "object" && !Array.isArray(p) &&
  Object.values(p).every((s) => !!s && typeof s === "object" && !Array.isArray(s) &&
    Object.values(s).every((v) => typeof v === "number"))

function load(): CalibrationRequest {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) ?? "null")
    if (v && typeof v.day === "string" && isSettings(v.params)) return v as CalibrationRequest
  } catch {
    // unavailable or corrupt storage: use the default
  }
  return DEFAULT
}

type State = { calibration: CalibrationRequest; setCalibration: (c: CalibrationRequest) => void }
const Ctx = createContext<State | null>(null)

export function CalibrationProvider({ children }: { children: ReactNode }) {
  const [calibration, set] = useState<CalibrationRequest>(load)
  const setCalibration = (c: CalibrationRequest) => {
    set(c)
    try {
      window.localStorage.setItem(KEY, JSON.stringify(c))
    } catch {
      // storage unavailable: keep it for this session only
    }
  }
  return <Ctx.Provider value={{ calibration, setCalibration }}>{children}</Ctx.Provider>
}

export function useCalibration(): State {
  const v = useContext(Ctx)
  if (!v) throw new Error("useCalibration outside CalibrationProvider")
  return v
}
