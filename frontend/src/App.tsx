import "./App.css"

import { Route, Routes } from "react-router"
import Select from "./pages/Select.tsx"
import CalibrationData from "./pages/CalibrationData.tsx"
import RawData from "./pages/RawData.tsx"
import CalibratedData from "./pages/CalibratedData.tsx"
import Home from "./pages/Home.tsx"
import LastNight from "./pages/LastNight.tsx"

import NavBar from "./components/NavBar.tsx"
import { RunProvider } from "./state/RunContext.tsx"

function App() {
  return (
    <RunProvider>
      <main className="app">
        <NavBar />
        <div className="page">
          <Routes>
            <Route path="/" element={<LastNight />} />
            <Route path="/Status" element={<Home />} />
            <Route path="/Select" element={<Select />} />
            <Route path="/CalibrationData" element={<CalibrationData />} />
            <Route path="/RawData" element={<RawData />} />
            <Route path="/CalibratedData" element={<CalibratedData />} />
          </Routes>
        </div>
      </main>
    </RunProvider>
  )
}

export default App
