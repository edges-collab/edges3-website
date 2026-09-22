/**
 * Multi plotter — overlays two npz line plots (e.g. calibration temperature
 * vs. actual on-site temperature) on the same axes. Supports an Actual /
 * Residuals toggle that swaps the second trace for the difference between
 * the two.
 */

import { useEffect, useState } from "react"
import Plot from "../utils/plotComponent"
import { openDataFile } from "../utils/dataLoader"
import type { Data } from "../utils/dataLoader"
import { withBaseUrl } from "../utils/baseURL"
import MultiSelector from "./MultiSelector"

export type MultiPlotInput = {
  type: "multi"
  title: string
  filePath1: string
  filePath2: string
  name1?: string
  name2?: string
  axisx?: string
  axisy?: string
}

type MultiPlotProps = {
  input: MultiPlotInput
}

/**
 * Stride-decimate a series to `targetLen` points (every Nth sample).
 * Used to align two series of different lengths so the overlay and the
 * residual are computed pointwise. Runs in O(n).
 */
function downsampleArray(arr: number[], targetLen: number): number[] {
  if (arr.length <= targetLen) return arr
  const factor = arr.length / targetLen
  const out = new Array(targetLen)
  for (let i = 0; i < targetLen; i++) {
    out[i] = arr[Math.floor(i * factor)]
  }
  return out
}

function MultiPlotter({ input }: MultiPlotProps) {
  const [data1, setData1] = useState<Data | null>(null)
  const [data2, setData2] = useState<Data | null>(null)
  const [displayMode, setDisplayMode] = useState("Actual Values")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
     
    setError(null)
    Promise.all([
      openDataFile(withBaseUrl(input.filePath1)),
      openDataFile(withBaseUrl(input.filePath2)),
    ])
      .then(([d1, d2]) => {
        if (cancelled) return
        setData1(d1)
        setData2(d2)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [input.filePath1, input.filePath2])

  if (error) return <div className="text-danger">Error: {error}</div>
  if (!data1 || !data2) return <div>Loading multi plot...</div>

  const name1 = input.name1 || "Trace 1"
  const name2 = input.name2 || "Trace 2"

  // Align both series onto a common length so the overlay (and the
  // residual) are computed pointwise. The larger series is stride-
  // downsampled to the smaller one's length; the common x axis is the
  // smaller (full-resolution) series' x.
  let y1 = data1.y
  let y2 = data2.y
  let x = data1.x
  let downsampledPoints = 0
  if (data1.y.length > data2.y.length && data2.y.length > 0) {
    y1 = downsampleArray(data1.y, data2.y.length)
    x = data2.x
    downsampledPoints = data1.y.length
  } else if (data2.y.length > data1.y.length && data1.y.length > 0) {
    y2 = downsampleArray(data2.y, data1.y.length)
    downsampledPoints = data2.y.length
  }

  const trace1 = { x, y: y1, type: "scatter" as const, mode: "lines" as const, name: name1 }
  let trace2
  if (displayMode === "Residuals") {
    const diff = y1.map((v, i) => v - y2[i])
    trace2 = { x, y: diff, type: "scatter" as const, mode: "lines" as const, name: `${name1} − ${name2}` }
  } else {
    trace2 = { x, y: y2, type: "scatter" as const, mode: "lines" as const, name: name2 }
  }

  return (
    <div className="plot-wrapper d-flex flex-column">
      <div className="d-flex flex-column w-100 align-items-start">
        <h2 className="p-2">{input.title}</h2>
        <MultiSelector onSelectChange={setDisplayMode} />
        {downsampledPoints > 0 && (
          <div className="alert alert-warning py-1 px-2 small mt-1 mb-0" role="alert">
            Note: the larger dataset was downsampled to{" "}
            {Math.min(data1.y.length, data2.y.length)} points to match the
            smaller one for comparison.
          </div>
        )}
      </div>
      <Plot
        data={[trace1, trace2]}
        layout={{
          autosize: true,
          height: 300,
          margin: { l: 50, r: 20, t: 40, b: 100 },
          showlegend: true,
          xaxis: { title: { text: input.axisx || "Frequency [MHz]" } },
          yaxis: { title: { text: input.axisy || "Y [arb. units]" } },
        }}
        useResizeHandler={true}
        style={{ width: "90%" }}
      />
    </div>
  )
}

export default MultiPlotter
