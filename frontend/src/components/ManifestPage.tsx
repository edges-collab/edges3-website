import { lazy, Suspense, useState } from "react"
import { Link } from "react-router"

// Lazy-loaded so plotly.js-basic-dist is only fetched when a page actually
// renders plots. Home and Select stay light.
const S11Plotter = lazy(() => import("./S11Plotter"))
const SinglePlotter = lazy(() => import("./SinglePlotter"))
const MultiPlotter = lazy(() => import("./MultiPlotter"))
const HeatmapPlotter = lazy(() => import("./HeatmapPlotter"))

import LatestRunBanner from "./LatestRunBanner"
import { useManifest } from "../hooks/useManifest"
import { useRunState } from "../state/RunContext"
import { withBaseUrl } from "../utils/baseURL"
import type {
  HeatmapPlot,
  ImagePlot,
  MultiPlot,
  PageName,
  S11Plot,
  SinglePlot,
} from "../types/manifest"

function PlotFallback() {
  return <div className="p-2 text-muted small">Loading plot…</div>
}

type Props = {
  page: PageName
  title?: string
}

export default function ManifestPage({ page, title }: Props) {
  const { manifest, error } = useManifest("/data/manifest.json")
  const { latest } = useRunState()
  const [showHeatmaps, setShowHeatmaps] = useState(false)

  if (error) return <div className="text-danger p-3">Error loading manifest: {error.message}</div>
  if (!manifest) return <div className="p-3">Loading manifest…</div>

  // If the user hasn't triggered a run yet, show a single friendly
  // message instead of an empty grid of "no plots" placeholders.
  if (!latest.run_id) {
    return (
      <>
        <LatestRunBanner pageTitle={title} />
        <div className="p-3">
          <p>Run has not been completed.</p>
          <p>
            Go to <Link to="/Select">Select</Link>, choose dates and parameters, and click{" "}
            <strong>Run with these dates</strong>.
          </p>
        </div>
      </>
    )
  }

  const pagePlots = manifest.plots.filter((p) => p.page === page)
  const hasHeatmaps = pagePlots.some((p) => p.type === "heatmap")
  const hasImages = pagePlots.some((p) => p.type === "image")

  const plots = pagePlots.filter((p) => {
    if (p.type === "heatmap") return showHeatmaps
    if (p.type === "image" && showHeatmaps) return false
    return true
  })

  if (plots.length === 0) {
    return (
      <>
        <LatestRunBanner pageTitle={title} />
        <div className="p-3">No plots to display for page: {page}</div>
      </>
    )
  }

  return (
    <>
      <LatestRunBanner pageTitle={title} />
      {hasHeatmaps && hasImages && (
        <div className="px-2 mb-2">
          <button
            className="btn btn-sm btn-outline-primary"
            onClick={() => setShowHeatmaps((prev) => !prev)}
          >
            {showHeatmaps
              ? "Show images"
              : "Show interactive heatmaps (Large files, may take several seconds to load and cause crashes.)"}
          </button>
        </div>
      )}
      <div className="data-panel p-2">
        <Suspense fallback={<PlotFallback />}>
          {plots.map((plot) => {
            switch (plot.type) {
              case "s11":
                return <S11Plotter key={plot.id} s11Input={plot as S11Plot} />
              case "single":
                return <SinglePlotter key={plot.id} input={plot as SinglePlot} />
              case "multi":
                return <MultiPlotter key={plot.id} input={plot as MultiPlot} />
              case "image":
                return (
                  <div key={plot.id} className="mb-3">
                    <h3>{plot.title}</h3>
                    <img
                      src={withBaseUrl((plot as ImagePlot).filePath)}
                      alt={plot.title}
                      style={{ maxWidth: "100%" }}
                    />
                  </div>
                )
              case "heatmap":
                return <HeatmapPlotter key={plot.id} input={plot as HeatmapPlot} />
              default:
                return null
            }
          })}
        </Suspense>
      </div>
    </>
  )
}
