/**
 * What a run with the selected dates would use, before running it:
 * `GET /api/calibration/inputs` resolves the dates in the EDGES catalog and
 * returns the exact input files, the probe temperatures at each
 * calibration / observation time (and where they came from), and the
 * catalog's `issues`: everything that would stop or weaken the calibration.
 */
import { useEffect, useState } from "react"
import { BASE_URL } from "../utils/baseURL"

type Temperature = {
  probe: number
  time: string | null
  reading_time: string | null
  source: "snapshot" | "templog" | "default"
  temperature_k: number
  temperature_c: number
}

type Inputs = {
  dates: { cal: string; s11: string; raw: string }
  files: Record<string, string | null | Record<string, string>> & { s11: Record<string, string> }
  temperatures: Record<string, Temperature>
  recommended_s11: string | null
  hk_coverage: number | null
  issues: string[]
}

const SOURCE_LABEL: Record<Temperature["source"], string> = {
  snapshot: ".tmp snapshot",
  templog: "temperature log",
  default: "fallback constant",
}
const TEMP_LABEL: Record<string, string> = {
  ambient: "Ambient load (at the amb spectrum)",
  hot: "Hot load (at the hot spectrum)",
  lna: "LNA / cable (at the antenna spectrum)",
}

const base = (p: string | null | undefined) => (p ? p.split("/").pop() : "–")
const utc = (iso: string | null) => (iso ? iso.replace("T", " ").slice(0, 19) + " UTC" : "–")

type Props = { dates: { cal: string; s11: string; raw: string } }

export default function InputsPreview({ dates }: Props) {
  const [inputs, setInputs] = useState<Inputs | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    setError(null)
    fetch(`${BASE_URL}/api/calibration/inputs?${new URLSearchParams(dates)}`, { signal: ctrl.signal })
      .then(async (r) => {
        if (!r.ok) {
          const body = await r.json().catch(() => ({}))
          throw new Error(body.detail ?? `HTTP ${r.status}`)
        }
        return r.json() as Promise<Inputs>
      })
      .then((d) => { if (!ctrl.signal.aborted) setInputs(d) })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return
        setInputs(null)
        setError(e instanceof Error ? e.message : String(e))
      })
    return () => ctrl.abort()
  }, [dates])

  return (
    <div className="p-3 border rounded">
      <h1>Inputs</h1>
      {error && <div className="alert alert-warning py-2">Cannot resolve these dates: {error}</div>}
      {inputs && (
        <>
          {inputs.issues.length > 0 ? (
            <div className="alert alert-warning py-2">
              <strong>Issues the catalog found with these inputs:</strong>
              <ul className="mb-0">
                {inputs.issues.map((i) => <li key={i}>{i}</li>)}
              </ul>
            </div>
          ) : (
            <div className="alert alert-success py-2">No issues found with these inputs.</div>
          )}
          <div className="small">
            <div>
              Calibration <strong>{inputs.dates.cal}</strong> · S11 session{" "}
              <strong>{inputs.dates.s11}</strong>
              {inputs.recommended_s11 && inputs.recommended_s11 !== inputs.dates.s11 && (
                <span className="text-muted"> (recommended: {inputs.recommended_s11})</span>
              )}{" "}
              · antenna <strong>{inputs.dates.raw}</strong>
            </div>
            <div className="font-monospace text-muted mt-1">
              {(["amb", "hot", "open", "short", "ant"] as const).map((k) => (
                <div key={k}>{k}: {base(inputs.files[k] as string | null)}</div>
              ))}
              <div>S11: {Object.keys(inputs.files.s11).length} files ({Object.keys(inputs.files.s11).join(", ")})</div>
            </div>
            <table className="table table-sm small mt-2 mb-0">
              <thead>
                <tr><th>Temperature</th><th>Probe</th><th className="text-end">Value</th><th>From</th><th>Reading time</th></tr>
              </thead>
              <tbody>
                {Object.entries(TEMP_LABEL).map(([k, label]) => {
                  const t = inputs.temperatures[k]
                  if (!t) return null
                  return (
                    <tr key={k} className={t.source === "default" ? "table-warning" : ""}>
                      <td>{label}</td>
                      <td>{t.probe}</td>
                      <td className="text-end">{t.temperature_k.toFixed(2)} K</td>
                      <td>{SOURCE_LABEL[t.source]}</td>
                      <td>{utc(t.reading_time)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            {inputs.hk_coverage !== null && (
              <div className="text-muted mt-1">
                Temperature-log coverage during the calibration spectra: {Math.round(100 * inputs.hk_coverage)}%.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
