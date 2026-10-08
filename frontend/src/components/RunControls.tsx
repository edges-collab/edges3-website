/**
 * Shared pieces of the Calibration and Calibrated night option panels:
 * a labelled select, a numeric parameter input that commits on blur/Enter,
 * the list of input issues, and the run status with its Run button.
 */
import { useState } from "react"
import type { RunDetail, RunStatus } from "../types/runs"

export function SelectField({
  label, value, options, onChange, autoLabel,
}: {
  label: string
  value: string
  options: string[]
  onChange: (v: string) => void
  autoLabel: string
}) {
  return (
    <label className="d-block mb-2 small">
      <span className="text-muted">{label}</span>
      <select className="form-select form-select-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="Latest">{autoLabel}</option>
        {[...options].reverse().map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  )
}

export function NumberField({
  label, value, onCommit, step,
}: {
  label: string
  value: number
  onCommit: (v: number) => void
  step?: number
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    if (draft === null) return
    const v = Number(draft)
    setDraft(null)
    if (draft.trim() !== "" && Number.isFinite(v) && v !== value) onCommit(v)
  }
  return (
    <label className="d-flex align-items-center justify-content-between gap-2 mb-1 small">
      <span className="text-muted">{label}</span>
      <input
        type="number" step={step} className="form-control form-control-sm text-end"
        style={{ width: "6.5rem" }}
        value={draft ?? String(value)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") commit() }}
      />
    </label>
  )
}

export function Issues({ issues }: { issues: string[] }) {
  if (issues.length === 0) return <div className="alert alert-success py-1 px-2 small mb-2">No input issues.</div>
  return (
    <div className="alert alert-warning py-1 px-2 small mb-2">
      <strong>Input issues:</strong>
      <ul className="mb-0 ps-3">{issues.map((i) => <li key={i}>{i}</li>)}</ul>
    </div>
  )
}

const STATE_TEXT: Record<RunStatus["state"], string> = {
  queued: "queued…",
  running: "running…",
  done: "done",
  failed: "failed",
}

export function RunStatusBox<I, R>({
  status, detail, onRun, what, seconds,
}: {
  status: RunStatus | null
  detail: RunDetail<I, R> | null
  onRun: () => void
  what: string
  seconds: string
}) {
  const state = detail?.status.state ?? status?.state ?? null
  const busy = state === "queued" || state === "running"
  return (
    <div className="mt-2">
      <button className="btn btn-primary btn-sm w-100" disabled={busy || state === "done"} onClick={onRun}>
        {state === "done" ? `${what} ready` : busy ? `${what} ${STATE_TEXT[state]}` :
          state === "failed" ? `Retry ${what.toLowerCase()}` : `Run ${what.toLowerCase()} (${seconds})`}
      </button>
      {busy && <div className="progress mt-1" style={{ height: 4 }}><div className="progress-bar progress-bar-striped progress-bar-animated w-100" /></div>}
      {state === "failed" && (
        <div className="alert alert-danger py-1 px-2 small mt-2 mb-0">
          {detail?.status.error ?? status?.error ?? "failed"}
        </div>
      )}
      {detail?.log_tail && busy && (
        <pre className="small bg-light border rounded p-1 mt-2 mb-0" style={{ maxHeight: 140, overflow: "auto", whiteSpace: "pre-wrap" }}>
          {detail.log_tail}
        </pre>
      )}
    </div>
  )
}

export const kelvin = (k: number) => `${k.toFixed(2)} K`
