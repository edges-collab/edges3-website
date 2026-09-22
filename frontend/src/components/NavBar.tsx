/*

This is the navigation bar.
It takes in no arguments

*/

// Import the react router stuff

import { Link } from 'react-router'

// Define the NavBar react object

function NavBar() {
  return (
    <div className="d-flex gap-4 align-items-center p-2">

        <div>
            <Link to="/" className="text-decoration-none text-dark">
            <h1 className='edges-logo'>EDGES</h1>  
            </Link>
        </div>  

        <div className="d-flex gap-2">

        <Link to="/Select" className="btn btn-primary nav-button">
            Select
        </Link>

        <Link to="/CalibrationData" className="btn btn-primary nav-button">
            Calibration
        </Link>

        <Link to="/RawData" className="btn btn-primary nav-button">
          Raw Data
        </Link>

        <Link to="/CalibratedData" className="btn btn-primary nav-button">
           Calibrated Data
        </Link>

        </div>

    </div>
  )
}

// Default export the object

export default NavBar