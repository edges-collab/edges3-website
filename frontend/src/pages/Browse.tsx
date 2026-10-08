/**
 * Raw data (EDGES-3, EDGES-2 low2): a receiver's whole record at a glance,
 * then any range of up to two months in detail, from the catalog only.
 *
 * - Overview, per UTC day over the whole record: hours of antenna data (and
 *   of calibration loads; spectrum files before extraction), GB, S11
 *   sessions, data drops and the largest ADC value. Zoom in to two months or
 *   less, or click a day, to open it.
 * - The range: each spectrum file's span (by load), the S11 sessions, the
 *   receiver's own housekeeping and the site's weather (zoom-linked); the
 *   quick-look Q waterfall of the times shown (up to 8 days); a file table
 *   (click a file for its cycles) and the S11 sessions (click one for its
 *   raw |S11| and phase).
 *
 * URL: ``/raw?dep=<deployment>&start=YYYY-MM-DD&end=YYYY-MM-DD``.
 */
import { useMemo, useState } from "react"
import { useSearchParams } from "react-router"
import type { Data } from "plotly.js"
import TimeStrips, { utc, type Strip } from "../components/TimeStrips"
import StackedPlot, { MUTED, SERIES, type Panel } from "../components/StackedPlot"
import Waterfall from "../components/Waterfall"
import { decodeRows, minusChannelMedian } from "../utils/nightData"
import type { QuickLook, WaterfallStat } from "../types/night"
import { useJson } from "../hooks/useJson"
import { magPhase } from "../utils/robust"
import { Link } from "react-router"
import type {
  BrowseFile, Cycles, Deployment, Housekeeping, Overview, RangeData, S11Session, S11Traces, Weather,
} from "../types/browse"

const CRITICAL = "#d03b3b"
const MAX_RANGE_DAYS = 62
const QL_MAX_DAYS = 8
const DEFAULT_DEPLOYMENT = "edges3-mro"
/** Housekeeping not plotted (not temperatures or voltages one reads by eye). */
const HK_SKIP = new Set(["pr59_current", "setpoint", "thermal_control"])
/** "front_end_temperature" -> "front end"; "sensors_frontend_low3_temperature" -> "frontend low3"
 * (EDGES-2 sensor files name the front end they claim to measure). */
const pretty = (k: string) => k.replace(/^sensors_/, "").replace(/_temperature$/, "").replace(/_/g, " ")
const DAY = 86400

const ymd = (t: number) => new Date(t * 1000).toISOString().slice(0, 10)
const unixOf = (d: string) => Date.parse(`${d}T00:00:00Z`) / 1000
const nums = (a: (number | null)[] | undefined) => (a ?? []).map((v) => (v === null ? NaN : v))
const fmt = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "–" : v.toFixed(digits))

export default function Browse() {
  const [q, setQ] = useSearchParams()
  const dep = q.get("dep") ?? DEFAULT_DEPLOYMENT
  const start = q.get("start")
  const end = q.get("end")
  const { data: deps } = useJson<Deployment[]>("/api/browse/deployments")
  const { data: ov, error: ovError } = useJson<Overview>(`/api/browse/${dep}/overview`)
  const [zoom, setZoom] = useState<[number, number] | null>(null)

  const open = (t0: number, t1: number) => {
    const n = new URLSearchParams(q)
    n.set("start", ymd(t0))
    n.set("end", ymd(t1))
    setQ(n)
  }
  const setDep = (d: string) => setQ({ dep: d })

  const s = ov?.summary
  const extracted = (s?.n_extracted ?? 0) > 0
  const overviewStrips: Strip[] = useMemo(() => {
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

  const zoomDays = zoom ? (zoom[1] - zoom[0]) / DAY : null

  return (
    <div className="d-flex flex-column p-3 gap-3">
      <div className="d-flex flex-wrap align-items-center gap-3">
        <h2 className="m-0">Raw data</h2>
        <select className="form-select form-select-sm w-auto" value={dep} onChange={(e) => setDep(e.target.value)}
          aria-label="Receiver">
          {(deps ?? [{ name: dep, label: dep }]).map((d) => <option key={d.name} value={d.name}>{d.label}</option>)}
        </select>
        {s && s.t_first_unix !== null && (
          <span className="text-muted small">
            {ymd(s.t_first_unix)} to {ymd(s.t_last_unix ?? s.t_first_unix)}: {s.n_files} spectrum files
            ({s.tb.toFixed(2)} TB) on {s.days_with_data} days
            {extracted ? `, ${s.hours.toFixed(0)} h of data` : ""}; {s.n_s11_sessions} S11 sessions
          </span>
        )}
      </div>
      {ovError && <div className="alert alert-warning">{ovError}</div>}
      {s && s.n_extracted < s.n_files && (
        <div className="alert alert-secondary py-1 small mb-0">
          {s.n_extracted === 0 ? "No" : `${s.n_extracted} of ${s.n_files}`} spectrum files have been extracted by the
          catalog yet, so the times, cycles, data drops and ADC values fill in as extraction runs. Until then each
          file is placed at its file-name time stamp.
        </div>
      )}

      <section className="border rounded p-2">
        <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
          <h3 className="h6 m-0">The whole record (per UTC day)</h3>
          <span className="small text-muted">
            Zoom in to {MAX_RANGE_DAYS} days or less, or click a day, to see it in detail below.
          </span>
          {zoom && zoomDays !== null && zoomDays <= MAX_RANGE_DAYS && (
            <button className="btn btn-sm btn-primary ms-auto" onClick={() => open(zoom[0], zoom[1] + 1)}>
              Show {ymd(zoom[0])} to {ymd(zoom[1])}
            </button>
          )}
        </div>
        {!ov ? <div className="text-muted small p-3">Loading the record…</div>
          : overviewStrips.length === 0 ? <div className="text-muted small p-3">No data for this receiver.</div>
            : <TimeStrips strips={overviewStrips} height={extracted ? 520 : 380} revision={dep} barmode="stack"
                onXRange={setZoom}
                onPick={(d) => { const t = unixOf(String(d)); open(t - 3 * DAY, t + 4 * DAY) }} />}
      </section>

      {start && end && <RangeView dep={dep} start={start} end={end} onRange={open}
        band={deps?.find((d) => d.name === dep)?.band_mhz ?? null} />}
    </div>
  )
}

function RangeView({ dep, start, end, onRange, band }: {
  dep: string; start: string; end: string; onRange: (t0: number, t1: number) => void
  band: [number, number] | null
}) {
  const t0 = unixOf(start), t1 = unixOf(end)
  const qs = `start=${start}&end=${end}`
  const { data: r, error } = useJson<RangeData>(`/api/browse/${dep}/range?${qs}`)
  const { data: w } = useJson<Weather>(`/api/browse/${dep}/weather?${qs}`)
  const { data: hk } = useJson<Housekeeping>(`/api/browse/${dep}/housekeeping?${qs}`)
  const [fileId, setFileId] = useState<number | null>(null)
  const [session, setSession] = useState<S11Session | null>(null)
  const pick = (stamp: number) => setSession(r?.s11_sessions.find((x) => x.stamp_unix === stamp) ?? null)
  const span = t1 - t0
  // both figures show the same times; zooming either zooms both
  const [view, setView] = useState<[number, number] | null>(null)
  const shown: [number, number] = view ?? [t0, t1]

  const strips: Strip[] = useMemo(() => {
    if (!r) return []
    // each extracted file as a bar over its span; others at their name's stamp
    const seg = (fs: BrowseFile[]) => ({
      x: fs.flatMap((f) => [utc(f.t_start_unix), utc(f.t_end_unix), null]),
      y: fs.flatMap(() => [0, 0, null]),
      customdata: fs.flatMap((f) => [f.file_id, f.file_id, null]),
      text: fs.flatMap((f) => [f.name, f.name, ""]),
    })
    const ext = r.files.filter((f) => f.t_start_unix !== null && f.t_end_unix !== null)
    const isAnt = (f: BrowseFile) => !f.load || f.load === "ant"
    const ok = ext.filter((f) => !f.data_drops && isAnt(f)), bad = ext.filter((f) => !!f.data_drops)
    const calOk = ext.filter((f) => !f.data_drops && !isAnt(f))
    const pending = r.files.filter((f) => f.t_start_unix === null)
    const files: Partial<Data>[] = [
      { type: "scatter", mode: "lines", ...seg(ok), name: "antenna file", line: { color: SERIES[0], width: 12 },
        hovertemplate: "%{text}<extra></extra>" },
      ...(calOk.length ? [{ type: "scatter" as const, mode: "lines" as const, ...seg(calOk), name: "calibration-load file",
        line: { color: SERIES[1], width: 12 }, hovertemplate: "%{text}<extra></extra>" }] : []),
      { type: "scatter", mode: "lines", ...seg(bad), name: "file with data drops", line: { color: CRITICAL, width: 12 },
        hovertemplate: "%{text}<extra></extra>" },
      { type: "scatter", mode: "markers", x: pending.map((f) => utc(f.stamp_unix)), y: pending.map(() => 0),
        customdata: pending.map((f) => f.file_id), text: pending.map((f) => f.name),
        name: "file (not extracted yet: at its name's time)", marker: { color: MUTED, symbol: "triangle-right", size: 10 },
        hovertemplate: "%{text}<extra></extra>" },
    ]
    const s11: Partial<Data>[] = [{
      type: "scatter", mode: "markers", x: r.s11_sessions.map((x) => utc(x.stamp_unix)), y: r.s11_sessions.map(() => 0),
      customdata: r.s11_sessions.map((x) => `s11:${x.stamp_unix}`), text: r.s11_sessions.map((x) => x.name),
      name: "S11 session (click)", marker: { color: MUTED, symbol: "diamond", size: 9 },
      hovertemplate: "S11 %{text}<extra></extra>",
    }]
    return [
      { title: "files", traces: files, weight: 0.5, events: true },
      { title: "S11", traces: s11, weight: 0.4, events: true },
    ]
  }, [r])

  const conditions: Strip[] = useMemo(() => {
    const out: Strip[] = []
    // the receiver's own log, one strip per unit (the hot load apart: ~100 °C)
    if (hk?.available && hk.series) {
      const groups = new Map<string, [string, (typeof hk.series)[string]][]>()
      for (const [k, v] of Object.entries(hk.series)) {
        if (HK_SKIP.has(k)) continue
        const g = k.startsWith("hot_load") ? `hot load [${v.unit}]` : `receiver log [${v.unit || "?"}]`
        groups.set(g, [...(groups.get(g) ?? []), [k, v]])
      }
      for (const [title, items] of groups) {
        out.push({ title, weight: 0.8, traces: items.map(([k, v], i) => ({
          // sparse logs (EDGES-2: one reading per S11 session) as points
          type: "scatter", mode: v.value.length < 300 ? "lines+markers" : "lines", marker: { size: 4 },
          x: v.time_unix.map(utc), y: nums(v.value), name: pretty(k),
          line: { color: [SERIES[0], SERIES[1], SERIES[2], MUTED][i % 4], width: 1 },
        })) })
      }
    }
    if (w?.available && w.series && w.time_unix) {
      const x = w.time_unix.map(utc)
      const line = (k: string, name: string, color: string): Partial<Data> => ({
        type: "scatter", mode: "lines", x, y: nums(w.series![k]), name, line: { color, width: 1 },
      })
      const temps = [["ambient_temp", "site: ambient", SERIES[0]], ["rack_temp", "site: rack", SERIES[1]],
        ["frontend", "site: front end (receiver not recorded)", MUTED]] as const
      const t = temps.filter(([k]) => w.series![k]).map(([k, n, c]) => line(k, n, c))
      if (t.length) out.push({ title: "site weather [K]", traces: t })
      if (w.series.ambient_hum) out.push({ title: "site humidity [%]", traces: [line("ambient_hum", "site: humidity", SERIES[2])], weight: 0.6 })
    }
    return out
  }, [w, hk])

  return (
    <>
      <section className="border rounded p-2">
        <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
          <h3 className="h6 m-0">{start} to {end} (UTC)</h3>
          <div className="btn-group btn-group-sm">
            <button className="btn btn-outline-primary" onClick={() => onRange(t0 - span, t0)}>← Earlier</button>
            <button className="btn btn-outline-primary" onClick={() => onRange(t1, t1 + span)}>Later →</button>
          </div>
          {r && <span className="small text-muted">{r.files.length} spectrum files, {r.s11_sessions.length} S11 sessions
            {w && !w.available ? `; weather: ${w.reason}` : ""}{hk && !hk.available ? `; housekeeping: ${hk.reason}` : ""}</span>}
          {dep === "edges3-mro" && (
            <Link className="btn btn-sm btn-outline-primary ms-auto" to={`/?date=${start}`}>
              Open the night of {start} in the Nightly Overview →
            </Link>
          )}
        </div>
        {error && <div className="alert alert-warning small py-1">{error}</div>}
        {!r ? <div className="text-muted small p-3">Loading…</div>
          : <TimeStrips strips={strips} height={230} revision={`${start}/${end}`} xRange={shown}
              onXRange={setView}
              onPick={(c) => {
                if (typeof c === "string" && c.startsWith("s11:")) pick(Number(c.slice(4)))
                else if (typeof c === "number") setFileId(c)
              }} />}
      </section>

      <section className="border rounded p-2">
        <h3 className="h6 mb-1">Receiver housekeeping and site weather</h3>
        <p className="small text-muted mb-1">
          The receiver's own log ({hk?.source ?? "none"}{hk && !hk.available ? `: ${hk.reason}` : ""}) and the MRO
          weather log ({w && !w.available ? w.reason : "every 5 min"}). The weather's front-end temperature is not
          attributed to a receiver yet (edges-database is asking the team whose it is). Zooming here zooms the files above.
        </p>
        {conditions.length === 0
          ? <div className="text-muted small p-3">{!w || !hk ? "Loading…" : "Nothing logged in this range."}</div>
          : <TimeStrips strips={conditions} height={120 + 150 * conditions.length} revision={`${start}/${end}/c`}
              xRange={shown} onXRange={setView} />}
      </section>

      <QuickLookView dep={dep} window={shown} band={band} />

      {r && r.files.length > 0 && (
        <section className="border rounded p-2">
          <h3 className="h6">Spectrum files</h3>
          <div style={{ maxHeight: 320, overflow: "auto" }}>
            <table className="table table-sm table-hover small mb-0">
              <thead className="sticky-top bg-white">
                <tr><th>File</th><th>Load</th><th>Start (UTC)</th><th className="text-end">Hours</th><th className="text-end">Cycles</th>
                  <th className="text-end">Data drops</th><th className="text-end">ADC max / min</th><th className="text-end">MB</th></tr>
              </thead>
              <tbody>
                {r.files.map((f) => (
                  <tr key={f.file_id} className={f.file_id === fileId ? "table-primary" : ""} style={{ cursor: "pointer" }}
                    onClick={() => setFileId(f.file_id)}>
                    <td className="font-monospace">{f.name}{f.category !== "science" && <span className="badge text-bg-secondary ms-1">{f.category}</span>}</td>
                    <td>{f.load ?? "–"}</td>
                    <td>{utc(f.t_start_unix ?? f.stamp_unix)}{f.t_start_unix === null && <span className="text-muted"> (name)</span>}</td>
                    <td className="text-end">{fmt(f.duration_hr)}</td>
                    <td className="text-end">{f.n_cycles ?? "–"}</td>
                    <td className={`text-end ${f.data_drops ? "text-danger fw-semibold" : ""}`}>{f.data_drops ?? "–"}</td>
                    <td className="text-end">{fmt(f.adcmax, 3)} / {fmt(f.adcmin, 3)}</td>
                    <td className="text-end">{f.size_mb.toFixed(0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {fileId !== null && <CyclesView dep={dep} fileId={fileId} />}
        </section>
      )}

      {r && r.s11_sessions.length > 0 && (
        <section className="border rounded p-2">
          <h3 className="h6">S11 sessions</h3>
          <div className="d-flex flex-wrap gap-1 mb-2">
            {r.s11_sessions.map((x) => (
              <button key={x.stamp_unix} className={`btn btn-sm ${x.stamp_unix === session?.stamp_unix ? "btn-primary" : "btn-outline-secondary"}`}
                title={`${x.kind}: ${x.labels.join(", ")}`} onClick={() => setSession(x)}>
                {x.name}{x.kind !== "antenna" ? ` (${x.kind})` : ""}
              </button>
            ))}
          </div>
          {session !== null ? <S11View dep={dep} session={session} />
            : <div className="small text-muted">Choose a session (or click one in the figure above).</div>}
        </section>
      )}
    </>
  )
}

const STATS: [WaterfallStat, string][] = [
  ["median", "median (RFI removed)"], ["mean", "mean (RFI kept)"], ["max", "max (RFI kept)"],
]

/** The pipeline's quick-look Q waterfall of the times shown (up to QL_MAX_DAYS). */
function QuickLookView({ dep, window: [w0, w1], band }: {
  dep: string; window: [number, number]; band: [number, number] | null
}) {
  const [stat, setStat] = useState<WaterfallStat>("median")
  const [relative, setRelative] = useState(false)
  const [full, setFull] = useState(false) // the whole 40-200 MHz, not the antenna's band
  const short = w1 - w0 <= QL_MAX_DAYS * DAY
  // whole minutes, so a re-render never refetches the same window
  const a = Math.floor(w0 / 60) * 60, b = Math.ceil(w1 / 60) * 60
  const { data: ql, error } = useJson<QuickLook>(
    short ? `/api/browse/${dep}/quicklook?start=${a}&end=${b}${stat === "median" ? "" : `&waterfall=${stat}`}` : null)
  const wf = useMemo(() => {
    if (!ql?.waterfall_q || !ql.time_unix || !ql.freq_mhz) return null
    // only the channels shown, so they alone set the colour scale (percentiles)
    const f = nums(ql.freq_mhz)
    const keep = f.map((x) => !band || full || (x >= band[0] && x <= band[1]))
    let z: number[][] = decodeRows(ql.waterfall_q).map((row) => Array.from(row).filter((_, j) => keep[j]))
    if (relative) z = minusChannelMedian(z)
    return { z, times: ql.time_unix.map((t) => utc(t) ?? ""), freqs: f.filter((_, j) => keep[j]) }
  }, [ql, relative, band, full])
  return (
    <section className="border rounded p-2">
      <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
        <h3 className="h6 m-0">Quick-look waterfall (Q)</h3>
        <div className="btn-group btn-group-sm" role="group" aria-label="Binning">
          {STATS.map(([k, label]) => (
            <button key={k} className={`btn ${stat === k ? "btn-primary" : "btn-outline-primary"}`}
              disabled={k !== "median" && ql?.rfi_waterfalls === false}
              onClick={() => { setStat(k); if (k === "max") setRelative(true) }}>{label}</button>
          ))}
        </div>
        <label className="form-check form-check-inline small m-0">
          <input type="checkbox" className="form-check-input" checked={relative} onChange={(e) => setRelative(e.target.checked)} />
          <span className="form-check-label">minus each channel's median</span>
        </label>
        {band && (
          <div className="btn-group btn-group-sm" role="group" aria-label="Frequencies">
            <button className={`btn ${!full ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setFull(false)}>
              Antenna band {band[0]}–{band[1]} MHz
            </button>
            <button className={`btn ${full ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setFull(true)}>
              Full 40–200 MHz
            </button>
          </div>
        )}
      </div>
      {!short ? (
        <div className="small text-muted p-2">
          Zoom the figures above to {QL_MAX_DAYS} days or less to see the waterfall of those times.
        </div>
      ) : error ? <div className="small text-danger">{error}</div>
        : !ql ? <div className="small text-muted p-2">Loading the quick-look products…</div>
          : !ql.available || !wf ? (
            <div className="small text-muted p-2">
              No quick-look products here: {ql.reason ?? "none"}.
            </div>
          ) : (
            <>
              {ql.waterfall_note && <div className="small text-muted">{ql.waterfall_note}</div>}
              <Waterfall title="" times={wf.times} freqs={wf.freqs} z={wf.z} diverging={relative}
                unit={relative ? "Q − median" : "Q"} height={380} xTitle="UTC" />
              <p className="small text-muted mb-0">
                {ql.n_rows} rows from {ql.files?.length ?? 0} files
                {ql.decimation && ql.decimation > 1 ? ` (up to ${ql.decimation} cycles averaged per row, never across a gap)` : ""};
                each 0.5 MHz bin is the {ql.waterfall_stat} of its ~80 channels. Colours span the 2nd–98th
                percentiles of what is shown, set per range: the Q level differs between periods of the record
                (setup changes, edges-database DATA_ISSUES #36).
                {band && " The antenna's band is from the EDGES papers (to be confirmed with the team)."}
              </p>
            </>
          )}
    </section>
  )
}

function CyclesView({ dep, fileId }: { dep: string; fileId: number }) {
  const { data: c, error } = useJson<Cycles>(`/api/browse/${dep}/cycles/${fileId}`)
  if (error) return <div className="text-danger small mt-2">{error}</div>
  if (!c) return <div className="text-muted small mt-2">Loading the cycles…</div>
  if (!c.available) return <div className="text-muted small mt-2">{c.name}: {c.reason}</div>
  const x = (c.time_unix ?? []).map(utc)
  const pos = [["0", "antenna", SERIES[0]], ["1", "ambient load", SERIES[1]], ["2", "load + noise source", SERIES[2]]] as const
  const line = (y: (number | null)[] | number[] | undefined, name: string, color: string, show = true): Partial<Data> => ({
    type: "scatter", mode: "lines", x, y: (y ?? []).map((v) => (v === null ? NaN : v)), name, line: { color, width: 1 },
    showlegend: show, legendgroup: name,
  })
  const strips: Strip[] = [
    { title: "ADC max", traces: pos.map(([i, n, col]) => line(c[`adcmax${i}` as keyof Cycles] as number[], n, col)) },
    { title: "ADC min", traces: pos.map(([i, n, col]) => line(c[`adcmin${i}` as keyof Cycles] as number[], n, col, false)) },
    { title: "data drops", traces: pos.map(([i, n, col]) => line(c[`drops${i}` as keyof Cycles] as number[], n, col, false)) },
  ]
  return (
    <div className="mt-2">
      <div className="small fw-semibold">{c.name}: {x.length} cycles, per switch position</div>
      <TimeStrips strips={strips} height={420} revision={String(fileId)} />
    </div>
  )
}

function S11View({ dep, session }: { dep: string; session: S11Session }) {
  const stamp = session.stamp_unix
  const { data: s, error } = useJson<S11Traces>(`/api/browse/${dep}/s11?${
    session.session_id !== null ? `session_id=${session.session_id}` : `stamp=${stamp}`}`)
  if (error) return <div className="text-danger small">{error}</div>
  if (!s) return <div className="text-muted small">Reading the session…</div>
  // the antenna solid blue; the rest (VNA inputs, standards, loads) each a
  // distinct colour and dash (a full EDGES-3 session has 12 traces)
  const others = [SERIES[1], SERIES[2], MUTED, CRITICAL]
  const dashes = ["solid", "dash", "dot"] as const
  const isAnt = (k: string) => k === "antenna_s11" || k === "ant"
  const style = (k: string, j: number) => isAnt(k)
    ? { color: SERIES[0], dash: "solid" as const }
    : { color: others[j % others.length], dash: dashes[Math.floor(j / others.length) % dashes.length] }
  const ok = Object.entries(s.traces).filter(([, t]) => "re" in t) as [string, { freq_mhz: (number | null)[]; re: (number | null)[]; im: (number | null)[] }][]
  const bad = Object.entries(s.traces).filter(([, t]) => "error" in t)
  // the antenna first, then the others in label order
  const mp = [...ok.filter(([k]) => isAnt(k)), ...ok.filter(([k]) => !isAnt(k))]
    .map(([k, t], i) => ({ k, f: nums(t.freq_mhz), ...magPhase(nums(t.re), nums(t.im)),
      ...style(k, isAnt(k) ? 0 : i - (ok.some(([a]) => isAnt(a)) ? 1 : 0)) }))
  const panels: Panel[] = [
    { title: "|S11| (as measured)", yTitle: "dB", traces: mp.map((m) => ({ x: m.f, y: m.db, name: m.k, color: m.color, dash: m.dash })) },
    { title: "Phase (as measured)", yTitle: "deg", traces: mp.map((m) => ({ x: m.f, y: m.deg, name: m.k, color: m.color, dash: m.dash })) },
  ]
  return (
    <>
      <div className="small text-muted mb-1">
        {utc(stamp)} UTC, {session.kind} session: the raw VNA readings, uncalibrated
        ({session.labels.join(", ")}).
      </div>
      {bad.map(([k, t]) => <div key={k} className="text-danger small">{k}: {"error" in t ? t.error : ""}</div>)}
      <StackedPlot panels={panels} cols={2} panelHeight={260} />
    </>
  )
}
