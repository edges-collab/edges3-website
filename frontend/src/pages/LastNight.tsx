/**
 * "Last night" (home page): the most recent MRO night at a glance, from
 * the precomputed quick-look and L1 products (`GET /api/night`). The
 * `?date=YYYY-MM-DD` query parameter selects another night (named by the
 * local date of its evening); previous/next step through nights.
 */
import { useEffect, useState } from "react"
import { useSearchParams } from "react-router"
import { BASE_URL } from "../utils/baseURL"
import type { NightPayload } from "../types/night"
import { shiftDate, toSiteTime } from "../utils/nightData"
import NightFigure from "../components/NightFigure"
import FileQATable from "../components/FileQATable"

type Quantity = "q" | "p0"

export default function LastNight() {
  const [params, setParams] = useSearchParams()
  const date = params.get("date")
  const [quantity, setQuantity] = useState<Quantity>("q")
  // The payload and the `date` it was requested for, so a previous night is
  // never shown under a new URL while it loads (or after an error).
  const [loaded, setLoaded] = useState<{ date: string | null; payload: NightPayload } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [pick, setPick] = useState("") // date-picker text, committed on Enter/blur

  useEffect(() => {
    const ctrl = new AbortController()
    const q = new URLSearchParams()
    if (date) q.set("date", date)
    if (quantity === "p0") q.set("p0", "true")
    setLoading(true)
    setError(null)
    fetch(`${BASE_URL}/api/night?${q}`, { signal: ctrl.signal })
      .then(async (r) => {
        if (!r.ok) {
          const body = await r.json().catch(() => ({}))
          throw new Error(body.detail ?? `HTTP ${r.status}`)
        }
        return r.json() as Promise<NightPayload>
      })
      .then((d) => {
        if (ctrl.signal.aborted) return
        setLoaded({ date, payload: d })
        setPick(d.night.date)
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false)
      })
    return () => ctrl.abort()
  }, [date, quantity])

  const data = loaded && loaded.date === date ? loaded.payload : null
  const night = data?.night
  const go = (d: string | null) => setParams(d ? { date: d } : {})
  const canNext = night && night.latest_date !== null && night.date < night.latest_date
  // Switching to p0 refetches; keep showing Q until the p0 waterfall arrives.
  const shown: Quantity = quantity === "p0" && !data?.quicklook.waterfall_p0 ? "q" : quantity
  const commitPick = () => {
    if (/^(19|20)\d\d-\d\d-\d\d$/.test(pick) && pick !== (night?.date ?? date)) go(pick)
  }

  return (
    <div className="d-flex flex-column p-3 gap-3">
      <div className="d-flex flex-wrap align-items-center gap-3">
        <h2 className="m-0">
          {night ? `Night of ${night.date}` : date ? `Night of ${date}` : "Last night"}
          {night?.is_latest && <span className="badge text-bg-primary ms-2 fs-6">latest</span>}
        </h2>
        {night && (
          <span className="text-muted">
            {toSiteTime(night.start_unix, night.utc_offset_hours).slice(11, 16)}–
            {toSiteTime(night.end_unix, night.utc_offset_hours).slice(11, 16)} {night.timezone}
          </span>
        )}
        <div className="btn-group btn-group-sm" role="group" aria-label="Night navigation">
          <button className="btn btn-outline-primary" disabled={!night}
            onClick={() => night && go(shiftDate(night.date, -1))}>← Previous</button>
          <button className="btn btn-outline-primary" disabled={!canNext}
            onClick={() => night && go(shiftDate(night.date, 1))}>Next →</button>
          <button className="btn btn-outline-primary" disabled={!date}
            onClick={() => go(null)}>Latest</button>
        </div>
        <input type="date" className="form-control form-control-sm w-auto"
          aria-label="Choose a night" value={pick}
          max={night?.latest_date ?? undefined}
          onChange={(e) => setPick(e.target.value)}
          onBlur={commitPick}
          onKeyDown={(e) => { if (e.key === "Enter") commitPick() }} />
        <div className="btn-group btn-group-sm" role="group" aria-label="Waterfall quantity">
          {(["q", "p0"] as const).map((k) => (
            <button key={k} className={`btn ${quantity === k ? "btn-primary" : "btn-outline-primary"}`}
              onClick={() => setQuantity(k)}>
              {k === "q" ? "Q" : "p0 (log)"}
            </button>
          ))}
        </div>
        {loading && <span className="spinner-border spinner-border-sm text-primary" role="status" />}
      </div>

      {error && (
        <div className="alert alert-warning">
          Could not load the night: {error}
        </div>
      )}

      {data && (
        <>
          {data.warnings.map((w) => (
            <div key={w} className="alert alert-secondary py-1 mb-0">{w}</div>
          ))}
          {!data.quicklook.available && (
            <div className="alert alert-info py-2 mb-0">
              No waterfall: {data.quicklook.reason ?? "no quick-look products"}
              {data.quicklook.coverage?.t_first_unix != null && (
                <> (quick-look products cover{" "}
                  {new Date(data.quicklook.coverage.t_first_unix * 1000).toISOString().slice(0, 10)} to{" "}
                  {new Date((data.quicklook.coverage.t_last_unix ?? 0) * 1000).toISOString().slice(0, 10)} UTC)</>
              )}
            </div>
          )}
          <div className="border rounded p-2">
            <NightFigure data={data} quantity={shown} />
            <p className="text-muted small mb-0 px-2">
              Uncalibrated quick-look products. Waterfall: {data.quicklook.n_cycles ?? 0} cycles
              {data.quicklook.decimation && data.quicklook.decimation > 1
                ? ` (averaged up to ${data.quicklook.decimation} per row, ${data.quicklook.n_rows} rows)` : ""}
              {" "}from {data.quicklook.files?.length ?? 0} files
              {data.quicklook.missing_files?.length ? `; unreadable: ${data.quicklook.missing_files.join(", ")}` : ""}.
              Blank columns and broken lines are gaps in the data.
            </p>
          </div>
          <div className="border rounded p-3">
            <h3 className="h5">Files</h3>
            <FileQATable files={data.files} utcOffsetHours={data.night.utc_offset_hours} />
            <p className="text-muted small mb-0">
              Badges use provisional thresholds: ADC full scale |x| ≥ {data.thresholds.adc_full_scale},
              RFI occupancy &gt; {100 * data.thresholds.rfi_occupancy}%, outliers &gt; {data.thresholds.outlier_cycles} cycles,
              short &lt; {100 * data.thresholds.short_fraction}% of the night's longest file.
              Generated {data.generated_at} in {data.elapsed_s} s.
            </p>
          </div>
        </>
      )}
    </div>
  )
}
