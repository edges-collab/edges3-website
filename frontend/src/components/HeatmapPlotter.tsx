import { useCallback, useEffect, useMemo, useState } from "react"
import Plot from "../utils/plotComponent"
import { openHeatmapFile } from "../utils/heatmapLoader"
import type { HeatmapData } from "../utils/heatmapLoader"
import { withBaseUrl } from "../utils/baseURL"
import type { HeatmapPlot } from "../types/manifest"

type Props = {
  input: HeatmapPlot
}

/**
 * Downsample a 2D array to at most maxRows × maxCols by stride decimation
 * (every Nth row/column). The previous block-averaging implementation was
 * O(rows × cols × rowFactor × colFactor) and took ~12 s per 450 × 32768
 * waterfall — fast enough to lock the main thread and make the page appear
 * blank. Stride decimation is O(rows × cols / strideFactor) and runs in
 * <100 ms for the same data.
 */
function downsample2D(
  x: number[],
  y: number[],
  z: number[][],
  maxRows = 500,
  maxCols = 1000,
): { x: number[]; y: number[]; z: number[][] } {
  const rowFactor = Math.max(1, Math.ceil(y.length / maxRows))
  const colFactor = Math.max(1, Math.ceil(x.length / maxCols))
  const newRows = Math.ceil(y.length / rowFactor)
  const newCols = Math.ceil(x.length / colFactor)

  const newX: number[] = new Array(newCols)
  for (let j = 0; j < newCols; j++) newX[j] = x[j * colFactor]
  const newY: number[] = new Array(newRows)
  for (let i = 0; i < newRows; i++) newY[i] = y[i * rowFactor]

  const newZ: number[][] = new Array(newRows)
  for (let i = 0; i < newRows; i++) {
    const srcRow = z[i * rowFactor]
    if (!srcRow) {
      newZ[i] = new Array(newCols).fill(NaN)
      continue
    }
    const outRow: number[] = new Array(newCols)
    for (let j = 0; j < newCols; j++) {
      const v = srcRow[j * colFactor]
      outRow[j] = v === undefined || !Number.isFinite(v) ? NaN : v
    }
    newZ[i] = outRow
  }
  return { x: newX, y: newY, z: newZ }
}

export default function HeatmapPlotter({ input }: Props) {
  const [data, setData] = useState<HeatmapData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [zmin, setZmin] = useState<number | null>(null)
  const [zmax, setZmax] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
     
    setError(null)
    setData(null)
    openHeatmapFile(withBaseUrl(input.filePath))
      .then((d) => { if (!cancelled) setData(d) })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => { cancelled = true }
  }, [input.filePath])

  // Downsample once (memoised) so neither the percentile pass nor the
  // render repeats the O(rows × cols) stride loop. The percentile is
  // computed on the *downsampled* grid — running it on the full 450×32768
  // waterfall means collecting + sorting ~14.7M values on the main thread
  // (multi-second freeze); on the ~500×1000 downsample it is instant.
  const downsampled = useMemo(
    () => (data ? downsample2D(data.x, data.y, data.z) : null),
    [data],
  )

  // Robust initial color range (5th / 95th percentiles).
  useEffect(() => {
    if (!downsampled) return
    const allValues: number[] = []
    for (const row of downsampled.z) {
      for (const val of row) {
        if (Number.isFinite(val)) allValues.push(val)
      }
    }
    if (allValues.length > 0) {
      allValues.sort((a, b) => a - b)

      setZmin(allValues[Math.floor(allValues.length * 0.05)])

      setZmax(allValues[Math.floor(allValues.length * 0.95)])
    } else {

      setZmin(null)

      setZmax(null)
    }
  }, [downsampled])

  const getRangeIndices = useCallback(
    (arr: number[], min: number, max: number): [number, number] => {
      let start = 0
      let end = arr.length - 1
      while (start < arr.length && arr[start] < min) start++
      while (end >= 0 && arr[end] > max) end--
      return [start, end]
    },
    [],
  )

  const handleRelayout = useCallback(
    (event: Record<string, unknown>) => {
      if (!data) return
      const xMin = event["xaxis.range[0]"] as number | undefined
      const xMax = event["xaxis.range[1]"] as number | undefined
      const yMin = event["yaxis.range[0]"] as number | undefined
      const yMax = event["yaxis.range[1]"] as number | undefined
      if (xMin === undefined || xMax === undefined || yMin === undefined || yMax === undefined) {
        setZmin(null)
        setZmax(null)
        return
      }
      const [xStart, xEnd] = getRangeIndices(data.x, xMin, xMax)
      const [yStart, yEnd] = getRangeIndices(data.y, yMin, yMax)
      const visibleValues: number[] = []
      for (let i = yStart; i <= yEnd; i++) {
        const row = data.z[i]
        if (!row) continue
        for (let j = xStart; j <= xEnd; j++) {
          const val = row[j]
          if (val !== undefined && Number.isFinite(val)) visibleValues.push(val)
        }
      }
      if (visibleValues.length > 0) {
        visibleValues.sort((a, b) => a - b)
        setZmin(visibleValues[Math.floor(visibleValues.length * 0.05)])
        setZmax(visibleValues[Math.floor(visibleValues.length * 0.95)])
      } else {
        setZmin(null)
        setZmax(null)
      }
    },
    [data, getRangeIndices],
  )

  if (error) return <div className="text-danger">Error: {error}</div>
  if (!data || !downsampled) return <div>Loading heatmap…</div>

  const { x, y, z } = downsampled

  return (
    <div className="plot-wrapper d-flex flex-column">
      <h2 className="p-2">{input.title}</h2>
      <Plot
        data={[
          {
            x,
            y,
            z,
            type: "heatmap",
            colorscale: "Viridis",
            zmin: zmin ?? undefined,
            zmax: zmax ?? undefined,
            colorbar: input.axisz ? { title: { text: input.axisz } } : undefined,
          },
        ]}
        layout={{
          autosize: true,
          height: 400,
          margin: { l: 60, r: 20, t: 40, b: 80 },
          xaxis: { title: { text: input.axisx || "Frequency [MHz]" } },
          yaxis: { title: { text: input.axisy || "LST [hr]" } },
        }}
        useResizeHandler={true}
        style={{ width: "90%" }}
        onRelayout={handleRelayout}
      />
    </div>
  )
}
