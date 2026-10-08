/*

The navigation bar: the receiver picker (EDGES-3, the EDGES-2 antennas),
then that receiver's views (Record, Night, Calibration, Calibrated night;
the ones it does not have are greyed out, saying why), and Status on the
right. Switching receivers keeps the view when the new one has it.

*/

import { useState } from "react"
import { Link, NavLink, useLocation, useNavigate } from "react-router"
import { useReceivers } from "../state/Receivers"
import { DEFAULT_RECEIVER, VIEWS, defaultView, hasView, missingReason } from "../utils/views"

function NavBar() {
  const { receivers } = useReceivers()
  const navigate = useNavigate()
  const [first, second] = useLocation().pathname.split("/").filter(Boolean)
  const inReceiver = !!first && first !== "status"
  // off a receiver's pages (Status), the bar keeps the last one chosen
  const [last, setLast] = useState(DEFAULT_RECEIVER)
  const dep = inReceiver ? first : last
  if (inReceiver && dep !== last) setLast(dep)
  const r = receivers?.find((x) => x.name === dep)
  const groups = [...new Set((receivers ?? []).map((x) => x.instrument))]

  const choose = (name: string) => {
    const next = receivers?.find((x) => x.name === name)
    navigate(`/${name}/${inReceiver && second && hasView(next, second) ? second : defaultView(next)}`)
  }

  return (
    <div className="d-flex flex-wrap gap-3 align-items-center p-2 border-bottom">
      <Link to="/" className="text-decoration-none text-dark">
        <h1 className="edges-logo m-0">EDGES</h1>
      </Link>
      <select className="form-select form-select-sm w-auto fw-semibold" aria-label="Receiver" value={dep}
        onChange={(e) => choose(e.target.value)}>
        {!receivers && <option value={dep}>{dep}</option>}
        {groups.map((g) => (
          <optgroup key={g} label={g}>
            {receivers!.filter((x) => x.instrument === g).map((x) => <option key={x.name} value={x.name}>{x.label}</option>)}
          </optgroup>
        ))}
      </select>
      <nav className="nav nav-pills gap-1">
        {VIEWS.map((v) => hasView(r, v.key) || !r ? (
          <NavLink key={v.key} to={`/${dep}/${v.key}`} title={v.help}
            className={({ isActive }) => `nav-link py-1 ${isActive ? "active" : ""}`}>
            {v.label}
          </NavLink>
        ) : (
          <span key={v.key} className="nav-link py-1 disabled" title={missingReason(r, v.key)}>{v.label}</span>
        ))}
      </nav>
      <NavLink to="/status" className={({ isActive }) => `ms-auto small ${isActive ? "fw-semibold" : "text-muted"}`}>
        Status
      </NavLink>
    </div>
  )
}

export default NavBar
