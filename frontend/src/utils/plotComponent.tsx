/**
 * Plotly React component, following the site's light or dark theme.
 *
 * Uses `plotly.js-cartesian-dist` (~1.5 MB) instead of the full `plotly.js`
 * (~3 MB) or the basic distribution (~1 MB): the heatmaps need the
 * cartesian distribution, and nothing needs the 3D / mapbox / finance
 * modules. `react-plotly.js` ships a `factory` that takes any Plotly
 * constructor (https://github.com/plotly/react-plotly.js#custom-plotly-build).
 *
 * The figures are written with the light palette (LIGHT). In dark mode this
 * wrapper swaps it for DARK: the backgrounds, the ink, the grid and axis
 * lines, and the muted grey of lines, markers and labels. The series
 * colours, status colours and colour scales work on both.
 */
import { useMemo } from "react"
import createPlotlyComponent from "react-plotly.js/factory"
import Plotly from "plotly.js-cartesian-dist"
import type { PlotParams } from "react-plotly.js"
import { useTheme } from "../state/Theme"

const BasePlot = createPlotlyComponent(Plotly)

export const LIGHT = { bg: "#fcfcfb", ink: "#0b0b0b", grid: "#e6e6e3", muted: "#52514e" }
export const DARK = { bg: "#1d1e20", ink: "#e6e4e0", grid: "#36383c", muted: "#a8a6a1" }

const SWAP: Record<string, string> = {
  [LIGHT.bg]: DARK.bg, [LIGHT.ink]: DARK.ink, [LIGHT.grid]: DARK.grid, [LIGHT.muted]: DARK.muted,
}
const swap = (c: unknown): unknown =>
  typeof c === "string" ? SWAP[c.toLowerCase()] ?? c : Array.isArray(c) ? c.map(swap) : c

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v)

/** A copy of ``o`` with every ``*color`` value swapped, recursively (arrays of data untouched). */
function swapColors(o: Obj): Obj {
  const out: Obj = {}
  for (const [k, v] of Object.entries(o)) {
    if (/color$/i.test(k)) out[k] = swap(v)
    else if (isObj(v)) out[k] = swapColors(v)
    else if (Array.isArray(v) && v.some(isObj)) out[k] = v.map((x) => (isObj(x) ? swapColors(x) : x))
    else out[k] = v
  }
  return out
}

function darkLayout(layout: Obj): Obj {
  const L = swapColors(layout)
  L.paper_bgcolor = DARK.bg
  L.plot_bgcolor = DARK.bg
  L.font = { ...(isObj(L.font) ? L.font : {}), color: DARK.ink }
  for (const [k, v] of Object.entries(L)) {
    if (/^[xy]axis\d*$/.test(k) && isObj(v)) {
      L[k] = { gridcolor: DARK.grid, linecolor: DARK.grid, zerolinecolor: DARK.grid, ...v }
    }
  }
  L.legend = { bgcolor: "rgba(0,0,0,0)", ...(isObj(L.legend) ? L.legend : {}) }
  L.modebar = { bgcolor: "rgba(0,0,0,0)", color: DARK.muted, activecolor: DARK.ink, ...(isObj(L.modebar) ? L.modebar : {}) }
  return L
}

export default function Plot(props: PlotParams) {
  const { dark } = useTheme()
  const { layout, data } = props
  const themed = useMemo(() => (dark
    ? { layout: darkLayout(layout as Obj) as PlotParams["layout"], data: (data ?? []).map((t) => swapColors(t as Obj)) as PlotParams["data"] }
    : { layout, data }), [dark, layout, data])
  return <BasePlot {...props} layout={themed.layout} data={themed.data} />
}
