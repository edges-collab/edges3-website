/**
 * Plots of a finished calibration run (backend run_calibration):
 * the modelled S11s of the four loads and the receiver, the noise-wave
 * parameters, the calibrated load temperatures against the known ones,
 * the mean calibration spectra and (on demand) their waterfalls.
 */
import { useState } from "react"
import { useSearchParams } from "react-router"
import StackedPlot, { MUTED, type Panel } from "./StackedPlot"
import Waterfall from "./Waterfall"
import { arr, rows, useNpz } from "../utils/npz"
import { magPhase, robustRange } from "../utils/robust"
import { withBaseUrl } from "../utils/baseURL"
import type { CalibrationResult, RunDetail, CalibrationInputs } from "../types/runs"

const LOADS: [string, string][] = [
  ["amb", "Ambient load"], ["hot", "Hot load"], ["open", "Open cable"], ["short", "Shorted cable"],
  ["lna", "Receiver (LNA)"],
]
const TEMP_LOADS: [string, string][] = [
  ["ambient", "Ambient load"], ["hot_load", "Hot load"], ["open", "Open cable"], ["short", "Shorted cable"],
]
const NW: [string, string][] = [
  ["Tsca", "Scale T_sca"], ["Toff", "Offset T_off"], ["Tunc", "Noise wave T_unc"],
  ["Tcos", "Noise wave T_cos"], ["Tsin", "Noise wave T_sin"],
]

type Props = { detail: RunDetail<CalibrationInputs, CalibrationResult> }

export default function CalibrationPlots({ detail }: Props) {
  const res = detail.result!
  const base = withBaseUrl(detail.base_url)
  const { data, error } = useNpz(base + res.data.plots)
  const [s11Mode, setS11Mode] = useState<"magphase" | "reim">("magphase")
  const [tempMode, setTempMode] = useState<"values" | "residuals">("values")
  const [q] = useSearchParams()
  const [showWf, setShowWf] = useState(q.get("waterfalls") === "1")
  if (error) return <div className="alert alert-danger">{error}</div>
  if (!data) return <div className="text-muted p-3">Loading the calibration…</div>
  const band: [number, number] = [res.params.fstart, res.params.fstop]

  // S11s: one row per load, magnitude|phase (or real|imag) side by side
  const sf = arr(data, "s11_freq")
  const s11Panels: Panel[] = []
  for (const [k, label] of LOADS) {
    const re = arr(data, `s11_${k}_re`), im = arr(data, `s11_${k}_im`)
    if (s11Mode === "magphase") {
      const { db, deg } = magPhase(re, im)
      s11Panels.push({ title: `${label} — |S11|`, yTitle: "dB", traces: [{ x: sf, y: db }] })
      s11Panels.push({ title: `${label} — phase`, yTitle: "deg", traces: [{ x: sf, y: deg }] })
    } else {
      s11Panels.push({ title: `${label} — Re S11`, traces: [{ x: sf, y: re }] })
      s11Panels.push({ title: `${label} — Im S11`, traces: [{ x: sf, y: im }] })
    }
  }

  const nf = arr(data, "nw_freq")
  const nwPanels: Panel[] = NW.map(([k, label]) => ({
    title: label, yTitle: "K", traces: [{ x: nf, y: arr(data, `nw_${k}`) }],
  }))

  const lf = arr(data, "lt_freq")
  const tempPanels: Panel[] = TEMP_LOADS.map(([k, label]) => {
    const cal = arr(data, k === "hot_load" ? "lt_cal_hot_load_delossed" : `lt_cal_${k}`)
    const known = arr(data, k === "hot_load" ? "lt_probe_hot_load" : `lt_known_${k}`)
    const title = k === "hot_load"
      ? `${label} (cable loss removed, G = ${res.hot_load_gain.map((g) => g.toFixed(4)).join("–")}) vs probe`
      : `${label} vs known (ambient probe)`
    if (tempMode === "residuals") {
      const d = cal.map((v, i) => v - known[i])
      return { title: `${title}: calibrated − known`, yTitle: "K", traces: [{ x: lf, y: d, name: "calibrated − known" }],
               yRange: robustRange([d], lf, band) }
    }
    return {
      title, yTitle: "K", yRange: robustRange([cal, known], lf, band),
      traces: [{ x: lf, y: cal, name: "calibrated" }, { x: lf, y: known, name: "known", color: MUTED, dash: "dash" }],
    }
  })

  const spf = arr(data, "spec_freq")
  const specPanels: Panel[] = LOADS.slice(0, 4).map(([k, label]) => {
    const y = arr(data, `spec_q_${k}`)
    return { title: `${label}: mean Q`, traces: [{ x: spf, y }], yRange: robustRange([y], spf, band) }
  })

  const grid = res.s11_grid
  return (
    <div className="d-flex flex-column gap-3">
      {grid?.reference && (
        <div className="alert alert-danger mb-0">
          <strong>Data quality warning — calibration may be erroneous.</strong> The VNA was reconfigured
          mid-session: <code>{grid.reference.file}</code> has {grid.reference.count} points
          ({grid.reference.range_mhz[0]}–{grid.reference.range_mhz[1]} MHz), so {grid.warnings?.length ?? 0} S11
          files were resampled onto it and the calibration is restricted to that range.
        </div>
      )}
      <div className="small text-muted">
        Calibration {res.dates.cal}, S11 session {res.dates.s11}; ambient {res.temperatures.ambient_k.toFixed(2)} K,
        hot {res.temperatures.hot_k.toFixed(2)} K; cterms {res.params.cterms}, wterms {res.params.wterms},{" "}
        {res.params.fstart}–{res.params.fstop} MHz; {res.seconds} s.{" "}
        <a href={withBaseUrl(`/api/runs/calibration/${detail.id}/download`)}>Download (zip)</a>
      </div>

      <section className="border rounded p-2">
        <div className="d-flex align-items-center gap-2">
          <h3 className="h6 m-0">S11 of the calibration loads and the receiver (modelled)</h3>
          <div className="btn-group btn-group-sm ms-auto">
            <button className={`btn ${s11Mode === "magphase" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setS11Mode("magphase")}>|S11| / phase</button>
            <button className={`btn ${s11Mode === "reim" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setS11Mode("reim")}>Re / Im</button>
          </div>
        </div>
        <StackedPlot panels={s11Panels} cols={2} panelHeight={120} />
      </section>

      <section className="border rounded p-2">
        <h3 className="h6">Noise-wave parameters</h3>
        <StackedPlot panels={nwPanels} panelHeight={120} xRange={band} />
      </section>

      <section className="border rounded p-2">
        <div className="d-flex align-items-center gap-2">
          <h3 className="h6 m-0">Calibrated load temperatures</h3>
          <div className="btn-group btn-group-sm ms-auto">
            <button className={`btn ${tempMode === "values" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setTempMode("values")}>Values</button>
            <button className={`btn ${tempMode === "residuals" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setTempMode("residuals")}>Residuals</button>
          </div>
        </div>
        <StackedPlot panels={tempPanels} panelHeight={130} xRange={band} />
      </section>

      <section className="border rounded p-2">
        <div className="d-flex align-items-center gap-2">
          <h3 className="h6 m-0">Calibration spectra</h3>
          <button className="btn btn-sm btn-outline-primary ms-auto" onClick={() => setShowWf((v) => !v)}>
            {showWf ? "Hide waterfalls" : "Show waterfalls (change over time)"}
          </button>
        </div>
        <StackedPlot panels={specPanels} panelHeight={110} xRange={band} />
        {showWf && <CalWaterfalls url={base + res.data.waterfalls} />}
      </section>
    </div>
  )
}

function CalWaterfalls({ url }: { url: string }) {
  const { data, error } = useNpz(url)
  if (error) return <div className="text-danger small">{error}</div>
  if (!data) return <div className="text-muted small">Loading waterfalls…</div>
  const f = arr(data, "freq")
  return (
    <div className="row g-2">
      {LOADS.slice(0, 4).map(([k, label]) => data[`${k}_dev`] && (
        <div key={k} className="col-12 col-xl-6">
          <Waterfall
            title={`${label}: change over time, T_NS·(Q − median) [K]`}
            times={arr(data, `${k}_time_unix`).map((t) => new Date(t * 1000).toISOString().slice(0, 19).replace("T", " "))}
            freqs={f} z={rows(data, `${k}_dev`)} unit="K" diverging height={280} xTitle="UTC"
          />
        </div>
      ))}
    </div>
  )
}
