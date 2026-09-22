import Plot from 'react-plotly.js'

function DataPlot() {
  return (
  <div className="plot-container">
    <Plot
  data={[
    {
      x: [1, 2, 3, 4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20],
      y: [10, 15, 13, 17,10, 15, 13, 17,10, 15, 13, 17,10, 15, 13, 17,10, 15, 13, 17],
      type: 'scatter',
      mode: 'lines',
    },
  ]}
  layout={{
    autosize: true, height: 300,
    margin: {
      l: 50,
      r: 20,
      t: 40,
      b: 50,
    },
  }}
  useResizeHandler={true}
  style={{
    width: "90%",
    height: "90%",
    
  }}
  />
</div>
  )
}

export default DataPlot