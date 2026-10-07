/**
 * Several strips in one Plotly figure sharing a UTC time axis (zooming one
 * zooms all), each with its own y axis. Reports the visible time range when
 * the user zooms (``onXRange``, POSIX seconds; null when reset) and the
 * ``customdata`` of a clicked point (``onPick``).
 */
import { useMemo } from "react"
import type { Data, Layout, PlotMouseEvent, PlotRelayoutEvent } from "plotly.js"
import Plot from "../utils/plotComponent"

const GRID = "#e6e6e3"

export type Strip = {
  title: string
  traces: Partial<Data>[]
  weight?: number
  log?: boolean
  /** a strip of markers only (no y scale), e.g. events */
  events?: boolean
}

type Props = {
  strips: Strip[]
  height?: number
  /** keeps the user's zoom while this stays the same */
  revision: string
  barmode?: "group" | "stack" | "overlay"
  /** the time range shown (POSIX s); figures given the same one line up */
  xRange?: [number, number] | null
  onXRange?: (r: [number, number] | null) => void
  onPick?: (customdata: unknown) => void
}

/** POSIX seconds -> a UTC date string Plotly reads as a date. */
export const utc = (t: number | null): string | null =>
  t === null ? null : new Date(t * 1000).toISOString().slice(0, 19).replace("T", " ")

/** A Plotly date string (UTC, no zone) -> POSIX seconds. */
const toUnix = (s: unknown): number => Date.parse(`${String(s).replace(" ", "T")}${String(s).length <= 10 ? "" : "Z"}`) / 1000

export default function TimeStrips({ strips, height = 600, revision, barmode, xRange, onXRange, onPick }: Props) {
  const { data, layout } = useMemo(() => {
    const gap = 0.04
    const total = strips.reduce((s, x) => s + (x.weight ?? 1), 0)
    const usable = 1 - gap * (strips.length - 1)
    const L: Record<string, unknown> = {
      autosize: true,
      height,
      margin: { l: 80, r: 20, t: 20, b: 40 },
      showlegend: true,
      legend: { orientation: "h", y: -0.06, yanchor: "top", font: { size: 11 } },
      hovermode: "closest",
      plot_bgcolor: "#fcfcfb",
      paper_bgcolor: "#fcfcfb",
      font: { size: 11, color: "#0b0b0b" },
      // a new range (e.g. zoomed in a linked figure) is applied, else the user's zoom is kept
      uirevision: `${revision}|${xRange ? xRange.join("-") : ""}`,
      barmode,
      xaxis: {
        type: "date", gridcolor: GRID, title: { text: "UTC" }, anchor: `y${strips.length > 1 ? strips.length : ""}`,
        ...(xRange ? { range: [utc(xRange[0]), utc(xRange[1])], autorange: false } : {}),
      },
    }
    const out: Data[] = []
    let top = 1
    strips.forEach((s, i) => {
      const h = ((s.weight ?? 1) / total) * usable
      const ax = i === 0 ? "y" : `y${i + 1}`
      L[i === 0 ? "yaxis" : `yaxis${i + 1}`] = {
        domain: [Math.max(0, top - h), top],
        title: { text: s.title, font: { size: 11 } },
        type: s.log ? "log" : "linear",
        gridcolor: GRID,
        zeroline: false,
        showticklabels: !s.events,
        fixedrange: !!s.events,
        range: s.events ? [-1, 1] : undefined,
      }
      top -= h + gap
      for (const t of s.traces) out.push({ ...t, xaxis: "x", yaxis: ax } as Data)
    })
    return { data: out, layout: L as Partial<Layout> }
  }, [strips, height, revision, barmode, xRange])

  return (
    <Plot
      data={data}
      layout={layout}
      useResizeHandler
      style={{ width: "100%" }}
      config={{ displaylogo: false, responsive: true }}
      onRelayout={(e: PlotRelayoutEvent) => {
        if (!onXRange) return
        const r = e as Record<string, unknown>
        if (r["xaxis.autorange"]) onXRange(null)
        else if (r["xaxis.range[0]"] !== undefined) onXRange([toUnix(r["xaxis.range[0]"]), toUnix(r["xaxis.range[1]"])])
      }}
      onClick={(e: PlotMouseEvent) => {
        const p = e.points[0] as unknown as { customdata?: unknown }
        if (onPick && p?.customdata !== undefined) onPick(p.customdata)
      }}
    />
  )
}
