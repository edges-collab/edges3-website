/**
 * Plots of a finished observation run (backend run_observation): the
 * night-mean spectra (P_ant, P_load, P_LNS, Q, R, uncalibrated T), the
 * antenna calibration (a, b, calibrated T, within the antenna S11 fit
 * window), the antenna S11 and, on demand, waterfalls. Default axis limits
 * keep the central 90% of the values inside the calibration band, so RFI and
 * out-of-band noise do not hide the signal (double-click to autoscale).
 */
import { useState } from "react"
import { useSearchParams } from "react-router"
import StackedPlot, { MUTED, type Panel } from "./StackedPlot"
import Waterfall from "./Waterfall"
import { arr, rows, useNpz } from "../utils/npz"
import { magPhase, robustRange } from "../utils/robust"
import { withBaseUrl } from "../utils/baseURL"
import { toSiteTime } from "../utils/nightData"
import type { ObservationInputs, ObservationResult, RunDetail } from "../types/runs"

type Props = { detail: RunDetail<ObservationInputs, ObservationResult> }

const UTC_OFFSET_H = 8 // AWST (the MRO)

export default function ObservationPlots({ detail }: Props) {
  const res = detail.result!
  const base = withBaseUrl(detail.base_url)
  const { data, error } = useNpz(base + res.data.plots)
  const [q] = useSearchParams()
  const [showWf, setShowWf] = useState(q.get("waterfalls") === "1")
  if (error) return <div className="alert alert-danger">{error}</div>
  if (!data) return <div className="text-muted p-3">Loading the night…</div>
  const cp = res.calibration.params
  const band: [number, number] = [cp.fstart, cp.fstop]
  const win = res.ant_s11_window_mhz
  const winView: [number, number] = [win[0] - 5, win[1] + 5]

  const f = arr(data, "freq")
  const spec = (key: string, title: string, yTitle?: string): Panel => {
    const y = arr(data, key)
    return { title, yTitle, traces: [{ x: f, y }], yRange: robustRange([y], f, band) }
  }
  const spectra: Panel[] = [
    spec("mean_pant", "Mean P_ant", "arb."),
    spec("mean_pload", "Mean P_load", "arb."),
    spec("mean_plns", "Mean P_LNS", "arb."),
    spec("mean_q", "Q = (P_ant − P_load) / (P_LNS − P_load)"),
    spec("mean_r", "R = P_load / (P_LNS − P_load)"),
    spec("mean_tuncal", `Uncalibrated T = ${res.t_load_ns} Q + ${res.t_load}`, "K"),
  ]

  const abf = arr(data, "ab_freq")
  const tcal = arr(data, "mean_tcal")
  const a = arr(data, "a"), b = arr(data, "b")
  const calPanels: Panel[] = [
    { title: "Scale temperature a", yTitle: "K", traces: [{ x: abf, y: a }], yRange: robustRange([a], abf, win) },
    { title: "Offset temperature b", yTitle: "K", traces: [{ x: abf, y: b }], yRange: robustRange([b], abf, win) },
    { title: "Calibrated temperature T_cal = a Q + b (night mean)", yTitle: "K", traces: [{ x: f, y: tcal }],
      yRange: robustRange([tcal], f, win) },
  ]

  const sf = arr(data, "ant_s11_freq"), mf = arr(data, "ant_s11_meas_freq")
  const model = magPhase(arr(data, "ant_s11_re"), arr(data, "ant_s11_im"))
  const meas = magPhase(arr(data, "ant_s11_meas_re"), arr(data, "ant_s11_meas_im"))
  const s11Panels: Panel[] = [
    { title: "Antenna |S11|", yTitle: "dB", traces: [
      { x: mf, y: meas.db, name: "measured", mode: "markers", color: MUTED },
      { x: sf, y: model.db, name: "model" }] },
    { title: "Antenna S11 phase", yTitle: "deg", traces: [
      { x: mf, y: meas.deg, name: "measured", mode: "markers", color: MUTED },
      { x: sf, y: model.deg, name: "model" }] },
  ]
  const shade = { range: win as [number, number], label: `antenna S11 fit ${win[0]}–${win[1]} MHz` }

  return (
    <div className="d-flex flex-column gap-3">
      <div className="small text-muted">
        Night of {res.night.date}: {res.n_cycles} cycles from {res.files.length} files; calibration{" "}
        {res.calibration.dates.cal} (S11 {res.calibration.dates.s11}); antenna S11 {res.dates.ant_s11};{" "}
        {res.seconds} s. <a href={withBaseUrl(`/api/runs/observation/${detail.id}/download`)}>Download (zip)</a>
      </div>

      <section className="border rounded p-2">
        <h3 className="h6">Spectra (mean over the night)</h3>
        <StackedPlot panels={spectra} panelHeight={130} xRange={band} />
      </section>

      <section className="border rounded p-2">
        <h3 className="h6">Calibration of the antenna</h3>
        <p className="small text-muted mb-1">
          Defined only where the antenna S11 model is (the shaded fit window, set on the left): outside it
          the antenna is poorly matched (|S11| up to ~0.9) and the calibration is not meaningful.
        </p>
        <StackedPlot panels={calPanels} panelHeight={150} xRange={winView} shade={shade} />
      </section>

      <section className="border rounded p-2">
        <h3 className="h6">Antenna S11</h3>
        <StackedPlot panels={s11Panels} cols={2} panelHeight={200} shade={shade} />
      </section>

      <section className="border rounded p-2">
        <div className="d-flex align-items-center">
          <h3 className="h6 m-0">Waterfalls</h3>
          <button className="btn btn-sm btn-outline-primary ms-auto" onClick={() => setShowWf((v) => !v)}>
            {showWf ? "Hide" : "Show Q and T_cal waterfalls"}
          </button>
        </div>
        {showWf && <ObsWaterfalls url={base + res.data.waterfalls} win={win} />}
      </section>

      <section className="border rounded p-2">
        <h3 className="h6">Files</h3>
        <table className="table table-sm small mb-0">
          <thead><tr><th>File</th><th className="text-end">Cycles in the night</th><th className="text-end">Ambient probe</th></tr></thead>
          <tbody>
            {res.files.map((x) => (
              <tr key={x.name}>
                <td className="font-monospace">{x.name}</td>
                <td className="text-end">{x.n_cycles_night}</td>
                <td className="text-end">{x.obs_ambient.source === "default" ? "–" : `${x.obs_ambient.temperature_k.toFixed(2)} K`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}

function ObsWaterfalls({ url, win }: { url: string; win: [number, number] }) {
  const { data, error } = useNpz(url)
  if (error) return <div className="text-danger small">{error}</div>
  if (!data) return <div className="text-muted small">Loading waterfalls…</div>
  const times = arr(data, "time_unix").map((t) => toSiteTime(t, UTC_OFFSET_H))
  const f = arr(data, "freq")
  const inWin = f.map((x) => x >= win[0] && x <= win[1])
  const tc = rows(data, "tcal").map((r) => r.filter((_, j) => inWin[j]))
  return (
    <>
      <Waterfall title="Q" times={times} freqs={f} z={rows(data, "q")} unit="Q" xTitle="Site time (AWST)" />
      <Waterfall title="Calibrated temperature (antenna S11 fit window)" times={times}
        freqs={f.filter((_, j) => inWin[j])} z={tc} unit="K" xTitle="Site time (AWST)" />
    </>
  )
}
