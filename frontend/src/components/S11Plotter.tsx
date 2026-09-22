/**
 * S11 plotter — reads frequency/real/imag .npz keys via s11Loader.ts and
 * shows Re/Im or Abs/Arg traces with a toggle.
 */

import { useEffect, useState } from "react"
import Plot from "../utils/plotComponent"
import { openS11File } from "../utils/s11Loader"
import type { S11Data } from "../utils/s11Loader"
import { withBaseUrl } from "../utils/baseURL"
import ComplexSelector from "./ComplexSelector"
import type { S11Plot } from "../types/manifest"

type Props = {
  s11Input: S11Plot
}

export default function S11Plotter({ s11Input }: Props) {
  const [complexDisplay, setComplexDisplay] = useState("Re/Im")
  const [s11Data, setS11Data] = useState<S11Data | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
     
    setError(null)
    setS11Data(null)
    openS11File(withBaseUrl(s11Input.filePath))
      .then((data) => {
        if (!cancelled) setS11Data(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [s11Input.filePath])

  if (error) return <div className="text-danger">Error: {error}</div>
  if (!s11Data) return <div>Loading S11 data…</div>

  const xAxisTitle = "Frequency [MHz]"
  const yAxisTitle = complexDisplay === "Re/Im" ? "Reflection coefficient" : "Magnitude / Phase"

  const traces =
    complexDisplay === "Re/Im"
      ? [
          { x: s11Data.frequency, y: s11Data.s11Real, type: "scatter" as const, mode: "lines" as const, name: "Real" },
          { x: s11Data.frequency, y: s11Data.s11Imag, type: "scatter" as const, mode: "lines" as const, name: "Imaginary" },
        ]
      : (() => {
          const abs = s11Data.s11Real.map((r, i) =>
            Math.sqrt(r * r + s11Data.s11Imag[i] * s11Data.s11Imag[i]),
          )
          const arg = s11Data.s11Real.map((r, i) => Math.atan2(s11Data.s11Imag[i], r))
          return [
            { x: s11Data.frequency, y: abs, type: "scatter" as const, mode: "lines" as const, name: "Magnitude" },
            { x: s11Data.frequency, y: arg, type: "scatter" as const, mode: "lines" as const, name: "Phase (rad)" },
          ]
        })()

  return (
    <div className="plot-wrapper d-flex flex-column">
      <div className="d-flex flex-column w-100 align-items-start">
        <h2 className="p-2">{s11Input.title}</h2>
        <ComplexSelector onSelectChange={setComplexDisplay} />
      </div>
      <Plot
        data={traces}
        layout={{
          autosize: true,
          height: 300,
          margin: { l: 60, r: 20, t: 40, b: 60 },
          showlegend: true,
          xaxis: { title: { text: xAxisTitle } },
          yaxis: { title: { text: yAxisTitle } },
        }}
        useResizeHandler={true}
        style={{ width: "90%" }}
      />
    </div>
  )
}
