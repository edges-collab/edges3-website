import { useState } from "react"
import type { Parameter } from "./ParameterSelector.tsx"
import ParameterSelector from "./ParameterSelector.tsx"
import { BASE_URL } from "../utils/baseURL"
import { useRunState } from "../state/RunContext"

export type ParameterSet = Parameter[]

type ParameterPanelProps = {
  parameterSet: ParameterSet
  selectedDates: { cal: string; s11: string; raw: string }
}

export default function ParameterPanel({ parameterSet, selectedDates }: ParameterPanelProps) {
  const { bumpRefresh } = useRunState()
  const initialValues: Record<string, string | number | boolean> = {}
  parameterSet.forEach((p) => {
    initialValues[p.name] = p.defaultValue
  })
  const [paramValues, setParamValues] = useState(initialValues)
  const [isRunning, setIsRunning] = useState(false)
  const [errorMsg, setErrorMsg] = useState("")

  const handleParamChange = (name: string, value: string | number | boolean) => {
    setParamValues((prev) => ({ ...prev, [name]: value }))
  }

  const handleApply = async () => {
    setIsRunning(true)
    setErrorMsg("")
    try {
      const response = await fetch(`${BASE_URL}/run_pipeline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dates: selectedDates,
          parameters: paramValues,
        }),
      })
      if (!response.ok) {
        const text = await response.text()
        throw new Error(`HTTP ${response.status}: ${text.slice(0, 200)}`)
      }
      const result = (await response.json()) as { success: boolean; message?: string }
      if (!result.success) {
        throw new Error(result.message || "Pipeline failed")
      }
      // Signal every page to re-fetch the manifest + latest_run banner.
      bumpRefresh()
    } catch (error: unknown) {
      setErrorMsg(error instanceof Error ? error.message : String(error))
    } finally {
      setIsRunning(false)
    }
  }

  return (
    <div className="p-3 border rounded">
      <h1>Parameters</h1>
      {parameterSet.map((parameter) => (
        <ParameterSelector
          key={parameter.name}
          parameter={parameter}
          value={paramValues[parameter.name]}
          onChange={(val) => handleParamChange(parameter.name, val)}
        />
      ))}
      <button onClick={handleApply} disabled={isRunning} className="btn btn-primary">
        {isRunning ? "Running…" : "Run with these dates"}
      </button>
      {errorMsg && <div className="text-danger mt-2">{errorMsg}</div>}
      <p className="mt-2 text-muted small">
        Note: this may take up to a minute to process.
      </p>
      <p className="mt-2 text-muted small">
        Clicking "Run with these dates" wipes any previous custom-run outputs and
        replaces them with the new ones. A run with the same dates and parameters
        as a previous one is reused automatically (no re-computation).
      </p>
    </div>
  )
}
