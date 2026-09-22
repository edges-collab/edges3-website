import { useEffect, useState } from "react"
import { Link } from "react-router"
import { BASE_URL } from "../utils/baseURL"
import { useRunState } from "../state/RunContext"
import LatestRunBanner from "../components/LatestRunBanner"

type PipelineStatus = {
  raw_data_root_exists: boolean
  lock: { busy: boolean; holder: string | null }
}

export default function Home() {
  const { latest } = useRunState()
  const [status, setStatus] = useState<PipelineStatus | null>(null)

  useEffect(() => {
    fetch(`${BASE_URL}/pipeline/status`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d: PipelineStatus) => setStatus(d))
      .catch(() => setStatus(null))
  }, [])

  return (
    <div className="d-flex flex-column p-3 gap-3">
      <LatestRunBanner pageTitle="Home" />

      <div className="border rounded p-3">
        <h2>Welcome</h2>
        <p>
          Use <Link to="/Select">Select</Link> to pick dates and parameters, then click
          <strong> Run with these dates</strong> to populate the plots on the
          other pages. Browse the results in <Link to="/CalibrationData">Calibration</Link>,{" "}
          <Link to="/RawData">Raw Data</Link>, and <Link to="/CalibratedData">Calibrated Data</Link>.
        </p>
      </div>

      <div className="border rounded p-3">
        <h2>Pipeline status</h2>
        {status ? (
          <>
            <p>
              Raw data root exists: <strong>{status.raw_data_root_exists ? "yes" : "no"}</strong>
            </p>
            <p>
              Pipeline busy:{" "}
              <strong>{status.lock.busy ? `yes (${status.lock.holder})` : "no"}</strong>
            </p>
          </>
        ) : (
          <p className="text-muted">Pipeline status unavailable (is the backend running?).</p>
        )}
        {latest.run_id && (
          <p className="text-muted small mt-2">
            The website is currently showing data from your most recent run.
          </p>
        )}
      </div>
    </div>
  )
}
