/* 

This is the selector for what type of complex number display we want to use

*/

// Define a complexSelectorProps, which is a function takes in some string 

type ComplexSelectorProps = {
    onSelectChange: (value: string) => void
    }   

// Define the React object

function ComplexSelector({ onSelectChange }: ComplexSelectorProps) {
        return (
            <div className="d-flex gap-4 align-items-center p-2 justify-content-between">

                <span>
                    Complex Options
                </span>
                {/* This says that on the change of the select object, you run the function which we put in the argument of ComplexSelector (which ends up being setComplexDisplay) with event.target.value as the input */}
                <select defaultValue="Re/Im" onChange={(event) => onSelectChange(event.target.value)}>
                    <option key="Re/Im" value="Re/Im">
                    Re/Im
                    </option>
                    <option key="Abs/Arg" value="Abs/Arg">
                    Abs/Arg
                    </option>
                </select>
                
            </div>
        )
}

// Default export the object

export default ComplexSelector