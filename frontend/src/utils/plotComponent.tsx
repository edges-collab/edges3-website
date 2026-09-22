/**
 * Plotly React component factory.
 *
 * Uses `plotly.js-cartesian-dist` (~1.5 MB) instead of the full `plotly.js`
 * (~3 MB) or the basic distribution (~1 MB). The basic distribution only
 * ships scatter + bar, but `HeatmapPlotter` needs the heatmap trace type,
 * which is in the cartesian distribution. We don't need the heavy 3D /
 * mapbox / finance / etc. modules of the full distribution.
 *
 * `react-plotly.js` ships a `factory` that takes any Plotly constructor,
 * which is how we plug in the cartesian distribution. See:
 * https://github.com/plotly/react-plotly.js#custom-plotly-build
 */
import createPlotlyComponent from "react-plotly.js/factory"
import Plotly from "plotly.js-cartesian-dist"

export default createPlotlyComponent(Plotly)
