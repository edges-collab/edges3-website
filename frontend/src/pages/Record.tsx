/**
 * Record: a receiver's whole record at a glance, from the catalog. Per UTC
 * day: hours of antenna data (and of calibration loads; spectrum files
 * before extraction), GB, S11 sessions, data drops and the largest ADC
 * value. Click a day to open its night, or zoom in to a month or less to
 * open those nights, on the Night tab.
 */
import { useMemo, useState } from "react"
import { useNavigate } from "react-router"
import type { Data } from "plotly.js"
import TimeStrips, { type Strip } from "../components/TimeStrips"
import { MUTED, SERIES } from "../components/StackedPlot"
import { useJson } from "../hooks/useJson"
import { useReceiver } from "../state/Receivers"
import { nightOf } from "../utils/nightData"
import type { Overview } from "../types/browse"

const CRITICAL = "#d03b3b"
const MAX_NIGHTS = 31
const DAY = 86400

const ymd = (t: number) => new Date(t * 1000).toISOString().slice(0, 10)
const unixOf = (d: string) => Date.parse(`${d}T00:00:00Z`) / 1000
const nums = (a: (number | null)[] | undefined) => (a ?? []).map((v) => (v === null ? NaN : v))

export default function Record() {
  const r = useReceiver()
  const navigate = useNavigate()
  const { data: ov, error } = useJson<Overview>(`/api/browse/${r.name}/overview`)
  const [zoom, setZoom] = useState<[number, number] | null>(null)
  const off = r.utc_offset_hours

  const openNights = (first: string, n = 1) =>
    navigate(`/${r.name}/night?date=${first}${n > 1 ? `&nights=${n}` : ""}`)

  const s = ov?.summary
  const extracted = (s?.n_extracted ?? 0) > 0
  const strips: Strip[] = useMemo(() => {
    if (!ov || !ov.days.length) return []
    const bar = (y: (number | null)[] | undefined, name: string, color = SERIES[0]): Partial<Data> => ({
      type: "bar", x: ov.days, y: nums(y), name, marker: { color }, customdata: ov.days,
      hovertemplate: `%{x}<br>${name}: %{y}<extra></extra>`, showlegend: false,
    })
    const cal = (s?.n_calibration_files ?? 0) > 0
    const stacked = (a: Partial<Data>, b: Partial<Data>) => (cal ? [a, { ...b, showlegend: true }] : [a])
    const out: Strip[] = [
      extracted
        ? { title: "hours", traces: stacked({ ...bar(ov.hours, "antenna"), showlegend: cal },
            bar(ov.cal_hours, "calibration loads", SERIES[1])), weight: 1.4 }
        : { title: "files", traces: stacked({ ...bar(ov.files, "antenna files"), showlegend: cal },
            bar(ov.cal_files, "calibration-load files", SERIES[1])), weight: 1.4 },
      { title: "GB", traces: [bar(ov.gb, "GB of spectra", SERIES[2])] },
      { title: "S11", traces: [bar(ov.s11_sessions, "S11 sessions", MUTED)] },
    ]
    if (extracted) {
      out.push({ title: "drops", traces: [bar(ov.drops, "data drops", CRITICAL)] })
      out.push({ title: "max ADC", traces: [{
        type: "scatter", mode: "markers", x: ov.days, y: nums(ov.adcmax), name: "largest ADC value",
        marker: { color: MUTED, size: 4 }, customdata: ov.days, showlegend: false,
      }] })
    }
    return out
  }, [ov, extracted, s])

  // the nights of the zoomed range (a UTC day's night: the one around its noon)
  const zoomNights = zoom
    ? { first: nightOf(zoom[0] + DAY / 2, off), n: Math.round((zoom[1] - zoom[0]) / DAY) + 1 }
    : null

  return (
    <div className="d-flex flex-column p-3 gap-3">
      <div className="d-flex flex-wrap align-items-center gap-3">
        <h2 className="m-0">{r.label}: the whole record</h2>
        {s && s.t_first_unix !== null && (
          <span className="text-muted small">
            {ymd(s.t_first_unix)} to {ymd(s.t_last_unix ?? s.t_first_unix)}: {s.n_files} spectrum files
            ({s.tb.toFixed(2)} TB) on {s.days_with_data} days
            {extracted ? `, ${s.hours.toFixed(0)} h of data` : ""}; {s.n_s11_sessions} S11 sessions
          </span>
        )}
      </div>
      {error && <div className="alert alert-warning">{error}</div>}
      {s && s.n_extracted < s.n_files && (
        <div className="alert alert-secondary py-1 small mb-0">
          {s.n_extracted === 0 ? "No" : `${s.n_extracted} of ${s.n_files}`} spectrum files have been extracted by the
          catalog yet, so the times, cycles, data drops and ADC values fill in as extraction runs. Until then each
          file is placed at its file-name time stamp.
        </div>
      )}

      <section className="border rounded p-2">
        <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
          <h3 className="h6 m-0">Per UTC day</h3>
          <span className="small text-muted">
            Click a day to open its night, or zoom in to {MAX_NIGHTS} days or less to open those nights.
          </span>
          {zoomNights && zoomNights.n <= MAX_NIGHTS && (
            <button className="btn btn-sm btn-primary ms-auto" onClick={() => openNights(zoomNights.first, zoomNights.n)}>
              Open these {zoomNights.n} nights →
            </button>
          )}
        </div>
        {!ov ? <div className="text-muted small p-3">{error ? "" : "Loading the record…"}</div>
          : strips.length === 0 ? <div className="text-muted small p-3">No data for this receiver.</div>
            : <TimeStrips strips={strips} height={extracted ? 560 : 400} revision={r.name} barmode="stack"
                timeTitle="UTC day" onXRange={setZoom}
                onPick={(d) => openNights(nightOf(unixOf(String(d)) + DAY / 2, off))} />}
      </section>
    </div>
  )
}
