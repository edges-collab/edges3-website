/**
 * Several panels in one Plotly figure, in one or two columns, sharing the
 * x axis: zooming or box-selecting in any panel applies to all of them, and
 * the figure stays compact. Each panel has its own y axis (no dual axes).
 */
import type { Data, Layout, Shape } from "plotly.js"
import Plot from "../utils/plotComponent"

// Reference categorical slots 1-3 (validated all-pairs).
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
export const MUTED = "#52514e"
const GRID = "#e6e6e3"

export type Trace = {
  x: ArrayLike<number>
  y: ArrayLike<number>
  name?: string
  color?: string
  dash?: "solid" | "dash" | "dot"
  mode?: "lines" | "markers" | "lines+markers"
}

export type Panel = {
  title: string
  yTitle?: string
  traces: Trace[]
  yRange?: [number, number]
}

type Props = {
  panels: Panel[]
  cols?: 1 | 2
  panelHeight?: number
  xTitle?: string
  xRange?: [number, number]
  /** Shade an x interval in every panel (e.g. a fit window), with a label. */
  shade?: { range: [number, number]; label: string }
}

export default function StackedPlot({
  panels, cols = 1, panelHeight = 150, xTitle = "Frequency [MHz]", xRange, shade,
}: Props) {
  const nRows = Math.ceil(panels.length / cols)
  const gap = 0.06 / Math.max(1, nRows / 4)
  const h = (1 - gap * (nRows - 1)) / nRows
  const L: Record<string, unknown> = {
    autosize: true,
    height: nRows * panelHeight + 90,
    margin: { l: 70, r: 20, t: 30, b: 50 },
    hovermode: "closest",
    showlegend: true,
    legend: { orientation: "h", y: -40 / (nRows * panelHeight + 90) - 0.02, yanchor: "top" },
    plot_bgcolor: "#fcfcfb",
    paper_bgcolor: "#fcfcfb",
    font: { size: 11, color: "#0b0b0b" },
  }
  const data: Data[] = []
  const annotations: Partial<Layout["annotations"][number]>[] = []
  const shapes: Partial<Shape>[] = []
  const colW = cols === 2 ? 0.46 : 1
  const named = new Set<string>() // one legend entry per series name
  for (let c = 0; c < cols; c++) {
    const xa = c === 0 ? "xaxis" : "xaxis2"
    const bottom = Math.min(panels.length - 1, (nRows - 1) * cols + c)
    L[xa] = {
      domain: c === 0 ? [0, colW] : [1 - colW, 1],
      anchor: `y${bottom === 0 ? "" : bottom + 1}`,
      title: { text: xTitle },
      gridcolor: GRID,
      range: xRange,
      showspikes: true, spikemode: "across", spikethickness: 1, spikecolor: MUTED, spikedash: "dot",
      ...(c === 1 ? { matches: "x" } : {}),
    }
  }
  panels.forEach((p, i) => {
    const r = Math.floor(i / cols)
    const c = i % cols
    const top = 1 - r * (h + gap)
    const yName = i === 0 ? "yaxis" : `yaxis${i + 1}`
    const yRef = i === 0 ? "y" : `y${i + 1}`
    const xRef = c === 0 ? "x" : "x2"
    L[yName] = {
      domain: [Math.max(0, top - h), top],
      anchor: xRef,
      title: { text: p.yTitle ?? "", font: { size: 10 } },
      gridcolor: GRID,
      zeroline: false,
      range: p.yRange,
      exponentformat: "power",
    }
    annotations.push({
      text: p.title, xref: "paper", yref: "paper", showarrow: false,
      x: c === 0 ? 0 : 1 - colW, y: top, xanchor: "left", yanchor: "bottom",
      font: { size: 11, color: MUTED },
    })
    p.traces.forEach((t, k) => {
      const first = !!t.name && !named.has(t.name)
      if (t.name) named.add(t.name)
      data.push({
        type: "scatter",
        mode: t.mode ?? "lines",
        x: Array.from(t.x),
        y: Array.from(t.y),
        xaxis: xRef,
        yaxis: yRef,
        name: t.name ?? p.title,
        showlegend: first,
        legendgroup: t.name,
        line: { color: t.color ?? SERIES[k % SERIES.length], width: 1.5, dash: t.dash ?? "solid" },
        marker: { color: t.color ?? SERIES[k % SERIES.length], size: 5 },
        hovertemplate: `%{x:.2f} MHz<br>${t.name ?? p.title} = %{y:.5g}<extra></extra>`,
      } as Data)
    })
    if (shade) {
      shapes.push({
        type: "rect", xref: xRef, yref: `${yRef} domain` as Shape["yref"],
        x0: shade.range[0], x1: shade.range[1], y0: 0, y1: 1,
        fillcolor: "#2a78d6", opacity: 0.06, line: { width: 0 }, layer: "below",
      })
    }
  })
  if (shade) {
    annotations.push({
      text: shade.label, xref: "x", yref: "paper", x: (shade.range[0] + shade.range[1]) / 2,
      y: 1, yanchor: "bottom", showarrow: false, font: { size: 10, color: MUTED },
    })
  }
  L.annotations = annotations
  L.shapes = shapes
  return (
    <Plot
      data={data}
      layout={L as Partial<Layout>}
      useResizeHandler={true}
      style={{ width: "100%" }}
      config={{ displaylogo: false, responsive: true }}
    />
  )
}
