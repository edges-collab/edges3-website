/**
 * Detailed Data View: choose a night, the antenna S11 session and its fit
 * window in the left panel; the receiver calibration is the one selected on
 * the Calibrations tab (the stored pipeline calibration of a day, or one
 * computed with other settings, which runs first if needed). The night's
 * plots appear on the right.
 */
import { useSearchParams } from "react-router"
import { Link } from "react-router"
import { useCalibration } from "../state/CalibrationContext"
import { useRun } from "../hooks/useRun"
import { useJson } from "../hooks/useJson"
import { Issues, NumberField, RunStatusBox, SelectField } from "../components/RunControls"
import ObservationPlots from "../components/ObservationPlots"
import type {
  CalibrationResolved, ObservationInputs, ObservationRequest, ObservationResult, Resolved,
} from "../types/runs"

type Options = { nights: string[]; antenna_s11: string[]; defaults: Record<string, number> }
type ObsResolved = Resolved<ObservationInputs> & { calibration: CalibrationResolved }

const PARAMS: [string, string, number?][] = [
  ["ant_s11_fstart", "Antenna S11 fit from [MHz]", 1],
  ["ant_s11_fstop", "Antenna S11 fit to [MHz]", 1],
  ["ant_s11_nterms", "Antenna S11 model terms"],
]

export default function DataView() {
  const { calibration } = useCalibration()
  const [q, setQ] = useSearchParams()
  const night = q.get("night") ?? "Latest"
  const antS11 = q.get("ant_s11") ?? "Latest"
  const params: Record<string, number> = {}
  for (const [k] of PARAMS) {
    const v = q.get(k)
    if (v !== null && Number.isFinite(Number(v))) params[k] = Number(v)
  }
  const set = (k: string, v: string | null) => {
    const n = new URLSearchParams(q)
    if (v === null || v === "Latest") n.delete(k)
    else n.set(k, v)
    setQ(n)
  }
  const { data: options, error: optError } = useJson<Options>("/api/observations/options")
  const request: ObservationRequest = { night, ant_s11: antS11, params, calibration }
  const { resolved: r, detail, error, start } =
    useRun<ObsResolved, ObservationInputs, ObservationResult>("observation", request)
  const inputs = r?.inputs
  const cal = r?.calibration
  const param = (k: string) => params[k] ?? options?.defaults[k] ?? 0
  const nights = options?.nights ?? []
  const idx = inputs ? nights.indexOf(inputs.night.date) : -1

  return (
    <div className="d-flex gap-3 p-3 align-items-start">
      <aside className="border rounded p-3 side-panel">
        <h2 className="h5">Night</h2>
        {optError && <div className="alert alert-warning small py-1">{optError}</div>}
        <SelectField label="Night (local evening date)" value={night} options={nights}
          autoLabel={`Latest${nights.length ? ` (${nights.at(-1)})` : ""}`} onChange={(v) => set("night", v)} />
        <div className="btn-group btn-group-sm w-100 mb-2">
          <button className="btn btn-outline-primary" disabled={idx <= 0} onClick={() => set("night", nights[idx - 1])}>← Previous</button>
          <button className="btn btn-outline-primary" disabled={idx < 0 || idx >= nights.length - 1}
            onClick={() => set("night", nights[idx + 1])}>Next →</button>
        </div>
        <SelectField label="Antenna S11 session" value={antS11} options={options?.antenna_s11 ?? []}
          autoLabel="Auto (nearest before the night)" onChange={(v) => set("ant_s11", v)} />
        {PARAMS.map(([k, label, step]) => (
          <NumberField key={k} label={label} value={param(k)} step={step} onCommit={(v) => set(k, String(v))} />
        ))}

        <div className="mt-3 small fw-semibold">Calibration</div>
        <div className="small">
          {cal ? (
            <>
              {cal.day}{cal.stored ? `, S11 ${cal.stored.s11_session}` : ""}{" "}
              <span className={`badge ${cal.source === "stored" ? "text-bg-primary" : "text-bg-warning"}`}>
                {cal.source === "stored" ? "stored" : "computed"}
              </span>
              <div className="text-muted">
                config <code>{cal.config_hash.slice(0, 12)}</code>
                {cal.source === "computed" && (cal.status?.state === "done" ? " · ready" : " · not computed yet: runs first (~45 s)")}
                {" · "}<Link to="/calibrations">change on the Calibrations tab</Link>
              </div>
              {cal.unavailable && (
                <div className="alert alert-warning py-1 px-2 mt-1 mb-0">
                  No calibration for {cal.day}: {cal.unavailable}. Choose another day on the Calibrations tab.
                </div>
              )}
            </>
          ) : "…"}
        </div>
        <hr />
        {error && <div className="alert alert-danger small py-1">{error}</div>}
        {inputs && (
          <div className="small">
            <div>
              Night <strong>{inputs.night.date}</strong>: {inputs.files.ant.length} antenna files; S11{" "}
              <strong>{inputs.dates.ant_s11}</strong>
            </div>
            <div className="mt-2"><Issues issues={inputs.issues} /></div>
          </div>
        )}
        {!cal?.unavailable && (
          <RunStatusBox status={r?.status ?? null} detail={detail} onRun={start}
            what="Night" seconds={cal?.source === "computed" && cal.status?.state !== "done" ? "~1.5 min" : "~30 s"} />
        )}
      </aside>

      <section className="flex-grow-1" style={{ minWidth: 0 }}>
        {detail?.status.state === "done" && detail.result ? (
          <ObservationPlots detail={detail} />
        ) : (
          <div className="text-muted p-4 border rounded">
            {detail && (detail.status.state === "queued" || detail.status.state === "running")
              ? "Processing the night; its plots will appear here."
              : cal?.unavailable
                ? `There is no receiver calibration for ${cal.day} (${cal.unavailable}): choose another day on the Calibrations tab.`
                : r
                ? "This night has not been processed with these options yet: click Run on the left."
                : "Resolving the night's inputs in the catalog…"}
          </div>
        )}
      </section>
    </div>
  )
}
