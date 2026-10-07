/**
 * Status: whether the data packages work, and what the run queue is doing.
 */
import { useEffect, useState } from "react"
import { Link } from "react-router"
import { BASE_URL } from "../utils/baseURL"

type Status = {
  data_packages: boolean
  data_packages_error: string | null
  queue: { running: string[]; queued: string[] }
  runs: string[]
}

export default function Home() {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${BASE_URL}/pipeline/status`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d: Status) => setStatus(d))
      .catch(() => setError("Status unavailable (is the backend running?)."))
  }, [])

  return (
    <div className="d-flex flex-column p-3 gap-3">
      <div className="border rounded p-3">
        <h2>EDGES-3 site</h2>
        <ul className="mb-0">
          <li><Link to="/">Nightly Overview</Link>: the latest night at a glance, from precomputed quick-look and L1 products.</li>
          <li><Link to="/calibrations">Calibrations</Link>: a day's receiver calibration, stored by the pipeline (or computed with other settings).</li>
          <li><Link to="/data">Detailed Data View</Link>: one night calibrated with the selected calibration.</li>
          <li><Link to="/raw">Raw Data</Link>: an EDGES-2 receiver's record from the catalog (low2 so far).</li>
        </ul>
      </div>
      <div className="border rounded p-3">
        <h2 className="h4">Status</h2>
        {error && <p className="text-muted">{error}</p>}
        {status && (
          <>
            <p className="mb-1">
              Catalog and pipeline packages:{" "}
              <strong>{status.data_packages ? "available" : `missing (${status.data_packages_error})`}</strong>
            </p>
            <p className="mb-1">
              Running: <strong>{status.queue.running.join(", ") || "nothing"}</strong>; queued:{" "}
              <strong>{status.queue.queued.length}</strong>
            </p>
            <p className="text-muted small mb-0">{status.runs.join(" · ")}</p>
          </>
        )}
      </div>
    </div>
  )
}
