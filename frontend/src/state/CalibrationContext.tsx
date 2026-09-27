/**
 * The calibration selected on the Calibrations tab. The Detailed Data View
 * uses it by default, so both tabs agree. Kept in localStorage per browser.
 */
import { createContext, useContext, useState, type ReactNode } from "react"
import type { CalibrationRequest } from "../types/runs"

const KEY = "edges.calibration"
const DEFAULT: CalibrationRequest = { cal: "Latest", s11: "Latest", params: {} }

function load(): CalibrationRequest {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) ?? "null")
    if (v && typeof v.cal === "string" && typeof v.s11 === "string" && typeof v.params === "object") {
      return v as CalibrationRequest
    }
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
