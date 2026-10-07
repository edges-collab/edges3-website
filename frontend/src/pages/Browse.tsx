/**
 * Raw data (first EDGES-2 low2): a receiver's whole record at a glance, then
 * any range of up to two months in detail, from the catalog only.
 *
 * - Overview, per UTC day over the whole record: hours of data (spectrum
 *   files before extraction), GB, S11 sessions, data drops and the largest
 *   ADC value. Zoom in to two months or less, or click a day, to open it.
 * - The range: each spectrum file's span, the S11 sessions, and the site's
 *   weather; a file table (click a file for its cycles) and the S11 sessions
 *   (click one for its raw |S11| and phase).
 *
 * URL: ``/raw?dep=<deployment>&start=YYYY-MM-DD&end=YYYY-MM-DD``.
 */
import { useMemo, useState } from "react"
import { useSearchParams } from "react-router"
import type { Data } from "plotly.js"
import TimeStrips, { utc, type Strip } from "../components/TimeStrips"
import StackedPlot, { MUTED, SERIES, type Panel } from "../components/StackedPlot"
import { useJson } from "../hooks/useJson"
import { magPhase } from "../utils/robust"
import type { BrowseFile, Cycles, Deployment, Overview, RangeData, S11Traces, Weather } from "../types/browse"

const CRITICAL = "#d03b3b"
const MAX_RANGE_DAYS = 62
const DEFAULT_DEPLOYMENT = "edges2-low2-mro"
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
    const out: Strip[] = [
      extracted
        ? { title: "hours", traces: [bar(ov.hours, "hours of data")], weight: 1.4 }
        : { title: "files", traces: [bar(ov.files, "spectrum files")], weight: 1.4 },
      { title: "GB", traces: [bar(ov.gb, "GB of spectra", SERIES[2])] },
      { title: "S11", traces: [bar(ov.s11_sessions, "S11 sessions", SERIES[1])] },
    ]
    if (extracted) {
      out.push({ title: "drops", traces: [bar(ov.drops, "data drops", CRITICAL)] })
      out.push({ title: "max ADC", traces: [{
        type: "scatter", mode: "markers", x: ov.days, y: nums(ov.adcmax), name: "largest ADC value",
        marker: { color: MUTED, size: 4 }, customdata: ov.days, showlegend: false,
      }] })
    }
    return out
  }, [ov, extracted])

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
            : <TimeStrips strips={overviewStrips} height={extracted ? 520 : 380} revision={dep}
                onXRange={setZoom}
                onPick={(d) => { const t = unixOf(String(d)); open(t - 3 * DAY, t + 4 * DAY) }} />}
      </section>

      {start && end && <RangeView dep={dep} start={start} end={end} onRange={open} />}
    </div>
  )
}

function RangeView({ dep, start, end, onRange }: {
  dep: string; start: string; end: string; onRange: (t0: number, t1: number) => void
}) {
  const t0 = unixOf(start), t1 = unixOf(end)
  const qs = `start=${start}&end=${end}`
  const { data: r, error } = useJson<RangeData>(`/api/browse/${dep}/range?${qs}`)
  const { data: w } = useJson<Weather>(`/api/browse/${dep}/weather?${qs}`)
  const [fileId, setFileId] = useState<number | null>(null)
  const [stamp, setStamp] = useState<number | null>(null)
  const span = t1 - t0

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
    const ok = ext.filter((f) => !f.data_drops), bad = ext.filter((f) => !!f.data_drops)
    const pending = r.files.filter((f) => f.t_start_unix === null)
    const files: Partial<Data>[] = [
      { type: "scatter", mode: "lines", ...seg(ok), name: "spectrum file", line: { color: SERIES[0], width: 12 },
        hovertemplate: "%{text}<extra></extra>" },
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
      name: "S11 session (click)", marker: { color: SERIES[1], symbol: "diamond", size: 9 },
      hovertemplate: "S11 %{text}<extra></extra>",
    }]
    const out: Strip[] = [
      { title: "files", traces: files, weight: 0.5, events: true },
      { title: "S11", traces: s11, weight: 0.4, events: true },
    ]
    if (w?.available && w.series && w.time_unix) {
      const x = w.time_unix.map(utc)
      const line = (k: string, name: string, color: string): Partial<Data> => ({
        type: "scatter", mode: "lines", x, y: nums(w.series![k]), name, line: { color, width: 1 },
      })
      const temps = [["ambient_temp", "ambient", SERIES[0]], ["rack_temp", "rack", SERIES[1]],
        ["frontend", "front end (receiver not recorded)", MUTED]] as const
      const t = temps.filter(([k]) => w.series![k]).map(([k, n, c]) => line(k, n, c))
      if (t.length) out.push({ title: "temperature [K]", traces: t })
      if (w.series.ambient_hum) out.push({ title: "humidity [%]", traces: [line("ambient_hum", "humidity", SERIES[2])], weight: 0.7 })
    }
    return out
  }, [r, w])

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
            {w && !w.available ? `; weather: ${w.reason}` : ""}</span>}
        </div>
        {error && <div className="alert alert-warning small py-1">{error}</div>}
        {!r ? <div className="text-muted small p-3">Loading…</div>
          : <TimeStrips strips={strips} height={strips.length > 2 ? 560 : 260} revision={`${start}/${end}`}
              onPick={(c) => {
                if (typeof c === "string" && c.startsWith("s11:")) setStamp(Number(c.slice(4)))
                else if (typeof c === "number") setFileId(c)
              }} />}
        <p className="small text-muted mb-0 px-2">
          Weather: the site's log (Catalog.weather). Its front-end temperature is not attributed to a receiver yet
          (edges-database is asking the team whose it is).
        </p>
      </section>

      {r && r.files.length > 0 && (
        <section className="border rounded p-2">
          <h3 className="h6">Spectrum files</h3>
          <div style={{ maxHeight: 320, overflow: "auto" }}>
            <table className="table table-sm table-hover small mb-0">
              <thead className="sticky-top bg-white">
                <tr><th>File</th><th>Start (UTC)</th><th className="text-end">Hours</th><th className="text-end">Cycles</th>
                  <th className="text-end">Data drops</th><th className="text-end">ADC max / min</th><th className="text-end">MB</th></tr>
              </thead>
              <tbody>
                {r.files.map((f) => (
                  <tr key={f.file_id} className={f.file_id === fileId ? "table-primary" : ""} style={{ cursor: "pointer" }}
                    onClick={() => setFileId(f.file_id)}>
                    <td className="font-monospace">{f.name}{f.category !== "science" && <span className="badge text-bg-secondary ms-1">{f.category}</span>}</td>
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
              <button key={x.stamp_unix} className={`btn btn-sm ${x.stamp_unix === stamp ? "btn-primary" : "btn-outline-secondary"}`}
                onClick={() => setStamp(x.stamp_unix)}>{x.name}</button>
            ))}
          </div>
          {stamp !== null ? <S11View dep={dep} stamp={stamp} />
            : <div className="small text-muted">Choose a session (or click one in the figure above).</div>}
        </section>
      )}
    </>
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

function S11View({ dep, stamp }: { dep: string; stamp: number }) {
  const { data: s, error } = useJson<S11Traces>(`/api/browse/${dep}/s11?stamp=${stamp}`)
  if (error) return <div className="text-danger small">{error}</div>
  if (!s) return <div className="text-muted small">Reading the session…</div>
  const colors: Record<string, string> = { antenna_s11: SERIES[0], input1: SERIES[1], input2: SERIES[2], input3: MUTED, input4: CRITICAL }
  const ok = Object.entries(s.traces).filter(([, t]) => "re" in t) as [string, { freq_mhz: (number | null)[]; re: (number | null)[]; im: (number | null)[] }][]
  const bad = Object.entries(s.traces).filter(([, t]) => "error" in t)
  const mp = ok.map(([k, t]) => ({ k, f: nums(t.freq_mhz), ...magPhase(nums(t.re), nums(t.im)) }))
  const panels: Panel[] = [
    { title: "|S11| (as measured)", yTitle: "dB", traces: mp.map((m) => ({ x: m.f, y: m.db, name: m.k, color: colors[m.k] ?? MUTED })) },
    { title: "Phase (as measured)", yTitle: "deg", traces: mp.map((m) => ({ x: m.f, y: m.deg, name: m.k, color: colors[m.k] ?? MUTED })) },
  ]
  return (
    <>
      <div className="small text-muted mb-1">
        {utc(stamp)} UTC: the raw VNA readings of the antenna and of the four inputs, uncalibrated.
      </div>
      {bad.map(([k, t]) => <div key={k} className="text-danger small">{k}: {"error" in t ? t.error : ""}</div>)}
      <StackedPlot panels={panels} cols={2} panelHeight={260} />
    </>
  )
}
