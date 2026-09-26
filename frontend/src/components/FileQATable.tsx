/**
 * Per-file QA for one night. Cycles, dropouts, outliers, median Q, RFI
 * and ADC full-scale hits count only the cycles inside the night
 * (`Products.l1(clip=True)`); data drops and lines are whole-file values.
 * Badges are computed by the backend. Catalogued files without products (e.g. .acq files that
 * read_acq cannot decode) are listed, not treated as errors.
 */
import type { Badge, FileQA } from "../types/night"
import { toSiteTime } from "../utils/nightData"

const BADGE_CLASS: Record<Badge["level"], string> = {
  critical: "text-bg-danger",
  warn: "text-bg-warning",
  info: "text-bg-secondary",
  ok: "text-bg-success",
}
const BADGE_ICON: Record<Badge["level"], string> = { critical: "⛔ ", warn: "⚠ ", info: "ⓘ ", ok: "✓ " }

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
            <th className="text-end" title="Cycles in the night / in the file">Cycles</th>
            <th className="text-end" title="Antenna dropouts (Q < 0) in the night">Dropouts</th>
            <th className="text-end" title="Median band Q in the night, excluding dropouts">Median Q</th>
            <th className="text-end" title="Intermittent-RFI flag fraction (in the night where available)">RFI occ.</th>
            <th className="text-end" title="Time-series outlier cycles in the night">Outliers</th>
            <th className="text-end">Lines</th>
            <th className="text-end" title="Cycles at ADC full scale within the night">ADC FS</th>
            <th className="text-end">Drops</th>
            <th>QA</th>
          </tr>
        </thead>
        <tbody>
          {files.map((f) => (
            <tr key={f.file_id}>
              <td className="font-monospace">{f.name}</td>
              <td>{time(f.t_start_unix)}–{time(f.t_end_unix)}</td>
              <td className="text-end">
                {f.n_cycles_window ?? "–"}
                <span className="text-muted"> / {f.n_cycles ?? "–"}</span>
              </td>
              <td className={`text-end ${(f.n_dropout_cycles ?? 0) > 0 ? "text-danger fw-bold" : ""}`}>
                {f.n_dropout_cycles ?? "–"}
              </td>
              <td className="text-end">{fmt(f.q_median, 4)}</td>
              <td className="text-end" title={f.rfi_whole_file ? "whole file (per-cycle RFI needs L1 v4)" : "in the night"}>
                {f.rfi_occupancy === null ? "–" : `${(100 * f.rfi_occupancy).toFixed(2)}%`}
                {f.rfi_whole_file && <span className="text-muted">*</span>}
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
