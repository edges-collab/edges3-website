/**
 * Selector for the Multi plotter — switches between "Actual Values" (raw
 * overlay) and "Residuals" (Trace1 - Trace2).
 */

type MultiSelectorProps = {
  onSelectChange: (value: string) => void
}

function MultiSelector({ onSelectChange }: MultiSelectorProps) {
  return (
    <div className="d-flex gap-4 align-items-center p-2 justify-content-between">
      <span>Display Options</span>
      <select
        defaultValue="Actual Values"
        onChange={(event) => onSelectChange(event.target.value)}
      >
        <option key="Actual Values" value="Actual Values">Actual Values</option>
        <option key="Residuals" value="Residuals">Residuals</option>
      </select>
    </div>
  )
}

export default MultiSelector
