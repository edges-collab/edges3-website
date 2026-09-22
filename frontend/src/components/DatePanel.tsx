import { useEffect, useState } from "react"
import { BASE_URL } from "../utils/baseURL"

export type DateSet = {
  calDate: string[]
  s11Date: string[]
  rawDate: string[]
}

type DatePanelProps = {
  dateSet: DateSet
  onDatesChange: (dates: { cal: string; s11: string; raw: string }) => void
}

type AvailableDates = {
  calibration: string[]
  s11: string[]
  raw: string[]
}

export default function DatePanel({ dateSet, onDatesChange }: DatePanelProps) {
  const [selectedCal, setSelectedCal] = useState("Latest")
  const [selectedS11, setSelectedS11] = useState("Latest")
  const [selectedRaw, setSelectedRaw] = useState("Latest")

  const [available, setAvailable] = useState<AvailableDates>({
    calibration: dateSet?.calDate ?? [],
    s11: dateSet?.s11Date ?? [],
    raw: dateSet?.rawDate ?? [],
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
     
    setLoading(true)
    setError(null)
    fetch(`${BASE_URL}/available_dates`, { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<AvailableDates>
      })
      .then((data) => {
        if (cancelled) return
        setAvailable({
          calibration: data.calibration ?? [],
          s11: data.s11 ?? [],
          raw: data.raw ?? [],
        })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleChange = (type: "cal" | "s11" | "raw", value: string) => {
    if (type === "cal") setSelectedCal(value)
    if (type === "s11") setSelectedS11(value)
    if (type === "raw") setSelectedRaw(value)
    onDatesChange({
      cal: type === "cal" ? value : selectedCal,
      s11: type === "s11" ? value : selectedS11,
      raw: type === "raw" ? value : selectedRaw,
    })
  }

  const withLatest = (arr: string[]) => ["Latest", ...(arr ?? [])]

  return (
    <div className="p-3 border rounded">
      <h1>Dates</h1>
      {error && <div className="text-danger small mb-2">{error}</div>}
      {loading && <div className="text-muted small mb-2">Loading dates…</div>}

      <div className="d-flex gap-4 align-items-center p-2 justify-content-between">
        <span>Calibration Date</span>
        <select value={selectedCal} onChange={(e) => handleChange("cal", e.target.value)}>
          {withLatest(available.calibration).map((date) => (
            <option key={date} value={date}>{date}</option>
          ))}
        </select>
      </div>

      <div className="d-flex gap-4 align-items-center p-2 justify-content-between">
        <span>S11 Date</span>
        <select value={selectedS11} onChange={(e) => handleChange("s11", e.target.value)}>
          {withLatest(available.s11).map((date) => (
            <option key={date} value={date}>{date}</option>
          ))}
        </select>
      </div>

      <div className="d-flex gap-4 align-items-center p-2 justify-content-between">
        <span>Raw Data Date</span>
        <select value={selectedRaw} onChange={(e) => handleChange("raw", e.target.value)}>
          {withLatest(available.raw).map((date) => (
            <option key={date} value={date}>{date}</option>
          ))}
        </select>
      </div>

      <p className="text-muted small mb-0 mt-2">
        Pick a specific date or leave on "Latest" to use the most recent available one.
      </p>
    </div>
  )
}
