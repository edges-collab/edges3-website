import { useState } from "react"
import DatePanel from "../components/DatePanel"
import ParameterPanel, { type ParameterSet } from "../components/ParameterPanel"

const allParameters: ParameterSet = [
  { name: "cterms", type: "number", defaultValue: 6 },
  { name: "wterms", type: "number", defaultValue: 5 },
  { name: "fstart", type: "number", defaultValue: 40.0 },
  { name: "fstop", type: "number", defaultValue: 190.0 },
  { name: "wfstart", type: "number", defaultValue: 40.0 },
  { name: "wfstop", type: "number", defaultValue: 190.0 },
  { name: "save_2d_npz", type: "boolean", defaultValue: false },
]

export default function Select() {
  const [selectedDates, setSelectedDates] = useState({
    cal: "Latest",
    s11: "Latest",
    raw: "Latest",
  })

  return (
    <div className="d-flex gap-4 flex-column p-3">
      <DatePanel
        dateSet={{ calDate: [], s11Date: [], rawDate: [] }}
        onDatesChange={setSelectedDates}
      />
      <ParameterPanel
        parameterSet={allParameters}
        selectedDates={selectedDates}
      />
    </div>
  )
}
