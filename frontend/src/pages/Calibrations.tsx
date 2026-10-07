/**
 * Calibrations: the receiver calibration of a day, from edges-pipeline. On
 * load the page shows the day's stored calibration (the pipeline's default
 * settings; nothing is computed). Changing a setting asks the server whether
 * that is still the default (same configuration hash); if not, the
 * calibration is computed with those settings in the background (~45 s) and
 * labelled as such. The selection is shared with the Detailed Data View.
 */
import { useState } from "react"
import { useCalibration } from "../state/CalibrationContext"
import { useRun } from "../hooks/useRun"
import { useJson } from "../hooks/useJson"
import { Issues, NumberField, RunStatusBox } from "../components/RunControls"
import CalibrationPlots from "../components/CalibrationPlots"
import { withBaseUrl } from "../utils/baseURL"
import type {
  CalField, CalibrationData, CalibrationList, CalibrationResolved, CalParams,
} from "../types/runs"

const short = (h: string | null | undefined) => (h ? h.slice(0, 12) : "–")

/** ``params`` with one setting changed; a setting back at its default is dropped. */
function withSetting(params: CalParams, f: CalField, v: number, dflt: unknown): CalParams {
  const section = { ...(params[f.section] ?? {}) }
  if (v === dflt) delete section[f.key]
  else section[f.key] = v
  const out = { ...params, [f.section]: section }
  if (Object.keys(section).length === 0) delete out[f.section]
  return out
}

export default function Calibrations() {
  const { calibration, setCalibration } = useCalibration()
  const { data: list, error: listError } = useJson<CalibrationList>("/api/calibrations")
  const { resolved, detail, error, start } =
    useRun<CalibrationResolved, null, CalibrationData>("calibration", calibration)
  const [compareHash, setCompareHash] = useState("")
  const [more, setMore] = useState(false)

  const stored = resolved?.source === "stored" && resolved.stored ? resolved.day : null
  const { data: storedData, error: storedError } =
    useJson<CalibrationData>(stored ? `/api/calibrations/stored/${stored}` : null)
  const { data: compare, error: compareError } = useJson<CalibrationData>(
    resolved && compareHash ? `/api/calibrations/stored/${resolved.day}?config_hash=${compareHash}` : null)
  const data = resolved?.source === "stored"
    ? storedData
    : detail?.status.state === "done" ? detail.result : null

  const value = (f: CalField): number =>
    calibration.params[f.section]?.[f.key] ?? (list?.defaults[f.section]?.[f.key] as number) ?? 0
  const changed = (f: CalField) => calibration.params[f.section]?.[f.key] !== undefined
  const field = (f: CalField) => (
    <NumberField key={`${f.section}.${f.key}`} value={value(f)} step={f.integer ? 1 : undefined}
      label={changed(f) ? `${f.label} •` : f.label}
      onCommit={(v) => setCalibration({
        ...calibration, params: withSetting(calibration.params, f, v, list?.defaults[f.section]?.[f.key]),
      })} />
  )
  const latest = list?.stored.at(-1)?.cal_day

  return (
    <div className="d-flex gap-3 p-3 align-items-start">
      <aside className="border rounded p-3 side-panel">
        <h2 className="h5">Receiver calibration</h2>
        {listError && <div className="alert alert-warning small py-1">{listError}</div>}
        <label className="d-block mb-1 small">
          <span className="text-muted">Calibration day (UTC)</span>
          <select className="form-select form-select-sm" value={calibration.day}
            onChange={(e) => setCalibration({ ...calibration, day: e.target.value })}>
            <option value="Latest">Latest{latest ? ` (${latest})` : ""}</option>
            {[...(list?.stored ?? [])].reverse().map((r) => (
              <option key={r.cal_day} value={r.cal_day}>
                {r.cal_day} — open/short RMS {r.rms_open_k?.toFixed(1) ?? "–"}/{r.rms_short_k?.toFixed(1) ?? "–"} K
              </option>
            ))}
            {(list?.missing ?? []).length > 0 && (
              <optgroup label="No calibration">
                {[...list!.missing].reverse().map((m) => (
                  <option key={m.cal_day} value={m.cal_day}>{m.cal_day} — {m.reason}</option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {list && <div className="small text-muted mb-2">{list.note}</div>}
        {list?.version_skew && (
          <div className="alert alert-warning small py-1 px-2">
            The site's edges-pipeline ({list.pipeline}) is not the version that made the stored
            calibrations: a computed one will differ from them by more than its settings.
          </div>
        )}

        <div className="mt-2 mb-1 small fw-semibold">Settings (edges-pipeline rcal)</div>
        {list?.fields.filter((f) => f.main).map(field)}
        <button className="btn btn-link btn-sm p-0" onClick={() => setMore((m) => !m)}>
          {more ? "Fewer settings" : "More settings"}
        </button>
        {more && list && (
          <div className="mt-1">
            {["fit", "spectra", "temperatures", "hardware"].map((s) => {
              const fs = list.fields.filter((f) => !f.main && f.section === s)
              return fs.length > 0 && (
                <div key={s} className="mb-1">
                  <div className="small text-muted text-uppercase" style={{ fontSize: "0.7rem" }}>{s}</div>
                  {fs.map(field)}
                </div>
              )
            })}
          </div>
        )}
        <div className="small text-muted mt-1">
          The fit band (wfstart–wfstop) is also the calibration band; the S11 session is the one the
          catalog recommends for the day.
        </div>
        {Object.keys(calibration.params).length > 0 && (
          <button className="btn btn-outline-secondary btn-sm mt-2 w-100"
            onClick={() => setCalibration({ ...calibration, params: {} })}>
            Back to the stored default settings
          </button>
        )}

        <hr />
        {error && <div className="alert alert-danger small py-1">{error}</div>}
        {resolved && (
          <div className="small">
            {resolved.source === "stored" ? (
              <>
                <span className="badge text-bg-primary">Stored pipeline calibration</span>
                <div className="text-muted mt-1">
                  Default settings, config <code>{short(resolved.config_hash)}</code>; nothing to compute.
                </div>
                {resolved.unavailable && (
                  <div className="alert alert-warning py-1 px-2 mt-2 mb-0">
                    No calibration for {resolved.day}: {resolved.unavailable}.
                  </div>
                )}
              </>
            ) : (
              <>
                <span className="badge text-bg-warning">Computed with these settings</span>
                <div className="text-muted mt-1">
                  Config <code>{short(resolved.config_hash)}</code> (default{" "}
                  <code>{short(resolved.default_hash)}</code>), by edges-pipeline {resolved.pipeline}.
                </div>
                {resolved.unavailable ? (
                  <div className="alert alert-warning py-1 px-2 mt-2 mb-0">
                    {resolved.day} cannot be calibrated with any settings: {resolved.unavailable}.
                  </div>
                ) : (
                  <RunStatusBox status={resolved.status} detail={detail} onRun={start}
                    what="Calibration" seconds="~45 s" />
                )}
                {detail?.status.state === "done" && (
                  <a className="d-block mt-1" href={withBaseUrl(`/api/runs/calibration/${detail.id}/download`)}>
                    Download (zip: the solution and its JSON)
                  </a>
                )}
              </>
            )}
          </div>
        )}
        {data && <div className="mt-2"><Issues issues={data.issues} /></div>}

        {list && list.other_configs.length > 0 && (
          <label className="d-block mt-2 small">
            <span className="text-muted">Compare with another stored configuration</span>
            <select className="form-select form-select-sm" value={compareHash}
              onChange={(e) => setCompareHash(e.target.value)}>
              <option value="">None</option>
              {list.other_configs.map((c) => (
                <option key={c.config_hash} value={c.config_hash}>
                  {short(c.config_hash)} — {c.created_utc.slice(0, 10)}, {c.n_done} days ({c.code_version})
                </option>
              ))}
            </select>
          </label>
        )}
        {compareError && <div className="text-muted small">Comparison: {compareError}</div>}
      </aside>

      <section className="flex-grow-1" style={{ minWidth: 0 }}>
        {data ? (
          <CalibrationPlots data={data} compare={compare} />
        ) : (
          <div className="text-muted p-4 border rounded">
            {storedError
              ? storedError
              : resolved?.unavailable
                ? `No calibration for ${resolved.day}: ${resolved.unavailable}. Choose another day.`
                : resolved?.source === "stored"
                  ? "Loading the stored calibration…"
                  : detail && (detail.status.state === "queued" || detail.status.state === "running")
                    ? "Computing the calibration with these settings (~45 s); its plots will appear here."
                    : resolved
                      ? "These settings are not the pipeline's default, so this calibration is not stored: click Run on the left to compute it (~45 s)."
                      : "Resolving the calibration…"}
          </div>
        )}
      </section>
    </div>
  )
}
