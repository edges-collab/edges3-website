/**
 * Plots of one receiver calibration (backend calibrations.calibration_json):
 * the calibrated load temperatures against the known ones (residuals by
 * default), the noise-wave parameters, the modelled S11 of the loads and the
 * receiver, the hot-load loss, and (rcal v3+) each load's mean Q spectrum and
 * the receiver temperature during it, (rcal v4+) the mean R of the four
 * loads, which should agree (a difference is receiver gain drift), and each
 * load's band-median Q per cycle over time (L1). Optionally a second stored configuration
 * of the same day is overlaid (e.g. the old Alan-mode products). Arrays come
 * at full resolution (3072 channels) and are thinned for plotting only.
 */
import { useState } from "react"
import StackedPlot, { MUTED, SERIES, type Panel, type Trace } from "./StackedPlot"
import { magPhase, robustRange } from "../utils/robust"
import { useJson } from "../hooks/useJson"
import type { CalibrationData, LoadCycles } from "../types/runs"

const LOADS: [string, string][] = [
  ["ambient", "Ambient load"], ["hot_load", "Hot load"], ["open", "Open cable"], ["short", "Shorted cable"],
]
const NW: [keyof CalibrationData["nw"], string][] = [
  ["Tsca", "Scale T_sca"], ["Toff", "Offset T_off"], ["Tunc", "Noise wave T_unc"],
  ["Tcos", "Noise wave T_cos"], ["Tsin", "Noise wave T_sin"],
]
const MAX_POINTS = 1000
const CRITICAL = "#d03b3b"
/** Receiver temperatures across the loads further apart than this are flagged. */
const FRONTEND_SPREAD_C = 2
const CYCLE_LOADS: [string, string, string][] = [
  ["amb", "ambient", "Ambient load"], ["hot", "hot_load", "Hot load"],
  ["open", "open", "Open cable"], ["short", "short", "Shorted cable"],
]

const nums = (a: (number | null)[] | null | undefined): number[] =>
  (a ?? []).map((v) => (v === null ? NaN : v))

/** Every k-th point, so a trace has at most MAX_POINTS (plotting only). */
function thin(x: number[], y: number[]): [number[], number[]] {
  const k = Math.max(1, Math.ceil(x.length / MAX_POINTS))
  if (k === 1) return [x, y]
  return [x.filter((_, i) => i % k === 0), y.filter((_, i) => i % k === 0)]
}

function trace(x: number[], y: number[], extra: Partial<Trace> = {}): Trace {
  const [tx, ty] = thin(x, y)
  return { x: tx, y: ty, ...extra }
}

const label = (d: CalibrationData) =>
  d.source === "computed" ? "computed" : d.is_default === false ? `config ${d.config_hash.slice(0, 8)}` : "stored"

type Props = { data: CalibrationData; compare?: CalibrationData | null }

export default function CalibrationPlots({ data, compare }: Props) {
  const [tempMode, setTempMode] = useState<"residuals" | "values">("residuals")
  const [s11Mode, setS11Mode] = useState<"magphase" | "reim">("magphase")
  const { data: cycles, error: cyclesError } = useJson<LoadCycles>(
    data.t_start_unix !== null && data.t_end_unix !== null
      ? `/api/calibrations/cycles?start=${data.t_start_unix}&end=${data.t_end_unix}` : null)
  const f = nums(data.freq_mhz)
  const band: [number, number] = [Math.min(...f), Math.max(...f)]
  const main = label(data)
  const other = compare ? label(compare) : ""
  const cf = compare ? nums(compare.freq_mhz) : []

  const residual = (d: CalibrationData, k: string) => {
    const c = nums(d.loads[k]?.calibrated), kn = nums(d.loads[k]?.known)
    return c.map((v, i) => v - kn[i])
  }
  const tempPanels: Panel[] = LOADS.map(([k, name]) => {
    const rms = data.rms_k[k]
    const what = k === "hot_load"
      ? "known = probe through the cable loss"
      : k === "ambient" ? "known = ambient probe" : "known = ambient probe (cable at ambient)"
    const title = `${name}${rms !== null && rms !== undefined ? ` — RMS ${rms.toFixed(3)} K` : ""} (${what})`
    if (tempMode === "residuals") {
      const r = residual(data, k)
      const traces = [trace(f, r, { name: main })]
      if (compare) traces.push(trace(cf, residual(compare, k), { name: other, color: SERIES[1], dash: "dash" }))
      return { title: `${title}: calibrated − known`, yTitle: "K", traces,
               yRange: robustRange(traces.map((t) => t.y), traces[0].x, band, 0.01, 0.99) }
    }
    const c = nums(data.loads[k]?.calibrated), kn = nums(data.loads[k]?.known)
    const traces = [trace(f, c, { name: `calibrated (${main})` }),
                    trace(f, kn, { name: "known", color: MUTED, dash: "dash" })]
    if (compare) traces.push(trace(cf, nums(compare.loads[k]?.calibrated), { name: `calibrated (${other})`, color: SERIES[1] }))
    return { title, yTitle: "K", traces, yRange: robustRange([c, kn], f, band, 0.01, 0.99) }
  })

  const nf = nums(data.nw.freq_mhz)
  const cnf = compare ? nums(compare.nw.freq_mhz) : []
  const nwPanels: Panel[] = NW.map(([k, name]) => {
    const traces = [trace(nf, nums(data.nw[k]), { name: main })]
    if (compare) traces.push(trace(cnf, nums(compare.nw[k]), { name: other, color: SERIES[1], dash: "dash" }))
    return { title: name, yTitle: "K", traces }
  })

  // S11 models: one row per load and the receiver, |S11|/phase (or Re/Im) side by side
  const s11Rows: [string, (number | null)[] | null, (number | null)[] | null, number[]][] = [
    ...LOADS.map(([k, name]) => [name, data.loads[k]?.s11_re ?? null, data.loads[k]?.s11_im ?? null, f] as
      [string, (number | null)[] | null, (number | null)[] | null, number[]]),
    ["Receiver (LNA)", data.receiver_s11.re, data.receiver_s11.im, nf],
  ]
  const s11Panels: Panel[] = []
  for (const [name, reA, imA, x] of s11Rows) {
    if (!reA || !imA) continue
    const re = nums(reA), im = nums(imA)
    if (s11Mode === "magphase") {
      const { db, deg } = magPhase(re, im)
      s11Panels.push({ title: `${name} — |S11|`, yTitle: "dB", traces: [trace(x, db)] })
      s11Panels.push({ title: `${name} — phase`, yTitle: "deg", traces: [trace(x, deg)] })
    } else {
      s11Panels.push({ title: `${name} — Re S11`, traces: [trace(x, re)] })
      s11Panels.push({ title: `${name} — Im S11`, traces: [trace(x, im)] })
    }
  }

  const loss = data.hot_load_loss ? nums(data.hot_load_loss) : null

  // each load's mean Q (rcal v3+), titled with the receiver temperature then
  const fe = (k: string) => data.loads[k]?.frontend_c ?? null
  const qPanels: Panel[] = LOADS.filter(([k]) => data.loads[k]?.q).map(([k, name]) => {
    const y = nums(data.loads[k].q)
    const t = fe(k)
    return {
      title: `${name}: mean Q${t ? ` — receiver ${t.mean.toFixed(1)} °C (${t.min.toFixed(1)}–${t.max.toFixed(1)})` : ""}`,
      traces: [trace(f, y)], yRange: robustRange([y], f, band, 0.01, 0.99),
    }
  })
  // mean R = P_L / (P_LNS − P_L) of each load (rcal v4+): the receiver's own
  // ratio, so the four should agree; shown with each load's difference from
  // the ambient load's
  const rLoads = LOADS.filter(([k]) => data.loads[k]?.r)
  const rAmb = data.loads.ambient?.r ? nums(data.loads.ambient.r) : null
  const R_COLORS = [...SERIES, MUTED]
  const rPanels: Panel[] = rLoads.length === 0 ? [] : [
    { title: "Mean R = P_L / (P_LNS − P_L)", traces: rLoads.map(([k, name], i) =>
      trace(f, nums(data.loads[k].r), { name, color: R_COLORS[i] })) },
    ...(rAmb ? [{
      title: "R − R(ambient load): receiver gain drift between the spectra", traces: rLoads.slice(1).map(([k, name], i) => {
        const r = nums(data.loads[k].r)
        return trace(f, r.map((v, j) => v - rAmb[j]), { name, color: R_COLORS[i + 1] })
      }),
    }] : []),
  ]
  const feMeans = LOADS.map(([k]) => fe(k)?.mean).filter((v): v is number => v !== undefined)
  const feSpread = feMeans.length > 1 ? Math.max(...feMeans) - Math.min(...feMeans) : 0

  // band-median Q per cycle over the day (L1), flagged cycles in red
  const day0 = data.t_start_unix !== null ? Math.floor(data.t_start_unix / 86400) * 86400 : 0
  const cyclePanels: Panel[] = []
  for (const [k, , name] of CYCLE_LOADS) {
    const c = cycles?.[k]
    if (!c) continue
    const x = nums(c.time_unix).map((t) => (t - day0) / 3600)
    const y = nums(c.band_median_q)
    const traces: Trace[] = [{ x, y, name: "band-median Q (60–90 MHz)", mode: "markers" }]
    const bad = c.flagged.map((b, i) => (b ? i : -1)).filter((i) => i >= 0)
    if (bad.length) {
      traces.push({ x: bad.map((i) => x[i]), y: bad.map((i) => y[i]), name: "dropout/outlier",
                    mode: "markers", color: CRITICAL })
    }
    cyclePanels.push({ title: `${name}: ${y.length} cycles${bad.length ? `, ${bad.length} flagged` : ""}`, traces })
  }
  const t = (s: number | null) => (s === null ? "–" : new Date(s * 1000).toISOString().slice(0, 16).replace("T", " "))

  return (
    <div className="d-flex flex-column gap-3">
      <div className="small">
        <strong>{data.source === "stored" ? "Stored pipeline calibration" : "Computed with these settings"}</strong>{" "}
        of {data.cal_day} (spectra {t(data.t_start_unix)}–{t(data.t_end_unix)} UTC), S11 session{" "}
        {data.s11_session ?? "–"}; ambient {data.t_ambient_k?.toFixed(2)} K, hot {data.t_hot_k?.toFixed(2)} K
        {data.n_readings ? ` (${data.n_readings.ambient}/${data.n_readings.hot} probe readings)` : ""};{" "}
        {data.method ?? "edges.cal"}, config <code>{data.config_hash.slice(0, 12)}</code>
        {data.product && <> (<span className="font-monospace">{data.product}</span>)</>}
        {data.seconds !== undefined && <>; computed in {data.seconds} s</>}.
        {compare && <> Dashed orange: the stored configuration <code>{compare.config_hash.slice(0, 12)}</code> ({compare.method ?? "?"}).</>}
        {" "}
        <details className="d-inline">
          <summary className="d-inline text-primary" style={{ cursor: "pointer" }}>Settings</summary>
          <pre className="small bg-light border rounded p-2 mt-1 mb-0">{JSON.stringify(data.config, null, 1)}</pre>
        </details>
      </div>

      <section className="border rounded p-2">
        <div className="d-flex align-items-center gap-2">
          <h3 className="h6 m-0">Calibrated load temperatures</h3>
          <div className="btn-group btn-group-sm ms-auto">
            <button className={`btn ${tempMode === "residuals" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setTempMode("residuals")}>Residuals</button>
            <button className={`btn ${tempMode === "values" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setTempMode("values")}>Values</button>
          </div>
        </div>
        <p className="small text-muted mb-1">
          The ambient and hot loads are in the fit, so their residuals are not an independent check; the
          open and shorted cables test the noise-wave model.
        </p>
        <StackedPlot panels={tempPanels} panelHeight={130} xRange={band} />
      </section>

      <section className="border rounded p-2">
        <h3 className="h6">Noise-wave parameters</h3>
        <StackedPlot panels={nwPanels} panelHeight={120} xRange={band} />
      </section>

      <section className="border rounded p-2">
        <div className="d-flex align-items-center gap-2">
          <h3 className="h6 m-0">S11 of the calibration loads and the receiver (modelled)</h3>
          <div className="btn-group btn-group-sm ms-auto">
            <button className={`btn ${s11Mode === "magphase" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setS11Mode("magphase")}>|S11| / phase</button>
            <button className={`btn ${s11Mode === "reim" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setS11Mode("reim")}>Re / Im</button>
          </div>
        </div>
        <StackedPlot panels={s11Panels} cols={2} panelHeight={120} xRange={band} />
      </section>

      {qPanels.length > 0 && (
        <section className="border rounded p-2">
          <h3 className="h6">Calibration spectra</h3>
          <p className="small text-muted mb-1">
            Each load's mean Q, exactly as used in the fit, and the receiver (front-end) temperature
            during its spectrum. Below, the mean R = P_L / (P_LNS − P_L) of the three-position switch
            (rcal version 4 and later): it depends on the receiver's internal load and noise source,
            not the input, so the four loads should agree; a difference means the receiver's gain
            drifted between their spectra. R rises below ~50 MHz (to ~1.25 at 40 MHz), the same
            for all four loads and on every day since 2022: a known effect of an out-of-band noise
            source, which the R − R(ambient) panel removes. The calibration's deflection is
            smaller below ~45 MHz, so Q is noisier there.
          </p>
          {feSpread > FRONTEND_SPREAD_C && (
            <div className="alert alert-warning py-1 px-2 small mb-1">
              The receiver temperature differed by {feSpread.toFixed(1)} °C between the loads' spectra
              (mean {feMeans.map((v) => v.toFixed(1)).join(", ")} °C): the thermal control did not hold it, which
              can bias the open and shorted cables (e.g. 2026_278, edges-database DATA_ISSUES #34).
            </div>
          )}
          <StackedPlot panels={qPanels} panelHeight={110} xRange={band} />
          {rPanels.length > 0 && (
            <StackedPlot panels={rPanels} panelHeight={140} xRange={band} />
          )}
        </section>
      )}

      <section className="border rounded p-2">
        <h3 className="h6">Change over time</h3>
        <p className="small text-muted mb-1">
          Band-median Q (60–90 MHz) of each cycle of the loads' spectra, from the L1 products;
          dropouts and outliers in red.
        </p>
        {cyclesError ? <div className="small text-muted">{cyclesError}</div>
          : !cycles ? <div className="small text-muted">Loading the cycles…</div>
            : cyclePanels.length === 0 ? <div className="small text-muted">No L1 products for these spectra.</div>
              : <StackedPlot panels={cyclePanels} panelHeight={100}
                  xTitle={`Hours (UTC) from ${new Date(day0 * 1000).toISOString().slice(0, 10)}`} />}
      </section>

      {loss && (
        <section className="border rounded p-2">
          <h3 className="h6">Hot-load cable loss</h3>
          <p className="small text-muted mb-1">
            Available gain G of the hot-load cable (UT-141C-SP model): the receiver sees
            G·T_hot + (1 − G)·T_ambient.
          </p>
          <StackedPlot panels={[{ title: "Hot-load loss G", traces: [trace(f, loss)] }]} panelHeight={140} xRange={band} />
        </section>
      )}
    </div>
  )
}
