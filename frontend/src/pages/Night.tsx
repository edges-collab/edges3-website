/**
 * Night: one night of a receiver (or up to a month of nights), everything
 * the site has for it. Nights are named by the site date of their evening
 * and run 18:00-06:00 site time; ``?date=YYYY-MM-DD&nights=N`` (default:
 * the latest night with data). Previous/next skip to the nearest nights
 * with antenna data.
 *
 * - With the pipeline's night products (EDGES-3, one night): the night
 *   figure (`GET /api/night`): the quick-look waterfall (median, mean or
 *   max binning, or p0), events, the L1 band series and the receiver's
 *   housekeeping, with the file QA in the file table.
 * - Otherwise: the quick-look waterfall of the times shown.
 * - Then, from the catalog (``RangePanels``): the files and S11 sessions,
 *   housekeeping and weather, the file table and the S11 traces.
 */
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"
import { BASE_URL } from "../utils/baseURL"
import { useJson } from "../hooks/useJson"
import { useReceiver } from "../state/Receivers"
import { shiftDate, toSiteTime } from "../utils/nightData"
import type { NightPayload, WaterfallStat } from "../types/night"
import type { NightSpan } from "../types/browse"
import NightFigure from "../components/NightFigure"
import RangePanels from "../components/RangePanels"

const SPANS = [1, 2, 3, 7, 14, 31]

type View = WaterfallStat | "p0"

const VIEWS: [View, string, string][] = [
  ["median", "Q, RFI removed", "Median of each bin's ~80 channels: hides narrowband RFI"],
  ["mean", "Q mean, RFI kept", "Mean of each bin's channels: RFI shows, diluted ~80×"],
  ["max", "Q max, RFI kept", "Max of each bin's channels: shows RFI best (biased ~0.014 high by noise)"],
  ["p0", "p0 (log)", "Antenna power, log scale"],
]

export default function Night() {
  const r = useReceiver()
  const [q, setQ] = useSearchParams()
  const date = q.get("date")
  const n = Math.min(31, Math.max(1, Math.round(Number(q.get("nights") ?? 1)) || 1))
  const { data: span, error } = useJson<NightSpan>(
    `/api/browse/${r.name}/night?${date ? `date=${date}&` : ""}nights=${n}`)
  const [pick, setPick] = useState("") // date-picker text, committed on Enter/blur
  useEffect(() => { if (span) setPick(span.date) }, [span])

  const go = (d: string | null, nights = n) => {
    const p = new URLSearchParams()
    if (d) p.set("date", d)
    if (nights !== 1) p.set("nights", String(nights))
    setQ(p)
  }
  const commitPick = () => {
    if (/^(19|20)\d\d-\d\d-\d\d$/.test(pick) && pick !== span?.date) go(pick)
  }
  const products = r.night_products && n === 1
  const off = r.utc_offset_hours

  return (
    <div className="d-flex flex-column p-3 gap-3">
      <div className="d-flex flex-wrap align-items-center gap-3">
        <h2 className="m-0">
          {span ? (n === 1 ? `Night of ${span.date}` : `${n} nights, ${span.date} to ${span.last_date}`)
            : date ? `Night of ${date}` : "Latest night"}
          {span?.is_latest && <span className="badge text-bg-primary ms-2 fs-6">latest</span>}
        </h2>
        {span && (
          <span className="text-muted">
            {n === 1
              ? `${toSiteTime(span.start_unix, off).slice(11, 16)}–${toSiteTime(span.end_unix, off).slice(11, 16)} ${span.timezone}`
              : `${span.nights_with_data} of ${n} nights have antenna data`}
          </span>
        )}
        <div className="btn-group btn-group-sm" role="group" aria-label="Night navigation">
          <button className="btn btn-outline-primary" disabled={!span?.prev_date}
            title="The nearest earlier night with antenna data"
            onClick={() => span?.prev_date && go(shiftDate(span.prev_date, -(n - 1)))}>← Previous</button>
          <button className="btn btn-outline-primary" disabled={!span?.next_date}
            title="The nearest later night with antenna data"
            onClick={() => span?.next_date && go(span.next_date)}>Next →</button>
          <button className="btn btn-outline-primary" disabled={!date} onClick={() => go(null)}>Latest</button>
        </div>
        <input type="date" className="form-control form-control-sm w-auto" aria-label="Choose a night" value={pick}
          min={span?.first_date ?? undefined} max={span?.latest_date ?? undefined}
          onChange={(e) => setPick(e.target.value)} onBlur={commitPick}
          onKeyDown={(e) => { if (e.key === "Enter") commitPick() }} />
        <select className="form-select form-select-sm w-auto" aria-label="Nights shown" value={n}
          onChange={(e) => go(span?.date ?? date, Number(e.target.value))}>
          {SPANS.map((k) => <option key={k} value={k}>{k === 1 ? "1 night" : `${k} nights`}</option>)}
        </select>
        {r.calibration && n === 1 && span && (
          <Link className="btn btn-sm btn-outline-primary ms-auto" to={`/${r.name}/calibrated?night=${span.date}`}>
            Calibrate this night →
          </Link>
        )}
      </div>

      {error && <div className="alert alert-warning mb-0">Could not load the night: {error}</div>}
      {span && span.nights_with_data === 0 && (
        <div className="alert alert-secondary py-1 mb-0">
          No antenna data {n === 1 ? "this night" : "these nights"}
          {span.prev_date || span.next_date
            ? ` (the nearest nights with data: ${[span.prev_date, span.next_date].filter(Boolean).join(" and ")})` : ""}.
        </div>
      )}

      {span && (products
        ? <NightProducts key={span.date} span={span} />
        : <RangePanels key={`${span.start_unix}`} receiver={r} start={span.start_unix} end={span.end_unix} />)}
    </div>
  )
}

/** One night with the pipeline's night products: the night figure, then the catalog's panels (with file QA). */
function NightProducts({ span }: { span: NightSpan }) {
  const r = useReceiver()
  const [view, setView] = useState<View>("median")
  const [relative, setRelative] = useState(false)
  const quantity = view === "p0" ? "p0" : "q"
  const [data, setData] = useState<NightPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const ctrl = new AbortController()
    const p = new URLSearchParams({ date: span.date })
    if (view === "p0") p.set("p0", "true")
    else if (view !== "median") p.set("waterfall", view)
    setLoading(true)
    setError(null)
    fetch(`${BASE_URL}/api/night?${p}`, { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`)
        return res.json() as Promise<NightPayload>
      })
      .then((d) => { if (!ctrl.signal.aborted) setData(d) })
      .catch((e: unknown) => { if (!ctrl.signal.aborted) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    return () => ctrl.abort()
  }, [span.date, view])

  // Switching refetches; keep showing what is loaded until the new one arrives.
  const shown = quantity === "p0" && !data?.quicklook.waterfall_p0 ? "q" : quantity
  const stat = data?.quicklook.waterfall_stat ?? "median"
  const rfiOk = data?.quicklook.rfi_waterfalls !== false
  const choose = (v: View) => {
    setView(v)
    if (v === "max") setRelative(true) // its noise bias is the same in every channel
  }

  return (
    <>
      <section className="border rounded p-2">
        <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
          <h3 className="h6 m-0">The night at a glance</h3>
          <div className="btn-group btn-group-sm" role="group" aria-label="Waterfall quantity">
            {VIEWS.map(([k, label, help]) => (
              <button key={k} className={`btn ${view === k ? "btn-primary" : "btn-outline-primary"}`}
                disabled={(k === "mean" || k === "max") && !rfiOk}
                title={(k === "mean" || k === "max") && !rfiOk ? "Needs QL version 3 products" : help}
                onClick={() => choose(k)}>
                {label}
              </button>
            ))}
          </div>
          <label className="form-check form-check-inline small m-0">
            <input type="checkbox" className="form-check-input" checked={relative}
              onChange={(e) => setRelative(e.target.checked)} />
            <span className="form-check-label">minus each channel's median</span>
          </label>
          {loading && <span className="spinner-border spinner-border-sm text-primary" role="status" />}
        </div>
        {error && <div className="alert alert-warning py-1 mb-1">Could not load the night's products: {error}</div>}
        {data && (
          <>
            {data.warnings.map((w) => <div key={w} className="alert alert-secondary py-1 mb-1">{w}</div>)}
            {data.dropouts.n_cycles > 0 && (
              <div className="alert alert-danger py-2 mb-1" role="alert">
                <strong>⚠ Antenna dropouts:</strong> {data.dropouts.n_cycles} cycle
                {data.dropouts.n_cycles === 1 ? "" : "s"} in {data.dropouts.n_files} file
                {data.dropouts.n_files === 1 ? "" : "s"} this night — the antenna power
                fell below the ambient load (Q &lt; 0). They are marked in red under the
                waterfall and show as horizontal stripes in it (seen since 2026-09-02).
              </div>
            )}
            {!data.quicklook.available && (
              <div className="alert alert-info py-2 mb-1">
                No waterfall: {data.quicklook.reason ?? "no quick-look products"}
                {data.quicklook.coverage?.t_first_unix != null && (
                  <> (quick-look products cover{" "}
                    {new Date(data.quicklook.coverage.t_first_unix * 1000).toISOString().slice(0, 10)} to{" "}
                    {new Date((data.quicklook.coverage.t_last_unix ?? 0) * 1000).toISOString().slice(0, 10)} UTC)</>
                )}
              </div>
            )}
            {data.quicklook.waterfall_note && (
              <div className="alert alert-secondary py-1 small mb-1">{data.quicklook.waterfall_note}</div>
            )}
            <NightFigure data={data} quantity={shown} relative={relative} />
            <p className="text-muted small mb-0 px-2">
              Uncalibrated quick-look and L1 products. Waterfall: {data.quicklook.n_rows} rows
              {data.quicklook.decimation && data.quicklook.decimation > 1
                ? ` (up to ${data.quicklook.decimation} cycles averaged per row, never across a gap)` : " (one per cycle)"}
              {" "}from {data.quicklook.files?.length ?? 0} files
              {data.quicklook.missing_files?.length ? `; unreadable: ${data.quicklook.missing_files.join(", ")}` : ""}.
              {shown === "q" && (stat === "median"
                ? " Each 0.5 MHz bin is the median of its ~80 channels, which hides narrowband RFI (choose mean or max to keep it)."
                : ` Each 0.5 MHz bin is the ${stat} of its ~80 channels, which keeps narrowband RFI${stat === "max" ? " (and sits ~0.014 above the median everywhere: noise)" : " (diluted ~80×)"}.`)}
              {" "}Blank columns and broken lines are gaps in the data. Generated {data.generated_at} in {data.elapsed_s} s.
            </p>
          </>
        )}
      </section>
      <RangePanels receiver={r} start={span.start_unix} end={span.end_unix} figure night={data} />
    </>
  )
}
