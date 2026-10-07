/*

This is the navigation bar: the Nightly Overview (home), Calibrations, the
Detailed Data View, Raw Data (EDGES-3 and EDGES-2) and Status. The current tab is highlighted.

*/

import { NavLink } from "react-router"

const TABS: [string, string][] = [
  ["/", "Nightly Overview"],
  ["/calibrations", "Calibrations"],
  ["/data", "Detailed Data View"],
  ["/raw", "Raw Data"],
  ["/status", "Status"],
]

function NavBar() {
  return (
    <div className="d-flex gap-4 align-items-center p-2 border-bottom">
      <NavLink to="/" className="text-decoration-none text-dark">
        <h1 className="edges-logo m-0">EDGES</h1>
      </NavLink>
      <nav className="nav nav-pills gap-1">
        {TABS.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}
            className={({ isActive }) => `nav-link py-1 ${isActive ? "active" : ""}`}>
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}

export default NavBar
