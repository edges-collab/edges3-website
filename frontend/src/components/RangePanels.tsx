/**
 * What the catalog has for a time window of one receiver (the Night tab):
 *
 * - without the pipeline's night figure: the quick-look waterfall of the
 *   times shown (``QuickLookPanel``);
 * - each spectrum file's span (by load) and the S11 sessions;
 * - the receiver's own housekeeping (unless the night figure shows it) and
 *   the site's weather;
 * - the spectrum files (with the night's QA from L1, where there is one;
 *   click a file for its cycles) and the S11 sessions (click one for its
 *   raw |S11| and phase).
 *
 * The figures share a site-time axis: zooming one zooms all, and the
 * waterfall follows.
 */
import { useMemo, useState } from "react"
import type { Data } from "plotly.js"
import TimeStrips, { clock, type Strip } from "./TimeStrips"
import StackedPlot, { MUTED, SERIES, type Panel } from "./StackedPlot"
import QuickLookPanel from "./QuickLookPanel"
import { useJson } from "../hooks/useJson"
import { magPhase } from "../utils/robust"
import type { Badge, FileQA, NightPayload } from "../types/night"
import type {
  BrowseFile, Cycles, Deployment, Housekeeping, RangeData, S11Session, S11Traces, Weather,
} from "../types/browse"

const CRITICAL = "#d03b3b"
/** Housekeeping not plotted (not temperatures or voltages one reads by eye). */
const HK_SKIP = new Set(["pr59_current", "setpoint", "thermal_control"])
/** "front_end_temperature" -> "front end"; "sensors_frontend_low3_temperature" -> "frontend low3"
 * (EDGES-2 sensor files name the front end they claim to measure). */
const pretty = (k: string) => k.replace(/^sensors_/, "").replace(/_temperature$/, "").replace(/_/g, " ")
const nums = (a: (number | null)[] | undefined) => (a ?? []).map((v) => (v === null ? NaN : v))
const fmt = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "–" : v.toFixed(digits))
const prec = (v: number | null | undefined, digits = 3) => (v === null || v === undefined ? "–" : v.toPrecision(digits))

const BADGE_CLASS: Record<Badge["level"], string> = {
  critical: "text-bg-danger", warn: "text-bg-warning", info: "text-bg-secondary", ok: "text-bg-success",
}
const BADGE_ICON: Record<Badge["level"], string> = { critical: "⛔ ", warn: "⚠ ", info: "ⓘ ", ok: "✓ " }

export default function RangePanels({ receiver: r, start, end, figure = false, night = null }: {
  receiver: Deployment; start: number; end: number
  /** the pipeline's night figure is shown above (with the waterfall and housekeeping) */
  figure?: boolean
  /** its payload, once loaded (the file QA) */
  night?: NightPayload | null
}) {
  const dep = r.name
  const off = r.utc_offset_hours
  const at = useMemo(() => clock(off), [off])
  const timeTitle = `Site time (${r.timezone}, UTC${off >= 0 ? "+" : ""}${off})`
  const qs = `start=${start}&end=${end}`
  const { data: rng, error } = useJson<RangeData>(`/api/browse/${dep}/range?${qs}`)
  const { data: w } = useJson<Weather>(`/api/browse/${dep}/weather?${qs}`)
  const showHk = !figure // the night figure has the receiver's housekeeping
  const { data: hk } = useJson<Housekeeping>(showHk ? `/api/browse/${dep}/housekeeping?${qs}` : null)
  const [fileId, setFileId] = useState<number | null>(null)
  const [session, setSession] = useState<S11Session | null>(null)
  const pick = (stamp: number) => setSession(rng?.s11_sessions.find((x) => x.stamp_unix === stamp) ?? null)
  // the figures show the same times; zooming one zooms all
  const [view, setView] = useState<[number, number] | null>(null)
  const shown: [number, number] = view ?? [start, end]
  const qa = useMemo(() => new Map((night?.files ?? []).map((f) => [f.file_id, f])), [night])

  const strips: Strip[] = useMemo(() => {
    if (!rng) return []
    // each extracted file as a bar over its span; others at their name's stamp
    const seg = (fs: BrowseFile[]) => ({
      x: fs.flatMap((f) => [at(f.t_start_unix), at(f.t_end_unix), null]),
      y: fs.flatMap(() => [0, 0, null]),
      customdata: fs.flatMap((f) => [f.file_id, f.file_id, null]),
      text: fs.flatMap((f) => [f.name, f.name, ""]),
    })
    const ext = rng.files.filter((f) => f.t_start_unix !== null && f.t_end_unix !== null)
    const isAnt = (f: BrowseFile) => !f.load || f.load === "ant"
    const ok = ext.filter((f) => !f.data_drops && isAnt(f)), bad = ext.filter((f) => !!f.data_drops)
    const calOk = ext.filter((f) => !f.data_drops && !isAnt(f))
    const pending = rng.files.filter((f) => f.t_start_unix === null)
    const files: Partial<Data>[] = [
      { type: "scatter", mode: "lines", ...seg(ok), name: "antenna file", line: { color: SERIES[0], width: 12 },
        hovertemplate: "%{text}<extra></extra>" },
      ...(calOk.length ? [{ type: "scatter" as const, mode: "lines" as const, ...seg(calOk), name: "calibration-load file",
        line: { color: SERIES[1], width: 12 }, hovertemplate: "%{text}<extra></extra>" }] : []),
      { type: "scatter", mode: "lines", ...seg(bad), name: "file with data drops", line: { color: CRITICAL, width: 12 },
        hovertemplate: "%{text}<extra></extra>" },
      { type: "scatter", mode: "markers", x: pending.map((f) => at(f.stamp_unix)), y: pending.map(() => 0),
        customdata: pending.map((f) => f.file_id), text: pending.map((f) => f.name),
        name: "file (not extracted yet: at its name's time)", marker: { color: MUTED, symbol: "triangle-right", size: 10 },
        hovertemplate: "%{text}<extra></extra>" },
    ]
    const s11: Partial<Data>[] = [{
      type: "scatter", mode: "markers", x: rng.s11_sessions.map((x) => at(x.stamp_unix)), y: rng.s11_sessions.map(() => 0),
      customdata: rng.s11_sessions.map((x) => `s11:${x.stamp_unix}`), text: rng.s11_sessions.map((x) => x.name),
      name: "S11 session (click)", marker: { color: MUTED, symbol: "diamond", size: 9 },
      hovertemplate: "S11 %{text}<extra></extra>",
    }]
    return [
      { title: "files", traces: files, weight: 0.5, events: true },
      { title: "S11", traces: s11, weight: 0.4, events: true },
    ]
  }, [rng, at])

  const conditions: Strip[] = useMemo(() => {
    const out: Strip[] = []
    // the receiver's own log, one strip per unit (the hot load apart: ~100 °C)
    if (showHk && hk?.available && hk.series) {
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
          x: v.time_unix.map(at), y: nums(v.value), name: pretty(k),
          line: { color: [SERIES[0], SERIES[1], SERIES[2], MUTED][i % 4], width: 1 },
        })) })
      }
    }
    if (w?.available && w.series && w.time_unix) {
      const x = w.time_unix.map(at)
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
  }, [w, hk, showHk, at])

  const when = (t: number | null) => (t === null ? "–" : (at(t) ?? "").slice(5, 16))

  return (
    <>
      {!figure && <QuickLookPanel receiver={r} window={shown} />}

      <section className="border rounded p-2">
        <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
          <h3 className="h6 m-0">Spectrum files and S11 sessions</h3>
          {rng && <span className="small text-muted">{rng.files.length} spectrum files, {rng.s11_sessions.length} S11 sessions.
            Click a file for its cycles, an S11 session for its traces.</span>}
        </div>
        {error && <div className="alert alert-warning small py-1">{error}</div>}
        {!rng ? <div className="text-muted small p-3">{error ? "" : "Loading…"}</div>
          : <TimeStrips strips={strips} height={230} revision={`${start}/${end}`} xRange={shown}
              utcOffsetHours={off} timeTitle={timeTitle} onXRange={setView}
              onPick={(c) => {
                if (typeof c === "string" && c.startsWith("s11:")) pick(Number(c.slice(4)))
                else if (typeof c === "number") setFileId(c)
              }} />}
      </section>

      <section className="border rounded p-2">
        <h3 className="h6 mb-1">{showHk ? "Receiver housekeeping and site weather" : "Site weather"}</h3>
        <p className="small text-muted mb-1">
          {showHk
            ? <>The receiver's own log ({hk?.source ?? "none"}{hk && !hk.available ? `: ${hk.reason}` : ""}) and the </>
            : <>The receiver's housekeeping is in the night figure above. The </>}
          MRO weather log ({w && !w.available ? w.reason : "every 5 min"}). The weather's front-end temperature is not
          attributed to a receiver yet (edges-database is asking the team whose it is).
        </p>
        {conditions.length === 0
          ? <div className="text-muted small p-3">{!w || (showHk && !hk) ? "Loading…" : "Nothing logged in this window."}</div>
          : <TimeStrips strips={conditions} height={120 + 150 * conditions.length} revision={`${start}/${end}/c`}
              xRange={shown} utcOffsetHours={off} timeTitle={timeTitle} onXRange={setView} />}
      </section>

      {rng && rng.files.length > 0 && (
        <section className="border rounded p-2">
          <h3 className="h6">Spectrum files</h3>
          <div style={{ maxHeight: 420, overflow: "auto" }}>
            <table className="table table-sm table-hover align-middle small mb-0">
              <thead className="sticky-top bg-white">
                <tr>
                  <th>File</th><th>Load</th><th>Site time</th><th className="text-end">Hours</th>
                  <th className="text-end" title={night ? "Cycles in the night / in the file" : "Cycles in the file"}>Cycles</th>
                  <th className="text-end">Data drops</th><th className="text-end">ADC max / min</th>
                  {night ? <>
                    <th className="text-end" title="Antenna dropouts (Q < 0) in the night">Dropouts</th>
                    <th className="text-end" title="Median band Q in the night, excluding dropouts">Median Q</th>
                    <th className="text-end" title="Intermittent-RFI flag fraction (in the night where available)">RFI occ.</th>
                    <th className="text-end" title="Time-series outlier cycles in the night">Outliers</th>
                    <th className="text-end">Lines</th>
                    <th className="text-end" title="Cycles at ADC full scale within the night">ADC FS</th>
                    <th>QA</th>
                  </> : <th className="text-end">MB</th>}
                </tr>
              </thead>
              <tbody>
                {rng.files.map((f) => {
                  const q: FileQA | undefined = qa.get(f.file_id)
                  return (
                    <tr key={f.file_id} className={f.file_id === fileId ? "table-primary" : ""} style={{ cursor: "pointer" }}
                      onClick={() => setFileId(f.file_id)}>
                      <td className="font-monospace">{f.name}{f.category !== "science" && <span className="badge text-bg-secondary ms-1">{f.category}</span>}</td>
                      <td>{f.load ?? "–"}</td>
                      <td className="text-nowrap">
                        {f.t_start_unix === null
                          ? <>{when(f.stamp_unix)}<span className="text-muted"> (name)</span></>
                          : <>{when(f.t_start_unix)}–{when(f.t_end_unix).slice(when(f.t_end_unix).slice(0, 5) === when(f.t_start_unix).slice(0, 5) ? 6 : 0)}</>}
                      </td>
                      <td className="text-end">{fmt(f.duration_hr)}</td>
                      <td className="text-end text-nowrap">
                        {q ? <>{q.n_cycles_window ?? "–"}<span className="text-muted"> / {f.n_cycles ?? "–"}</span></> : f.n_cycles ?? "–"}
                      </td>
                      <td className={`text-end ${f.data_drops ? "text-danger fw-semibold" : ""}`}>{f.data_drops ?? "–"}</td>
                      <td className="text-end">{fmt(f.adcmax, 3)} / {fmt(f.adcmin, 3)}</td>
                      {night ? <>
                        <td className={`text-end ${(q?.n_dropout_cycles ?? 0) > 0 ? "text-danger fw-bold" : ""}`}>{q?.n_dropout_cycles ?? "–"}</td>
                        <td className="text-end">{prec(q?.q_median, 4)}</td>
                        <td className="text-end" title={q?.rfi_whole_file ? "whole file (per-cycle RFI needs L1 v4)" : "in the night"}>
                          {q?.rfi_occupancy == null ? "–" : `${(100 * q.rfi_occupancy).toFixed(2)}%`}
                          {q?.rfi_whole_file && <span className="text-muted">*</span>}
                        </td>
                        <td className="text-end">{q?.n_outlier_cycles ?? "–"}</td>
                        <td className="text-end">{q?.n_persistent_lines ?? "–"}</td>
                        <td className="text-end">{q?.n_adc_clip_cycles ?? "–"}</td>
                        <td>
                          <div className="d-flex flex-wrap gap-1">
                            {(q?.badges ?? []).map((b, i) => (
                              <span key={i} className={`badge ${BADGE_CLASS[b.level]}`}>{BADGE_ICON[b.level]}{b.text}</span>
                            ))}
                          </div>
                        </td>
                      </> : <td className="text-end">{f.size_mb.toFixed(0)}</td>}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {night && (
            <p className="text-muted small mb-0 mt-1">
              QA (antenna files in the night, from L1) uses provisional thresholds: ADC full scale |x| ≥ {night.thresholds.adc_full_scale},
              RFI occupancy &gt; {100 * night.thresholds.rfi_occupancy}%, outliers &gt; {night.thresholds.outlier_cycles} cycles,
              short &lt; {100 * night.thresholds.short_fraction}% of the night's longest file.
              RFI values marked * are whole-file (per-cycle RFI arrives with L1 v4).
            </p>
          )}
          {fileId !== null && <CyclesView dep={dep} fileId={fileId} off={off} timeTitle={timeTitle} />}
        </section>
      )}

      {rng && rng.s11_sessions.length > 0 && (
        <section className="border rounded p-2">
          <h3 className="h6">S11 sessions</h3>
          <div className="d-flex flex-wrap gap-1 mb-2">
            {rng.s11_sessions.map((x) => (
              <button key={x.stamp_unix} className={`btn btn-sm ${x.stamp_unix === session?.stamp_unix ? "btn-primary" : "btn-outline-secondary"}`}
                title={`${x.kind}: ${x.labels.join(", ")}`} onClick={() => setSession(x)}>
                {x.name}{x.kind !== "antenna" ? ` (${x.kind})` : ""}
              </button>
            ))}
          </div>
          {session !== null ? <S11View dep={dep} session={session} off={off} tz={r.timezone} />
            : <div className="small text-muted">Choose a session (or click one in the figure above).</div>}
        </section>
      )}
    </>
  )
}

function CyclesView({ dep, fileId, off, timeTitle }: { dep: string; fileId: number; off: number; timeTitle: string }) {
  const { data: c, error } = useJson<Cycles>(`/api/browse/${dep}/cycles/${fileId}`)
  if (error) return <div className="text-danger small mt-2">{error}</div>
  if (!c) return <div className="text-muted small mt-2">Loading the cycles…</div>
  if (!c.available) return <div className="text-muted small mt-2">{c.name}: {c.reason}</div>
  const x = (c.time_unix ?? []).map(clock(off))
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
      <TimeStrips strips={strips} height={420} revision={String(fileId)} utcOffsetHours={off} timeTitle={timeTitle} />
    </div>
  )
}

function S11View({ dep, session, off, tz }: { dep: string; session: S11Session; off: number; tz: string }) {
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
        {clock(off)(stamp)} {tz}, {session.kind} session: the raw VNA readings, uncalibrated
        ({session.labels.join(", ")}).
      </div>
      {bad.map(([k, t]) => <div key={k} className="text-danger small">{k}: {"error" in t ? t.error : ""}</div>)}
      <StackedPlot panels={panels} cols={2} panelHeight={260} />
    </>
  )
}
