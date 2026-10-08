/**
 * The pipeline's quick-look Q waterfall of a time window (up to
 * QL_MAX_DAYS), for any receiver: binned by median (hides narrowband RFI),
 * mean or max (keep it); optionally minus each channel's median; the
 * antenna's band or the full 40-200 MHz. The colour scale spans the shown
 * channels only, set per window.
 */
import { useMemo, useState } from "react"
import Waterfall from "./Waterfall"
import { clock } from "./TimeStrips"
import { decodeRows, minusChannelMedian } from "../utils/nightData"
import { useJson } from "../hooks/useJson"
import type { QuickLook, WaterfallStat } from "../types/night"
import type { Deployment } from "../types/browse"

export const QL_MAX_DAYS = 8
const DAY = 86400

const STATS: [WaterfallStat, string][] = [
  ["median", "median (RFI removed)"], ["mean", "mean (RFI kept)"], ["max", "max (RFI kept)"],
]

const nums = (a: (number | null)[]) => a.map((v) => (v === null ? NaN : v))

export default function QuickLookPanel({ receiver: r, window: [w0, w1], note }: {
  receiver: Deployment; window: [number, number]; note?: string
}) {
  const band = r.band_mhz
  const [stat, setStat] = useState<WaterfallStat>("median")
  const [relative, setRelative] = useState(false)
  const [full, setFull] = useState(false) // the whole 40-200 MHz, not the antenna's band
  const short = w1 - w0 <= QL_MAX_DAYS * DAY
  // whole minutes, so a re-render never refetches the same window
  const a = Math.floor(w0 / 60) * 60, b = Math.ceil(w1 / 60) * 60
  const { data: ql, error } = useJson<QuickLook>(
    short ? `/api/browse/${r.name}/quicklook?start=${a}&end=${b}${stat === "median" ? "" : `&waterfall=${stat}`}` : null)
  const wf = useMemo(() => {
    if (!ql?.waterfall_q || !ql.time_unix || !ql.freq_mhz) return null
    // only the channels shown, so they alone set the colour scale (percentiles)
    const f = nums(ql.freq_mhz)
    const keep = f.map((x) => !band || full || (x >= band[0] && x <= band[1]))
    let z: number[][] = decodeRows(ql.waterfall_q).map((row) => Array.from(row).filter((_, j) => keep[j]))
    if (relative) z = minusChannelMedian(z)
    const at = clock(r.utc_offset_hours)
    return { z, times: ql.time_unix.map((t) => at(t) ?? ""), freqs: f.filter((_, j) => keep[j]),
      range: [at(w0) ?? "", at(w1) ?? ""] as [string, string] }
  }, [ql, relative, band, full, r.utc_offset_hours, w0, w1])
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
      {note && <div className="small text-muted mb-1">{note}</div>}
      {!short ? (
        <div className="small text-muted p-2">
          Zoom the figures below to {QL_MAX_DAYS} days or less to see the waterfall of those times.
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
                unit={relative ? "Q − median" : "Q"} height={380} xRange={wf.range}
                xTitle={`Site time (${r.timezone}, UTC${r.utc_offset_hours >= 0 ? "+" : ""}${r.utc_offset_hours})`} />
              <p className="small text-muted mb-0">
                Uncalibrated. {ql.n_rows} rows from {ql.files?.length ?? 0} files
                {ql.decimation && ql.decimation > 1 ? ` (up to ${ql.decimation} cycles averaged per row, never across a gap)` : ""};
                each 0.5 MHz bin is the {ql.waterfall_stat} of its ~80 channels. Colours span the 2nd–98th
                percentiles of what is shown, set per window: the Q level differs between periods of the record
                (setup changes, edges-database DATA_ISSUES #36).
                {band && " The antenna's band is from the EDGES papers (to be confirmed with the team)."}
              </p>
            </>
          )}
    </section>
  )
}
