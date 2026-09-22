/**
 * Page header shown above every data page. Displays:
 *   * whether a run has been completed yet
 *   * the dates that were used
 *   * the generated-at timestamp
 *   * the save controls (2D checkbox + Save button + result)
 *   * a summary of per-load actual temperatures (with their sampling time)
 * The header sits above all plots; everything inside is stacked vertically
 * so neither the summary nor the save controls sit beside the plots.
 */

import { useState } from "react"
import { useRunState } from "../state/RunContext"
import { BASE_URL } from "../utils/baseURL"
import type {
  ActualTemperature,
  S11GridMismatch,
  S11GridReference,
} from "../types/manifest"

type BannerProps = {
  pageTitle?: string
}

const LOAD_ORDER = ["ambient", "hot"] as const
const LOAD_LABELS: Record<(typeof LOAD_ORDER)[number], string> = {
  ambient: "Ambient",
  hot: "Hot",
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  // YYYY-MM-DD HH:MM:SS UTC
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`
}

export default function LatestRunBanner({ pageTitle }: BannerProps) {
  const { latest } = useRunState()
  const [include2D, setInclude2D] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null)

  const sourceLabel = latest.run_id
  ? "Latest user run"
  : "Run has not been completed"

  const dateLine = [
    latest.dates.cal && `cal: ${latest.dates.cal}`,
    latest.dates.s11 && `S11: ${latest.dates.s11}`,
    latest.dates.raw && `raw: ${latest.dates.raw}`,
  ]
    .filter(Boolean)
    .join("  \u00b7  ")

  const temps = latest.actual_temperatures ?? {}
  const tempLines = LOAD_ORDER
    .map((load) => {
      const t = temps[load] as ActualTemperature | undefined
      if (!t) return null
      const tempStr =
        typeof t.temperature_k === "number" ? `${t.temperature_k.toFixed(2)} K` : "n/a"
      const timeStr = formatTime(t.time)
      return `${LOAD_LABELS[load]}: ${tempStr}${timeStr ? `  \u00b7  ${timeStr}` : ""}`
    })
    .filter((line): line is string => Boolean(line))

  const warnings = latest.warnings ?? []
  const s11Reference = warnings.find(
    (w): w is S11GridReference => w.type === "s11_grid_reference",
  )
  const s11Mismatches = warnings.filter(
    (w): w is S11GridMismatch => w.type === "s11_grid_mismatch",
  )
  const hasS11Warning = s11Reference !== undefined || s11Mismatches.length > 0

  async function handleSave() {
    setSaving(true)
    setMessage(null)
    setDownloadUrl(null)
    try {
      const res = await fetch(`${BASE_URL}/save_outputs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_2d: include2D }),
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`)
      }
      const data = (await res.json()) as {
        size_bytes: number
        download_url: string
        label: string
        include_2d: boolean
      }
      setMessage(
        `Saved ${data.label} (${(data.size_bytes / 1024 / 1024).toFixed(2)} MB, ${
          data.include_2d ? "with 2D" : "images only"
        })`,
      )
      setDownloadUrl(data.download_url)
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="latest-run-banner p-3 mb-3 border rounded bg-light d-flex flex-column gap-2">
      <div>
        <strong>{pageTitle ? `${pageTitle} \u2014 ` : ""}{sourceLabel}</strong>
        {latest.generated_at && (
          <div className="text-muted small">
            Generated {new Date(latest.generated_at).toLocaleString()}
          </div>
        )}
        {dateLine && <div className="small">{dateLine}</div>}
      </div>

      {hasS11Warning && s11Reference && (
        <div
          className="alert alert-danger border border-danger border-2 mb-0"
          role="alert"
          data-testid="s11-grid-warning"
          style={{
            fontSize: "1.15rem",
            lineHeight: 1.5,
            padding: "1.25rem 1.5rem",
          }}
        >
          <div
            className="fw-bold mb-2"
            style={{ fontSize: "1.5rem", letterSpacing: "0.02em" }}
          >
            Data quality warning — calibration may be erroneous
          </div>
          <p className="mb-2">
            The VNA was reconfigured mid-session during this calibration.
            The reference file <code>{s11Reference.file}</code> was logged
            at a much coarser sweep (
            {s11Reference.count}&nbsp;pts,&nbsp;
            {s11Reference.range_mhz[0].toFixed(1)}&ndash;
            {s11Reference.range_mhz[1].toFixed(1)}&nbsp;MHz) than the rest
            of the {latest.dates.s11} set. Because EDGES requires a single
            frequency grid,{" "}
            {s11Mismatches.length === 1
              ? "1 file was resampled"
              : `${s11Mismatches.length} files were resampled`}{" "}
            down to match, and calibration is now restricted to{" "}
            {s11Reference.range_mhz[0].toFixed(1)}&ndash;
            {s11Reference.range_mhz[1].toFixed(1)}&nbsp;MHz.
          </p>
          <p className="mb-2 fw-semibold">
            The data in this range is likely erroneous. Do not trust the
            calibrated temperature, the noise-wave fit (a, b), or the
            antenna S11 model from this run without first reviewing the
            raw measurements.
          </p>
          {s11Mismatches.length > 0 && (
            <details className="mt-2">
              <summary className="text-muted">
                Show {s11Mismatches.length} resampled file
                {s11Mismatches.length === 1 ? "" : "s"}
              </summary>
              <div className="mt-2">
                {s11Mismatches.map((w) => (
                  <div key={w.file} className="mb-1">
                    <code>{w.file}</code>{" "}
                    <span className="text-muted">
                      ({w.from_count}&nbsp;pts{" "}
                      {w.from_range_mhz[0].toFixed(1)}&ndash;
                      {w.from_range_mhz[1].toFixed(1)}&nbsp;MHz)
                    </span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {tempLines.length > 0 && (
        <div className="small">
          <div className="text-muted">
            Probe readings from the temperature log at each calibration time:
          </div>
          <ul className="mb-0 ps-3">
            {tempLines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="d-flex align-items-center gap-3">
        <label
          className={`d-flex align-items-center gap-1 small mb-0${latest.has_2d ? "" : " text-muted"}`}
          title={
            latest.has_2d
              ? "Include heatmap (.npz) data in the saved zip"
              : "No heatmap data was produced by this run \u2014 re-run with \u201cInclude 2D heatmaps\u201d checked on the Select page to enable"
          }
        >
          <input
            type="checkbox"
            checked={include2D}
            disabled={!latest.has_2d}
            onChange={(e) => setInclude2D(e.target.checked)}
          />
          Include 2D heatmaps
          {!latest.has_2d && (
            <span className="text-muted">(none on disk)</span>
          )}
        </label>
        <button
          onClick={handleSave}
          disabled={saving}
          className="btn btn-sm btn-primary"
        >
          {saving ? "Saving\u2026" : "Save outputs"}
        </button>
      </div>

      {message && (
        <div className="small">
          {message}
          {downloadUrl && (
            <>
              {" \u2014 "}
              <a href={downloadUrl}>Download zip</a>
            </>
          )}
        </div>
      )}
    </div>
  )
}
