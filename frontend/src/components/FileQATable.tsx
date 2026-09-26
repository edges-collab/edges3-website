/**
 * Per-file QA for one night: catalogue facts (cycles, data drops, ADC
 * full-scale hits) and L1 metrics, with badges computed by the backend.
 * Files without products (e.g. unreadable .acq files) are listed, not
 * treated as errors.
 */
import type { Badge, FileQA } from "../types/night"
import { toSiteTime } from "../utils/nightData"

const BADGE_CLASS: Record<Badge["level"], string> = {
  warn: "text-bg-warning",
  info: "text-bg-secondary",
  ok: "text-bg-success",
}
const BADGE_ICON: Record<Badge["level"], string> = { warn: "⚠ ", info: "ⓘ ", ok: "✓ " }

function fmt(v: number | null, digits = 3): string {
  return v === null || v === undefined ? "–" : v.toPrecision(digits)
}

type Props = { files: FileQA[]; utcOffsetHours: number }

export default function FileQATable({ files, utcOffsetHours }: Props) {
  if (files.length === 0) {
    return <p className="text-muted">No antenna spectrum files catalogued for this night.</p>
  }
  const time = (t: number | null) => (t === null ? "–" : toSiteTime(t, utcOffsetHours).slice(11, 16))
  return (
    <div className="table-responsive">
      <table className="table table-sm align-middle small">
        <thead>
          <tr>
            <th>File</th>
            <th>Site time</th>
            <th className="text-end">Cycles</th>
            <th className="text-end">Median Q</th>
            <th className="text-end">RFI occ.</th>
            <th className="text-end">Outliers</th>
            <th className="text-end">Lines</th>
            <th className="text-end">ADC FS</th>
            <th className="text-end">Drops</th>
            <th>QA</th>
          </tr>
        </thead>
        <tbody>
          {files.map((f) => (
            <tr key={f.file_id}>
              <td className="font-monospace">{f.name}</td>
              <td>{time(f.t_start_unix)}–{time(f.t_end_unix)}</td>
              <td className="text-end">{f.n_cycles ?? "–"}</td>
              <td className="text-end">{fmt(f.q_median, 4)}</td>
              <td className="text-end">
                {f.rfi_occupancy === null ? "–" : `${(100 * f.rfi_occupancy).toFixed(2)}%`}
              </td>
              <td className="text-end">{f.n_outlier_cycles ?? "–"}</td>
              <td className="text-end">{f.n_persistent_lines ?? "–"}</td>
              <td className="text-end">{f.n_adc_clip_cycles}</td>
              <td className="text-end">{f.total_data_drops ?? "–"}</td>
              <td>
                <div className="d-flex flex-wrap gap-1">
                  {f.badges.map((b, i) => (
                    <span key={i} className={`badge ${BADGE_CLASS[b.level]}`}>
                      {BADGE_ICON[b.level]}
                      {b.text}
                    </span>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
