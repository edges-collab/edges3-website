/**
 * Calibrations: choose a receiver calibration (day, S11 session, fit
 * parameters) in the left panel; its plots appear on the right as soon as a
 * run with these options exists or finishes. The selection is shared with
 * the Detailed Data View.
 */
import { useEffect, useState } from "react"
import { BASE_URL } from "../utils/baseURL"
import { useCalibration } from "../state/CalibrationContext"
import { useRun } from "../hooks/useRun"
import { Issues, NumberField, RunStatusBox, SelectField, kelvin } from "../components/RunControls"
import CalibrationPlots from "../components/CalibrationPlots"
import type { CalibrationInputs, CalibrationResult, Temperature } from "../types/runs"

type Options = { calibration: string[]; s11: string[]; defaults: Record<string, number> }

const PARAMS: [string, string, number?][] = [
  ["cterms", "cterms (scale/offset terms)"], ["wterms", "wterms (noise-wave terms)"],
  ["fstart", "fstart [MHz]", 1], ["fstop", "fstop [MHz]", 1],
  ["wfstart", "wfstart [MHz]", 1], ["wfstop", "wfstop [MHz]", 1],
]

export function useOptions<T>(path: string): { options: T | null; error: string | null } {
  const [state, setState] = useState<{ options: T | null; error: string | null }>({ options: null, error: null })
  useEffect(() => {
    fetch(`${BASE_URL}${path}`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`)
        return r.json() as Promise<T>
      })
      .then((o) => setState({ options: o, error: null }))
      .catch((e: unknown) => setState({ options: null, error: e instanceof Error ? e.message : String(e) }))
  }, [path])
  return state
}

export function TempLine({ label, t }: { label: string; t: Temperature }) {
  const src = { snapshot: ".tmp snapshot", templog: "temperature log", default: "FALLBACK" }[t.source]
  return (
    <div className={t.source === "default" ? "text-danger" : ""}>
      {label}: {kelvin(t.temperature_k)} <span className="text-muted">(probe {t.probe}, {src})</span>
    </div>
  )
}

export default function Calibrations() {
  const { calibration, setCalibration } = useCalibration()
  const { options, error: optError } = useOptions<Options>("/api/calibrations/options")
  const { resolved, detail, error, start } = useRun<CalibrationInputs, CalibrationResult>("calibration", calibration)
  const inputs = resolved?.inputs
  const param = (k: string) => calibration.params[k] ?? options?.defaults[k] ?? 0
  const setParam = (k: string, v: number) =>
    setCalibration({ ...calibration, params: { ...calibration.params, [k]: v } })

  return (
    <div className="d-flex gap-3 p-3 align-items-start">
      <aside className="border rounded p-3 side-panel">
        <h2 className="h5">Calibration</h2>
        {optError && <div className="alert alert-warning small py-1">{optError}</div>}
        <SelectField
          label="Calibration day (UTC)" value={calibration.cal} options={options?.calibration ?? []}
          autoLabel={`Latest${options?.calibration.length ? ` (${options.calibration.at(-1)})` : ""}`}
          onChange={(v) => setCalibration({ ...calibration, cal: v })}
        />
        <SelectField
          label="S11 session" value={calibration.s11} options={options?.s11 ?? []}
          autoLabel="Auto (recommended for the day)"
          onChange={(v) => setCalibration({ ...calibration, s11: v })}
        />
        <div className="mt-2 mb-1 small fw-semibold">Fit parameters</div>
        {PARAMS.map(([k, label, step]) => (
          <NumberField key={k} label={label} value={param(k)} step={step} onCommit={(v) => setParam(k, v)} />
        ))}
        {Object.keys(calibration.params).length > 0 && (
          <button className="btn btn-link btn-sm p-0" onClick={() => setCalibration({ ...calibration, params: {} })}>
            Reset to defaults
          </button>
        )}
        <hr />
        {error && <div className="alert alert-danger small py-1">{error}</div>}
        {inputs && (
          <div className="small">
            <div>Day <strong>{inputs.dates.cal}</strong>, S11 <strong>{inputs.dates.s11}</strong></div>
            <TempLine label="Ambient load" t={inputs.temperatures.ambient} />
            <TempLine label="Hot load" t={inputs.temperatures.hot} />
            <div className="text-muted mb-2">
              {(["amb", "hot", "open", "short"] as const).map((k) => inputs.files[k]?.split("/").pop()).join(", ")}
            </div>
            <Issues issues={inputs.issues} />
          </div>
        )}
        <RunStatusBox status={resolved?.status ?? null} detail={detail} onRun={start}
          what="Calibration" seconds="~1 min" />
      </aside>

      <section className="flex-grow-1" style={{ minWidth: 0 }}>
        {detail?.status.state === "done" && detail.result ? (
          <CalibrationPlots detail={detail} />
        ) : (
          <div className="text-muted p-4 border rounded">
            {detail && (detail.status.state === "queued" || detail.status.state === "running")
              ? "The calibration is running; its plots will appear here."
              : resolved
                ? "No calibration with these options yet: click Run on the left (about a minute). Its plots appear here."
                : "Resolving the calibration inputs in the catalog…"}
          </div>
        )}
      </section>
    </div>
  )
}
