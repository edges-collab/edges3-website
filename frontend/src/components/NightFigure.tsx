/**
 * "Last night at a glance": one Plotly figure whose strips share a
 * site-local time axis (zooming one zooms all):
 *
 *   waterfall (Q or log10 p0) vs frequency, with LST on the top axis
 *   band-median Q          (L1)
 *   band power p0/p1/p2    (L1)
 *   load temperatures      (catalog housekeeping)
 *   hot load temperature
 *   battery voltage
 *   events                 (ADC full-scale hits, data drops; catalog)
 *
 * Each strip has its own y axis (no dual axes). Gaps arrive from the
 * backend as NaN rows / nulls, so nothing is interpolated across them.
 */
import { useMemo } from "react"
import type { Data, Layout } from "plotly.js"
import Plot from "../utils/plotComponent"
import type { NightPayload } from "../types/night"
import { decodeRows, lstTicks, robustRange, siteTimes, toSiteTime } from "../utils/nightData"

// Reference categorical slots 1-3 (validated all-pairs) and status colours.
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
const WARNING = "#fab219"
const CRITICAL = "#d03b3b"
const GRID = "#e6e6e3"
const MUTED = "#52514e"

type Props = {
  data: NightPayload
  quantity: "q" | "p0"
}

type Strip = { key: string; weight: number; title: string; log?: boolean }

export default function NightFigure({ data, quantity }: Props) {
  const off = data.night.utc_offset_hours
  const ql = data.quicklook
  const hk = useMemo(
    () => Object.fromEntries((data.housekeeping?.series ?? []).map((s) => [s.name, s])),
    [data.housekeeping],
  )

  const wf = useMemo(() => {
    const enc = quantity === "p0" ? ql.waterfall_p0 : ql.waterfall_q
    if (!enc || !ql.time_unix) return null
    let rows: number[][] = decodeRows(enc).map((r) => Array.from(r))
    if (quantity === "p0") rows = rows.map((r) => r.map((v) => (v > 0 ? Math.log10(v) : NaN)))
    return { rows, range: robustRange(rows) }
  }, [ql, quantity])

  const strips: Strip[] = [
    { key: "wf", weight: 4, title: "Frequency [MHz]" },
    { key: "q", weight: 1, title: "Q" },
    { key: "power", weight: 1, title: "Power" },
    { key: "temps", weight: 1, title: "°C" },
    { key: "hot", weight: 0.8, title: "°C" },
    { key: "battery", weight: 0.8, title: hk.battery_voltage?.unit ?? "V" },
    { key: "events", weight: 0.45, title: "" },
  ]

  // Stack strips top to bottom with a fixed gap between them.
  const gap = 0.025
  const total = strips.reduce((s, x) => s + x.weight, 0)
  const usable = 1 - gap * (strips.length - 1)
  const axis: Record<string, string> = {}
  const layout: Partial<Layout> = {
    autosize: true,
    height: 1100,
    margin: { l: 70, r: 150, t: 60, b: 50 },
    showlegend: true,
    legend: { orientation: "v", x: 1.02, xanchor: "left", yanchor: "top", font: { size: 11 } },
    hovermode: "closest",
    plot_bgcolor: "#fcfcfb",
    paper_bgcolor: "#fcfcfb",
    font: { size: 11, color: "#0b0b0b" },
  }
  const L = layout as Record<string, unknown>
  let top = 1
  strips.forEach((s, i) => {
    const h = (s.weight / total) * usable
    const name = i === 0 ? "yaxis" : `yaxis${i + 1}`
    axis[s.key] = i === 0 ? "y" : `y${i + 1}`
    L[name] = {
      domain: [Math.max(0, top - h), top],
      title: { text: s.title, font: { size: 11 } },
      type: s.log ? "log" : s.key === "events" ? "category" : "linear",
      gridcolor: GRID,
      zeroline: false,
      fixedrange: s.key === "events",
      exponentformat: "power",
    }
    top -= h + gap
  })
  // Legend below the waterfall's colour bar.
  ;(layout.legend as Record<string, unknown>).y = (L.yaxis as { domain: number[] }).domain[0] - gap
  const lastY = axis.events
  const x0 = toSiteTime(data.night.start_unix, off)
  const x1 = toSiteTime(data.night.end_unix, off)
  L.xaxis = {
    type: "date",
    anchor: lastY,
    range: [x0, x1],
    title: { text: `Site time (${data.night.timezone}, UTC${off >= 0 ? "+" : ""}${off})` },
    gridcolor: GRID,
    showspikes: true,
    spikemode: "across",
    spikethickness: 1,
    spikecolor: MUTED,
    spikedash: "dot",
  }

  const traces: Data[] = []
  const add = (t: Record<string, unknown>) => traces.push(t as Data)

  // --- waterfall + LST axis
  if (wf && ql.time_unix && ql.freq_edges_mhz) {
    const x = siteTimes(ql.time_unix, off)
    const [d0, d1] = (L.yaxis as { domain: number[] }).domain
    add({
      type: "heatmap",
      x,
      y: ql.freq_edges_mhz,
      z: wf.rows,
      transpose: true,
      xaxis: "x",
      yaxis: "y",
      colorscale: "Viridis",
      zmin: wf.range?.[0],
      zmax: wf.range?.[1],
      zsmooth: false,
      showscale: true,
      showlegend: false,
      colorbar: {
        title: { text: quantity === "q" ? "Q" : "log₁₀ p0", side: "right" },
        y: (d0 + d1) / 2,
        len: d1 - d0,
        x: 1.02,
        thickness: 12,
      },
      hovertemplate:
        `%{x}<br>%{y:.1f} MHz<br>${quantity === "q" ? "Q" : "log₁₀ p0"} = %{z:.4g}<extra></extra>`,
    })
    const ticks = lstTicks(ql.time_unix, ql.lst_hour ?? [])
    L.xaxis2 = {
      type: "date",
      overlaying: "x",
      matches: "x",
      side: "top",
      anchor: "y",
      tickmode: "array",
      tickvals: ticks.map((k) => toSiteTime(k.t, off)),
      ticktext: ticks.map((k) => k.label),
      title: { text: "LST", standoff: 4 },
      showgrid: false,
    }
    add({ type: "scatter", x: [x0, x1], y: [null, null], xaxis: "x2", yaxis: "y", showlegend: false, hoverinfo: "skip" })
  }

  // --- L1 band series
  const band = data.band
  if (band && band.time_unix.length) {
    const x = siteTimes(band.time_unix, off)
    const [qa, qb] = band.q_band_mhz
    const [pa, pb] = band.power_band_mhz
    add({
      type: "scatter", mode: "lines", x, y: band.q_median, xaxis: "x", yaxis: axis.q,
      name: `median Q ${qa}–${qb} MHz`, line: { color: SERIES[0], width: 1.5 },
      legendgroup: "q", legendgrouptitle: { text: "Band (L1)" },
      hovertemplate: "%{x}<br>Q = %{y:.4f}<extra></extra>",
    })
    ;(["p0", "p1", "p2"] as const).forEach((k, i) =>
      add({
        type: "scatter", mode: "lines", x, y: band[k], xaxis: "x", yaxis: axis.power,
        name: `${k} ${pa}–${pb} MHz`, line: { color: SERIES[i], width: 1.5 }, legendgroup: "q",
        hovertemplate: `%{x}<br>${k} = %{y:.4g}<extra></extra>`,
      }),
    )
  }

  // --- housekeeping
  const hkLine = (name: string, yaxis: string, color: string, group?: string) => {
    const s = hk[name]
    if (!s || s.t_unix.length === 0) return
    add({
      type: "scatter", mode: "lines", x: siteTimes(s.t_unix, off), y: s.value,
      xaxis: "x", yaxis, name: `${s.label} (${s.code})`, line: { color, width: 1.5 },
      legendgroup: "hk", legendgrouptitle: group ? { text: group } : undefined,
      hovertemplate: `%{x}<br>${s.label} = %{y:.3f} ${s.unit ?? ""}<extra></extra>`,
    })
  }
  hkLine("amb_load_temperature", axis.temps, SERIES[0], "Housekeeping")
  hkLine("front_end_temperature", axis.temps, SERIES[1])
  hkLine("inner_box_temperature", axis.temps, SERIES[2])
  hkLine("hot_load_temperature", axis.hot, SERIES[0])
  hkLine("battery_voltage", axis.battery, SERIES[0])

  // --- events (status colours, always labelled on the axis)
  const events: [string, number[], string][] = [
    ["ADC full scale", data.events.adc_clip_unix, WARNING],
    ["Data drops", data.events.data_drop_unix, CRITICAL],
  ]
  for (const [label, times, color] of events) {
    add({
      type: "scatter", mode: "markers", x: times.map((t) => toSiteTime(t, off)),
      y: times.map(() => label), xaxis: "x", yaxis: axis.events, name: `${label} (${times.length})`,
      marker: { symbol: "line-ns-open", size: 12, color, line: { width: 2, color } },
      legendgroup: "ev", legendgrouptitle: label === "ADC full scale" ? { text: "Events" } : undefined,
      hovertemplate: `%{x}<br>${label}<extra></extra>`,
    })
  }
  ;(L[axis.events.replace("y", "yaxis")] as Record<string, unknown>).categoryarray = events.map((e) => e[0])
  ;(L[axis.events.replace("y", "yaxis")] as Record<string, unknown>).categoryorder = "array"

  // strip labels (in text ink, not series colour)
  const labels: Record<string, string> = {
    q: "Band-median Q",
    power: "Band power (raw units)",
    temps: "Ambient load · front end · inner box",
    hot: "Hot load",
    battery: "Battery",
  }
  L.annotations = Object.entries(labels).map(([k, text]) => {
    const i = strips.findIndex((s) => s.key === k)
    const dom = (L[i === 0 ? "yaxis" : `yaxis${i + 1}`] as { domain: number[] }).domain
    return {
      text, xref: "paper", yref: "paper", x: 0, y: dom[1], xanchor: "left", yanchor: "bottom",
      showarrow: false, font: { size: 11, color: MUTED },
    }
  })

  return (
    <Plot
      data={traces}
      layout={layout}
      useResizeHandler={true}
      style={{ width: "100%" }}
      config={{ displaylogo: false, responsive: true }}
    />
  )
}
