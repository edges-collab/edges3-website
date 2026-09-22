import { useEffect, useState } from "react"
import Plot from "../utils/plotComponent"
import { openDataFile } from "../utils/dataLoader"
import type { Data } from "../utils/dataLoader"
import { withBaseUrl } from "../utils/baseURL"
import type { SinglePlot } from "../types/manifest"

type Props = {
  input: SinglePlot
}

export default function SinglePlotter({ input }: Props) {
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
     
    setError(null)
    setData(null)
    openDataFile(withBaseUrl(input.filePath))
      .then((d) => { if (!cancelled) setData(d) })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => { cancelled = true }
  }, [input.filePath])

  if (error) return <div className="text-danger">Error: {error}</div>
  if (!data) return <div>Loading data…</div>

  return (
    <div className="plot-wrapper d-flex flex-column">
      <div className="d-flex flex-column w-100 align-items-start">
        <h2 className="p-2">{input.title}</h2>
      </div>
      <Plot
        data={[{ x: data.x, y: data.y, type: "scatter", mode: "lines" }]}
        layout={{
          autosize: true,
          height: 300,
          margin: { l: 60, r: 20, t: 40, b: 60 },
          xaxis: { title: { text: input.axisx || "Frequency [MHz]" } },
          yaxis: { title: { text: input.axisy || "Y [arb. units]" } },
        }}
        useResizeHandler={true}
        style={{ width: "90%" }}
      />
    </div>
  )
}
