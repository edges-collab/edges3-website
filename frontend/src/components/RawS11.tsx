/**
 * One load's unaveraged S11 repeats in a raw session (EDGES-3 records 50
 * per load since 2026-08, over ~2 hours; 25 in full sessions), as measured
 * (uncalibrated), to show what the averaged session hides:
 *
 * - **States.** Repeats more than STATE_TOL apart (RMS |ΔΓ| over the band)
 *   are in different states. The antenna has been seen flipping between its
 *   usual S11 and near total reflection within a session (2026-10-11, -13),
 *   which the averaged file then mixes. The largest state is the "main" one;
 *   repeats in any other state are red.
 * - **Glitches.** Within a state, a repeat's step from the mean of its
 *   neighbours in that state (slow drifts cancel) over GLITCH_FACTOR times
 *   the typical step: orange.
 *
 * Against frequency: every repeat's |S11|, phase and |Γ − the main state's
 * median|, coloured by the order taken. Against time: each repeat's RMS
 * difference from the main state's median (drifts, states) and its step
 * within its state (glitches).
 */
import { useMemo, useState } from "react"
import type { Data, Layout } from "plotly.js"
import Plot from "../utils/plotComponent"
import TimeStrips, { clock, type Strip } from "./TimeStrips"
import { MUTED } from "./StackedPlot"
import { useJson } from "../hooks/useJson"
import type { RawS11, S11Session } from "../types/browse"

const CRITICAL = "#d03b3b"
const SERIOUS = "#ec835a"
const GRID = "#e6e6e3"
const GLITCH_FACTOR = 5
/** RMS |ΔΓ| beyond which two repeats are in different states (drifts are
 * ~0.002 over a session, glitches ~0.01; the antenna's flip ~1). */
const STATE_TOL = 0.05
/** The band where each state's level is summarised (MHz). */
const LEVEL_BAND: [number, number] = [50, 100]
// viridis, for the order the repeats were taken in
const VIRIDIS = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]

function viridis(u: number): string {
  const x = Math.min(1, Math.max(0, u)) * (VIRIDIS.length - 1)
  const i = Math.min(VIRIDIS.length - 2, Math.floor(x))
  const f = x - i
  const a = VIRIDIS[i], b = VIRIDIS[i + 1]
  const ch = (h: string, k: number) => parseInt(h.slice(1 + 2 * k, 3 + 2 * k), 16)
  const mix = [0, 1, 2].map((k) => Math.round(ch(a, k) + f * (ch(b, k) - ch(a, k))))
  return `rgb(${mix.join(",")})`
}

const median = (v: number[]) => {
  const s = v.filter(Number.isFinite).sort((a, b) => a - b)
  if (!s.length) return NaN
  const m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}
const rms = (v: number[]) => Math.sqrt(v.reduce((a, x) => a + x * x, 0) / Math.max(1, v.length))
const num = (v: number | null) => (v === null ? NaN : v)

export default function RawS11View({ dep, session, off, tz }: {
  dep: string; session: S11Session; off: number; tz: string
}) {
  const [label, setLabel] = useState(session.labels.includes("ant") ? "ant" : session.labels[0])
  const { data: raw, error } = useJson<RawS11>(
    session.raw_session_id === null ? null
      : `/api/browse/${dep}/s11/raw?raw_session_id=${session.raw_session_id}&label=${encodeURIComponent(label)}`)
  const at = useMemo(() => clock(off), [off])

  const view = useMemo(() => {
    if (!raw || raw.repeats.length === 0) return null
    const f = raw.freq_mhz.map(num)
    const re = raw.re.map((r) => r.map(num)), im = raw.im.map((r) => r.map(num))
    const n = re.length
    const dist = (i: number, k: number) => rms(re[i].map((x, j) => Math.hypot(x - re[k][j], im[i][j] - im[k][j])))
    // states: each repeat joins the first state whose first repeat is within STATE_TOL
    const firsts: number[] = [], state: number[] = []
    for (let i = 0; i < n; i++) {
      let s = firsts.findIndex((k) => dist(i, k) < STATE_TOL)
      if (s < 0) { s = firsts.length; firsts.push(i) }
      state.push(s)
    }
    const counts = firsts.map((_, s) => state.filter((x) => x === s).length)
    const main = counts.indexOf(Math.max(...counts))
    const members = (s: number) => state.flatMap((x, i) => (x === s ? [i] : []))
    const medianOf = (idx: number[]) => ({
      re: f.map((_, j) => median(idx.map((i) => re[i][j]))), im: f.map((_, j) => median(idx.map((i) => im[i][j]))),
    })
    const m = medianOf(members(main))
    const dev = re.map((r, i) => r.map((x, j) => Math.hypot(x - m.re[j], im[i][j] - m.im[j])))
    const fromMain = dev.map(rms)
    // the step from the mean of its neighbours in the same state (one neighbour at the ends)
    const step = re.map((r, i) => {
      let a = i - 1, b = i + 1
      while (a >= 0 && state[a] !== state[i]) a--
      while (b < n && state[b] !== state[i]) b++
      const nb = [a, b].filter((k) => k >= 0 && k < n)
      if (!nb.length) return NaN
      return rms(r.map((x, j) => Math.hypot(x - nb.reduce((t, k) => t + re[k][j], 0) / nb.length,
        im[i][j] - nb.reduce((t, k) => t + im[k][j], 0) / nb.length)))
    })
    const typical = median(step)
    const glitch = step.map((v) => n > 2 && v > GLITCH_FACTOR * typical)
    const other = state.map((x) => x !== main)
    const colour = re.map((_, i) => (other[i] ? CRITICAL : glitch[i] ? SERIOUS : viridis(n > 1 ? i / (n - 1) : 0)))
    const db = re.map((r, i) => r.map((x, j) => 20 * Math.log10(Math.hypot(x, im[i][j]))))
    const deg = re.map((r, i) => r.map((x, j) => (Math.atan2(im[i][j], x) * 180) / Math.PI))
    const inBand = f.map((x) => x >= LEVEL_BAND[0] && x <= LEVEL_BAND[1])
    const states = firsts.map((_, s) => {
      const md = medianOf(members(s))
      const lv = md.re.map((x, j) => 20 * Math.log10(Math.hypot(x, md.im[j]))).filter((_, j) => inBand[j])
      return { count: counts[s], level_db: lv.reduce((t, x) => t + x, 0) / Math.max(1, lv.length), repeats: members(s), main: s === main }
    })
    return { f, n, dev, step, typical, glitch, other, colour, db, deg, fromMain, states }
  }, [raw])

  const figure = useMemo(() => {
    if (!raw || !view) return null
    const panels = [
      { key: "db", title: "|S11| [dB]", rows: view.db, log: false },
      { key: "deg", title: "Phase [deg]", rows: view.deg, log: false },
      { key: "dev", title: "|Γ − main state's median|", rows: view.dev, log: true },
    ]
    const gap = 0.06, h = (1 - 2 * gap) / 3
    const L: Record<string, unknown> = {
      autosize: true, height: 620, margin: { l: 70, r: 90, t: 20, b: 50 }, hovermode: "closest", showlegend: false,
      plot_bgcolor: "#fcfcfb", paper_bgcolor: "#fcfcfb", font: { size: 11, color: "#0b0b0b" },
      xaxis: { title: { text: "Frequency [MHz]" }, gridcolor: GRID, anchor: "y3" },
    }
    const data: Data[] = []
    panels.forEach((p, k) => {
      const top = 1 - k * (h + gap)
      L[k === 0 ? "yaxis" : `yaxis${k + 1}`] = {
        domain: [top - h, top], title: { text: p.title, font: { size: 11 } }, gridcolor: GRID,
        type: p.log ? "log" : "linear", exponentformat: "power", zeroline: false,
      }
      p.rows.forEach((y, i) => {
        const r = raw.repeats[i]
        // glitches last, so they are drawn on top
        data.push({
          type: "scatter", mode: "lines", x: view.f, y, xaxis: "x", yaxis: k === 0 ? "y" : `y${k + 1}`,
          line: { color: view.colour[i], width: view.other[i] || view.glitch[i] ? 2 : 1 },
          name: `repeat ${r.index}`,
          hovertemplate: `repeat ${r.index}${r.time_unix ? `, ${(at(r.time_unix) ?? "").slice(11, 16)}` : ""}`
            + `<br>%{x:.2f} MHz: %{y:.5g}<extra></extra>`,
        } as Data)
      })
    })
    data.sort((a, b) => Number((a as { line: { width: number } }).line.width) - Number((b as { line: { width: number } }).line.width))
    // the colour scale (order taken), as a colour bar
    data.push({
      type: "scatter", mode: "markers", x: [null], y: [null], xaxis: "x", yaxis: "y", hoverinfo: "skip",
      marker: { color: [0], cmin: 0, cmax: Math.max(1, view.n - 1), colorscale: "Viridis", showscale: true,
        colorbar: { title: { text: "repeat", side: "right" }, thickness: 10, len: 0.6, y: 0.7 } },
    } as Data)
    return { data, layout: L as Partial<Layout> }
  }, [raw, view, at])

  const strips: Strip[] = useMemo(() => {
    if (!raw || !view) return []
    const x = raw.repeats.map((r) => at(r.time_unix))
    const text = raw.repeats.map((r) => `repeat ${r.index}`)
    const marker = { color: view.colour, size: 7 }
    return [
      { title: "from main state", log: true, traces: [
        { type: "scatter", mode: "lines+markers", x, y: view.fromMain, text, marker,
          line: { color: MUTED, width: 1 }, name: "RMS |Γ − main state's median|",
          hovertemplate: "%{text}, %{x}<br>%{y:.3g}<extra></extra>" },
        { type: "scatter", mode: "lines", x: [x[0], x[x.length - 1]], y: [STATE_TOL, STATE_TOL],
          line: { color: CRITICAL, width: 1, dash: "dot" }, name: "another state beyond this", hoverinfo: "skip" },
      ] },
      { title: "step in its state", traces: [
        { type: "scatter", mode: "lines+markers", x, y: view.step, text, marker, line: { color: MUTED, width: 1 },
          name: "RMS |Γ − mean of its neighbours in its state|", hovertemplate: "%{text}, %{x}<br>%{y:.3g}<extra></extra>" },
        { type: "scatter", mode: "lines", x: [x[0], x[x.length - 1]], y: [GLITCH_FACTOR * view.typical, GLITCH_FACTOR * view.typical],
          line: { color: SERIOUS, width: 1, dash: "dot" }, name: `${GLITCH_FACTOR}× the typical step`, hoverinfo: "skip" },
      ] },
    ]
  }, [raw, view, at])

  if (session.raw_session_id === null) return null
  const glitches = view ? raw!.repeats.filter((_, i) => view.glitch[i]) : []
  const hhmm = (t: number | null) => (t ? (at(t) ?? "").slice(11, 16) : "?")
  return (
    <div className="mt-3">
      <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
        <h4 className="h6 m-0">Unaveraged repeats</h4>
        <div className="btn-group btn-group-sm" role="group" aria-label="Load">
          {session.labels.map((k) => (
            <button key={k} className={`btn ${k === label ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setLabel(k)}>{k}</button>
          ))}
        </div>
        {raw && view && (
          <span className="small text-muted">
            {view.n} repeats{raw.repeats[0]?.time_unix && raw.repeats[view.n - 1]?.time_unix
              ? `, ${(at(raw.repeats[0].time_unix) ?? "").slice(11, 16)}–${(at(raw.repeats[view.n - 1].time_unix) ?? "").slice(11, 16)} ${tz}` : ""}
          </span>
        )}
      </div>
      {session.pipeline_qa && <PipelineNote qa={session.pipeline_qa} mixed={!!view && view.states.length > 1} />}
      {error && <div className="text-danger small">{error}</div>}
      {!raw && !error && <div className="text-muted small">Reading the repeats…</div>}
      {raw?.errors.map((e) => <div key={e} className="text-danger small">{e}</div>)}
      {raw && view && figure && (
        <>
          {view.states.length > 1 && (
            <div className="alert alert-danger py-1 small mb-1">
              <strong>{label} switches between {view.states.length} states in this session</strong>, so the field's averaged S11 file
              mixes them. Mean |S11| over {LEVEL_BAND[0]}–{LEVEL_BAND[1]} MHz:{" "}
              {view.states.map((st, k) => (
                <span key={k}>{k > 0 && "; "}
                  {st.main ? "main (most repeats)" : "other (red)"}: {st.level_db.toFixed(1)} dB
                  {st.level_db > -1 ? " (near total reflection)" : ""}, {st.count} repeat{st.count === 1 ? "" : "s"}
                  {!st.main && ` at ${st.repeats.map((i) => hhmm(raw!.repeats[i].time_unix)).join(", ")}`}
                </span>
              ))}.
            </div>
          )}
          {glitches.length > 0 ? (
            <div className="alert alert-warning py-1 small mb-1">
              {glitches.length} of {view.n} repeats of {label} step away from their neighbours in the same state (over{" "}
              {GLITCH_FACTOR}× the typical step), drawn in orange:{" "}
              {glitches.map((r) => `${r.index}${r.time_unix ? ` (${hhmm(r.time_unix)})` : ""}`).join(", ")}.
            </div>
          ) : (
            <div className="small text-muted mb-1">No repeat of {label} steps away from its neighbours in its state.</div>
          )}
          <Plot data={figure.data} layout={figure.layout} useResizeHandler style={{ width: "100%" }}
            config={{ displaylogo: false, responsive: true }} />
          <TimeStrips strips={strips} height={330} revision={`${raw.raw_session_id}/${label}`}
            utcOffsetHours={off} timeTitle={`File time (${tz}; the VNA's own clock is not set)`} />
          <p className="small text-muted mb-0">
            Raw VNA readings, uncalibrated, coloured by the order they were taken; repeats in another state than the
            main one are red, glitches orange. The step from the neighbours (in the same state) cancels slow drifts
            (e.g. the VNA warming up), so a glitch stands out there; the difference from the main state shows the
            drifts and the states.
          </p>
        </>
      )}
    </div>
  )
}

/** How the pipeline built this session's antenna S11, and whether nights are calibrated with it. */
function PipelineNote({ qa, mixed }: { qa: NonNullable<S11Session["pipeline_qa"]>; mixed: boolean }) {
  const left = (qa.n_implausible ?? 0) + (qa.n_outliers ?? 0)
  return (
    <div className={`small mb-1 ${qa.ok ? "text-muted" : "text-danger"}`}>
      The pipeline's antenna S11 for this session (as used by the night calibration):{" "}
      {qa.repeats === "main_state" ? (
        <>the mean of the {qa.n_used} of {qa.n_repeats} repeats in the main state ({left} left out:{" "}
          {qa.n_implausible ?? 0} open-like, {qa.n_outliers ?? 0} other), so the fault is repaired</>
      ) : qa.repeats === "all" ? (
        <>the mean of all {qa.n_repeats} repeats</>
      ) : (
        <>the field's averaged file{qa.note ? ` (${qa.note})` : ""}
          {mixed && ", which mixes the states below"}</>
      )}
      {"; "}
      {qa.ok
        ? <>accepted ({qa.reason}), so nights are calibrated with it</>
        : <>rejected ({qa.reason}), so nights use the last good session instead</>}
      {qa.dev !== null && <> (max |ΔΓ| from the recent accepted sessions {qa.dev.toFixed(3)})</>}
      {!qa.applied && " (the default night configuration does not apply the check yet)"}.
    </div>
  )
}
