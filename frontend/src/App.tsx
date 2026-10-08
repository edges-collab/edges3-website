import "./App.css"

import { Link, Navigate, Outlet, Route, Routes, useParams } from "react-router"
import Record from "./pages/Record.tsx"
import Night from "./pages/Night.tsx"
import Calibrations from "./pages/Calibrations.tsx"
import DataView from "./pages/DataView.tsx"
import Status from "./pages/Status.tsx"

import NavBar from "./components/NavBar.tsx"
import UpdateBanner from "./components/UpdateBanner.tsx"
import { CalibrationProvider } from "./state/CalibrationContext.tsx"
import { ReceiversProvider, useReceivers } from "./state/Receivers.tsx"
import { DEFAULT_RECEIVER, defaultView, hasView, missingReason, type ViewKey } from "./utils/views.ts"

/** A receiver's pages (``/:dep/...``), once the receivers are known. */
function ReceiverRoute() {
  const { dep } = useParams()
  const { receivers, error } = useReceivers()
  if (error) return <div className="alert alert-warning m-3">Could not load the receivers: {error}</div>
  if (!receivers) return <div className="text-muted p-3">Loading…</div>
  if (!receivers.some((r) => r.name === dep)) {
    return (
      <div className="alert alert-warning m-3">
        No receiver called “{dep}”. <Link to="/">Go to EDGES-3</Link>.
      </div>
    )
  }
  return <Outlet key={dep} /> // a new receiver starts its pages afresh
}

function DefaultView() {
  const { dep } = useParams()
  const { receivers } = useReceivers()
  return <Navigate to={`/${dep}/${defaultView(receivers?.find((r) => r.name === dep))}`} replace />
}

/** A view the receiver may not have (e.g. calibration for EDGES-2): say so. */
function Needs({ view, children }: { view: ViewKey; children: React.ReactNode }) {
  const { dep } = useParams()
  const { receivers } = useReceivers()
  const r = receivers?.find((x) => x.name === dep)
  if (hasView(r, view)) return children
  return <div className="alert alert-secondary m-3">{missingReason(r, view)}.</div>
}

function App() {
  return (
    <ReceiversProvider>
      <CalibrationProvider>
        <main className="app">
          <NavBar />
          <UpdateBanner />
          <div className="page">
            <Routes>
              <Route path="/" element={<Navigate to={`/${DEFAULT_RECEIVER}/night`} replace />} />
              <Route path="/status" element={<Status />} />
              <Route path="/:dep" element={<ReceiverRoute />}>
                <Route index element={<DefaultView />} />
                <Route path="record" element={<Record />} />
                <Route path="night" element={<Night />} />
                <Route path="calibration" element={<Needs view="calibration"><Calibrations /></Needs>} />
                <Route path="calibrated" element={<Needs view="calibrated"><DataView /></Needs>} />
                <Route path="*" element={<DefaultView />} />
              </Route>
            </Routes>
          </div>
        </main>
      </CalibrationProvider>
    </ReceiversProvider>
  )
}

export default App
