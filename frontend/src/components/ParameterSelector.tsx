export type Parameter = {
  name: string
  type: 'select' | 'number' | 'boolean'
  options?: string[]
  defaultValue: number | string | boolean
}

type ParameterSelectorProps = {
  parameter: Parameter
  value: string | number | boolean
  onChange: (value: string | number | boolean) => void
}

function ParameterSelector({ parameter, value, onChange }: ParameterSelectorProps) {
  if (parameter.type === "select") {
    return (
      <div className="d-flex gap-4 align-items-center p-2 justify-content-between">
        <span>{parameter.name}</span>
        <select value={value as string} onChange={e => onChange(e.target.value)}>
          {parameter.options?.map(option => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
      </div>
    )
  } else if (parameter.type === "number") {
    return (
      <div className="d-flex gap-4 align-items-center p-2 justify-content-between">
        <span>{parameter.name}</span>
        <input
          className="parameter-input"
          type="number"
          value={value as number}
          onChange={e => {
            const parsed = parseFloat(e.target.value)
            // An emptied input parses to NaN; keeping the last valid value
            // avoids submitting null to the backend (which 500s argparse).
            onChange(Number.isNaN(parsed) ? (value as number) : parsed)
          }}
        />
      </div>
    )
  } else if (parameter.type === "boolean") {
    return (
      <div className="d-flex gap-4 align-items-center p-2 justify-content-between">
        <span>{parameter.name}</span>
        <input
          type="checkbox"
          checked={value as boolean}
          onChange={e => onChange(e.target.checked)}
        />
      </div>
    )
  }
  return null
}

export default ParameterSelector