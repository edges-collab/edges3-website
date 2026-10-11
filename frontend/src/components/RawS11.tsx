/**
 * One load's unaveraged S11 repeats in a raw session (EDGES-3 records 50
 * per load since 2026-08, over ~2 hours), as measured (uncalibrated), to
 * show glitches the averaged session hides:
 *
 * - |S11|, phase and |Γ − the session's median| against frequency, every
 *   repeat coloured by the order it was taken;
 * - per repeat, against time: its RMS difference from the session's median
 *   (slow drifts show as a trend) and from the mean of its two neighbours
 *   (a drift cancels; a glitch stands out). Repeats whose step from their
 *   neighbours is over GLITCH_FACTOR times the session's typical step are
 *   marked in red.
 */
import { useMemo, useState } from "react"
import type { Data, Layout } from "plotly.js"
import Plot from "../utils/plotComponent"
import TimeStrips, { clock, type Strip } from "./TimeStrips"
import { MUTED } from "./StackedPlot"
import { useJson } from "../hooks/useJson"
import type { RawS11, S11Session } from "../types/browse"

const CRITICAL = "#d03b3b"
const GRID = "#e6e6e3"
const GLITCH_FACTOR = 5
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
    const mRe = f.map((_, j) => median(re.map((r) => r[j]))), mIm = f.map((_, j) => median(im.map((r) => r[j])))
    const dev = re.map((r, i) => r.map((x, j) => Math.hypot(x - mRe[j], im[i][j] - mIm[j])))
    // the step from the mean of its neighbours (the end ones: from their one neighbour)
    const step = re.map((r, i) => {
      const a = i === 0 ? 1 : i - 1, b = i === n - 1 ? n - 2 : i + 1
      return rms(r.map((x, j) => Math.hypot(x - (re[a][j] + re[b][j]) / 2, im[i][j] - (im[a][j] + im[b][j]) / 2)))
    })
    const typical = median(step)
    const glitch = step.map((s) => n > 2 && s > GLITCH_FACTOR * typical)
    const colour = re.map((_, i) => (glitch[i] ? CRITICAL : viridis(n > 1 ? i / (n - 1) : 0)))
    const db = re.map((r, i) => r.map((x, j) => 20 * Math.log10(Math.hypot(x, im[i][j]))))
    const deg = re.map((r, i) => r.map((x, j) => (Math.atan2(im[i][j], x) * 180) / Math.PI))
    return { f, n, dev, step, typical, glitch, colour, db, deg, fromMedian: dev.map(rms) }
  }, [raw])

  const figure = useMemo(() => {
    if (!raw || !view) return null
    const panels = [
      { key: "db", title: "|S11| [dB]", rows: view.db, log: false },
      { key: "deg", title: "Phase [deg]", rows: view.deg, log: false },
      { key: "dev", title: "|Γ − median|", rows: view.dev, log: true },
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
          line: { color: view.colour[i], width: view.glitch[i] ? 2 : 1 },
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
      { title: "from median", traces: [{ type: "scatter", mode: "lines+markers", x, y: view.fromMedian, text, marker,
        line: { color: MUTED, width: 1 }, name: "RMS |Γ − session median|",
        hovertemplate: "%{text}, %{x}<br>%{y:.3g}<extra></extra>" }] },
      { title: "from neighbours", traces: [
        { type: "scatter", mode: "lines+markers", x, y: view.step, text, marker, line: { color: MUTED, width: 1 },
          name: "RMS |Γ − mean of its neighbours|", hovertemplate: "%{text}, %{x}<br>%{y:.3g}<extra></extra>" },
        { type: "scatter", mode: "lines", x: [x[0], x[x.length - 1]], y: [GLITCH_FACTOR * view.typical, GLITCH_FACTOR * view.typical],
          line: { color: CRITICAL, width: 1, dash: "dot" }, name: `${GLITCH_FACTOR}× the typical step`, hoverinfo: "skip" },
      ] },
    ]
  }, [raw, view, at])

  if (session.raw_session_id === null) return null
  const glitches = view ? raw!.repeats.filter((_, i) => view.glitch[i]) : []
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
      {error && <div className="text-danger small">{error}</div>}
      {!raw && !error && <div className="text-muted small">Reading the repeats…</div>}
      {raw?.errors.map((e) => <div key={e} className="text-danger small">{e}</div>)}
      {raw && view && figure && (
        <>
          {glitches.length > 0 ? (
            <div className="alert alert-danger py-1 small mb-1">
              {glitches.length} repeat{glitches.length === 1 ? "" : "s"} of {label} stand{glitches.length === 1 ? "s" : ""} out
              from {glitches.length === 1 ? "its" : "their"} neighbours (over {GLITCH_FACTOR}× the session's typical step):{" "}
              {glitches.map((r) => `${r.index}${r.time_unix ? ` (${(at(r.time_unix) ?? "").slice(11, 16)})` : ""}`).join(", ")}.
              They are drawn in red.
            </div>
          ) : (
            <div className="small text-muted mb-1">No repeat of {label} stands out from its neighbours.</div>
          )}
          <Plot data={figure.data} layout={figure.layout} useResizeHandler style={{ width: "100%" }}
            config={{ displaylogo: false, responsive: true }} />
          <TimeStrips strips={strips} height={330} revision={`${raw.raw_session_id}/${label}`}
            utcOffsetHours={off} timeTitle={`File time (${tz}; the VNA's own clock is not set)`} />
          <p className="small text-muted mb-0">
            Raw VNA readings, uncalibrated, coloured by the order they were taken (glitches in red). The step from
            the neighbours cancels slow drifts (e.g. the VNA warming up), so a glitch stands out there; the
            difference from the median shows the drifts.
          </p>
        </>
      )}
    </div>
  )
}
