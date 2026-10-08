/**
 * An interactive waterfall (time on x, frequency on y) with a robust colour
 * range. `diverging` centres a two-hue scale on zero (for fractional
 * deviations); otherwise a sequential scale.
 */
import type { Data, Layout } from "plotly.js"
import Plot from "../utils/plotComponent"
import { robustRange } from "../utils/robust"

type Props = {
  title: string
  times: string[]
  freqs: number[]
  z: number[][] // rows = times, cols = freqs
  unit: string
  diverging?: boolean
  height?: number
  xTitle?: string
  /** the x (time) range shown, as the times' strings (default: the data's) */
  xRange?: [string, string]
}

export default function Waterfall({ title, times, freqs, z, unit, diverging, height = 320, xTitle, xRange }: Props) {
  const r = robustRange(z, undefined, undefined, 0.02, 0.98)
  let zmin = r?.[0], zmax = r?.[1]
  if (diverging && r) {
    const m = Math.max(Math.abs(r[0]), Math.abs(r[1]))
    zmin = -m
    zmax = m
  }
  const data: Data[] = [{
    type: "heatmap", x: times, y: freqs, z, transpose: true, zmin, zmax, zsmooth: false,
    colorscale: diverging ? "RdBu" : "Viridis", reversescale: !!diverging,
    colorbar: { title: { text: unit, side: "right" }, thickness: 10 },
    hovertemplate: `%{x}<br>%{y:.2f} MHz<br>%{z:.4g} ${unit}<extra></extra>`,
  } as Data]
  const layout: Partial<Layout> = {
    title: { text: title, font: { size: 12 }, x: 0.01, xanchor: "left" },
    height, autosize: true, margin: { l: 60, r: 10, t: 30, b: 45 },
    xaxis: { type: "date", title: { text: xTitle ?? "" }, ...(xRange ? { range: xRange, autorange: false } : {}) },
    yaxis: { title: { text: "Frequency [MHz]" } },
    plot_bgcolor: "#fcfcfb", paper_bgcolor: "#fcfcfb", font: { size: 11 },
  }
  return <Plot data={data} layout={layout} useResizeHandler style={{ width: "100%" }}
    config={{ displaylogo: false, responsive: true }} />
}
