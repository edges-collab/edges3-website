import "./App.css"

import { Navigate, Route, Routes } from "react-router"
import Home from "./pages/Home.tsx"
import LastNight from "./pages/LastNight.tsx"
import Calibrations from "./pages/Calibrations.tsx"
import DataView from "./pages/DataView.tsx"

import NavBar from "./components/NavBar.tsx"
import { CalibrationProvider } from "./state/CalibrationContext.tsx"

function App() {
  return (
    <CalibrationProvider>
      <main className="app">
        <NavBar />
        <div className="page">
          <Routes>
            <Route path="/" element={<LastNight />} />
            <Route path="/calibrations" element={<Calibrations />} />
            <Route path="/data" element={<DataView />} />
            <Route path="/status" element={<Home />} />
            {/* old addresses */}
            <Route path="/Select" element={<Navigate to="/calibrations" replace />} />
            <Route path="/CalibrationData" element={<Navigate to="/calibrations" replace />} />
            <Route path="/RawData" element={<Navigate to="/data" replace />} />
            <Route path="/CalibratedData" element={<Navigate to="/data" replace />} />
            <Route path="/Status" element={<Navigate to="/status" replace />} />
          </Routes>
        </div>
      </main>
    </CalibrationProvider>
  )
}

export default App
